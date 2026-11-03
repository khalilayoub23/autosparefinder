"""
Script: email_agent/loop.py
Purpose: Supervised background loop for the Email Agent (registered in
         BACKEND_API_ROUTES.startup via _supervised_task("email_agent_loop", ...)).
Process:
  - state disabled (EMAIL_AGENT_ENABLED != 1, the default) or unconfigured (a Gmail OAuth value
    is missing): logs the state ONCE and sleeps >= 1h. Never raises, never crash-loops.
  - configured but halted by the runtime kill switch (email_agent/control.py; the default until an
    operator arms it): logs once, creates no Gmail client, re-checks every HALT_RECHECK_S.
  - configured and armed: every EMAIL_AGENT_INTERVAL_S runs agent.process_once() (read -> classify ->
    context -> policy -> draft -> verify). No LLM call. Nothing is sent.
  - after each armed cycle: owner_flow.process_owner_updates (WhatsApp alert for important mail,
    an agent_todos task for the owning agent, the 09:00 digest) - added 2026-10-27.
  - before it: email_agent/supplier_routing (supplier replies -> NIR / BOAZ / exception); after it, only
    when NIR_OUTREACH_ENABLED=1: supplier_outreach.run_outreach_cycle (NIR's non-binding inquiries).
  - a Gmail auth / wrong-account / scope failure backs off for 1h and notifies the owner at
    most once per 24h (notify-by-exception); routine cycles send no notification.
Data Imported/Modified: email_agent_messages (PII DB); Gmail drafts when EMAIL_AGENT_GMAIL_DRAFTS=1.
Last Updated: 2026-10-09
"""
from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, Optional

from email_agent import control as _control
from email_agent.agent import audit_log, process_once
from email_agent.config import CONFIGURED, load_config
from email_agent.context import DbContextSource
from email_agent.gmail_client import GmailClient
from email_agent.owner_flow import PgTodoSink, process_owner_updates
from email_agent.supplier_routing import route_supplier_replies
import supplier_flags as _flags
import supplier_outreach as _outreach
from supplier_outreach import PgOutreachStore
from supplier_authorization import PgAuthStore
from procurement import PgProcurementStore
from email_agent.redaction import safe_error
from email_agent.store import PgStore

Notify = Callable[..., Awaitable[None]]
# Owner action needed. A rate limit ("throttled") or an unknown transient error is NOT in this list:
# the next cycle simply continues.
_ATTENTION = ("auth", "wrong_account", "scope", "config", "permission")

last_cycle: dict = {}   # read by routes/email_agent_routes.py
HALT_RECHECK_S = 60     # while enabled but halted, re-read the runtime control this often


async def _heartbeat(stats: dict) -> None:
    try:
        from agents.memory import AgentMemory
        from BACKEND_DATABASE_MODELS import async_session_factory
        async with async_session_factory() as db:
            await AgentMemory(db, agent_name="email_agent").write_worker_heartbeat(stats)
    except Exception:
        pass


async def email_agent_loop(notify: Optional[Notify] = None) -> None:
    await asyncio.sleep(45)  # let startup settle
    logged_state = None
    client: Optional[GmailClient] = None
    while True:
        cfg = load_config()
        if cfg.state != CONFIGURED:
            if logged_state != cfg.state:
                audit_log("idle", state=cfg.state, missing=cfg.missing())
                logged_state = cfg.state
            last_cycle.clear()
            last_cycle.update({"state": cfg.state})
            await asyncio.sleep(max(cfg.interval_s, 3600))
            continue
        ctl = _control.read_control()
        if not ctl["running"]:
            # Runtime kill switch: no Gmail client, no heartbeat, no DB access while halted.
            if logged_state != "halted":
                audit_log("idle", state="halted", reason=ctl["reason"])
                logged_state = "halted"
            last_cycle.clear()
            last_cycle.update({"state": "halted", "reason": ctl["reason"]})
            await asyncio.sleep(HALT_RECHECK_S)
            continue
        logged_state = cfg.state
        delay = cfg.interval_s
        try:
            client = client or GmailClient(cfg)
            await _heartbeat({"status": "starting"})
            store = PgStore()
            outreach_store = PgOutreachStore()
            summary = await process_once(cfg, client, store, DbContextSource(), outreach_store=outreach_store)
            last_cycle.clear()
            last_cycle.update({k: v for k, v in summary.items() if k != "errors"})
            last_cycle["error_count"] = len(summary.get("errors", []))
            await _heartbeat({"status": "ok", **{k: summary.get(k) for k in
                                                 ("listed", "processed", "failed", "requires_human", "drafts", "sent")}})
            # Owner flow (2026-10-27): alerts for important mail, tasks for the owning agent, the
            # 09:00 digest. Only while armed (this line is past the halt check); never fatal.
            # Supplier routing (2026-10-27) runs BEFORE the owner flow so alerts/tasks name the right owner.
            schema_ok = await store.supplier_schema_ready()
            if not schema_ok and any(_flags.snapshot().values()):
                audit_log("supplier_schema_not_ready", detail="migration 0042 not applied - supplier features skipped")
            if schema_ok and _flags.enabled("SUPPLIER_ROUTING_ENABLED") and not summary.get("halted"):
                try:
                    sr = await route_supplier_replies(store, outreach_store, PgTodoSink(),
                                                      procurement_store=PgProcurementStore())
                    if any(sr.values()):
                        audit_log("supplier_routing", **sr)
                except Exception as _sre:
                    audit_log("supplier_routing_error", failure_reason=safe_error(_sre))
            if notify and not summary.get("halted"):
                try:
                    of = await process_owner_updates(store, notify, PgTodoSink())
                    if any(of.get(k) for k in ("alerted", "tasks", "alert_failed", "digest")):
                        audit_log("owner_updates", **of)
                except Exception as _ofe:
                    audit_log("owner_updates_error", failure_reason=safe_error(_ofe))
            # NIR routine supplier inquiries: OFF unless NIR_OUTREACH_ENABLED=1, and every send still needs the
            # owner's standing supplier_message/routine_inquiry authorization (supplier_outreach.py).
            if schema_ok and _outreach.enabled() and not summary.get("halted") and summary.get("connection", {}).get("ok"):
                try:
                    oc = await _outreach.run_outreach_cycle(outreach_store, PgAuthStore(), client, sender=cfg.mailbox)
                    audit_log("nir_outreach", **{k: v for k, v in oc.items() if k != "details"})
                except Exception as _oce:
                    audit_log("nir_outreach_error", failure_reason=safe_error(_oce))
            kind = summary.get("aborted") or summary.get("connection", {}).get("error_kind")
            if kind in _ATTENTION:
                delay = max(delay, 3600)
                if notify:
                    await notify("system", "Email Agent - חיבור Gmail דורש טיפול",
                                 f"סוג התקלה: {kind}. הסוכן ממתין ולא מעבד מיילים.",
                                 severity="warning", alert_key="email_agent_gmail_connection", cooldown_s=86400)
        except Exception as e:
            audit_log("cycle_error", failure_reason=safe_error(e))
        await asyncio.sleep(delay)
