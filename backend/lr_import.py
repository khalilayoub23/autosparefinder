"""
Land Rover OEM Parts Importer
Imports land_rover_parts.json (output of lr_browser_scraper.js) into parts_catalog.

Usage:
  docker exec -e JSON_FILE=/app/uploads/land_rover_parts.json autospare_backend python3 /tmp/lr_import.py

The JSON file must first be copied to the container:
  docker cp /opt/autosparefinder/land_rover_parts.json autospare_backend:/app/uploads/
"""

import asyncio, asyncpg, json, os, re, sys, time

DB_URL   = os.getenv("DB_URL", "postgresql://autospare:e4b79d75ca640dbe7f259618f078b82f21573e419308f668beed5e20b26b1d43@postgres_catalog:5432/autospare")
JSON_FILE = os.getenv("JSON_FILE", "/app/uploads/land_rover_parts.json")
MANUFACTURER = "Land Rover"
PREFIX   = "LR-"
BATCH    = 25

CATEGORY_KEYWORDS = {
    "Engine":       ["engine","timing","piston","camshaft","crankshaft","valve","cylinder","gasket","head","block","oil pump"],
    "Brakes":       ["brake","pad","disc","caliper","master cylinder","abs","handbrake"],
    "Suspension":   ["suspension","shock","strut","spring","wishbone","arm","bush","bearing","hub","knuckle"],
    "Steering":     ["steering","rack","tie rod","trackrod","column","power steering"],
    "Transmission": ["gearbox","transmission","clutch","diff","propshaft","driveshaft","transfer"],
    "Electrical":   ["sensor","relay","fuse","ecu","switch","lamp","light","bulb","alternator","starter","battery","wire","harness"],
    "Cooling":      ["radiator","coolant","thermostat","water pump","fan","hose","cooling"],
    "Fuel":         ["fuel","injector","pump","filter","tank","rail","throttle"],
    "Exhaust":      ["exhaust","manifold","catalytic","silencer","muffler","downpipe","lambda"],
    "Body":         ["door","bonnet","boot","panel","bumper","wing","mirror","glass","seal","trim","grille","spoiler"],
    "Interior":     ["seat","belt","airbag","dashboard","carpet","headlining","sunroof","interior","handle"],
    "Air":          ["air filter","air mass","intake","intercooler","turbo","supercharger","breather"],
    "Wheels":       ["wheel","tyre","tire","nut","bolt","stud","hub cap","spacer"],
    "Climate":      ["ac","air conditioning","compressor","condenser","heater","blower","hvac"],
    "Other":        [],
}

def guess_category(name: str, desc: str) -> str:
    text = (name + " " + desc).lower()
    for cat, keywords in CATEGORY_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            return cat
    return "Other"

def make_sku(raw_sku: str) -> str:
    clean = re.sub(r'[^A-Z0-9\-]', '', raw_sku.upper().strip())
    if not clean.startswith(PREFIX.rstrip('-')):
        return f"{PREFIX}{clean}"
    return clean

async def main():
    if not os.path.exists(JSON_FILE):
        print(f"ERROR: {JSON_FILE} not found")
        print("Copy file first: docker cp /opt/autosparefinder/land_rover_parts.json autospare_backend:/app/uploads/")
        sys.exit(1)

    print(f"Loading {JSON_FILE} ...")
    data = json.load(open(JSON_FILE, encoding="utf-8"))
    raw_parts = data.get("parts", data) if isinstance(data, dict) else data
    print(f"  {len(raw_parts)} raw parts loaded from JSON")

    conn = await asyncpg.connect(DB_URL)

    old = await conn.fetchval("SELECT COUNT(*) FROM parts_catalog WHERE manufacturer=$1 AND is_active=TRUE", MANUFACTURER)
    print(f"  Existing active LR parts: {old} \u2192 clearing...")
    await conn.execute("UPDATE parts_catalog SET is_active=FALSE, updated_at=NOW() WHERE manufacturer=$1", MANUFACTURER)

    inserted = 0
    skipped  = 0
    t0 = time.time()

    batch = []
    for raw in raw_parts:
        raw_sku = str(raw.get("sku") or raw.get("part_number") or "").strip()
        if not raw_sku:
            skipped += 1
            continue

        name     = str(raw.get("name") or raw.get("title") or "").strip()[:500]
        desc     = str(raw.get("description") or "").strip()[:2000]
        price    = float(raw.get("price") or raw.get("retail_price") or 0)
        oem      = str(raw.get("oem_number") or raw_sku).strip()[:100]
        cat      = raw.get("category") or guess_category(name, desc)
        image    = str(raw.get("image") or raw.get("image_url") or "")[:500]
        in_stock = bool(raw.get("in_stock", True))

        sku = make_sku(raw_sku)

        batch.append((
            sku, name, desc, MANUFACTURER, cat,
            price, price,
            oem, image, in_stock,
        ))

        if len(batch) >= BATCH:
            await conn.executemany("""
                INSERT INTO parts_catalog
                    (sku, name, description, manufacturer, category,
                     base_price, importer_price_ils, oem_number, image_url, in_stock,
                     is_active, created_at, updated_at)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,TRUE,NOW(),NOW())
                ON CONFLICT (sku) DO UPDATE SET
                    name=EXCLUDED.name, description=EXCLUDED.description,
                    category=EXCLUDED.category, base_price=EXCLUDED.base_price,
                    importer_price_ils=EXCLUDED.importer_price_ils,
                    oem_number=EXCLUDED.oem_number, image_url=EXCLUDED.image_url,
                    in_stock=EXCLUDED.in_stock, is_active=TRUE, updated_at=NOW()
            """, batch)
            inserted += len(batch)
            batch = []

    if batch:
        await conn.executemany("""
            INSERT INTO parts_catalog
                (sku, name, description, manufacturer, category,
                 base_price, importer_price_ils, oem_number, image_url, in_stock,
                 is_active, created_at, updated_at)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,TRUE,NOW(),NOW())
            ON CONFLICT (sku) DO UPDATE SET
                name=EXCLUDED.name, description=EXCLUDED.description,
                category=EXCLUDED.category, base_price=EXCLUDED.base_price,
                importer_price_ils=EXCLUDED.importer_price_ils,
                oem_number=EXCLUDED.oem_number, image_url=EXCLUDED.image_url,
                in_stock=EXCLUDED.in_stock, is_active=TRUE, updated_at=NOW()
        """, batch)
        inserted += len(batch)

    elapsed = time.time() - t0
    total   = await conn.fetchval("SELECT COUNT(*) FROM parts_catalog WHERE manufacturer=$1 AND is_active=TRUE", MANUFACTURER)
    priced  = await conn.fetchval("SELECT COUNT(*) FROM parts_catalog WHERE manufacturer=$1 AND base_price>0", MANUFACTURER)

    print(f"\n{'='*50}")
    print(f"IMPORT COMPLETE \u2014 Land Rover")
    print(f"  Inserted/updated : {inserted}")
    print(f"  Skipped (no SKU) : {skipped}")
    print(f"  DB total active  : {total}")
    print(f"  Priced           : {priced} ({priced*100//total if total else 0}%)")
    print(f"  Elapsed          : {elapsed:.0f}s")
    print(f"{'='*50}")
    await conn.close()

asyncio.run(main())
