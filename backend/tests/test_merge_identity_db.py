"""merge_master_parts / bmw_oem_dedup / unmerge against a real database (sandbox only; every scenario is rolled back):
only PROVEN duplicates are merged, uncertain rows are untouched, nothing is lost in a merge -> unmerge cycle."""
import asyncio
import importlib.util
import json
import os
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import asyncpg
import pytest

pytestmark = pytest.mark.skipif(os.getenv("ENVIRONMENT") != "sandbox" or not os.getenv("DATABASE_URL"),
                                reason="sandbox database only")
ROOT = Path(__file__).resolve().parent.parent


class _Rollback(Exception):
    pass


def _load(name):
    spec = importlib.util.spec_from_file_location("mid_" + name, ROOT / "maintenance" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def _part(c, brand, name, oem, pt, spec=None, created=None, active=True, base=None):
    pid = uuid.uuid4()
    await c.execute("INSERT INTO parts_catalog (id,sku,name,manufacturer,manufacturer_id,oem_number,part_type,is_active,base_price,"
                    "created_at,specifications) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::jsonb)", pid, "T-" + uuid.uuid4().hex[:10],
                    name, brand["name"], brand["id"], oem, pt, active, base, created or datetime(2026, 6, 1), json.dumps(spec or {}))
    return pid


async def _offer(c, sup, pid, sku, price):
    oid = uuid.uuid4()
    await c.execute("INSERT INTO supplier_parts (id,supplier_id,part_id,supplier_sku,price_usd,price_ils,is_available,min_order_qty)"
                    " VALUES ($1,$2,$3,$4,1,$5,true,1)", oid, sup, pid, sku, price)
    return oid


def _run(scenario):
    async def go():
        c = await asyncpg.connect(os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://"), statement_cache_size=0)
        try:
            async with c.transaction():
                brand = await c.fetchrow("SELECT id, name FROM car_brands LIMIT 1")
                tag = uuid.uuid4().hex[:6].upper()
                sup = uuid.uuid4()
                await c.execute("INSERT INTO suppliers (id,name,country,is_active) VALUES ($1,$2,'IE',true)", sup, f"mid-{tag}")
                await scenario(c, brand, tag, sup)
                raise _Rollback()
        except _Rollback:
            pass
        finally:
            await c.close()
    asyncio.run(go())


async def _state(c, ids):
    return {"parts": [tuple(r) for r in await c.fetch("SELECT id, is_active, specifications::text, base_price, min_price_ils, max_price_ils "
                                                      "FROM parts_catalog WHERE id = ANY($1::uuid[]) ORDER BY id", ids)],
            "offers": [tuple(r) for r in await c.fetch("SELECT id, part_id, price_ils FROM supplier_parts WHERE part_id = ANY($1::uuid[]) ORDER BY id", ids)],
            "fit": [tuple(r) for r in await c.fetch("SELECT part_id, model FROM part_vehicle_fitment WHERE part_id = ANY($1::uuid[]) ORDER BY 1, 2", ids)],
            "img": [tuple(r) for r in await c.fetch("SELECT part_id, url FROM parts_images WHERE part_id = ANY($1::uuid[]) ORDER BY 1, 2", ids)]}


def test_mixed_group_merges_only_the_proven_twins_and_touches_nothing_else():
    mmp = _load("merge_master_parts")

    async def scenario(c, brand, tag, sup):
        k2a = await _part(c, brand, f"K2 {tag} Brake Fluid", f"Article №: {tag}", "aftermarket", {"source": "Car-Parts.ie", "product_url": "u-a"}, base=10)
        k2b = await _part(c, brand, f"K2 {tag} Brake Fluid Capacity: 0,5l", f"Article №: {tag}", "aftermarket", {"image_url": "img-b"})
        tow = await _part(c, brand, f"STEINHOF {tag[:3]}-{tag[3:]} Towbar", f"Article №: {tag[:3]}-{tag[3:]}", "aftermarket")
        veh = await _part(c, brand, f"Toyota Yaris towbar {tag}", tag, "aftermarket")              # vehicle-first, no brand
        typ = await _part(c, brand, f"K2 {tag} Brake Fluid", f"Article №: {tag}", None)              # missing product type
        o_tow, o_veh = await _offer(c, sup, tow, f"TOW-{tag}", 500), None
        await c.execute("INSERT INTO part_vehicle_fitment (part_id, manufacturer, model, year_from, year_to) VALUES ($1,$2,'TowCar',2010,2015)", tow, brand["name"])
        await c.execute("INSERT INTO part_vehicle_fitment (part_id, manufacturer, model, year_from, year_to) VALUES ($1,$2,'VehCar',2010,2015)", veh, brand["name"])
        ids = [k2a, k2b, tow, veh, typ]
        untouched_before = await _state(c, [tow, veh, typ])

        dry = await mmp.merge_group(c, await mmp.fetch_group(c, [str(i) for i in ids]), True)
        assert dry["losers"] == 1 and await c.fetchval("SELECT count(*) FROM parts_catalog WHERE id = ANY($1::uuid[]) AND is_active", ids) == 5

        st = await mmp.merge_group(c, await mmp.fetch_group(c, [str(i) for i in ids]), False)
        assert st["losers"] == 1 and st["skipped_distinct"] == 1 and st["skipped_uncertain"] == 2
        assert await _state(c, [tow, veh, typ]) == untouched_before                                  # different + uncertain: byte-identical
        assert await c.fetchval("SELECT part_id FROM supplier_parts WHERE id=$1", o_tow) == tow
        active = {r["id"] for r in await c.fetch("SELECT id FROM parts_catalog WHERE id = ANY($1::uuid[]) AND is_active", [k2a, k2b])}
        assert active == {k2a}                                                                       # the twins became one master
        spec = json.loads(await c.fetchval("SELECT specifications::text FROM parts_catalog WHERE id=$1", k2a))
        # the master's own specifications survive and the loser's are unioned in (was: replaced by {"merged_from": [...]})
        assert spec["source"] == "Car-Parts.ie" and spec["product_url"] == "u-a" and spec["image_url"] == "img-b"
        assert spec["merged_from"] == [str(k2b)] and "dedup_merged_into" not in spec

        again = await mmp.merge_group(c, await mmp.fetch_group(c, [str(i) for i in (k2a, tow, veh, typ)]), False)
        assert again.get("losers", 0) == 0                                                           # rerun on merged data: nothing to do
    _run(scenario)


def test_merged_from_accumulates_and_loser_markers_do_not_leak_onto_the_master():
    mmp = _load("merge_master_parts")

    async def scenario(c, brand, tag, sup):
        old = str(uuid.uuid4())
        a = await _part(c, brand, "מפריד שמן", f"06Q{tag}H", "oem", {"merged_from": [old], "source": "importer"}, base=10)
        b = await _part(c, brand, "Oil separator", f"06Q-{tag}-H", "oem", {"unmerged_from": "x", "unmerged_at": "t", "oem_ref": "r"})
        st = await mmp.merge_group(c, await mmp.fetch_group(c, [str(a), str(b)]), False)
        assert st["losers"] == 1
        spec = json.loads(await c.fetchval("SELECT specifications::text FROM parts_catalog WHERE id=$1", a))
        assert spec["merged_from"] == [old, str(b)] and spec["source"] == "importer" and spec["oem_ref"] == "r"
        assert "unmerged_from" not in spec and "unmerged_at" not in spec
        lspec = json.loads(await c.fetchval("SELECT specifications::text FROM parts_catalog WHERE id=$1", b))
        assert lspec["dedup_merged_into"] == str(a) and lspec["unmerged_from"] == "x"                # the loser keeps its own history
    _run(scenario)


@pytest.mark.parametrize("names,pt", [(("Heater Hose", "Heater Hose"), "aftermarket"),                       # unbranded
                                      (("Opel Corsa D brake hose", "BMW Z8 E52 Tailgate strut"), "aftermarket"),  # vehicle-first
                                      (("צינור בלם", "צינור בלם A75"), "aftermarket"),                        # Hebrew unbranded
                                      (("K2 {t} Brake Fluid", "K2 {t} Brake Fluid"), None)])                 # missing type
def test_uncertain_identity_is_never_merged(names, pt):
    mmp, bmw = _load("merge_master_parts"), _load("bmw_oem_dedup")

    async def scenario(c, brand, tag, sup):
        a = await _part(c, brand, names[0].format(t=tag), tag, pt, base=10)
        b = await _part(c, brand, names[1].format(t=tag), tag, pt)
        o = await _offer(c, sup, b, f"O-{tag}", 70)
        before = await _state(c, [a, b])
        st = await mmp.merge_group(c, await mmp.fetch_group(c, [str(a), str(b)]), False)
        assert st["losers"] == 0 and st.get("skipped_uncertain") == 2                   # both rows left untouched
        st2 = await bmw._merge_group(c, str(a), [str(b)], False)
        assert st2["losers"] == 0 and st2["skipped_identity"] == 1
        assert await _state(c, [a, b]) == before and await c.fetchval("SELECT part_id FROM supplier_parts WHERE id=$1", o) == b
    _run(scenario)


def test_same_supplier_same_sku_different_price_is_not_collapsed():
    """Two rows of one article whose offers come from the SAME supplier at different prices: the offer policy keeps both
    offers (no price overwrite, no delete) and the loser part stays active."""
    mmp = _load("merge_master_parts")

    async def scenario(c, brand, tag, sup):
        a = await _part(c, brand, f"K2 {tag} Brake Fluid", tag, "aftermarket", base=10)
        b = await _part(c, brand, f"K2 {tag} Brake Fluid", tag, "aftermarket")
        oa, ob = await _offer(c, sup, a, f"{tag}", 100), await _offer(c, sup, b, f"{tag}-B", 80)
        st = await mmp.merge_group(c, await mmp.fetch_group(c, [str(a), str(b)]), False)
        assert st.get("sp_kept_policy") == 1
        rows = {r["id"]: (r["part_id"], float(r["price_ils"])) for r in await c.fetch("SELECT id, part_id, price_ils FROM supplier_parts WHERE id = ANY($1::uuid[])", [oa, ob])}
        assert rows == {oa: (a, 100.0), ob: (b, 80.0)}
        assert await c.fetchval("SELECT count(*) FROM parts_catalog WHERE id = ANY($1::uuid[]) AND is_active", [a, b]) == 2
    _run(scenario)


def test_wrong_merge_then_unmerge_restores_offers_fitment_images_and_prices_and_is_repeatable():
    um = _load("unmerge_distinct_articles")

    async def scenario(c, brand, tag, sup):
        t_l = datetime(2026, 6, 1, 10, 0)
        t_m = t_l + timedelta(days=30)
        master = await _part(c, brand, f"TOPRAN {tag} 02 Gear selector cable", f"{tag} 02", "aftermarket", created=t_m, base=90)
        loser = await _part(c, brand, f"REMSA {tag}.02 Brake pad set", f"{tag}.02", "aftermarket", {"dedup_merged_into": str(master)},
                            created=t_l, active=False)
        await c.execute("UPDATE parts_catalog SET specifications = $2::jsonb WHERE id=$1", master, json.dumps({"merged_from": [str(loser)]}))
        o_l, o_m = await _offer(c, sup, master, f"{tag}.02", 40), await _offer(c, sup, master, f"{tag} 02", 90)
        for model, ts in (("LoserCar", t_l), ("MasterCar", t_m)):
            await c.execute("INSERT INTO part_vehicle_fitment (part_id, manufacturer, model, year_from, year_to, created_at) VALUES ($1,$2,$3,2010,2015,$4)",
                            master, brand["name"], model, ts)
        for url, ts in (("loser.jpg", t_l), ("master.jpg", t_m)):
            await c.execute("INSERT INTO parts_images (id, part_id, url, is_primary, created_at) VALUES ($1,$2,$3,false,$4)", uuid.uuid4(), master, url, ts)
        plan = await um.build_pair_plan(c, loser, master)
        assert plan and plan["offers_move"] == [str(o_l)]
        await um.apply_pair(c, plan)
        after = await _state(c, [loser, master])
        assert (loser, "LoserCar") in after["fit"] and (master, "MasterCar") in after["fit"] and len(after["fit"]) == 2
        assert (loser, "loser.jpg") in after["img"] and (master, "master.jpg") in after["img"]
        assert {(o[1], float(o[2])) for o in after["offers"]} == {(loser, 40.0), (master, 90.0)}     # moved back, prices untouched
        assert await c.fetchval("SELECT is_active FROM parts_catalog WHERE id=$1", loser) is True
        assert await um.build_pair_plan(c, loser, master) is None                                    # repeat: nothing left to do
        assert await _state(c, [loser, master]) == after
    _run(scenario)


def test_unmerge_never_plans_an_uncertain_or_same_pair():
    um = _load("unmerge_distinct_articles")

    async def scenario(c, brand, tag, sup):
        for names, pts in ((("Connector", "Connector"), ("oem", "oe_equivalent")),                   # label conflict: not proof
                           ((f"BREMBO {tag}.11 Brake disc", f"BREMBO {tag}11 Brake disc"), ("aftermarket", "aftermarket")),
                           (("Heater Hose", "Heater Hose"), ("aftermarket", "aftermarket"))):
            master = await _part(c, brand, names[1], f"{tag}11", pts[1])
            loser = await _part(c, brand, names[0], f"{tag}.11", pts[0], {"dedup_merged_into": str(master)}, active=False)
            await c.execute("UPDATE parts_catalog SET specifications = $2::jsonb WHERE id=$1", master, json.dumps({"merged_from": [str(loser)]}))
            assert await um.build_pair_plan(c, loser, master) is None
    _run(scenario)
