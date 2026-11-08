"""AliExpress _write_supplier_part: the offer label is canonicalised by variant_identity BEFORE any comparison/write; a label the
shared function cannot place fails closed (nothing written, reported). Isolated DB only; rolled back."""
import uuid
from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

import services.aliexpress_price_sync as svc
from BACKEND_DATABASE_MODELS import engine
import isolated_db_guard  # noqa: E402


@pytest.fixture
async def db():
    _r = isolated_db_guard.static_refusal()
    if _r is not None:
        pytest.skip(f"isolated DB only: {_r}")
    eng = create_async_engine(engine.url, poolclass=NullPool)
    try:
        conn = await eng.connect()
    except Exception as exc:  # pragma: no cover
        await eng.dispose()
        pytest.skip(f"database unreachable: {type(exc).__name__}")
    trans = await conn.begin()
    s = AsyncSession(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False)
    try:
        _l = await isolated_db_guard.live_refusal(s)
        if _l is not None:
            pytest.fail(f"REFUSED before any write: {_l}")
        yield s
    finally:
        await s.close()
        await trans.rollback()
        await conn.close()
        await eng.dispose()


async def _world(db):
    tag = uuid.uuid4().hex[:8]
    bid = (await db.execute(sa.text("INSERT INTO car_brands (id,name) VALUES (gen_random_uuid(),:n) RETURNING id"), {"n": f"zz-ali-{tag}"})).scalar_one()
    sup = (await db.execute(sa.text("INSERT INTO suppliers (id,name,country,is_active) VALUES (gen_random_uuid(),:n,'CN',TRUE) RETURNING id::text"), {"n": f"zz-ali-sup-{tag}"})).scalar_one()
    pid = (await db.execute(sa.text("INSERT INTO parts_catalog (id,sku,name,manufacturer_id,manufacturer,oem_number,is_active) VALUES (gen_random_uuid(),:s,'n',:b,:m,:o,TRUE) RETURNING id::text"),
                            {"s": f"ALV-{tag}", "b": bid, "m": f"zz-ali-{tag}", "o": f"ALV{tag}"})).scalar_one()
    return sup, pid, tag


async def _write(db, sup, pid, tag, label, price="10"):
    return await svc._write_supplier_part(
        db, supplier_id=sup, part_id=pid, part_number=f"ALV{tag}", price_usd=Decimal(price), price_ils=Decimal(price) * Decimal("3.7"),
        ils_per_usd_rate=Decimal("3.7"), item_url="https://a/1", image_urls=[], warranty=(12, "platform_default"),
        offer_part_type=label, touch_catalog=False)


async def _rows(db, sup, pid):
    return (await db.execute(sa.text("SELECT part_type FROM supplier_parts WHERE supplier_id=CAST(:s AS uuid) AND part_id=CAST(:p AS uuid)"), {"s": sup, "p": pid})).fetchall()


async def test_unknown_label_is_rejected_with_nothing_written(db):
    sup, pid, tag = await _world(db)
    out = await _write(db, sup, pid, tag, "totally-unknown-label")
    assert out.get("variant_label_rejected") == 1 and out["writes"] == 0 and out["history"] == 0
    assert await _rows(db, sup, pid) == []


async def test_known_label_is_stored_canonicalised_and_repeat_is_idempotent(db):
    sup, pid, tag = await _world(db)
    out = await _write(db, sup, pid, tag, "aftermarket")
    assert not out.get("variant_label_rejected") and out["history"] == 1
    assert [r[0] for r in await _rows(db, sup, pid)] == ["aftermarket"]
    out2 = await _write(db, sup, pid, tag, "aftermarket")
    assert out2["history"] == 0 and len(await _rows(db, sup, pid)) == 1


async def test_missing_label_stays_untyped(db):
    sup, pid, tag = await _world(db)
    out = await _write(db, sup, pid, tag, None)
    assert not out.get("variant_label_rejected")
    assert [r[0] for r in await _rows(db, sup, pid)] == [None]
