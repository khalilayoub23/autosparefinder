"""eBay price sync: the existing-offer lookup is deterministic and variant/SKU safe (stage-3 blocker for dropping
uq_supplier_parts_part_supplier; OFFER_DEDUPLICATION_POLICY 1.0.0: offer identity = supplier + SKU).
Isolated DB only (same guard as the AliExpress tests); everything runs in a rolled-back transaction."""
import uuid
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

import services.ebay_price_sync as eb
from BACKEND_DATABASE_MODELS import engine
import isolated_db_guard  # noqa: E402  (tests/ on sys.path via conftest)


class _Res:
    def __init__(self, price):
        self.price, self.shipping_cost, self.currency = price, 5, "USD"
        self.ships_to_israel, self.item_id, self.item_url = True, None, "https://ebay.example/1"
        self.condition, self.image_url, self.total_cost, self.seller, self.seller_rating = "New", None, price + 5, "s", 1


class _FakeEbay:
    _base_url = "https://x"
    price = 10.0

    async def search_by_oem(self, pn, limit=3):
        return [_Res(self.price)]

    async def get_part_details(self, item_id):
        return None


@pytest.fixture
async def db():
    _refusal = isolated_db_guard.static_refusal()
    if _refusal is not None:
        pytest.skip(f"isolated DB only: {_refusal}")
    eng = create_async_engine(engine.url, poolclass=NullPool)
    try:
        conn = await eng.connect()
    except Exception as exc:  # pragma: no cover
        await eng.dispose()
        pytest.skip(f"database unreachable: {type(exc).__name__}")
    trans = await conn.begin()
    session = AsyncSession(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False)
    try:
        _live = await isolated_db_guard.live_refusal(session)
        if _live is not None:
            pytest.fail(f"REFUSED before any write: {_live}")
        yield session
    finally:
        await session.close()
        await trans.rollback()
        await conn.close()
        await eng.dispose()


@pytest.fixture(autouse=True)
def mocks(monkeypatch):
    monkeypatch.setattr(eb, "_EBAY_SYNC_LAST_ID", "00000000-0000-0000-0000-000000000000")
    monkeypatch.setattr(eb, "get_usd_to_ils_rate", AsyncMock(return_value=3.7))
    monkeypatch.setattr(eb, "_meili_sync_part", AsyncMock(return_value=True))
    monkeypatch.setattr(eb.asyncio, "sleep", AsyncMock())
    fake = _FakeEbay()
    monkeypatch.setattr(eb, "ebay", fake)
    return fake


async def _world(db, monkeypatch):
    tag = uuid.uuid4().hex[:8]
    brand = f"zz-ebay-{tag}"
    monkeypatch.setenv("EBAY_PRICE_SYNC_BRANDS", brand)
    bid = (await db.execute(sa.text("INSERT INTO car_brands (id, name) VALUES (gen_random_uuid(), :n) RETURNING id"), {"n": brand})).scalar_one()
    sup = (await db.execute(sa.text("SELECT id::text FROM suppliers WHERE name='eBay Motors'"))).scalar()
    if sup is None:
        sup = (await db.execute(sa.text("INSERT INTO suppliers (id,name,country,is_active) VALUES (gen_random_uuid(),'eBay Motors','US',TRUE) RETURNING id::text"))).scalar_one()
    oem = f"EB{tag.upper()}"
    pid = (await db.execute(sa.text(
        "INSERT INTO parts_catalog (id, sku, name, manufacturer_id, manufacturer, oem_number, is_active) "
        "VALUES (gen_random_uuid(), :s, 'n', :b, :m, :o, TRUE) RETURNING id::text"),
        {"s": f"EBT-{tag}", "b": bid, "m": brand, "o": oem})).scalar_one()
    return sup, pid, oem


