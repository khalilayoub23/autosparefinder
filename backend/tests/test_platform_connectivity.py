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


def _db_target():
    """The exact connection parameters _db_conn uses (DATABASE_URL, else the DB_* variables)."""
    if DATABASE_URL:
        parsed = urlparse(DATABASE_URL)
        return dict(host=parsed.hostname or DB_HOST, port=parsed.port or DB_PORT,
                    dbname=(parsed.path or "/").lstrip("/") or DB_NAME,
                    user=parsed.username or DB_USER, password=parsed.password or DB_PASSWORD)
    return dict(host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD)


def _db_conn():
    return psycopg2.connect(**_db_target())


def _db_conn_for_writes():
    """#101: a connection a test may WRITE through. The static checks run on the host the test will really use
    (DATABASE_URL or the DB_HOST fallback, whose default is the production container name) and skip before
    connecting; the live check runs on the open connection and FAILS before any write. Callers roll back."""
    import isolated_db_guard as g
    why = g.target_refusal(_db_target()["host"])
    if why:
        pytest.skip(f"isolated DB only: {why}")
    conn = _db_conn()
    with conn.cursor() as cur:
        why = g.live_refusal_dbapi(cur)
    if why:
        conn.close()
        pytest.fail(f"REFUSED before any write: {why}")
    return conn


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

    conn = _db_conn_for_writes()        # #101: refuses a production target before any write
    try:
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
    finally:
        conn.rollback()                   # the write path is checked inside this transaction; nothing persists
        conn.close()


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


# ── #98: Meilisearch + site search for Xpeng — deterministic, with no possible supplier traffic ──────────────────────
# Before: this asserted that the CONNECTED platform already held Xpeng parts and then called a RUNNING server's site
# search over HTTP — so it was permanently red on any isolated database, and on a server with the default
# ENABLE_EXTERNAL_SUPPLIERS=1 every site search also fired the background lookup at real suppliers (the sandbox's own
# logs: 771 requests to autodoc / partsouq / amayama during test runs, 2026-10-21). Now the test supplies its own Xpeng
# part (catalog row + search document, removed afterwards), runs the REAL /api/v1/parts/search route in-process, and
# an outbound-network guard refuses — and records — any name lookup that is not a local/sandbox service.
# The guard now lives in tests/network_guard.py and conftest installs it for EVERY test (#100); these two tests also
# install their own instance to assert on exactly what they triggered.
from network_guard import NetworkGuard, is_local  # noqa: E402


def _require_isolated():
    import sys as _sys
    _sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import isolated_db_guard
    refusal = isolated_db_guard.static_refusal()
    if refusal is not None:
        pytest.skip(f"isolated DB only: {refusal}")


def test_network_guard_refuses_real_supplier_hosts(monkeypatch):
    """The guard itself: a request to a real supplier host never leaves the process."""
    guard = NetworkGuard().install(monkeypatch)
    with pytest.raises(httpx.ConnectError):
        httpx.get("https://partsouq.com/en/search?q=xpeng", timeout=5)
    import socket
    with pytest.raises(socket.gaierror):
        socket.create_connection(("www.autodoc.eu", 443), timeout=5)
    assert guard.blocked == ["partsouq.com", "www.autodoc.eu"]
    assert is_local("sandbox_meili") and is_local("172.18.0.4") and is_local("meilisearch")


