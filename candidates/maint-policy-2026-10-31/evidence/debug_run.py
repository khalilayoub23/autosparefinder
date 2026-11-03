"""#119 DEBUG harness — ISOLATED database only (refuses anything else before connecting).

usage: python3 debug_run.py <old|new> <out.json>
Seeds a representative dataset (committed, prefix DBG-), runs the six tasks with the code mounted at /app, and
records snapshots of the database rows AND of every customer price surface before/after each task.
"""
import asyncio
import json
import os
import sys
import time
import uuid

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/tests")
import isolated_db_guard  # noqa: E402

_ref = isolated_db_guard.static_refusal()
if _ref is not None:
    sys.exit(f"REFUSED: {_ref}")

import sqlalchemy as sa  # noqa: E402
from BACKEND_DATABASE_MODELS import async_session_factory  # noqa: E402
import db_update_agent as dua  # noqa: E402

PHASE, OUT = sys.argv[1], sys.argv[2]
RATE = 3.0
SIX = ["backfill_bmw_fitment_from_name_he", "backfill_ford_fitment_from_name_he", "backfill_jaguar_fitment_from_name",
       "merge_catalog_fitment_from_part_vehicle_fitment", "fix_base_prices", "normalize_base_price"]   # run_all_tasks order
BRANDS = {"BMW": "caa6ba39-02aa-4394-969d-a15f3f19104c", "MINI": "47a433bf-4f6f-4f8f-a686-a8c02f7727a8",
          "Ford": "73fc77ef-5414-4270-9476-2444d8b7eb41", "Jaguar": "fde0f2dc-c6fb-4ab6-b699-765044fbc073",
          "Toyota": "11111111-0000-4000-8000-000000000001", "Lexus": "11111111-0000-4000-8000-000000000002",
          "TestMake": "11111111-0000-4000-8000-000000000003"}

