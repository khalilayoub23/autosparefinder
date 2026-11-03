"""base_price writers vs the central price policy (2026-11-01).

Gate B of the maintenance-safety task: every executable writer of `parts_catalog.base_price` follows
`price_policy.py` (`base_price_for_cost` for a writer that has a cost, `apply_base_price_rule` for the maintenance
tasks / healers), no writer overwrites a value that fix_base_prices / normalize_base_price own, and nothing invents a
price. The converted writers are exercised against the isolated database; the static ratchet at the bottom freezes the
inventory (docs/BASE_PRICE_WRITERS.md): a NEW writer file, or a changed classification, fails the suite until the
inventory and the report are updated.
"""
import json
import os
import re
import uuid
from contextlib import asynccontextmanager
from datetime import datetime

import asyncpg
import pytest
import sqlalchemy as sa

import isolated_db_guard
import price_policy as pp
from base_price_inventory import scan
from test_maintenance_policy_alignment import base_of, db, offer, part, supplier  # noqa: F401
import maint_guard_support as support

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    support.neutral_probes(monkeypatch)


@asynccontextmanager
async def pg():
    """A raw asyncpg connection to the ISOLATED database inside a transaction that is always rolled back."""
    url = os.environ["DATABASE_URL"]
    refusal = isolated_db_guard.url_refusal(url)
    if refusal is not None:
        pytest.skip(f"isolated DB only: {refusal}")
    conn = await asyncpg.connect(url.replace("postgresql+asyncpg://", "postgresql://"))
    tr = conn.transaction()
    await tr.start()
    try:
        yield conn
    finally:
        await tr.rollback()
        await conn.close()


# A policy writer may keep ONE documented line that is not a base_price formula.
ALLOWED_FORMULA_LINES = {"catalog_scraper.py": "derived_cost_ils = median_ils / 1.18 / 1.45 (a supplier COST from a scraped market price)"}

SAMPLE_COSTS = [0.01, 1, 3.33, 9.99, 17.5, 45.45, 80, 99.99, 123.45, 1999.99, 25000]


# ── the policy accessor ───────────────────────────────────────────────────────────────────────────────────────
def test_base_price_for_cost_is_the_policy_net_and_zero_without_a_cost():
    for c in SAMPLE_COSTS:
        assert pp.base_price_for_cost(c) == pp.unit_price(c).net == round(c * 1.45, 2)
        assert pp.base_price_for_cost(c) == pp.unit_price(c, supplier_country="IE").net      # foreign: same net, VAT 0
    for c in (None, 0, -5, "", "x"):
        assert pp.base_price_for_cost(c) == 0.0


def test_mechanical_importer_conversions_are_value_neutral():
    """Every `round(x * 1.45, 2)` / `round(x * (1 + MARGIN), 2)` converted to the accessor gives the same figure."""
    for c in SAMPLE_COSTS:
        assert pp.base_price_for_cost(c) == round(c * 1.45, 2) == round(c * (1 + 0.45), 2) == round(c * 0.45 + c, 2)
    # the one converted expression that was not a plain `x * 1.45` (isuzu: from the consumer price)
    for retail in (118, 235.7, 1000, 4999.9):
        assert pp.base_price_for_cost(round(retail / 1.18, 2)) == pytest.approx(round(retail / 1.18 * 1.45, 2), abs=0.01)


# ── catalog_scraper.db_upsert_part (REX, every 3 h) ───────────────────────────────────────────────────────────
@pytest.fixture
def scraper(monkeypatch):
    import catalog_scraper as cs

    async def no_meili(*a, **k):
        return None
    monkeypatch.setattr(cs, "_meili_sync_part", no_meili)
    return cs


async def _upsert(cs, db, sku, **kw):
    kw.setdefault("name", "Test pad")
    kw.setdefault("manufacturer", "BMW")      # a car_brands row the shared fixture creates (a trigger derives manufacturer_id)
    kw.setdefault("category", "brakes")
    return await cs.db_upsert_part(db, sku=sku, **kw)


