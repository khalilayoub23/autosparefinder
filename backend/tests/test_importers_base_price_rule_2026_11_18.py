"""Importers stop being second base_price writers (2026-11-18). Isolated DB only (end-to-end, in-process).

Defect (FIXES_TRACKER, base_price RCA 2026-11-16/17): ~15 importers each wrote their own `cost x 1.45` into base_price next to the ownership
rule (base_price_rule.py == what the maintenance tasks run), so a part flipped between the importer's number and the rule's on every run
(integer-rounded Colmobil bases, Kia base == max, foreign offers valued at another rate, ...). The importers below now write NO base_price of
their own (new parts start 0.0) and hand the parts they touched to base_price_rule.apply_to_parts after the offer exists.

Checks per importer: (1) the persisted base_price equals the rule's target for the part (a second apply writes nothing), (2) it is not the
old importer formula where the two differ, (3) source guard: no `1.45` base_price formula and a base_price_rule call remain in the file."""
import ast, asyncio, json, os, re, sys, uuid
from pathlib import Path
import pytest, asyncpg
import isolated_db_guard

ROOT = Path(__file__).resolve().parent.parent
for sub in ("", "importers"):
    sys.path.insert(0, str(ROOT / sub) if sub else str(ROOT))

MIGRATED = [
    "harvesters/acura_browser_harvest.py",
    "harvesters/spareto_harvester.py",
    "importers/amayama_price_import.py",
    "importers/asap_import.py",
    "importers/bmw_il_direct_import.py",
    "importers/bmw_il_import.py",
    "importers/cadillac_israel_import.py",
    "importers/car_parts_ie_import_generic.py",
    "importers/colmobil_import_v2.py",
    "importers/delek_json_price_update.py",
    "importers/delek_multi_importer.py",
    "importers/ev_vag_lexus_import.py",
    "importers/fitz_mb_suzuki_import.py",
    "importers/fitz_pdf_import.py",
    "importers/freesbe_importer.py",
    "importers/freesbe_recovery.py",
    "importers/geely_israel_import.py",
    "importers/gmc_buick_umi_import.py",
    "importers/hyundai_genesis_mitsubishi_import.py",
    "importers/il_consumer_price_import.py",
    "importers/il_importer_pdf_import.py",
    "importers/il_pdf_batch_import.py",
    "importers/import_champion_motors.py",
    "importers/import_delek_brands.py",
    "importers/import_from_excel.py",
    "importers/import_kia_israel.py",
    "importers/isuzu_excel_import.py",
    "importers/jaguar_batch_import.py",
    "importers/jaguar_ndjson_import.py",
    "importers/kgm_ssangyong_importer.py",
    "importers/kia_il_direct_import.py",
    "importers/kia_import.py",
    "importers/kia_new_models_import.py",
    "importers/lexus_pdf_import.py",
    "importers/lr_import.py",
    "importers/lr_subaru_byd_import.py",
    "importers/mazda_il_importer.py",
    "importers/mct_importer.py",
    "importers/mixed_brands_import.py",
    "importers/porsche_import.py",
    "importers/renault_pdf_import.py",
    "importers/rockauto_price_import.py",
    "importers/samelet_import_v2.py",
    "importers/sng_barratt_jaguar_import.py",
    "importers/subaru_il_importer.py",
    "importers/supplier_pdf_import.py",
    "importers/toyota_il_importer.py",
    "importers/xls_prices_import.py",
    "importers/xlsx_il_price_import.py",
    "importers/zeekr_full_import.py",
    "importers/zeekr_pdf_import.py",
    "oempartsonline_importer.py",
    "scrapers/bydil_scraper.py",
    "scrapers/eliteparts_scraper.py",
]

# base_price formula spellings the importers used; none may remain in a migrated file's executable code
_FORMULA = re.compile(r"(base_price\w*\s*=[^=\n]*1\.45|\*\s*1\.45|MARGIN)")


def _code_without_docstrings_and_comments(path: Path) -> str:
    src = path.read_text()
    tree = ast.parse(src)
    doc_lines = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and ast.get_docstring(node, clean=False) is not None:
            d = node.body[0]
            doc_lines.update(range(d.lineno, d.end_lineno + 1))
    out = []
    for i, line in enumerate(src.splitlines(), 1):
        if i in doc_lines:
            continue
        out.append(line.split("#", 1)[0] if '"' not in line and "'" not in line else line)
    return "\n".join(out)


@pytest.mark.parametrize("rel", MIGRATED)
def test_migrated_importer_has_no_own_base_price_formula_and_calls_the_rule(rel):
    p = ROOT / rel
    code = _code_without_docstrings_and_comments(p)
    assert "base_price_rule" in code and ("apply_and_report" in code or "apply_to_parts" in code), f"{rel} must hand its parts to base_price_rule"
    bad = [l.strip() for l in code.splitlines() if _FORMULA.search(l) and "importer_price_ils" not in l]
    assert not bad, f"{rel} still has its own base_price formula: {bad[:3]}"
    ast.parse(p.read_text())