# ── dataset ───────────────────────────────────────────────────────────────────────────────────────────────────
# (sku, part_type, manufacturer, name, name_he, base, importer, max, online, compatible_vehicles-json)
PARTS = [
    ("DBG-LOCAL", "oem", "TestMake", "Local offer", None, 50, None, None, None, None),          # stale low base
    ("DBG-FOREIGN", "oem", "TestMake", "Foreign offer", None, 999, None, None, None, None),     # stale high base
    ("DBG-UNKNOWN", "oem", "TestMake", "Unknown-country offer", None, None, None, None, None, None),
    ("DBG-USD", "oem", "TestMake", "USD-only offer", None, None, None, None, None, None),
    ("DBG-COMPETE", "oem", "TestMake", "Competing offers", None, 10, None, None, None, None),
    ("DBG-IMPCHG", "oem", "TestMake", "No offer, importer cost changed", None, 116, 100, None, None, None),
    ("DBG-MAXONLY", "oem", "TestMake", "No offer, consumer ref only", None, None, 0, 118, 50, None),
    ("DBG-ONLINE", "oem", "TestMake", "No offer, online only", None, None, 0, 0, 50, None),
    ("DBG-NOBASIS", "oem", "TestMake", "No input at all", None, None, None, None, None, None),
    ("DBG-OTHERCLS", "oem", "TestMake", "OEM part, only aftermarket offer", None, 77, 100, None, None, None),
    ("DBG-STALE", "oem", "TestMake", "Stale (unavailable) offer + importer", None, None, 100, None, None, None),
    ("DBG-URLLESS", "oem", "TestMake", "Price-list offer only + importer", None, None, 100, None, None, None),
    ("DBG-PINGPONG", "oem", "TestMake", "Offer AND importer cost", None, None, 80, None, None, None),
    # fitment
    ("DBG-FIT-2MAKES", "oem", "Toyota", "Brake pad set", None, None, None, None, None, None),
    ("DBG-FIT-OK", "oem", "Toyota", "Oil filter", None, None, None, None, None,
     '[{"manufacturer": "Toyota", "model": "Corolla", "source": "part_vehicle_fitment", "year_from": 2015, "year_to": 2018}]'),
    ("DBG-FIT-STALE", "oem", "Toyota", "Air filter", None, None, None, None, None,
     '[{"manufacturer": "Toyota", "model": "OLDMODEL", "source": "part_vehicle_fitment"}, {"manufacturer": "Toyota", "model": "Yaris", "source": "xls"}]'),
    ("DBG-FIT-DICT", "oem", "Toyota", "Cabin filter", None, None, None, None, None, '{"manufacturer": "Toyota"}'),
    ("DBG-FIT-DUP", "oem", "Toyota", "Spark plug", None, None, None, None, None,
     '[{"manufacturer": "Toyota", "model": "Auris", "source": "xls"}, {"manufacturer": "Toyota", "model": "Auris", "source": "xls"}]'),
    ("DBG-FIT-NOYEAR", "oem", "Toyota", "Wiper", None, None, None, None, None, None),
    # name backfills
    ("DBG-BMW-G20", "oem", "BMW", "Left illuminor", "G20 מגן שמן נהג", None, None, None, None, None),
    ("DBG-BMW-MINI", "oem", "BMW", "WIPER BLADE", "F-54 'מגב אח", None, None, None, None, None),
    ("DBG-BMW-NONE", "oem", "BMW", "Bolt", "בורג", None, None, None, None, None),
    ("DBG-FORD-FOCUS", "oem", "Ford", "PUMP-FUEL INJ.", "#  משאבת הזרקה פוקוס דיזל", None, None, None, None, None),
    ("DBG-FORD-RANGER", "oem", "Ford", "CONNECTION", "# מחבר ליציאת מים ריינג'ר", None, None, None, None, None),
    ("DBG-FORD-CONNECTOR", "oem", "Ford", "CONNECTOR ASY", None, None, None, None, None, None),
    ("DBG-FORD-F250", "oem", "Ford", "BOLT", "#    F250 בורג גל הינע אח", None, None, None, None, None),
    ("DBG-JAG-XF", "oem", "Jaguar", "XF 2.7D Rear Sports Exhaust Sections", None, None, None, None, None, None),
    ("DBG-JAG-TSHIRT", "oem", "Jaguar", "F-type T-shi", None, None, None, None, None, None),
    ("DBG-JAG-LOGO", "oem", "Jaguar", "F-type Logo", None, None, None, None, None, None),
    ("DBG-JAG-CLASSIC", "oem", "Jaguar", "Powerlite Starter Motor Jaguar E Type 3.8 litre - S type - XK150", None, None, None, None, None, None),
    ("DBG-JAG-KIT", "oem", "Jaguar", "S-Type V8 Silver Service Kit", None, None, None, None, None, None),
]
# (sku, supplier key, price_ils, price_usd, class, url?, available?)
OFFERS = [
    ("DBG-LOCAL", "IL", 100, 33.33, None, True, True),
    ("DBG-FOREIGN", "IE", 100, 33.33, None, True, True),
    ("DBG-UNKNOWN", "UNK", 100, 33.33, None, True, True),
    ("DBG-USD", "IE", None, 40, None, True, True),
    ("DBG-COMPETE", "IL", 90, 30, None, True, True),          # the price basis
    ("DBG-COMPETE", "IE", 120, 40, "oem", True, True),
    ("DBG-COMPETE", "CN", 10, 3.33, "aftermarket", True, True),
    ("DBG-COMPETE", "IL2", 20, 6.67, None, True, False),      # stale
    ("DBG-COMPETE", "IL3", 30, 10, None, False, True),        # not customer-visible
    ("DBG-COMPETE", "OFF", 40, 13.33, None, True, True),      # inactive supplier
    ("DBG-OTHERCLS", "CN", 20, 6.67, "aftermarket", True, True),
    ("DBG-STALE", "IL", 60, 20, None, True, False),
    ("DBG-URLLESS", "IL", 50, 16.67, None, False, True),
    ("DBG-PINGPONG", "IL", 100, 33.33, None, True, True),
    ("DBG-FORD-FOCUS", "IL", 200, 66.67, None, True, True),
]
SUPPLIERS = {"IL": ("DBG Local IL", "IL", True), "IL2": ("DBG Local IL 2", "IL", True), "IL3": ("DBG Price list IL", "Israel", True),
             "IE": ("DBG Foreign IE", "IE", True), "CN": ("DBG Marketplace CN", "CN", True),
             "UNK": ("DBG Unknown", None, True), "OFF": ("DBG Inactive", "IL", False)}
