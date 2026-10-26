"""Guard for tests that change a database (DDL or DML).

The backend container's DATABASE_URL IS production. On 2026-10-18/19 a test ran
ensure_part_vehicle_fitment_table() there: an ALTER TABLE queued for an exclusive lock and every
fitment read/write queued behind it for ~40 minutes, then a full-table DELETE ran on live data
(FIXES_TRACKER #69 §10). A flag alone is not enough, so a mutating test needs ALL of:
  1. RUN_DB_MUTATION_TESTS=1                         (explicit opt-in)
  2. ENVIRONMENT in {sandbox, test}                   (never "production")
  3. a DB host that is not a production host          (postgres_catalog / postgres_pii / localhost)
     — the host the test ACTUALLY connects to (target_refusal / url_refusal), not just DATABASE_URL
  4. the server itself does not look like production  (checked live: catalogue size, prod-only tables)

Checks 1-3 are static: a refusal there means "not configured for writes" (callers SKIP, before connecting).
Check 4 runs on the open connection BEFORE the first write: a refusal there means the configuration claims to be
isolated but the server is production (callers FAIL, never skip). #101 (2026-10-22): tests that connect via other
variables (DB_HOST, DATABASE_PII_URL) must check THAT target; the PII database is recognised live by its own
production-only table, since the catalogue checks cannot see it.
"""
from __future__ import annotations

import os
from typing import Optional
from urllib.parse import urlparse

PRODUCTION_DB_HOSTS = frozenset({"postgres_catalog", "postgres_pii", "localhost", "127.0.0.1", "::1"})
ISOLATED_ENVIRONMENTS = frozenset({"sandbox", "test"})
PRODUCTION_MIN_PARTS = 100_000          # production holds ~4.1M; the sandbox 0
PRODUCTION_ONLY_TABLE = "night_pipeline_runs"          # catalog: created only by the night pipeline (production only)
# PII: exists only in production (no migration and no runtime code creates it; read-only comparison 2026-10-22).
# NEVER version it in a migration without first giving this guard another production marker (see #88/0062).
PII_PRODUCTION_ONLY_TABLE = "part_price_watches"
PRODUCTION_ONLY_TABLES = (PRODUCTION_ONLY_TABLE, PII_PRODUCTION_ONLY_TABLE)


def _host_of(url: Optional[str]) -> str:
    url = (url or "").replace("postgresql+asyncpg://", "postgresql://").replace("postgresql+psycopg2://", "postgresql://")
    try:
        return (urlparse(url).hostname or "").strip().lower()
    except ValueError:
        return ""


def target_refusal(host: Optional[str], env: Optional[dict] = None) -> Optional[str]:
    """Static checks 1-3 for the database host a test will actually connect to (None = may connect for writes)."""
    env = os.environ if env is None else env
    if (env.get("RUN_DB_MUTATION_TESTS") or "").strip() != "1":
        return "RUN_DB_MUTATION_TESTS is not 1"
    if (env.get("ENVIRONMENT") or "").strip().lower() not in ISOLATED_ENVIRONMENTS:
        return f"ENVIRONMENT={env.get('ENVIRONMENT')!r} is not an isolated environment"
    host = (host or "").strip().lower()
    if not host or host in PRODUCTION_DB_HOSTS:
        return f"database host {host!r} is a production host"
    return None


def url_refusal(url: Optional[str], env: Optional[dict] = None) -> Optional[str]:
    """target_refusal for a database URL (any driver prefix). An unparsable or empty URL is refused."""
    return target_refusal(_host_of(url), env)


def static_refusal(env: Optional[dict] = None) -> Optional[str]:
    """Why a mutating test must not run here (None = configuration looks isolated). Checks DATABASE_URL."""
    env = os.environ if env is None else env
    return url_refusal(env.get("DATABASE_URL"), env)


# Two statements, not one CASE: PostgreSQL resolves every table named in a statement before running it, so a CASE that
# guards `FROM parts_catalog` still fails with UndefinedTable on a database without it (the PII database).
_HAS_CATALOG_SQL = "SELECT to_regclass('public.parts_catalog') IS NOT NULL"
_SCALE_SQL = "SELECT count(*) FROM (SELECT 1 FROM parts_catalog LIMIT {n}) x"


def _verdict(parts, present_tables) -> Optional[str]:
    if int(parts or 0) > PRODUCTION_MIN_PARTS:
        return f"connected database has production-scale data (>{PRODUCTION_MIN_PARTS:,} parts)"
    for name in present_tables:
        return f"connected database has the production-only table {name}"
    return None


async def live_refusal(db) -> Optional[str]:
    """Inspect the connected server (SQLAlchemy async session/connection); refuse anything that looks like production."""
    from sqlalchemy import text
    parts = 0
    if (await db.execute(text(_HAS_CATALOG_SQL))).scalar():
        parts = (await db.execute(text(_SCALE_SQL.format(n=PRODUCTION_MIN_PARTS + 1)))).scalar()
    present = [t for t in PRODUCTION_ONLY_TABLES
               if (await db.execute(text(f"SELECT to_regclass('public.{t}')"))).scalar()]
    return _verdict(parts, present)


def live_refusal_dbapi(cur) -> Optional[str]:
    """live_refusal for a DB-API cursor (psycopg2)."""
    parts = 0
    cur.execute(_HAS_CATALOG_SQL)
    if cur.fetchone()[0]:
        cur.execute(_SCALE_SQL.format(n=PRODUCTION_MIN_PARTS + 1))
        parts = cur.fetchone()[0]
    present = []
    for t in PRODUCTION_ONLY_TABLES:
        cur.execute(f"SELECT to_regclass('public.{t}')")
        if cur.fetchone()[0]:
            present.append(t)
    return _verdict(parts, present)
