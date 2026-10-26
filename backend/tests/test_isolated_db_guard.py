"""tests/isolated_db_guard.py must refuse production on every axis (FIXES_TRACKER #69 §10)."""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import isolated_db_guard as g  # noqa: E402

SANDBOX = {"RUN_DB_MUTATION_TESTS": "1", "ENVIRONMENT": "sandbox",
           "DATABASE_URL": "postgresql+asyncpg://u:p@sandbox_postgres:5432/autospare"}


def test_isolated_configuration_is_accepted():
    assert g.static_refusal(SANDBOX) is None


@pytest.mark.parametrize("change,why", [
    ({"RUN_DB_MUTATION_TESTS": ""}, "RUN_DB_MUTATION_TESTS"),
    ({"ENVIRONMENT": "production"}, "isolated environment"),
    ({"ENVIRONMENT": ""}, "isolated environment"),
    ({"DATABASE_URL": "postgresql+asyncpg://u:p@postgres_catalog:5432/autospare"}, "production host"),
    ({"DATABASE_URL": "postgresql+asyncpg://u:p@localhost:5432/autospare"}, "production host"),
    ({"DATABASE_URL": "postgresql+asyncpg://u:p@127.0.0.1:5432/autospare"}, "production host"),
    ({"DATABASE_URL": ""}, "production host"),
])
def test_every_production_axis_is_refused(change, why):
    assert why in (g.static_refusal({**SANDBOX, **change}) or "")


def test_flag_alone_does_not_unlock_the_production_container():
    prod = {"RUN_DB_MUTATION_TESTS": "1", "ENVIRONMENT": "production",
            "DATABASE_URL": "postgresql+asyncpg://u:p@postgres_catalog:5432/autospare"}
    assert g.static_refusal(prod)


class _Db:
    def __init__(self, parts, has_prod_table):
        self.parts, self.prod = parts, has_prod_table

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        value = (min(self.parts, g.PRODUCTION_MIN_PARTS + 1) if "parts_catalog" in sql
                 else ("night_pipeline_runs" if self.prod else None))
        class R:
            def scalar(_self):
                return value
        return R()


def test_live_check_refuses_a_production_sized_or_production_shaped_server():
    assert "production-scale" in asyncio.run(g.live_refusal(_Db(4_131_554, False)))
    assert "production-only table" in asyncio.run(g.live_refusal(_Db(0, True)))
    assert asyncio.run(g.live_refusal(_Db(0, False))) is None


@pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="needs a database")
def test_the_connected_database_verdict_matches_the_environment():
    """Read-only. In production both checks refuse; in the sandbox both accept."""
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import NullPool

    async def go():
        e = create_async_engine(os.environ["DATABASE_URL"], poolclass=NullPool)
        try:
            async with sessionmaker(e, class_=AsyncSession)() as db:
                return await g.live_refusal(db)
        finally:
            await e.dispose()
    live = asyncio.run(go())
    if (os.getenv("ENVIRONMENT") or "").lower() == "production":
        assert live is not None and g.static_refusal() is not None
    else:
        assert live is None


def test_versioned_schema_never_includes_the_production_fingerprint_table():
    """#88: alembic 0062 versions production's out-of-band tables so migrated databases match production — but it
    must NEVER create PRODUCTION_ONLY_TABLE: its absence is how live_refusal() tells a non-production database apart."""
    import re
    backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    creators = []
    files = [os.path.join(backend, d, "versions", f) for d in ("alembic", "alembic_pii")
             for f in sorted(os.listdir(os.path.join(backend, d, "versions")))]
    for f in files:
        if f.endswith(".py"):
            src = open(f, encoding="utf-8").read()
            for table in g.PRODUCTION_ONLY_TABLES:
                if re.search(rf"CREATE TABLE[^(]*\b{table}\b", src):
                    creators.append((f, table))
    assert creators == [], f"a migration creates the production fingerprint table: {creators}"


# ── #101 (2026-10-22): the guard checks the database a test ACTUALLY connects to ───────────────────────────────
@pytest.mark.parametrize("host,ok", [
    ("sandbox_postgres", True), ("h78disp-sandbox_postgres-1", True),            # isolated
    ("postgres_catalog", False), ("postgres_pii", False), ("POSTGRES_PII", False),  # protected (any case)
    ("localhost", False), ("127.0.0.1", False), ("::1", False),
    ("", False), (None, False),                                                     # missing
])
def test_target_refusal_by_host(host, ok):
    assert (g.target_refusal(host, SANDBOX) is None) is ok


