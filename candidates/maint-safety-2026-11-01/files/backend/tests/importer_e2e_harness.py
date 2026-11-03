"""Shared harness for the importer END-TO-END tests (sandbox only).

An E2E test here runs the importer's REAL entry point - normally the script itself as a subprocess
(`python3 /app/<rel> <fixture>`), which parses the fixture, maps it, computes the price, writes through its OWN
database connection and commits. The test then reads the persisted rows over an independent connection, runs the
importer a second time, and asserts nothing owned was overwritten. Rows are tagged and removed afterwards.

Fixtures are SYNTHETIC (built from the parser contract in the importer's own code) unless a test says otherwise; a
fixture-based E2E is never presented as a live-source verification."""
import asyncio
import os
import subprocess
import sys
import uuid

import asyncpg
import pytest

import isolated_db_guard


def dsn() -> str:
    url = os.environ["DATABASE_URL"]
    refusal = isolated_db_guard.url_refusal(url)
    if refusal is not None:
        pytest.skip(f"isolated DB only: {refusal}")
    return url.replace("postgresql+asyncpg://", "postgresql://")


def new_tag() -> str:
    return uuid.uuid4().hex[:8].upper()


def run_code(code: str, *, env_extra=None, timeout=240) -> subprocess.CompletedProcess:
    """Run an importer ENTRY FUNCTION in a fresh interpreter (for modules that are not runnable as scripts)."""
    env = {**os.environ, "PYTHONPATH": "/app", **(env_extra or {})}
    return subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=timeout, cwd="/app")


async def align_sandbox_schema(conn):
    """The sandbox catalogue drifted from production (`prod catalog not reproducible from migrations`): production has
    part_vehicle_fitment.id DEFAULT gen_random_uuid() (read-only checked 2026-11-02), the sandbox copy has none, so importers that omit
    the id (kia_new_models) fail ONLY there. Align the ISOLATED database (dsn() refuses anything else) - never production."""
    await conn.execute("ALTER TABLE part_vehicle_fitment ALTER COLUMN id SET DEFAULT gen_random_uuid()")


CREATED_BRAND_IDS: list = []      # car_brands rows THIS test run created (a pre-existing row is never recorded, so never deleted)


async def release_created_brands(conn):
    """Delete the brands the test created, once nothing references them: a leaked brand (e.g. 'Jaguar' under another id) breaks the
    FK-bound inserts of OTHER tests that need the production brand id."""
    while CREATED_BRAND_IDS:
        bid = CREATED_BRAND_IDS.pop()
        try:
            # an importer-created supplier row ("<Brand> Official Importer Israel", ...) points at the brand: it is the test's own leftover
            sids = [r["id"] for r in await conn.fetch("SELECT id FROM suppliers WHERE manufacturer_id = $1::uuid", bid)]
            if sids:
                await conn.execute("DELETE FROM supplier_parts WHERE supplier_id = ANY($1::uuid[])", sids)
                await conn.execute("DELETE FROM suppliers WHERE id = ANY($1::uuid[])", sids)
            await conn.execute("DELETE FROM car_brands WHERE id = $1::uuid", bid)
        except asyncpg.PostgresError:
            pass                                  # still referenced: leave it rather than cascade


async def ensure_brand(conn, name: str):
    """(id, created). A pre-existing brand row is reused and NEVER deleted; a row created here is released at test teardown."""
    row = await conn.fetchrow("SELECT id FROM car_brands WHERE LOWER(name) = LOWER($1)", name)
    if row:
        return str(row["id"]), False
    bid = str(await conn.fetchval("INSERT INTO car_brands (id, name) VALUES (gen_random_uuid(), $1) RETURNING id", name))
    CREATED_BRAND_IDS.append(bid)
    return bid, True


async def ensure_brand_id(conn, bid: str, name_hint: str):
    """The importer hard-codes the production car_brands UUID in its FK-bound inserts (fitment rows): make that id exist in the
    sandbox. Returns (id, created). Uses a tagged name when the natural name is taken by another id; created rows are
    removed by the caller."""
    if await conn.fetchval("SELECT 1 FROM car_brands WHERE id = $1::uuid", bid):
        return bid, False
    name = name_hint if not await conn.fetchval("SELECT 1 FROM car_brands WHERE LOWER(name)=LOWER($1)", name_hint) else f"{name_hint}-e2e-{bid[:8]}"
    await conn.execute("INSERT INTO car_brands (id, name) VALUES ($1::uuid, $2)", bid, name)
    CREATED_BRAND_IDS.append(bid)
    return bid, True


