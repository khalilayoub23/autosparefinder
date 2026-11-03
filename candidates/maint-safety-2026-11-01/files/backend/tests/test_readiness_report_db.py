"""SP16 - the DATABASE checks of social.noa_ops.readiness_report, executed for real (2026-11-02 state-path audit, final gap).

Every test runs the REAL `readiness_report` / `autonomous_gate` against a throw-away database created INSIDE the isolated
sandbox Postgres (`asf_sp16_<random>`, dropped afterwards). Refused unless the repo's isolated-DB guard allows writes
(RUN_DB_MUTATION_TESTS=1, ENVIRONMENT sandbox/test, non-production host, server does not look like production).

The scratch schema is the sandbox DDL of group_comment_drafts / social_inbox / noa_scan_runs (pg_dump, 2026-11-02) - NOT a dump of
production, whose catalog schema is partly out-of-band (see CLAUDE.md "Out-of-band prod schema").

Check 13 (owner WhatsApp channel) is a bridge/Redis probe, not a database check, and is replaced by a fixed result here; check 11
(EOD loop) inspects the app process and is evaluated both as "outside the app" (not evaluable -> False) and with a stand-in task registry."""
import asyncio
import inspect
import json
import os
import subprocess
import sys
import types
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import isolated_db_guard as guard
from social import noa_ops

APP = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(guard.static_refusal() is not None, reason=f"isolated DB not configured: {guard.static_refusal()}")

DDL = [
    "CREATE TABLE group_targets (id uuid PRIMARY KEY DEFAULT gen_random_uuid())",
    """CREATE TABLE group_comment_drafts (
        id uuid DEFAULT gen_random_uuid() NOT NULL, group_target_id uuid, post_url text NOT NULL, post_text text DEFAULT ''::text NOT NULL,
        draft_comment text DEFAULT ''::text NOT NULL, relevance_score double precision DEFAULT 0.0 NOT NULL,
        status text DEFAULT 'pending_approval'::text NOT NULL, created_at timestamp with time zone DEFAULT now() NOT NULL,
        approved_at timestamp with time zone, posted_at timestamp with time zone, skipped_at timestamp with time zone, approved_by text,
        attempts integer DEFAULT 0 NOT NULL, last_error text, last_attempt_at timestamp with time zone,
        CONSTRAINT group_comment_drafts_status_check CHECK ((status = ANY (ARRAY['pending_approval'::text, 'approved'::text,
            'posted'::text, 'skipped'::text, 'failed'::text]))))""",
    """CREATE TABLE noa_scan_runs (
        id uuid DEFAULT gen_random_uuid() NOT NULL, source text NOT NULL, started_at timestamp with time zone NOT NULL,
        finished_at timestamp with time zone DEFAULT now() NOT NULL, items_scanned integer DEFAULT 0 NOT NULL,
        relevant integer DEFAULT 0 NOT NULL, rejected integer DEFAULT 0 NOT NULL, drafted integer DEFAULT 0 NOT NULL,
        duplicates integer DEFAULT 0 NOT NULL, failures integer DEFAULT 0 NOT NULL, session_failed boolean DEFAULT false NOT NULL,
        error text, detail jsonb)""",
    """CREATE TABLE social_inbox (
        id uuid DEFAULT gen_random_uuid() NOT NULL, platform character varying(24) NOT NULL, kind character varying(16) NOT NULL,
        external_id character varying(128) NOT NULL, parent_id character varying(128), author character varying(200), message text,
        permalink text, status character varying(20) DEFAULT 'new'::character varying NOT NULL, reply_text text,
        reply_external_id character varying(128), created_at timestamp with time zone DEFAULT now() NOT NULL,
        replied_at timestamp with time zone, approved_by character varying, attempts integer DEFAULT 0 NOT NULL, last_error text)""",
    "ALTER TABLE group_comment_drafts ADD CONSTRAINT group_comment_drafts_pkey PRIMARY KEY (id)",
    "ALTER TABLE noa_scan_runs ADD CONSTRAINT noa_scan_runs_pkey PRIMARY KEY (id)",
    "ALTER TABLE social_inbox ADD CONSTRAINT social_inbox_pkey PRIMARY KEY (id)",
    "ALTER TABLE social_inbox ADD CONSTRAINT social_inbox_platform_external_id_key UNIQUE (platform, external_id)",
    "ALTER TABLE group_comment_drafts ADD CONSTRAINT group_comment_drafts_group_target_id_fkey FOREIGN KEY (group_target_id) "
    "REFERENCES group_targets(id) ON DELETE CASCADE",
    "CREATE UNIQUE INDEX uq_group_comment_drafts_active_post ON group_comment_drafts USING btree (post_url) "
    "WHERE (status = ANY (ARRAY['pending_approval'::text, 'approved'::text, 'posted'::text, 'failed'::text]))",
]
CHANNEL_OK = {"usable": True, "state": "AUTHENTICATED", "detail": "stub - not a database check"}