async def _base_by_sku(db, sku):
    v = (await db.execute(sa.text("SELECT base_price FROM parts_catalog WHERE sku = :s"), {"s": sku})).scalar()
    return None if v is None else float(v)


async def test_new_discovered_part_gets_the_policy_net_not_the_raw_supplier_price(db, scraper):
    sku = f"BPW-{uuid.uuid4().hex[:10]}"
    _, created = await _upsert(scraper, db, sku, cost_ils=100.0)
    assert created and await _base_by_sku(db, sku) == 145.0
    sku2 = f"BPW-{uuid.uuid4().hex[:10]}"
    cost = scraper._pp_offer_cost({"price_usd": 10.0, "price_ils": 0})          # USD-only source: price x live rate
    assert cost == pytest.approx(10.0 * scraper.ILS_PER_USD)
    await _upsert(scraper, db, sku2, cost_ils=cost)
    assert await _base_by_sku(db, sku2) == pp.base_price_for_cost(cost) != 10.0   # the old code stored 10.0 (raw USD)


async def test_rediscovery_never_overwrites_a_valid_base_price_and_only_fills_an_empty_one(db, scraper):
    sku = f"BPW-{uuid.uuid4().hex[:10]}"
    await _upsert(scraper, db, sku, cost_ils=100.0)                            # 145.0
    await db.execute(sa.text("UPDATE parts_catalog SET base_price = 77.77 WHERE sku = :s"), {"s": sku})   # owned value
    await _upsert(scraper, db, sku, cost_ils=999.0)                            # the old code wrote the raw price here
    assert await _base_by_sku(db, sku) == 77.77
    await _upsert(scraper, db, sku, cost_ils=0.0)                              # no price in the source: the old code wrote 0.0
    assert await _base_by_sku(db, sku) == 77.77
    await db.execute(sa.text("UPDATE parts_catalog SET base_price = 0 WHERE sku = :s"), {"s": sku})
    await _upsert(scraper, db, sku, cost_ils=200.0)
    assert await _base_by_sku(db, sku) == 290.0                                # empty -> the policy net of the cost
    await _upsert(scraper, db, sku, cost_ils=200.0)
    assert await _base_by_sku(db, sku) == 290.0                                # repeated run: unchanged


async def test_part_without_any_cost_gets_the_no_price_sentinel_not_a_guess(db, scraper):
    sku = f"BPW-{uuid.uuid4().hex[:10]}"
    await _upsert(scraper, db, sku)                                            # cost_ils defaults to 0.0
    assert await _base_by_sku(db, sku) == 0.0


# ── ai_catalog_builder.insert_parts (CLI; LLM-estimated prices) ───────────────────────────────────────────────
async def test_ai_catalog_builder_never_writes_the_llm_price_into_base_price():
    import ai_catalog_builder as acb
    async with pg() as conn:
        sup = await conn.fetchval("""INSERT INTO suppliers (id, name, country, is_active)
                                     VALUES (gen_random_uuid(), $1, 'IL', TRUE) RETURNING id""", f"AI test {uuid.uuid4().hex[:8]}")
        tag = uuid.uuid4().hex[:8]
        brand = f"zzbrand{tag}"
        await conn.execute("INSERT INTO car_brands (id, name) VALUES (gen_random_uuid(), $1)", brand)
        parts = [{"name_he": "רפידות", "name_en": "Pads", "catalog_num": f"AIT{tag}1", "price_ils": 400, "category": "brakes"}]
        cat, sp, errs = await acb.insert_parts(conn, sup, brand, parts, set(), False, datetime.utcnow())
        assert (cat, errs) == (1, 0)
        row = await conn.fetchrow("SELECT sku, base_price, specifications FROM parts_catalog WHERE sku = $1",
                                  acb.make_sku(brand, f"AIT{tag}1", 0))
        assert row["base_price"] is None                                  # the old code stored the 400 guess here
        assert json.loads(row["specifications"])["base_price_note"] == acb.BASE_PRICE_NOTE
        # conflict-update: a valid owned value survives a rebuild of the same SKU
        await conn.execute("UPDATE parts_catalog SET base_price = 123.45 WHERE sku = $1", row["sku"])
        await acb.insert_parts(conn, sup, brand, [{**parts[0], "price_ils": 9999, "name_he": "שונה"}], set(),
                               False, datetime.utcnow())
        again = await conn.fetchrow("SELECT base_price, name FROM parts_catalog WHERE sku = $1", row["sku"])
        assert float(again["base_price"]) == 123.45 and "שונה" in again["name"]      # name updated, price untouched


