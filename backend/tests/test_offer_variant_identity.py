"""Regression + integration tests for OFFER_DEDUPLICATION_POLICY 1.0.0 variant identity (Agent 4, 2026-11-06).

Two layers:
  * PURE (no DB): the six classes, same supplier/part/different variant, changed price, different suppliers, true duplicate
    with full evidence, typed/untyped/case/empty-string variants in the classifier, and the write gate.
  * DB (disposable Postgres only): the two exact partial-unique-index DDLs from
    candidates/offer-policy-2026-11-06/migration_variant_identity.sql are read FROM THE FILE and applied; tests run at
    stage1 (old unique index + new ones) and stage3 (old index dropped). Needs AG4_PG_DSN (a DSN whose database name
    contains "sandbox_"); otherwise the DB tests SKIP. It refuses to run against anything that is not a sandbox DB.
Never run against production: the DSN guard below is a hard stop."""
import asyncio
import os
import re
import uuid
from pathlib import Path

import pytest

import offer_policy as op
from offer_policy import Classification as C

ROOT = Path(__file__).resolve().parent.parent
SQL_FILE = Path(os.getenv("AG4_MIGRATION_SQL") or ROOT.parent / "candidates" / "offer-policy-2026-11-06" / "migration_variant_identity.sql")
POLICY = Path(os.getenv("OFFER_POLICY_PATH") or ROOT.parent / "docs" / "policies" / "OFFER_DEDUPLICATION_POLICY.md")
S1, S2 = "s1", "s2"


def offer(**kw):
    base = {"supplier_id": S1, "part_id": "p1", "supplier_sku": "X1", "part_type": "oem", "price_ils": 100.0,
            "is_available": True, "supplier_url": "u", "name": "Pad", "row_id": None}
    base.update(kw)
    return base


# ============================================================================ PURE: six classes
def test_class_A_competing_offers_different_suppliers():
    assert op.classify_pair(offer(), offer(supplier_id=S2)) is C.COMPETING_OFFERS
    assert op.classify_pair(offer(), offer(supplier_id=S2, supplier_sku="Z", price_ils=1.0, part_type="aftermarket")) is C.COMPETING_OFFERS


def test_class_B_same_supplier_same_part_different_variant():
    assert op.classify_pair(offer(part_type="oem"), offer(part_type="aftermarket", supplier_sku="X2")) is C.DISTINCT_VARIANTS
    # same SKU string but a different variant is STILL a different variant (sku never implies same variant)
    assert op.classify_pair(offer(part_type="oem"), offer(part_type="aftermarket")) is C.DISTINCT_VARIANTS
    assert op.classify_pair(offer(part_type="oe_equivalent"), offer(part_type="aftermarket", supplier_sku="X2")) is C.DISTINCT_VARIANTS


def test_class_C_same_variant_changed_price_needs_evidence_of_later_observation():
    a, b = offer(price_ils=100.0), offer(price_ils=110.0)
    assert op.classify_pair(a, b) is C.UNRESOLVED
    assert op.classify_pair(a, b, evidence={"later": "b"}) is C.PRICE_UPDATE
    assert op.classify_pair(a, b, evidence={"later": "a"}) is C.PRICE_UPDATE
    # an update to an identical price is not a price update (it would be a true duplicate)
    assert op.classify_pair(a, offer(), evidence={"later": "b"}) is C.TRUE_DUPLICATE


def test_class_D_true_duplicate_requires_full_evidence():
    assert op.classify_pair(offer(), offer()) is C.TRUE_DUPLICATE
    assert op.classify_pair(offer(), offer(supplier_sku="x1")) is C.TRUE_DUPLICATE  # sku compared case-insensitively
    # each missing piece of evidence downgrades to UNRESOLVED, never D
    assert op.classify_pair(offer(), offer(supplier_url="other")) is C.UNRESOLVED
    assert op.classify_pair(offer(), offer(is_available=False)) is C.UNRESOLVED
    assert op.classify_pair(offer(), offer(price_ils=101.0)) is C.UNRESOLVED
    assert op.classify_pair(offer(), offer(part_id="p2")) is C.UNRESOLVED
    assert op.classify_pair(offer(), offer(supplier_sku="X2")) is C.UNRESOLVED
    assert op.classify_pair(offer(supplier_url=None), offer(supplier_url=None)) is C.TRUE_DUPLICATE


