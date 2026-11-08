"""Tests for variant_identity (ONE variant vocabulary, index-identical key) and every writer that must use it.

Layers
  * PURE: vocabulary, Hebrew/English equivalence, fail-closed errors, idempotence, index-identity against the migration SQL,
    "no second dictionary" and "every supplier_parts.part_type writer uses the module" (AST call-path audit).
  * DB (disposable Postgres only): needs VI_PG_DSN pointing at a database whose NAME contains "sandbox_" and that carries the
    PRODUCTION schema + the CURRENT production indexes (old (part_id, supplier_id) index absent, the two partial unique
    indexes present). Refuses anything else. Skipped (never silently passing) when the DSN is absent.
Mandatory cases covered (numbers refer to the 2026-11-07 mission list):
  1 מקורי==original  2 חליפי==aftermarket  3 equivalent labels cannot make 2 offers  4 distinct variants stay distinct
  5 different suppliers stay separate  6 same-variant conflict is safe  7 price change = exactly one price_history
  8 no-op idempotent  9 unknown fails safe  10 original label + provenance retained  11 normalisation before writes
  12 every write path uses the same implementation."""
import ast
import asyncio
import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

import variant_identity as vi
from variant_identity import (AmbiguousVariantLabel, UnknownVariantLabel, canonical_part_type, index_key, resolve, variant_key)

ROOT = Path(__file__).resolve().parent.parent
SQL_FILE = Path(os.getenv("AG4_MIGRATION_SQL") or ROOT.parent / "candidates" / "offer-policy-2026-11-06" / "migration_variant_identity.sql")
DSN = os.getenv("VI_PG_DSN", "")
db_only = pytest.mark.skipif(not DSN or "sandbox_" not in DSN.rsplit("/", 1)[-1], reason="VI_PG_DSN (disposable sandbox_ database) not set")


# ===================================================================== PURE: vocabulary
def test_1_hebrew_original_equals_original():
    for lab in ("מקורי", "מָקוֹרִי", " מקורי ", "ירוקמ", "מקורימקורי", "Original", "ORIGINAL", "genuine", "Genuine Part", "oem_original"):
        assert canonical_part_type(lab) == "original", lab
        assert variant_key(lab) == "oem", lab


def test_2_hebrew_aftermarket_equals_aftermarket():
    for lab in ("חליפי", "תחליפי", "חליפיחליפי", "תחליפיתחליפי", "יפילח", "שוק משני", "Aftermarket", "after-market", "AFTERMARKET", "analog"):
        assert canonical_part_type(lab) == "aftermarket", lab
        assert variant_key(lab) == "aftermarket", lab


def test_oem_and_original_are_two_spellings_one_identity():
    assert canonical_part_type("oem") == "oem" and canonical_part_type("OE") == "oem"
    assert canonical_part_type("original") == "original"
    assert variant_key("oem") == variant_key("original") == variant_key("genuine") == variant_key("OE") == variant_key("מקורי") == "oem"


def test_4_distinct_variants_stay_distinct():
    keys = [variant_key(x) for x in ("oem", "oe_equivalent", "aftermarket", "remanufactured", "used")]
    assert len(set(keys)) == 5, keys
    assert variant_key("OEM equivalent") == variant_key("oe-equivalent") == variant_key("שווה ערך ל-OEM") == "oe_equivalent"
    assert variant_key("משופץ") == variant_key("refurbished") == "remanufactured"
    assert variant_key("oem") != variant_key("oe_equivalent")            # the 2026-07-28 map conflated these


def test_replacement_compatible_new_are_never_silently_merged():
    for lab in ("replacement", "compatible", "new", "other"):
        with pytest.raises(AmbiguousVariantLabel):
            canonical_part_type(lab)
        assert resolve(lab).kind == "ambiguous"


def test_9_unknown_fails_safe_and_missing_is_distinct():
    with pytest.raises(UnknownVariantLabel):
        canonical_part_type("some new supplier word")
    assert resolve("some new supplier word").kind == "unknown"
    with pytest.raises(AmbiguousVariantLabel):
        canonical_part_type("מקורי / חליפי")
    assert resolve("oem/aftermarket").kind == "ambiguous"
    assert resolve("original / genuine").ok and resolve("original / genuine").stored == "original"
    for missing in (None, "", "   ", "unknown", "None", "NULL", "סוג מוצר", "n/a", "-"):
        assert resolve(missing).kind == "missing"
        assert canonical_part_type(missing) is None
        assert variant_key(missing) is None
    assert vi.try_canonical("zzz") is None and vi.try_canonical("מקורי") == "original"
    assert vi.lenient_class("zzz") == "zzz" and vi.lenient_class(None) is None      # comparison helper never raises/merges


def test_8_idempotent_and_deterministic():
    labels = list(vi.synonym_table()) + list(vi.noncanonical_literals("class")) + list(vi.noncanonical_literals("stored"))
    for lab in labels:
        r = resolve(lab)
        assert r.ok, lab
        again = resolve(r.stored)
        assert again.ok and again.stored == r.stored and again.key == r.key, lab        # canonical(canonical(x)) == canonical(x)
        assert resolve(lab) == r                                                       # deterministic
    for stored in vi.STORED_VALUES:
        assert canonical_part_type(stored) == stored