# (sku, manufacturer, model, year_from, year_to, notes)
PVF = [
    ("DBG-FIT-2MAKES", "Toyota", "Corolla", 2015, 2018, None),
    ("DBG-FIT-2MAKES", "Lexus", "CT", 2011, 2017, None),
    ("DBG-FIT-OK", "Toyota", "Corolla", 2015, 2018, None),
    ("DBG-FIT-STALE", "Toyota", "Corolla", 2015, 2018, None),
    ("DBG-FIT-DICT", "Toyota", "Corolla", 2015, 2018, None),
    ("DBG-FIT-DUP", "Toyota", "Auris", 2012, 2018, None),
    ("DBG-FIT-NOYEAR", "Toyota", "Corolla", 2015, None, None),       # incomplete: no year_to
]


async def reset_and_seed(db):
    await db.execute(sa.text("DELETE FROM part_vehicle_fitment WHERE part_id IN (SELECT id FROM parts_catalog WHERE sku LIKE 'DBG-%')"))
    await db.execute(sa.text("DELETE FROM supplier_parts WHERE part_id IN (SELECT id FROM parts_catalog WHERE sku LIKE 'DBG-%')"))
    await db.execute(sa.text("DELETE FROM parts_catalog WHERE sku LIKE 'DBG-%'"))
    await db.execute(sa.text("DELETE FROM suppliers WHERE name LIKE 'DBG %'"))
    await db.execute(sa.text("DELETE FROM system_settings WHERE key LIKE 'cursor__%' OR key LIKE 'delta_checkpoint__normalize_base_price'"))
    await db.execute(sa.text("""INSERT INTO system_settings (id, key, value, value_type, is_public, updated_at)
        VALUES (gen_random_uuid(), 'ils_per_usd', '3.0', 'string', false, NOW()) ON CONFLICT (key) DO UPDATE SET value='3.0'"""))
    for name, bid in BRANDS.items():
        await db.execute(sa.text("INSERT INTO car_brands (id, name) VALUES (CAST(:i AS uuid), :n) ON CONFLICT DO NOTHING"),
                         {"i": bid, "n": name})
    sup = {}
    for k, (n, c, a) in SUPPLIERS.items():
        sup[k] = (await db.execute(sa.text("INSERT INTO suppliers (id, name, country, is_active) VALUES (gen_random_uuid(), :n, :c, :a) RETURNING id::text"),
                                   {"n": n, "c": c, "a": a})).scalar_one()
    pid = {}
    for sku, pt, mfr, name, nhe, base, imp, mx, onl, cvj in PARTS:
        pid[sku] = (await db.execute(sa.text("""INSERT INTO parts_catalog (id, sku, name, name_he, manufacturer, manufacturer_id,
                oem_number, part_type, is_active, base_price, importer_price_ils, max_price_ils, online_price_ils, compatible_vehicles)
            VALUES (gen_random_uuid(), :s, :n, :nh, :m, CAST(:mid AS uuid), :o, :t, TRUE, :b, :imp, :mx, :onl, CAST(:cv AS jsonb))
            RETURNING id::text"""), {"s": sku, "n": name, "nh": nhe, "m": mfr, "mid": BRANDS[mfr], "o": sku.replace("-", ""),
                                      "t": pt, "b": base, "imp": imp, "mx": mx, "onl": onl, "cv": cvj})).scalar_one()
    for sku, sk, ils, usd, kl, url, av in OFFERS:
        await db.execute(sa.text("""INSERT INTO supplier_parts (id, supplier_id, part_id, supplier_sku, price_ils, price_usd,
                is_available, availability, part_type, supplier_url, updated_at, created_at)
            VALUES (gen_random_uuid(), CAST(:s AS uuid), CAST(:p AS uuid), :k, :i, :u, :av, 'in_stock', :t, :url, NOW(), NOW())"""),
            {"s": sup[sk], "p": pid[sku], "k": f"{sku}-{sk}", "i": ils, "u": usd, "av": av, "t": kl,
             "url": "https://debug.invalid/offer" if url else None})
    for sku, mfr, model, yf, yt, notes in PVF:
        await db.execute(sa.text("""INSERT INTO part_vehicle_fitment (id, part_id, manufacturer, manufacturer_id, model, year_from,
                year_to, notes, updated_at) VALUES (gen_random_uuid(), CAST(:p AS uuid), :m, CAST(:mid AS uuid), :mo, :yf, :yt, :n, NOW())"""),
            {"p": pid[sku], "m": mfr, "mid": BRANDS[mfr], "mo": model, "yf": yf, "yt": yt, "n": notes})
    # what the OLD Ford backfill already wrote in production: a make-prefixed model (12,360 live rows)
    old_ford = (await db.execute(sa.text("""INSERT INTO parts_catalog (id, sku, name, name_he, manufacturer, manufacturer_id, oem_number,
            part_type, is_active) VALUES (gen_random_uuid(), 'DBG-FORD-PREFIXED', 'REGULATOR-WINDW', '# FOC ''מנגנון שמשה קד'' ימ',
            'Ford', CAST(:mid AS uuid), 'DBGFORDPREFIXED', 'oem', TRUE) RETURNING id::text"""), {"mid": BRANDS["Ford"]})).scalar_one()
    await db.execute(sa.text("""INSERT INTO part_vehicle_fitment (id, part_id, manufacturer, manufacturer_id, model, year_from, year_to,
            notes, updated_at) VALUES (gen_random_uuid(), CAST(:p AS uuid), 'Ford', CAST(:mid AS uuid), 'Ford Focus', 1998, 2023,
            'extracted:name_he', NOW())"""), {"p": old_ford, "mid": BRANDS["Ford"]})
    await db.commit()