async def _offer(db, sup, pid, sku, ptype, price, age_s=0):
    return (await db.execute(sa.text(
        "INSERT INTO supplier_parts (id, supplier_id, part_id, supplier_sku, price_usd, price_ils, is_available, part_type, created_at) "
        "VALUES (gen_random_uuid(), CAST(:s AS uuid), CAST(:p AS uuid), :k, 1, :pr, TRUE, :t, NOW() - make_interval(secs => :a)) RETURNING id::text"),
        {"s": sup, "p": pid, "k": sku, "pr": price, "t": ptype, "a": age_s})).scalar_one()


async def _rows(db, sup, pid):
    r = await db.execute(sa.text("SELECT id::text, supplier_sku, part_type, price_ils::float FROM supplier_parts "
                                 "WHERE supplier_id = CAST(:s AS uuid) AND part_id = CAST(:p AS uuid) ORDER BY supplier_sku"), {"s": sup, "p": pid})
    return {x[0]: x[1:] for x in r.fetchall()}


async def _run(db):
    await db.execute(sa.text("UPDATE parts_catalog SET base_price = 0 WHERE manufacturer LIKE 'zz-ebay-%'"))
    eb._EBAY_SYNC_LAST_ID = "00000000-0000-0000-0000-000000000000"
    return await eb.sync_ebay_prices(db, limit_per_run=5000)


async def test_exact_sku_row_is_the_offer_and_other_variants_are_untouched(db, monkeypatch):
    """Old index dropped (stage 3, transactional here): typed + untyped rows on the part; the row whose SKU equals the
    eBay OEM is THE offer. Only it is updated; its SKU is kept; the other variant's row is untouched."""
    sup, pid, oem = await _world(db, monkeypatch)
    await db.execute(sa.text("DROP INDEX IF EXISTS uq_supplier_parts_part_supplier"))
    other = await _offer(db, sup, pid, "OTHER-SKU", None, 77)                 # untyped, different SKU (older)
    mine = await _offer(db, sup, pid, oem, "original", 50)                     # typed, exact SKU
    await _run(db)
    rows = await _rows(db, sup, pid)
    assert len(rows) == 2 and rows[mine][0] == oem and rows[mine][2] != 50.0 and rows[other][2] == 77.0


async def test_without_sku_match_the_untyped_row_is_chosen_deterministically(db, monkeypatch):
    sup, pid, oem = await _world(db, monkeypatch)
    await db.execute(sa.text("DROP INDEX IF EXISTS uq_supplier_parts_part_supplier"))
    typed = await _offer(db, sup, pid, "T-SKU", "original", 50, age_s=100)    # older + typed
    untyped = await _offer(db, sup, pid, "U-SKU", None, 60, age_s=10)
    for _ in range(2):
        await _run(db)
    rows = await _rows(db, sup, pid)
    assert len(rows) == 2 and rows[typed][2] == 50.0 and rows[untyped][2] != 60.0
    assert rows[untyped][0] == "U-SKU"                                          # SKU never renamed onto the OEM


async def test_old_index_present_single_typed_row_is_updated_in_place(db, monkeypatch):
    sup, pid, oem = await _world(db, monkeypatch)
    typed = await _offer(db, sup, pid, "T-SKU", "original", 50)
    await _run(db)
    rows = await _rows(db, sup, pid)
    assert list(rows) == [typed] and rows[typed][0] == "T-SKU" and rows[typed][2] != 50.0


async def test_first_run_inserts_one_offer_and_repeat_is_idempotent(db, monkeypatch, mocks):
    sup, pid, oem = await _world(db, monkeypatch)
    await _run(db)
    first = await _rows(db, sup, pid)
    await _run(db)
    again = await _rows(db, sup, pid)
    assert len(first) == 1 and first == again
    (spid,) = first
    mocks.price = 12.0
    await _run(db)
    assert list(await _rows(db, sup, pid)) == [spid]
    hist = (await db.execute(sa.text("SELECT count(*) FROM price_history WHERE supplier_part_id = CAST(:i AS uuid)"), {"i": spid})).scalar()
    assert hist == 2        # first insert + exactly one price change