def test_10_source_label_and_reason_are_kept():
    r = resolve("  מקורי ")
    assert r.source_label == "  מקורי " and r.stored == "original"                      # provenance travels with the result
    e = None
    try:
        canonical_part_type("weird!")
    except UnknownVariantLabel as exc:
        e = exc
    assert e is not None and e.label == "weird!" and "unrecognised" in e.reason


def test_resolve_is_total():
    for weird in (0, 1.5, b"x", ["a"], object(), "‏‎מקורי‏"):
        resolve(weird)                                                                   # never raises
    assert resolve("‏‎מקורי‏").stored == "original"                       # bidi marks are stripped


def test_noncanonical_literals_are_exactly_the_changes():
    for target in ("class", "stored"):
        for old, new in vi.noncanonical_literals(target).items():
            assert old != new
            assert resolve(old).ok and resolve(new).ok
    assert vi.noncanonical_literals("class")["Original"] == "oem"                         # parts_catalog convention
    assert vi.noncanonical_literals("stored")["Original"] == "original"                   # supplier_parts convention
    assert vi.noncanonical_literals("stored")["מקורי"] == "original"


# ===================================================================== PURE: index identity
def _sql_index_group() -> tuple:
    sql = SQL_FILE.read_text()
    m = re.search(r"lower\(part_type\)\s+IN\s*\(([^)]*)\)\s*THEN\s*'oem'\s*ELSE\s*lower\(part_type\)", sql, re.I)
    assert m, "variant index expression not found in the migration SQL"
    return tuple(re.findall(r"'([^']+)'", m.group(1)))


def test_index_expression_in_code_equals_migration_sql():
    assert _sql_index_group() == vi.INDEX_OEM_GROUP
    norm = lambda t: re.sub(r"\s+", " ", t).strip().lower()
    sql = SQL_FILE.read_text()
    case = re.search(r"\(CASE WHEN lower\(part_type\) IN \([^)]*\) THEN 'oem' ELSE lower\(part_type\) END\)", sql)
    assert case and norm(case.group(0)) == norm(vi.INDEX_KEY_SQL("part_type"))


def test_variant_key_equals_index_key_of_the_stored_value_for_every_label():
    for lab in list(vi.synonym_table()) + list(vi.noncanonical_literals("stored")):
        r = resolve(lab)
        assert r.key == index_key(r.stored) == variant_key(lab)
    assert index_key("OEM") == "oem" and index_key("Original") == "oem" and index_key("Aftermarket") == "aftermarket"
    assert index_key("מקורי") == "מקורי"            # the INDEX cannot fold Hebrew: only canonical stored values are safe


# ===================================================================== PURE: call-path audit
def _py_files():
    for p in ROOT.rglob("*.py"):
        parts = set(p.relative_to(ROOT).parts)
        if parts & {"tests", "devtests", "legacy", "alembic", "alembic_pii", "__pycache__", "candidates"}:
            continue
        yield p


def _writer_files():
    """Files with an INSERT/UPDATE of supplier_parts whose SQL text names part_type (AST over string constants and f-strings)."""
    out = {}
    for p in _py_files():
        try:
            tree = ast.parse(p.read_text(errors="ignore"))
        except SyntaxError:
            continue
        doc_ids = {id(b.value) for b in ast.walk(tree) if isinstance(b, ast.Expr) and isinstance(b.value, ast.Constant)}
        for n in ast.walk(tree):
            s = None
            if id(n) in doc_ids:
                continue                                    # docstrings/comments mention SQL; they are not writers
            if isinstance(n, ast.Constant) and isinstance(n.value, str):
                s = n.value
            elif isinstance(n, ast.JoinedStr):
                s = "".join(v.value if isinstance(v, ast.Constant) else "{}" for v in n.values)
            if s and re.search(r"(INSERT\s+INTO|UPDATE)\s+(public\.)?supplier_parts\b", s, re.I) and "part_type" in s:
                out.setdefault(p.relative_to(ROOT).as_posix(), []).append(n.lineno)
    return out


EXPECTED_WRITERS = {
    "importers/amayama_price_import.py", "importers/rockauto_price_import.py", "importers/sng_barratt_jaguar_import.py",
    "services/aliexpress_price_sync.py", "maintenance/restore_merged_offers_133.py",
}


def test_12_every_supplier_parts_part_type_writer_is_known_and_uses_the_module():
    found = set(_writer_files())
    # a NEW writer must be added here consciously (and wired to variant_identity), never discovered by accident
    assert found <= EXPECTED_WRITERS | {"importers/import_from_excel.py"}, sorted(found - EXPECTED_WRITERS)
    missing = []
    for rel in sorted(found):
        src = (ROOT / rel).read_text()
        if "variant_identity" not in src and "canonical_variant" not in src:
            missing.append(rel)
    pending_other_agent = {"services/aliexpress_price_sync.py"}      # edit deferred to the agent that owns the live file; see report
    assert [m for m in missing if m not in pending_other_agent] == [], missing