def _dsn():
    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


async def _connect():
    ref = isolated_db_guard.static_refusal()
    if ref is not None:
        pytest.skip(f"isolated DB only: {ref}")
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.pool import NullPool
    eng = create_async_engine(os.environ["DATABASE_URL"], poolclass=NullPool)
    async with AsyncSession(eng) as s:
        live = await isolated_db_guard.live_refusal(s)
    await eng.dispose()
    if live is not None:
        pytest.fail(f"REFUSED before any write: {live}")
    return await asyncpg.connect(_dsn())


async def _assert_follows_rule(c, part_id, expect_positive=True):
    """The importer left base_price exactly where the ownership rule puts it: a second apply is a no-op."""
    import base_price_rule
    before = await c.fetchval("SELECT base_price FROM parts_catalog WHERE id=$1::uuid", str(part_id))
    res = await base_price_rule.apply_to_parts(c, [str(part_id)])
    assert res["updated"] == 0, f"importer base_price {before} != rule target ({res})"
    if expect_positive:
        assert float(before or 0) > 0, "no base_price after import"
    return float(before or 0)


async def _cleanup(c, tag):
    ids = [r["id"] for r in await c.fetch("SELECT id FROM parts_catalog WHERE oem_number LIKE $1 OR sku LIKE $1", f"%{tag}%")]
    if ids:
        await c.execute("DELETE FROM price_history WHERE supplier_part_id IN (SELECT id FROM supplier_parts WHERE part_id = ANY($1::uuid[]))", ids)
        await c.execute("DELETE FROM supplier_parts WHERE part_id = ANY($1::uuid[])", ids)
        await c.execute("DELETE FROM part_vehicle_fitment WHERE part_id = ANY($1::uuid[])", ids)
        await c.execute("DELETE FROM parts_images WHERE part_id = ANY($1::uuid[])", ids)
        await c.execute("DELETE FROM parts_catalog WHERE id = ANY($1::uuid[])", ids)


async def _supplier(c, name, country="IL", basis=None):
    """Supplier row; a local supplier needs its VAT basis in the registry, exactly like production (a local supplier of
    unknown basis prices as 0 by design), and the process-wide registry is reset so the importer's rule call reloads it."""
    import price_policy as pp
    sid = await c.fetchval("SELECT id::text FROM suppliers WHERE name=$1 LIMIT 1", name)
    if not sid:
        sid = await c.fetchval("INSERT INTO suppliers (id,name,country,is_active,created_at,updated_at) "
                               "VALUES (gen_random_uuid(),$1,$2,true,NOW(),NOW()) RETURNING id::text", name, country)
    if basis:
        await c.execute("UPDATE suppliers SET vat_price_basis=$2 WHERE id=$1::uuid", sid, basis)
    pp.load_vat_bases(None)
    return sid


async def _brand(c, name, bid=None):
    """car_brands row (parts_catalog.manufacturer_id is NOT NULL + FK) in the isolated DB; returns its id."""
    got = await c.fetchval("SELECT id::text FROM car_brands WHERE LOWER(name)=LOWER($1) LIMIT 1", name)
    if got:
        return got
    return await c.fetchval("INSERT INTO car_brands(id,name,country,is_active) VALUES(COALESCE($2::uuid, gen_random_uuid()),$1,'XX',TRUE) RETURNING id::text", name, bid)


async def _seed_part(c, tag, mfr, oem=None):
    oem = oem or f"{tag}-OEM"
    mid = await _brand(c, mfr)
    return await c.fetchval(
        "INSERT INTO parts_catalog (id,sku,oem_number,name,manufacturer,manufacturer_id,category,is_active,base_price,part_condition,created_at,updated_at) "
        "VALUES (gen_random_uuid(),$1,$2,$3,$4,$5::uuid,'כללי',true,0,'new',NOW(),NOW()) RETURNING id::text", f"SKU-{tag}", oem, f"Test part {tag}", mfr, mid)


async def test_amayama_and_rockauto_price_fill_follow_the_rule(tmp_path):
    c = await _connect()
    tag = "BPT" + uuid.uuid4().hex[:6].upper()
    try:
        import amayama_price_import as am, rockauto_price_import as ra
        for mod, label in ((am, "Amayama"), (ra, "RockAuto")):
            t = tag + label[:2].upper()
            pid = await _seed_part(c, t, "Toyota", oem=f"{t}9091")
            f = tmp_path / f"{label}.json"
            f.write_text(json.dumps([{"oem": f"{t}9091", "name": "FILTER", "brand": "Toyota", "part_num": "X1", "warehouse": "JP",
                                      "price_usd": 10.0, "shipping_usd": 5.0, "part_type": "genuine"}]))
            mod.DB = _dsn()
            await mod.main(str(f))
            assert await c.fetchval("SELECT count(*) FROM supplier_parts WHERE part_id=$1::uuid", pid) >= 1, f"{label}: no offer written"
            await _assert_follows_rule(c, pid)
            await _cleanup(c, t)
    finally:
        await _cleanup(c, tag)
        await c.close()


