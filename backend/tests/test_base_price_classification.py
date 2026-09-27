"""Base-price classification safety (2026-09-21).

`base_price` is the price of the catalog part's OWN product class. An offer of another class (an aftermarket AliExpress
listing on an OEM part) must never silently become — or raise — the base price of that part. Both writers that derive
`base_price` from supplier offers are exercised against the REAL schema (rolled-back transaction):
  * db_cleanup_agent.task_normalize_base_price_batched  (unpriced parts: base = min compatible cost x 1.45)
  * db_update_agent.fix_base_prices                     (raises base to the cheapest compatible offer's retail)
Customer-facing pricing policy is unchanged: supplier cost -> policy -> customer price.
"""
import uuid
from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

import db_cleanup_agent as cleanup
import db_update_agent as updater
from BACKEND_DATABASE_MODELS import engine


@pytest.fixture
async def db():
    eng = create_async_engine(engine.url, poolclass=NullPool)
    try:
        conn = await eng.connect()
    except Exception as exc:                                                            # pragma: no cover
        await eng.dispose()
        pytest.skip(f"database unreachable: {type(exc).__name__}")
    trans = await conn.begin()
    sess = AsyncSession(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False)
    try:
        yield sess
    finally:
        await sess.close(); await trans.rollback(); await conn.close(); await eng.dispose()


async def _part(db, part_type, offset=0):
    """A real, unpriced, offer-less catalog part, forced to the given class and to base_price NULL."""
    pid = (await db.execute(sa.text("""SELECT pc.id::text FROM parts_catalog pc WHERE pc.is_active AND pc.oem_number<>''
        AND NOT EXISTS (SELECT 1 FROM supplier_parts sp WHERE sp.part_id = pc.id) ORDER BY pc.id LIMIT 1 OFFSET :o"""), {"o": offset})).scalar_one()
    await db.execute(sa.text("UPDATE parts_catalog SET part_type = :t, base_price = NULL, importer_price_ils = NULL, online_price_ils = NULL, min_price_ils = NULL, max_price_ils = NULL WHERE id = CAST(:p AS uuid)"), {"t": part_type, "p": pid})
    return pid


async def _offer(db, pid, cost, klass):
    # one offer per (part, supplier): pick a supplier that has no offer on this part yet
    sup = (await db.execute(sa.text("""SELECT id::text FROM suppliers WHERE is_active AND name <> 'AliExpress'
        AND id NOT IN (SELECT supplier_id FROM supplier_parts WHERE part_id = CAST(:p AS uuid)) ORDER BY name LIMIT 1"""), {"p": pid})).scalar_one()
    await db.execute(sa.text("""INSERT INTO supplier_parts (id, supplier_id, part_id, supplier_sku, price_ils, price_usd, is_available, availability, part_type, updated_at, created_at)
        VALUES (gen_random_uuid(), CAST(:s AS uuid), CAST(:p AS uuid), :sku, :c, :u, TRUE, 'in_stock', :t, NOW(), NOW())"""),
        {"s": sup, "p": pid, "sku": f"BPT-{uuid.uuid4().hex[:10]}", "c": cost, "u": cost / 3.0, "t": klass})


async def _run_normalize(db, pid):
    await db.execute(sa.text(cleanup._normalize_base_price_sql(only_ids=True)), {"batch": 10, "only_ids": [pid]})


async def _run_fix(db, pid):
    rep = await updater.fix_base_prices(db, only_part_ids=[pid])
    assert rep["status"] == "ok"


async def _base(db, pid):
    r = (await db.execute(sa.text("SELECT base_price, importer_price_ils FROM parts_catalog WHERE id = CAST(:p AS uuid)"), {"p": pid})).fetchone()
    return (float(r.base_price) if r.base_price else None, float(r.importer_price_ils) if r.importer_price_ils else None)


RUNNERS = [pytest.param(_run_normalize, id="normalize_batched"), pytest.param(_run_fix, id="fix_base_prices")]


@pytest.mark.parametrize("run", RUNNERS)
async def test_A_oem_part_oem_offer_and_aftermarket_offer_uses_the_oem_basis(db, run):
    pid = await _part(db, "oem")
    await _offer(db, pid, 100.0, "oem")
    await _offer(db, pid, 20.0, "aftermarket")            # cheaper, other class
    await run(db, pid)
    assert (await _base(db, pid))[0] == 145.0             # 100 x 1.45, NOT 20 x 1.45


@pytest.mark.parametrize("run", RUNNERS)
async def test_B_oem_part_with_only_an_aftermarket_offer_stays_unpriced(db, run):
    pid = await _part(db, "oem")
    await _offer(db, pid, 20.0, "aftermarket")
    await run(db, pid)
    assert await _base(db, pid) == (None, None)           # the aftermarket cost must not become the OEM base price