def test_12b_dynamic_and_orm_writers_use_the_module():
    for rel in ("importers/import_from_excel.py", "db_update_agent.py", "db_cleanup_agent.py", "routes/parts.py", "offer_classification.py"):
        assert "variant_identity" in (ROOT / rel).read_text(), rel
    src = (ROOT / "importers/import_from_excel.py").read_text()
    assert "else \"OEM\"" not in src, "the guessed OEM default must be gone"


_LABELS = {"מקורי", "חליפי", "genuine", "aftermarket", "oem_equivalent", "oe_equivalent", "תחליפי", "משופץ", "refurbished", "remanufactured"}
_STRONG = {"מקורי", "חליפי", "genuine", "oem_equivalent", "תחליפי", "משופץ", "refurbished"}
# Reviewed non-vocabularies (each is NOT a label -> variant map): quality-tier / display / UI-bucket / free-text-scoring tables.
_DICT_ALLOW = {
    "offer_classification.py": "class -> Hebrew display label (class_label_he)",
    "ai_catalog_builder.py": "label -> aftermarket_tier (catalog quality tier, a different column)",
    "BACKEND_AI_AGENTS.py": "class -> emoji tier text for chat",
    "routes/parts.py": "response bucket keys (original/oem/aftermarket), not labels",
    "routes/vehicles.py": "response bucket keys",
    "category_learning.py": "stop-word list for category mining",
    "catalog_scraper.py": "free-text brand/keyword scoring of scraped titles (classify_part_type)",
    "scrapers/sng_barratt_jaguar_scraper.py": "SNG free-text type-name substrings (source-specific rule)",
    "services/suppliers/aliexpress_supplier.py": "title keyword scoring for marketplace listings",
    "maintenance/restore_merged_offers_133.py": "fallback KNOWN_VARIANTS when variant_identity is absent (dead once deployed)",
    "importers/sng_barratt_jaguar_import.py": "SNG substring rule; result is validated by canonical_part_type",
}


def test_no_second_label_dictionary():
    offenders = []
    for p in _py_files():
        rel = p.relative_to(ROOT).as_posix()
        if rel == "variant_identity.py" or rel in _DICT_ALLOW:
            continue
        try:
            tree = ast.parse(p.read_text(errors="ignore"))
        except SyntaxError:
            continue
        for n in ast.walk(tree):
            if isinstance(n, ast.Dict):
                ks = {k.value for k in n.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)}
                if len(ks & _LABELS) >= 2 and ks & _STRONG:
                    offenders.append((rel, n.lineno, "dict"))
            elif isinstance(n, (ast.Set, ast.List, ast.Tuple)):
                vs = {k.value for k in n.elts if isinstance(k, ast.Constant) and isinstance(k.value, str)}
                if len(vs & (_LABELS | {"original", "oem"})) >= 3 and vs & _STRONG:
                    offenders.append((rel, n.lineno, "seq"))
    assert offenders == [], offenders


# ===================================================================== DB layer
async def _engine():
    from sqlalchemy.ext.asyncio import create_async_engine
    return create_async_engine(DSN.replace("postgresql://", "postgresql+asyncpg://"))


def _run(coro):
    return asyncio.run(coro)


class W:
    """Fresh brand/part/suppliers per test, removed afterwards (the normalizer commits, so no rollback trick here)."""

    def __init__(self, c):
        self.c = c
        self.tag = uuid.uuid4().hex[:8]
        self.parts, self.sups = [], []

    async def part(self):
        brand = await self.c.fetchval("INSERT INTO car_brands (name) VALUES ($1) RETURNING id", f"b{self.tag}{len(self.parts)}")
        pid = uuid.uuid4()
        await self.c.execute("INSERT INTO parts_catalog (id, sku, name, manufacturer_id, oem_number) VALUES ($1,$2,'n',$3,$4)",
                             pid, f"P{self.tag}{len(self.parts)}", brand, f"OEM{self.tag.upper()}{len(self.parts)}")
        self.parts.append(pid)
        return pid

    async def sup(self, name=None):
        sid = uuid.uuid4()
        await self.c.execute("INSERT INTO suppliers (id, name) VALUES ($1,$2)", sid, name or f"s{self.tag}{len(self.sups)}")
        self.sups.append(sid)
        return sid

    async def offer(self, part, sup, sku, ptype, price=100):
        rid = uuid.uuid4()
        await self.c.execute("INSERT INTO supplier_parts (id, supplier_id, part_id, supplier_sku, price_usd, price_ils, part_type, is_available) "
                             "VALUES ($1,$2,$3,$4,1,$5,$6,true)", rid, sup, part, sku, price, ptype)
        return rid

    async def cleanup(self):
        await self.c.execute("DELETE FROM price_history WHERE supplier_part_id IN (SELECT id FROM supplier_parts WHERE supplier_id = ANY($1::uuid[]))", self.sups)
        await self.c.execute("DELETE FROM supplier_parts WHERE supplier_id = ANY($1::uuid[]) OR part_id = ANY($2::uuid[])", self.sups, self.parts)
        await self.c.execute("DELETE FROM parts_catalog WHERE id = ANY($1::uuid[])", self.parts)
        await self.c.execute("DELETE FROM suppliers WHERE id = ANY($1::uuid[])", self.sups)
        await self.c.execute("DELETE FROM car_brands WHERE name LIKE $1", f"b{self.tag}%")


