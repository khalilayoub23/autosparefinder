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
  Two implementations with the same contract: PgStore (production) and InMemoryStore (tests,
  dry runs). sendable is always written false; the table also enforces CHECK (sendable = false).
Data Imported/Modified: email_agent_messages (PII DB).
Last Updated: 2026-10-04
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from email_agent.redaction import redact

_JSON_FIELDS = ("risk_flags", "context", "attachments", "verification")
_RESULT_FIELDS = (
    "rfc822_message_id", "sender_email", "subject", "received_at", "classification", "confidence",
    "reason", "risk_flags", "policy_tier", "recommended_action", "requires_human", "context",
    "attachments", "draft_subject", "draft_body", "draft_reason", "gmail_draft_id", "verification",
)


class InMemoryStore:
    def __init__(self):
        self.rows: Dict[str, Dict[str, Any]] = {}

    async def claim(self, message_id: str, thread_id: str, max_attempts: int) -> str:
        row = self.rows.get(message_id)
        if row is None:
            self.rows[message_id] = {"gmail_message_id": message_id, "gmail_thread_id": thread_id,
                                     "status": "processing", "attempts": 1, "sendable": False,
                                     "draft_attempted_at": None, "gmail_draft_id": None}
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

    async def stats(self) -> Dict[str, Any]:
        out: Dict[str, int] = {}
        for r in self.rows.values():
            out[r["status"]] = out.get(r["status"], 0) + 1
        return {"by_status": out, "requires_human": sum(1 for r in self.rows.values() if r.get("requires_human")
                                                        and r["status"] == "processed")}


class PgStore:
    def __init__(self, session_factory=None):
        self._factory = session_factory

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
