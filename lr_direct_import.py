#!/usr/bin/env python3
"""
Land Rover direct import — runs on the HOST and pipes SQL to PostgreSQL via docker exec.
Uses the actual parts_catalog schema (no in_stock, no image_url columns).
Applies name quality fixes: strip surrounding quotes, remove [CODE] suffixes, title-case all-caps.
"""
import json, re, subprocess, sys, uuid
from pathlib import Path

JSON_FILE   = "/opt/autosparefinder/land_rover_parts.json"
MANUFACTURER = "Land Rover"
MFR_ID      = "7f060acf-2382-42e1-8413-f9b045cb0836"
BATCH       = 100
CATEGORY_MAP = {
    "brake": "Brakes & Clutch", "clutch": "Brakes & Clutch",
    "filter": "Filters", "air filter": "Filters", "oil filter": "Filters",
    "fuel": "Fuel System",
    "engine": "Engine & Drivetrain", "piston": "Engine & Drivetrain",
    "suspension": "Suspension & Steering", "strut": "Suspension & Steering",
    "shock": "Suspension & Steering", "bearing": "Suspension & Steering",
    "belt": "Engine & Drivetrain", "timing": "Engine & Drivetrain",
    "exhaust": "Exhaust", "muffler": "Exhaust",
    "light": "Lighting & Electrical", "lamp": "Lighting & Electrical",
    "battery": "Lighting & Electrical", "sensor": "Lighting & Electrical",
    "body": "Body & Interior", "door": "Body & Interior",
    "wheel": "Wheels & Tyres", "tyre": "Wheels & Tyres", "tire": "Wheels & Tyres",
    "steering": "Suspension & Steering", "rack": "Suspension & Steering",
    "transmission": "Gearbox & Transmission", "gearbox": "Gearbox & Transmission",
    "cooling": "Cooling System", "radiator": "Cooling System", "thermostat": "Cooling System",
    "gasket": "Engine & Drivetrain", "seal": "Engine & Drivetrain",
    "windscreen": "Body & Interior", "wiper": "Body & Interior",
    "seat": "Body & Interior", "mirror": "Body & Interior",
}

def guess_category(name: str, desc: str = "") -> str:
    text = (name + " " + desc).lower()
    for kw, cat in CATEGORY_MAP.items():
        if kw in text:
            return cat
    return "Other"

def fix_name(name: str) -> str:
    name = name.strip().strip('"')
    name = re.sub(r'\s*\[[A-Z0-9]{2,6}\]\s*$', '', name)
    name = re.sub(r' {2,}', ' ', name).strip()
    if name == name.upper() and re.search(r'[A-Z]{3,}', name):
        name = name.title()
    return name

def make_sku(raw: str) -> str:
    return "LR-" + re.sub(r'[^A-Z0-9\-]', '', raw.upper())[:80]

def esc(s: str) -> str:
    return s.replace("'", "''")

def run_sql(sql: str) -> str:
    result = subprocess.run(
        ["docker", "exec", "-i", "autospare_postgres_catalog",
         "psql", "-U", "autospare", "-d", "autospare", "-t", "-A", "-c", sql],
        capture_output=True, text=True, timeout=30
    )
    if result.returncode != 0:
        raise RuntimeError(f"psql error: {result.stderr.strip()}")
    return result.stdout.strip()

def run_sql_pipe(sql: str) -> None:
    result = subprocess.run(
        ["docker", "exec", "-i", "autospare_postgres_catalog",
         "psql", "-U", "autospare", "-d", "autospare", "-q"],
        input=sql, capture_output=True, text=True, timeout=60
    )
    if result.returncode != 0:
        raise RuntimeError(f"psql error: {result.stderr.strip()}")


