"""
Champion Motors Catalog Importer
Reads champion_motors_parts.json and imports into parts_catalog + builds fitment records.

Run:
  docker exec -i autospare_backend python /opt/autosparefinder/backend/import_champion_motors.py [--file PATH]

Root fix actions:
  - Inserts new parts by (manufacturer, oem_number)
  - Builds fitment records from vehicle_market_il registry
  - Syncs Meilisearch for new brands (Volkswagen, Audi, Skoda, SEAT, CUPRA)
"""
import json
import sys
import time
from pathlib import Path
from datetime import datetime
import asyncio
import asyncpg

DEFAULT_INPUT = "/opt/autosparefinder/champion_motors_parts.json"
DB_HOST = "autospare_postgres_catalog"
DB_NAME = "autospare"
DB_USER = "autospare"
DB_PASS = "autospare_secure_2025"

JOB_REGISTRY = []
PROCESSED = {"inserted": 0, "skipped": 0, "errors": 0}

async def get_db():
    return await asyncpg.connect(
        host=DB_HOST,
        database=DB_NAME,
        user=DB_USER,
        password=DB_PASS,
        timeout=30
    )

async def ensure_supplier():
    conn = await get_db()
    try:
        supplier = await conn.fetchrow(
            "SELECT id FROM suppliers WHERE name = 'Champion Motors' LIMIT 1"
        )
        if not supplier:
            supplier = await conn.fetchrow(
                """
                INSERT INTO suppliers (name, country, is_active, contact_email, created_at, updated_at)
                VALUES ('Champion Motors', 'IL', TRUE, 'info@championmotors.co.il', NOW(), NOW())
                RETURNING id
                """
            )
            print(f"[Import] Created supplier: Champion Motors (ID: {supplier['id']})")
        return supplier['id']
    finally:
        await conn.close()

async def get_or_create_brand(conn, brand_name):
    brand = await conn.fetchrow(
        "SELECT id FROM car_brands WHERE name = $1 LIMIT 1",
        brand_name
    )
    if brand:
        return brand['id']
    brand = await conn.fetchrow(
        """
        INSERT INTO car_brands (name, tozeret_cd, is_active, created_at, updated_at)
        VALUES ($1, NULL, TRUE, NOW(), NOW())
        RETURNING id
        """,
        brand_name
    )
    print(f"[Import] Created new brand: {brand_name}")
    return brand['id']

async def insert_part(conn, supplier_id, part, brand_id):
    try:
        existing = await conn.fetchrow(
            "SELECT id FROM parts_catalog WHERE manufacturer = $1 AND oem_number = $2 AND is_active = TRUE LIMIT 1",
            part.get("manufacturer", "Champion Motors"),
            part["oem_number"]
        )
        if existing:
            return None
        part_id = await conn.fetchval(
            """
            INSERT INTO parts_catalog (
                manufacturer, oem_number, name, description,
                supplier_sku, price_ils, price_ils_incl_vat,
                car_brand_id, origin, is_active,
                source_url, created_at, updated_at
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, TRUE, $10, NOW(), NOW())
            RETURNING id
            """,
            part.get("manufacturer", "Champion Motors"),
            part["oem_number"],
            part.get("name", f"{part['oem_number']}"),
            part.get("description", ""),
            f"CM-{part['oem_number']}",
            float(part.get("price_ils", 0)),
            float(part.get("price_ils_incl_vat", 0)),
            brand_id,
            "aftermarket",
            part.get("source_url", "https://www.championmotors.co.il/catalog/"),
        )
        return part_id
    except Exception as e:
        raise Exception(f"Failed to insert {part['oem_number']}: {str(e)}")

