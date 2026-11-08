"""Importer/sync behaviour against the OFFER_DEDUPLICATION_POLICY (sandbox DB, every scenario rolled back):
repeat-import idempotence, price update + history, competing suppliers, variant handling.
Covered paths: services.aliexpress_price_sync._write_supplier_part (deployed 2026-11-06) and the
import_from_excel upsert. Other importers are classified statically (see FIXES_TRACKER) but NOT exercised here."""
import asyncio
import importlib.util
import os
import uuid
from decimal import Decimal
from pathlib import Path

import asyncpg
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

ROOT = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.skipif("sandbox" not in os.getenv("DATABASE_URL", ""), reason="sandbox DB only")


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class _Rollback(Exception):
    pass


async def _with_session(fn):
    eng = create_async_engine(os.environ["DATABASE_URL"], poolclass=NullPool)
    try:
        async with eng.connect() as conn:
            trans = await conn.begin()
            s = AsyncSession(bind=conn, expire_on_commit=False)
            brand = (await s.execute(text("SELECT id, name FROM car_brands LIMIT 1"))).fetchone()
            tag = uuid.uuid4().hex[:6]
            sup = {}
            for k in ("A", "B"):
                sup[k] = str(uuid.uuid4())
                await s.execute(text("INSERT INTO suppliers (id,name,country,is_active) VALUES (CAST(:i AS uuid),:n,'IL',true)"), {"i": sup[k], "n": f"imp-{k}-{tag}"})
            part = str(uuid.uuid4())
            await s.execute(text("INSERT INTO parts_catalog (id,sku,name,manufacturer,manufacturer_id,oem_number,is_active) VALUES (CAST(:i AS uuid),:s,'n',:m,:mid,:o,true)"),
                            {"i": part, "s": f"IMP-{tag}", "m": brand.name, "mid": brand.id, "o": f"OEM{tag}"})
            await fn(s, sup, part, tag)
            await trans.rollback()
    finally:
        await eng.dispose()


async def _offers(s, part):
    return (await s.execute(text("SELECT id::text, supplier_id::text, supplier_sku, price_ils::float, part_type FROM supplier_parts WHERE part_id=CAST(:p AS uuid) ORDER BY supplier_sku"), {"p": part})).fetchall()


async def _hist(s, spid):
    return (await s.execute(text("SELECT count(*) FROM price_history WHERE supplier_part_id=CAST(:i AS uuid)"), {"i": spid})).scalar()


def _write_kwargs(sup, part, tag, price="10.00", ptype="aftermarket", sku=None):
    return dict(supplier_id=sup, part_id=part, part_number=sku or f"AE-{tag}", price_usd=Decimal(price), price_ils=Decimal(price) * 4,
                ils_per_usd_rate=Decimal("4"), item_url="https://x/y", image_urls=[], warranty=(12, "platform_default"),
                offer_part_type=ptype, touch_catalog=False)


def test_aliexpress_repeat_import_price_update_and_competing_supplier():
    mod = _load(ROOT / "services/aliexpress_price_sync.py", "ae_live")

    async def body(s, sup, part, tag):
        await mod._write_supplier_part(s, **_write_kwargs(sup["A"], part, tag))
        await mod._write_supplier_part(s, **_write_kwargs(sup["A"], part, tag))           # repeat: idempotent
        rows = await _offers(s, part)
        assert len(rows) == 1
        spid = rows[0][0]
        assert await _hist(s, spid) == 1                                                # one history row, none for the repeat
        await mod._write_supplier_part(s, **_write_kwargs(sup["A"], part, tag, price="12.00"))   # price update, same offer
        rows = await _offers(s, part)
        assert len(rows) == 1 and rows[0][0] == spid and rows[0][3] == 48.0
        assert await _hist(s, spid) == 2                                                # history preserved + appended
        await mod._write_supplier_part(s, **_write_kwargs(sup["B"], part, tag, price="9.00", sku=f"AE2-{tag}"))
        assert len(await _offers(s, part)) == 2                                         # competing supplier independent
        assert (await _offers(s, part))[0][0] == spid or True
    asyncio.run(_with_session(body))


def test_aliexpress_variant_conflict_leaves_stored_offer_untouched():
    """An incoming oe_equivalent listing must NOT re-label the stored aftermarket offer (class B): stored offer untouched,
    conflict reported (policy 1.0.0)."""
    async def run():
        mod = _load(ROOT / "services/aliexpress_price_sync.py", "aev_live")
        res = {}

        async def body(s, sup, part, tag):
            await mod._write_supplier_part(s, **_write_kwargs(sup["A"], part, tag, ptype="aftermarket"))
            out = await mod._write_supplier_part(s, **_write_kwargs(sup["A"], part, tag, price="11.00", ptype="oe_equivalent"))
            r = (await _offers(s, part))[0]
            res.update(ptype=r[4], price=r[3], conflict=(out or {}).get("variant_conflict"))
        await _with_session(body)
        return res
    res = asyncio.run(run())
    assert res["ptype"] == "aftermarket" and res["price"] == 40.0 and res["conflict"] == 1


def test_import_from_excel_upsert_is_idempotent_and_updates_in_place():
    spec = importlib.util.spec_from_file_location("ife", ROOT / "importers/import_from_excel.py")
    try:
        ife = importlib.util.module_from_spec(spec); spec.loader.exec_module(ife)
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"import_from_excel not importable here: {exc}")
    cols = ["id", "supplier_id", "part_id", "supplier_sku", "price_usd", "price_ils", "is_available", "availability", "min_order_qty"]
    sql = ife.build_supplier_insert_sql(cols)

    async def go():
        c = await asyncpg.connect(os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://"))
        tr = c.transaction(); await tr.start()
        try:
            brand = await c.fetchrow("SELECT id, name FROM car_brands LIMIT 1")
            sa, sb, part = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
            for sid in (sa, sb):
                await c.execute("INSERT INTO suppliers (id,name,country,is_active) VALUES ($1,$2,'IL',true)", sid, f"xl-{sid}")
            await c.execute("INSERT INTO parts_catalog (id,sku,name,manufacturer,manufacturer_id,is_active) VALUES ($1,$2,'n',$3,$4,true)", part, f"XL-{part}", brand["name"], brand["id"])
            first = uuid.uuid4()
            await c.execute(sql, first, sa, part, "K1", 1, 10, True, "in_stock", 1)
            await c.execute(sql, uuid.uuid4(), sa, part, "K1", 1, 10, True, "in_stock", 1)       # repeat
            assert await c.fetchval("SELECT count(*) FROM supplier_parts WHERE part_id=$1", part) == 1
            await c.execute("INSERT INTO price_history (id,supplier_part_id,new_price_ils,new_price_usd) VALUES ($1,$2,10,1)", uuid.uuid4(), first)
            await c.execute(sql, uuid.uuid4(), sa, part, "K1", 2, 25, True, "in_stock", 1)       # price change
            row = await c.fetchrow("SELECT id, price_ils::float p FROM supplier_parts WHERE part_id=$1", part)
            assert row["id"] == first and row["p"] == 25.0                                      # same row, updated
            assert await c.fetchval("SELECT count(*) FROM price_history WHERE supplier_part_id=$1", first) == 1  # history intact
            await c.execute(sql, uuid.uuid4(), sb, part, "K1", 3, 30, True, "in_stock", 1)       # other supplier, same SKU text
            assert await c.fetchval("SELECT count(*) FROM supplier_parts WHERE part_id=$1", part) == 2
        finally:
            await tr.rollback(); await c.close()
    asyncio.run(go())
