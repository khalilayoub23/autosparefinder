"""Branch-level regression for the deployed (policy-bound) merge_master_parts conflict branch. Sandbox DB only; each scenario
runs in a transaction that is always rolled back."""
import asyncio
import importlib.util
import os
import uuid
from pathlib import Path

import asyncpg
import pytest

ROOT = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.skipif("sandbox" not in os.getenv("DATABASE_URL", ""), reason="sandbox DB only")


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class _Rollback(Exception):
    pass


async def _scenario(module, fn):
    c = await asyncpg.connect(os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://"))
    try:
        async with c.transaction():
            brand = await c.fetchrow("SELECT id, name FROM car_brands LIMIT 1")
            tag = uuid.uuid4().hex[:6]
            S, T = uuid.uuid4(), uuid.uuid4()
            for sid, nm in ((S, f"m-S-{tag}"), (T, f"m-T-{tag}")):
                await c.execute("INSERT INTO suppliers (id,name,country,is_active) VALUES ($1,$2,'IL',true)", sid, nm)
            canon, loser = uuid.uuid4(), uuid.uuid4()
            for pid, sku in ((canon, f"C-{tag}"), (loser, f"L-{tag}")):
                await c.execute("INSERT INTO parts_catalog (id,sku,name,manufacturer,manufacturer_id,oem_number,is_active,base_price,created_at,part_type)"
                                " VALUES ($1,$2,'n',$3,$4,$5,true,$6,NOW(),'oem')", pid, sku, brand["name"], brand["id"], f"OEM{tag}", 10 if pid == canon else None)
            o_can, o_los_s, o_los_t = (uuid.uuid4() for _ in range(3))
            for oid, sid, pid, sku, price, pt in ((o_can, S, canon, f"A-{tag}", 100, "oem"), (o_los_s, S, loser, f"B-{tag}", 80, "aftermarket"),
                                                  (o_los_t, T, loser, f"C-{tag}", 90, "oem")):
                await c.execute("INSERT INTO supplier_parts (id,supplier_id,part_id,supplier_sku,price_usd,price_ils,is_available,min_order_qty,part_type)"
                                " VALUES ($1,$2,$3,$4,1,$5,true,1,$6)", oid, sid, pid, sku, price, pt)
            await c.execute("INSERT INTO price_history (id,supplier_part_id,new_price_ils,new_price_usd) VALUES ($1,$2,80,1)", uuid.uuid4(), o_los_s)
            # the loser's own fitment (2 rows) + one row the master already has
            for pid, model in ((loser, "M1"), (loser, "M2"), (canon, "M1")):
                await c.execute("INSERT INTO part_vehicle_fitment (part_id, manufacturer, model, year_from, year_to) "
                                "VALUES ($1,$2,$3,2010,2015)", pid, brand["name"], model)
            rows = await module.fetch_group(c, [str(canon), str(loser)])
            await module.merge_group(c, rows, False)
            res = {
                "loser_fitment": await c.fetchval("SELECT count(*) FROM part_vehicle_fitment WHERE part_id=$1", loser),
                "canon_fitment": await c.fetchval("SELECT count(*) FROM part_vehicle_fitment WHERE part_id=$1", canon),
                "canon_price": await c.fetchval("SELECT price_ils::float FROM supplier_parts WHERE id=$1", o_can),
                "loser_variant_offer_exists": bool(await c.fetchval("SELECT count(*) FROM supplier_parts WHERE id=$1", o_los_s)),
                "history_rows": await c.fetchval("SELECT count(*) FROM price_history WHERE supplier_part_id=$1", o_los_s),
                "other_supplier_offer_part": await c.fetchval("SELECT part_id FROM supplier_parts WHERE id=$1", o_los_t),
                "loser_active": await c.fetchval("SELECT is_active FROM parts_catalog WHERE id=$1", loser),
                "canon": canon, "loser": loser,
            }
            await fn(res)
            raise _Rollback()
    except _Rollback:
        pass
    finally:
        await c.close()


def test_merge_keeps_variant_offer_history_and_price():
    async def check(r):
        assert r["loser_variant_offer_exists"] is True      # same supplier, other SKU/variant: never deleted
        assert r["history_rows"] == 1                       # price_history intact (no FK cascade)
        assert r["canon_price"] == 100.0                    # canonical price not overwritten without history
        assert r["other_supplier_offer_part"] == r["canon"]  # competing supplier's offer was moved, id preserved
        assert r["loser_active"] is True                    # loser still carries an offer -> not retired
        # 2026-11-14: an active (kept) loser KEEPS its fitment; the master gets a copy of what it lacked
        assert r["loser_fitment"] == 2 and r["canon_fitment"] == 2
    asyncio.run(_scenario(_load(ROOT / "maintenance" / "merge_master_parts.py", "mmp_live"), check))


def test_merge_never_touches_a_different_aftermarket_article():
    """REMSA 1187.02 vs TOPRAN 118 702: same string once punctuation is stripped, different parts (2026-11-14)."""
    mod = _load(ROOT / "maintenance" / "merge_master_parts.py", "mmp_distinct")

    async def run():
        c = await asyncpg.connect(os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://"))
        try:
            async with c.transaction():
                brand = await c.fetchrow("SELECT id, name FROM car_brands LIMIT 1")
                tag = uuid.uuid4().hex[:6].upper()
                S, T = uuid.uuid4(), uuid.uuid4()
                for sid, nm in ((S, f"d-S-{tag}"), (T, f"d-T-{tag}")):
                    await c.execute("INSERT INTO suppliers (id,name,country,is_active) VALUES ($1,$2,'IL',true)", sid, nm)
                a, b = uuid.uuid4(), uuid.uuid4()
                for pid, sku, name, oem, price in ((a, f"DA-{tag}", f"REMSA {tag}.02 Brake pad set", f"{tag}.02", 10),
                                                   (b, f"DB-{tag}", f"TOPRAN {tag} 02 Gear selector cable", f"{tag} 02", None)):
                    await c.execute("INSERT INTO parts_catalog (id,sku,name,manufacturer,manufacturer_id,oem_number,is_active,base_price,created_at,part_type)"
                                    " VALUES ($1,$2,$3,$4,$5,$6,true,$7,NOW(),'aftermarket')", pid, sku, name, brand["name"], brand["id"], oem, price)
                ob = uuid.uuid4()
                await c.execute("INSERT INTO supplier_parts (id,supplier_id,part_id,supplier_sku,price_usd,price_ils,is_available,min_order_qty)"
                                " VALUES ($1,$2,$3,$4,1,50,true,1)", ob, T, b, f"X-{tag}")
                await c.execute("INSERT INTO part_vehicle_fitment (part_id, manufacturer, model, year_from, year_to) VALUES ($1,$2,'Z',2010,2015)", b, brand["name"])
                st = await mod.merge_group(c, await mod.fetch_group(c, [str(a), str(b)]), False)
                assert st.get("skipped_distinct") == 2 and st["losers"] == 0      # both rows left untouched
                assert await c.fetchval("SELECT count(*) FROM parts_catalog WHERE id = ANY($1::uuid[]) AND is_active", [a, b]) == 2
                assert await c.fetchval("SELECT part_id FROM supplier_parts WHERE id=$1", ob) == b
                assert await c.fetchval("SELECT count(*) FROM part_vehicle_fitment WHERE part_id=$1", b) == 1
                assert await c.fetchval("SELECT count(*) FROM part_vehicle_fitment WHERE part_id=$1", a) == 0
                raise _Rollback()
        except _Rollback:
            pass
        finally:
            await c.close()
    asyncio.run(run())


def test_bmw_oem_dedup_keeps_conflicting_offer_and_history():
    """bmw_oem_dedup._merge_group (deployed): same-supplier conflicting offer is never deleted/price-overwritten, its
    price_history survives, the conflict-free competing offer is repointed, and the loser stays active."""
    mod = _load(ROOT / "maintenance" / "bmw_oem_dedup.py", "bmw_live")

    async def run():
        c = await asyncpg.connect(os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://"))
        try:
            async with c.transaction():
                brand = await c.fetchrow("SELECT id, name FROM car_brands LIMIT 1")
                tag = uuid.uuid4().hex[:6]
                S, T = uuid.uuid4(), uuid.uuid4()
                for sid, nm in ((S, f"b-S-{tag}"), (T, f"b-T-{tag}")):
                    await c.execute("INSERT INTO suppliers (id,name,country,is_active) VALUES ($1,$2,'IL',true)", sid, nm)
                canon, loser = uuid.uuid4(), uuid.uuid4()
                for pid, sku in ((canon, f"BC-{tag}"), (loser, f"BL-{tag}")):
                    await c.execute("INSERT INTO parts_catalog (id,sku,name,manufacturer,manufacturer_id,oem_number,is_active,created_at,part_type)"
                                    " VALUES ($1,$2,'n',$3,$4,$5,true,NOW(),'oem')", pid, sku, brand["name"], brand["id"], f"BOEM{tag}")
                o_can, o_los_s, o_los_t = (uuid.uuid4() for _ in range(3))
                for oid, sid, pid, sku, price, pt in ((o_can, S, canon, f"A-{tag}", 100, "oem"), (o_los_s, S, loser, f"B-{tag}", 80, "aftermarket"),
                                                      (o_los_t, T, loser, f"C-{tag}", 90, "oem")):
                    await c.execute("INSERT INTO supplier_parts (id,supplier_id,part_id,supplier_sku,price_usd,price_ils,is_available,min_order_qty,part_type)"
                                    " VALUES ($1,$2,$3,$4,1,$5,true,1,$6)", oid, sid, pid, sku, price, pt)
                await c.execute("INSERT INTO price_history (id,supplier_part_id,new_price_ils,new_price_usd) VALUES ($1,$2,80,1)", uuid.uuid4(), o_los_s)
                st = await mod._merge_group(c, str(canon), [str(loser)], False)
                assert await c.fetchval("SELECT price_ils::float FROM supplier_parts WHERE id=$1", o_can) == 100.0
                assert await c.fetchval("SELECT count(*) FROM supplier_parts WHERE id=$1", o_los_s) == 1
                assert await c.fetchval("SELECT count(*) FROM price_history WHERE supplier_part_id=$1", o_los_s) == 1
                assert await c.fetchval("SELECT part_id FROM supplier_parts WHERE id=$1", o_los_t) == canon
                assert await c.fetchval("SELECT is_active FROM parts_catalog WHERE id=$1", loser) is True
                assert st["sp_dropped"] == 0
                raise _Rollback()
        except _Rollback:
            pass
        finally:
            await c.close()
    asyncio.run(run())
