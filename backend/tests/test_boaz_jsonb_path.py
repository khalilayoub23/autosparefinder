"""BOAZ supplier-record merge (2026-11-13): the old jsonb_set path failed in asyncpg ('{boaz,last_mail}' string) AND would
have been a silent no-op without credentials.boaz. MERGE_SQL must create and update the nested key."""
import asyncio
import json

from sqlalchemy import text

import boaz_tasks
from BACKEND_DATABASE_MODELS import async_session_factory

# same expression as MERGE_SQL, applied to a literal so no supplier row is needed
EXPR = boaz_tasks.MERGE_SQL.split("SET credentials = ", 1)[1].split(", updated_at")[0].replace("credentials", "CAST(:c AS jsonb)")


def _merge(creds, key, value):
    async def run():
        async with async_session_factory() as db:
            r = await db.execute(text("SELECT (" + EXPR + ")::text"), {"c": json.dumps(creds), "k": key, "v": json.dumps(value)})
            return json.loads(r.scalar())
    return asyncio.run(run())


def test_creates_boaz_key_when_missing():
    assert _merge({"k": 1}, "last_mail", {"ref": "x"}) == {"k": 1, "boaz": {"last_mail": {"ref": "x"}}}


def test_keeps_siblings_and_replaces_same_key():
    out = _merge({"boaz": {"onboarding": {"a": 1}, "last_mail": {"ref": "old"}}, "q": 2}, "last_mail", {"ref": "new"})
    assert out == {"boaz": {"onboarding": {"a": 1}, "last_mail": {"ref": "new"}}, "q": 2}


def test_source_uses_merge_sql():
    import inspect
    assert "MERGE_SQL" in inspect.getsource(boaz_tasks.pg_deps) and "jsonb_set" not in inspect.getsource(boaz_tasks.pg_deps)