# ── snapshots ─────────────────────────────────────────────────────────────────────────────────────────────────
def _f(v):
    return None if v is None else float(v)


async def db_snapshot(db):
    rows = (await db.execute(sa.text("""SELECT id::text, sku, base_price, importer_price_ils, max_price_ils, min_price_ils,
        online_price_ils, compatible_vehicles, updated_at FROM parts_catalog WHERE sku LIKE 'DBG-%' ORDER BY sku"""))).fetchall()
    out = {}
    for r in rows:
        fit = sorted([list(map(lambda x: x if not hasattr(x, "isoformat") else str(x), f)) for f in (await db.execute(sa.text(
            "SELECT manufacturer, model, year_from, year_to, notes FROM part_vehicle_fitment WHERE part_id = CAST(:p AS uuid)"),
            {"p": r[0]})).fetchall()], key=str)
        out[r[1]] = {"id": r[0], "base_price": _f(r[2]), "importer_price_ils": _f(r[3]), "max_price_ils": _f(r[4]),
                     "min_price_ils": _f(r[5]), "online_price_ils": _f(r[6]), "compatible_vehicles": r[7], "fitment": fit,
                     "updated_at": str(r[8])}
    return out


async def surface_snapshot(db, snap):
    """Every customer price surface we can call in-process, per DBG part."""
    from routes.parts import _current_part_offer, get_part_suppliers
    out = {}
    for sku, row in snap.items():
        s = {}
        try:
            cmp_ = await get_part_suppliers(part_id=row["id"], limit=50, db=db, request=None, redis=None)
            s["compare"] = [(o.get("supplier_part_id") is not None, o.get("customer_price_ils"), o.get("customer_vat_ils"),
                             o.get("customer_total_ils"), bool(o.get("is_base_price_fallback"))) for o in (cmp_.get("suppliers") or [])]
        except Exception as e:
            s["compare"] = f"ERR {type(e).__name__}: {str(e)[:120]}"
        try:
            cur = await _current_part_offer(db, row["id"])
            s["wishlist_cart"] = None if not cur else round(float(cur["price_ils"]), 2)
        except Exception as e:
            s["wishlist_cart"] = f"ERR {type(e).__name__}: {str(e)[:120]}"
        out[sku] = s
    return out


