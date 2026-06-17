#!/usr/bin/env python3
"""
acura_browser_harvest.py
Import Acura OEM parts from acura.oempartsonline.com.

This scraper runs INSIDE the Docker container but CANNOT access the site
directly (Cloudflare blocks datacenter IPs). Instead, it reads a JSON seed
file produced by the browser-side harvest (acura_browser_parts.json).

The browser harvest uses:
  GET /ajax/search?page=N&search_str=QUERY&catalog_type=parts
which is accessible from the user's browser session.

To produce the seed file, run the JS harvest in the browser via claude-in-chrome,
then docker cp the resulting JSON into the container.

Alternatively, this script can be run with --from-json /path/to/file.json
"""
import asyncio
import asyncpg
import json
import os
import sys
import uuid

sys.path.insert(0, '/app')

DB_URL = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")
MARGIN = 1.45
USD_TO_ILS = float(os.environ.get("USD_ILS_RATE", "3.70"))


async def get_or_create_brand_id(conn, name: str) -> str:
    row = await conn.fetchrow(
        "SELECT id FROM car_brands WHERE lower(name)=lower($1) AND is_active=TRUE LIMIT 1", name
    )
    if row:
        return str(row["id"])
    new_id = str(uuid.uuid4())
    await conn.execute(
        "INSERT INTO car_brands (id, name, is_active, created_at, updated_at) "
        "VALUES ($1,$2,TRUE,NOW(),NOW()) ON CONFLICT DO NOTHING",
        new_id, name,
    )
    return new_id


async def import_parts(parts: list, conn):
    brand_id = await get_or_create_brand_id(conn, "Acura")
    inserted = 0
    updated = 0

    for p in parts:
        oem = (p.get("oem") or "").strip()
        name = (p.get("name") or "").strip()
        price_usd = float(p.get("price") or 0)
        msrp_usd = float(p.get("msrp") or 0)

        if not oem or price_usd <= 0:
            continue

        # Convert USD → ILS
        importer_price_ils = round(price_usd * USD_TO_ILS, 2)
        base_price = round(importer_price_ils * MARGIN, 2)
        sku = f"ACU-{oem}"

        existing = await conn.fetchrow(
            "SELECT id FROM parts_catalog WHERE sku=$1 OR "
            "(manufacturer ILIKE 'Acura' AND oem_number=$2) LIMIT 1",
            sku, oem,
        )
        if existing:
            await conn.execute(
                "UPDATE parts_catalog SET importer_price_ils=$1, base_price=$2, updated_at=NOW() "
                "WHERE id=$3",
                importer_price_ils, base_price, existing["id"],
            )
            updated += 1
        else:
            new_id = str(uuid.uuid4())
            await conn.execute(
                "INSERT INTO parts_catalog (id, sku, oem_number, name, manufacturer, "
                "manufacturer_id, importer_price_ils, base_price, is_active, "
                "master_enriched, needs_oem_lookup, created_at, updated_at) "
                "VALUES ($1,$2,$3,$4,'Acura',$5,$6,$7,TRUE,FALSE,FALSE,NOW(),NOW()) "
                "ON CONFLICT (sku) DO UPDATE SET importer_price_ils=EXCLUDED.importer_price_ils, "
                "base_price=EXCLUDED.base_price, updated_at=NOW()",
                new_id, sku, oem, name, brand_id,
                importer_price_ils, base_price,
            )
            inserted += 1

    return inserted, updated


async def main():
    json_file = sys.argv[1] if len(sys.argv) > 1 else "/app/state/acura_parts.json"
    if not os.path.exists(json_file):
        print(f"Parts file not found: {json_file}")
        print("Run the browser harvest first to produce this file.")
        sys.exit(1)

    with open(json_file) as f:
        parts = json.load(f)

    print(f"Loading {len(parts)} parts from {json_file}")
    conn = await asyncpg.connect(DB_URL)
    inserted, updated = await import_parts(parts, conn)
    await conn.close()
    print(f"Done: {inserted} inserted, {updated} updated")


if __name__ == "__main__":
    asyncio.run(main())
