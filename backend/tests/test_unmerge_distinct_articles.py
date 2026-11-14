"""Undo of a wrong merge of two different aftermarket articles (2026-11-14). Sandbox DB, everything rolled back."""
import asyncio
import importlib.util
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import asyncpg
import pytest

ROOT = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.skipif(os.getenv("ENVIRONMENT") != "sandbox" or not os.getenv("DATABASE_URL"),
                                reason="needs the sandbox database")


def _load():
    spec = importlib.util.spec_from_file_location("unmerge_da", ROOT / "maintenance" / "unmerge_distinct_articles.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class _Rollback(Exception):
    pass


def test_wrong_merge_is_planned_and_undone_exactly():
    mod = _load()

    async def run():
        c = await asyncpg.connect(os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://"), statement_cache_size=0)
        try:
            async with c.transaction():
                brand = await c.fetchrow("SELECT id, name FROM car_brands LIMIT 1")
                tag = uuid.uuid4().hex[:6].upper()
                S = uuid.uuid4()
                await c.execute("INSERT INTO suppliers (id,name,country,is_active) VALUES ($1,$2,'IE',true)", S, f"u-S-{tag}")
                loser, master = uuid.uuid4(), uuid.uuid4()
                t_l = datetime(2026, 6, 1, 10, 0)
                t_m = t_l + timedelta(days=30)
                await c.execute("INSERT INTO parts_catalog (id,sku,name,manufacturer,manufacturer_id,oem_number,is_active,created_at,description,specifications,part_type)"
                                " VALUES ($1,$2,$3,$4,$5,$6,false,$7,'pads desc',$8::jsonb,'aftermarket')", loser, f"UL-{tag}", f"REMSA {tag}.02 Brake pad set",
                                brand["name"], brand["id"], f"{tag}.02", t_l, '{"dedup_merged_into": "%s"}' % master)
                await c.execute("INSERT INTO parts_catalog (id,sku,name,manufacturer,manufacturer_id,oem_number,is_active,created_at,description,specifications,part_type)"
                                " VALUES ($1,$2,$3,$4,$5,$6,true,$7,'pads desc',$8::jsonb,'aftermarket')", master, f"UM-{tag}", f"TOPRAN {tag} 02 Gear selector cable",
                                brand["name"], brand["id"], f"{tag} 02", t_m, '{"merged_from": ["%s"]}' % loser)
                o_l, o_m = uuid.uuid4(), uuid.uuid4()
                for oid, sku, price in ((o_l, f"{tag}.02", 40), (o_m, f"{tag} 02", 90)):
                    await c.execute("INSERT INTO supplier_parts (id,supplier_id,part_id,supplier_sku,price_usd,price_ils,is_available,min_order_qty)"
                                    " VALUES ($1,$2,$3,$4,1,$5,true,1)", oid, S, master, sku, price)
                f_l, f_m = uuid.uuid4(), uuid.uuid4()
                for fid, model, ts in ((f_l, "LoserCar", t_l), (f_m, "MasterCar", t_m)):
                    await c.execute("INSERT INTO part_vehicle_fitment (id, part_id, manufacturer, model, year_from, year_to, created_at) "
                                    "VALUES ($1,$2,$3,$4,2010,2015,$5)", fid, master, brand["name"], model, ts)

                plan = await mod.build_pair_plan(c, loser, master)
                assert plan and plan["reactivate"] is True
                assert plan["offers_move"] == [str(o_l)] and plan["fitment_move"] == [str(f_l)]
                assert plan["master_fields_clear"] == ["description"]
                assert plan["unattributed"]["master_fitment_rows_left"] == 1

                prev = await mod.apply_pair(c, plan)
                assert prev["offers_moved"] == 1 and prev["fitment_moved"] == 1
                assert await c.fetchval("SELECT part_id FROM supplier_parts WHERE id=$1", o_l) == loser
                assert await c.fetchval("SELECT part_id FROM supplier_parts WHERE id=$1", o_m) == master
                assert await c.fetchval("SELECT price_ils::float FROM supplier_parts WHERE id=$1", o_l) == 40.0
                assert await c.fetchval("SELECT part_id FROM part_vehicle_fitment WHERE id=$1", f_l) == loser
                assert await c.fetchval("SELECT part_id FROM part_vehicle_fitment WHERE id=$1", f_m) == master
                lrow = await c.fetchrow("SELECT is_active, specifications ? 'dedup_merged_into' gone, specifications->>'unmerged_from' uf FROM parts_catalog WHERE id=$1", loser)
                assert lrow["is_active"] is True and lrow["gone"] is False and lrow["uf"] == str(master)
                mrow = await c.fetchrow("SELECT description, specifications->'merged_from' mf FROM parts_catalog WHERE id=$1", master)
                assert mrow["description"] is None and mrow["mf"] in ("[]", [])
                # idempotent: the pair is no longer a recorded wrong merge
                assert await mod.build_pair_plan(c, loser, master) is None
                raise _Rollback()
        except _Rollback:
            pass
        finally:
            await c.close()
    asyncio.run(run())


def test_same_article_pair_is_never_planned():
    mod = _load()

    async def run():
        c = await asyncpg.connect(os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://"), statement_cache_size=0)
        try:
            async with c.transaction():
                brand = await c.fetchrow("SELECT id, name FROM car_brands LIMIT 1")
                tag = uuid.uuid4().hex[:6].upper()
                loser, master = uuid.uuid4(), uuid.uuid4()
                for pid, sku, oem, active, spec in ((loser, f"SL-{tag}", f"06Q{tag}H", False, '{"dedup_merged_into": "%s"}' % master),
                                                    (master, f"SM-{tag}", f"06Q-{tag}-H", True, '{"merged_from": ["%s"]}' % loser)):
                    await c.execute("INSERT INTO parts_catalog (id,sku,name,manufacturer,manufacturer_id,oem_number,is_active,created_at,specifications,part_type)"
                                    " VALUES ($1,$2,'Oil separator',$3,$4,$5,$6,NOW(),$7::jsonb,'oem')", pid, sku, brand["name"], brand["id"], oem, active, spec)
                assert await mod.build_pair_plan(c, loser, master) is None
                raise _Rollback()
        except _Rollback:
            pass
        finally:
            await c.close()
    asyncio.run(run())