@pytest.mark.parametrize("run", RUNNERS)
async def test_C_oem_part_with_only_an_oe_equivalent_offer_stays_unpriced_and_unclassified_offer_is_the_basis(db, run):
    pid = await _part(db, "oem")
    await _offer(db, pid, 30.0, "oe_equivalent")
    await run(db, pid)
    assert await _base(db, pid) == (None, None)           # equivalent is its own class (same rule as offer selection)
    pid2 = await _part(db, "oem", offset=1)
    await _offer(db, pid2, 30.0, "oe_equivalent")
    await _offer(db, pid2, 80.0, None)                    # legacy/unclassified offer inherits the part's class
    await run(db, pid2)
    assert (await _base(db, pid2))[0] == 116.0            # 80 x 1.45


@pytest.mark.parametrize("run", RUNNERS)
async def test_D_aftermarket_part_with_aftermarket_offer_is_priced_from_it(db, run):
    pid = await _part(db, "aftermarket")
    await _offer(db, pid, 20.0, "aftermarket")
    await _offer(db, pid, 500.0, "oem")                   # other class, pricier: ignored
    await run(db, pid)
    assert (await _base(db, pid))[0] == 29.0              # 20 x 1.45


@pytest.mark.parametrize("run", RUNNERS)
async def test_E_multiple_suppliers_mixed_classes_uses_cheapest_compatible(db, run):
    pid = await _part(db, "oem")
    await _offer(db, pid, 100.0, None)                    # unclassified (inherits oem)
    await _offer(db, pid, 90.0, "oem")                    # cheapest compatible
    await _offer(db, pid, 30.0, "oe_equivalent")
    await _offer(db, pid, 10.0, "aftermarket")            # cheapest overall, incompatible
    await run(db, pid)
    assert (await _base(db, pid))[0] == 130.5             # 90 x 1.45


async def test_healer_never_changes_pricing_policy_and_keeps_importer_cost(db):
    pid = await _part(db, "oem")
    await _offer(db, pid, 100.0, "oem")
    await _run_normalize(db, pid)
    base, importer = await _base(db, pid)
    assert base == round(100.0 * 1.45, 2) and importer == 100.0          # cost -> x1.45; importer cost = the compatible cost


async def test_fix_base_prices_does_not_raise_an_oem_base_price_to_an_aftermarket_offers_retail(db):
    pid = await _part(db, "oem")
    await db.execute(sa.text("UPDATE parts_catalog SET base_price = 100 WHERE id = CAST(:p AS uuid)"), {"p": pid})
    await _offer(db, pid, 500.0, "aftermarket")           # retail 725 would exceed base 100 -> must NOT raise it
    await _run_fix(db, pid)
    assert (await _base(db, pid))[0] == 100.0


# --- third writer: db_update_agent.normalize_base_price (importer/online/max_price_ils -> base_price) -------------
async def _run_normalize_cols(db, pid):
    rep = await updater.normalize_base_price(db, only_part_ids=[pid])
    assert rep["status"] == "ok"


async def test_F_normalize_base_price_ignores_max_price_fed_only_by_an_aftermarket_offer(db):
    pid = await _part(db, "oem")
    await _offer(db, pid, 91.02, "aftermarket")
    await db.execute(sa.text("UPDATE parts_catalog SET max_price_ils = 107.40 WHERE id = CAST(:p AS uuid)"), {"p": pid})   # cross-offer aggregate
    await _run_normalize_cols(db, pid)
    assert await _base(db, pid) == (None, None)


async def test_G_normalize_base_price_still_prices_from_max_when_a_compatible_offer_exists(db):
    pid = await _part(db, "oem")
    await _offer(db, pid, 100.0, "oem")
    await _offer(db, pid, 20.0, "aftermarket")
    await db.execute(sa.text("UPDATE parts_catalog SET max_price_ils = 118 WHERE id = CAST(:p AS uuid)"), {"p": pid})
    await _run_normalize_cols(db, pid)
    assert (await _base(db, pid))[0] == 145.0             # 118 / 1.18 x 1.45


async def test_H_normalize_base_price_unaffected_for_catalog_only_importer_data(db):
    pid = await _part(db, "oem")                          # no supplier rows at all
    await db.execute(sa.text("UPDATE parts_catalog SET importer_price_ils = 100 WHERE id = CAST(:p AS uuid)"), {"p": pid})
    await _run_normalize_cols(db, pid)
    assert (await _base(db, pid))[0] == 145.0


async def test_I_normalize_base_price_aftermarket_part_with_aftermarket_offer_is_priced(db):
    pid = await _part(db, "aftermarket")
    await _offer(db, pid, 50.0, "aftermarket")
    await db.execute(sa.text("UPDATE parts_catalog SET max_price_ils = 59 WHERE id = CAST(:p AS uuid)"), {"p": pid})
    await _run_normalize_cols(db, pid)
    assert (await _base(db, pid))[0] == 72.5              # 59 / 1.18 x 1.45
