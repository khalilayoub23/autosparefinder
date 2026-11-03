"""Importer / harvester end-to-end tests for the base_price writers (2026-11-01).

E2E here = the module's OWN entry function reads a representative input fixture (JSON file, embedded part table,
record batch), maps it, calculates the price and writes through its own SQL on the ISOLATED database; the test then
reads the persisted rows, repeats the run, and repeats it again against a part whose base_price is already owned by
fix_base_prices / normalize_base_price. Every case asserts: NEW part -> the policy's net; EXISTING owned value -> never
overwritten; second run -> no change. All inside a rolled-back asyncpg transaction (pg()).

Writers that cannot be driven this way (PDF / XLSX / live-HTTP / browser inputs without a fixture generator) are NOT
claimed here; they are listed BLOCKED_WITH_EVIDENCE in docs/BASE_PRICE_WRITERS.json and covered only by the generic
"every writer statement prepares on the real schema" test below.
"""
import ast
import builtins
import json
import os
import re
import uuid

import pytest

import isolated_db_guard  # noqa: F401
import price_policy as pp
from base_price_inventory import _sql_assigns_base_price, py_files
from test_base_price_writers import pg, BACKEND  # noqa: F401
import maint_guard_support as support

OWNED = 33.0


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    support.neutral_probes(monkeypatch)


def redirect_paths(monkeypatch, mapping):
    """The importers read fixed /app/*.json paths: serve fixtures there without touching /app."""
    real_open, real_exists = builtins.open, os.path.exists

    def fake_exists(p, *a, **k):
        return True if str(p) in mapping else real_exists(p, *a, **k)

    def fake_open(p, *a, **k):
        return real_open(mapping.get(str(p), p), *a, **k)
    monkeypatch.setattr(builtins, "open", fake_open)
    monkeypatch.setattr(os.path, "exists", fake_exists)


def write_json(tmp_path, name, data):
    p = tmp_path / name
    p.write_text(json.dumps(data, ensure_ascii=False))
    return str(p)


async def brand(conn, name, bid=None):
    if bid:
        await conn.execute("INSERT INTO car_brands (id, name) VALUES ($1::uuid, $2) ON CONFLICT DO NOTHING", bid, name)
        return bid
    return str(await conn.fetchval("INSERT INTO car_brands (id, name) VALUES (gen_random_uuid(), $1) "
                                   "ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name RETURNING id", name))


async def mkpart(conn, sku, manufacturer, mid, *, oem=None, base=None):
    await conn.execute("""INSERT INTO parts_catalog (id, sku, oem_number, name, manufacturer, manufacturer_id, part_type,
        is_active, base_price) VALUES (gen_random_uuid(), $1, $2, 'fixture', $3, $4::uuid, 'oem', TRUE, $5)""",
                       sku, oem or sku, manufacturer, mid, base)


async def base(conn, sku):
    v = await conn.fetchval("SELECT base_price FROM parts_catalog WHERE sku = $1", sku)
    return None if v is None else float(v)


# ── delek_json_price_update: UPDATE by OEM from a JSON price list ────────────────────────────────────────────
async def test_delek_json_update_end_to_end(tmp_path):
    from importers import delek_json_price_update as m
    async with pg() as conn:
        mid = await brand(conn, f"zz-fordx-{uuid.uuid4().hex[:6]}")
        t = uuid.uuid4().hex[:8]
        await mkpart(conn, f"DJ-{t}-new", "ZZFord", mid, oem=f"OEM{t}A")
        await mkpart(conn, f"DJ-{t}-own", "ZZFord", mid, oem=f"OEM{t}B", base=OWNED)
        path = write_json(tmp_path, "ford.json", [
            {"oem_number": f"OEM{t}A", "price_ils": 100.0, "price_ils_vat": 118.0},
            {"oem_number": f"OEM{t}B", "price_ils": 200.0, "price_ils_vat": 236.0}])
        for _ in range(2):                                                    # second run = idempotent
            n = await m.update_from_json(conn, path, ["ZZFord"], "Delek test", "price_ils", "price_ils_vat")
            assert n == 2
            assert await base(conn, f"DJ-{t}-new") == pp.base_price_for_cost(100.0) == 145.0   # old code: 118.0 (gross)
            assert await base(conn, f"DJ-{t}-own") == OWNED                                    # old code: 236.0
        assert float(await conn.fetchval("SELECT max_price_ils FROM parts_catalog WHERE sku=$1", f"DJ-{t}-new")) == 118.0