def with_world(fn):
    import functools
    import inspect

    @functools.wraps(fn)
    def wrapper(*a, **k):
        import asyncpg

        async def main():
            c = await asyncpg.connect(DSN)
            w = W(c)
            try:
                await fn(c, w, *a, **k)
            finally:
                await w.cleanup()
                await c.close()
        _run(main())
    sig = inspect.signature(fn)
    wrapper.__signature__ = sig.replace(parameters=list(sig.parameters.values())[2:])    # pytest sees only the real fixtures
    return wrapper


@db_only
@with_world
async def test_db_index_definition_is_what_the_code_assumes(c, w):
    d = await c.fetchval("SELECT pg_get_indexdef('uq_sp_part_supplier_variant'::regclass)")
    assert tuple(re.findall(r"'([a-z]+)'::text", d.split("ARRAY")[1].split(")")[0])) == vi.INDEX_OEM_GROUP, d
    assert await c.fetchval("SELECT count(*) FROM pg_class WHERE relname='uq_supplier_parts_part_supplier'") == 0
    assert await c.fetchval("SELECT bool_and(indisvalid AND indisready) FROM pg_index WHERE indexrelid::regclass::text IN ('uq_sp_part_supplier_variant','uq_sp_part_supplier_untyped')")
    # SQL expression == python twin for every spelling that could ever be stored (case variants included)
    samples = list(vi.STORED_VALUES) + ["OEM", "Original", "GENUINE", "Oe", "Aftermarket", "AFTERMARKET", "מקורי", "x", "oe_equivalent", "OE_Equivalent"]
    for s_ in samples:
        got = await c.fetchval(f"SELECT {vi.INDEX_KEY_SQL('$1::varchar')}", s_)
        assert got == index_key(s_), (s_, got, index_key(s_))


@db_only
@with_world
async def test_3_4_5_6_index_semantics_with_canonical_values(c, w):
    import asyncpg
    p, p2 = await w.part(), await w.part()
    s1, s2 = await w.sup(), await w.sup()
    a = await w.offer(p, s1, "A", canonical_part_type("מקורי"))          # original
    # 3: every equivalent label, once canonicalised, hits the same index key -> a second offer is impossible
    for k, lab in enumerate(("genuine", "oem", "OE", "Original", "מקורי")):
        with pytest.raises(asyncpg.UniqueViolationError):
            await w.offer(p, s1, f"B{k}", canonical_part_type(lab) if canonical_part_type(lab) else None)
    # 4: distinct variants coexist for ONE supplier+part
    for k, lab in enumerate(("חליפי", "oe equivalent", "refurbished", "used")):
        await w.offer(p, s1, f"V{k}", canonical_part_type(lab))
    assert await c.fetchval("SELECT count(*) FROM supplier_parts WHERE supplier_id=$1 AND part_id=$2", s1, p) == 5
    # 5: other supplier, same variant -> its own row
    await w.offer(p, s2, "A", canonical_part_type("מקורי"))
    # 6: same-variant conflict is a loud unique violation: the stored offer is untouched
    before = await c.fetchrow("SELECT price_ils, part_type FROM supplier_parts WHERE id=$1", a)
    with pytest.raises(asyncpg.UniqueViolationError):
        await w.offer(p, s1, "A2", canonical_part_type("genuine"), price=1)
    assert dict(await c.fetchrow("SELECT price_ils, part_type FROM supplier_parts WHERE id=$1", a)) == dict(before)
    # untyped is its own identity: one NULL per supplier+part
    await w.offer(p2, s1, "N1", None)
    with pytest.raises(asyncpg.UniqueViolationError):
        await w.offer(p2, s1, "N2", None)
    # WHY the writers must canonicalise: the index itself cannot fold Hebrew (documented, not a defect of the writers)
    await w.offer(p2, s1, "H1", "מקורי")
    await w.offer(p2, s1, "H2", "original")                                 # accepted: different index keys -> the 2026-11-06 hazard
    assert await c.fetchval("SELECT count(*) FROM supplier_parts WHERE supplier_id=$1 AND part_id=$2 AND part_type IN ('מקורי','original')", s1, p2) == 2


async def _normalize(dsn_engine_factory):
    from sqlalchemy.ext.asyncio import AsyncSession
    import db_update_agent as dua
    eng = await dsn_engine_factory()
    async with AsyncSession(eng) as db:
        res = await dua.normalize_part_types(db)
    await eng.dispose()
    return res


