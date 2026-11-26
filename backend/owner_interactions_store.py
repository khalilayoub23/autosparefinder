"""
Script:  owner_interactions_store.py
Purpose: Persistence for owner_interactions.py - PgStore (PII DB, migration 0043) and MemoryStore (tests).
Process: every state change is a single conditional UPDATE so consume / begin_followup / claim_followup are atomic:
         of two concurrent taps (or two deliveries of one follow-up text) exactly one gets rowcount 1.
         States: open -> consumed (one-shot action) | awaiting_input (resumable action) -> completed (follow-up text
         claimed) | open (cancelled / follow-up window lapsed); any open/awaiting -> expired (superseded by a newer
         version of the same agent+entity).
Data Imported/Modified: owner_interactions, owner_interaction_events (PII DB).
Data Sources: none.
Missing Data Delegation: n/a.
Last Updated: 2026-11-26
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Dict, List, Optional

from sqlalchemy import text

from owner_interactions import Action, Interaction


def _j(v, default):
    if v is None:
        return default
    return json.loads(v) if isinstance(v, str) else v   # asyncpg returns jsonb as text


def _row_to_ix(r) -> Interaction:
    acts = _j(r["actions"], [])
    return Interaction(
        id=r["id"], agent=r["agent"], task_ref=r["task_ref"] or "", entity_id=r["entity_id"] or "",
        actions=[Action(a["id"], a["label"], a.get("command", "")) for a in acts], state=r["state"],
        created_at=r["created_at"], expires_at=r["expires_at"], wa_message_id=r["wa_message_id"] or "",
        consumed_action=r["consumed_action"] or "", pending_action=r["pending_action"] or "",
        idempotency_key=r["idempotency_key"] or "", continuation=_j(r.get("continuation"), {}) or {},
        prompt_message_id=r.get("prompt_message_id") or "", followup_expires_at=r.get("followup_expires_at"))


_LIVE = "expires_at > now()"


class PgStore:
    """`factory` is an async_sessionmaker for the PII DB (BACKEND_DATABASE_MODELS.pii_session_factory)."""

    def __init__(self, factory):
        self._f = factory

    async def _rowcount(self, sql: str, params: dict) -> int:
        async with self._f() as s:
            res = await s.execute(text(sql), params)
            await s.commit()
            return res.rowcount or 0

    async def _rows(self, sql: str, params: dict) -> List[Interaction]:
        async with self._f() as s:
            rows = (await s.execute(text(sql), params)).mappings().all()
        return [_row_to_ix(r) for r in rows]

    async def insert(self, ix: Interaction) -> Interaction:
        async with self._f() as s:
            # idempotency: the same agent+task+commands+displayed text while still open returns the existing interaction
            got = (await s.execute(text(
                "SELECT * FROM owner_interactions WHERE idempotency_key=:k AND state IN ('open','awaiting_input') "
                f"AND {_LIVE} ORDER BY created_at DESC LIMIT 1"), {"k": ix.idempotency_key})).mappings().first()
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
        r = await self._rows("SELECT * FROM owner_interactions WHERE id=:i", {"i": interaction_id})
        return r[0] if r else None

    async def set_wa_message_id(self, interaction_id: str, wa_message_id: str) -> None:
        await self._rowcount("UPDATE owner_interactions SET wa_message_id=:m WHERE id=:i", {"m": wa_message_id, "i": interaction_id})

    async def set_prompt_message_id(self, interaction_id: str, message_id: str) -> None:
        await self._rowcount("UPDATE owner_interactions SET prompt_message_id=:m WHERE id=:i AND state='awaiting_input'",
                             {"m": message_id, "i": interaction_id})

    async def consume(self, interaction_id: str, action_id: str) -> bool:
        return await self._rowcount(
            "UPDATE owner_interactions SET state='consumed', consumed_action=:a, consumed_at=now() "
            f"WHERE id=:i AND state IN ('open','awaiting_input') AND {_LIVE}", {"i": interaction_id, "a": action_id}) == 1

    async def begin_followup(self, interaction_id: str, action_id: str, continuation: dict, until: datetime) -> bool:
        return await self._rowcount(
            "UPDATE owner_interactions SET state='awaiting_input', pending_action=:a, continuation=CAST(:c AS jsonb), "
            f"followup_expires_at=:u, prompt_message_id=NULL WHERE id=:i AND state='open' AND {_LIVE}",
            {"i": interaction_id, "a": action_id, "c": json.dumps(continuation, ensure_ascii=False), "u": until}) == 1

    async def awaiting(self) -> List[Interaction]:
        return await self._rows(
            f"SELECT * FROM owner_interactions WHERE state='awaiting_input' AND {_LIVE} AND followup_expires_at > now() "
            "ORDER BY created_at DESC LIMIT 20", {})

    async def lapse_followups(self) -> List[Interaction]:
        async with self._f() as s:
            rows = (await s.execute(text(
                "UPDATE owner_interactions SET state='open', pending_action=NULL, prompt_message_id=NULL "
                "WHERE state='awaiting_input' AND followup_expires_at <= now() RETURNING *"))).mappings().all()
            await s.commit()
        return [_row_to_ix(r) for r in rows]

    async def claim_followup(self, interaction_id: str) -> bool:
        return await self._rowcount(
            "UPDATE owner_interactions SET state='completed', consumed_action=pending_action, consumed_at=now() "
            f"WHERE id=:i AND state='awaiting_input' AND {_LIVE} AND followup_expires_at > now()", {"i": interaction_id}) == 1

    async def cancel_followup(self, interaction_id: str) -> bool:
        return await self._rowcount(
            "UPDATE owner_interactions SET state='open', pending_action=NULL, prompt_message_id=NULL "
            "WHERE id=:i AND state='awaiting_input'", {"i": interaction_id}) == 1

    async def release_followup(self, interaction_id: str) -> None:
        """The continuation failed before the agent changed anything: let the owner retry (back to open)."""
        await self._rowcount("UPDATE owner_interactions SET state='open', pending_action=NULL, consumed_action=NULL, "
                             "consumed_at=NULL WHERE id=:i AND state='completed'", {"i": interaction_id})

    async def supersede(self, agent: str, entity_id: str, keep_id: str) -> int:
        return await self._rowcount(
            "UPDATE owner_interactions SET state='expired' WHERE agent=:a AND entity_id=:e AND id<>:k "
            "AND state IN ('open','awaiting_input')", {"a": agent, "e": entity_id, "k": keep_id})

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

    @staticmethod
    def _now():
        return datetime.now(timezone.utc)

    def _live(self, ix: Interaction) -> bool:
        return not ix.expires_at or ix.expires_at > self._now()

    def _fu_live(self, ix: Interaction) -> bool:
        return bool(ix.followup_expires_at) and ix.followup_expires_at > self._now()

    async def insert(self, ix):
        for r in self.rows.values():
            if r.idempotency_key == ix.idempotency_key and r.state in ("open", "awaiting_input") and self._live(r):
                return r
        self.rows[ix.id] = ix
        return ix

    async def get(self, i): return self.rows.get(i)

    async def set_wa_message_id(self, i, m): self.rows[i].wa_message_id = m

    async def set_prompt_message_id(self, i, m):
        r = self.rows.get(i)
        if r and r.state == "awaiting_input":
            r.prompt_message_id = m

    async def consume(self, i, a):
        r = self.rows.get(i)
        if r and r.state in ("open", "awaiting_input") and self._live(r):
            r.state, r.consumed_action = "consumed", a
            return True
        return False

    async def begin_followup(self, i, a, cont, until):
        r = self.rows.get(i)
        if r and r.state == "open" and self._live(r):
            r.state, r.pending_action, r.continuation, r.followup_expires_at, r.prompt_message_id = "awaiting_input", a, dict(cont), until, ""
            return True
        return False

    async def awaiting(self):
        c = [r for r in self.rows.values() if r.state == "awaiting_input" and self._live(r) and self._fu_live(r)]
        return sorted(c, key=lambda r: r.created_at, reverse=True)

    async def lapse_followups(self):
        out = []
        for r in self.rows.values():
            if r.state == "awaiting_input" and not self._fu_live(r):
                r.state, r.pending_action, r.prompt_message_id = "open", "", ""
                out.append(r)
        return out

    async def claim_followup(self, i):
        r = self.rows.get(i)
        if r and r.state == "awaiting_input" and self._live(r) and self._fu_live(r):
            r.state, r.consumed_action = "completed", r.pending_action
            return True
        return False

    async def cancel_followup(self, i):
        r = self.rows.get(i)
        if r and r.state == "awaiting_input":
            r.state, r.pending_action, r.prompt_message_id = "open", "", ""
            return True
        return False

    async def release_followup(self, i):
        r = self.rows.get(i)
        if r and r.state == "completed":
            r.state, r.pending_action, r.consumed_action = "open", "", ""

    async def supersede(self, agent, entity_id, keep_id):
        n = 0
        for r in self.rows.values():
            if r.agent == agent and r.entity_id == entity_id and r.id != keep_id and r.state in ("open", "awaiting_input"):
                r.state = "expired"
                n += 1
        return n

    async def event(self, i, k, d): self.events.append((i, k, d))