def test_class_E_misattributed_only_with_source_evidence_naming_the_other_supplier():
    a, b = offer(supplier_id=S1), offer(supplier_id=S2)
    assert op.classify_pair(a, b) is C.COMPETING_OFFERS  # no evidence -> never E
    assert op.classify_pair(a, b, evidence={"source_proves_supplier": S2}) is C.MISATTRIBUTED
    assert op.classify_pair(a, b, evidence={"source_proves_supplier": "s3"}) is C.UNRESOLVED  # third supplier: stay put


def test_class_F_unresolved_unknown_variant():
    assert op.classify_pair(offer(part_type=None), offer(part_type="oem", supplier_sku="Z")) is C.UNRESOLVED
    assert op.classify_pair(offer(part_type=None, supplier_sku="A"), offer(part_type=None, supplier_sku="B", name="o")) is C.UNRESOLVED


# ============================================================================ PURE: typed / untyped / boundary variants
@pytest.mark.parametrize("x,y", [("OEM", "oem"), ("original", "oem"), ("Genuine", "OEM"), ("oe", "original")])
def test_variant_spellings_are_one_class_not_B(x, y):
    assert op.classify_pair(offer(part_type=x), offer(part_type=y)) is C.TRUE_DUPLICATE


@pytest.mark.parametrize("x,y", [("OEM", "aftermarket"), ("original", "aftermarket"), ("oe_equivalent", "oem")])
def test_distinct_variant_spellings_are_B(x, y):
    assert op.classify_pair(offer(part_type=x, supplier_sku="A"), offer(part_type=y, supplier_sku="B")) is C.DISTINCT_VARIANTS


@pytest.mark.parametrize("unknown", [None, "", "unknown", "none", "null"])
def test_empty_or_placeholder_variant_is_unknown_never_merged_with_typed(unknown):
    assert op.classify_pair(offer(part_type=unknown), offer(part_type="oem", supplier_sku="Z")) is C.UNRESOLVED
    assert op.classify_pair(offer(part_type="oem", supplier_sku="Z"), offer(part_type=unknown)) is C.UNRESOLVED


def test_null_offer_variant_inherits_catalog_class():
    a = offer(part_type=None, catalog_part_type="oem")
    assert op.classify_pair(a, offer(part_type="OEM")) is C.TRUE_DUPLICATE
    assert op.classify_pair(a, offer(part_type="aftermarket", supplier_sku="B")) is C.DISTINCT_VARIANTS


def test_write_gate_matrix():
    info = op.load_policy(POLICY)
    ok = dict(policy=info, dry_run_done=True, has_other_orderable_offer=True)
    for cls in (C.COMPETING_OFFERS, C.DISTINCT_VARIANTS, C.PRICE_UPDATE, C.UNRESOLVED, C.MISATTRIBUTED):
        for action in ("deactivate", "retire", "hide", "merge", "delete"):
            with pytest.raises(op.PolicyViolation):
                op.assert_write_allowed(action, cls, **ok)
    assert op.assert_write_allowed("retire", C.TRUE_DUPLICATE, **ok)
    with pytest.raises(op.PolicyViolation):  # hard delete never allowed, even for D
        op.assert_write_allowed("delete", C.TRUE_DUPLICATE, **ok)
    with pytest.raises(op.PolicyViolation):
        op.assert_write_allowed("retire", C.TRUE_DUPLICATE, policy=info, dry_run_done=True, has_other_orderable_offer=None)


# ============================================================================ DB layer (disposable PG only)
DSN = os.getenv("AG4_PG_DSN", "")
db_only = pytest.mark.skipif(not DSN or "sandbox_" not in DSN.rsplit("/", 1)[-1],
                             reason="AG4_PG_DSN (disposable sandbox_ database) not set")

OLD_IDX = "uq_supplier_parts_part_supplier"
NEW_IDXS = ("uq_sp_part_supplier_variant", "uq_sp_part_supplier_untyped")


def _create_index_statements():
    sql = SQL_FILE.read_text()
    stmts = [s.strip() for s in re.split(r";\s*(?:\n|$)", re.sub(r"(?m)^\s*--.*$", "", sql)) if s.strip()]
    out = [s for s in stmts if s.upper().startswith("CREATE UNIQUE INDEX CONCURRENTLY")]
    assert len(out) == 2, out
    return out