@db_only
@with_world
async def test_normalize_part_types_is_collision_safe_idempotent_and_reports(c, w, tmp_path, monkeypatch):
    import db_update_agent as dua
    manifest = tmp_path / "manifest.jsonl"
    monkeypatch.setattr(dua, "_PT_MANIFEST_PATH", manifest)
    p, p2, p3 = await w.part(), await w.part(), await w.part()
    s1, s2 = await w.sup(), await w.sup()
    blocker = await w.offer(p, s1, "K1", "original")                       # key 'oem'
    victim = await w.offer(p, s1, "K2", "מקורי")                           # would become 'original' -> collision with K1
    movable = await w.offer(p2, s2, "M1", "מקורי")                         # no collision -> must still be fixed
    am = await w.offer(p3, s1, "M2", "Aftermarket")                       # case-only fix
    junk = await w.offer(p3, s2, "J1", "totally-new-label")                # unknown: untouched, reported
    ph = await w.offer(p2, s1, "J2", "unknown")                            # placeholder: untouched, reported
    r1 = await _normalize(_engine)
    assert r1["status"] == "ok", r1                                        # a legitimate collision must NOT fail the task
    assert r1["skipped_collision"] >= 1 and r1["errors"] == 0, r1
    got = {str(r["id"]): r["part_type"] for r in await c.fetch("SELECT id, part_type FROM supplier_parts WHERE id = ANY($1::uuid[])", [blocker, victim, movable, am, junk, ph])}
    assert got[str(victim)] == "מקורי"                                      # skipped, not rewritten, not deleted
    assert got[str(blocker)] == "original" and got[str(movable)] == "original" and got[str(am)] == "aftermarket"
    assert got[str(junk)] == "totally-new-label" and got[str(ph)] == "unknown"
    col = [x for x in r1["collisions"] if x["id"] == str(victim)]
    assert col and col[0]["blocked_by"] == str(blocker) and col[0]["to"] == "original" and col[0]["key"] == "oem"
    assert "totally-new-label" in r1["unmapped"]["supplier_parts"] and "unknown" in r1["placeholders_left"]["supplier_parts"]
    rows = [json.loads(x) for x in manifest.read_text().splitlines()]
    mine = {x["id"]: x for x in rows if x["table"] == "supplier_parts"}
    assert mine[str(movable)]["before"] == "מקורי" and mine[str(movable)]["after"] == "original"      # provenance of the source label
    assert mine[str(am)]["before"] == "Aftermarket"
    # second run: no-op for rewrites, the collision is surfaced again, nothing breaks
    r2 = await _normalize(_engine)
    assert r2["status"] == "ok" and r2["supplier_updated"] == 0, r2
    assert r2["skipped_collision"] >= 1
    assert {x["id"] for x in r2["collisions"]} >= {str(victim)}


@db_only
@with_world
async def test_normalize_part_types_survives_a_concurrent_collision_race(c, w, tmp_path, monkeypatch):
    """The batch UPDATE fails on a unique violation (a writer inserted the blocker after the check): row-by-row fallback skips it."""
    import db_update_agent as dua
    from sqlalchemy.ext.asyncio import AsyncSession
    monkeypatch.setattr(dua, "_PT_MANIFEST_PATH", tmp_path / "m.jsonl")
    p = await w.part()
    s1 = await w.sup()
    await w.offer(p, s1, "R1", "original")
    v = await w.offer(p, s1, "R2", "מקורי")
    eng = await _engine()
    real = dua._VARIANT_KEY_SQL
    monkeypatch.setattr(dua, "_VARIANT_KEY_SQL", lambda col: "(CASE WHEN false THEN 'x' ELSE 'never-matches' END)")   # blind the pre-check
    async with AsyncSession(eng) as db:
        res = await dua._normalize_part_type_table(db, "supplier_parts")
    await eng.dispose()
    assert res["errors"] == 0 and res["skipped_collision"] >= 1 and res["changed"] >= 0
    assert await c.fetchval("SELECT part_type FROM supplier_parts WHERE id=$1", v) == "מקורי"


