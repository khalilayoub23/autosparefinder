"""Runtime verification of the REAL importers / price writers against the production index states (Agent 4, 2026-11-06).

Runs ONLY against a DISPOSABLE Postgres loaded with the production schema (pg_dump -s) plus a real data subset. Each scenario
executes the real code path (CLI subprocess for the harvest/price importers, in-process call of the real main()/sync function
for the others) and asserts the database outcome including price_history. Index states exercised:
  A = old uq_supplier_parts_part_supplier + both new partial indexes  (production now)
  B = old index dropped, both new indexes                              (stage 3)
Set AG4_RT_DSN (asyncpg DSN of the disposable DB) and AG4_RT_SUBENV_URL (the SQLAlchemy/asyncpg URL subprocesses use, whose host
alias resolves to the same disposable DB). Refuses to run when the DB looks like production (>= 100k parts) or the flag is absent.
Not covered (stated, not claimed): PDF text parsing (colmobil extract_parts is replaced by fixture rows), samelet HTTP enumeration
(replaced by fixture rows), eBay HTTP client (mocked)."""
import asyncio
import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parent.parent
DSN = os.getenv("AG4_RT_DSN", "")
SUB_URL = os.getenv("AG4_RT_SUBENV_URL", "")
SQL_FILE = Path(os.getenv("AG4_MIGRATION_SQL") or ROOT.parent / "candidates" / "offer-policy-2026-11-06" / "migration_variant_identity.sql")
pytestmark = pytest.mark.skipif(not (DSN and SUB_URL and os.getenv("AG4_DISPOSABLE") == "1"),
                                reason="needs AG4_RT_DSN + AG4_RT_SUBENV_URL + AG4_DISPOSABLE=1 (disposable DB only)")

OLD = "uq_supplier_parts_part_supplier"
NEW = ("uq_sp_part_supplier_variant", "uq_sp_part_supplier_untyped")
STATES = ["A_old_plus_new", "B_new_only"]
SAAB_BRAND = "c21a289d-5b3e-411f-afbb-5f7a4d022a88"


# ------------------------------------------------------------------ plumbing
def _create_stmts():
    sql = re.sub(r"(?m)^\s*--.*$", "", SQL_FILE.read_text())
    out = [s.strip() for s in re.split(r";\s*(?:\n|$)", sql) if s.strip().upper().startswith("CREATE UNIQUE INDEX CONCURRENTLY")]
    assert len(out) == 2
    return out


async def _conn():
    import asyncpg
    return await asyncpg.connect(DSN)


def run(coro):
    return asyncio.run(coro)


async def _set_state(state):
    c = await _conn()
    try:
        n = await c.fetchval("SELECT count(*) FROM parts_catalog")
        assert n < 100_000, f"refusing: {n} parts looks like production"
        for i in (OLD, *NEW):
            await c.execute(f"DROP INDEX IF EXISTS {i}")
        if state.startswith("A"):
            # fixture-created second variants (state-B scenarios) cannot exist under the old index: drop the extra rows
            await c.execute("DELETE FROM supplier_parts WHERE id IN (SELECT id FROM (SELECT id, row_number() OVER "
                            "(PARTITION BY part_id, supplier_id ORDER BY created_at, id) rn FROM supplier_parts) x WHERE rn > 1)")
            await c.execute(f"CREATE UNIQUE INDEX {OLD} ON supplier_parts (part_id, supplier_id)")
        for s in _create_stmts():
            await c.execute(s)
        bad = await c.fetchval("SELECT count(*) FROM pg_index WHERE NOT indisvalid OR NOT indisready")
        assert bad == 0
    finally:
        await c.close()


async def Q(sql, *a):
    c = await _conn()
    try:
        return await c.fetch(sql, *a)
    finally:
        await c.close()


async def X(sql, *a):
    c = await _conn()
    try:
        return await c.execute(sql, *a)
    finally:
        await c.close()


async def V(sql, *a):
    c = await _conn()
    try:
        return await c.fetchval(sql, *a)
    finally:
        await c.close()


def tag():
    return uuid.uuid4().hex[:8].upper()


async def supplier(name, country="IL"):
    sid = await V("SELECT id FROM suppliers WHERE name=$1", name)
    if sid:
        return sid
    return await V("INSERT INTO suppliers(id,name,country,is_active) VALUES(gen_random_uuid(),$1,$2,true) RETURNING id", name, country)


async def part(sku, oem=None, manufacturer="Saab", is_active=True, base_price=None):
    return await V("INSERT INTO parts_catalog(id,sku,name,manufacturer,manufacturer_id,oem_number,is_active,base_price) "
                   "VALUES(gen_random_uuid(),$1,$2,$3,$4::uuid,$5,$6,$7) RETURNING id", sku, f"fixture {sku}", manufacturer, SAAB_BRAND, oem, is_active, base_price)


async def offer(supplier_id, part_id, sku, price, part_type=None, available=True):
    return await V("INSERT INTO supplier_parts(id,supplier_id,part_id,supplier_sku,price_usd,price_ils,part_type,is_available,availability) "
                   "VALUES(gen_random_uuid(),$1,$2,$3,0,$4,$5,$6,'in_stock') RETURNING id", supplier_id, part_id, sku, price, part_type, available)


async def hist(offer_id):
    return await Q("SELECT old_price_ils, new_price_ils, source FROM price_history WHERE supplier_part_id=$1 ORDER BY created_at", offer_id)


def sub_env(**extra):
    e = {**os.environ, "DATABASE_URL": SUB_URL, "EUR_ILS_RATE": "3.48", "USD_TO_ILS": "3.00", "PYTHONDONTWRITEBYTECODE": "1"}
    e.update(extra)
    return e