async def search(db, mfr, model, year, query=""):
    from routes import parts as rp
    import vehicle_model_match as vmm
    rp.SEARCH_RESPONSE_CACHE.clear()
    vmm._cache.clear()
    try:
        from BACKEND_AUTH_SECURITY import get_redis
        r = await get_redis()
        await r.flushdb()
    except Exception:
        pass
    try:
        res = await rp.search_parts(query=query, vehicle_id=None, category=None, per_type=50, sort_by="price_ils",
                                    vehicle_manufacturer=mfr, vehicle_model=model, vehicle_submodel=None, vehicle_year=year,
                                    enable_cross_refs=False, db=db, request=None, redis=None)
    except Exception as e:
        return f"ERR {type(e).__name__}: {str(e)[:200]}"
    skus = set()
    def walk(o):
        if isinstance(o, dict):
            if isinstance(o.get("sku"), str) and o["sku"].startswith("DBG-"):
                skus.add(o["sku"])
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(res)
    return sorted(skus)


async def searches(db):
    return {"Ford Focus 2015": await search(db, "Ford", "Focus", 2015),
            "Ford Ranger 2015": await search(db, "Ford", "Ranger", 2015),
            "Ford Transit Connect 2015": await search(db, "Ford", "Transit Connect", 2015),
            "Lexus CT 2014": await search(db, "Lexus", "CT", 2014),
            "Toyota Corolla 2016": await search(db, "Toyota", "Corolla", 2016),
            "BMW 3 Series 2020": await search(db, "BMW", "3 Series", 2020),
            "MINI Clubman 2016": await search(db, "MINI", "MINI Clubman", 2016),
            "Jaguar XF 2012": await search(db, "Jaguar", "XF", 2012)}


def diff(a, b, keys=("base_price", "importer_price_ils", "max_price_ils", "min_price_ils", "online_price_ils",
                     "compatible_vehicles", "fitment")):
    d = {}
    for sku in sorted(set(a) | set(b)):
        for k in keys:
            if (a.get(sku) or {}).get(k) != (b.get(sku) or {}).get(k):
                d.setdefault(sku, {})[k] = [(a.get(sku) or {}).get(k), (b.get(sku) or {}).get(k)]
    return d


async def run(db, name, **kw):
    fn = getattr(dua, name)
    t = time.monotonic()
    try:
        r = await fn(db, **kw)
    except Exception as e:
        await db.rollback()
        r = {"status": "exception", "error": f"{type(e).__name__}: {str(e)[:200]}"}
    r = json.loads(json.dumps(r, default=str))
    r["_wall_s"] = round(time.monotonic() - t, 2)
    return r