# ── ev_vag_lexus_import: NIO update, Voyah / M-Hero insert, VAG + Lexus update ───────────────────────────────
async def test_ev_vag_lexus_end_to_end(monkeypatch):
    from importers import ev_vag_lexus_import as m
    async with pg() as conn:
        t = uuid.uuid4().hex[:8]
        nio = await brand(conn, f"zz-nio-{t}")
        # NIO: update path (manufacturer='NIO')
        await mkpart(conn, f"NIO-{t}-n", "NIO", nio, oem=f"N{t}1")
        await mkpart(conn, f"NIO-{t}-o", "NIO", nio, oem=f"N{t}2", base=OWNED)
        monkeypatch.setattr(m, "load_json", lambda p: [
            {"oem_number": f"N{t}1", "price_ils": 100.0, "price_ils_vat": 118.0},
            {"oem_number": f"N{t}2", "price_ils": 50.0, "price_ils_vat": 59.0}])
        for _ in range(2):
            await m.update_nio(conn)
            assert await base(conn, f"NIO-{t}-n") == 145.0 and await base(conn, f"NIO-{t}-o") == OWNED
        # Voyah: insert path with ON CONFLICT (sku)
        await brand(conn, "Voyah", m.MFR_IDS["Voyah"])
        monkeypatch.setattr(m, "load_json", lambda p: [{"oem_number": f"V{t}1", "price_ils": 80.0, "price_ils_vat": 94.4,
                                                       "name": "Voyah pad", "name_he": "רפידה", "model": "Free 2022"}])
        await m.import_voyah(conn)
        assert await base(conn, f"VOYAH-V{t}1") == pp.base_price_for_cost(80.0) == 116.0              # old: round(80*1.45)
        await conn.execute("UPDATE parts_catalog SET base_price=$1 WHERE sku=$2", OWNED, f"VOYAH-V{t}1")
        await m.import_voyah(conn)                                                                      # re-import
        assert await base(conn, f"VOYAH-V{t}1") == OWNED                                                # old code overwrote
        # M-Hero insert
        await brand(conn, "M-Hero", m.MFR_IDS["M-Hero"])
        monkeypatch.setattr(m, "load_json", lambda p: [{"oem_number": f"H{t}1", "price_ils": 40.0, "price_ils_vat": 47.2,
                                                       "name": "Hero", "name_he": "הירו", "model": "M-Hero 2021"}])
        await m.import_mhero(conn)
        assert await base(conn, f"MHERO-H{t}1") == 58.0


# ── geely_israel_import: embedded part table, ex-VAT importer prices ────────────────────────────────────────
async def test_geely_israel_end_to_end(monkeypatch):
    from importers import geely_israel_import as m
    async with pg() as conn:
        await brand(conn, "Geely", m.GEELY_BRAND_ID)
        t = uuid.uuid4().hex[:6].upper()
        monkeypatch.setattr(m, "PARTS", [(f"GE{t}1", "Brake pad", "רפידות", 100.0, "Coolray", "brakes")])
        r1 = await m.import_parts(conn)
        sku = f"GEELY-GE{t}1"
        assert r1["inserted"] == 1 and await base(conn, sku) == 145.0                          # old code: 118.0 (price*1.18)
        await conn.execute("UPDATE parts_catalog SET base_price=$1 WHERE sku=$2", OWNED, sku)
        await m.import_parts(conn)
        assert await base(conn, sku) == OWNED                                                   # old code overwrote


# ── sng_barratt_jaguar_import: foreign (UK) supplier, GBP cost ──────────────────────────────────────────────
async def test_sng_barratt_end_to_end():
    from importers import sng_barratt_jaguar_import as m
    async with pg() as conn:
        await brand(conn, "Jaguar", m.JAGUAR_BRAND_ID)
        t = uuid.uuid4().hex[:8]
        sup = await conn.fetchval("INSERT INTO suppliers (id,name,country,is_active) VALUES (gen_random_uuid(),$1,'UK',TRUE) RETURNING id", f"SNG {t}")
        rec = {"part_number": f"JX{t}", "title": "Front brake pad set", "description": "d", "type_name": "Genuine",
               "price_gbp": 20.0, "applications": [], "stock_status": "in stock", "web_product_guid": "g"}
        await m.import_batch(conn, sup, [rec], True)
        sku = m.sku(rec["part_number"])
        cost = round(20.0 * m.GBP_TO_ILS, 2)
        assert await base(conn, sku) == pp.base_price_for_cost(cost)                           # foreign: net only, no IL VAT
        assert await base(conn, sku) != m.base_price_ils(20.0)                                 # old code stored cost*1.18 here
        await conn.execute("UPDATE parts_catalog SET base_price=$1 WHERE sku=$2", OWNED, sku)
        await m.import_batch(conn, sup, [{**rec, "price_gbp": 99.0}], True)
        assert await base(conn, sku) == OWNED