def cli(args, **env):
    r = subprocess.run([sys.executable, *args], cwd="/app", env=sub_env(**env), capture_output=True, text=True, timeout=300)
    return r


# ------------------------------------------------------------------ real HTML -> parse_parts -> importer JSON
def _harvester():
    import importlib.util
    spec = importlib.util.spec_from_file_location("cpie_h", ROOT / "harvesters" / "car_parts_ie_flaresolverr_harvester.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def block(name, sku, price_eur, img=True):
    price_html = f'<div class="bottom_block"><span class="price">€ {price_eur:.2f}</span> <a>Buy</a></div>' if price_eur is not None \
        else '<div class="bottom_block"><a>Check availability</a></div>'
    image = f'<img class="lazy" src="/img/spacer.gif" data-src="//cdn.car-parts.ie/p/{sku}.jpg">' if img else ""
    return (f'<div class="rec_products_single_block"><a href="/p/{sku}">{image}</a>'
            f'<div class="title">{name}</div><div class="artikle">Article №: {sku}</div>{price_html}</div>')


def harvest_file(tmp_path, blocks, vehicle="saab/9-3-ys3f", name="batch.json"):
    h = _harvester()
    parts = h.parse_parts("<html><body>" + "".join(blocks) + "</body></html>", f"/car-parts/{vehicle}/brake-pads")
    assert len(parts) == len(blocks), (len(parts), len(blocks))
    p = tmp_path / name
    p.write_text(json.dumps({"source": "car-parts.ie", "vehicle": vehicle, "vehicle_slug": vehicle, "parts": parts}))
    return p, parts


def cpie_import(path, vehicle="saab/9-3-ys3f"):
    r = cli(["/app/importers/car_parts_ie_import_generic.py", "--brand", "saab", "--file", str(path), "--vehicle-slug", vehicle])
    assert r.returncode == 0, r.stdout[-800:] + r.stderr[-800:]
    return json.loads(r.stdout[r.stdout.index("{"):]), r


async def cpie_supplier():
    return await supplier("Car-Parts.ie", "IE")


# ================================================================== 1. car-parts.ie harvest import, real parse_parts + CLI
@pytest.mark.parametrize("state", STATES)
def test_cpie_first_repeat_pricechange_priceless(state, tmp_path):
    run(_set_state(state))
    t = tag()
    skus = [f"AG4{t}{i}" for i in range(3)]
    f1, _ = harvest_file(tmp_path, [block(f"Brake pad set {s}", s, 10.00 + i) for i, s in enumerate(skus)])
    r1, _ = cpie_import(f1)
    assert r1["parts_inserted"] == 3 and r1["supplier_rows"] == 3 and r1["fitment_rows"] == 3 and r1["image_rows"] == 3
    sid = run(cpie_supplier())
    rows = run(Q("SELECT id, supplier_sku, price_ils, part_type, part_id FROM supplier_parts WHERE supplier_id=$1 AND supplier_sku = ANY($2)", sid, skus))
    assert len(rows) == 3 and all(r["part_type"] is None for r in rows)
    ids = {r["supplier_sku"]: r["id"] for r in rows}
    assert float(next(r for r in rows if r["supplier_sku"] == skus[0])["price_ils"]) == round(10.00 * 3.48, 2)
    assert all(run(hist(i)) == [] for i in ids.values()), "first insert must write no history"
    # repeat: idempotent, same ids, no history, no duplicate images/fitment
    nimg = run(V("SELECT count(*) FROM parts_images WHERE part_id IN (SELECT part_id FROM supplier_parts WHERE id = ANY($1))", list(ids.values())))
    r2, _ = cpie_import(f1)
    assert r2["parts_inserted"] == 0 and r2["parts_updated"] == 3
    rows2 = run(Q("SELECT id, supplier_sku FROM supplier_parts WHERE supplier_id=$1 AND supplier_sku = ANY($2)", sid, skus))
    assert {r["supplier_sku"]: r["id"] for r in rows2} == ids
    assert all(run(hist(i)) == [] for i in ids.values())
    assert run(V("SELECT count(*) FROM parts_images WHERE part_id IN (SELECT part_id FROM supplier_parts WHERE id = ANY($1))", list(ids.values()))) == nimg
    # price change on one SKU: exactly one history row on that offer only
    f3, _ = harvest_file(tmp_path, [block(f"Brake pad set {skus[0]}", skus[0], 12.50)] + [block(f"Brake pad set {s}", s, 10.00 + i) for i, s in enumerate(skus) if i > 0], name="b3.json")
    cpie_import(f3)
    h0 = run(hist(ids[skus[0]]))
    assert len(h0) == 1 and float(h0[0]["old_price_ils"]) == 34.8 and float(h0[0]["new_price_ils"]) == round(12.5 * 3.48, 2) and h0[0]["source"] == "car_parts_ie_import"
    assert float(run(V("SELECT price_ils FROM supplier_parts WHERE id=$1", ids[skus[0]]))) == round(12.5 * 3.48, 2)
    assert all(run(hist(ids[s])) == [] for s in skus[1:]), "unchanged offers get no history"
    # price-less re-harvest keeps price, writes no history
    f4, parts4 = harvest_file(tmp_path, [block(f"Brake pad set {skus[0]}", skus[0], None)], name="b4.json")
    assert parts4[0]["price_eur"] is None
    before = len(run(hist(ids[skus[0]])))
    cpie_import(f4)
    assert float(run(V("SELECT price_ils FROM supplier_parts WHERE id=$1", ids[skus[0]]))) == round(12.5 * 3.48, 2)
    assert len(run(hist(ids[skus[0]]))) == before


@pytest.mark.parametrize("state", STATES)
def test_cpie_reharvest_real_offers_history_matches_changes(state, tmp_path):
    """Real Saab Car-Parts.ie offers from the production subset, rendered back to car-parts.ie HTML blocks."""
    run(_set_state(state))
    sid = run(cpie_supplier())
    real = run(Q("SELECT sp.id, sp.supplier_sku, sp.price_ils, pc.name FROM supplier_parts sp JOIN parts_catalog pc ON pc.id=sp.part_id "
                 "WHERE sp.supplier_id=$1 AND sp.price_ils > 0 AND sp.supplier_sku !~ '\\s' AND pc.sku = 'SAAB-' || sp.supplier_sku "
                 "AND pc.name !~ '[<>&]' ORDER BY sp.id LIMIT 40", sid))
    assert len(real) >= 20, len(real)
    snap = {r["id"]: float(r["price_ils"]) for r in real}
    hist_before = {i: len(run(hist(i))) for i in snap}
    f, _ = harvest_file(tmp_path, [block(r["name"], r["supplier_sku"], round(float(r["price_ils"]) / 3.48, 2)) for r in real])
    cpie_import(f)
    after = {r["id"]: float(r["price_ils"]) for r in run(Q("SELECT id, price_ils FROM supplier_parts WHERE id = ANY($1)", list(snap)))}
    changed = {i for i in snap if round(after[i], 2) != round(snap[i], 2)}
    for i in snap:
        added = len(run(hist(i))) - hist_before[i]
        assert added == (1 if i in changed else 0), (i, snap[i], after[i], added)
    # second run: nothing changes any more -> no new rows at all
    total = run(V("SELECT count(*) FROM price_history WHERE supplier_part_id = ANY($1)", list(snap)))
    cpie_import(f)
    assert run(V("SELECT count(*) FROM price_history WHERE supplier_part_id = ANY($1)", list(snap))) == total
    assert run(V("SELECT count(*) FROM supplier_parts WHERE supplier_id=$1 AND supplier_sku = ANY($2)", sid, [r["supplier_sku"] for r in real])) == len(real)


def test_cpie_typed_variant_coexistence_and_sku_identity_state_B(tmp_path):
    run(_set_state("B_new_only"))
    sid = run(cpie_supplier())
    t = tag()
    k = f"AG4V{t}"
    f, _ = harvest_file(tmp_path, [block(f"Pad {k}", k, 10.0)])
    cpie_import(f)
    p = run(V("SELECT part_id FROM supplier_parts WHERE supplier_id=$1 AND supplier_sku=$2", sid, k))
    # a second variant of the same supplier+part (typed) coexists once the old index is gone
    typed = run(offer(sid, p, f"{k}-AM", 77.0, part_type="aftermarket"))
    untyped = run(V("SELECT id FROM supplier_parts WHERE supplier_id=$1 AND supplier_sku=$2", sid, k))
    # re-harvest with a changed price: only the untyped row of that SKU changes, with one history row; the typed variant is untouched
    f2, _ = harvest_file(tmp_path, [block(f"Pad {k}", k, 11.0)], name="v2.json")
    cpie_import(f2)
    assert len(run(hist(untyped))) == 1 and run(hist(typed)) == []
    assert float(run(V("SELECT price_ils FROM supplier_parts WHERE id=$1", typed))) == 77.0
    assert run(V("SELECT part_type FROM supplier_parts WHERE id=$1", typed)) == "aftermarket"
    # SKU identity: the harvest of SKU '<k>-AM' addresses the typed row's offer identity (supplier+sku): its PRICE is updated
    # (one history row), part_id and part_type are NOT changed, and no duplicate offer is created.
    f3, _ = harvest_file(tmp_path, [block(f"Pad {k}-AM", f"{k}-AM", 20.0)], name="v3.json")
    cpie_import(f3)
    row = run(Q("SELECT part_id, part_type, price_ils FROM supplier_parts WHERE id=$1", typed))[0]
    assert row["part_id"] == p and row["part_type"] == "aftermarket" and float(row["price_ils"]) == round(20.0 * 3.48, 2)
    assert len(run(hist(typed))) == 1
    assert run(V("SELECT count(*) FROM supplier_parts WHERE supplier_id=$1 AND supplier_sku=$2", sid, f"{k}-AM")) == 1


@pytest.mark.parametrize("state", STATES)
def test_cpie_variant_clash_is_counted_logged_and_harmless(state, tmp_path):
    """Legacy untyped offer under another SKU for the same supplier+part: the harvest insert violates the untyped (and old)
    unique index. The importer skips the offer (data behaviour unchanged): stored row untouched, no duplicate, import exit 0.
    F2 (2026-11-06): the skip is now COUNTED (`supplier_skipped`) and logged with the SKU and reason (first 5 per run)."""
    run(_set_state(state))
    sid = run(cpie_supplier())
    t = tag()
    k = f"AG4C{t}"
    pid = run(part(f"SAAB-{k}", oem=k))
    legacy = run(offer(sid, pid, f"OLD-{k}", 55.0))
    f, _ = harvest_file(tmp_path, [block(f"Pad {k}", k, 10.0)])
    r, proc = cpie_import(f)
    assert r["parts_updated"] == 1 and r["supplier_rows"] == 0 and r["supplier_skipped"] == 1, r
    rows = run(Q("SELECT id, supplier_sku, price_ils FROM supplier_parts WHERE part_id=$1", pid))
    assert len(rows) == 1 and rows[0]["id"] == legacy and float(rows[0]["price_ils"]) == 55.0
    assert run(hist(legacy)) == []
    assert f"supplier_parts skip {k}" in proc.stdout and "uniqueviolationerror" in proc.stdout.lower()


# ================================================================== 2. RockAuto / Amayama price imports (typed writers)
def _price_import(script, rows):
    p = Path("/tmp") / f"ag4_{uuid.uuid4().hex[:6]}.json"
    p.write_text(json.dumps(rows))
    return cli([f"/app/importers/{script}", str(p)])


@pytest.mark.parametrize("state", STATES)
def test_rockauto_first_repeat_change(state):
    run(_set_state(state))
    t = tag()
    oem = f"RA{t}99"
    pid = run(part(f"SAAB-RA{t}", oem=oem))
    sid = run(supplier("RockAuto", "US"))
    rows = [{"oem": oem, "brand": "BOSCH", "partnum": "B1", "name": "pad", "price_usd": 20.0}]
    assert _price_import("rockauto_price_import.py", rows).returncode == 0
    o = run(Q("SELECT id, part_type, price_ils, supplier_sku FROM supplier_parts WHERE part_id=$1 AND supplier_id=$2", pid, sid))
    assert len(o) == 1 and o[0]["part_type"] == "aftermarket" and o[0]["supplier_sku"] == f"RA-{oem}" and float(o[0]["price_ils"]) == 60.0
    oid = o[0]["id"]
    assert run(hist(oid)) == []
    assert _price_import("rockauto_price_import.py", rows).returncode == 0
    assert run(hist(oid)) == [] and run(V("SELECT count(*) FROM supplier_parts WHERE part_id=$1 AND supplier_id=$2", pid, sid)) == 1
    rows[0]["price_usd"] = 25.0
    assert _price_import("rockauto_price_import.py", rows).returncode == 0
    h = run(hist(oid))
    assert len(h) == 1 and float(h[0]["old_price_ils"]) == 60.0 and float(h[0]["new_price_ils"]) == 75.0 and h[0]["source"] == "rockauto_price_import"


@pytest.mark.parametrize("state", STATES)
def test_amayama_first_repeat_change_and_synonym_variant_clash(state):
    run(_set_state(state))
    t = tag()
    oem = f"AM{t}77"
    pid = run(part(f"SAAB-AM{t}", oem=oem))
    sid = run(supplier("Amayama", "JP"))
    rows = [{"oem": oem, "brand": "SAAB", "part_num": "P1", "name": "pad", "price_usd": 30.0}]
    assert _price_import("amayama_price_import.py", rows).returncode == 0
    o = run(Q("SELECT id, part_type, price_ils FROM supplier_parts WHERE part_id=$1 AND supplier_id=$2", pid, sid))
    assert len(o) == 1 and o[0]["part_type"] == "oem"
    oid = o[0]["id"]
    assert _price_import("amayama_price_import.py", rows).returncode == 0 and run(hist(oid)) == []
    rows[0]["price_usd"] = 33.0
    assert _price_import("amayama_price_import.py", rows).returncode == 0
    h = run(hist(oid))
    assert len(h) == 1 and float(h[0]["new_price_ils"]) == 99.0 and h[0]["source"] == "amayama_price_import"
    # second part: a legacy row of the SAME supplier+part typed with the synonym 'Original' under another SKU
    oem2 = f"AM{t}78"
    pid2 = run(part(f"SAAB-AM{t}B", oem=oem2))
    legacy = run(offer(sid, pid2, f"LEG-{oem2}", 10.0, part_type="Original"))
    r = _price_import("amayama_price_import.py", [{"oem": oem2, "brand": "SAAB", "part_num": "P2", "name": "x", "price_usd": 5.0}])
    assert r.returncode != 0 and "UniqueViolation" in (r.stdout + r.stderr) and "uq_" in (r.stdout + r.stderr), (state, r.stderr[-400:])
    # LOUD in both states: A -> old index, B -> normalised variant index (synonym 'Original' == 'oem'); stored row untouched
    assert run(V("SELECT count(*) FROM supplier_parts WHERE part_id=$1", pid2)) == 1
    assert float(run(V("SELECT price_ils FROM supplier_parts WHERE id=$1", legacy))) == 10.0


def test_rockauto_second_variant_coexists_only_when_old_index_dropped():
    t = tag()
    sid = run(supplier("RockAuto", "US"))
    out = {}
    for state in STATES:
        run(_set_state(state))
        oem = f"RB{t}{state[0]}"
        pid = run(part(f"SAAB-RB{t}{state[0]}", oem=oem))
        run(offer(sid, pid, f"RA-LEGACY-{oem}", 5.0, part_type="oem"))     # a different variant of the same supplier+part
        r = _price_import("rockauto_price_import.py", [{"oem": oem, "brand": "B", "partnum": "x", "name": "n", "price_usd": 9.0}])
        out[state] = (r.returncode, run(V("SELECT count(*) FROM supplier_parts WHERE part_id=$1 AND supplier_id=$2", pid, sid)), r.stderr[-300:])
    assert out["A_old_plus_new"][0] != 0 and out["A_old_plus_new"][1] == 1 and "uq_supplier_parts_part_supplier" in out["A_old_plus_new"][2]
    assert out["B_new_only"][0] == 0 and out["B_new_only"][1] == 2, out["B_new_only"]


# ================================================================== 3. merged catalog workbook (psycopg2 CLI)
@pytest.mark.parametrize("state", STATES)
def test_import_merged_catalog_first_repeat_change(state, tmp_path):
    from openpyxl import Workbook
    run(_set_state(state))
    t = tag()
    sku1, sku2 = f"MC{t}1", f"MC{t}2"

    def wb(price1):
        w = Workbook(); ws = w.active; ws.title = "Saab"
        ws.append(["מספר קטלוגי", "תיאור החלק", "מותג", "דגם רכב", "מחיר לצרכן", "סוג מוצר", "זמינות מלאי"])
        ws.append([sku1, "pad one", "Saab", "9-3 2005", price1, "מקורי", "זמין"])
        ws.append([sku2, "pad two", "Saab", "9-5 2006", 200.0, "aftermarket", "זמין"])
        p = tmp_path / f"m{price1}.xlsx"; w.save(p); return p
    def imp(p):
        r = cli(["/app/scripts/import_merged_catalog.py", "--file", str(p)])
        assert r.returncode == 0, r.stdout[-500:] + r.stderr[-500:]
        return r.stdout
    o1 = imp(wb(100.0))
    sid = run(supplier("Official Manufacturer Sites", "IL"))
    rows = run(Q("SELECT id, supplier_sku, price_ils FROM supplier_parts WHERE supplier_id=$1 AND supplier_sku = ANY($2)", sid, [sku1, sku2]))
    ids = {r["supplier_sku"]: r["id"] for r in rows}
    assert len(ids) == 2 and "errors=0" in o1
    imp(wb(100.0))
    assert run(V("SELECT count(*) FROM supplier_parts WHERE supplier_id=$1 AND supplier_sku = ANY($2)", sid, [sku1, sku2])) == 2
    assert run(hist(ids[sku1])) == [] and run(hist(ids[sku2])) == []
    imp(wb(130.0))
    h = run(hist(ids[sku1]))
    assert len(h) == 1 and float(h[0]["old_price_ils"]) == 100.0 and float(h[0]["new_price_ils"]) == 130.0 and h[0]["source"] == "import_merged_catalog"
    assert run(hist(ids[sku2])) == []
    # the script must not have re-created the old index (stage-3 safety)
    if state.startswith("B"):
        assert run(V("SELECT count(*) FROM pg_indexes WHERE indexname=$1", OLD)) == 0


# ================================================================== 4. IL importers: colmobil main (real), samelet import_brand (real)
@pytest.mark.parametrize("state", STATES)
def test_colmobil_main_first_repeat_change(state, tmp_path):
    run(_set_state(state))
    os.environ["DATABASE_URL"] = SUB_URL
    sys.path.insert(0, str(ROOT / "importers"))
    import importlib
    if "colmobil_import_v2" in sys.modules:
        del sys.modules["colmobil_import_v2"]
    cm = importlib.import_module("colmobil_import_v2")
    t = tag()
    oems = [f"CM{t}1", f"CM{t}2"]
    state_dir = tmp_path / "pdfs"; state_dir.mkdir()
    (state_dir / "MIT.PDF").write_bytes(b"%PDF-fixture")
    prices = {"v": 118.0}

    def fake_extract(path, brand):
        return [{"oem": oems[0], "price": prices["v"], "desc": "pad a"}, {"oem": oems[1], "price": 59.0, "desc": "pad b"}]

    def go():
        with mock.patch.object(cm, "PDF_DIR", state_dir), mock.patch.object(cm, "LOG_PATH", tmp_path / "cm.log"), \
             mock.patch.object(cm, "extract_parts", fake_extract), mock.patch.object(cm, "DB_URL", SUB_URL.replace("postgresql+asyncpg://", "postgresql://")), \
             mock.patch.object(sys, "argv", ["colmobil", "--brands", "mitsubishi"]):
            asyncio.run(cm.main())
    go()
    sid = run(V("SELECT id FROM suppliers WHERE name='Colmobil - Mitsubishi Israel'"))
    rows = run(Q("SELECT id, supplier_sku, price_ils, part_type FROM supplier_parts WHERE supplier_id=$1 AND supplier_sku = ANY($2)", sid, oems))
    ids = {r["supplier_sku"]: r["id"] for r in rows}
    assert len(ids) == 2
    go()
    assert run(V("SELECT count(*) FROM supplier_parts WHERE supplier_id=$1 AND supplier_sku = ANY($2)", sid, oems)) == 2
    assert run(hist(ids[oems[0]])) == []
    prices["v"] = 150.0
    go()
    h = run(hist(ids[oems[0]]))
    assert len(h) == 1 and float(h[0]["old_price_ils"]) == 118.0 and float(h[0]["new_price_ils"]) == 150.0 and h[0]["source"] == "colmobil_import_v2"
    assert run(hist(ids[oems[1]])) == []


@pytest.mark.parametrize("state", STATES)
def test_samelet_import_brand_first_repeat_change(state):
    run(_set_state(state))
    os.environ["DATABASE_URL"] = SUB_URL
    sys.path.insert(0, str(ROOT / "importers"))
    import importlib
    if "samelet_import_v2" in sys.modules:
        del sys.modules["samelet_import_v2"]
    sm = importlib.import_module("samelet_import_v2")
    t = tag()
    brand = f"Agtest{t}"
    mids = {f"00{t}1": {"MatDescEn": "Brake pad", "MatDescHe": "רפידה", "PriceWithVat": "118.00", "MaterialType": "01"},
            f"00{t}2": {"MatDescEn": "Oil filter", "MatDescHe": "מסנן", "PriceWithVat": "59.00", "MaterialType": "02"}}
    # The importer's own parts_catalog upsert fails at HEAD (query has 15 params, fallback passes 14 - pre-existing defect, reported);
    # its supplier_parts step works on the brand's EXISTING active parts, which is the path exercised here.
    run(X("INSERT INTO car_brands(id,name,is_active) VALUES(gen_random_uuid(),$1,true)", brand))
    bid = run(V("SELECT id FROM car_brands WHERE name=$1", brand))
    pids = []
    for i, cost in ((1, 100.0), (2, 50.0)):
        pids.append(run(V("INSERT INTO parts_catalog(id,sku,name,manufacturer,manufacturer_id,oem_number,is_active,importer_price_ils) "
                          "VALUES(gen_random_uuid(),$1,'n',$2,$3,$4,true,$5) RETURNING id", f"AGT-{t}{i}", brand, bid, f"00{t}{i}", cost)))

    async def go(price1):
        import asyncpg
        mids[f"00{t}1"]["PriceWithVat"] = price1
        conn = await asyncpg.connect(DSN)
        try:
            with mock.patch.object(sm, "get_token", lambda s: "tok"), mock.patch.object(sm, "enumerate_all_parts", lambda s, k: dict(mids)):
                await sm.import_brand(conn, "agtest", brand, "AGT")
        finally:
            await conn.close()
    asyncio.run(go("118.00"))
    sid = run(V("SELECT id FROM suppliers WHERE name=$1", f"{brand} Official Importer Israel"))
    assert sid
    rows = run(Q("SELECT id, supplier_sku, price_ils FROM supplier_parts WHERE supplier_id=$1", sid))
    assert len(rows) == 2, rows
    ids = [r["id"] for r in rows]
    asyncio.run(go("118.00"))
    assert run(V("SELECT count(*) FROM supplier_parts WHERE supplier_id=$1", sid)) == 2
    assert all(run(hist(i)) == [] for i in ids)
    run(X("UPDATE parts_catalog SET importer_price_ils=200.0 WHERE id=$1", pids[0]))
    asyncio.run(go("236.00"))
    changed = [i for i in ids if run(hist(i))]
    assert len(changed) == 1
    h = run(hist(changed[0]))
    assert len(h) == 1 and h[0]["source"] == "samelet_import_v2" and float(h[0]["old_price_ils"]) == 100.0 and float(h[0]["new_price_ils"]) == 200.0


@pytest.mark.parametrize("state", STATES)
def test_samelet_import_brand_creates_catalog_parts_then_idempotent_and_one_history_row(state, capsys):
    """D3 regression: import_brand used to write NO parts_catalog row (25*15 flattened args into a 15-parameter VALUES, and a
    per-row fallback with 14). Real import_brand against fixtures, no pre-seeded parts."""
    run(_set_state(state))
    os.environ["DATABASE_URL"] = SUB_URL
    sys.path.insert(0, str(ROOT / "importers"))
    import importlib
    if "samelet_import_v2" in sys.modules:
        del sys.modules["samelet_import_v2"]
    sm = importlib.import_module("samelet_import_v2")
    t = tag()
    brand = f"Agsm{t}"
    m1, m2, m3 = f"00{t}1", f"00{t}2", f"00{t}3"
    mids = {m1: {"MatDescEn": "Brake pad", "MatDescHe": "רפידה", "PriceWithVat": "118.00", "MaterialType": "01"},
            m2: {"MatDescEn": "Oil filter", "MatDescHe": "מסנן", "PriceWithVat": "59.00", "MaterialType": "02"},
            m3: {"MatDescEn": "", "MatDescHe": "", "PriceWithVat": "11.80", "MaterialType": "01"}}

    async def go(price1):
        import asyncpg
        mids[m1]["PriceWithVat"] = price1
        conn = await asyncpg.connect(DSN)
        try:
            with mock.patch.object(sm, "get_token", lambda s: "tok"), mock.patch.object(sm, "enumerate_all_parts", lambda s, k: dict(mids)):
                return await sm.import_brand(conn, "agsm", brand, "AGS")
        finally:
            await conn.close()

    assert asyncio.run(go("118.00")) == 3
    out1 = capsys.readouterr().out
    assert "parts new=3, updated=0" in out1 and "supplier_parts upserted=3 (new=3, updated=0)" in out1 and "search_errors=0" in out1, out1[-600:]
    assert "rex_todo=created" in out1
    assert run(V("SELECT count(*) FROM agent_todos WHERE assigned_to_agent='rex' AND title=$1", f"Fetch fitment for {brand} samelet parts (3 parts)")) == 1
    parts = run(Q("SELECT id, sku, part_type, aftermarket_tier, needs_oem_lookup, part_condition, is_active, importer_price_ils, "
                  "max_price_ils, min_price_ils, oem_number FROM parts_catalog WHERE manufacturer=$1 ORDER BY sku", brand))
    assert [r["sku"] for r in parts] == sorted(f"AGS-{m.lstrip('0')}" for m in (m1, m2, m3))
    by = {r["oem_number"]: r for r in parts}
    assert by[m1]["part_type"] == "oem" and float(by[m1]["importer_price_ils"]) == 100.0 and float(by[m1]["max_price_ils"]) == 118.0
    assert float(by[m1]["min_price_ils"]) == 118.0 and by[m1]["part_condition"] == "new" and by[m1]["is_active"] and by[m1]["aftermarket_tier"] is None
    assert by[m2]["part_type"] == "aftermarket" and by[m2]["aftermarket_tier"] == "OE_equivalent"
    assert by[m3]["needs_oem_lookup"] is True and by[m1]["needs_oem_lookup"] is False       # nameless row flagged (RULE 7)
    sid = run(V("SELECT id FROM suppliers WHERE name=$1", f"{brand} Official Importer Israel"))
    offers = run(Q("SELECT id, supplier_sku, price_ils FROM supplier_parts WHERE supplier_id=$1", sid))
    assert sorted(r["supplier_sku"] for r in offers) == sorted([m1, m2, m3])               # offers only for THIS run's parts
    assert run(V("SELECT count(*) FROM part_vehicle_fitment WHERE part_id = ANY($1)", [r["id"] for r in parts])) == 0   # fitment is delegated to REX by design
    ids_before = {r["sku"]: r["id"] for r in parts}
    offer_ids = {r["supplier_sku"]: r["id"] for r in offers}

    # repeat: idempotent - same rows, no duplicates, no history
    assert asyncio.run(go("118.00")) == 3
    out2 = capsys.readouterr().out
    assert "parts new=0, updated=3" in out2 and "supplier_parts upserted=3 (new=0, updated=3)" in out2, out2[-600:]
    assert "rex_todo=exists (not duplicated)" in out2, out2[-400:]
    assert run(V("SELECT count(*) FROM agent_todos WHERE assigned_to_agent='rex' AND title LIKE $1", f"Fetch fitment for {brand} samelet parts (%")) == 1
    parts2 = run(Q("SELECT id, sku FROM parts_catalog WHERE manufacturer=$1", brand))
    assert {r["sku"]: r["id"] for r in parts2} == ids_before and len(parts2) == 3
    assert run(V("SELECT count(*) FROM supplier_parts WHERE supplier_id=$1", sid)) == 3
    assert all(run(hist(i)) == [] for i in offer_ids.values())

    # conflict policy: a better category set by someone else is kept; a non-positive source price never erases a stored price
    run(X("UPDATE parts_catalog SET category='brakes' WHERE id=$1", by[m1]["id"]))
    run(X("UPDATE parts_catalog SET category='כללי' WHERE id=$1", by[m2]["id"]))
    assert asyncio.run(go("0")) == 3
    assert run(V("SELECT category FROM parts_catalog WHERE id=$1", by[m1]["id"])) == "brakes"
    assert run(V("SELECT category FROM parts_catalog WHERE id=$1", by[m2]["id"])) != "כללי"         # catch-all is filled from the source name
    assert float(run(V("SELECT importer_price_ils FROM parts_catalog WHERE id=$1", by[m1]["id"]))) == 100.0
    assert float(run(V("SELECT max_price_ils FROM parts_catalog WHERE id=$1", by[m1]["id"]))) == 118.0

    # price change on one part -> same rows, exactly one history row, on that offer only
    assert asyncio.run(go("236.00")) == 3
    assert run(V("SELECT count(*) FROM parts_catalog WHERE manufacturer=$1", brand)) == 3
    assert float(run(V("SELECT importer_price_ils FROM parts_catalog WHERE id=$1", by[m1]["id"]))) == 200.0
    changed = {k: run(hist(v)) for k, v in offer_ids.items()}
    assert len(changed[m1]) == 1 and changed[m1][0]["source"] == "samelet_import_v2"
    assert float(changed[m1][0]["old_price_ils"]) == 100.0 and float(changed[m1][0]["new_price_ils"]) == 200.0
    assert changed[m2] == [] and changed[m3] == []


@pytest.mark.parametrize("state", STATES)
def test_cpie_import_counts_and_logs_skipped_supplier_rows(state, tmp_path):
    """F2: a supplier_parts insert that fails (here: the same supplier+part already holds a different SKU of the same
    variant) is skipped as before, but is now counted (supplier_skipped) and logged with the SKU and reason."""
    run(_set_state(state))
    t = tag()
    sku = f"SK{t}"
    f = tmp_path / "p.json"
    f.write_text(json.dumps({"manufacturer": "Toyota", "maker": "toyota", "model": "Corolla", "vehicle_slug": "toyota/corolla",
                             "parts": [{"sku": sku, "name": f"Pad {t}", "price_eur": 10.0, "product_url": f"https://x/{t}", "category": "brake-pads"}]}))

    def go():
        return subprocess.run([sys.executable, "-B", str(ROOT / "importers" / "car_parts_ie_import_generic.py"), "--brand", "toyota", "--file", str(f),
                               "--vehicle-slug", "toyota/corolla"], capture_output=True, text=True, env=sub_env(), cwd=str(ROOT))
    r = go()
    assert r.returncode == 0, r.stderr[-400:]
    out = json.loads(r.stdout[r.stdout.index("{"):])
    assert out["supplier_rows"] == 1 and out["supplier_skipped"] == 0
    pid = run(V("SELECT sp.part_id FROM supplier_parts sp JOIN suppliers s ON s.id=sp.supplier_id WHERE s.name='Car-Parts.ie' AND sp.supplier_sku=$1", sku))
    # force a clash: an untyped offer of the same supplier+part under ANOTHER SKU exists, then the importer's SKU row is removed
    sid = run(V("SELECT id FROM suppliers WHERE name='Car-Parts.ie'"))
    run(X("UPDATE supplier_parts SET supplier_sku=$3 WHERE supplier_id=$1 AND supplier_sku=$2", sid, sku, f"OTHER{t}"))
    r2 = go()
    assert r2.returncode == 0, r2.stderr[-400:]
    out2 = json.loads(r2.stdout[r2.stdout.index("{"):])
    assert out2["supplier_skipped"] == 1 and out2["supplier_rows"] == 0
    assert f"supplier_parts skip {sku}" in r2.stdout and "UniqueViolationError" in r2.stdout
    assert run(V("SELECT count(*) FROM supplier_parts WHERE supplier_id=$1 AND part_id=$2", sid, pid)) == 1        # data behaviour unchanged


# ================================================================== 5. eBay price sync (real function, mocked eBay client)
@pytest.mark.parametrize("state", STATES)
def test_ebay_sync_first_repeat_change_and_non_il(state, monkeypatch):
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.pool import NullPool
    from services.suppliers.base_supplier import PartResult
    import services.ebay_price_sync as es
    run(_set_state(state))
    t = tag()
    oem1, oem2 = f"EB{t}1", f"EB{t}2"
    run(supplier("eBay Motors", "US"))
    p1 = run(part(f"SAAB-EB{t}1", oem=oem1, manufacturer="Kia"))
    p2 = run(part(f"SAAB-EB{t}2", oem=oem2, manufacturer="Kia"))
    monkeypatch.setenv("EBAY_PRICE_SYNC_BRANDS", "Kia")
    prices = {oem1: 40.0, oem2: 25.0}
    ship = {oem1: True, oem2: False}

    def hit(oem):
        return PartResult(supplier="ebay", item_id=f"it{oem}", title=f"{oem} pad", price=prices[oem], currency="USD", shipping_cost=5.0,
                          total_cost=prices[oem] + 5.0, condition="New", seller="s", seller_rating=99.0, item_url=f"https://ebay.example/{oem}",
                          image_url=None, location="US", estimated_delivery_days=9, ships_to_israel=ship[oem])

    async def search(oem, limit=3):
        return [hit(oem)] if oem in prices else []

    async def details(item_id):
        return None
    monkeypatch.setattr(es.ebay, "search_by_oem", search)
    monkeypatch.setattr(es.ebay, "get_part_details", details)
    monkeypatch.setattr(es, "_meili_sync_part", mock.AsyncMock(return_value=False))
    monkeypatch.setattr(es.asyncio, "sleep", mock.AsyncMock())
    url = SUB_URL.replace("postgresql://", "postgresql+asyncpg://") if "+asyncpg" not in SUB_URL else SUB_URL
    # connect through the DSN host (not the production-host alias) for the in-process engine
    url = DSN.replace("postgresql://", "postgresql+asyncpg://")

    async def sync():
        es._EBAY_SYNC_LAST_ID = "00000000-0000-0000-0000-000000000000"
        eng = create_async_engine(url, poolclass=NullPool)
        try:
            async with AsyncSession(eng, expire_on_commit=False) as db:
                rep = await es.sync_ebay_prices(db, limit_per_run=5000)
                await db.commit()
                return rep
        finally:
            await eng.dispose()
    sid = run(V("SELECT id FROM suppliers WHERE name='eBay Motors'"))
    rep = asyncio.run(sync())
    row1 = run(Q("SELECT id, price_ils, is_available FROM supplier_parts WHERE supplier_id=$1 AND part_id=$2", sid, p1))
    assert len(row1) == 1 and row1[0]["is_available"] is True, rep
    o1 = row1[0]["id"]
    h1 = run(hist(o1))
    assert len(h1) == 1 and h1[0]["old_price_ils"] is None and h1[0]["source"] == "ebay_sync"   # eBay records the first price itself
    # non-IL item: no offer created for p2 as available
    row2 = run(Q("SELECT is_available FROM supplier_parts WHERE supplier_id=$1 AND part_id=$2", sid, p2))
    assert all(r["is_available"] is False for r in row2)
    run(X("UPDATE parts_catalog SET base_price=NULL WHERE id=ANY($1)", [p1, p2]))   # make it eligible again (target = unpriced parts)
    asyncio.run(sync())
    assert run(V("SELECT count(*) FROM supplier_parts WHERE supplier_id=$1 AND part_id=$2", sid, p1)) == 1
    assert len(run(hist(o1))) == 1, "same price twice: no new history"
    prices[oem1] = 55.0
    run(X("UPDATE parts_catalog SET base_price=NULL WHERE id=ANY($1)", [p1, p2]))
    asyncio.run(sync())
    h = run(hist(o1))
    assert len(h) == 2 and float(h[1]["new_price_ils"]) > float(h[1]["old_price_ils"]) and float(h[1]["old_price_ils"]) == float(h[0]["new_price_ils"]), h
    assert run(V("SELECT count(*) FROM supplier_parts WHERE supplier_id=$1 AND part_id=$2", sid, p1)) == 1
    # a typed eBay variant of the same part (state B only) is never selected over the unlisted-type row and is untouched
    if state.startswith("B"):
        typed = run(offer(sid, p1, f"{oem1}-T", 999.0, part_type="oem"))
        prices[oem1] = 60.0
        run(X("UPDATE parts_catalog SET base_price=NULL WHERE id=ANY($1)", [p1, p2]))
        asyncio.run(sync())
        assert float(run(V("SELECT price_ils FROM supplier_parts WHERE id=$1", typed))) == 999.0
        assert len(run(hist(o1))) == 3


@pytest.mark.parametrize("state", STATES)
def test_cpie_import_all_parts_skipped_is_loud_but_exit_0(state, tmp_path):
    """Brand the site does not list and car_brands does not know (car_brand_resolver -> unresolved): nothing is written (no guessed
    UUID, no FK failure), exit 0, and the skip is loud: skip_reasons brand_unresolved + ERROR line."""
    run(_set_state(state))
    t = tag()
    f = tmp_path / "p.json"
    f.write_text(json.dumps({"manufacturer": "Zzbrand", "maker": "zzbrand", "model": "M1", "vehicle_slug": "zzbrand/m1",
                             "parts": [{"sku": f"ZB{t}{i}", "name": f"Pad {t}{i}", "price_eur": 5.0 + i, "product_url": f"https://x/{t}{i}", "category": "brake-pads"} for i in range(3)]}))
    r = subprocess.run([sys.executable, "-B", str(ROOT / "importers" / "car_parts_ie_import_generic.py"), "--brand", "zzbrand", "--file", str(f),
                        "--vehicle-slug", "zzbrand/m1"], capture_output=True, text=True, env=sub_env(), cwd=str(ROOT))
    assert r.returncode == 0, r.stderr[-400:]
    out = json.loads(r.stdout[r.stdout.index("{"):])
    assert out["parts_inserted"] == 0 and out["parts_updated"] == 0 and out["parts_skipped"] == 3
    assert out["skip_reasons"] == {"brand_unresolved": 3}
    assert "brand_unresolved brand='zzbrand'" in r.stderr
    assert run(V("SELECT count(*) FROM parts_catalog WHERE sku LIKE $1", f"ZZBRA-ZB{t}%")) == 0