@db_only
@with_world
async def test_write_paths_normalise_before_the_db_and_changed_prices_make_exactly_one_history_row(c, w, tmp_path):
    """Runs the REAL rockauto and amayama importers as subprocesses against the sandbox DB."""
    p_ra, p_am = await w.part(), await w.part()
    oem_ra = await c.fetchval("SELECT oem_number FROM parts_catalog WHERE id=$1", p_ra)
    oem_am = await c.fetchval("SELECT oem_number FROM parts_catalog WHERE id=$1", p_am)
    await c.execute("UPDATE parts_catalog SET is_active=true WHERE id = ANY($1::uuid[])", [p_ra, p_am])
    for nm in ("RockAuto", "Amayama"):          # importers look suppliers up by name; keep ours distinct from any seeded rows
        await c.execute("DELETE FROM suppliers WHERE name=$1 AND id NOT IN (SELECT supplier_id FROM supplier_parts)", nm)
    env = {**os.environ, "DATABASE_URL": DSN, "PYTHONPATH": str(ROOT), "PYTHONDONTWRITEBYTECODE": "1"}

    def run(script, rows):
        f = tmp_path / f"{script}.json"
        f.write_text(json.dumps(rows, ensure_ascii=False))
        out = subprocess.run([sys.executable, str(ROOT / "importers" / script), str(f)], env=env, capture_output=True, text=True, timeout=120)
        assert out.returncode == 0, out.stdout + out.stderr
        return out.stdout

    async def offers(sku):
        return await c.fetch("SELECT id, part_type, price_ils FROM supplier_parts WHERE supplier_sku=$1", sku)

    # --- RockAuto: stored variant comes from the module
    norm_ra = re.sub(r"[^0-9A-Z]", "", oem_ra.upper())
    run("rockauto_price_import.py", [{"oem": oem_ra, "brand": "B", "partnum": "1", "name": "n", "price_usd": 10}])
    o1 = await offers(f"RA-{norm_ra}")
    assert len(o1) == 1 and o1[0]["part_type"] == "aftermarket"
    hist0 = await c.fetchval("SELECT count(*) FROM price_history WHERE supplier_part_id=$1", o1[0]["id"])
    run("rockauto_price_import.py", [{"oem": oem_ra, "brand": "B", "partnum": "1", "name": "n", "price_usd": 10}])      # no-op
    assert await c.fetchval("SELECT count(*) FROM price_history WHERE supplier_part_id=$1", o1[0]["id"]) == hist0
    run("rockauto_price_import.py", [{"oem": oem_ra, "brand": "B", "partnum": "1", "name": "n", "price_usd": 12}])      # price change
    o2 = await offers(f"RA-{norm_ra}")
    assert len(o2) == 1 and o2[0]["id"] == o1[0]["id"] and o2[0]["part_type"] == "aftermarket"
    assert await c.fetchval("SELECT count(*) FROM price_history WHERE supplier_part_id=$1", o1[0]["id"]) == hist0 + 1   # exactly one row
    # --- Amayama: stored variant from the option's OWN label via the module; no label keeps the historical source default
    def amy(oem, **extra):
        run("amayama_price_import.py", [{"oem": oem, "brand": "X", "part_num": "1", "name": "n", "price_usd": 9.5, **extra}])
        return offers("AMY-" + re.sub(r"[^0-9A-Z]", "", oem.upper()))

    async def newpart():
        pid = await w.part()
        oem = await c.fetchval("SELECT oem_number FROM parts_catalog WHERE id=$1", pid)
        await c.execute("UPDATE parts_catalog SET is_active=true WHERE id=$1", pid)
        return oem

    assert [r["part_type"] for r in await amy(oem_am, part_type="genuine")] == ["oem"]          # valid label is NEVER None
    assert [r["part_type"] for r in await amy(await newpart(), part_type="analog")] == ["aftermarket"]
    assert [r["part_type"] for r in await amy(await newpart())] == ["oem"]                      # no label -> source default
    assert [r["part_type"] for r in await amy(await newpart(), part_type="מקורי")] == ["oem"]
    unk = await newpart()
    assert await amy(unk, part_type="mystery-grade") == []                                      # unknown label: skipped + counted, not written
    # --- runtime proof over EVERYTHING written by the sandbox so far: only canonical values (or NULL) ever reached the table
    # --- runtime proof over EVERYTHING written by the sandbox so far: only canonical values (or NULL) ever reached the table
    bad = await c.fetch("SELECT DISTINCT part_type FROM supplier_parts WHERE supplier_id = ANY($1::uuid[]) OR supplier_sku LIKE 'RA-%' OR supplier_sku LIKE 'AMY-%'", w.sups)
    for r in bad:
        assert r["part_type"] is None or r["part_type"] in vi.STORED_VALUES, r["part_type"]
    for nm in ("RockAuto", "Amayama"):
        await c.execute("DELETE FROM price_history WHERE supplier_part_id IN (SELECT sp.id FROM supplier_parts sp JOIN suppliers s ON s.id=sp.supplier_id WHERE s.name=$1)", nm)
        await c.execute("DELETE FROM supplier_parts WHERE supplier_id IN (SELECT id FROM suppliers WHERE name=$1)", nm)
        await c.execute("DELETE FROM suppliers WHERE name=$1", nm)