async def test_oempartsonline_new_part_starts_at_zero_then_follows_the_rule():
    c = await _connect()
    tag = "BPO" + uuid.uuid4().hex[:6].upper()
    try:
        import oempartsonline_importer as oi
        await _brand(c, "Audi", oi.SUPPLIER_MAP["audi"]["manufacturer_id"])
        sid = await oi.ensure_supplier(c, "audi")
        usd = await oi.get_usd_to_ils(c)
        prod = {"sku": f"{tag}8K0", "name": "Brake pad", "msrp": 20.0, "sale_price": 18.0, "in_stock": True,
                "category_path": "Brakes", "vehicle_slug": "2015-audi-a4", "product_url": "/p/1"}
        r = await oi.import_products(c, [prod], "audi", sid, usd)
        assert r["inserted"] == 1 and not r["errors"], r
        pid = await c.fetchval("SELECT id::text FROM parts_catalog WHERE oem_number=$1", f"{tag}8K0")
        await _assert_follows_rule(c, pid)
    finally:
        await _cleanup(c, tag)
        await c.close()


async def test_colmobil_delek_multi_mct_follow_the_rule():
    c = await _connect()
    tag = "BPC" + uuid.uuid4().hex[:6].upper()
    try:
        import colmobil_import_v2 as cm, delek_multi_importer as dm, mct_importer as mc
        # Colmobil: integer-rounded base_price was the measured defect (413 vs rule 413.96)
        sid = await _supplier(c, "Colmobil - Hyundai Israel", basis="inclusive")
        await cm.import_brand(c, "Hyundai", [{"oem": f"{tag}H1", "price": 489.0, "desc": "מסנן"}], sid, await _brand(c, "Hyundai"))
        pid = await c.fetchval("SELECT id::text FROM parts_catalog WHERE oem_number=$1", f"{tag}H1")
        await _assert_follows_rule(c, pid)
        # Delek multi + MCT: an existing part (UPDATE path) and a new part (INSERT path)
        for mod, fn, name in ((dm, dm.upsert_parts, "BMW Delek Motors"), (mc, mc.upsert_batch, "MCT (Mayer Group)")):
            sid = await _supplier(c, name, basis="inclusive")
            mid = await _brand(c, "BMW")
            row = {"oem_number": f"{tag}{name[:2].upper()}1", "name_he": "פריט", "name_en": "Item", "manufacturer": "BMW",
                   "importer_price_ils": 100.0, "max_price_ils": 118.0, "is_available": True, "vehicle_model": ""}
            for _ in range(2):      # second pass = UPDATE path
                if fn is dm.upsert_parts:
                    await fn(c, [dict(row)], sid, mid)
                else:
                    await fn(c, [dict(row)], sid, mid, "BMW")
            pid = await c.fetchval("SELECT id::text FROM parts_catalog WHERE oem_number=$1", row["oem_number"])
            await _assert_follows_rule(c, pid)
    finally:
        await _cleanup(c, tag)
        await c.close()