async def _set_stage(stage):
    import asyncpg
    c = await asyncpg.connect(DSN)
    try:
        for n in NEW_IDXS:
            await c.execute(f"DROP INDEX IF EXISTS {n}")
        await c.execute(f"DROP INDEX IF EXISTS {OLD_IDX}")
        if stage == "stage0":
            await c.execute(f"CREATE UNIQUE INDEX {OLD_IDX} ON supplier_parts (part_id, supplier_id)")
        elif stage == "stage1":
            await c.execute(f"CREATE UNIQUE INDEX {OLD_IDX} ON supplier_parts (part_id, supplier_id)")
            for s in _create_index_statements():
                await c.execute(s)
        elif stage == "stage3":
            for s in _create_index_statements():
                await c.execute(s)
        else:
            raise ValueError(stage)
    finally:
        await c.close()


class _Rollback(Exception):
    pass


def run_db(stage, fn):
    """Apply the index stage, then run fn(conn, ctx) inside a transaction that is always rolled back."""
    import asyncpg

    async def main():
        await _set_stage(stage)
        c = await asyncpg.connect(DSN)
        try:
            tx = c.transaction()
            await tx.start()
            tag = uuid.uuid4().hex[:8]
            brand = await c.fetchval("INSERT INTO car_brands (name) VALUES ($1) RETURNING id", f"b{tag}")
            part = uuid.uuid4()
            part2 = uuid.uuid4()
            for p in (part, part2):
                await c.execute("INSERT INTO parts_catalog (id, sku, name, manufacturer_id) VALUES ($1,$2,'n',$3)", p, f"P{tag}{str(p)[:4]}", brand)
            sup = [uuid.uuid4(), uuid.uuid4()]
            for i, s in enumerate(sup):
                await c.execute("INSERT INTO suppliers (id, name) VALUES ($1,$2)", s, f"s{i}{tag}")
            ctx = type("Ctx", (), {"part": part, "part2": part2, "s1": sup[0], "s2": sup[1], "tag": tag})
            try:
                await fn(c, ctx)
            finally:
                await tx.rollback()
        finally:
            await c.close()

    asyncio.run(main())


async def ins(c, part, sup, sku, ptype, price=100):
    """Insert one offer inside a savepoint so a unique violation does not abort the surrounding test transaction."""
    rid = uuid.uuid4()
    await c.execute("INSERT INTO supplier_parts (id, supplier_id, part_id, supplier_sku, price_usd, price_ils, part_type, is_available) "
                    "VALUES ($1,$2,$3,$4,1,$5,$6,true)", rid, sup, part, sku, price, ptype)
    return rid


async def rejected(c, part, sup, sku, ptype, index=None):
    import asyncpg
    sp = c.transaction()
    await sp.start()
    try:
        await ins(c, part, sup, sku, ptype)
    except asyncpg.UniqueViolationError as e:
        await sp.rollback()
        if index:
            assert index in (e.constraint_name or ""), e.constraint_name
        return e.constraint_name
    await sp.rollback()
    return None


STAGES = ["stage1", "stage3"]


@db_only
def test_indexes_valid_and_exact_ddl_idempotent():
    async def f(c, x):
        rows = await c.fetch("SELECT c.relname, i.indisvalid, i.indisready, i.indisunique, pg_get_expr(i.indpred, i.indrelid) pred "
                             "FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid WHERE c.relname = ANY($1)", list(NEW_IDXS))
        d = {r["relname"]: r for r in rows}
        assert set(d) == set(NEW_IDXS)
        assert all(r["indisvalid"] and r["indisready"] and r["indisunique"] for r in rows)
        assert "part_type IS NOT NULL" in d["uq_sp_part_supplier_variant"]["pred"]
        assert "part_type IS NULL" in d["uq_sp_part_supplier_untyped"]["pred"]
    run_db("stage1", f)

    async def again():  # IF NOT EXISTS: re-running the exact DDL is a no-op
        import asyncpg
        c = await asyncpg.connect(DSN)
        for s in _create_index_statements():
            await c.execute(s)
        await c.close()
    asyncio.run(again())