# ── admin Excel upload ────────────────────────────────────────────────────────────────────────────────────────
class _Upload:
    def __init__(self, data, filename="parts.xlsx"):
        self.filename, self._data = filename, data

    async def read(self):
        return self._data


def _xlsx(rows):
    import io
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


async def test_admin_upload_does_not_type_a_price_into_base_price(db):
    from routes.admin import import_parts_excel
    tag = uuid.uuid4().hex[:8]
    await db.execute(sa.text("""INSERT INTO parts_catalog (id, sku, name, manufacturer, part_type, is_active, base_price)
        VALUES (gen_random_uuid(), :s, 'old', 'BMW', 'oem', TRUE, 88.0)"""), {"s": f"BPU-{tag}-old"})
    await db.commit()
    data = _xlsx([["sku", "name", "price", "manufacturer"], [f"BPU-{tag}-old", "renamed", 5, "BMW"], [f"BPU-{tag}-new", "fresh", 7, "BMW"]])
    out = await import_parts_excel(_Upload(data), None, db)
    assert out["created"] == 1 and out["updated"] == 1 and out["prices_not_applied"] == 2 and not out["errors"]
    assert await _base_by_sku(db, f"BPU-{tag}-old") == 88.0                    # the old code overwrote it with 5
    assert await _base_by_sku(db, f"BPU-{tag}-new") is None                    # the old code stored 7 (an unexplained number)


# ── car-parts.ie importer (harvester relay) and freesbe normalizer: the SQL itself ────────────────────────────
def _cpie_sql():
    src = open(os.path.join(BACKEND, "importers", "car_parts_ie_import_generic.py")).read()
    end = src.index("RETURNING id, (xmax = 0) AS is_insert") + len("RETURNING id, (xmax = 0) AS is_insert")
    return src[src.rindex("INSERT INTO parts_catalog(", 0, end):end]


async def _cpie_upsert(conn, sku, brand_id, base):
    return await conn.fetchrow(_cpie_sql(), sku, sku, "pad", "Aftermarket", brand_id, "brakes", "{}", "[]",
                               10.0, 10.0, base, "d", False)


async def test_car_parts_ie_reharvest_fills_an_empty_base_price_but_keeps_an_owned_one():
    async with pg() as conn:
        brand = await conn.fetchval("INSERT INTO car_brands (id, name) VALUES (gen_random_uuid(), $1) RETURNING id",
                                    f"zz-bpw-{uuid.uuid4().hex[:8]}")
        sku = f"CPIE-{uuid.uuid4().hex[:10]}"
        first = await _cpie_upsert(conn, sku, brand, pp.base_price_for_cost(10.0))
        assert first["is_insert"] and float(await conn.fetchval("SELECT base_price FROM parts_catalog WHERE id=$1", first["id"])) == 14.5
        await conn.execute("UPDATE parts_catalog SET base_price = 33.0 WHERE id = $1", first["id"])      # fix_base_prices' value
        await _cpie_upsert(conn, sku, brand, pp.base_price_for_cost(50.0))                               # re-harvest, new price
        assert float(await conn.fetchval("SELECT base_price FROM parts_catalog WHERE id=$1", first["id"])) == 33.0
        await conn.execute("UPDATE parts_catalog SET base_price = NULL WHERE id = $1", first["id"])
        await _cpie_upsert(conn, sku, brand, pp.base_price_for_cost(50.0))
        assert float(await conn.fetchval("SELECT base_price FROM parts_catalog WHERE id=$1", first["id"])) == 72.5