async def test_kia_toyota_delek_brands_and_kia_direct_follow_the_rule(tmp_path):
    c = await _connect()
    tag = "BPK" + uuid.uuid4().hex[:6].upper()
    try:
        import import_kia_israel as ki, toyota_il_importer as ty, import_delek_brands as db
        # Kia Israel (file-driven)
        if not await c.fetchval("SELECT 1 FROM car_brands WHERE LOWER(name)='kia'"):
            await c.execute("INSERT INTO car_brands(id,name,country,is_active) VALUES(gen_random_uuid(),'Kia','KR',TRUE)")
        await _supplier(c, "Kia Israel", basis="inclusive")
        await _supplier(c, "Toyota Israel - Union Motors", basis="exclusive")
        f = tmp_path / "kia.json"
        f.write_text(json.dumps({"parts": [{"oem_number": f"{tag}K1", "name_he": "בולם", "price_no_vat": 95.0, "in_stock": True}]}))
        ki.INPUT_FILE, ki.DATABASE_URL = str(f), _dsn()
        await ki.run_import()
        pid = await c.fetchval("SELECT id::text FROM parts_catalog WHERE oem_number=$1", f"{tag}K1")
        assert pid, "kia part not written"
        await _assert_follows_rule(c, pid)
        # Toyota IL (file-driven; DSN is hard-coded to production's host, repoint at the verified isolated DB)
        f2 = tmp_path / "toyota.json"
        f2.write_text(json.dumps({"parts": [{"oem": f"{tag}T1", "name_he": "מסנן שמן", "price": 50.0, "models": [], "type": "מקורי", "in_stock": True}]}))
        ty.MANUFACTURER_ID = await _brand(c, "Toyota", ty.MANUFACTURER_ID)
        ty.SOURCE_FILE, ty.DSN = str(f2), _dsn()
        await ty.main()
        pid = await c.fetchval("SELECT id::text FROM parts_catalog WHERE oem_number=$1", f"{tag}T1")
        assert pid, "toyota part not written"
        await _assert_follows_rule(c, pid)
        # Delek brands JSON importer: new part, then the same part again (UPDATE path)
        dname = f"Delek Motors {tag} IL"
        await _supplier(c, dname, basis="exclusive")
        f4 = tmp_path / "delek.json"
        f4.write_text(json.dumps({"parts": [{"oem_number": f"{tag}B1", "name_he": "מסנן אוויר", "name": "Air filter", "price_ils": 120.0, "is_original": True}]}))
        for _ in range(2):
            st = await db.import_brand(c, "BMW", tag[:3], str(f4), dname)
            assert st["errors"] == 0, st
        pid = await c.fetchval("SELECT id::text FROM parts_catalog WHERE oem_number=$1", f"{tag}B1")
        assert pid, "delek_brands part not written"
        await _assert_follows_rule(c, pid)
        # Kia direct (module runs on import -> run its coroutine through a fresh import with argv)
        f3 = tmp_path / "kia_direct.json"
        f3.write_text(json.dumps([{"sku": f"{tag}D1", "name": "Direct", "price": 80.0}]))
        import subprocess   # the legacy tool runs asyncio.run(run()) at import time -> a real subprocess, env = the isolated DB
        r = subprocess.run([sys.executable, str(ROOT / "importers" / "kia_il_direct_import.py"), str(f3)], capture_output=True, text=True,
                           env={**os.environ, "DATABASE_URL": _dsn()}, timeout=120)
        assert r.returncode == 0 and "Done in" in r.stdout, r.stdout[-400:] + r.stderr[-400:]
        pid = await c.fetchval("SELECT id::text FROM parts_catalog WHERE oem_number=$1", f"{tag}D1")
        await _assert_follows_rule(c, pid, expect_positive=False)   # no offer written by this legacy tool -> estimate or untouched
    finally:
        await _cleanup(c, tag)
        await c.close()


def test_samelet_upsert_statement_has_14_parameters_and_no_base_price_update():
    import samelet_import_v2 as sm
    sql = sm._CATALOG_UPSERT
    assert max(int(n) for n in re.findall(r"\$(\d+)", sql)) == 14
    tail = sql.split("ON CONFLICT", 1)[1]
    assert "base_price" not in tail, "re-import must never touch base_price"


# ───────────────────────── repository-wide guard (all 43 + the 11 earlier + every other writer found) ──────────────────────────
# NO file outside this allowlist may write base_price or compute `x 1.45`/`x MARGIN`. The scan reads CODE only (comments/docstrings are
# stripped), over every .py/.sh of backend/ except tests/devtests/legacy/alembic/candidates/state/data/uploads.
_WRITER_PAT = re.compile(r"(?<![A-Za-z_])base_price\s*(?:=(?!=)(?!\s*0(?:\.0+)?\b)|:=)|EXCLUDED\.base_price|SET\s+base_price|"
                         r"\*\s*1\.45|1\.45\s*\*|\*\s*(?:_?pp\.|_pp\.)?MARGIN\b|\b(?:_?pp\.)?MARGIN\s*\*")
_SCAN_EXCLUDE = {"tests", "devtests", "legacy", "alembic", "candidates", "node_modules", "__pycache__", "state", "data", "uploads", "test_images", "archive"}
ALLOWED_WRITERS = {
    # the ONE rule and the maintenance tasks that call it
    "base_price_rule.py": "THE ownership rule",
    "db_update_agent.py": "fix_base_prices / normalize_base_price call the rule (apply_base_price_rule)",
    # definitions, not writers
    "price_policy.py": "the policy: defines MARGIN and the net formula",
    "BACKEND_DATABASE_MODELS.py": "the column definition",
    # dead behind an immediate exit / raise
    "ai_catalog_builder.py": "insert_parts raises before any write (fabricated prices; 2026-11-17)",
    "maintenance/margin_fix_batched.py": "sys.exit('DEPRECATED') at import",
    "maintenance/price_audit_fix.py": "sys.exit('DEPRECATED') at import",
    # owner-run, manifest-pinned / journaled repair tools (not importers): they write the POLICY value or restore a journal
    "maintenance/cpie_base_price_repair.py": "owner-run repair, SHA-pinned manifest, writes the rule's target",
    "maintenance/cpie_overwrite_repair.py": "owner-run repair, manifest-pinned",
    "maintenance/repair_double_margin_129.py": "owner-run journaled repair (rollback)",
    "maintenance/repair_vat_paths_151.py": "owner-run journaled repair (rollback)",
    # human-entered admin spreadsheet price: NOT migrated, open owner decision (FIXES_TRACKER 2026-11-18)
    "routes/admin.py": "admin bulk upload stores the HUMAN-entered price column; cost semantics undefined - owner decision pending",
}