async def main():
    ev = {"phase": PHASE, "code": os.environ.get("CODE_LABEL", "?")}
    async with async_session_factory() as db:
        live = await isolated_db_guard.live_refusal(db)
        if live is not None:
            sys.exit(f"REFUSED: {live}")
        await reset_and_seed(db)
        s0 = await db_snapshot(db)
        ev["seed"] = s0
        ev["surfaces_before"] = await surface_snapshot(db, s0)
        ev["search_before"] = await searches(db)
        ev["tasks"] = []
        cur = s0
        for name in SIX:                                     # individually, in run_all_tasks order
            r = await run(db, name)
            after = await db_snapshot(db)
            ev["tasks"].append({"task": name, "report": r, "diff": diff(cur, after)})
            cur = after
        ev["after_first_pass"] = cur
        ev["surfaces_after"] = await surface_snapshot(db, cur)
        ev["search_after"] = await searches(db)
        # re-run everything: must change nothing
        ev["rerun"] = []
        for name in SIX:
            r = await run(db, name)
            after = await db_snapshot(db)
            ev["rerun"].append({"task": name, "report": r, "diff": diff(cur, after)})
            cur = after
        # the other order: the two price owners swapped, fitment after price
        ev["reverse"] = []
        for name in list(reversed(SIX)):
            r = await run(db, name)
            after = await db_snapshot(db)
            ev["reverse"].append({"task": name, "report": r, "diff": diff(cur, after)})
            cur = after
        if PHASE == "new":
            # interrupted run: stale values back, one batch, a failing batch, then resume
            final_ok = {k: v["base_price"] for k, v in cur.items()}
            basis = [k for k in ("DBG-LOCAL", "DBG-FOREIGN", "DBG-UNKNOWN", "DBG-USD", "DBG-COMPETE", "DBG-IMPCHG", "DBG-MAXONLY",
                                 "DBG-ONLINE", "DBG-STALE", "DBG-URLLESS", "DBG-PINGPONG", "DBG-FORD-FOCUS")]
            await db.execute(sa.text("UPDATE parts_catalog SET base_price = 1 WHERE sku = ANY(:s)"), {"s": basis})
            await db.commit()
            os.environ["BASE_PRICE_TASK_BATCH"] = "4"
            await dua._save_task_cursor(db, "fix_base_prices", dua._ZERO_UUID)
            await dua._save_task_cursor(db, "normalize_base_price", dua._ZERO_UUID)
            steps = [await run(db, "fix_base_prices", max_batches=1)]
            real, calls = dua.apply_base_price_rule, {"n": 0}

            async def flaky(*a, **k):
                calls["n"] += 1
                if calls["n"] == 2:
                    raise RuntimeError("simulated interruption")
                return await real(*a, **k)
            dua.apply_base_price_rule = flaky
            steps.append(await run(db, "fix_base_prices", max_batches=10))
            dua.apply_base_price_rule = real
            for _ in range(20):
                r = await run(db, "fix_base_prices", max_batches=10)
                steps.append(r)
                if r.get("pass_completed"):
                    break
            for _ in range(20):
                r = await run(db, "normalize_base_price", max_batches=10)
                steps.append(r)
                if r.get("pass_completed"):
                    break
            resumed = {k: v["base_price"] for k, v in (await db_snapshot(db)).items()}
            mism = {k: (final_ok.get(k), resumed.get(k)) for k in final_ok if final_ok.get(k) != resumed.get(k)}
            ev["interrupt"] = {"steps": [{k: s.get(k) for k in ("status", "batches", "started_after", "resume_after",
                                                                 "pass_completed", "updated", "failed", "error")} for s in steps],
                               "mismatch_vs_uninterrupted": mism}
            # the cleanup agent's normalizer on fresh offers (its own scope: unpriced parts with a fresh offer)
            import db_cleanup_agent as cl
            await db.execute(sa.text("UPDATE parts_catalog SET base_price = NULL WHERE sku IN ('DBG-LOCAL','DBG-FOREIGN','DBG-URLLESS')"))
            await db.execute(sa.text("UPDATE parts_catalog SET importer_price_ils = NULL WHERE sku = 'DBG-URLLESS'"))
            await db.commit()
            before = await db_snapshot(db)
            n = await cl.normalize_base_price_for_parts(db, 100)
            await db.commit()
            ev["cleanup_normalizer"] = {"priced": n, "diff": diff(before, await db_snapshot(db))}
            # the prepared production repair of the old Ford rows (FORD_REPAIR.sql), then search again
            await db.execute(sa.text(open("/m/FORD_REPAIR_DEBUG.sql").read()))
            await db.commit()
            ev["after_ford_repair"] = {"fitment": (await db_snapshot(db))["DBG-FORD-PREFIXED"]["fitment"],
                                       "search": await search(db, "Ford", "Focus", 2015)}
        ev["final"] = await db_snapshot(db)
    json.dump(ev, open(OUT, "w"), ensure_ascii=False, indent=1, default=str)
    print("wrote", OUT)


asyncio.run(main())
