"""
Script: email_agent/store.py
Purpose: Idempotency + audit store for the Email Agent. One row per Gmail message in
         email_agent_messages (PII DB, alembic_pii 0039); the unique gmail_message_id is what
         guarantees a message never creates duplicate internal work or a duplicate draft.
Process:
  claim()               atomic INSERT .. ON CONFLICT: 'new' for an unseen message, 'retry' for a
                        row left in status 'retry' with attempts below the maximum, else 'skip'.
  mark_draft_attempt()  written BEFORE the Gmail draft call. If the process dies between the
                        call and save_result(), a later retry sees the marker without a draft id
                        and refuses to create a second draft (it escalates instead).
  save_result()         final audit record (classification, context, policy, draft, verification).
  mark_failed()         'retry' (retryable and attempts left) or terminal 'failed'.
  release()             hands a claim back without consuming an attempt (mailbox-level outage:
                        auth / scope failure is not the message's fault).
  Owner flow (0041): owner_pending / digest_pending / open_items / by_ref and the mark_* writers
                        used by email_agent/owner_flow.py. mark_sent() is only reached after
                        mark_send_approved(); the table enforces CHECK (sent_at IS NULL OR
                        send_approved_at IS NOT NULL).
  Two implementations with the same contract: PgStore (production) and InMemoryStore (tests,
  dry runs). sendable is always written false; the table also enforces CHECK (sendable = false).
Data Imported/Modified: email_agent_messages (PII DB).
Last Updated: 2026-10-27
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from email_agent.redaction import redact

_JSON_FIELDS = ("risk_flags", "context", "attachments", "verification")
_RESULT_FIELDS = (
    "rfc822_message_id", "sender_email", "subject", "received_at", "classification", "confidence",
    "reason", "risk_flags", "policy_tier", "recommended_action", "requires_human", "context",
    "attachments", "draft_subject", "draft_body", "draft_reason", "gmail_draft_id", "verification",
)


# Owner-flow columns (alembic_pii 0041). See email_agent/owner_flow.py.
_OWNER_DEFAULTS = {"owner_notified_at": None, "digest_at": None, "owner_closed_at": None, "task_ids": [],
                   "send_approved_at": None, "send_approved_by": None, "sent_at": None,
                   "gmail_sent_message_id": None, "send_error": None,
                   # supplier routing (alembic_pii 0042, email_agent/supplier_routing.py)
                   "source_folder": "inbox", "supplier_routed_at": None, "supplier_route": None}
_OWNER_COLS = ("id::text AS id, gmail_message_id, gmail_thread_id, sender_email, subject, classification, "
               "reason, risk_flags, recommended_action, requires_human, policy_tier, draft_subject, draft_body, "
               "gmail_draft_id, verification, context, status, created_at, owner_notified_at, digest_at, "
               "owner_closed_at, task_ids, send_approved_at, send_approved_by, sent_at, gmail_sent_message_id, "
               "send_error")
# alembic_pii 0042 columns. Selected only when the live schema has them: the container starts uvicorn even when
# `alembic upgrade` failed (docker-compose `... && ... &`), so new code must keep the 0041 owner flow working on
# an old schema and refuse ONLY the supplier-routing features (SupplierSchemaNotReady).
_SUPPLIER_COLS = ", source_folder, supplier_routed_at, supplier_route"


class SupplierSchemaNotReady(RuntimeError):
    """email_agent_messages has no 0042 supplier-routing columns (migration not applied)."""
_OPEN_SQL = ("status = 'processed' AND owner_closed_at IS NULL AND sent_at IS NULL AND "
             "(requires_human OR gmail_draft_id IS NOT NULL OR draft_body IS NOT NULL)")


def _is_open(r: Dict[str, Any]) -> bool:
    return (r.get("status") == "processed" and not r.get("owner_closed_at") and not r.get("sent_at")
            and bool(r.get("requires_human") or r.get("gmail_draft_id") or r.get("draft_body")))


class InMemoryStore:
    def __init__(self, supplier_schema: bool = True):
        self.rows: Dict[str, Dict[str, Any]] = {}
        self._supplier_schema = supplier_schema

    async def supplier_schema_ready(self) -> bool:
        return self._supplier_schema

    async def claim(self, message_id: str, thread_id: str, max_attempts: int) -> str:
        row = self.rows.get(message_id)
        if row is None:
            self.rows[message_id] = {"id": str(uuid.uuid4()), "gmail_message_id": message_id,
                                     "gmail_thread_id": thread_id, "status": "processing", "attempts": 1,
                                     "sendable": False, "draft_attempted_at": None, "gmail_draft_id": None,
                                     "created_at": datetime.now(timezone.utc), **_OWNER_DEFAULTS}
            return "new"
        if row["status"] == "retry" and row["attempts"] < max_attempts:
            row["status"] = "processing"
            row["attempts"] += 1
            return "retry"
        return "skip"

    async def get(self, message_id: str) -> Optional[Dict[str, Any]]:
        return self.rows.get(message_id)

    async def mark_draft_attempt(self, message_id: str) -> None:
        self.rows[message_id]["draft_attempted_at"] = datetime.now(timezone.utc).isoformat()

    async def save_result(self, message_id: str, record: Dict[str, Any]) -> None:
        row = self.rows[message_id]
        row.update({k: record.get(k) for k in _RESULT_FIELDS})
        row.update(status="processed", sendable=False, last_error=None)

    async def mark_failed(self, message_id: str, error: str, retryable: bool, max_attempts: int) -> str:
        row = self.rows[message_id]
        row["status"] = "retry" if retryable and row["attempts"] < max_attempts else "failed"
        row["last_error"] = redact(error)[:500]
        return row["status"]

    async def release(self, message_id: str) -> None:
        row = self.rows[message_id]
        row["status"] = "retry"
        row["attempts"] = max(0, row["attempts"] - 1)

    # ── owner flow (email_agent/owner_flow.py) ────────────────────────────────────────
    def _now(self):
        return datetime.now(timezone.utc)

    async def owner_pending(self, limit: int = 50) -> List[Dict[str, Any]]:
        rows = [r for r in self.rows.values() if r.get("status") == "processed"
                and r.get("owner_notified_at") is None and r.get("owner_closed_at") is None]
        return sorted(rows, key=lambda r: r["created_at"])[:limit]

    async def digest_pending(self, limit: int = 200) -> List[Dict[str, Any]]:
        rows = [r for r in self.rows.values() if r.get("status") == "processed" and r.get("digest_at") is None]
        return sorted(rows, key=lambda r: r["created_at"])[:limit]

    async def last_digest_at(self):
        vals = [r["digest_at"] for r in self.rows.values() if r.get("digest_at")]
        return max(vals) if vals else None

    async def open_items(self, limit: int = 20) -> List[Dict[str, Any]]:
        rows = [r for r in self.rows.values() if _is_open(r)]
        return sorted(rows, key=lambda r: r["created_at"], reverse=True)[:limit]

    async def by_ref(self, ref: str) -> List[Dict[str, Any]]:
        ref = (ref or "").strip().lower()
        return [r for r in self.rows.values() if ref and str(r.get("id", "")).startswith(ref)]

    def _by_id(self, row_id: str) -> Dict[str, Any]:
        return next(r for r in self.rows.values() if r.get("id") == row_id)

    async def mark_owner_notified(self, row_id: str) -> None:
        self._by_id(row_id)["owner_notified_at"] = self._now()

    async def mark_digested(self, row_ids: List[str]) -> None:
        for i in row_ids:
            self._by_id(i)["digest_at"] = self._now()

    async def set_task_ids(self, row_id: str, task_ids: List[str]) -> None:
        self._by_id(row_id)["task_ids"] = list(task_ids)

    async def close_item(self, row_id: str) -> None:
        self._by_id(row_id)["owner_closed_at"] = self._now()

    async def set_source_folder(self, message_id: str, folder: str) -> None:
        self.rows[message_id]["source_folder"] = folder

    async def supplier_route_pending(self, limit: int = 50) -> List[Dict[str, Any]]:
        rows = [r for r in self.rows.values() if r.get("status") == "processed" and r.get("supplier_routed_at") is None]
        return sorted(rows, key=lambda r: r["created_at"])[:limit]

    async def set_supplier_route(self, row_id: str, route: Dict[str, Any], requires_human: bool = False) -> None:
        r = self._by_id(row_id)
        r["supplier_route"], r["supplier_routed_at"] = dict(route), self._now()
        if requires_human:
            r["requires_human"] = True

    async def mark_send_approved(self, row_id: str, by: str) -> None:
        r = self._by_id(row_id)
        r["send_approved_at"], r["send_approved_by"] = self._now(), by

    async def mark_sent(self, row_id: str, gmail_message_id: str) -> None:
        r = self._by_id(row_id)
        if not r.get("send_approved_at"):                      # mirrors the table CHECK
            raise ValueError("sent_requires_approval")
        r["sent_at"], r["gmail_sent_message_id"], r["send_error"] = self._now(), gmail_message_id, None

    async def mark_send_error(self, row_id: str, error: str) -> None:
        self._by_id(row_id)["send_error"] = redact(error)[:500]

    async def stats(self) -> Dict[str, Any]:
        out: Dict[str, int] = {}
        for r in self.rows.values():
            out[r["status"]] = out.get(r["status"], 0) + 1
        return {"by_status": out, "requires_human": sum(1 for r in self.rows.values() if r.get("requires_human")
                                                        and r["status"] == "processed")}


class PgStore:
    def __init__(self, session_factory=None):
        self._factory = session_factory
        self._supplier_schema: Optional[bool] = None

    async def supplier_schema_ready(self) -> bool:
        """True once migration 0042 added the supplier-routing columns. Re-checked until it is True."""
        if self._supplier_schema:
            return True
        from sqlalchemy import text
        async with self._session() as db:
            n = (await db.execute(text(
                "SELECT count(*) FROM information_schema.columns WHERE table_name = 'email_agent_messages' "
                "AND column_name IN ('source_folder', 'supplier_routed_at', 'supplier_route')"))).scalar()
        self._supplier_schema = (n == 3)
        return self._supplier_schema

    async def _require_supplier_schema(self) -> None:
        if not await self.supplier_schema_ready():
            raise SupplierSchemaNotReady("migration 0042_supplier_lifecycle is not applied")

    def _session(self):
        if self._factory is None:
            from BACKEND_DATABASE_MODELS import pii_session_factory
            self._factory = pii_session_factory
        return self._factory()

    async def claim(self, message_id: str, thread_id: str, max_attempts: int) -> str:
        from sqlalchemy import text
        async with self._session() as db:
            res = await db.execute(text("""
                INSERT INTO email_agent_messages (gmail_message_id, gmail_thread_id, status, attempts)
                VALUES (:m, :t, 'processing', 1)
                ON CONFLICT (gmail_message_id) DO UPDATE
                   SET status = 'processing', attempts = email_agent_messages.attempts + 1, updated_at = NOW()
                 WHERE email_agent_messages.status = 'retry' AND email_agent_messages.attempts < :mx
                RETURNING attempts
            """), {"m": message_id, "t": thread_id, "mx": max_attempts})
            row = res.first()
            await db.commit()
        if row is None:
            return "skip"
        return "new" if row[0] == 1 else "retry"

    async def get(self, message_id: str) -> Optional[Dict[str, Any]]:
        from sqlalchemy import text
        async with self._session() as db:
            res = await db.execute(text(
                "SELECT gmail_message_id, gmail_thread_id, status, attempts, sendable, gmail_draft_id, "
                "draft_attempted_at::text AS draft_attempted_at, classification, recommended_action, "
                "requires_human, draft_body, last_error, verification, context "
                "FROM email_agent_messages WHERE gmail_message_id = :m"), {"m": message_id})
            row = res.first()
        return dict(row._mapping) if row else None

    async def mark_draft_attempt(self, message_id: str) -> None:
        from sqlalchemy import text
        async with self._session() as db:
            await db.execute(text("UPDATE email_agent_messages SET draft_attempted_at = NOW(), updated_at = NOW() "
                                  "WHERE gmail_message_id = :m"), {"m": message_id})
            await db.commit()

    async def save_result(self, message_id: str, record: Dict[str, Any]) -> None:
        from sqlalchemy import text
        params = {k: record.get(k) for k in _RESULT_FIELDS}
        for k in _JSON_FIELDS:
            empty = {} if k in ("context", "verification") else []
            params[k] = json.dumps(params[k] if params[k] is not None else empty, ensure_ascii=False, default=str)
        if not isinstance(params["received_at"], datetime):     # asyncpg binds timestamptz from a datetime
            params["received_at"] = None
        elif params["received_at"].tzinfo is None:
            params["received_at"] = params["received_at"].replace(tzinfo=timezone.utc)
        params["requires_human"] = bool(params["requires_human"])
        params["m"] = message_id
        async with self._session() as db:
            await db.execute(text("""
                UPDATE email_agent_messages SET
                    rfc822_message_id = :rfc822_message_id, sender_email = :sender_email, subject = :subject,
                    received_at = CAST(:received_at AS timestamptz), classification = :classification,
                    confidence = :confidence, reason = :reason, risk_flags = CAST(:risk_flags AS jsonb),
                    policy_tier = :policy_tier, recommended_action = :recommended_action,
                    requires_human = :requires_human, context = CAST(:context AS jsonb),
                    attachments = CAST(:attachments AS jsonb), draft_subject = :draft_subject,
                    draft_body = :draft_body, draft_reason = :draft_reason, gmail_draft_id = :gmail_draft_id,
                    verification = CAST(:verification AS jsonb), sendable = false,
                    status = 'processed', last_error = NULL, updated_at = NOW()
                WHERE gmail_message_id = :m
            """), params)
            await db.commit()

    async def mark_failed(self, message_id: str, error: str, retryable: bool, max_attempts: int) -> str:
        from sqlalchemy import text
        async with self._session() as db:
            res = await db.execute(text("""
                UPDATE email_agent_messages
                   SET status = CASE WHEN :r AND attempts < :mx THEN 'retry' ELSE 'failed' END,
                       last_error = :e, updated_at = NOW()
                 WHERE gmail_message_id = :m RETURNING status
            """), {"r": bool(retryable), "mx": max_attempts, "e": redact(error)[:500], "m": message_id})
            row = res.first()
            await db.commit()
        return row[0] if row else "failed"

    async def release(self, message_id: str) -> None:
        from sqlalchemy import text
        async with self._session() as db:
            await db.execute(text("UPDATE email_agent_messages SET status = 'retry', "
                                  "attempts = GREATEST(attempts - 1, 0), updated_at = NOW() "
                                  "WHERE gmail_message_id = :m"), {"m": message_id})
            await db.commit()

    # ── owner flow (email_agent/owner_flow.py) ────────────────────────────────────────
    async def _select(self, where: str, params: Dict[str, Any], order: str, limit: int) -> List[Dict[str, Any]]:
        from sqlalchemy import text
        async with self._session() as db:
            cols = _OWNER_COLS + (_SUPPLIER_COLS if await self.supplier_schema_ready() else "")
            res = await db.execute(text(f"SELECT {cols} FROM email_agent_messages WHERE {where} "
                                        f"ORDER BY {order} LIMIT :lim"), {**params, "lim": int(limit)})
            return [dict(r._mapping) for r in res.fetchall()]

    async def _exec(self, sql: str, params: Dict[str, Any]) -> None:
        from sqlalchemy import text
        async with self._session() as db:
            await db.execute(text(sql), params)
            await db.commit()

    async def owner_pending(self, limit: int = 50) -> List[Dict[str, Any]]:
        return await self._select("status = 'processed' AND owner_notified_at IS NULL AND owner_closed_at IS NULL",
                                  {}, "created_at", limit)

    async def digest_pending(self, limit: int = 200) -> List[Dict[str, Any]]:
        return await self._select("status = 'processed' AND digest_at IS NULL", {}, "created_at", limit)

    async def last_digest_at(self):
        from sqlalchemy import text
        async with self._session() as db:
            return (await db.execute(text("SELECT MAX(digest_at) FROM email_agent_messages"))).scalar()

    async def open_items(self, limit: int = 20) -> List[Dict[str, Any]]:
        return await self._select(_OPEN_SQL, {}, "created_at DESC", limit)

    async def by_ref(self, ref: str) -> List[Dict[str, Any]]:
        ref = "".join(ch for ch in (ref or "").lower() if ch in "0123456789abcdef-")
        if len(ref) < 4:
            return []
        return await self._select("id::text LIKE :p", {"p": ref + "%"}, "created_at", 5)

    async def mark_owner_notified(self, row_id: str) -> None:
        await self._exec("UPDATE email_agent_messages SET owner_notified_at = NOW(), updated_at = NOW() "
                         "WHERE id = CAST(:i AS uuid)", {"i": row_id})

    async def mark_digested(self, row_ids: List[str]) -> None:
        if row_ids:
            await self._exec("UPDATE email_agent_messages SET digest_at = NOW(), updated_at = NOW() "
                             "WHERE id = ANY(CAST(:ids AS uuid[]))", {"ids": list(row_ids)})

    async def set_task_ids(self, row_id: str, task_ids: List[str]) -> None:
        await self._exec("UPDATE email_agent_messages SET task_ids = CAST(:t AS jsonb), updated_at = NOW() "
                         "WHERE id = CAST(:i AS uuid)", {"i": row_id, "t": json.dumps(list(task_ids))})

    async def close_item(self, row_id: str) -> None:
        await self._exec("UPDATE email_agent_messages SET owner_closed_at = NOW(), updated_at = NOW() "
                         "WHERE id = CAST(:i AS uuid)", {"i": row_id})

    async def set_source_folder(self, message_id: str, folder: str) -> None:
        await self._require_supplier_schema()
        await self._exec("UPDATE email_agent_messages SET source_folder = :f, updated_at = NOW() "
                         "WHERE gmail_message_id = :m", {"f": folder, "m": message_id})

    async def supplier_route_pending(self, limit: int = 50) -> List[Dict[str, Any]]:
        await self._require_supplier_schema()
        return await self._select("status = 'processed' AND supplier_routed_at IS NULL", {}, "created_at", limit)

    async def set_supplier_route(self, row_id: str, route: Dict[str, Any], requires_human: bool = False) -> None:
        await self._require_supplier_schema()
        await self._exec("UPDATE email_agent_messages SET supplier_route = CAST(:r AS jsonb), supplier_routed_at = NOW(), "
                         "requires_human = requires_human OR :h, updated_at = NOW() WHERE id = CAST(:i AS uuid)",
                         {"i": row_id, "r": json.dumps(route, default=str), "h": bool(requires_human)})

    async def mark_send_approved(self, row_id: str, by: str) -> None:
        await self._exec("UPDATE email_agent_messages SET send_approved_at = NOW(), send_approved_by = :b, "
                         "updated_at = NOW() WHERE id = CAST(:i AS uuid)", {"i": row_id, "b": by[:40]})

    async def mark_sent(self, row_id: str, gmail_message_id: str) -> None:
        await self._exec("UPDATE email_agent_messages SET sent_at = NOW(), gmail_sent_message_id = :g, "
                         "send_error = NULL, updated_at = NOW() WHERE id = CAST(:i AS uuid)",
                         {"i": row_id, "g": gmail_message_id[:128]})

    async def mark_send_error(self, row_id: str, error: str) -> None:
        await self._exec("UPDATE email_agent_messages SET send_error = :e, updated_at = NOW() "
                         "WHERE id = CAST(:i AS uuid)", {"i": row_id, "e": redact(error)[:500]})

    async def stats(self) -> Dict[str, Any]:
        from sqlalchemy import text
        async with self._session() as db:
            res = await db.execute(text("SELECT status, COUNT(*) FROM email_agent_messages GROUP BY 1"))
            by_status = {r[0]: int(r[1]) for r in res.fetchall()}
            res = await db.execute(text("SELECT COUNT(*) FROM email_agent_messages "
                                        "WHERE status = 'processed' AND requires_human"))
            human = int(res.scalar() or 0)
        return {"by_status": by_status, "requires_human": human}

    async def recent(self, limit: int = 20) -> List[Dict[str, Any]]:
        from sqlalchemy import text
        async with self._session() as db:
            res = await db.execute(text(
                "SELECT gmail_message_id, gmail_thread_id, classification, confidence, recommended_action, "
                "requires_human, status, gmail_draft_id, (draft_body IS NOT NULL) AS has_draft, "
                "created_at::text AS created_at FROM email_agent_messages "
                "ORDER BY created_at DESC LIMIT :n"), {"n": max(1, min(int(limit), 100))})
            return [dict(r._mapping) for r in res.fetchall()]
