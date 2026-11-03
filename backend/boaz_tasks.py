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
Data Imported/Modified: agent_todos (status/progress/metrics), suppliers.credentials.boaz (catalog DB).
Data Sources: agent_todos, suppliers, email_agent_messages (PII DB, by email ref).
Last Updated: 2026-10-27
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

from agent_identity import aliases, canonical

AGENT = canonical("BOAZ")
MAX_ATTEMPTS = 3
BATCH = int(os.getenv("BOAZ_TASK_BATCH", "10"))


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
class InMemoryTaskRepo:
    def __init__(self, todos: Optional[List[Dict[str, Any]]] = None):
        self.todos = todos or []

    async def claim(self, owner_names: List[str], limit: int) -> List[Dict[str, Any]]:
        out = []
        for t in self.todos:
            if len(out) >= limit:
                break
            if t.get("assigned_to_agent") in owner_names and t.get("status") == "not_started":
                t["status"] = "in_progress"
                out.append(t)
        return out

    async def finish(self, task_id: str, *, status: str, note: str, metrics: Dict[str, Any]) -> None:
        t = next(x for x in self.todos if x["id"] == task_id)
        t.update(status=status, progress_notes=note, metrics=metrics)

    async def open_tasks(self, owner_names: List[str]) -> List[Dict[str, Any]]:
        return [t for t in self.todos if t.get("assigned_to_agent") in owner_names
                and t.get("status") in ("not_started", "in_progress")]


class PgTaskRepo:
    def __init__(self, session_factory=None):
        self._factory = session_factory

    def _session(self):
        if self._factory is None:
            from BACKEND_DATABASE_MODELS import async_session_factory
            self._factory = async_session_factory
        return self._factory()

    async def claim(self, owner_names: List[str], limit: int) -> List[Dict[str, Any]]:
        from sqlalchemy import text
        async with self._session() as db:
            res = await db.execute(text("""
                UPDATE agent_todos SET status = 'in_progress', started_at = COALESCE(started_at, NOW()), updated_at = NOW()
                WHERE id IN (
                    SELECT id FROM agent_todos
                    WHERE assigned_to_agent = ANY(CAST(:names AS varchar[])) AND status = 'not_started'
                    ORDER BY CASE priority WHEN 'critical' THEN 1 WHEN 'high' THEN 2 WHEN 'medium' THEN 3 ELSE 4 END,
                             created_at
                    LIMIT :n FOR UPDATE SKIP LOCKED)
                RETURNING id::text AS id, title, description, category, priority, assigned_to_agent,
                          COALESCE(artifacts,'{}'::jsonb) AS artifacts, COALESCE(metrics,'{}'::jsonb) AS metrics"""),
                {"names": owner_names, "n": limit})
            rows = [dict(r._mapping) for r in res.fetchall()]
            await db.commit()
            return rows

    async def finish(self, task_id: str, *, status: str, note: str, metrics: Dict[str, Any]) -> None:
        from sqlalchemy import text
        async with self._session() as db:
            await db.execute(text("""
                UPDATE agent_todos SET status = CAST(:s AS varchar), progress_notes = CAST(:n AS text),
                       metrics = CAST(:m AS jsonb),
                       progress_pct = CASE WHEN CAST(:s AS varchar) = 'completed' THEN 100 ELSE progress_pct END,
                       completed_at = CASE WHEN CAST(:s AS varchar) = 'completed' THEN NOW() ELSE completed_at END,
                       updated_at = NOW()
                WHERE id = CAST(:i AS uuid)"""), {"s": status, "n": note[:2000], "m": json.dumps(metrics, default=str),
                                                    "i": task_id})
            await db.commit()

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


async def run_once(repo, deps: Dict[str, Any], notify=None, limit: int = BATCH) -> Dict[str, Any]:
    out = {"claimed": 0, "completed": 0, "escalated": 0, "retry": 0, "failed": 0}
    for task in await repo.claim(sorted(aliases(AGENT)), limit):
        out["claimed"] += 1
        metrics = _json(task.get("metrics"), {}) or {}
        boaz = dict(metrics.get("boaz") or {})
        kind = task_kind(task)
        handler = HANDLERS.get(kind)
        try:
            if handler is None:
                r = {"done": False, "result": {}, "escalate": f"no automated BOAZ handler for task type '{kind}' - "
                                                               "owner / manual handling"}
            else:
                r = await handler(task, deps)
        except Exception as e:
            boaz["attempts"] = int(boaz.get("attempts", 0)) + 1
            boaz["last_error"] = f"{type(e).__name__}: {str(e)[:200]}"
            if boaz["attempts"] >= MAX_ATTEMPTS:
                boaz["escalated"] = _now().isoformat()
                await repo.finish(task["id"], status="in_progress",
                                  note=f"BOAZ failed {boaz['attempts']}x: {boaz['last_error']} - escalated",
                                  metrics={**metrics, "boaz": boaz})
                out["failed"] += 1
                await _alert(notify, task, f"נכשל {boaz['attempts']} פעמים: {boaz['last_error'][:120]}")
            else:
                await repo.finish(task["id"], status="not_started", note=f"BOAZ attempt {boaz['attempts']} failed: "
                                  f"{boaz['last_error']} - will retry", metrics={**metrics, "boaz": boaz})
                out["retry"] += 1
            continue
        boaz.update(kind=kind, handled_at=_now().isoformat(), result=r.get("result"))
        if r.get("done"):
            await repo.finish(task["id"], status="completed", note=r.get("note") or "done by BOAZ",
                              metrics={**metrics, "boaz": boaz})
            out["completed"] += 1
        else:
            boaz["escalated"] = _now().isoformat()
            await repo.finish(task["id"], status="in_progress", note=f"BOAZ escalated: {r.get('escalate')}",
                              metrics={**metrics, "boaz": boaz})
            out["escalated"] += 1
            await _alert(notify, task, str(r.get("escalate"))[:300])
    return out


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