def _strip_code(src: str, suffix: str) -> str:
    if suffix == ".sh":
        return "\n".join(l for l in src.split("\n") if not l.strip().startswith("#"))
    import io, tokenize
    try:
        toks = [t for t in tokenize.generate_tokens(io.StringIO(src).readline) if t.type != tokenize.COMMENT]
    except Exception:
        return src
    code, prev = [], None
    for t in toks:
        if t.type == tokenize.STRING and prev in (None, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT, tokenize.NL):
            prev = t.type
            continue                       # a statement-level string = docstring
        code.append(t)
        if t.type != tokenize.NL:
            prev = t.type
    return tokenize.untokenize(code)


def test_no_file_outside_the_allowlist_writes_or_computes_base_price():
    offenders = {}
    for p in sorted(ROOT.rglob("*")):
        if p.suffix not in (".py", ".sh") or any(x in p.parts for x in _SCAN_EXCLUDE):
            continue
        rel = str(p.relative_to(ROOT))
        if rel in ALLOWED_WRITERS:
            continue
        code = _strip_code(p.read_text(errors="replace"), p.suffix)
        hits = [l.strip()[:100] for l in code.split("\n") if _WRITER_PAT.search(l)]
        if hits:
            offenders[rel] = hits[:2]
    assert not offenders, f"base_price written/computed outside the central rule (call base_price_rule.apply_and_report): {offenders}"


def test_allowlist_has_no_stale_entries_and_dead_tools_really_exit():
    for rel in ALLOWED_WRITERS:
        assert (ROOT / rel).exists(), f"stale allowlist entry {rel}"
    for rel in ("maintenance/margin_fix_batched.py", "maintenance/price_audit_fix.py"):
        assert "sys.exit(" in (ROOT / rel).read_text().split("import", 3)[-1][:4000] or "sys.exit(" in (ROOT / rel).read_text()[:3000]
    assert "raise RuntimeError" in (ROOT / "ai_catalog_builder.py").read_text()


NO_RULE_CALL = {
    "harvesters/kia_israel_harvester.py": "harvests to JSON and runs importers/import_kia_israel.py (which applies the rule); writes no DB",   # write NO base_price at all and do not call the rule: their price column is not a supplier cost (documented)
    "importers/import_parts_db.py": "spreadsheet price has no VAT basis/supplier: kept as specifications.source_price_ils",
    "importers/repair_manufacturer_import.py": "same: sheet price kept as provenance only",
    "scrapers/selected_parts_scraper.py": "a retailer's consumer price: retail reference only, no offer / no cost",
}


@pytest.mark.parametrize("rel", sorted(NO_RULE_CALL))
def test_reference_only_importers_never_write_base_price(rel):
    code = _strip_code((ROOT / rel).read_text(), ".py")
    assert not _WRITER_PAT.search(code), f"{rel} must not write base_price"


# ───────────── every parts_catalog / supplier_parts statement of a migrated importer prepares against the real schema ─────────────
# This is the check that catches a mis-renumbered $N: asyncpg raises on a parameter-count mismatch and most importers swallow the
# exception per row, so a wrong count would silently write nothing. For each execute/fetch/fetchrow/fetchval call whose SQL is a
# literal (or a module constant) and whose arguments are plain positionals, PREPARE the statement on the isolated DB and compare.
def _sql_calls(path: Path):
    tree = ast.parse(path.read_text())
    consts = {}
    for n in tree.body:
        if isinstance(n, ast.Assign) and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str):
            for t in n.targets:
                if isinstance(t, ast.Name):
                    consts[t.id] = n.value.value
    out = []
    for n in ast.walk(tree):
        if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in ("execute", "fetch", "fetchrow", "fetchval")):
            continue
        if not n.args:
            continue
        a0 = n.args[0]
        sql = a0.value if isinstance(a0, ast.Constant) and isinstance(a0.value, str) else consts.get(a0.id) if isinstance(a0, ast.Name) else None
        if not sql or "$" not in sql or not re.search(r"\b(parts_catalog|supplier_parts)\b", sql):
            continue
        if any(isinstance(x, ast.Starred) for x in n.args[1:]):
            continue
        out.append((n.lineno, sql, len(n.args) - 1))
    return out


@pytest.mark.parametrize("rel", MIGRATED + sorted(NO_RULE_CALL))
async def test_every_catalog_statement_of_a_migrated_importer_prepares_with_the_right_parameter_count(rel):
    calls = _sql_calls(ROOT / rel)
    if not calls:
        pytest.skip("no literal catalog SQL with positional args")
    c = await _connect()
    bad = []
    try:
        for lineno, sql, nargs in calls:
            try:
                st = await c.prepare(sql)
            except Exception as exc:        # a statement that cannot be prepared on its own (temp tables, CTE over a temp table...)
                if "does not exist" in str(exc) and ("_jag_prices" in sql):
                    continue
                bad.append((lineno, f"{type(exc).__name__}: {str(exc)[:90]}"))
                continue
            if len(st.get_parameters()) != nargs:
                bad.append((lineno, f"SQL takes {len(st.get_parameters())} parameters, call passes {nargs}"))
    finally:
        await c.close()
    assert not bad, f"{rel}: {bad}"