@db_only
@pytest.mark.parametrize("stage", STAGES)
def test_typed_variants_coexist_only_when_old_index_gone(stage):
    async def f(c, x):
        await ins(c, x.part, x.s1, "A-OEM", "oem")
        # the aftermarket twin of the same supplier+part: blocked by the OLD index at stage1, allowed at stage3
        bad = await rejected(c, x.part, x.s1, "A-AM", "aftermarket")
        if stage == "stage1":
            assert bad == OLD_IDX
        else:
            assert bad is None
            assert await rejected(c, x.part, x.s1, "A-OE", "oe_equivalent") is None
            n = await c.fetchval("SELECT count(*) FROM supplier_parts WHERE part_id=$1 AND supplier_id=$2", x.part, x.s1)
            assert n == 1  # rejected() rolls its own insert back; use an explicit insert to prove coexistence:
            await ins(c, x.part, x.s1, "A-AM2", "aftermarket")
            await ins(c, x.part, x.s1, "A-OE2", "oe_equivalent")
            assert await c.fetchval("SELECT count(*) FROM supplier_parts WHERE part_id=$1 AND supplier_id=$2", x.part, x.s1) == 3
    run_db(stage, f)


@db_only
def test_stage3_same_variant_still_unique_with_case_and_synonyms():
    async def f(c, x):
        await ins(c, x.part, x.s1, "O1", "oem")
        for dup in ("OEM", "Oem", "original", "Original", "genuine", "GENUINE", "oe", "OE"):
            assert await rejected(c, x.part, x.s1, f"D-{dup}", dup, "uq_sp_part_supplier_variant") is not None, dup
        await ins(c, x.part, x.s1, "AM1", "aftermarket")
        for dup in ("AFTERMARKET", "Aftermarket"):
            assert await rejected(c, x.part, x.s1, f"D-{dup}", dup, "uq_sp_part_supplier_variant") is not None, dup
        # a different class is accepted
        assert await rejected(c, x.part, x.s1, "OEQ", "oe_equivalent") is None
    run_db("stage3", f)


@db_only
def test_stage3_untyped_and_typed_null_boundary():
    async def f(c, x):
        await ins(c, x.part, x.s1, "N1", None)
        assert await rejected(c, x.part, x.s1, "N2", None, "uq_sp_part_supplier_untyped") is not None  # second NULL rejected
        await ins(c, x.part, x.s1, "T1", "oem")            # NULL + typed coexist
        await ins(c, x.part, x.s1, "T2", "aftermarket")
        # a different part / supplier is independent for NULL
        await ins(c, x.part2, x.s1, "N3", None)
        await ins(c, x.part, x.s2, "N4", None)
        await ins(c, x.part, x.s2, "T3", "oem")
        assert await c.fetchval("SELECT count(*) FROM supplier_parts WHERE part_id=$1", x.part) == 5
    run_db("stage3", f)


@db_only
def test_stage3_boundary_values_empty_string_whitespace_unknown():
    """BOUNDARY FINDINGS (documented behaviour, asserted so a change is noticed):
    '' is a TYPED value for the index (not NULL): '' coexists with NULL, two '' collide, and '' coexists with 'oem'.
    ' oem' / 'oem ' (whitespace) and 'unknown' are NOT normalised by the DB: they coexist with 'oem'/NULL.
    offer_policy._clean_type treats '', 'unknown', 'none', 'null' as unknown, so the DB and classifier disagree for 'unknown'."""
    async def f(c, x):
        await ins(c, x.part, x.s1, "E0", None)
        await ins(c, x.part, x.s1, "E1", "")
        assert await rejected(c, x.part, x.s1, "E2", "", "uq_sp_part_supplier_variant") is not None
        await ins(c, x.part, x.s1, "E3", "oem")
        await ins(c, x.part, x.s1, "E4", " oem")      # whitespace variant escapes the unique key
        await ins(c, x.part, x.s1, "E5", "unknown")   # placeholder escapes NULL-uniqueness
        assert await c.fetchval("SELECT count(*) FROM supplier_parts WHERE part_id=$1 AND supplier_id=$2", x.part, x.s1) == 5
    run_db("stage3", f)


@db_only
@pytest.mark.parametrize("stage", STAGES)
def test_different_suppliers_same_part_same_variant_always_coexist(stage):
    async def f(c, x):
        await ins(c, x.part, x.s1, "SK", "oem", 100)
        await ins(c, x.part, x.s2, "SK", "oem", 90)       # same SKU string, other supplier: independent
        await ins(c, x.part, x.s2, "SK2", None, 95) if stage == "stage3" else None
        assert await c.fetchval("SELECT count(*) FROM supplier_parts WHERE part_id=$1", x.part) >= 2
    run_db(stage, f)


