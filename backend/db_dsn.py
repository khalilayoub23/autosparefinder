"""
Script: db_dsn.py
Purpose: The ONE place standalone scripts (importers, harvesters, scrapers, maintenance)
         get the catalog database credential from — the environment, never source code
         (FIXES_TRACKER #61, 2026-10-03).
Why: the production DB password was pasted as a literal DSN into 54 tracked files
     ("postgresql://autospare:<password>@postgres_catalog:5432/autospare"). Rotating the
     password would have required editing 54 files, and the secret is in git history.
Process:
  - DB_PASSWORD: taken from the DB_PASSWORD environment variable if set (host-side runs,
    same name docker-compose.yml interpolates), otherwise parsed out of DATABASE_URL (what
    the backend container actually has). Empty string if neither is configured — the
    connection then fails with an authentication error instead of this module crashing
    an importing app.
  - catalog_dsn(): DATABASE_URL in plain libpq/asyncpg form (no "+asyncpg").
  Scripts keep their own host/port/dbname and embed only the password:
      DB_DSN = f"postgresql://autospare:{_DB_PW}@postgres_catalog:5432/autospare"
Data Imported/Modified: none.
Data Sources: environment (DATABASE_URL / DB_PASSWORD), set by docker-compose from .env.
Missing Data Delegation: n/a.
Last Updated: 2026-10-03
"""
from __future__ import annotations

import os
from urllib.parse import unquote, urlsplit


def db_password() -> str:
    """The catalog DB password from the environment. Raises if it is not configured."""
    pw = os.environ.get("DB_PASSWORD")
    if pw:
        return pw
    url = os.environ.get("DATABASE_URL", "")
    if url:
        parsed = urlsplit(url.replace("+asyncpg", "", 1).replace("+psycopg2", "", 1))
        if parsed.password:
            return unquote(parsed.password)
    raise RuntimeError("database credential is not configured: set DATABASE_URL (or DB_PASSWORD)")


def catalog_dsn() -> str:
    """DATABASE_URL as a plain postgresql:// DSN."""
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


def _resolve_or_empty() -> str:
    try:
        return db_password()
    except RuntimeError:
        return ""


DB_PASSWORD: str = _resolve_or_empty()
