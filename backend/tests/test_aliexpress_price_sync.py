"""aliexpress_price_sync regression suite — real Postgres schema, zero persistent writes.

Every test runs the production sync inside an OUTER transaction (session joins it via SAVEPOINT mode) that is
ROLLED BACK at the end, with the AliExpress network layer mocked. It proves the write path against the real
constraints/indexes (which is where the original defects were) without touching production data.
"""
import time
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

import services.aliexpress_price_sync as svc
from BACKEND_DATABASE_MODELS import engine
from services.suppliers.base_supplier import PartResult


def _hit(oem: str, price: float, img: str = "https://img.example/x.jpg") -> PartResult:
    return PartResult(supplier="aliexpress", item_id="1005000000000009", title=f"{oem} brake pad", price=price,
                      currency="USD", shipping_cost=0.0, total_cost=price, condition="New", seller="",
                      seller_rating=None, item_url="https://www.aliexpress.com/item/1005000000000009.html",
                      image_url=img, location="CN", estimated_delivery_days=20, ships_to_israel=True,
                      image_urls=[img])


@pytest.fixture
async def db():
    # own NullPool engine: the shared engine's pooled connections belong to another test's event loop
    eng = create_async_engine(engine.url, poolclass=NullPool)
    try:
        conn = await eng.connect()
    except Exception as exc:                       # pragma: no cover
        await eng.dispose()
        pytest.skip(f"database unreachable: {type(exc).__name__}")
    trans = await conn.begin()
    session = AsyncSession(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False)
    try:
        yield session
    finally:
        await session.close()
        await trans.rollback()                     # nothing persists
        await conn.close()
        await eng.dispose()


