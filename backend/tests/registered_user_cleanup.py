"""Unique test identities + guarded removal of a user a test registered ITSELF (FIXES_TRACKER #102).

The duplicate-registration tests used to share one email with the registration test, so their outcome depended on
which ran first. Each now registers its own unique user, proves a second registration is refused, and removes that
user here. Removal is a DIRECT write to the PII database, so it is guarded like every mutating test
(tests/isolated_db_guard.py): static checks on the URL actually used, then the live check, before the DELETE. Only
`@example.com` test addresses can be removed. A just-registered user has only cascading children
(user_profiles, two_factor_codes), so one DELETE removes everything the registration wrote.
"""
from __future__ import annotations

import os
import uuid
from urllib.parse import urlparse

import isolated_db_guard as g

TEST_EMAIL_DOMAIN = "@example.com"


def unique_identity(prefix: str):
    """(email, phone) owned by one test: never shared, never reused across runs."""
    rid = uuid.uuid4().hex
    digits = "".join(str(int(c, 16) % 10) for c in rid)[:7]
    return f"{prefix}_{rid[:12]}{TEST_EMAIL_DOMAIN}", "+9725" + digits


def delete_registered_user(email: str) -> str:
    """'deleted' / 'absent' when done; 'skipped: <why>' when the PII database is not configured as isolated (nothing
    is written). Raises if the configuration claims isolation but the server looks like production."""
    if not email.endswith(TEST_EMAIL_DOMAIN):
        raise ValueError(f"refusing to delete a non-test address: {email!r}")
    url = os.environ.get("DATABASE_PII_URL")
    why = g.url_refusal(url)
    if why:
        return f"skipped: {why}"
    import psycopg2
    u = urlparse(url.replace("postgresql+asyncpg://", "postgresql://"))
    conn = psycopg2.connect(host=u.hostname, port=u.port or 5432, dbname=u.path.lstrip("/"),
                            user=u.username, password=u.password, connect_timeout=10)
    try:
        with conn.cursor() as cur:
            why = g.live_refusal_dbapi(cur)
            if why:
                raise AssertionError(f"REFUSED before any write: {why}")
            cur.execute("DELETE FROM users WHERE email = %s", (email,))
            n = cur.rowcount
        conn.commit()
        return "deleted" if n else "absent"
    finally:
        conn.close()
