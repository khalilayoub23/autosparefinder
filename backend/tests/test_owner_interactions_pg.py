"""Owner interactions on REAL Postgres (#157): migration 0043 DDL, atomic transitions under concurrency, supersede,
and Resume persistence across store re-instantiation (a backend restart). Runs only when OWNER_IX_PG_DSN points at an
isolated, disposable database (sandbox); skipped otherwise. Never point it at production."""
import asyncio
import importlib.util
import os
from pathlib import Path

import pytest

import owner_interactions as oi
import owner_poll as op

DSN = os.getenv("OWNER_IX_PG_DSN", "")
pytestmark = pytest.mark.skipif(not DSN, reason="OWNER_IX_PG_DSN not set (isolated sandbox DB only)")


def _migration():
    p = Path(__file__).resolve().parent.parent / "alembic_pii" / "versions" / "0043_owner_interactions.py"
    spec = importlib.util.spec_from_file_location("m0043", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


async def _factory():
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    eng = create_async_engine(DSN, pool_size=12)
    m = _migration()
    async with eng.begin() as c:
        for st in m.DOWNGRADE_SQL[1:]:
            await c.execute(text(st))
        for st in m.UPGRADE_SQL:
            await c.execute(text(st))
    return eng, async_sessionmaker(eng, expire_on_commit=False)


async def _scenario():
    from owner_interactions_store import PgStore
    eng, f = await _factory()
    try:
        s1 = PgStore(f)
        ix = await oi.create_from_choices(s1, op.post_choices("ab12cd34"), body="v1")
        # concurrent approve taps: exactly one executes
        res = await asyncio.gather(*[oi.resolve_response(PgStore(f), {"interaction_id": ix.id, "action_id": "approve"},
                                                         sender_is_owner=True) for _ in range(8)])
        assert sorted(r.status for r in res) == ["execute"] + ["reply"] * 7

        # resume across a "restart": tap on one store instance, bind on a brand-new one
        ix2 = await oi.create_from_choices(s1, op.email_choices("e1e2e3e4", has_draft=True), body="mail v1")
        r = await oi.resolve_response(s1, {"interaction_id": ix2.id, "action_id": "edit"}, sender_is_owner=True)
        assert r.status == "followup"
        await s1.set_prompt_message_id(ix2.id, "PROMPT1")
        s2 = PgStore(f)                                              # re-instantiated (new process)
        got = await s2.get(ix2.id)
        assert got.state == "awaiting_input" and got.continuation["verb"] == "ערוךמייל" and got.prompt_message_id == "PROMPT1"
        binds = await asyncio.gather(*[oi.bind_followup(PgStore(f), "נוסח חדש", "PROMPT1") for _ in range(5)])
        bound = [b for b in binds if b and b.status == "bound"]
        assert len(bound) == 1 and bound[0].command == "ערוךמייל e1e2e3e4 נוסח חדש"
        assert (await s2.get(ix2.id)).state == "completed"

        # supersede: v2 of the same entity expires v1's buttons
        a = await oi.create_from_choices(s2, op.post_choices("ffff0000"), body="post v1")
        b = await oi.create_from_choices(s2, op.post_choices("ffff0000"), body="post v2")
        assert a.id != b.id and (await s2.get(a.id)).state == "expired" and (await s2.get(b.id)).state == "open"
        same = await oi.create_from_choices(s2, op.post_choices("ffff0000"), body="post v2")
        assert same.id == b.id, "the same displayed version is idempotent"

        # lapse + cancel + release round-trips
        await oi.resolve_response(s2, {"interaction_id": b.id, "action_id": "change"}, sender_is_owner=True)
        assert (await oi.bind_followup(s2, "בטל")).status == "cancelled" and (await s2.get(b.id)).state == "open"
        from sqlalchemy import text
        async with f() as s:
            kinds = [r[0] for r in (await s.execute(text(
                "SELECT kind FROM owner_interaction_events WHERE interaction_id=:i ORDER BY id"), {"i": ix2.id})).all()]
        assert kinds[:3] == ["created", "followup_started", "followup_received"]
    finally:
        await eng.dispose()


def test_pg_transitions_persistence_and_concurrency():
    asyncio.get_event_loop_policy().new_event_loop().run_until_complete(_scenario())