# ───────────────────────────── behaviour: price update / new part / missing cost / failure / idempotency ─────────────────────────────
def _expected_net(cost: float) -> float:
    import price_policy as pp
    return pp.unit_price(cost, supplier_country="il").net


async def _seed_priced_target(c, tag, mfr, oem):
    return await _seed_part(c, tag, mfr, oem=oem)


@pytest.mark.parametrize("modname,brand,price", [
    ("renault_pdf_import", "Renault", 236.0),
    ("renault_pdf_import", "Renault", 145.67),   # fractional cost: an int4-typed parameter would truncate it
    ("zeekr_pdf_import", "Zeekr", 100.0),
    ("fitz_mb_suzuki_import", "Mercedes-Benz", 118.0),
    ("fitz_mb_suzuki_import", "Mercedes-Benz", 123.45),
])
async def test_update_db_importers_set_cost_and_retail_and_let_the_rule_own_base_price(modname, brand, price):
    import importlib
    mod = importlib.import_module(modname)
    c = await _connect()
    tag = "BPU" + uuid.uuid4().hex[:6].upper()
    try:
        oem = f"{tag}X1"
        pid = await _seed_part(c, tag, brand, oem=oem)
        parts = [{"oem": oem, "price": price}]
        if modname == "renault_pdf_import":
            await mod.update_db(c, parts)
            cost = round(price / (1 + mod.VAT), 2)
        elif modname == "zeekr_pdf_import":
            await mod.update_db(c, parts, {oem: oem})
            cost = price
        else:
            await mod.update_db(c, brand, parts, "test importer")
            cost = round(price / (1 + mod.VAT), 2)
        row = await c.fetchrow("SELECT importer_price_ils, max_price_ils, base_price FROM parts_catalog WHERE id=$1::uuid", pid)
        assert float(row["importer_price_ils"]) == cost
        assert float(row["max_price_ils"]) > cost                                   # retail reference kept independently
        assert float(row["base_price"]) == _expected_net(cost) != float(row["max_price_ils"])
        # idempotency: the same list again changes nothing (no price drift, no duplicate rows)
        before = await c.fetchrow("SELECT base_price, importer_price_ils, max_price_ils FROM parts_catalog WHERE id=$1::uuid", pid)
        if modname == "zeekr_pdf_import":
            await mod.update_db(c, parts, {oem: oem})
        elif modname == "renault_pdf_import":
            await mod.update_db(c, parts)
        else:
            await mod.update_db(c, brand, parts, "test importer")
        assert dict(before) == dict(await c.fetchrow("SELECT base_price, importer_price_ils, max_price_ils FROM parts_catalog WHERE id=$1::uuid", pid))
        assert await c.fetchval("SELECT count(*) FROM parts_catalog WHERE oem_number=$1", oem) == 1
        # a changed supplier cost recalculates base_price on the existing part
        parts2 = [{"oem": oem, "price": price * 2}]
        if modname == "zeekr_pdf_import":
            await mod.update_db(c, parts2, {oem: oem}); cost2 = price * 2
        elif modname == "renault_pdf_import":
            await mod.update_db(c, parts2); cost2 = round(price * 2 / (1 + mod.VAT), 2)
        else:
            await mod.update_db(c, brand, parts2, "test importer"); cost2 = round(price * 2 / (1 + mod.VAT), 2)
        assert float(await c.fetchval("SELECT base_price FROM parts_catalog WHERE id=$1::uuid", pid)) == _expected_net(cost2)
    finally:
        await _cleanup(c, tag)
        await c.close()


async def test_unverified_vat_basis_importers_leave_base_price_unset_and_say_so(capsys, monkeypatch):
    """Cadillac / GMC-Buick: the price list's VAT basis is unestablished -> NO cost, NO base_price, and the report counts the parts as
    unpriced (visible), never a guess. With a stated basis the same rows are priced through the rule."""
    import cadillac_israel_import as cad
    c = await _connect()
    tag = "BPG" + uuid.uuid4().hex[:6].upper()
    try:
        cad.CADILLAC_BRAND_ID = await _brand(c, "Cadillac")
        rows = [("imp", f"{tag}-A1", "מסנן שמן", "מקורי", "", 118.0, "", "CT5")]
        r = await cad.import_parts(c, rows)
        assert r["inserted"] == 1
        out = capsys.readouterr().out
        assert "unpriced_no_cost_evidence=1" in out, out
        row = await c.fetchrow("SELECT importer_price_ils, base_price FROM parts_catalog WHERE oem_number=$1", f"{tag}-A1")
        assert not row["importer_price_ils"] and float(row["base_price"] or 0) == 0.0
        monkeypatch.setattr(cad, "GM_PRICE_BASIS", "inclusive")
        rows2 = [("imp", f"{tag}-A2", "מסנן שמן", "מקורי", "", 118.0, "", "CT5")]
        await cad.import_parts(c, rows2)
        row2 = await c.fetchrow("SELECT importer_price_ils, max_price_ils, base_price FROM parts_catalog WHERE oem_number=$1", f"{tag}-A2")
        assert float(row2["importer_price_ils"]) == 100.0 and float(row2["max_price_ils"]) == 118.0
        assert float(row2["base_price"]) == _expected_net(100.0)
    finally:
        await _cleanup(c, tag)
        await c.close()