@pytest.fixture(autouse=True)
def mocks(monkeypatch):
    monkeypatch.setattr(svc, "_ALIEXPRESS_SYNC_LAST_ID", "00000000-0000-0000-0000-000000000000")
    monkeypatch.setattr(svc, "_ALIEXPRESS_SYNC_LAST_ID_PRICED", "00000000-0000-0000-0000-000000000000")
    monkeypatch.setenv("ALIEXPRESS_PRICED_SHARE", "0")            # these tests exercise the unpriced population
    monkeypatch.setenv("ALIEXPRESS_TARGETING", "blind")           # ... through the blind cursor (discovery is tested separately)
    monkeypatch.setattr(svc, "get_usd_to_ils_rate", AsyncMock(return_value=3.7))
    monkeypatch.setattr(svc, "_meili_sync_part", AsyncMock(return_value=True))
    monkeypatch.setattr(svc.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(svc.aliexpress, "_ensure_token", AsyncMock(return_value=True))
    monkeypatch.setattr(svc.aliexpress, "get_part_details", AsyncMock(return_value=None))


async def _unpriced(db, n):
    rows = (await db.execute(sa.text(
        """SELECT id::text, oem_number FROM parts_catalog
           WHERE is_active AND oem_number IS NOT NULL AND oem_number != '' AND (base_price IS NULL OR base_price = 0)
           ORDER BY id LIMIT :n"""), {"n": n})).fetchall()
    return rows


async def _ali_id(db):
    return (await db.execute(sa.text("SELECT id::text FROM suppliers WHERE name='AliExpress'"))).scalar_one()


async def test_matched_part_written_correctly_and_only_for_aliexpress(db, monkeypatch):
    rows = await _unpriced(db, 3)
    oem_map = {r[1]: _hit(r[1], 10.0) for r in rows[:1]}
    monkeypatch.setattr(svc.aliexpress, "search_by_oem", AsyncMock(side_effect=lambda oem, limit=3, brand='', name='', **kw: [oem_map[oem]] if oem in oem_map else []))
    ali = await _ali_id(db)
    other_before = (await db.execute(sa.text("SELECT count(*), coalesce(sum(price_ils),0) FROM supplier_parts WHERE supplier_id <> CAST(:a AS uuid)"), {"a": ali})).fetchone()
    part_id, oem = rows[0]
    await db.execute(sa.text("UPDATE parts_catalog SET min_price_ils = 0 WHERE id = CAST(:p AS uuid)"), {"p": part_id})   # the real backlog state
    base_before = (await db.execute(sa.text("SELECT base_price FROM parts_catalog WHERE id=CAST(:p AS uuid)"), {"p": part_id})).scalar_one()

    rep = await svc.sync_aliexpress_prices(db, limit_per_run=3)

    assert rep["errors"] == [] and rep["parts_checked"] == 3 and rep["parts_updated"] == 1 and rep["parts_not_found"] == 2
    sp = (await db.execute(sa.text("""SELECT price_usd, price_ils, is_available, warranty_months, warranty_source, shipping_cost_ils, supplier_sku
                                      FROM supplier_parts WHERE supplier_id=CAST(:a AS uuid) AND part_id=CAST(:p AS uuid)"""), {"a": ali, "p": part_id})).fetchone()
    assert (float(sp.price_usd), float(sp.price_ils)) == (10.0, 37.0)          # USD x rate = ex-VAT cost
    assert sp.is_available and sp.supplier_sku == oem
    assert sp.warranty_months == 12 and sp.warranty_source == "platform_default"  # warranty_policy.resolve
    assert sp.shipping_cost_ils is None                                        # never a false "free shipping"
    pc = (await db.execute(sa.text("SELECT min_price_ils, base_price FROM parts_catalog WHERE id=CAST(:p AS uuid)"), {"p": part_id})).fetchone()
    assert float(pc.min_price_ils) == 37.0 and pc.base_price == base_before    # 0.00 (unpriced) replaced; base_price untouched
    assert rep["parts_images_added"] == 1 and rep["price_history_rows"] == 1
    img = (await db.execute(sa.text("SELECT count(*) FROM parts_images WHERE part_id=CAST(:p AS uuid) AND url='https://img.example/x.jpg'"), {"p": part_id})).scalar_one()
    assert img == 1
    hist = (await db.execute(sa.text("SELECT source, new_price_ils FROM price_history ph JOIN supplier_parts s ON s.id=ph.supplier_part_id WHERE s.part_id=CAST(:p AS uuid) AND s.supplier_id=CAST(:a AS uuid)"), {"p": part_id, "a": ali})).fetchall()
    assert [(h.source, float(h.new_price_ils)) for h in hist] == [("aliexpress_sync", 37.0)]
    other_after = (await db.execute(sa.text("SELECT count(*), coalesce(sum(price_ils),0) FROM supplier_parts WHERE supplier_id <> CAST(:a AS uuid)"), {"a": ali})).fetchone()
    assert tuple(other_before) == tuple(other_after)                           # no other supplier touched


async def test_rerun_is_idempotent_no_duplicate_rows_or_history(db, monkeypatch):
    rows = await _unpriced(db, 1)
    part_id, oem = rows[0]
    monkeypatch.setattr(svc.aliexpress, "search_by_oem", AsyncMock(return_value=[_hit(oem, 10.0)]))
    await svc.sync_aliexpress_prices(db, limit_per_run=1)
    monkeypatch.setattr(svc, "_ALIEXPRESS_SYNC_LAST_ID", "00000000-0000-0000-0000-000000000000")
    rep2 = await svc.sync_aliexpress_prices(db, limit_per_run=1)
    ali = await _ali_id(db)
    n = (await db.execute(sa.text("SELECT count(*) FROM supplier_parts WHERE supplier_id=CAST(:a AS uuid) AND part_id=CAST(:p AS uuid)"), {"a": ali, "p": part_id})).scalar_one()
    h = (await db.execute(sa.text("SELECT count(*) FROM price_history ph JOIN supplier_parts s ON s.id=ph.supplier_part_id WHERE s.part_id=CAST(:p AS uuid) AND s.supplier_id=CAST(:a AS uuid)"), {"p": part_id, "a": ali})).scalar_one()
    assert n == 1 and h == 1 and rep2["price_history_rows"] == 0 and rep2["errors"] == []   # history only on change


async def test_sku_conflict_skips_that_part_and_batch_continues(db, monkeypatch):
    rows = await _unpriced(db, 4)
    (p1, o1), (p2, o2), (p3, o3), (px, ox) = rows
    ali = await _ali_id(db)
    # another part of this supplier already holds OEM o2 -> unique (supplier_id, supplier_sku) would fire
    await db.execute(sa.text("""INSERT INTO supplier_parts (id, supplier_id, part_id, supplier_sku, price_usd, price_ils, is_available, availability)
                                VALUES (gen_random_uuid(), CAST(:a AS uuid), CAST(:px AS uuid), :sku, 5, 18.5, TRUE, 'in_stock')"""),
                     {"a": ali, "px": px, "sku": o2})
    monkeypatch.setattr(svc.aliexpress, "search_by_oem", AsyncMock(side_effect=lambda oem, limit=3, brand='', name='', **kw: [_hit(oem, 10.0)] if oem in (o1, o2, o3) else []))
    rep = await svc.sync_aliexpress_prices(db, limit_per_run=3)
    assert rep["errors"] == [] and rep["sku_conflicts"] == 1 and rep["parts_updated"] == 2   # p1 and p3 written, p2 skipped
    held = (await db.execute(sa.text("SELECT part_id::text, price_ils FROM supplier_parts WHERE supplier_id=CAST(:a AS uuid) AND supplier_sku=:s"), {"a": ali, "s": o2})).fetchall()
    assert len(held) == 1 and held[0][0] == px and float(held[0][1]) == 18.5             # other part's row NOT overwritten


async def test_write_failure_rolls_back_only_that_part(db, monkeypatch):
    rows = await _unpriced(db, 2)
    (p1, o1), (p2, o2) = rows
    monkeypatch.setattr(svc.aliexpress, "search_by_oem", AsyncMock(side_effect=lambda oem, limit=3, brand='', name='', **kw: [_hit(oem, 10.0)]))
    real = svc._update_catalog_min_price
    async def boom(db_, *, part_id, candidate_min_price_ils):
        if part_id == p1:
            raise RuntimeError("simulated write failure")
        return await real(db_, part_id=part_id, candidate_min_price_ils=candidate_min_price_ils)
    monkeypatch.setattr(svc, "_update_catalog_min_price", boom)
    rep = await svc.sync_aliexpress_prices(db, limit_per_run=2)
    ali = await _ali_id(db)
    assert len(rep["errors"]) == 1 and "RuntimeError" in rep["errors"][0] and rep["parts_updated"] == 1   # reported, not swallowed
    got = {r[0] for r in (await db.execute(sa.text("SELECT part_id::text FROM supplier_parts WHERE supplier_id=CAST(:a AS uuid) AND part_id IN (CAST(:p1 AS uuid), CAST(:p2 AS uuid))"), {"a": ali, "p1": p1, "p2": p2})).fetchall()}
    assert got == {p2}                                                                                  # p1's partial insert rolled back


async def test_credentials_not_ready_skips_without_work(db, monkeypatch):
    monkeypatch.setattr(svc.aliexpress, "_ensure_token", AsyncMock(return_value=False))
    search = AsyncMock(return_value=[])
    monkeypatch.setattr(svc.aliexpress, "search_by_oem", search)
    rep = await svc.sync_aliexpress_prices(db, limit_per_run=5)
    assert rep["parts_checked"] == 0 and any("credentials not ready" in e for e in rep["errors"]) and search.await_count == 0


def test_price_sync_enabled_in_sync_prices_and_no_second_scheduler():
    import inspect, re
    import BACKEND_AI_AGENTS as agents
    src = inspect.getsource(agents.SupplierManagerAgent.sync_prices)
    assert "platform_account_deleted" not in src
    assert "from services.aliexpress_price_sync import sync_aliexpress_prices" in src
    assert "ALIEXPRESS_PRICE_SYNC_ENABLED" in src and "_price_asf() as _adb" in src      # isolated session, kill-switch
    api = open(agents.__file__.replace("BACKEND_AI_AGENTS.py", "BACKEND_API_ROUTES.py")).read()
    assert len(re.findall(r"sync_aliexpress_prices", api)) == 0                          # no separate loop/worker


async def test_sync_passes_manufacturer_as_query_context_only(db, monkeypatch):
    """Query context (brand) improves retrieval; acceptance stays with the OEM-token guard."""
    rows = (await db.execute(sa.text("""SELECT oem_number, manufacturer, left(name, 100) FROM parts_catalog
        WHERE is_active AND oem_number<>'' AND (base_price IS NULL OR base_price=0) AND manufacturer IS NOT NULL ORDER BY id LIMIT 2"""))).fetchall()
    seen = []
    async def spy(oem, limit=3, brand="", name="", **kw):
        seen.append((oem, brand, name)); return []
    monkeypatch.setattr(svc.aliexpress, "search_by_oem", spy)
    await svc.sync_aliexpress_prices(db, limit_per_run=2)
    assert [(a, b) for a, b, _ in seen] == [(r[0], r[1]) for r in rows] and all(n is not None for _, _, n in seen)


@pytest.mark.parametrize("existing,expected", [(None, 37.0), (0, 37.0), (500, 37.0), (5, 5.0)])
async def test_min_price_semantics(db, existing, expected):
    """0/NULL = unpriced -> replaced; a higher price -> lowered; an already-lower price is kept."""
    part_id = (await _unpriced(db, 1))[0][0]
    await db.execute(sa.text("UPDATE parts_catalog SET min_price_ils = :v WHERE id = CAST(:p AS uuid)"), {"v": existing, "p": part_id})
    doc = await svc._update_catalog_min_price(db, part_id=part_id, candidate_min_price_ils=37.0)
    assert doc is not None
    now = (await db.execute(sa.text("SELECT min_price_ils FROM parts_catalog WHERE id = CAST(:p AS uuid)"), {"p": part_id})).scalar_one()
    assert float(now) == expected


# ---- ApiCallLimit observability, end-to-end through the real production sync (2026-09-24) ---------
async def test_api_call_limit_on_get_part_details_is_reported_and_fallback_write_unchanged(db, monkeypatch):
    """When get_part_details hits AliExpress's own ApiCallLimit rejection, sync_aliexpress_prices must:
    (a) still write the part using the listing-level (search_by_oem) result as a fallback — identical
        to the existing successful-match write path;
    (b) surface the hit in the run report as api_call_limit_hits, not silently as a plain success;
    (c) never treat the ApiCallLimit response itself as a successful API call or business error.
    """
    rows = await _unpriced(db, 3)
    oem_map = {r[1]: _hit(r[1], 10.0) for r in rows[:1]}
    monkeypatch.setattr(svc.aliexpress, "search_by_oem", AsyncMock(side_effect=lambda oem, limit=3, brand='', name='', **kw: [oem_map[oem]] if oem in oem_map else []))

    async def fake_get_part_details(item_id):
        svc.aliexpress._classify_api_error("aliexpress.ds.product.get", {"code": "ApiCallLimit", "msg": "Api access frequency exceeds the limit. this ban will last 1 seconds"})
        return None                                    # exactly what the real method returns on this error
    monkeypatch.setattr(svc.aliexpress, "get_part_details", fake_get_part_details)

    ali = await _ali_id(db)
    part_id, oem = rows[0]

    rep = await svc.sync_aliexpress_prices(db, limit_per_run=3)

    assert rep["errors"] == []                        # NOT raised as an exception/business error
    assert rep["api_call_limit_hits"] == 1             # but IS observable in the run report
    assert rep["parts_updated"] == 1                   # the part was still written (fallback), not skipped
    sp = (await db.execute(sa.text("SELECT price_usd, price_ils, is_available FROM supplier_parts WHERE supplier_id=CAST(:a AS uuid) AND part_id=CAST(:p AS uuid)"), {"a": ali, "p": part_id})).fetchone()
    assert (float(sp.price_usd), float(sp.price_ils)) == (10.0, 37.0) and sp.is_available   # identical to the plain-success case


async def test_no_api_call_limit_hits_when_nothing_trips_it(db, monkeypatch):
    rows = await _unpriced(db, 1)
    oem_map = {r[1]: _hit(r[1], 10.0) for r in rows[:1]}
    monkeypatch.setattr(svc.aliexpress, "search_by_oem", AsyncMock(side_effect=lambda oem, limit=3, brand='', name='', **kw: [oem_map[oem]] if oem in oem_map else []))
    rep = await svc.sync_aliexpress_prices(db, limit_per_run=1)
    assert rep["api_call_limit_hits"] == 0 and rep["parts_updated"] == 1
