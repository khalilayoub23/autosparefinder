"""
Script: email_agent/loop.py
Purpose: Supervised background loop for the Email Agent (registered in
         BACKEND_API_ROUTES.startup via _supervised_task("email_agent_loop", ...)).
Process:
  - state disabled (EMAIL_AGENT_ENABLED != 1, the default) or unconfigured (a Gmail OAuth value
    is missing): logs the state ONCE and sleeps >= 1h. Never raises, never crash-loops.
  - configured: every EMAIL_AGENT_INTERVAL_S runs agent.process_once() (read -> classify ->
    context -> policy -> draft -> verify). No LLM call. Nothing is sent.
  - a Gmail auth / wrong-account / scope failure backs off for 1h and notifies the owner at
    most once per 24h (notify-by-exception); routine cycles send no notification.
Data Imported/Modified: email_agent_messages (PII DB); Gmail drafts when EMAIL_AGENT_GMAIL_DRAFTS=1.
Last Updated: 2026-10-04
"""
from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, Optional

from email_agent.agent import audit_log, process_once
from email_agent.config import CONFIGURED, load_config
from email_agent.context import DbContextSource
from email_agent.gmail_client import GmailClient
from email_agent.redaction import safe_error
from email_agent.store import PgStore

Notify = Callable[..., Awaitable[None]]
_ATTENTION = ("auth", "wrong_account", "scope")

last_cycle: dict = {}   # read by routes/email_agent_routes.py


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
        logged_state = cfg.state
        delay = cfg.interval_s
        try:
            client = client or GmailClient(cfg)
            await _heartbeat({"status": "starting"})
            summary = await process_once(cfg, client, PgStore(), DbContextSource())
            last_cycle.clear()
            last_cycle.update({k: v for k, v in summary.items() if k != "errors"})
            last_cycle["error_count"] = len(summary.get("errors", []))
            await _heartbeat({"status": "ok", **{k: summary.get(k) for k in
                                                 ("listed", "processed", "failed", "requires_human", "drafts", "sent")}})
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
