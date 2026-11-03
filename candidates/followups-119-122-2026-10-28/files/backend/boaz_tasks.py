"""
Script: boaz_tasks.py
Purpose: BOAZ actually EXECUTES its agent_todos (owner task 2026-10-27, section 5.1). Before this, BOAZ looked up
         "pricing_agent" while its todos were filed as "BOAZ", and even a found todo was only used to pick a sort
         order - nothing in BOAZ performed, recorded or closed a task.
Process (run_once, every BOAZ_TASK_INTERVAL_S by the supervised loop in BACKEND_API_ROUTES):
  claim      not_started todos owned by BOAZ under ANY of its spellings (agent_identity.aliases) -> in_progress,
             FOR UPDATE SKIP LOCKED (never two workers on one task; NIR's or LIOR's todos are never claimed).
  dispatch   by artifacts.kind / category:
               email            supplier mail from the Email Agent: link it to the supplier record (exact DB
                                match from the Email Agent's context only) and escalate - a reply, an offer or
                                any commitment is the owner's decision (BOAZ has no send/commit permission).
               supplier_handoff a supplier the owner activated: record onboarding prerequisites (price connector /
                                importer, qualification gaps) and escalate what only the owner can supply.
               bulk_scan        run detect_bulk_opportunities and complete with its result.
               (anything else)  escalate: "no automated handler" - never silently completed.
  outcome    completed ONLY when the handler did the work (done=True, with a result in metrics);
             escalated tasks stay in_progress with metrics.boaz.escalated and an owner alert (deduped per task);
             a handler error puts the task back to not_started with an attempt counter; after MAX_ATTEMPTS it
             is escalated with the error - a failure is never marked completed.
  lease      (#122, 2026-10-28) every claim writes metrics.boaz.lease {token, phase, worker, claimed_at, expires_at}
             in the SAME statement; the phase moves claimed -> executing -> handled (with the handler's outcome) and
             the lease is removed when the task is finished. A handler runs at most HANDLER_TIMEOUT_S, the lease
             lasts LEASE_S (> 2 x handler timeout), so a live worker always finishes or times out before its lease
             expires. Every write after the claim is fenced by the token (a late worker cannot overwrite a newer one).
  recover    at the start of each pass, ONE atomic statement (FOR UPDATE SKIP LOCKED) takes over in_progress BOAZ
             tasks whose lease EXPIRED (= the worker died) and gives them a new lease; then by the phase they died in:
               claimed   (before the handler ran)         -> back to not_started (re-run)
               handled   (handler finished, not recorded) -> the RECORDED outcome is applied; never re-run
               executing (inside the handler)             -> re-run only for RETRY_SAFE kinds (idempotent handlers);
                                                             any other kind -> escalated, reconciliation_required
             at most MAX_RECOVERIES per task, then escalated (terminal). Escalated tasks carry no lease and are never
             reclaimed; other agents' tasks are never selected. Every step is appended to metrics.boaz.history.
Data Imported/Modified: agent_todos (status/progress/metrics), suppliers.credentials.boaz (catalog DB).
Data Sources: agent_todos, suppliers, email_agent_messages (PII DB, by email ref).
Last Updated: 2026-10-28 (#122 lease + stale-task recovery)
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

from agent_identity import aliases, canonical

AGENT = canonical("BOAZ")
MAX_ATTEMPTS = 3
BATCH = int(os.getenv("BOAZ_TASK_BATCH", "10"))
# #122: a handler is bounded; the lease outlives the bound, so an expired lease means the worker is gone.
HANDLER_TIMEOUT_S = int(os.getenv("BOAZ_HANDLER_TIMEOUT_S", "300"))
LEASE_S = max(int(os.getenv("BOAZ_LEASE_S", "0") or 0), 2 * HANDLER_TIMEOUT_S + 60)
MAX_RECOVERIES = int(os.getenv("BOAZ_MAX_RECOVERIES", "2"))
# Kinds whose handler may run again after a crash INSIDE it: their writes overwrite the same keys
# (suppliers.credentials.boaz.*) and their owner alert is deduped by alert_key. bulk_scan is NOT here: it creates
# approval_queue rows and only skips ones still pending - a re-run after the owner decided would re-create them.
RETRY_SAFE = frozenset({"email", "supplier_handoff", "procurement_reply"})
HISTORY_MAX = 30
WORKER = f"{socket.gethostname()}:{os.getpid()}"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _json(v, default):
    if v is None:
        return default
    if isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return default


# ── repositories ───────────────────────────────────────────────────────────────────────────────────────
def _lease(phase: str, lease_s: int, now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or _now()
    return {"token": str(uuid.uuid4()), "phase": phase, "worker": WORKER, "claimed_at": now.isoformat(),
            "expires_at": (now + timedelta(seconds=lease_s)).isoformat()}


class InMemoryTaskRepo:
    def __init__(self, todos: Optional[List[Dict[str, Any]]] = None):
        self.todos = todos or []

    @staticmethod
    def _boaz(t):
        if not isinstance(t.get("metrics"), dict):
            t["metrics"] = {}
        return t["metrics"].setdefault("boaz", {})

    async def claim(self, owner_names: List[str], limit: int, lease_s: int = LEASE_S) -> List[Dict[str, Any]]:
        out = []
        for t in self.todos:
            if len(out) >= limit:
                break
            if t.get("assigned_to_agent") in owner_names and t.get("status") == "not_started":
                t["status"] = "in_progress"
                self._boaz(t)["lease"] = _lease("claimed", lease_s)
                out.append(t)
        return out

    async def set_phase(self, task_id: str, token: str, phase: str, outcome: Optional[Dict[str, Any]] = None) -> bool:
        t = next(x for x in self.todos if x["id"] == task_id)
        lease = self._boaz(t).get("lease") or {}
        if lease.get("token") != token:
            return False
        lease.update(phase=phase, phase_at=_now().isoformat(), **({"outcome": outcome} if outcome is not None else {}))
        return True

    async def recover_stale(self, owner_names: List[str], limit: int, lease_s: int = LEASE_S):
        out, now = [], _now()
        for t in self.todos:
            if len(out) >= limit:
                break
            lease = (t.get("metrics") or {}).get("boaz", {}).get("lease")
            if (t.get("assigned_to_agent") in owner_names and t.get("status") == "in_progress" and lease
                    and datetime.fromisoformat(lease["expires_at"]) < now):
                new = _lease("recovering", lease_s, now)
                new["previous"] = lease
                self._boaz(t)["lease"] = new
                out.append((t, lease))
        return out

    async def finish(self, task_id: str, *, status: str, note: str, metrics: Dict[str, Any],
                     token: Optional[str] = None) -> bool:
        t = next(x for x in self.todos if x["id"] == task_id)
        if token is not None and ((t.get("metrics") or {}).get("boaz", {}).get("lease") or {}).get("token") != token:
            return False
        t.update(status=status, progress_notes=note, metrics=metrics)
        return True

    async def open_tasks(self, owner_names: List[str]) -> List[Dict[str, Any]]:
        return [t for t in self.todos if t.get("assigned_to_agent") in owner_names
                and t.get("status") in ("not_started", "in_progress")]


_RET = ("id::text AS id, title, description, category, priority, assigned_to_agent, "
        "COALESCE(artifacts,'{}'::jsonb) AS artifacts, COALESCE(metrics,'{}'::jsonb) AS metrics")
_NEW_LEASE = ("jsonb_build_object('token', gen_random_uuid()::text, 'phase', CAST(:phase AS text), "
              "'worker', CAST(:w AS text), 'claimed_at', NOW(), 'expires_at', NOW() + make_interval(secs => :lease))")


class PgTaskRepo:
    def __init__(self, session_factory=None):
        self._factory = session_factory

    def _session(self):
        if self._factory is None:
            from BACKEND_DATABASE_MODELS import async_session_factory
            self._factory = async_session_factory
        return self._factory()

    async def claim(self, owner_names: List[str], limit: int, lease_s: int = LEASE_S) -> List[Dict[str, Any]]:
        from sqlalchemy import text
        async with self._session() as db:
            res = await db.execute(text(f"""
                UPDATE agent_todos t SET status = 'in_progress', started_at = COALESCE(started_at, NOW()), updated_at = NOW(),
                       metrics = COALESCE(t.metrics, '{{}}'::jsonb) || jsonb_build_object('boaz',
                                 COALESCE(t.metrics->'boaz', '{{}}'::jsonb) || jsonb_build_object('lease', {_NEW_LEASE}))
                WHERE id IN (
                    SELECT id FROM agent_todos
                    WHERE assigned_to_agent = ANY(CAST(:names AS varchar[])) AND status = 'not_started'
                    ORDER BY CASE priority WHEN 'critical' THEN 1 WHEN 'high' THEN 2 WHEN 'medium' THEN 3 ELSE 4 END,
                             created_at
                    LIMIT :n FOR UPDATE SKIP LOCKED)
                RETURNING {_RET}"""),
                {"names": owner_names, "n": limit, "phase": "claimed", "w": WORKER, "lease": lease_s})
            rows = [dict(r._mapping) for r in res.fetchall()]
            await db.commit()
            return rows

    async def set_phase(self, task_id: str, token: str, phase: str, outcome: Optional[Dict[str, Any]] = None) -> bool:
        from sqlalchemy import text
        extra = json.dumps({"outcome": outcome} if outcome is not None else {}, default=str)
        async with self._session() as db:
            res = await db.execute(text("""
                UPDATE agent_todos SET metrics = jsonb_set(metrics, '{boaz,lease}',
                       (metrics->'boaz'->'lease') || jsonb_build_object('phase', CAST(:p AS text), 'phase_at', NOW())
                       || CAST(:x AS jsonb)), updated_at = NOW()
                WHERE id = CAST(:i AS uuid) AND metrics->'boaz'->'lease'->>'token' = :tok RETURNING id"""),
                {"p": phase, "x": extra, "i": task_id, "tok": token})
            ok = res.scalar() is not None
            await db.commit()
            return ok

    async def recover_stale(self, owner_names: List[str], limit: int, lease_s: int = LEASE_S):
        """ONE statement: select expired leases (SKIP LOCKED) and replace them with a new lease, returning the old one.
        Two concurrent recoverers can never get the same task: the second no longer sees an expired lease."""
        from sqlalchemy import text
        async with self._session() as db:
            res = await db.execute(text(f"""
                WITH s AS (
                    SELECT id, metrics->'boaz'->'lease' AS old FROM agent_todos
                    WHERE assigned_to_agent = ANY(CAST(:names AS varchar[])) AND status = 'in_progress'
                      AND metrics->'boaz'->'lease' IS NOT NULL
                      AND CAST(metrics->'boaz'->'lease'->>'expires_at' AS timestamptz) < NOW()
                    ORDER BY updated_at LIMIT :n FOR UPDATE SKIP LOCKED)
                UPDATE agent_todos t SET updated_at = NOW(),
                       metrics = jsonb_set(t.metrics, '{{boaz,lease}}', {_NEW_LEASE} || jsonb_build_object('previous', s.old))
                FROM s WHERE t.id = s.id
                RETURNING t.id::text AS id, t.title, t.description, t.category, t.priority, t.assigned_to_agent,
                          COALESCE(t.artifacts,'{{}}'::jsonb) AS artifacts, t.metrics AS metrics, s.old AS old_lease"""),
                {"names": owner_names, "n": limit, "phase": "recovering", "w": WORKER, "lease": lease_s})
            rows = [dict(r._mapping) for r in res.fetchall()]
            await db.commit()
        return [({k: v for k, v in r.items() if k != "old_lease"}, _json(r["old_lease"], {})) for r in rows]

    async def finish(self, task_id: str, *, status: str, note: str, metrics: Dict[str, Any],
                     token: Optional[str] = None) -> bool:
        from sqlalchemy import text
        async with self._session() as db:
            res = await db.execute(text("""
                UPDATE agent_todos SET status = CAST(:s AS varchar), progress_notes = CAST(:n AS text),
                       metrics = CAST(:m AS jsonb),
                       progress_pct = CASE WHEN CAST(:s AS varchar) = 'completed' THEN 100 ELSE progress_pct END,
                       completed_at = CASE WHEN CAST(:s AS varchar) = 'completed' THEN NOW() ELSE completed_at END,
                       updated_at = NOW()
                WHERE id = CAST(:i AS uuid)
                  AND (CAST(:tok AS text) IS NULL OR metrics->'boaz'->'lease'->>'token' = CAST(:tok AS text))
                RETURNING id"""), {"s": status, "n": note[:2000], "m": json.dumps(metrics, default=str),
                                   "i": task_id, "tok": token})
            ok = res.scalar() is not None
            await db.commit()
            return ok

    async def open_tasks(self, owner_names: List[str]) -> List[Dict[str, Any]]:
        from sqlalchemy import text
        async with self._session() as db:
            res = await db.execute(text(
                "SELECT id::text AS id, title, status, priority, progress_notes, COALESCE(metrics,'{}'::jsonb) AS metrics "
                "FROM agent_todos WHERE assigned_to_agent = ANY(CAST(:names AS varchar[])) "
                "AND status IN ('not_started','in_progress') ORDER BY created_at DESC LIMIT 20"), {"names": owner_names})
            return [dict(r._mapping) for r in res.fetchall()]


# ── handlers: return {"done": bool, "note": str, "result": {...}, "escalate": Optional[str]} ─────────────
Handler = Callable[[Dict[str, Any], Dict[str, Any]], Awaitable[Dict[str, Any]]]


async def handle_email(task: Dict[str, Any], deps: Dict[str, Any]) -> Dict[str, Any]:
    art = _json(task.get("artifacts"), {})
    ref = art.get("email_ref")
    rows = await deps["email_store"].by_ref(ref) if ref else []
    if len(rows) != 1:
        return {"done": False, "escalate": f"email ref {ref or '?'} not found uniquely - owner review", "result": {}}
    row = rows[0]
    ctx = _json(row.get("context"), {}) or {}
    sup = ctx.get("supplier") or {}
    if sup.get("status") == "resolved" and sup.get("supplier_id"):
        await deps["link_supplier_mail"](sup["supplier_id"], {"email_ref": ref, "classification": row.get("classification"),
                                                              "at": _now().isoformat()})
        who = f"linked to supplier {sup['supplier_id'][:8]}"
    else:
        who = f"supplier not identified by exact DB match ({sup.get('status') or 'unresolved'})"
    return {"done": False, "result": {"email_ref": ref, "supplier": sup.get("supplier_id")},
            "escalate": f"{who}. Any reply, offer or commitment is the owner's decision: 'מייל {ref}'."}


async def handle_supplier_handoff(task: Dict[str, Any], deps: Dict[str, Any]) -> Dict[str, Any]:
    art = _json(task.get("artifacts"), {})
    sid = art.get("supplier_id")
    if not sid:
        return {"done": False, "escalate": "handoff without supplier_id", "result": {}}
    info = await deps["supplier_status"](sid)
    missing = []
    if not info.get("is_active"):
        missing.append("supplier not active (owner activation required)")
    if not info.get("priced_offers"):
        missing.append("no priced offers yet - a price connector / importer must be built for this supplier")
    gaps = info.get("qualification_unverified") or []
    if gaps:
        missing.append("unverified: " + ", ".join(gaps))
    if info.get("dropshipping") and info["dropshipping"] != "DROPSHIPPING-READY":
        missing.append(f"dropshipping: {info['dropshipping']} - no direct-to-customer orders until verified")
    await deps["record_onboarding"](sid, {"checked_at": _now().isoformat(), "missing": missing})
    if missing:
        return {"done": False, "result": info, "escalate": "onboarding prerequisites: " + "; ".join(missing)}
    return {"done": True, "note": "supplier active with priced offers and verified qualification", "result": info}


async def handle_bulk_scan(task: Dict[str, Any], deps: Dict[str, Any]) -> Dict[str, Any]:
    res = await deps["bulk_scan"]()
    if res.get("status") == "error":
        raise RuntimeError(res.get("error") or "bulk scan failed")
    return {"done": True, "note": f"bulk scan: {res.get('status')} created={res.get('created', 0)}", "result": res}


async def handle_procurement_reply(task: Dict[str, Any], deps: Dict[str, Any]) -> Dict[str, Any]:
    """A supplier answered a purchase-order email. BOAZ records the link; the owner decides (a confirmation is
    recorded with בדוקרכש / the email; a changed price or stock-out needs a NEW approval)."""
    art = _json(task.get("artifacts"), {})
    pid = art.get("procurement_id")
    return {"done": False, "result": {"procurement_id": pid, "email_ref": art.get("email_ref")},
            "escalate": f"supplier replied to purchase order {str(pid)[:8]} - read it ('מייל {art.get('email_ref')}'); "
                        "changed terms need a new אשררכש."}


HANDLERS: Dict[str, Handler] = {"email": handle_email, "supplier_handoff": handle_supplier_handoff,
                                "bulk_scan": handle_bulk_scan, "procurement_reply": handle_procurement_reply}


def task_kind(task: Dict[str, Any]) -> str:
    art = _json(task.get("artifacts"), {})
    return str(art.get("kind") or task.get("category") or "general")


def _hist(boaz: Dict[str, Any], event: str, **kw) -> None:
    h = list(boaz.get("history") or [])
    h.append({"at": _now().isoformat(), "event": event, "worker": WORKER, **kw})
    boaz["history"] = h[-HISTORY_MAX:]


def _released(task: Dict[str, Any]):
    """(metrics, boaz) for writing a finished task: the lease is dropped, everything else kept."""
    metrics = dict(_json(task.get("metrics"), {}) or {})
    boaz = dict(metrics.get("boaz") or {})
    lease = boaz.pop("lease", None) or {}
    metrics["boaz"] = boaz
    return metrics, boaz, lease.get("token")


async def _apply_outcome(repo, task, metrics, boaz, kind, r, token, notify, out) -> None:
    boaz.update(kind=kind, handled_at=_now().isoformat(), result=r.get("result"))
    if r.get("done"):
        _hist(boaz, "completed")
        if await repo.finish(task["id"], status="completed", note=r.get("note") or "done by BOAZ",
                             metrics={**metrics, "boaz": boaz}, token=token):
            out["completed"] += 1
    else:
        boaz["escalated"] = _now().isoformat()
        _hist(boaz, "escalated", reason=str(r.get("escalate"))[:200])
        if await repo.finish(task["id"], status="in_progress", note=f"BOAZ escalated: {r.get('escalate')}",
                             metrics={**metrics, "boaz": boaz}, token=token):
            out["escalated"] += 1
            await _alert(notify, task, str(r.get("escalate"))[:300])


async def _reconcile(repo, task, metrics, boaz, token, notify, out, why: str) -> None:
    boaz.update(escalated=_now().isoformat(), reconciliation_required=True, reconciliation_reason=why)
    _hist(boaz, "reconciliation_required", reason=why)
    if await repo.finish(task["id"], status="in_progress", note=f"BOAZ: reconciliation required - {why}",
                         metrics={**metrics, "boaz": boaz}, token=token):
        out["reconcile"] += 1
        await _alert(notify, task, f"בדיקה ידנית נדרשת: {why}"[:300])


async def recover_stale(repo, notify=None, limit: int = BATCH, lease_s: int = LEASE_S,
                        out: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    out = out if out is not None else {"recovered": 0, "requeued": 0, "applied": 0, "reconcile": 0,
                                       "completed": 0, "escalated": 0}
    for task, old in await repo.recover_stale(sorted(aliases(AGENT)), limit, lease_s):
        out["recovered"] += 1
        token = ((_json(task.get("metrics"), {}) or {}).get("boaz", {}).get("lease") or {}).get("token")
        metrics, boaz, _ = _released(task)
        kind, phase = task_kind(task), (old or {}).get("phase")
        boaz["recoveries"] = int(boaz.get("recoveries", 0)) + 1
        _hist(boaz, "recovered_stale", phase=phase, dead_worker=(old or {}).get("worker"),
              expired_at=(old or {}).get("expires_at"))
        if boaz["recoveries"] > MAX_RECOVERIES:
            await _reconcile(repo, task, metrics, boaz, token, notify, out,
                             f"worker died {boaz['recoveries']} times on this task - stopped retrying")
        elif phase == "handled" and isinstance(old.get("outcome"), dict):
            out["applied"] += 1                      # the handler finished; only recording the outcome was lost
            await _apply_outcome(repo, task, metrics, boaz, kind, old["outcome"], token, notify, out)
        elif phase == "claimed" or (phase == "executing" and (kind in RETRY_SAFE or kind not in HANDLERS)):
            _hist(boaz, "requeued", phase=phase)
            if await repo.finish(task["id"], status="not_started", metrics={**metrics, "boaz": boaz}, token=token,
                                 note=f"BOAZ worker died ({phase}) - re-queued (recovery {boaz['recoveries']})"):
                out["requeued"] += 1
        else:
            await _reconcile(repo, task, metrics, boaz, token, notify, out,
                             f"worker died while running '{kind}' (phase {phase}); its effect may or may not have "
                             "happened - check before re-running")
    return out


async def run_once(repo, deps: Dict[str, Any], notify=None, limit: int = BATCH, lease_s: int = LEASE_S,
                   handler_timeout_s: int = HANDLER_TIMEOUT_S) -> Dict[str, Any]:
    out = {"recovered": 0, "requeued": 0, "applied": 0, "reconcile": 0,
           "claimed": 0, "completed": 0, "escalated": 0, "retry": 0, "failed": 0}
    await recover_stale(repo, notify, limit, lease_s, out)
    for task in await repo.claim(sorted(aliases(AGENT)), limit, lease_s):
        out["claimed"] += 1
        token = ((_json(task.get("metrics"), {}) or {}).get("boaz", {}).get("lease") or {}).get("token")
        metrics, boaz, _ = _released(task)
        kind = task_kind(task)
        handler = HANDLERS.get(kind)
        if not await repo.set_phase(task["id"], token, "executing"):
            continue                                  # lease lost (another worker owns the task now)
        try:
            if handler is None:
                r = {"done": False, "result": {}, "escalate": f"no automated BOAZ handler for task type '{kind}' - "
                                                               "owner / manual handling"}
            else:
                r = await asyncio.wait_for(handler(task, deps), timeout=handler_timeout_s)
        except asyncio.TimeoutError:
            if kind not in RETRY_SAFE:
                await _reconcile(repo, task, metrics, boaz, token, notify, out,
                                 f"'{kind}' did not finish within {handler_timeout_s}s; its effect is unknown")
                continue
            boaz["attempts"] = int(boaz.get("attempts", 0)) + 1
            boaz["last_error"] = f"TimeoutError: no result within {handler_timeout_s}s"
            await _retry_or_fail(repo, task, metrics, boaz, token, notify, out)
            continue
        except Exception as e:
            boaz["attempts"] = int(boaz.get("attempts", 0)) + 1
            boaz["last_error"] = f"{type(e).__name__}: {str(e)[:200]}"
            await _retry_or_fail(repo, task, metrics, boaz, token, notify, out)
            continue
        await repo.set_phase(task["id"], token, "handled",
                             {k: r.get(k) for k in ("done", "note", "escalate", "result")})
        await _apply_outcome(repo, task, metrics, boaz, kind, r, token, notify, out)
    return out


async def _retry_or_fail(repo, task, metrics, boaz, token, notify, out) -> None:
    if boaz["attempts"] >= MAX_ATTEMPTS:
        boaz["escalated"] = _now().isoformat()
        _hist(boaz, "failed_escalated", error=boaz["last_error"][:200])
        if await repo.finish(task["id"], status="in_progress",
                             note=f"BOAZ failed {boaz['attempts']}x: {boaz['last_error']} - escalated",
                             metrics={**metrics, "boaz": boaz}, token=token):
            out["failed"] += 1
            await _alert(notify, task, f"נכשל {boaz['attempts']} פעמים: {boaz['last_error'][:120]}")
    else:
        _hist(boaz, "retry", error=boaz["last_error"][:200])
        if await repo.finish(task["id"], status="not_started", note=f"BOAZ attempt {boaz['attempts']} failed: "
                             f"{boaz['last_error']} - will retry", metrics={**metrics, "boaz": boaz}, token=token):
            out["retry"] += 1


async def _alert(notify, task: Dict[str, Any], text_: str) -> None:
    if not notify:
        return
    try:
        await notify("supplier", f"BOAZ צריך החלטה שלך — {str(task.get('title') or '')[:60]}",
                     f"{text_}\nמשימה {task['id'][:8]} · כתוב *משימות בועז* לרשימה.",
                     severity="info", alert_key=f"boaz_task_{task['id']}", cooldown_s=30 * 86400)
    except Exception:
        pass


async def open_tasks_text(repo) -> str:
    rows = await repo.open_tasks(sorted(aliases(AGENT)))
    if not rows:
        return "📋 אין משימות פתוחות לבועז ✅"
    lines = [f"📋 משימות בועז ({len(rows)}):"]
    for t in rows:
        lines.append(f"• *{t['id'][:8]}* · {t['status']} · {str(t.get('title'))[:70]}\n  {str(t.get('progress_notes') or '')[:140]}")
    return "\n".join(lines)


# ── production dependencies ───────────────────────────────────────────────────────────────────────────
def pg_deps(agent=None) -> Dict[str, Any]:
    from sqlalchemy import text
    from BACKEND_DATABASE_MODELS import async_session_factory
    from email_agent.store import PgStore

    async def _merge(sid: str, key: str, value: Dict[str, Any]) -> None:
        async with async_session_factory() as db:
            await db.execute(text(
                "UPDATE suppliers SET credentials = jsonb_set(coalesce(credentials,'{}'::jsonb), "
                "CAST(:path AS text[]), CAST(:v AS jsonb), true), updated_at = NOW() WHERE id = CAST(:i AS uuid)"),
                {"path": "{boaz," + key + "}", "v": json.dumps(value, default=str), "i": sid})
            await db.commit()

    async def link_supplier_mail(sid, entry):
        await _merge(sid, "last_mail", entry)

    async def record_onboarding(sid, entry):
        await _merge(sid, "onboarding", entry)

    async def supplier_status(sid):
        async with async_session_factory() as db:
            r = (await db.execute(text(
                "SELECT is_active, credentials->'qualification' AS q, (SELECT count(*) FROM supplier_parts sp WHERE "
                "sp.supplier_id = s.id AND sp.price_ils > 0 AND sp.is_available) AS priced FROM suppliers s "
                "WHERE id = CAST(:i AS uuid)"), {"i": sid})).first()
        if not r:
            return {"exists": False}
        q = _json(r.q, None)
        unverified, ds = [], "DROPSHIPPING-CANDIDATE"
        if q:
            import supplier_qualification as _q
            c = _q.classify(q)
            unverified, ds = c["unverified"], c["dropshipping"]
        return {"exists": True, "is_active": bool(r.is_active), "priced_offers": int(r.priced or 0),
                "qualification_unverified": unverified, "dropshipping": ds}

    async def bulk_scan():
        from BACKEND_AI_AGENTS import SupplierManagerAgent
        async with async_session_factory() as db:
            return await (agent or SupplierManagerAgent()).detect_bulk_opportunities(db)

    return {"email_store": PgStore(), "link_supplier_mail": link_supplier_mail, "record_onboarding": record_onboarding,
            "supplier_status": supplier_status, "bulk_scan": bulk_scan}


async def file_supplier_handoff(supplier_id: str, name: str, approved_by: str) -> Optional[str]:
    """NIR -> BOAZ handoff after the owner activates a supplier (one task per supplier activation)."""
    from sqlalchemy import text
    from BACKEND_DATABASE_MODELS import async_session_factory
    async with async_session_factory() as db:
        exists = (await db.execute(text(
            "SELECT 1 FROM agent_todos WHERE assigned_to_agent = :a AND artifacts->>'kind' = 'supplier_handoff' "
            "AND artifacts->>'supplier_id' = :s AND status IN ('not_started','in_progress') LIMIT 1"),
            {"a": AGENT, "s": supplier_id})).first()
        if exists:
            return None
        res = await db.execute(text("""
            INSERT INTO agent_todos (id, title, description, status, priority, assigned_to_agent, category, tags,
                                     artifacts, created_at, updated_at)
            VALUES (gen_random_uuid(), :t, :d, 'not_started', 'medium', :a, 'supplier',
                    ARRAY['nir_handoff']::varchar[], CAST(:art AS jsonb), NOW(), NOW()) RETURNING id::text"""),
            {"t": f"Onboard supplier {name[:80]}", "a": AGENT,
             "d": f"NIR candidate activated by {approved_by}. BOAZ: check onboarding prerequisites (offers, connector, "
                  "qualification gaps). Activation does not authorize messages, orders or payments.",
             "art": json.dumps({"kind": "supplier_handoff", "supplier_id": supplier_id, "from": "NIR"})})
        tid = res.scalar()
        await db.commit()
        return tid