async def test_rule_failure_is_loud_queued_for_retry_and_never_reported_as_success(tmp_path, monkeypatch, capsys):
    import base_price_rule as bpr
    monkeypatch.setattr(bpr, "RETRY_FILE", str(tmp_path / "retry.jsonl"))
    c = await _connect()
    tag = "BPF" + uuid.uuid4().hex[:6].upper()
    try:
        pid = await _seed_part(c, tag, "Kia", oem=f"{tag}R1")
        await c.execute("UPDATE parts_catalog SET importer_price_ils=100 WHERE id=$1::uuid", pid)
        real = bpr.apply_to_parts

        async def boom(conn, ids, usd=None, chunk=2000):
            raise RuntimeError("db hiccup")
        monkeypatch.setattr(bpr, "apply_to_parts", boom)
        res = await bpr.apply_and_report(c, [pid], "test_importer")
        assert "error" in res and res["updated"] == 0 and res["reasons"] == {"rule_failed": 1}
        assert "ERROR [base_price] test_importer: rule FAILED for 1 parts" in capsys.readouterr().err
        assert pid in (tmp_path / "retry.jsonl").read_text()
        assert float(await c.fetchval("SELECT base_price FROM parts_catalog WHERE id=$1::uuid", pid)) == 0.0   # visible: still unpriced
        monkeypatch.setattr(bpr, "apply_to_parts", real)                         # recovered: ANY later call drains the retry list
        res2 = await bpr.apply_and_report(c, [], "some_other_importer")
        assert res2["retried"] == 1 and res2["updated"] == 1
        assert float(await c.fetchval("SELECT base_price FROM parts_catalog WHERE id=$1::uuid", pid)) == _expected_net(100.0)
        assert (tmp_path / "retry.jsonl").read_text() == ""            # drained under a lock (emptied, not deleted)
    finally:
        await _cleanup(c, tag)
        await c.close()


async def test_file_driven_il_importers_bmw_direct_and_jaguar_ndjson(tmp_path):
    import subprocess
    c = await _connect()
    tag = "BPJ" + uuid.uuid4().hex[:6].upper()
    try:
        await _supplier(c, "BMW Israel", basis="exclusive")
        await _brand(c, "BMW"); await _brand(c, "Jaguar")
        f1 = tmp_path / "bmw.json"
        f1.write_text(json.dumps([{"oem_number": f"{tag}B1", "name_he": "פריט", "price_ils": 200.0, "model": "X5"}]))
        r = subprocess.run([sys.executable, str(ROOT / "importers" / "bmw_il_direct_import.py"), str(f1)], capture_output=True, text=True,
                           env={**os.environ, "DATABASE_URL": _dsn()}, timeout=120)
        assert r.returncode == 0 and "Done in" in r.stdout, r.stdout[-300:] + r.stderr[-300:]
        pid = await c.fetchval("SELECT id::text FROM parts_catalog WHERE oem_number=$1", f"{tag}B1")
        assert pid
        assert float(await c.fetchval("SELECT max_price_ils FROM parts_catalog WHERE id=$1::uuid", pid)) == round(200 * 1.18, 2)
        assert float(await c.fetchval("SELECT base_price FROM parts_catalog WHERE id=$1::uuid", pid)) == _expected_net(200.0)
        f2 = tmp_path / "jag.ndjson"
        f2.write_text(json.dumps({"base_part_number": f"{tag}J1", "part_number": f"{tag}J1", "title": "Brake pad", "price_gbp": 10.0,
                                  "part_origin": "aftermarket", "applications": []}) + "\n")
        r = subprocess.run([sys.executable, str(ROOT / "importers" / "jaguar_ndjson_import.py"), str(f2)], capture_output=True, text=True,
                           env={**os.environ, "DATABASE_URL": _dsn()}, timeout=120)
        assert r.returncode == 0 and "Done in" in r.stdout, r.stdout[-300:] + r.stderr[-300:]
        row = await c.fetchrow("SELECT importer_price_ils, base_price FROM parts_catalog WHERE oem_number=$1", f"{tag}J1")
        assert float(row["importer_price_ils"]) > 0 and float(row["base_price"]) == _expected_net(float(row["importer_price_ils"]))
    finally:
        await _cleanup(c, tag)
        await c.close()