@pytest.mark.parametrize("url,ok", [
    ("postgresql+asyncpg://u:p@sandbox_postgres:5432/autospare_pii", True),
    ("postgresql://u:p@sandbox_postgres/x", True),
    ("postgresql+psycopg2://u:p@sandbox_postgres/x", True),
    ("postgresql+asyncpg://u:p@postgres_pii:5432/autospare_pii", False),
    ("postgresql://u:p@postgres_catalog/autospare", False),
    ("", False), (None, False), ("not a url", False), ("postgresql://[bad", False),
])
def test_url_refusal_checks_the_given_url_not_database_url(url, ok):
    # SANDBOX's DATABASE_URL is isolated; the verdict must follow the URL actually used (e.g. DATABASE_PII_URL)
    assert (g.url_refusal(url, SANDBOX) is None) is ok


@pytest.mark.parametrize("env_label", ["production", "Production", "prod", "staging", "sandbox-prod", "", None])
def test_misleading_or_missing_environment_label_is_refused(env_label):
    env = {**SANDBOX, "ENVIRONMENT": env_label} if env_label is not None else \
        {k: v for k, v in SANDBOX.items() if k != "ENVIRONMENT"}
    assert "isolated environment" in (g.target_refusal("sandbox_postgres", env) or "")


def test_an_isolated_label_cannot_unlock_a_production_host():
    for host in ("postgres_catalog", "postgres_pii"):
        assert "production host" in g.target_refusal(host, SANDBOX)


class _Pii(_Db):
    async def execute(self, stmt, params=None):
        sql = str(stmt)
        value = 0 if "parts_catalog" in sql else ("part_price_watches" if "part_price_watches" in sql else None)
        class R:
            def scalar(_self):
                return value
        return R()


def test_live_check_recognises_the_production_pii_database():
    assert "part_price_watches" in asyncio.run(g.live_refusal(_Pii(0, False)))


class _Cur:
    def __init__(self, parts, present):
        self.parts, self.present, self.last = parts, set(present), None

    def execute(self, sql, params=None):
        self.last = sql

    def fetchone(self):
        if "parts_catalog" in self.last:
            return (min(self.parts, g.PRODUCTION_MIN_PARTS + 1),)
        return (next((t for t in self.present if t in self.last), None),)


@pytest.mark.parametrize("parts,present,refused", [
    (4_131_554, [], "production-scale"), (0, ["night_pipeline_runs"], "night_pipeline_runs"),
    (0, ["part_price_watches"], "part_price_watches"), (0, [], None)])
def test_dbapi_live_check(parts, present, refused):
    verdict = g.live_refusal_dbapi(_Cur(parts, present))
    assert (verdict is None) if refused is None else (refused in verdict)


@pytest.mark.skipif(g.static_refusal() is not None, reason=f"isolated DB only: {g.static_refusal()}")
@pytest.mark.parametrize("table", list(g.PRODUCTION_ONLY_TABLES))
def test_a_real_server_with_a_production_marker_is_refused_by_both_live_checks(table):
    """On the ISOLATED database: create a production marker inside a transaction, prove both live checks refuse,
    roll back (nothing persists)."""
    import psycopg2
    from urllib.parse import urlparse
    u = urlparse(os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://"))
    conn = psycopg2.connect(host=u.hostname, port=u.port or 5432, dbname=u.path.lstrip("/"), user=u.username,
                            password=u.password)
    try:
        with conn.cursor() as cur:
            assert g.live_refusal_dbapi(cur) is None
            cur.execute(f"CREATE TABLE public.{table} (id int)")
            assert table in g.live_refusal_dbapi(cur)
    finally:
        conn.rollback()
        with conn.cursor() as cur:
            cur.execute(f"SELECT to_regclass('public.{table}')")
            assert cur.fetchone()[0] is None                       # rolled back: the isolated DB is unchanged
        conn.close()


@pytest.mark.skipif(g.url_refusal(os.getenv("DATABASE_PII_URL")) is not None,
                    reason=f"isolated PII DB only: {g.url_refusal(os.getenv('DATABASE_PII_URL'))}")
def test_both_live_checks_run_on_a_real_database_without_the_catalog():
    """The PII database has no parts_catalog. A single CASE statement naming it failed there with UndefinedTable
    (found by the first full run of #101/#102); both live checks must answer, read-only, on such a database."""
    import psycopg2
    from urllib.parse import urlparse
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool
    url = os.environ["DATABASE_PII_URL"]
    u = urlparse(url.replace("postgresql+asyncpg://", "postgresql://"))
    conn = psycopg2.connect(host=u.hostname, port=u.port or 5432, dbname=u.path.lstrip("/"), user=u.username,
                            password=u.password)
    try:
        conn.set_session(readonly=True)
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.parts_catalog')")
            assert cur.fetchone()[0] is None                       # really a database without the catalog
            assert g.live_refusal_dbapi(cur) is None
    finally:
        conn.close()

    async def go():
        e = create_async_engine(url, poolclass=NullPool)
        try:
            async with e.connect() as c:
                return await g.live_refusal(c)
        finally:
            await e.dispose()
    assert asyncio.run(go()) is None