# ===================================================================== catalog branch + samelet (2026-11-07)
@db_only
@with_world
async def test_normalize_part_types_catalog_branch_and_table_isolation(c, w, tmp_path, monkeypatch):
    """parts_catalog has no part_id column: the catalog branch must work, use the CLASS convention, and a failure in one table
    must never stop the other."""
    import db_update_agent as dua
    monkeypatch.setattr(dua, "_PT_MANIFEST_PATH", tmp_path / "m.jsonl")
    b = await c.fetchval("INSERT INTO car_brands (name) VALUES ($1) RETURNING id", f"b{w.tag}c")
    ids = {}
    for lab in ("Original", "חליפי", "Refurbished", "oem", "mystery-type", "unknown"):
        pid = uuid.uuid4()
        await c.execute("INSERT INTO parts_catalog (id, sku, name, manufacturer_id, part_type) VALUES ($1,$2,'n',$3,$4)", pid, f"C{w.tag}{len(ids)}", b, lab)
        ids[lab] = pid
    w.parts += list(ids.values())
    p = await w.part()
    s1 = await w.sup()
    await w.offer(p, s1, "K1", "original")
    victim = await w.offer(p, s1, "K2", "מקורי")
    r1 = await _normalize(_engine)
    got = {k: await c.fetchval("SELECT part_type FROM parts_catalog WHERE id=$1", v) for k, v in ids.items()}
    assert got == {"Original": "oem", "חליפי": "aftermarket", "Refurbished": "remanufactured", "oem": "oem",
                   "mystery-type": "mystery-type", "unknown": "unknown"}, got
    assert r1["status"] == "ok" and r1["errors"] == 0 and r1["catalog_updated"] >= 3
    assert r1["skipped_collision"] >= 1 and await c.fetchval("SELECT part_type FROM supplier_parts WHERE id=$1", victim) == "מקורי"
    assert "mystery-type" in r1["unmapped"]["parts_catalog"] and "unknown" in r1["placeholders_left"]["parts_catalog"]
    r2 = await _normalize(_engine)                                                    # idempotent
    assert r2["status"] == "ok" and r2["catalog_updated"] == 0 and r2["supplier_updated"] == 0
    await c.execute("DELETE FROM parts_catalog WHERE id = ANY($1::uuid[])", list(ids.values()))
    # isolation: make the catalog pass blow up -> supplier pass still runs and the failure is reported, not swallowed
    victim2 = await w.offer(await w.part(), s1, "K9", "Original")
    real = dua._normalize_part_type_table

    async def boom(db, table, **k):
        if table == "parts_catalog":
            raise RuntimeError("simulated catalog failure")
        return await real(db, table, **k)
    monkeypatch.setattr(dua, "_normalize_part_type_table", boom)
    r3 = await _normalize(_engine)
    assert r3["status"] == "error" and r3["errors"] >= 1 and "parts_catalog" in r3["table_failures"]
    assert await c.fetchval("SELECT part_type FROM supplier_parts WHERE id=$1", victim2) == "original"      # supplier pass still ran
    await c.execute("DELETE FROM car_brands WHERE id=$1", b)


def test_samelet_writes_the_catalog_class_and_never_overwrites_it():
    src = (ROOT / "importers" / "samelet_import_v2.py").read_text()
    assert "catalog_part_type = variant_key(part_type)" in src
    assert "catalog_part_type, base_price_val" in src                                 # the tuple carries the class, not 'original'
    assert "part_type=EXCLUDED.part_type" not in src
    assert "part_type=COALESCE(NULLIF(parts_catalog.part_type, ''), EXCLUDED.part_type)" in src
    assert variant_key("original") == "oem" and variant_key("aftermarket") == "aftermarket"      # MaterialType 01 / other


@db_only
@with_world
async def test_samelet_catalog_upsert_fills_but_never_overwrites_part_type(c, w):
    import importlib
    sys.path.insert(0, str(ROOT / "importers"))
    sam = importlib.import_module("samelet_import_v2")
    brand = await c.fetchval("INSERT INTO car_brands (name) VALUES ($1) RETURNING id", f"b{w.tag}s")
    sku = f"SAM-{w.tag}"

    def row(pt):
        return (sku, "n", "ש", "כללי", "Brand", str(brand), pt, 10.0, 5.0, 12.0, f"OEM{w.tag}", "{}", 12.0, None, False)
    r1 = await c.fetchrow(sam._CATALOG_UPSERT, *row("oem"))
    assert await c.fetchval("SELECT part_type FROM parts_catalog WHERE sku=$1", sku) == "oem"
    await c.fetchrow(sam._CATALOG_UPSERT, *row("aftermarket"))                      # re-import with another class: existing kept
    assert await c.fetchval("SELECT part_type FROM parts_catalog WHERE sku=$1", sku) == "oem"
    await c.execute("UPDATE parts_catalog SET part_type=NULL WHERE sku=$1", sku)
    await c.fetchrow(sam._CATALOG_UPSERT, *row("aftermarket"))                      # empty: filled
    assert await c.fetchval("SELECT part_type FROM parts_catalog WHERE sku=$1", sku) == "aftermarket"
    await c.execute("DELETE FROM parts_catalog WHERE sku=$1", sku)
    await c.execute("DELETE FROM car_brands WHERE id=$1", brand)


# ===================================================================== free-text type fields, SNG importer, admin upload (2026-11-08)
def test_resolve_text_places_a_label_inside_a_type_field():
    rt = vi.resolve_text
    assert (rt("Genuine Land Rover Part").stored, rt("Genuine Land Rover Part").key) == ("original", "oem")
    assert (rt("OEM Equivalent").stored, rt("OEM Equivalent").key) == ("oe_equivalent", "oe_equivalent")   # never also an 'oem'
    assert rt("Uprated - OE equivalent").stored == "oe_equivalent"
    assert rt("Aftermarket Premium").stored == "aftermarket" and rt("Remanufactured").stored == "remanufactured"
    assert rt("Original / Aftermarket").kind == "ambiguous" and rt("OEM and Aftermarket").kind == "ambiguous"
    assert rt("Economy").kind == "unknown" and rt("Land Rover").kind == "unknown"
    assert rt("").kind == "missing" and rt(None).kind == "missing"
    assert rt("New").kind == "ambiguous"


def _sng():
    import importlib
    sys.path.insert(0, str(ROOT / "importers"))
    os.environ.setdefault("DB_PASSWORD", "x")
    return importlib.import_module("sng_barratt_jaguar_import")