# ── lr_subaru_byd_import: UPDATE by OEM from fixed-path JSON ─────────────────────────────────────────────────
async def test_subaru_update_end_to_end(monkeypatch, tmp_path):
    from importers import lr_subaru_byd_import as m
    async with pg() as conn:
        mid = await brand(conn, "Subaru")
        t = uuid.uuid4().hex[:8]
        await mkpart(conn, f"SU-{t}-n", "Subaru", mid, oem=f"S{t}1")
        await mkpart(conn, f"SU-{t}-o", "Subaru", mid, oem=f"S{t}2", base=OWNED)
        redirect_paths(monkeypatch, {"/app/subaru_il_parts.json": write_json(tmp_path, "s.json", [
            {"Material": f"S{t}1", "PriceNoVat": 100.0, "PriceWithVat": 118.0},
            {"Material": f"S{t}2", "PriceNoVat": 10.0, "PriceWithVat": 11.8}])})
        for _ in range(2):
            await m.import_subaru(conn)
            assert await base(conn, f"SU-{t}-n") == 145.0 and await base(conn, f"SU-{t}-o") == OWNED


# ── generic: every base_price writer statement is valid on the real schema ───────────────────────────────────
def _writer_statements():
    out = []
    for p in py_files(BACKEND):
        rel = os.path.relpath(p, BACKEND)
        try:
            tree = ast.parse(open(p, errors="ignore").read())
        except SyntaxError:
            continue
        if rel == os.path.join("maintenance", "base_price_snapshot.py"):
            continue                                               # temp-table statements: covered by test_base_price_snapshot.py
        for n in ast.walk(tree):
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and _sql_assigns_base_price(n.value):
                if re.search(r"\$\d+", n.value):                 # asyncpg statements (positional parameters)
                    out.append((rel, n.lineno, n.value))
    return out


@pytest.mark.parametrize("rel,line,sql", _writer_statements(), ids=lambda v: v if isinstance(v, str) and len(v) < 60 else "")
async def test_every_asyncpg_writer_statement_prepares_on_the_real_schema(rel, line, sql):
    """Catches placeholder / type errors (AmbiguousParameterError, a $N with no argument slot, bad SQL) in EVERY
    asyncpg base_price writer - statement-level only, NOT an end-to-end claim."""
    async with pg() as conn:
        await conn.prepare(sql)


# ── scripts/fix_oem_price_gaps.sh: the embedded python run for real against the isolated DB ───────────────────
async def test_fix_oem_price_gaps_copies_cost_but_never_a_stale_base_price():
    import asyncpg
    import subprocess
    import sys
    url = os.environ["DATABASE_URL"]
    assert isolated_db_guard.url_refusal(url) is None, "isolated DB only"
    sh = open(os.path.join(BACKEND, "scripts", "fix_oem_price_gaps.sh")).read()
    code = sh.split('python3 -c "', 1)[1].rsplit('"', 1)[0].replace('\\$', '$').replace('\\"', '"')
    t = uuid.uuid4().hex[:8]
    mfr = f"zzmfr{t}"
    dsn = url.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(dsn)
    try:
        mid = await brand(conn, f"zz-oemgap-{t}")
        # priced twin (dashed OEM, stale base 999) and three unpriced twins (no dash)
        await conn.execute("""INSERT INTO parts_catalog (id, sku, oem_number, name, manufacturer, manufacturer_id, part_type,
            is_active, importer_price_ils, max_price_ils, base_price) VALUES (gen_random_uuid(), $1, $2, 'p', $3, $4::uuid,
            'oem', TRUE, 100, 118, 999)""", f"G-{t}-src", f"{t[:3]}-{t[3:]}1234", mfr, mid)
        for tag, b in (("empty", None), ("zero", 0), ("owned", OWNED)):
            await conn.execute("""INSERT INTO parts_catalog (id, sku, oem_number, name, manufacturer, manufacturer_id, part_type,
                is_active, importer_price_ils, base_price) VALUES (gen_random_uuid(), $1, $2, 'p', $3, $4::uuid, 'oem', TRUE, 0, $5)""",
                               f"G-{t}-{tag}", f"{t[:3]}{t[3:]}1234" + ("" if tag == "empty" else ""), mfr, mid, b)
        # distinct twins need distinct oem numbers within the same normalized key -> only one unpriced twin can share it
        await conn.execute("UPDATE parts_catalog SET oem_number = $1 WHERE sku = $2", f"{t[:3]} {t[3:]}1234", f"G-{t}-zero")
        await conn.execute("UPDATE parts_catalog SET oem_number = $1 WHERE sku = $2", f"{t[:3]}.{t[3:]}1234", f"G-{t}-owned")
        env = {**os.environ, "PYTHONPATH": "/app"}
        for _ in range(2):                                                     # run twice: idempotent
            subprocess.run([sys.executable, "-c", code.replace("${BRAND}", mfr)], check=True, env=env, capture_output=True)
            rows = {r["sku"]: r for r in await conn.fetch(
                "SELECT sku, importer_price_ils, max_price_ils, base_price FROM parts_catalog WHERE manufacturer=$1", mfr)}
            assert float(rows[f"G-{t}-src"]["base_price"]) == 999.0                 # the source row is untouched
            for tag in ("empty", "zero", "owned"):
                assert float(rows[f"G-{t}-{tag}"]["importer_price_ils"]) == 100.0   # cost copied
            assert float(rows[f"G-{t}-empty"]["base_price"]) == pp.base_price_for_cost(100.0) == 145.0   # old: 999 (twin's)
            assert float(rows[f"G-{t}-zero"]["base_price"]) == 145.0
            assert float(rows[f"G-{t}-owned"]["base_price"]) == OWNED                # old code overwrote with 999
    finally:
        await conn.execute("DELETE FROM parts_catalog WHERE manufacturer = $1", mfr)
        await conn.execute("DELETE FROM car_brands WHERE name = $1", f"zz-oemgap-{t}")
        await conn.close()