@db_only
@pytest.mark.parametrize("stage", STAGES)
def test_sku_constraint_unchanged_same_supplier_sku_cannot_sit_on_two_parts(stage):
    async def f(c, x):
        await ins(c, x.part, x.s1, "DUP", "oem")
        assert await rejected(c, x.part2, x.s1, "DUP", "oem", "supplier_parts_supplier_id_supplier_sku_key") is not None
    run_db(stage, f)


UPSERT = """INSERT INTO supplier_parts (id, supplier_id, part_id, supplier_sku, price_usd, price_ils, part_type, is_available)
VALUES ($1,$2,$3,$4,1,$5,$6,true)
ON CONFLICT ON CONSTRAINT supplier_parts_supplier_id_supplier_sku_key DO UPDATE SET
    price_ils=EXCLUDED.price_ils, is_available=EXCLUDED.is_available, updated_at=NOW()
RETURNING id, (xmax = 0) AS inserted, price_ils"""


async def upsert(c, part, sup, sku, ptype, price):
    r = await c.fetchrow(UPSERT, uuid.uuid4(), sup, part, sku, price, ptype)
    if not r["inserted"]:  # importer pattern: record the change in price_history
        await c.execute("INSERT INTO price_history (id, supplier_part_id, new_price_ils, new_price_usd) VALUES ($1,$2,$3,0)", uuid.uuid4(), r["id"], price)
    return r


@db_only
@pytest.mark.parametrize("stage", STAGES)
def test_repeat_import_idempotent_and_price_update_preserves_id_and_history(stage):
    async def f(c, x):
        r1 = await upsert(c, x.part, x.s1, "RI", "oem", 100)
        assert r1["inserted"]
        for _ in range(3):                      # repeated identical import: same row, no new rows
            r = await upsert(c, x.part, x.s1, "RI", "oem", 100)
            assert r["id"] == r1["id"] and not r["inserted"]
        assert await c.fetchval("SELECT count(*) FROM supplier_parts WHERE part_id=$1", x.part) == 1
        r2 = await upsert(c, x.part, x.s1, "RI", "oem", 120)  # price change: update in place
        assert r2["id"] == r1["id"] and float(r2["price_ils"]) == 120.0
        hist = await c.fetchval("SELECT count(*) FROM price_history WHERE supplier_part_id=$1", r1["id"])
        assert hist == 4  # 3 repeats + 1 change (importer helper appends on every non-insert; real helpers dedupe equal prices)
        assert await c.fetchval("SELECT count(*) FROM supplier_parts WHERE part_id=$1", x.part) == 1
    run_db(stage, f)


@db_only
def test_stage3_variant_import_each_variant_own_row_and_history():
    async def f(c, x):
        a = await upsert(c, x.part, x.s1, "V-OEM", "oem", 200)
        b = await upsert(c, x.part, x.s1, "V-AM", "aftermarket", 80)
        assert a["id"] != b["id"]
        await upsert(c, x.part, x.s1, "V-AM", "aftermarket", 70)     # only the aftermarket row changes
        assert float(await c.fetchval("SELECT price_ils FROM supplier_parts WHERE id=$1", a["id"])) == 200.0
        assert float(await c.fetchval("SELECT price_ils FROM supplier_parts WHERE id=$1", b["id"])) == 70.0
        assert await c.fetchval("SELECT count(*) FROM price_history WHERE supplier_part_id=$1", a["id"]) == 0
        assert await c.fetchval("SELECT count(*) FROM price_history WHERE supplier_part_id=$1", b["id"]) == 1
    run_db("stage3", f)


@db_only
def test_stage3_variant_conflict_is_a_loud_unique_violation_not_an_overwrite():
    """A different SKU for an already present supplier+part+variant must FAIL (importers report variant_conflict), never overwrite."""
    async def f(c, x):
        a = await ins(c, x.part, x.s1, "KEEP", "oem", 100)
        bad = await rejected(c, x.part, x.s1, "OTHER-SKU", "OEM", "uq_sp_part_supplier_variant")
        assert bad
        row = await c.fetchrow("SELECT supplier_sku, price_ils FROM supplier_parts WHERE id=$1", a)
        assert row["supplier_sku"] == "KEEP" and float(row["price_ils"]) == 100.0
    run_db("stage3", f)