def test_sng_variant_of_uses_the_shared_vocabulary_and_never_defaults():
    sng = _sng()
    v = sng.variant_of
    assert v("Genuine Land Rover Part") == ("original", "oem")
    assert v("OEM Equivalent") == ("oe_equivalent", "oe_equivalent")
    assert v("Aftermarket") == ("aftermarket", "aftermarket")
    assert v("Economy", "OE_equivalent") == ("oe_equivalent", "oe_equivalent")            # second evidence: SNG's own tier
    assert v("Economy", "generic") == ("aftermarket", "aftermarket")
    before = sng.VARIANT_STATS["unresolved"]
    assert v("Economy", "economy") == (None, None) and v("Mystery", None) == (None, None)  # not placed -> untyped, counted
    assert sng.VARIANT_STATS["unresolved"] == before + 2


@db_only
@with_world
async def test_sng_import_batch_writes_matching_offer_value_and_catalog_class(c, w):
    sng = _sng()
    suppliers = await w.sup("SNG Barratt " + w.tag)
    brand = sng.JAGUAR_BRAND_ID
    await c.execute("INSERT INTO car_brands (id, name) VALUES ($1::uuid, 'Jaguar') ON CONFLICT DO NOTHING", brand)

    def rec(n, tn, tier=None):
        return {"part_number": f"T{w.tag}{n}", "title": f"Part {n}", "description": "d", "type_name": tn, "aftermarket_tier": tier,
                "price_gbp": 10.0, "stock_status": "in_stock", "base_part_number": f"B{w.tag}{n}", "applications": [], "web_product_guid": ""}
    batch = [rec(1, "Genuine Land Rover Part"), rec(2, "OEM Equivalent"), rec(3, "Aftermarket"), rec(4, "Economy")]
    ins, skp = await sng.import_batch(c, suppliers, batch, True)
    rows = {r["supplier_sku"]: (r["sp"], r["cat"]) for r in await c.fetch(
        "SELECT sp.supplier_sku, sp.part_type sp, pc.part_type cat FROM supplier_parts sp JOIN parts_catalog pc ON pc.id=sp.part_id WHERE sp.supplier_id=$1", suppliers)}
    assert rows[f"T{w.tag}1"] == ("original", "oem") and rows[f"T{w.tag}2"] == ("oe_equivalent", "oe_equivalent")
    assert rows[f"T{w.tag}3"] == ("aftermarket", "aftermarket") and rows[f"T{w.tag}4"] == (None, None)
    await sng.import_batch(c, suppliers, batch, True)                                      # idempotent: same values, no new rows
    assert await c.fetchval("SELECT count(*) FROM supplier_parts WHERE supplier_id=$1", suppliers) == 4
    pids = [r["part_id"] for r in await c.fetch("SELECT part_id FROM supplier_parts WHERE supplier_id=$1", suppliers)]
    await c.execute("DELETE FROM part_vehicle_fitment WHERE part_id = ANY($1::uuid[])", pids)
    w.parts += pids


@db_only
@with_world
async def test_admin_parts_upload_normalises_part_type_and_reports_unknown(c, w):
    import io
    import openpyxl
    from types import SimpleNamespace
    from sqlalchemy.ext.asyncio import AsyncSession
    import routes.admin as adm
    # production fills manufacturer_id with a BEFORE trigger the sandbox schema dump omits; relax the NOT NULL here (sandbox only)
    await c.execute("ALTER TABLE parts_catalog ALTER COLUMN manufacturer_id DROP NOT NULL")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["sku", "name", "part_type"])
    tag = w.tag.upper()
    ws.append([f"AD{tag}1", "p1", "מקורי"])
    ws.append([f"AD{tag}2", "p2", "Aftermarket"])
    ws.append([f"AD{tag}3", "p3", "OEM equivalent"])
    ws.append([f"AD{tag}4", "p4", "mystery-grade"])
    ws.append([f"AD{tag}5", "p5", "מקורי/חליפי"])
    ws.append([f"AD{tag}6", "p6", None])
    buf = io.BytesIO()
    wb.save(buf)

    class F:
        filename = "x.xlsx"

        async def read(self):
            return buf.getvalue()
    eng = await _engine()
    async with AsyncSession(eng) as db:
        fn = getattr(adm.import_parts_excel, "__wrapped__", adm.import_parts_excel)
        res = await fn(F(), current_user=SimpleNamespace(), db=db)
    await eng.dispose()
    got = {r["sku"]: r["part_type"] for r in await c.fetch("SELECT sku, part_type FROM parts_catalog WHERE sku LIKE $1", f"AD{tag}%")}
    assert got == {f"AD{tag}1": "oem", f"AD{tag}2": "aftermarket", f"AD{tag}3": "oe_equivalent", f"AD{tag}6": "aftermarket"}, got
    assert res["created"] == 4 and res["skipped"] == 2 and res["defaulted_part_type"] == 1
    assert {x["label"] for x in res["rejected_part_type"]} == {"mystery-grade", "מקורי/חליפי"}
    await c.execute("DELETE FROM parts_catalog WHERE sku LIKE $1", f"AD{tag}%")