def test_meilisearch_and_site_search_flow_for_xpeng(monkeypatch):
    import asyncio
    _require_isolated()
    guard = NetworkGuard().install(monkeypatch)
    monkeypatch.setenv("ENABLE_EXTERNAL_SUPPLIERS", "0")      # this test is about the local search path
    headers = {"Authorization": f"Bearer {MEILI_KEY}"} if MEILI_KEY else {}
    tag = uuid.uuid4().hex[:8]
    pid, sku = str(uuid.uuid4()), f"XPENG-T-{tag}"

    async def main():
        from fastapi import FastAPI
        from sqlalchemy import text
        import routes.parts as parts_routes
        from BACKEND_DATABASE_MODELS import async_session_factory, engine
        host = str(engine.url.host or "")
        assert host.startswith("sandbox_") or host.startswith("h78disp"), f"REFUSED: catalog host {host!r}"
        # asyncio.run() gives this test its own event loop, but the app's engines are module-global: pooled
        # connections opened by an EARLIER test belong to that test's (now closed) loop and fail here with
        # "attached to a different loop" (seen only in the full suite). Forget them without touching them
        # (close=False never awaits on the dead loop); fresh connections are then opened in this loop.
        import BACKEND_DATABASE_MODELS as _m
        await _m.engine.dispose(close=False)
        await _m.pii_engine.dispose(close=False)
        async with async_session_factory() as db:
            import isolated_db_guard
            why = await isolated_db_guard.live_refusal(db)          # #101: live check before the first write
            if why:
                pytest.fail(f"REFUSED before any write: {why}")
            brand = (await db.execute(text("INSERT INTO car_brands (id, name) VALUES (gen_random_uuid(), :n) RETURNING id"),
                                      {"n": f"zz-xpeng-test-{tag}"})).scalar_one()
            await db.execute(text(
                "INSERT INTO parts_catalog (id, sku, name, manufacturer_id, manufacturer, oem_number, part_type, "
                "category, is_active) VALUES (CAST(:i AS uuid), :s, :n, :m, 'XPENG', :o, 'oem', 'brakes', TRUE)"),
                {"i": pid, "s": sku, "n": f"XPENG G9 front brake pad set {tag}", "m": brand, "o": f"XP{tag.upper()}01"})
            await db.commit()
        try:
            async with httpx.AsyncClient(timeout=30) as c:
                if "parts" not in {x.get("uid") for x in (await c.get(f"{MEILI_URL}/indexes", headers=headers)).json().get("results", [])}:
                    t = (await c.post(f"{MEILI_URL}/indexes", json={"uid": "parts", "primaryKey": "id"}, headers=headers)).json()
                    await _meili_wait(c, headers, t)
                t = (await c.post(f"{MEILI_URL}/indexes/parts/documents", headers=headers, json=[{
                    "id": pid, "name": f"XPENG G9 front brake pad set {tag}", "sku": sku, "manufacturer": "XPENG",
                    "oem_number": f"XP{tag.upper()}01", "category": "brakes"}])).json()
                await _meili_wait(c, headers, t)
                uids = {x.get("uid") for x in (await c.get(f"{MEILI_URL}/indexes", headers=headers)).json().get("results", [])}
                assert "parts" in uids, "Meilisearch 'parts' index is missing"
                hits = (await c.post(f"{MEILI_URL}/indexes/parts/search", json={"q": "xpeng", "limit": 50},
                                     headers=headers)).json().get("hits", [])
                assert any(h.get("id") == pid for h in hits), "No Xpeng hits in Meilisearch parts index"

            app = FastAPI()
            app.include_router(parts_routes.router)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as site:
                r = await site.get("/api/v1/parts/search", params={"q": f"xpeng {tag}", "limit": 10})
            assert r.status_code == 200, r.text[:300]
            payload = r.json()
            assert "all_parts" in payload, "Site search payload missing all_parts"
            assert isinstance(payload.get("all_parts"), list)
            assert len(payload.get("all_parts")) > 0, "Site search returned no Xpeng parts"
            assert any(str((p.get("part") or p).get("id")) == pid for p in payload["all_parts"]), payload["all_parts"][:2]
        finally:
            async with httpx.AsyncClient(timeout=30) as c:
                t = (await c.delete(f"{MEILI_URL}/indexes/parts/documents/{pid}", headers=headers)).json()
                await _meili_wait(c, headers, t)
            async with async_session_factory() as db:
                await db.execute(text("DELETE FROM parts_catalog WHERE id = CAST(:i AS uuid)"), {"i": pid})
                await db.execute(text("DELETE FROM car_brands WHERE name = :n"), {"n": f"zz-xpeng-test-{tag}"})
                await db.commit()
            import BACKEND_DATABASE_MODELS as _m
            await _m.engine.dispose()
            await _m.pii_engine.dispose()
            import BACKEND_AUTH_SECURITY as _sec
            if _sec._redis_client is not None:
                await _sec._redis_client.aclose()
                _sec._redis_client = None
    asyncio.run(main())
    assert guard.blocked == [], f"the search path tried to reach external hosts: {guard.blocked}"


async def _meili_wait(c, headers, task, timeout_s=30):
    import asyncio
    uid = task.get("taskUid", task.get("uid"))
    for _ in range(int(timeout_s * 10)):
        st = (await c.get(f"{MEILI_URL}/tasks/{uid}", headers=headers)).json().get("status")
        if st in ("succeeded", "failed", "canceled"):
            assert st == "succeeded", task
            return
        await asyncio.sleep(0.1)
    raise AssertionError(f"meili task {uid} did not finish")