@db_only
def test_stage3_on_conflict_part_supplier_target_has_no_arbiter_but_targetless_do_nothing_works():
    """db_update_agent used `ON CONFLICT (supplier_id, part_id)`; once the old index is dropped that statement errors
    (InvalidColumnReferenceError) - the targetless form keeps working. Proves why the code change must precede stage 3."""
    import asyncpg

    async def f(c, x):
        await ins(c, x.part, x.s1, "T0", "oem")
        sp = c.transaction()
        await sp.start()
        with pytest.raises(asyncpg.InvalidColumnReferenceError):
            await c.execute("INSERT INTO supplier_parts (id,supplier_id,part_id,supplier_sku,price_usd,is_available) VALUES ($1,$2,$3,'T1',1,true) "
                            "ON CONFLICT (supplier_id, part_id) DO NOTHING", uuid.uuid4(), x.s1, x.part)
        await sp.rollback()
        r = await c.execute("INSERT INTO supplier_parts (id,supplier_id,part_id,supplier_sku,price_usd,is_available) VALUES ($1,$2,$3,'T1',1,true) "
                            "ON CONFLICT DO NOTHING", uuid.uuid4(), x.s1, x.part)  # untyped row next to a typed one: allowed, inserted
        assert r.endswith("1")
        r = await c.execute("INSERT INTO supplier_parts (id,supplier_id,part_id,supplier_sku,price_usd,is_available) VALUES ($1,$2,$3,'T2',1,true) "
                            "ON CONFLICT DO NOTHING", uuid.uuid4(), x.s1, x.part)  # second untyped: swallowed silently by DO NOTHING
        assert r.endswith("0")
    run_db("stage3", f)


@db_only
def test_price_history_and_row_ids_survive_the_index_migration():
    """Stage0 -> stage1 -> stage3 must not change any row, id or price_history row; deleting an offer DOES cascade its
    history (why the old merge DELETE was destructive)."""
    import asyncpg

    async def main():
        await _set_stage("stage0")
        c = await asyncpg.connect(DSN)
        tag = uuid.uuid4().hex[:8]
        brand = await c.fetchval("INSERT INTO car_brands (name) VALUES ($1) RETURNING id", f"mig{tag}")
        part, sup = uuid.uuid4(), uuid.uuid4()
        await c.execute("INSERT INTO parts_catalog (id, sku, name, manufacturer_id) VALUES ($1,$2,'n',$3)", part, f"MIG{tag}", brand)
        await c.execute("INSERT INTO suppliers (id, name) VALUES ($1,$2)", sup, f"mig{tag}")
        ids = [await ins(c, part, sup, "M1", "oem")]
        await c.execute("INSERT INTO price_history (id, supplier_part_id, new_price_ils, new_price_usd) VALUES ($1,$2,5,0),($3,$2,6,0)", uuid.uuid4(), ids[0], uuid.uuid4())
        snap = lambda: c.fetch("SELECT id, price_ils, part_type, supplier_sku FROM supplier_parts WHERE part_id=$1 ORDER BY id", part)  # noqa: E731
        before, hb = await snap(), await c.fetchval("SELECT count(*) FROM price_history WHERE supplier_part_id=$1", ids[0])
        try:
            for st in ("stage1", "stage3"):
                await _set_stage(st) if False else None  # _set_stage drops indexes only, never rows
                await c.close(); await _set_stage(st); c = await asyncpg.connect(DSN)  # noqa: E702
                assert [tuple(r) for r in await snap()] == [tuple(r) for r in before]
                assert await c.fetchval("SELECT count(*) FROM price_history WHERE supplier_part_id=$1", ids[0]) == hb == 2
            await c.execute("DELETE FROM supplier_parts WHERE id=$1", ids[0])
            assert await c.fetchval("SELECT count(*) FROM price_history WHERE supplier_part_id=$1", ids[0]) == 0  # cascade (documented hazard)
        finally:
            await c.execute("DELETE FROM parts_catalog WHERE id=$1", part)
            await c.execute("DELETE FROM suppliers WHERE id=$1", sup)
            await c.execute("DELETE FROM car_brands WHERE id=$1", brand)
            await c.close()
    asyncio.run(main())