async def test_freesbe_normalizer_uses_the_policy_margin_and_fills_only_empty_prices():
    from importers.freesbe_importer import normalize_base_price_batched
    async with pg() as conn:
        tag = uuid.uuid4().hex[:8]
        brand = await conn.fetchval("INSERT INTO car_brands (id, name) VALUES (gen_random_uuid(), $1) RETURNING id", f"zz-fb-{tag}")
        for sku, imp, base in ((f"FB-{tag}-a", 100.0, None), (f"FB-{tag}-b", 100.0, 5.0)):
            await conn.execute("""INSERT INTO parts_catalog (id, sku, name, manufacturer, manufacturer_id, part_type, is_active,
                importer_price_ils, base_price) VALUES (gen_random_uuid(), $1, 'x', 'BMW', $4, 'oem', TRUE, $2, $3)""", sku, imp, base, brand)
        await normalize_base_price_batched(conn, batch_size=50)
        got = {r["sku"]: float(r["base_price"]) for r in await conn.fetch(
            "SELECT sku, base_price FROM parts_catalog WHERE sku LIKE $1", f"FB-{tag}-%")}
        assert got == {f"FB-{tag}-a": 145.0, f"FB-{tag}-b": 5.0}


# ── the inventory (docs/BASE_PRICE_WRITERS.json is the ONE authoritative registry) ─────────────────────────────
REGISTRY_PATH = os.path.join(os.path.dirname(BACKEND), "docs", "BASE_PRICE_WRITERS.json")
FINAL_STATUSES = {"VERIFIED_COMPLIANT", "FIXED_AND_VERIFIED", "INACTIVE_VERIFIED", "E2E_VERIFIED_FIXTURE", "E2E_VERIFIED_SAVED_SOURCE", "E2E_VERIFIED_LIVE_SOURCE", "BLOCKED_WITH_EVIDENCE"}


def _registry():
    if not os.path.exists(REGISTRY_PATH):
        pytest.skip("docs/BASE_PRICE_WRITERS.json is not mounted in this tree")
    return json.load(open(REGISTRY_PATH))


def test_registry_equals_the_ast_scan_and_every_writer_has_exactly_one_final_status():
    reg = _registry()
    found = scan(BACKEND)
    listed = {w["file"] for w in reg["writers"]}
    assert not (set(found) - listed), f"base_price writer(s) missing from docs/BASE_PRICE_WRITERS.json: {sorted(set(found) - listed)}"
    assert not (listed - set(found)), f"registry lists a file that no longer writes base_price: {sorted(listed - set(found))}"
    assert len(listed) == len(reg["writers"]), "a writer is listed twice"
    for w in reg["writers"]:
        assert w["status"] in FINAL_STATUSES, w
        assert w["evidence"].strip(), w
        if w["status"] == "BLOCKED_WITH_EVIDENCE":
            assert w["limitation"].strip(), f"{w['file']}: a blocked writer must state its blocker"
        if w["status"] in ("VERIFIED_COMPLIANT", "FIXED_AND_VERIFIED"):
            assert not w["limitation"], w


# writes only the catalogue's "no price" sentinel (0.0) - never a computed price
SENTINEL_ONLY = {"maintenance/base_price_snapshot.py", "importers/repair_manufacturer_import.py", "importers/import_parts_db.py"}


def test_no_executable_writer_keeps_its_own_price_formula():
    found = scan(BACKEND)
    for f, v in found.items():
        extra = [n for n in v["formulas"] if f not in ALLOWED_FORMULA_LINES]
        assert not extra, f"{f} computes its own price on line(s) {extra}: use price_policy.base_price_for_cost / base_price_for_estimate"
    for f in found:
        src = open(os.path.join(BACKEND, f), errors="ignore").read()
        assert re.search(r"base_price_for_cost|base_price_for_estimate|apply_base_price_rule|catalog_estimate|_pp\.MARGIN|_price_policy\.MARGIN|price_policy\.MARGIN|ROUND\(pairs", src) \
            or f in SENTINEL_ONLY, f"{f} does not take its base_price from the price policy"