async def seed_part(conn, sku, manufacturer, mid, *, oem=None, base=None, importer=None, mx=None, part_type="oem"):
    await conn.execute("""INSERT INTO parts_catalog (id, sku, oem_number, name, manufacturer, manufacturer_id, part_type,
        is_active, base_price, importer_price_ils, max_price_ils) VALUES (gen_random_uuid(), $1, $2, 'e2e fixture', $3,
        $4::uuid, $5, TRUE, $6, $7, $8)""", sku, oem or sku, manufacturer, mid, part_type, base, importer, mx)


async def seed_offer(conn, sku, *, ils, country="IL", url=True, klass=None):
    sid = await conn.fetchval("INSERT INTO suppliers (id, name, country, is_active) VALUES (gen_random_uuid(), $1, $2, TRUE) RETURNING id",
                              f"e2e-sup-{uuid.uuid4().hex[:8]}", country)
    await conn.execute("""INSERT INTO supplier_parts (id, supplier_id, part_id, supplier_sku, price_ils, price_usd, is_available,
        availability, part_type, supplier_url, updated_at, created_at) SELECT gen_random_uuid(), $1, id, $2, $3, $4, TRUE,
        'in_stock', $5, $6, NOW(), NOW() FROM parts_catalog WHERE sku = $7""",
                       sid, f"e2e-{uuid.uuid4().hex[:10]}", ils, round(ils / 3.0, 2), klass, "https://fixture.invalid/e2e" if url else None, sku)
    return sid


async def get(conn, where_sql: str, *params):
    return await conn.fetch(f"SELECT sku, oem_number, base_price, importer_price_ils, max_price_ils, manufacturer FROM parts_catalog WHERE {where_sql}", *params)


async def base_by(conn, col: str, value: str):
    v = await conn.fetchval(f"SELECT base_price FROM parts_catalog WHERE {col} = $1 AND is_active", value)
    return None if v is None else float(v)


async def cleanup(conn, *like_patterns):
    """Delete every part whose sku or oem_number matches one of the LIKE patterns, and what hangs off it."""
    for pat in like_patterns:
        ids = [r["id"] for r in await conn.fetch("SELECT id FROM parts_catalog WHERE sku LIKE $1 OR oem_number LIKE $1", pat)]
        if not ids:
            continue
        await conn.execute("DELETE FROM supplier_parts WHERE part_id = ANY($1::uuid[])", ids)
        for t in ("part_vehicle_fitment", "parts_images", "part_thumbnails"):
            try:
                await conn.execute(f"DELETE FROM {t} WHERE part_id = ANY($1::uuid[])", ids)
            except asyncpg.PostgresError:
                pass
        await conn.execute("DELETE FROM parts_catalog WHERE id = ANY($1::uuid[])", ids)


_SHIM = r'''
import json, os, runpy, socket, sys, urllib.request
# Several importers hard-code DSN host `postgres_catalog` (the PRODUCTION host name). In the isolated test container that name
# does not resolve; map it - inside THIS subprocess only - to the sandbox database host taken from the (isolation-checked)
# DATABASE_URL. Nothing is ever pointed at a production host.
_SANDBOX_HOST = os.environ["E2E_SANDBOX_DB_HOST"]
_real_gai = socket.getaddrinfo
def _gai(host, *a, **k):
    if host in ("postgres_catalog", "postgres_pii"):
        host = _SANDBOX_HOST
    return _real_gai(host, *a, **k)
socket.getaddrinfo = _gai

FIX = json.loads(os.environ.get("E2E_HTTP_FIXTURE") or "null")
if FIX is not None:
    class _Resp:
        def __init__(self, body):
            self._b = (body if isinstance(body, str) else json.dumps(body)).encode("utf-8")
            self.status = 200
            self.headers = {}
        def read(self, *a): return self._b
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def getcode(self): return 200
    def _fake_urlopen(req, data=None, timeout=None, *a, **k):
        url = req if isinstance(req, str) else req.full_url
        for r in FIX.get("routes", []):
            if r["match"] in url:
                return _Resp(r["body"])
        return _Resp(FIX.get("default", {"data": []}))
    urllib.request.urlopen = _fake_urlopen
    for _d in FIX.get('mkdirs', []):
        os.makedirs(_d, exist_ok=True)           # a hard-coded output directory that exists on the host but not in the throwaway container
    if FIX.get('no_sleep'):
        import time as _t
        _t.sleep = lambda *a, **k: None          # importers that rate-limit with sleep() between thousands of searches
        import asyncio as _aio
        _real_asleep = _aio.sleep
        async def _fast_asleep(delay, result=None):
            return await _real_asleep(0, result)
        _aio.sleep = _fast_asleep

    def _pick(url):
        for r in FIX.get("routes", []):
            if r["match"] in url:
                return r["body"]
        return FIX.get("default", {"data": []})

    def _bytes(body):
        return (body if isinstance(body, str) else json.dumps(body)).encode("utf-8")
    try:                                              # httpx (sync + async clients)
        import httpx
        def _hx(request):
            body = _pick(str(request.url))
            return httpx.Response(200, content=_bytes(body), request=request,
                                  headers={"content-type": "text/html; charset=utf-8" if isinstance(body, str) else "application/json"})
        httpx.Client.send = lambda self, request, **k: _hx(request)
        async def _asend(self, request, **k):
            return _hx(request)
        httpx.AsyncClient.send = _asend
    except ImportError:
        pass
    try:                                              # requests
        import requests
        from requests.adapters import HTTPAdapter
        def _rq(self, request, **k):
            body = _pick(request.url)
            r = requests.Response()
            r.status_code = 200
            r._content = _bytes(body)
            r.url = request.url
            r.request = request
            r.encoding = "utf-8"
            r.headers["Content-Type"] = "text/html; charset=utf-8" if isinstance(body, str) else "application/json"
            return r
        HTTPAdapter.send = _rq
    except ImportError:
        pass

script = sys.argv[1]
sys.argv = sys.argv[1:]
runpy.run_path(script, run_name="__main__")
'''