def main():
    p = Path(JSON_FILE)
    if not p.exists():
        print(f"ERROR: {JSON_FILE} not found")
        sys.exit(1)

    raw = json.loads(p.read_text(encoding="utf-8"))
    parts = raw.get("parts", raw) if isinstance(raw, dict) else raw
    print(f"Loaded {len(parts)} parts from JSON")

    old_count = int(run_sql(f"SELECT COUNT(*) FROM parts_catalog WHERE manufacturer='{MANUFACTURER}' AND is_active=TRUE"))
    print(f"Existing active LR parts: {old_count} → deactivating...")
    run_sql(f"UPDATE parts_catalog SET is_active=FALSE, updated_at=NOW() WHERE manufacturer='{MANUFACTURER}'")

    inserted = 0
    skipped  = 0
    batches  = 0

    rows_buffer = []
    for raw_part in parts:
        raw_sku = str(raw_part.get("sku") or raw_part.get("part_number") or "").strip()
        if not raw_sku:
            skipped += 1
            continue

        name     = fix_name(str(raw_part.get("name") or raw_part.get("title") or "").strip()[:255])
        if not name:
            skipped += 1
            continue
        desc     = esc(str(raw_part.get("description") or "").strip()[:2000])
        price    = float(raw_part.get("price") or raw_part.get("retail_price") or 0)
        oem      = esc(str(raw_part.get("oem_number") or raw_sku).strip()[:100])
        cat      = guess_category(name, raw_part.get("description") or "")
        sku      = make_sku(raw_sku)
        part_id  = str(uuid.uuid4())

        rows_buffer.append(
            f"('{part_id}','{esc(sku)}','{esc(name)}','{esc(cat)}','{MANUFACTURER}',"
            f"{price},{price},'{oem}',"
            f"TRUE,NOW(),NOW(),"
            f"'{MFR_ID}'::uuid)"
        )

        if len(rows_buffer) >= BATCH:
            sql = (
                "INSERT INTO parts_catalog"
                " (id,sku,name,category,manufacturer,"
                "  base_price,importer_price_ils,oem_number,"
                "  is_active,created_at,updated_at,"
                "  manufacturer_id)"
                " VALUES " + ",".join(rows_buffer) +
                " ON CONFLICT (sku) DO UPDATE SET"
                "  name=EXCLUDED.name, category=EXCLUDED.category,"
                "  base_price=EXCLUDED.base_price,"
                "  importer_price_ils=EXCLUDED.importer_price_ils,"
                "  oem_number=EXCLUDED.oem_number,"
                "  is_active=TRUE, updated_at=NOW();"
            )
            run_sql_pipe(sql)
            inserted += len(rows_buffer)
            batches  += 1
            rows_buffer = []
            if batches % 10 == 0:
                print(f"  {inserted} inserted so far...")

    if rows_buffer:
        sql = (
            "INSERT INTO parts_catalog"
            " (id,sku,name,category,manufacturer,"
            "  base_price,importer_price_ils,oem_number,"
            "  is_active,created_at,updated_at,"
            "  manufacturer_id)"
            " VALUES " + ",".join(rows_buffer) +
            " ON CONFLICT (sku) DO UPDATE SET"
            "  name=EXCLUDED.name, category=EXCLUDED.category,"
            "  base_price=EXCLUDED.base_price,"
            "  importer_price_ils=EXCLUDED.importer_price_ils,"
            "  oem_number=EXCLUDED.oem_number,"
            "  is_active=TRUE, updated_at=NOW();"
        )
        run_sql_pipe(sql)
        inserted += len(rows_buffer)

    total = int(run_sql(f"SELECT COUNT(*) FROM parts_catalog WHERE manufacturer='{MANUFACTURER}' AND is_active=TRUE"))
    priced = int(run_sql(f"SELECT COUNT(*) FROM parts_catalog WHERE manufacturer='{MANUFACTURER}' AND is_active=TRUE AND base_price>0"))

    print(f"\n{'='*50}")
    print(f"IMPORT COMPLETE — Land Rover")
    print(f"  Inserted/updated: {inserted}")
    print(f"  Skipped (no SKU/name): {skipped}")
    print(f"  Active in DB: {total}")
    print(f"  With price: {priced}")
    print(f"{'='*50}")


if __name__ == "__main__":
    main()
