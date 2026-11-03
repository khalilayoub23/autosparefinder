"""Old-vs-new behaviour of the corrected base_price writers (isolated sandbox DB, everything rolled back).
Runs unchanged against the BASELINE tree and the CANDIDATE tree and prints one JSON object.
usage (sandbox container, tree mounted at /app): python3 old_vs_new_writers.py"""
import asyncio, inspect, io, json, os, sys, uuid
sys.path.insert(0, "/app"); sys.path.insert(0, "/app/tests")
import asyncpg, sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool
import isolated_db_guard

URL = os.environ["DATABASE_URL"]
assert isolated_db_guard.url_refusal(URL) is None, "isolated DB only"
out = {}


async def main():
    eng = create_async_engine(URL, poolclass=NullPool)
    conn = await eng.connect()
    trans = await conn.begin()
    s = AsyncSession(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False)
    await s.execute(sa.text("INSERT INTO car_brands (id, name) VALUES (gen_random_uuid(), 'BMW') ON CONFLICT DO NOTHING"))
    await s.commit()
    # (a) catalog_scraper.db_upsert_part --------------------------------------------------------------------------
    import catalog_scraper as cs

    async def no_meili(*a, **k): return None
    cs._meili_sync_part = no_meili
    new_api = "cost_ils" in inspect.signature(cs.db_upsert_part).parameters
    sku = f"EV-{uuid.uuid4().hex[:8]}"
    kw = dict(sku=sku, name="pad", manufacturer="BMW", category="brakes")
    try:
        if new_api:
            await cs.db_upsert_part(s, cost_ils=10.0 * cs.ILS_PER_USD, **kw)
        else:
            await cs.db_upsert_part(s, base_price=10.0, **kw)         # what the caller passed: part["price_usd"]
        first = (await s.execute(sa.text("SELECT base_price FROM parts_catalog WHERE sku=:s"), {"s": sku})).scalar()
        await s.execute(sa.text("UPDATE parts_catalog SET base_price = 77.77 WHERE sku=:s"), {"s": sku})
        await s.commit()
        if new_api:
            await cs.db_upsert_part(s, cost_ils=999.0, **kw)
        else:
            await cs.db_upsert_part(s, base_price=999.0, **kw)
        second = (await s.execute(sa.text("SELECT base_price FROM parts_catalog WHERE sku=:s"), {"s": sku})).scalar()
        out["catalog_scraper.db_upsert_part"] = {"new_part_from_USD_10": float(first), "owned_77.77_after_rediscovery_at_999": float(second)}
    except Exception as exc:
        await s.rollback()
        out["catalog_scraper.db_upsert_part"] = {"error": f"{type(exc).__name__}: {str(exc)[:120]}"}
    # (b) admin upload ----------------------------------------------------------------------------------------------
    from routes.admin import import_parts_excel
    import openpyxl
    tag = uuid.uuid4().hex[:8]
    await s.execute(sa.text("INSERT INTO parts_catalog (id, sku, name, manufacturer, part_type, is_active, base_price) VALUES (gen_random_uuid(), :s, 'old', 'BMW', 'oem', TRUE, 88.0)"), {"s": f"EVA-{tag}-old"})
    await s.commit()
    wb = openpyxl.Workbook(); ws = wb.active
    for r in (["sku", "name", "price", "manufacturer"], [f"EVA-{tag}-old", "renamed", 5, "BMW"], [f"EVA-{tag}-new", "fresh", 7, "BMW"]): ws.append(r)
    buf = io.BytesIO(); wb.save(buf)

    class Up:
        filename = "p.xlsx"
        async def read(self): return buf.getvalue()
    await import_parts_excel(Up(), None, s)
    g = lambda k: (lambda v: None if v is None else float(v))((s.sync_session if False else None)) 
    rows = {r[0]: r[1] for r in (await s.execute(sa.text("SELECT sku, base_price FROM parts_catalog WHERE sku LIKE :p"), {"p": f"EVA-{tag}-%"})).fetchall()}
    out["routes.admin.import_parts_excel"] = {"existing_88_after_upload_price_5": float(rows[f"EVA-{tag}-old"]),
                                              "new_part_uploaded_price_7": None if rows[f"EVA-{tag}-new"] is None else float(rows[f"EVA-{tag}-new"])}
    await s.close(); await trans.rollback(); await conn.close(); await eng.dispose()
    # (c) ai_catalog_builder.insert_parts ---------------------------------------------------------------------------
    import ai_catalog_builder as acb
    from datetime import datetime
    c = await asyncpg.connect(URL.replace("postgresql+asyncpg://", "postgresql://")); tr = c.transaction(); await tr.start()
    sup = await c.fetchval("INSERT INTO suppliers (id,name,country,is_active) VALUES (gen_random_uuid(),$1,'IL',TRUE) RETURNING id", f"AI {tag}")
    brand = f"zzb{tag}"; await c.execute("INSERT INTO car_brands (id,name) VALUES (gen_random_uuid(),$1)", brand)
    part = {"name_he": "רפידות", "name_en": "Pads", "catalog_num": f"X{tag}", "price_ils": 400, "category": "brakes"}
    await acb.insert_parts(c, sup, brand, [part], set(), False, datetime.utcnow())
    sk = acb.make_sku(brand, f"X{tag}", 0)
    ins = await c.fetchval("SELECT base_price FROM parts_catalog WHERE sku=$1", sk)
    await c.execute("UPDATE parts_catalog SET base_price = 123.45 WHERE sku=$1", sk)
    await acb.insert_parts(c, sup, brand, [{**part, "price_ils": 9999}], set(), False, datetime.utcnow())
    upd = await c.fetchval("SELECT base_price FROM parts_catalog WHERE sku=$1", sk)
    out["ai_catalog_builder.insert_parts"] = {"inserted_with_LLM_price_400": None if ins is None else float(ins), "owned_123.45_after_rebuild_at_9999": float(upd)}
    # (d) car_parts_ie_import_generic conflict clause ----------------------------------------------------------------
    src = open("/app/importers/car_parts_ie_import_generic.py").read()
    end = src.index("RETURNING id, (xmax = 0) AS is_insert") + len("RETURNING id, (xmax = 0) AS is_insert")
    sql = src[src.rindex("INSERT INTO parts_catalog(", 0, end):end]
    bid = await c.fetchval("INSERT INTO car_brands (id,name) VALUES (gen_random_uuid(),$1) RETURNING id", f"zzc{tag}")
    skc = f"CP-{tag}"
    row = await c.fetchrow(sql, skc, skc, "pad", "Aftermarket", bid, "brakes", "{}", "[]", 10.0, 10.0, 14.5, "d", False)
    await c.execute("UPDATE parts_catalog SET base_price = 33.0 WHERE id=$1", row["id"])
    await c.fetchrow(sql, skc, skc, "pad", "Aftermarket", bid, "brakes", "{}", "[]", 50.0, 50.0, 72.5, "d", False)
    out["car_parts_ie_import_generic (re-harvest)"] = {"owned_33.0_after_reharvest_at_72.5": float(await c.fetchval("SELECT base_price FROM parts_catalog WHERE id=$1", row["id"]))}
    await tr.rollback(); await c.close()
    print(json.dumps(out, indent=1, ensure_ascii=False))

asyncio.run(main())