# ── amayama_price_import / rockauto_price_import: the relay-triggered price-fill importers (real main(path)) ───
@pytest.mark.parametrize("modname,brand_key", [("amayama_price_import", "amayama"), ("rockauto_price_import", "rockauto")])
async def test_price_fill_importers_end_to_end(tmp_path, modname, brand_key):
    import asyncpg
    import importlib
    m = importlib.import_module(f"importers.{modname}")
    url = os.environ["DATABASE_URL"]
    assert isolated_db_guard.url_refusal(url) is None, "isolated DB only"
    dsn = url.replace("postgresql+asyncpg://", "postgresql://")
    t = uuid.uuid4().hex[:8].upper()
    conn = await asyncpg.connect(dsn)
    try:
        mid = await brand(conn, f"zz-{brand_key}-{t}")
        await mkpart(conn, f"PF-{t}-new", "ZZPF", mid, oem=f"PF{t}A1")                       # unpriced -> filled
        await mkpart(conn, f"PF-{t}-own", "ZZPF", mid, oem=f"PF{t}B2", base=OWNED)           # owned -> untouched
        rate = await m._usd_to_ils(conn)
        path = write_json(tmp_path, "in.json", [
            {"oem": f"PF-{t}-A1", "price_usd": 10.0, "brand": "X", "name": "pad"},
            {"oem": f"PF{t}B2", "price_usd": 20.0, "brand": "X", "name": "pad2"}])
        for _ in range(2):                                                                    # repeated import: no change
            await m.main(path)
            assert await base(conn, f"PF-{t}-new") == pp.base_price_for_cost(round(10.0 * rate, 2))   # old: round(cost*1.45)
            assert await base(conn, f"PF-{t}-own") == OWNED
    finally:
        await conn.execute("DELETE FROM supplier_parts WHERE part_id IN (SELECT id FROM parts_catalog WHERE sku LIKE $1)", f"PF-{t}-%")
        await conn.execute("DELETE FROM parts_catalog WHERE sku LIKE $1", f"PF-{t}-%")
        await conn.execute("DELETE FROM car_brands WHERE name = $1", f"zz-{brand_key}-{t}")
        await conn.close()


# ── oempartsonline_importer.import_products (spawned by catalog_scraper) ───────────────────────────────────
async def test_oempartsonline_import_products_end_to_end():
    import oempartsonline_importer as m
    async with pg() as conn:
        await brand(conn, "Audi", m.SUPPLIER_MAP["audi"]["manufacturer_id"])
        sid = await m.ensure_supplier(conn, "audi")
        t = uuid.uuid4().hex[:8].upper()
        products = [{"sku": f"8K{t}01", "name": "Brake pad set", "msrp": 12.0, "sale_price": 10.0, "category_path": "Brakes"}]
        rate = 3.5
        r1 = await m.import_products(conn, products, "audi", sid, rate)
        sku = m.build_sku("Audi", f"8K{t}01")
        assert r1["inserted"] == 1 and await base(conn, sku) == pp.base_price_for_cost(round(10.0 * rate, 2)) == 50.75
        await conn.execute("UPDATE parts_catalog SET base_price=$1 WHERE sku=$2", OWNED, sku)
        await m.import_products(conn, products, "audi", sid, 9.9)               # re-import at another rate
        assert await base(conn, sku) == OWNED