# ───────────── review findings (Opus, 2026-11-18): cost truncation, registry fail-closed, retry-queue hardening ─────────────
_CASE_NUM = re.compile(r"CASE WHEN \$(\d+)\s*>\s*0\s+THEN \$\1\b(?!::)")


@pytest.mark.parametrize("rel", MIGRATED)
def test_no_untyped_case_when_parameter_that_postgres_would_type_as_int4(rel):
    """`CASE WHEN $1 > 0 THEN $1` makes Postgres type $1 as int4 and asyncpg then STORES 123.75 as 123.00 without an error."""
    code = _strip_code((ROOT / rel).read_text(), ".py")
    assert not _CASE_NUM.search(code), f"{rel}: cast the parameter ($N::numeric) in `CASE WHEN $N > 0 THEN $N`"


@pytest.mark.parametrize("rel", MIGRATED + sorted(NO_RULE_CALL))
async def test_price_parameters_of_catalog_statements_are_numeric_not_int4(rel):
    c = await _connect()
    bad = []
    try:
        for lineno, sql, nargs in _sql_calls(ROOT / rel):
            try:
                st = await c.prepare(sql)
            except Exception:
                continue
            cols = re.findall(r"(importer_price_ils|max_price_ils|min_price_ils|base_price|online_price_ils)\s*=\s*(?:CASE WHEN\s+)?\$(\d+)", sql)
            types = [a.name for a in st.get_parameters()]
            for col, n in cols:
                if types[int(n) - 1] in ("int4", "int8", "int2"):
                    bad.append((lineno, col, f"${n} is {types[int(n) - 1]}"))
    finally:
        await c.close()
    assert not bad, f"{rel}: price column bound to an integer-typed parameter: {bad}"


async def test_supplier_pdf_price_update_keeps_the_fractional_cost():
    import supplier_pdf_import as sp
    c = await _connect()
    tag = "BPS" + uuid.uuid4().hex[:6].upper()
    try:
        pid = await _seed_part(c, tag, "Porsche", oem=f"{tag}P1")
        await c.execute("UPDATE parts_catalog SET max_price_ils=1 WHERE id=$1::uuid", pid)
        src = (ROOT / "importers/supplier_pdf_import.py").read_text()
        m = re.search(r'executemany\(\s*"""(UPDATE parts_catalog\s+SET importer_price_ils = CASE.*?)""",\s*to_uprice', src, re.S)
        assert m, "to_uprice statement not found"
        await c.executemany(m.group(1), [(123.75, 146.0, pid)])
        got = await c.fetchrow("SELECT importer_price_ils, max_price_ils FROM parts_catalog WHERE id=$1::uuid", pid)
        assert float(got["importer_price_ils"]) == 123.75 and float(got["max_price_ils"]) == 146.0
    finally:
        await _cleanup(c, tag)
        await c.close()


async def test_rule_fails_closed_when_the_vat_registry_cannot_be_loaded(tmp_path, monkeypatch, capsys):
    import base_price_rule as bpr
    monkeypatch.setattr(bpr, "RETRY_FILE", str(tmp_path / "retry.jsonl"))
    c = await _connect()
    tag = "BPV" + uuid.uuid4().hex[:6].upper()
    try:
        pid = await _seed_part(c, tag, "Kia", oem=f"{tag}V1")
        await c.execute("UPDATE parts_catalog SET importer_price_ils=100 WHERE id=$1::uuid", pid)

        async def no_registry(conn=None, db=None):
            return False
        monkeypatch.setattr(bpr, "ensure_vat_registry", no_registry)
        res = await bpr.apply_and_report(c, [pid], "test_importer")
        assert res["error"] == "vat_registry_unavailable" and res["updated"] == 0
        assert "NOT priced" in capsys.readouterr().err
        assert float(await c.fetchval("SELECT base_price FROM parts_catalog WHERE id=$1::uuid", pid)) == 0.0    # nothing written
        assert pid in (tmp_path / "retry.jsonl").read_text()
    finally:
        await _cleanup(c, tag)
        await c.close()


def test_retry_queue_skips_a_malformed_line_and_rejects_non_uuid_ids(tmp_path, monkeypatch, caplog):
    import base_price_rule as bpr
    f = tmp_path / "retry.jsonl"
    monkeypatch.setattr(bpr, "RETRY_FILE", str(f))
    good = str(uuid.uuid4())
    f.write_text('{"importer": "x", "ids": ["%s", "not-a-uuid"]}\nTHIS IS NOT JSON\n' % good)
    with caplog.at_level("ERROR"):
        assert bpr._retry_take() == [good]
    assert "malformed line" in caplog.text and "non-UUID" in caplog.text
    assert f.read_text() == ""
    assert bpr._retry_take() == []
