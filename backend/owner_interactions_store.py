"""
Script:  owner_interactions_store.py
Purpose: Persistence for owner_interactions.py - PgStore (PII DB, migration 0043) and MemoryStore (tests).
Process: every state change is a single conditional UPDATE so consume / begin_followup are atomic: of two concurrent
         taps exactly one gets rowcount 1.
Data Imported/Modified: owner_interactions, owner_interaction_events (PII DB).
Data Sources: none.
Missing Data Delegation: n/a.
Last Updated: 2026-11-25
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Dict, List, Optional

from sqlalchemy import text

from owner_interactions import Action, Interaction


def _row_to_ix(r) -> Interaction:
    acts = r["actions"]
    if isinstance(acts, str):  # asyncpg returns jsonb as text
        acts = json.loads(acts)
    return Interaction(
        id=r["id"], agent=r["agent"], task_ref=r["task_ref"] or "", entity_id=r["entity_id"] or "",
        actions=[Action(a["id"], a["label"], a.get("command", "")) for a in acts], state=r["state"],
        created_at=r["created_at"], expires_at=r["expires_at"], wa_message_id=r["wa_message_id"] or "",
        consumed_action=r["consumed_action"] or "", pending_action=r["pending_action"] or "",
        idempotency_key=r["idempotency_key"] or "")


class PgStore:
    """`factory` is an async_sessionmaker for the PII DB (BACKEND_DATABASE_MODELS.pii_session_factory)."""

    def __init__(self, factory):
        self._f = factory

    async def insert(self, ix: Interaction) -> Interaction:
        async with self._f() as s:
            # idempotency: the same agent+task+commands while still open returns the existing interaction
            got = (await s.execute(text(
                "SELECT * FROM owner_interactions WHERE idempotency_key=:k AND state IN ('open','awaiting_input') "
                "AND expires_at > now() ORDER BY created_at DESC LIMIT 1"), {"k": ix.idempotency_key})).mappings().first()
            if got:
                return _row_to_ix(got)
            await s.execute(text(
                "INSERT INTO owner_interactions (id, agent, task_ref, entity_id, actions, state, created_at, expires_at, idempotency_key) "
                "VALUES (:id,:agent,:task,:ent,CAST(:acts AS jsonb),'open',:c,:e,:k)"),
                {"id": ix.id, "agent": ix.agent, "task": ix.task_ref, "ent": ix.entity_id,
                 "acts": json.dumps([a.__dict__ for a in ix.actions], ensure_ascii=False),
                 "c": ix.created_at, "e": ix.expires_at, "k": ix.idempotency_key})
            await s.commit()
        return ix

    async def get(self, interaction_id: str) -> Optional[Interaction]:
        async with self._f() as s:
            r = (await s.execute(text("SELECT * FROM owner_interactions WHERE id=:i"), {"i": interaction_id})).mappings().first()
        return _row_to_ix(r) if r else None

    async def set_wa_message_id(self, interaction_id: str, wa_message_id: str) -> None:
        async with self._f() as s:
            await s.execute(text("UPDATE owner_interactions SET wa_message_id=:m WHERE id=:i"), {"m": wa_message_id, "i": interaction_id})
            await s.commit()

    async def consume(self, interaction_id: str, action_id: str) -> bool:
        async with self._f() as s:
            res = await s.execute(text(
                "UPDATE owner_interactions SET state='consumed', consumed_action=:a, consumed_at=now() "
                "WHERE id=:i AND state IN ('open','awaiting_input') AND expires_at > now()"), {"i": interaction_id, "a": action_id})
            await s.commit()
            return res.rowcount == 1

    async def begin_followup(self, interaction_id: str, action_id: str) -> bool:
        async with self._f() as s:
            res = await s.execute(text(
                "UPDATE owner_interactions SET state='awaiting_input', pending_action=:a "
                "WHERE id=:i AND state='open' AND expires_at > now()"), {"i": interaction_id, "a": action_id})
            await s.commit()
            return res.rowcount == 1

    async def pending_followup(self) -> Optional[Interaction]:
        async with self._f() as s:
            r = (await s.execute(text(
                "SELECT * FROM owner_interactions WHERE state='awaiting_input' AND expires_at > now() "
                "ORDER BY created_at DESC LIMIT 1"))).mappings().first()
        return _row_to_ix(r) if r else None

    async def reopen(self, interaction_id: str) -> None:
        async with self._f() as s:
            await s.execute(text("UPDATE owner_interactions SET state='open', pending_action=NULL WHERE id=:i AND state='awaiting_input'"),
                            {"i": interaction_id})
            await s.commit()

    async def event(self, interaction_id: str, kind: str, detail: dict) -> None:
        try:
            async with self._f() as s:
                await s.execute(text("INSERT INTO owner_interaction_events (interaction_id, kind, detail) VALUES (:i,:k,CAST(:d AS jsonb))"),
                                {"i": interaction_id, "k": kind, "d": json.dumps(detail, ensure_ascii=False)})
                await s.commit()
        except Exception:  # the audit write must never break the owner's tap
            pass


class MemoryStore:
    """Single-process store for tests; mirrors PgStore's conditional-update semantics."""

    def __init__(self):
        self.rows: Dict[str, Interaction] = {}
        self.events: List[tuple] = []

    def _live(self, ix: Interaction) -> bool:
        return not ix.expires_at or ix.expires_at > datetime.now(timezone.utc)

    async def insert(self, ix):
        for r in self.rows.values():
            if r.idempotency_key == ix.idempotency_key and r.state in ("open", "awaiting_input") and self._live(r):
                return r
        self.rows[ix.id] = ix
        return ix

    async def get(self, i): return self.rows.get(i)

    async def set_wa_message_id(self, i, m): self.rows[i].wa_message_id = m

    async def consume(self, i, a):
        r = self.rows.get(i)
        if r and r.state in ("open", "awaiting_input") and self._live(r):
            r.state, r.consumed_action = "consumed", a
            return True
        return False

    async def begin_followup(self, i, a):
        r = self.rows.get(i)
        if r and r.state == "open" and self._live(r):
            r.state, r.pending_action = "awaiting_input", a
            return True
        return False

    async def pending_followup(self):
        c = [r for r in self.rows.values() if r.state == "awaiting_input" and self._live(r)]
        return max(c, key=lambda r: r.created_at) if c else None

    async def reopen(self, i):
        r = self.rows.get(i)
        if r and r.state == "awaiting_input":
            r.state, r.pending_action = "open", ""

    async def event(self, i, k, d): self.events.append((i, k, d))