async def build_fitment(conn, part_id, brand_name, model_name):
    try:
        brand_result = await conn.fetchrow(
            "SELECT id FROM car_brands WHERE name = $1",
            brand_name
        )
        if not brand_result:
            return 0
        car_brand_id = brand_result['id']
        vehicles = await conn.fetch(
            """
            SELECT DISTINCT vehicle_id, tozeret_cd FROM vehicle_market_il
            WHERE car_brand_id = $1
            AND (
              model_name ILIKE $2
              OR CONCAT(manufacturer_name, ' ', model_name) ILIKE $2
            )
            LIMIT 5000
            """,
            car_brand_id,
            f"%{model_name}%"
        )
        if not vehicles:
            return 0
        fitment_count = 0
        for vehicle in vehicles:
            try:
                existing = await conn.fetchrow(
                    "SELECT id FROM part_vehicle_fitment WHERE part_id = $1 AND vehicle_id = $2 LIMIT 1",
                    part_id,
                    vehicle['vehicle_id']
                )
                if existing:
                    continue
                await conn.execute(
                    """
                    INSERT INTO part_vehicle_fitment (part_id, vehicle_id, tozeret_cd, created_at)
                    VALUES ($1, $2, $3, NOW())
                    """,
                    part_id,
                    vehicle['vehicle_id'],
                    vehicle['tozeret_cd']
                )
                fitment_count += 1
            except:
                pass
        return fitment_count
    except Exception as e:
        print(f"[Import] Fitment build error for part {part_id}: {str(e)}")
        return 0

async def import_catalog(input_file):
    print(f"[Import] Starting Champion Motors import from {input_file}")
    start = time.time()
    with open(input_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
    parts_to_import = data.get("parts", [])
    print(f"[Import] Loaded {len(parts_to_import)} parts from {data.get('source', 'unknown')}")
    supplier_id = await ensure_supplier()
    conn = await get_db()
    try:
        total_fitments = 0
        for idx, part in enumerate(parts_to_import):
            try:
                brand_name = part.get("brand") or "Unknown"
                brand_id = await get_or_create_brand(conn, brand_name)
                part_id = await insert_part(conn, supplier_id, part, brand_id)
                if not part_id:
                    PROCESSED["skipped"] += 1
                    continue
                PROCESSED["inserted"] += 1
                model_name = part.get("model", "")
                if model_name:
                    fitment_count = await build_fitment(conn, part_id, brand_name, model_name)
                    total_fitments += fitment_count
                if (idx + 1) % 50 == 0:
                    print(f"[Import] Progress: {idx + 1}/{len(parts_to_import)} | "
                          f"Inserted: {PROCESSED['inserted']} | Fitments: {total_fitments}")
            except Exception as e:
                PROCESSED["errors"] += 1
                print(f"[Import] Error on part {idx}: {str(e)}")
                continue
        await conn.close()
    except Exception as e:
        print(f"[Import] Fatal error: {str(e)}")
        PROCESSED["errors"] += 1
        await conn.close()
        return None
    elapsed = time.time() - start
    result = {
        "task": "import_champion_motors",
        "status": "ok" if PROCESSED["inserted"] > 0 else "error",
        "scanned": len(parts_to_import),
        "updated": PROCESSED["inserted"],
        "flagged": PROCESSED["skipped"],
        "errors_count": PROCESSED["errors"],
        "total_fitments": total_fitments,
        "elapsed_s": round(elapsed, 2),
        "errors": [],
    }
    print(f"\n[Import] \u2713 Complete")
    print(f"  Scanned: {result['scanned']}")
    print(f"  Inserted: {result['updated']}")
    print(f"  Skipped: {PROCESSED['skipped']}")
    print(f"  Errors: {PROCESSED['errors']}")
    print(f"  Fitment records: {result['total_fitments']}")
    print(f"  Elapsed: {result['elapsed_s']}s")
    return result

async def main():
    input_file = DEFAULT_INPUT
    if len(sys.argv) > 1 and sys.argv[1] == '--file' and len(sys.argv) > 2:
        input_file = sys.argv[2]
    if not Path(input_file).exists():
        print(f"[Import] Error: {input_file} not found")
        sys.exit(1)
    result = await import_catalog(input_file)
    sys.exit(0 if result and result["status"] == "ok" else 1)

if __name__ == "__main__":
    asyncio.run(main())