def test_shell_and_sql_writers_are_registered():
    from base_price_inventory import scan_text_files
    reg = _registry()
    listed = {w["file"] for w in reg["shell_writers"]}
    repo = os.path.dirname(os.path.dirname(BACKEND)) if os.path.basename(BACKEND) == "backend" else None
    if repo is None or not os.path.isdir(os.path.join(repo, "backend")):
        pytest.skip("repository root not available")
    found = set(scan_text_files(repo))
    assert found <= listed, f"unregistered shell/SQL base_price writer(s): {sorted(found - listed)}"


def test_the_only_code_that_writes_base_price_in_the_scheduled_tasks_is_the_policy_rule():
    import inspect
    import db_update_agent as dua
    import db_cleanup_agent as dca
    assert "base_price = t.v" in inspect.getsource(dua.apply_base_price_rule)
    for fn in (dca.task_heal_importer_price, dca.normalize_base_price_for_parts):
        assert "apply_base_price_rule" in inspect.getsource(fn)
        assert "1.45" not in inspect.getsource(fn)


# files whose base_price assignment is not a CASE guard but is restricted to empty values by COALESCE / the WHERE clause
EMPTY_ONLY_BY_WHERE = {"harvesters/spareto_harvester.py", "importers/amayama_price_import.py", "importers/rockauto_price_import.py",
                       "importers/freesbe_importer.py", "importers/xls_prices_import.py"}
POLICY_OWNERS = {"db_update_agent.py", "db_cleanup_agent.py", "maintenance/base_price_snapshot.py"}


def test_no_importer_overwrites_a_non_empty_base_price():
    """Ownership rule: an importer / harvester may only FILL an empty base_price. Every SET base_price = <x> outside the
    policy owners is a CASE guard on `base_price IS NULL OR = 0`, or lives in a statement whose WHERE restricts it to empty."""
    import ast
    from base_price_inventory import _sql_assigns_base_price, py_files
    offenders = []
    for p in py_files(BACKEND):
        rel = os.path.relpath(p, BACKEND)
        if rel in POLICY_OWNERS or rel in EMPTY_ONLY_BY_WHERE:
            continue
        for n in ast.walk(ast.parse(open(p, errors="ignore").read())):
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and _sql_assigns_base_price(n.value):
                for part in re.split(r"\bSET\b", n.value, flags=re.I)[1:]:
                    # a guard is `base_price IS NULL OR base_price = 0`; mask it, then every remaining assignment must be
                    # `= CASE WHEN <guard> ...` or `= COALESCE(...)`. A CASE on `$N > 0` / `EXCLUDED.x > 0` is NOT a guard.
                    masked = re.sub(r"(?:\w+\.)?base_price\s+IS\s+NULL\s+OR\s+(?:\w+\.)?base_price\s*=\s*0", "<GUARD>", part, flags=re.I)
                    for m in re.finditer(r"(?<![\w.])base_price\s*=\s*(?!=)(.{0,160})", masked, re.S):
                        rest = m.group(1).lstrip()
                        if rest.startswith("0") or rest.upper().startswith("CASE WHEN <GUARD>") or \
                                re.match(r"CASE\s+WHEN\s+<GUARD>", rest, re.I) or rest.upper().startswith("COALESCE("):
                            continue
                        offenders.append((rel, n.lineno, m.group(0)[:60]))
    assert not offenders, f"unguarded base_price overwrite: {offenders}"
    # the exceptions really are empty-only
    for rel in EMPTY_ONLY_BY_WHERE:
        src = open(os.path.join(BACKEND, rel), errors="ignore").read()
        assert re.search(r"base_price IS NULL OR base_price\s*=\s*0|COALESCE\(base_price", src), rel