def _admin_url() -> str:
    return os.environ["DATABASE_URL"]


def _raw(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://")


async def _make_scratch() -> str:
    import asyncpg
    admin = make_url(_admin_url())
    assert (admin.host or "").startswith("sandbox_"), f"refusing: {admin.host} is not a sandbox host"
    name = "asf_sp16_" + uuid.uuid4().hex[:10]
    conn = await asyncpg.connect(_raw(str(admin.render_as_string(hide_password=False))))
    try:
        refusal = await _live_refusal(conn)
        assert refusal is None, f"server looks like production: {refusal}"
        await conn.execute(f'CREATE DATABASE "{name}"')
    finally:
        await conn.close()
    url = admin.set(database=name).render_as_string(hide_password=False)
    c2 = await asyncpg.connect(_raw(url))
    try:
        for stmt in DDL:
            await c2.execute(stmt)
    finally:
        await c2.close()
    return url


async def _live_refusal(conn) -> object:
    parts = await conn.fetchval("SELECT count(*) FROM parts_catalog") if await conn.fetchval("SELECT to_regclass('parts_catalog') IS NOT NULL") else 0
    present = [r[0] for r in await conn.fetch("SELECT tablename FROM pg_tables WHERE tablename = ANY($1::text[])", list(guard.PRODUCTION_ONLY_TABLES))]
    return guard._verdict(parts, present)


async def _drop_scratch(url: str) -> None:
    import asyncpg
    u = make_url(url)
    assert u.database.startswith("asf_sp16_")
    conn = await asyncpg.connect(_raw(make_url(_admin_url()).render_as_string(hide_password=False)))
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{u.database}" WITH (FORCE)')
    finally:
        await conn.close()


class Scratch:
    """A scratch database + helpers. `session()` yields an AsyncSession bound to it (optionally read-only at the server level)."""

    def __init__(self, url: str):
        self.url = url
        self.engines = []

    def session(self, read_only: bool = False, url: str = None) -> AsyncSession:
        args = {"server_settings": {"default_transaction_read_only": "on"}} if read_only else {}
        eng = create_async_engine(url or self.url, connect_args=args)
        self.engines.append(eng)
        return AsyncSession(eng, expire_on_commit=False)

    async def sql(self, stmt: str, **p):
        async with self.session() as s:
            r = await s.execute(sa.text(stmt), p)
            await s.commit()
            return r

    async def scalar(self, stmt: str, **p):
        async with self.session() as s:
            return (await s.execute(sa.text(stmt), p)).scalar()

    async def fingerprint(self) -> dict:
        out = {}
        for t in ("group_comment_drafts", "social_inbox", "noa_scan_runs", "group_targets"):
            out[t] = await self.scalar(f"SELECT count(*) || ':' || coalesce(md5(string_agg(x::text, '' ORDER BY x::text)), '') FROM {t} x")
        out["schema"] = await self.scalar(
            "SELECT md5(string_agg(table_name || column_name || data_type, '' ORDER BY table_name, column_name)) FROM information_schema.columns "
            "WHERE table_schema='public'")
        out["indexes"] = await self.scalar("SELECT md5(string_agg(indexdef, '' ORDER BY indexname)) FROM pg_indexes WHERE schemaname='public'")
        return out

    async def run(self, db=None, flag_off=True):
        async with (db or self.session()) as s:
            with patch.object(noa_ops, "owner_channel_state", AsyncMock(return_value=CHANNEL_OK)):
                return await noa_ops.readiness_report(s)

    async def seed_healthy(self):
        await self.sql("INSERT INTO noa_scan_runs (source, started_at, items_scanned, relevant, drafted, detail) "
                       "VALUES ('group', now() - interval '1 hour', 40, 6, 3, '{}'::jsonb)")
        await self.sql("INSERT INTO noa_scan_runs (source, started_at, items_scanned, detail) "
                       "VALUES ('page', now() - interval '10 minutes', 5, '{\"facebook_ok\": true}'::jsonb)")
        await self.sql("INSERT INTO group_comment_drafts (post_url, status) VALUES ('https://x.invalid/p/1', 'pending_approval')")

    async def close(self):
        for e in self.engines:
            await e.dispose()
        await _drop_scratch(self.url)


@pytest.fixture
async def db(tmp_path, monkeypatch):
    monkeypatch.delenv(noa_ops.AUTONOMOUS_FLAG, raising=False)
    prof = tmp_path / "profile"
    monkeypatch.setenv("FB_PROFILE_DIR", str(prof))
    for k in ("NOA_GROUP_COMMENTS_PER_HOUR", "NOA_AUTONOMOUS_MAX_PER_HOUR", "NOA_AUTONOMOUS_MAX_PER_DAY"):
        monkeypatch.delenv(k, raising=False)
    sc = Scratch(await _make_scratch())
    sc.profile = prof
    yield sc
    await sc.close()


def by_n(rep):
    return {c["n"]: c for c in rep["checks"]}


def with_eod(running=True):
    task = types.SimpleNamespace(done=lambda: not running)
    mod = types.SimpleNamespace(_SUPERVISED_TASKS={"noa_eod_report_loop": task})
    return patch.dict(sys.modules, {"BACKEND_API_ROUTES": mod})


def put_profile_cookies(sc):
    (sc.profile / "Default").mkdir(parents=True)
    (sc.profile / "Default" / "Cookies").write_bytes(b"x")


# ── success ──────────────────────────────────────────────────────────────────
async def test_all_database_checks_pass_on_a_healthy_populated_database(db):
    await db.seed_healthy()
    put_profile_cookies(db)
    with with_eod():
        rep = await db.run()
    assert [c["n"] for c in rep["checks"]] == list(range(1, 14))
    failing = [(c["n"], c["detail"]) for c in rep["checks"] if not c["ok"]]
    assert failing == [], failing
    assert rep["ready_for_release"] is True and rep["gate_ok"] is True and rep["flag_on"] is False
    d = by_n(rep)
    assert "profile_present=True" in d[1]["detail"] and "items_scanned=40 relevant=6" in d[3]["detail"]
    assert d[4]["detail"] == "1 group drafts created in the last 14 days"
    assert d[6]["detail"] == "unique indexes present: ['social_inbox_platform_external_id_key', 'uq_group_comment_drafts_active_post']"
    assert d[9]["detail"].endswith("noa_scan_runs rows=2")


# ── empty ────────────────────────────────────────────────────────────────────
async def test_empty_tables_follow_the_contract_not_ready_but_no_exception_and_structure_checks_still_pass(db):
    with patch.dict(sys.modules):
        sys.modules.pop("BACKEND_API_ROUTES", None)             # outside the app process: no task registry at all
        rep = await db.run()                                    # schema only, zero rows, no profile
    d = by_n(rep)
    assert {n: c["ok"] for n, c in d.items()} == {1: False, 2: False, 3: False, 4: False, 5: True, 6: True, 7: True, 8: True,
                                                  9: False, 10: True, 11: False, 12: True, 13: True}
    assert d[1]["detail"] == "no group scan recorded yet" and d[2]["detail"] == "no page cycle recorded yet"
    assert d[3]["detail"] == "no group scan recorded yet"
    assert d[4]["detail"] == "0 group drafts created in the last 14 days"
    assert d[5]["detail"] == "missing_cols=[] stuck_in_approved=0"
    assert d[9]["detail"].endswith("noa_scan_runs rows=0") and "audit cols=ok" in d[9]["detail"]
    assert d[11]["detail"] == "task missing/finished"           # no registry -> the loop is not running -> not ok
    assert rep["ready_for_release"] is False and rep["gate_ok"] is False
    # a registry that cannot be read at all is reported as "not evaluable" - still not ok, never a pass
    with patch.dict(sys.modules, {"BACKEND_API_ROUTES": types.SimpleNamespace(_SUPERVISED_TASKS=None)}):
        d2 = by_n(await db.run())
    assert d2[11]["ok"] is False and d2[11]["detail"] == "not evaluable outside the app process"


async def test_missing_optional_records_do_not_make_a_check_pass(db):
    """A group scan but no page cycle, no profile: only the checks fed by the missing records fail."""
    await db.sql("INSERT INTO noa_scan_runs (source, started_at, items_scanned) VALUES ('group', now(), 3)")
    with with_eod():
        d = by_n(await db.run())
    assert d[3]["ok"] is True and d[2]["ok"] is False and d[1]["ok"] is False
    assert "profile_present=False" in d[1]["detail"]


# ── interpretation of results ────────────────────────────────────────────────
async def test_results_are_interpreted_correctly_for_unhealthy_data(db):
    put_profile_cookies(db)
    await db.sql("INSERT INTO noa_scan_runs (source, started_at, finished_at, items_scanned, session_failed) "
                 "VALUES ('group', now() - interval '60 hours', now() - interval '60 hours', 9, false)")          # stale
    await db.sql("INSERT INTO noa_scan_runs (source, started_at, items_scanned, error, detail) "
                 "VALUES ('page', now(), 4, 'boom', '{\"facebook_ok\": true}'::jsonb)")                              # errored
    await db.sql("INSERT INTO group_comment_drafts (post_url, status, approved_at) VALUES ('https://x.invalid/p/2', 'approved', now() - interval '2 hours')")
    with with_eod():
        d = by_n(await db.run())
    assert d[1]["ok"] is False and "60." in d[1]["detail"]                                  # stale group scan
    assert d[2]["ok"] is False and "error=boom" in d[2]["detail"]
    assert d[3]["ok"] is False
    assert d[5]["ok"] is False and d[5]["detail"] == "missing_cols=[] stuck_in_approved=1"  # approved >30 min and not posted
    await db.sql("UPDATE noa_scan_runs SET finished_at=now(), session_failed=true WHERE source='group'")
    with with_eod():
        d = by_n(await db.run())
    assert d[1]["ok"] is False and "session_failed=True" in d[1]["detail"]


async def test_schema_checks_report_what_is_missing(db):
    await db.sql("DROP INDEX uq_group_comment_drafts_active_post")
    await db.sql("ALTER TABLE group_comment_drafts DROP CONSTRAINT group_comment_drafts_status_check")
    await db.sql("ALTER TABLE group_comment_drafts DROP COLUMN last_error")
    await db.sql("ALTER TABLE social_inbox DROP COLUMN attempts")                  # (approved_by / approved_at are used by earlier queries: see below)
    d = by_n(await db.run())
    assert d[6]["ok"] is False and d[6]["detail"] == "unique indexes present: ['social_inbox_platform_external_id_key']"
    assert d[10]["ok"] is False and "terminal 'failed' state=False" in d[10]["detail"] and "present=False" in d[10]["detail"]
    assert d[5]["ok"] is False and "missing_cols=['last_error']" in d[5]["detail"]
    assert d[9]["ok"] is False and "audit cols=missing" in d[9]["detail"]


async def test_rate_limit_check_reads_the_ledger_and_the_caps(db, monkeypatch):
    d = by_n(await db.run())
    assert d[7]["ok"] is True and "group/hour=3 autonomous/hour=3 autonomous/day=10 (query ok, current=ok)" in d[7]["detail"]
    monkeypatch.setenv("NOA_AUTONOMOUS_MAX_PER_HOUR", "0")
    assert by_n(await db.run())[7]["ok"] is False                                           # a zero cap is "not active"


# ── database errors are reported, never turned into readiness ────────────────
@pytest.mark.parametrize("ddl", [
    "DROP TABLE noa_scan_runs",                                   # checks 1, 2, 9 query it
    "DROP TABLE group_comment_drafts",                            # checks 4, 5, 7
    "ALTER TABLE group_comment_drafts DROP COLUMN approved_at",   # the stuck-approval query itself
    "DROP TABLE social_inbox",                                    # check 7 (autonomous ledger)
    "ALTER TABLE social_inbox DROP COLUMN approved_by",           # check 7 uses it before check 9 could report "audit cols=missing"
])
async def test_database_errors_propagate_and_the_gate_fails_closed(db, ddl, monkeypatch):
    await db.seed_healthy()
    put_profile_cookies(db)
    await db.sql(ddl)
    with with_eod():
        with pytest.raises(sa.exc.DBAPIError) as ei:
            await db.run()
        assert "does not exist" in str(ei.value) or "UndefinedTable" in str(ei.value) or "UndefinedColumn" in str(ei.value)
        monkeypatch.setenv(noa_ops.AUTONOMOUS_FLAG, "1")
        noa_ops._GATE_CACHE.update(t=0.0)
        with patch.object(noa_ops, "owner_channel_state", AsyncMock(return_value=CHANNEL_OK)), patch.object(noa_ops, "_notify", AsyncMock()):
            async with db.session() as s:
                ok, why = await noa_ops.autonomous_gate(s)
    assert ok is False and why and why[0].startswith("gate_error:")
    noa_ops._GATE_CACHE.update(t=0.0, v=(False, ["not_evaluated"]))


async def test_unreachable_database_raises_instead_of_reporting(db, monkeypatch):
    dead = make_url(db.url).set(port=1).render_as_string(hide_password=False)               # nothing listens on port 1
    with pytest.raises((OSError, sa.exc.DBAPIError)):
        await db.run(db=db.session(url=dead))
    monkeypatch.setenv(noa_ops.AUTONOMOUS_FLAG, "1")
    noa_ops._GATE_CACHE.update(t=0.0)
    with patch.object(noa_ops, "_notify", AsyncMock()):
        async with db.session(url=dead) as s:
            ok, why = await noa_ops.autonomous_gate(s)
    assert ok is False and why[0].startswith("gate_error:")
    noa_ops._GATE_CACHE.update(t=0.0, v=(False, ["not_evaluated"]))


# ── read-only and repeatable ─────────────────────────────────────────────────
async def test_repeated_runs_change_nothing_and_work_on_a_read_only_server_session(db):
    await db.seed_healthy()
    put_profile_cookies(db)
    before = await db.fingerprint()
    with with_eod():
        first = await db.run()
        again = await db.run()
        ro = await db.run(db=db.session(read_only=True))            # the SERVER refuses any write in this session
    assert await db.fingerprint() == before
    assert [(c["n"], c["ok"], c["detail"]) for c in first["checks"]] == [(c["n"], c["ok"], c["detail"]) for c in again["checks"]] \
        == [(c["n"], c["ok"], c["detail"]) for c in ro["checks"]]
    assert await db.scalar("SHOW default_transaction_read_only") == "off"


async def test_every_statement_is_a_select_and_uses_only_the_session_it_was_given(db):
    class Spy:
        def __init__(self, s):
            self.s, self.sql = s, []

        async def execute(self, stmt, *a, **k):
            self.sql.append(str(stmt))
            return await self.s.execute(stmt, *a, **k)

        def __getattr__(self, name):
            raise AssertionError(f"readiness_report used db.{name} - it may only call execute()")

    async with db.session() as s:
        spy = Spy(s)
        with patch.object(noa_ops, "owner_channel_state", AsyncMock(return_value=CHANNEL_OK)):
            await noa_ops.readiness_report(spy)
    assert len(spy.sql) == 11, spy.sql
    for q in spy.sql:
        low = " ".join(q.lower().split())
        assert low.startswith("select"), q
        assert not any(w in low.split() for w in ("insert", "update", "delete", "alter", "create", "drop", "truncate")), q
    src = inspect.getsource(noa_ops.readiness_report)
    for forbidden in ("DATABASE_URL", "create_engine", "create_async_engine", "asyncpg.connect", "async_session_factory", "commit("):
        assert forbidden not in src, forbidden


async def test_database_used_is_the_one_passed_in_and_it_is_not_a_production_host(db):
    u = make_url(db.url)
    assert u.host.startswith("sandbox_") and u.host not in guard.PRODUCTION_DB_HOSTS and u.database.startswith("asf_sp16_")
    assert await db.scalar("SELECT current_database()") == u.database
    assert guard.target_refusal("postgres_catalog") is not None          # the guard itself refuses a production host


# ── independent of the working directory ─────────────────────────────────────
@pytest.mark.parametrize("cwd", ["/", "tmp", "tests"])
async def test_runs_identically_from_any_working_directory(db, tmp_path, cwd):
    await db.seed_healthy()
    put_profile_cookies(db)
    wd = {"/": "/", "tmp": str(tmp_path), "tests": str(APP / "tests")}[cwd]
    env = {**os.environ, "DATABASE_URL": db.url, "PYTHONPATH": str(APP), "FB_PROFILE_DIR": str(db.profile)}
    env.pop(noa_ops.AUTONOMOUS_FLAG, None)
    helper = ("import asyncio, json, sys, types\nfrom unittest.mock import AsyncMock, patch\nimport sqlalchemy as sa, os\n"
              "from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine\nfrom social import noa_ops\n"
              "async def go():\n    e = create_async_engine(os.environ['DATABASE_URL'])\n"
              "    task = types.SimpleNamespace(done=lambda: False)\n"
              "    sys.modules['BACKEND_API_ROUTES'] = types.SimpleNamespace(_SUPERVISED_TASKS={'noa_eod_report_loop': task})\n"
              "    with patch.object(noa_ops, 'owner_channel_state', AsyncMock(return_value="+repr(CHANNEL_OK)+")):\n"
              "        async with AsyncSession(e) as s:\n            r = await noa_ops.readiness_report(s)\n"
              "    await e.dispose()\n    print(json.dumps([(c['n'], c['ok'], c['detail']) for c in r['checks']] + [r['ready_for_release']]))\n"
              "asyncio.run(go())\n")
    p = subprocess.run([sys.executable, "-W", "ignore", "-c", helper], cwd=wd, env=env, capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, p.stderr[-1500:]
    got = json.loads(p.stdout.strip().splitlines()[-1])
    assert got[-1] is True and all(ok for _, ok, _ in got[:-1])
    with with_eod():
        inproc = await db.run()
    assert got[:-1] == [[c["n"], c["ok"], c["detail"]] for c in inproc["checks"]]
