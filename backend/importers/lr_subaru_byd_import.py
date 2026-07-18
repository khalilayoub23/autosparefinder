#!/usr/bin/env python3
"""
Import prices from land_rover_parts.json, subaru_il_parts.json, bydil_parts.json.
Applies importer_price_ils, max_price_ils, base_price per 45% margin policy.
"""
import asyncio, json, os, sys, time, asyncpg

DB_URL = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")
VAT = 0.18


async def import_land_rover(conn):
    path = "/app/land_rover_parts.json"
    if not os.path.exists(path):
        print("SKIP: land_rover_parts.json not found"); return 0
    with open(path) as f:
        data = json.load(f)
    parts = data if isinstance(data, list) else data.get('parts', [])
    print(f"\n[Land Rover] {len(parts):,} parts from {path}")
    # Keys: sku, name, description, price, oem_number, category, image_url, in_stock
    # price is likely retail incl. VAT (check typical LR prices)
    updated = not_found = errors = 0
    spec = json.dumps({"importer": "Delek Motors - Land Rover Israel", "source": "land_rover_parts.json"})
    for p in parts:
        oem = str(p.get('oem_number', '') or '').strip()
        price_raw = float(p.get('price') or 0)
        if not oem or price_raw <= 0:
            continue
        # Assume price is excl. VAT (most LR sources)
        retail = round(price_raw * (1 + VAT), 2)
        try:
            res = await conn.execute("""
                UPDATE parts_catalog SET
                    importer_price_ils=$1, max_price_ils=$2, base_price=$2,
                    specifications=COALESCE(specifications,'{}')::jsonb || $3::jsonb,
                    updated_at=NOW()
                WHERE oem_number=$4 AND manufacturer='Land Rover' AND is_active=true
            """, price_raw, retail, spec, oem)
            n = int(res.split()[-1])
            if n > 0:
                updated += n
            else:
                not_found += 1
        except Exception as e:
            errors += 1
            if errors <= 3: print(f"  LR error [{oem}]: {e}")
    print(f"  updated={updated:,} not_found={not_found:,} errors={errors}")
    return updated


async def import_subaru(conn):
    path = "/app/subaru_il_parts.json"
    if not os.path.exists(path):
        print("SKIP: subaru_il_parts.json not found"); return 0
    with open(path) as f:
        data = json.load(f)
    parts = data if isinstance(data, list) else data.get('parts', [])
    print(f"\n[Subaru] {len(parts):,} parts from {path}")
    # Keys: Material, MatDescHe, MatDescEn, PriceNoVat, PriceWithVat, StockExist
    updated = not_found = errors = 0
    spec = json.dumps({"importer": "Subaru Israel", "source": "subaru_il_parts.json"})
    for p in parts:
        oem = str(p.get('Material', '') or '').strip()
        cost = float(p.get('PriceNoVat') or 0)
        retail = float(p.get('PriceWithVat') or 0)
        if not oem or cost <= 0:
            continue
        if retail <= 0:
            retail = round(cost * (1 + VAT), 2)
        try:
            res = await conn.execute("""
                UPDATE parts_catalog SET
                    importer_price_ils=$1, max_price_ils=$2, base_price=$2,
                    specifications=COALESCE(specifications,'{}')::jsonb || $3::jsonb,
                    updated_at=NOW()
                WHERE oem_number=$4 AND manufacturer='Subaru' AND is_active=true
            """, cost, retail, spec, oem)
            n = int(res.split()[-1])
            if n > 0:
                updated += n
            else:
                not_found += 1
        except Exception as e:
            errors += 1
            if errors <= 3: print(f"  Subaru error [{oem}]: {e}")
    print(f"  updated={updated:,} not_found={not_found:,} errors={errors}")
    return updated


async def import_byd(conn):
    path = "/app/bydil_parts.json"
    if not os.path.exists(path):
        print("SKIP: bydil_parts.json not found"); return 0
    with open(path) as f:
        data = json.load(f)
    parts = data if isinstance(data, list) else data.get('parts', [])
    print(f"\n[BYD] {len(parts):,} parts from {path}")
    # Keys: catalog_number, brand, model_he, name_he, price_ils_vat, price_ils, in_stock
    updated = not_found = errors = 0
    mfr = await conn.fetchrow("SELECT id FROM car_brands WHERE LOWER(name)='byd' LIMIT 1")
    mfr_id = str(mfr['id']) if mfr else None
    spec_base = {"importer": "BYD Israel (Colmobil)", "source": "bydil_parts.json"}

    for p in parts:
        oem = str(p.get('catalog_number', '') or '').strip()
        cost = float(p.get('price_ils') or 0)
        retail = float(p.get('price_ils_vat') or 0)
        if not oem or cost <= 0:
            continue
        if retail <= 0:
            retail = round(cost * (1 + VAT), 2)
        model_he = str(p.get('model_he', '') or '')
        spec = json.dumps({**spec_base, "model_he": model_he})

        try:
            res = await conn.execute("""
                UPDATE parts_catalog SET
                    importer_price_ils=$1, max_price_ils=$2, base_price=$2,
                    specifications=COALESCE(specifications,'{}')::jsonb || $3::jsonb,
                    updated_at=NOW()
                WHERE (oem_number=$4 OR sku=$4) AND manufacturer='BYD' AND is_active=true
            """, cost, retail, spec, oem)
            n = int(res.split()[-1])
            if n > 0:
                updated += n
            else:
                not_found += 1
        except Exception as e:
            errors += 1
            if errors <= 3: print(f"  BYD error [{oem}]: {e}")
    print(f"  updated={updated:,} not_found={not_found:,} errors={errors}")
    return updated


async def run():
    if not DB_URL:
        print("ERROR: DATABASE_URL not set"); sys.exit(1)
    conn = await asyncpg.connect(DB_URL)
    t0 = time.monotonic()
    try:
        n1 = await import_land_rover(conn)
        n2 = await import_subaru(conn)
        n3 = await import_byd(conn)
        print(f"\n=== DONE ({time.monotonic()-t0:.1f}s) total_updated={n1+n2+n3:,} ===")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(run())
