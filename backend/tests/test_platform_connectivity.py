import os
import uuid
from urllib.parse import urlparse

import httpx
import psycopg2
import pytest


DB_HOST = os.getenv("DB_HOST", "postgres_catalog")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_NAME = os.getenv("DB_NAME", "autospare")
DB_USER = os.getenv("DB_USER", "autospare")
DB_PASSWORD = os.getenv("DB_PASSWORD", "autospare")
DATABASE_URL = os.getenv("DATABASE_URL", "")

MEILI_URL = os.getenv("MEILI_URL", "http://meilisearch:7700")
MEILI_KEY = os.getenv("MEILI_MASTER_KEY", "")
SITE_BASE = os.getenv("SITE_BASE", "http://localhost:8000")
SITE_BASE_CANDIDATES = [
    SITE_BASE,
    "http://backend:8000",
    "http://127.0.0.1:8000",
    "http://localhost:8000",
    "http://nginx",
]


def _db_conn():
    if DATABASE_URL:
        parsed = urlparse(DATABASE_URL)
        return psycopg2.connect(
            host=parsed.hostname or DB_HOST,
            port=parsed.port or DB_PORT,
            dbname=(parsed.path or "/").lstrip("/") or DB_NAME,
            user=parsed.username or DB_USER,
            password=parsed.password or DB_PASSWORD,
        )
    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
    )


def test_scraper_api_calls_schema_and_write_path():
    expected_cols = {
        "id",
        "source",
        "query",
        "part_number",
        "url",
        "part_id",
        "http_status",
        "success",
        "results_count",
        "response_ms",
        "error_message",
        "called_at",
        "created_at",
    }

    source = "pytest_schema_fix"
    marker = f"XPENG-PYTEST-{uuid.uuid4().hex[:8]}"

    with _db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema='public' AND table_name='scraper_api_calls'
                """
            )
            cols = {r[0] for r in cur.fetchall()}
            missing = expected_cols - cols
            assert not missing, f"Missing scraper_api_calls columns: {sorted(missing)}"

            cur.execute(
                """
                INSERT INTO scraper_api_calls
                    (id, source, url, query, part_number, http_status,
                     response_ms, part_id, success, error_message, called_at, created_at)
                VALUES
                    (%s, %s, %s, %s, %s, %s,
                     %s, %s, %s, %s, NOW(), NOW())
                """,
                (
                    str(uuid.uuid4()),
                    source,
                    f"https://scraper/pytest/{marker}",
                    f"https://scraper/pytest/{marker}",
                    marker,
                    200,
                    42,
                    None,
                    True,
                    None,
                ),
            )
            cur.execute(
                """
                SELECT source, url, query, part_number, http_status, response_ms
                FROM scraper_api_calls
                WHERE source = %s
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (source,),
            )
            row = cur.fetchone()
            assert row is not None
            assert row[0] == source
            assert marker in (row[1] or "")
            assert marker in (row[2] or "")
            assert row[3] == marker
            assert row[4] == 200
            assert row[5] == 42


def test_pgvector_available_and_queryable():
    with _db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT extname FROM pg_extension WHERE extname='vector'")
            assert cur.fetchone() is not None, "pgvector extension is not installed"

            cur.execute(
                """
                SELECT table_name, column_name
                FROM information_schema.columns
                WHERE table_schema='public' AND udt_name='vector'
                ORDER BY table_name, column_name
                """
            )
            vector_cols = cur.fetchall()
            assert vector_cols, "No vector columns found in public schema"

            # Ensure pgvector operator path is executable on real table data.
            cur.execute(
                """
                SELECT COUNT(*)
                FROM parts_catalog
                WHERE embedding IS NOT NULL
                """
            )
            non_null_embeddings = cur.fetchone()[0]
            if non_null_embeddings == 0:
                pytest.skip("No non-null embeddings in parts_catalog yet")

            cur.execute(
                """
                SELECT id
                FROM parts_catalog
                WHERE embedding IS NOT NULL
                ORDER BY embedding <=> embedding
                LIMIT 1
                """
            )
            assert cur.fetchone() is not None


def test_meilisearch_and_site_search_flow_for_xpeng():
    headers = {"Authorization": f"Bearer {MEILI_KEY}"} if MEILI_KEY else {}

    with httpx.Client(timeout=60) as c:
        idx_resp = c.get(f"{MEILI_URL}/indexes", headers=headers)
        idx_resp.raise_for_status()
        idx_results = idx_resp.json().get("results", [])
        uids = {x.get("uid") for x in idx_results}
        assert "parts" in uids, "Meilisearch 'parts' index is missing"

        meili_search = c.post(
            f"{MEILI_URL}/indexes/parts/search",
            json={"q": "xpeng", "limit": 5},
            headers=headers,
        )
        meili_search.raise_for_status()
        meili_hits = meili_search.json().get("hits", [])
        assert len(meili_hits) > 0, "No Xpeng hits in Meilisearch parts index"

        last_err = None
        payload = None
        saw_redirect = False
        for base in SITE_BASE_CANDIDATES:
            try:
                site_resp = c.get(
                    f"{base}/api/v1/parts/search",
                    params={"q": "xpeng", "limit": 10},
                    follow_redirects=False,
                )
                if site_resp.status_code in (301, 302, 307, 308):
                    saw_redirect = True
                    continue
                site_resp.raise_for_status()
                payload = site_resp.json()
                break
            except Exception as exc:
                last_err = exc

        if payload is None and saw_redirect:
            pytest.skip("Site endpoint is externally redirected from container network")
        if payload is None:
            raise AssertionError(f"Site search endpoint unreachable from test container: {last_err}")

        assert "all_parts" in payload, "Site search payload missing all_parts"
        assert isinstance(payload.get("all_parts"), list)
        assert len(payload.get("all_parts")) > 0, "Site search returned no Xpeng parts"