def _sandbox_host() -> str:
    from urllib.parse import urlparse
    return urlparse(dsn()).hostname


def run_script(rel: str, *args, env_extra=None, timeout=240) -> subprocess.CompletedProcess:
    """Run an importer script exactly as production does (python3 /app/<rel> ...) against the SANDBOX database (the DB host
    name some importers hard-code is mapped to the sandbox host inside the subprocess)."""
    env = {**os.environ, "PYTHONPATH": "/app", "E2E_SANDBOX_DB_HOST": _sandbox_host(), **(env_extra or {})}
    return subprocess.run([sys.executable, "-c", _SHIM, f"/app/{rel}", *map(str, args)], env=env, capture_output=True,
                          text=True, timeout=timeout, cwd="/app")


def run_script_with_http(rel: str, fixture: dict, *args, env_extra=None, timeout=240) -> subprocess.CompletedProcess:
    """Same as run_script, but urllib's TRANSPORT is replaced by a saved/synthetic API response (`fixture`). Everything after
    the HTTP read - the importer's own parsing, mapping, pricing and SQL - is real. This is FIXTURE E2E; it never touches a
    live source and must not be reported as live-source verification."""
    import json as _json
    return run_script(rel, *args, env_extra={"E2E_HTTP_FIXTURE": _json.dumps(fixture), **(env_extra or {})}, timeout=timeout)


# ── PDF fixtures ───────────────────────────────────────────────────────────────────────────────────────────────────
import re as _re

_HEB_RUN = _re.compile(r"[֐-׿]+(?:[ ]+[֐-׿]+)*")
_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


def hebrew_for_pdf(line: str) -> str:
    """PyMuPDF stores inserted RTL text visually: a Hebrew run comes back REVERSED from page.get_text(). The importers'
    regexes expect the logical order (`זמין`), so each Hebrew run is character-reversed before insertion; the importer's own
    text extraction then yields the logical string. (Latin / digit runs are unaffected.)"""
    return _HEB_RUN.sub(lambda m: m.group(0)[::-1], line)


def make_pdf(path, lines, *, per_page=40):
    """Write a real PDF whose extracted text lines are `lines`. SYNTHETIC fixture built from the importer's parser contract."""
    import fitz
    if not os.path.exists(_FONT):
        pytest.skip("DejaVuSans.ttf (fonts-dejavu-core) is needed to build a Hebrew PDF fixture")
    doc = fitz.open()
    for i in range(0, len(lines), per_page):
        page = doc.new_page()
        y = 60
        for ln in lines[i:i + per_page]:
            page.insert_text((40, y), hebrew_for_pdf(ln), fontsize=10, fontname="dv", fontfile=_FONT)
            y += 16
    os.makedirs(os.path.dirname(str(path)), exist_ok=True)
    doc.save(str(path))
    doc.close()
    return str(path)


def make_table_pdf(path, rows, *, col_w=90, row_h=24):
    """A real PDF with a ruled table (pdfplumber.extract_tables() finds it): `rows` is a list of cell-string lists."""
    import fitz
    if not os.path.exists(_FONT):
        pytest.skip("DejaVuSans.ttf (fonts-dejavu-core) is needed to build a table PDF fixture")
    doc = fitz.open()
    page = doc.new_page()
    y = 40
    for r in rows:
        for c, txt in enumerate(r):
            x = 30 + c * col_w
            rect = fitz.Rect(x, y, x + col_w, y + row_h)
            page.draw_rect(rect, color=(0, 0, 0), width=0.8)
            page.insert_text((x + 3, y + 15), str(txt), fontsize=8, fontname="dv", fontfile=_FONT)
        y += row_h
    os.makedirs(os.path.dirname(str(path)), exist_ok=True)
    doc.save(str(path))
    doc.close()
    return str(path)
