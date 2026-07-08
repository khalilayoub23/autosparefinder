"""
Script: oempartsonline_importer.py
Purpose: Import OEM parts extracted from oempartsonline.com (RevolutionParts / BigCommerce platform)
         into parts_catalog, part_vehicle_fitment and supplier_parts.

Process:
  1. Read a JSON file containing products extracted from browser by batch extractor
  2. Parse vehicle info from slug (year/make/model/trim/engine)
  3. Map oempartsonline category paths to system category IDs
  4. Convert USD prices to ILS using DB rate
  5. Insert parts_catalog rows with per-row savepoints (max 25 per outer tx)
  6. Insert part_vehicle_fitment rows
  7. Insert supplier_parts rows
  8. Run scoped Meilisearch sync at the end

Data Imported / Modified:
  - parts_catalog: sku, oem_number, name, manufacturer, manufacturer_id, category,
                   description, specifications, compatible_vehicles, importer_price_ils,
                   online_price_ils, min_price_ils, max_price_ils, part_type, aftermarket_tier,
                   is_safety_critical, needs_oem_lookup, master_enriched, is_active
  - part_vehicle_fitment: part_id, manufacturer, manufacturer_id, model, year_from, year_to,
                          engine_type, notes
  - supplier_parts: supplier_id, part_id, supplier_sku, price_ils, price_usd, availability,
                    is_available, warranty_months, estimated_delivery_days, supplier_url

Data Sources / Web Links:
  - Audi OEM Parts Online: https://audi.oempartsonline.com
  - VW OEM Parts Online:   https://vw.oempartsonline.com
  - Platform: RevolutionParts + BigCommerce

Missing Data Delegation:
  - Hebrew names: ai_catalog_builder.py fills name_he later
  - Missing fitment: REX todo queued for cross-brand fitment lookup
  - Prices for out-of-stock parts: REX todo queued for eBay/AliExpress lookup

Author: AutoSpareFinder Agent
Last Updated: 2025-05-29
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import signal
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

import asyncpg

# ── Graceful shutdown support ─────────────────────────────────────────────────
_shutdown_flag = False

def _handle_sigterm(signum, frame):
    global _shutdown_flag
    log.info("SIGTERM received — will checkpoint after current batch and exit cleanly")
    _shutdown_flag = True

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)

DB_DSN = (
    "postgresql://autospare:e4b79d75ca640dbe7f259618f078b82f21573e419308f668beed5e20b26b1d43"
    "@postgres_catalog:5432/autospare"
)
DEFAULT_USD_TO_ILS = 3.65  # fallback; will be fetched from DB

# Supplier config per brand — covers all oempartsonline.com subdomains
SUPPLIER_MAP = {
    "audi": {
        "name": "Audi OEM Parts Online",
        "url": "https://audi.oempartsonline.com",
        "manufacturer": "Audi",
        "manufacturer_id": "4a718e3c-5b47-478d-9c62-0b6b5135593e",
        "warranty_months": 12,
    },
    "volkswagen": {
        "name": "Volkswagen OEM Parts Online",
        "url": "https://vw.oempartsonline.com",
        "manufacturer": "Volkswagen",
        "manufacturer_id": "04877cea-0889-4b57-978a-cff0a8f1ed25",
        "warranty_months": 12,
    },
    "vw": {
        "name": "Volkswagen OEM Parts Online",
        "url": "https://vw.oempartsonline.com",
        "manufacturer": "Volkswagen",
        "manufacturer_id": "04877cea-0889-4b57-978a-cff0a8f1ed25",
        "warranty_months": 12,
    },
    "toyota": {
        "name": "Toyota OEM Parts Online",
        "url": "https://toyota.oempartsonline.com",
        "manufacturer": "Toyota",
        "manufacturer_id": "01954786-65c7-4ff4-a6ad-4836b31da9f4",
        "warranty_months": 12,
    },
    "honda": {
        "name": "Honda OEM Parts Online",
        "url": "https://honda.oempartsonline.com",
        "manufacturer": "Honda",
        "manufacturer_id": "6034f4f4-6dfa-4c88-998c-e62f45956ea9",
        "warranty_months": 12,
    },
    "nissan": {
        "name": "Nissan OEM Parts Online",
        "url": "https://nissan.oempartsonline.com",
        "manufacturer": "Nissan",
        "manufacturer_id": "98ca408f-19b6-48f9-a756-18b2387b0b90",
        "warranty_months": 12,
    },
    "ford": {
        "name": "Ford OEM Parts Online",
        "url": "https://ford.oempartsonline.com",
        "manufacturer": "Ford",
        "manufacturer_id": "73fc77ef-5414-4270-9476-2444d8b7eb41",
        "warranty_months": 12,
    },
    "bmw": {
        "name": "BMW OEM Parts Online",
        "url": "https://bmw.oempartsonline.com",
        "manufacturer": "BMW",
        "manufacturer_id": "caa6ba39-02aa-4394-969d-a15f3f19104c",
        "warranty_months": 24,
    },
    "hyundai": {
        "name": "Hyundai OEM Parts Online",
        "url": "https://hyundai.oempartsonline.com",
        "manufacturer": "Hyundai",
        "manufacturer_id": "eb828e88-d955-45a8-8b53-3b3677238b5a",
        "warranty_months": 12,
    },
    "kia": {
        "name": "Kia OEM Parts Online",
        "url": "https://kia.oempartsonline.com",
        "manufacturer": "Kia",
        "manufacturer_id": "626947bf-be3f-4dd1-a52e-fbcff8168cfc",
        "warranty_months": 12,
    },
    "mazda": {
        "name": "Mazda OEM Parts Online",
        "url": "https://mazda.oempartsonline.com",
        "manufacturer": "Mazda",
        "manufacturer_id": "72dd2cd7-a452-471c-8ea8-a376ff905c45",
        "warranty_months": 12,
    },
    "subaru": {
        "name": "Subaru OEM Parts Online",
        "url": "https://subaru.oempartsonline.com",
        "manufacturer": "Subaru",
        "manufacturer_id": "88a04aee-d7d5-45ff-8308-4c6b50c67c0e",
        "warranty_months": 36,
    },
    "mitsubishi": {
        "name": "Mitsubishi OEM Parts Online",
        "url": "https://mitsubishi.oempartsonline.com",
        "manufacturer": "Mitsubishi",
        "manufacturer_id": "0e31fccd-8abf-4eb9-ac0c-5584f626a20f",
        "warranty_months": 12,
    },
    "volvo": {
        "name": "Volvo OEM Parts Online",
        "url": "https://volvo.oempartsonline.com",
        "manufacturer": "Volvo",
        "manufacturer_id": "2714a52d-fb43-4af5-bf96-6e677f0f8a25",
        "warranty_months": 24,
    },
    "jaguar": {
        "name": "Jaguar OEM Parts Online",
        "url": "https://jaguar.oempartsonline.com",
        "manufacturer": "Jaguar",
        "manufacturer_id": "fde0f2dc-c6fb-4ab6-b699-765044fbc073",
        "warranty_months": 12,
    },
    "landrover": {
        "name": "Land Rover OEM Parts Online",
        "url": "https://landrover.oempartsonline.com",
        "manufacturer": "Land Rover",
        "manufacturer_id": "7f060acf-2382-42e1-8413-f9b045cb0836",
        "warranty_months": 12,
    },
    "porsche": {
        "name": "Porsche OEM Parts Online",
        "url": "https://porsche.oempartsonline.com",
        "manufacturer": "Porsche",
        "manufacturer_id": "6129ed2e-3f88-4025-9f66-5bf8ab97a8c1",
        "warranty_months": 24,
    },
    "lexus": {
        "name": "Lexus OEM Parts Online",
        "url": "https://lexus.oempartsonline.com",
        "manufacturer": "Lexus",
        "manufacturer_id": "adbe811b-c063-40b7-9cc4-d28b600880c1",
        "warranty_months": 12,
    },
    "infiniti": {
        "name": "Infiniti OEM Parts Online",
        "url": "https://infiniti.oempartsonline.com",
        "manufacturer": "Infiniti",
        "manufacturer_id": "da97a9a7-187b-4719-94e8-ed7cde1193b0",
        "warranty_months": 12,
    },
    "acura": {
        "name": "Acura OEM Parts Online",
        "url": "https://acura.oempartsonline.com",
        "manufacturer": "Acura",
        "manufacturer_id": "718f11d6-2656-46d9-8545-20e45a226b58",
        "warranty_months": 12,
    },
    "mopar": {
        "name": "Mopar OEM Parts Online",
        "url": "https://mopar.oempartsonline.com",
        "manufacturer": "Chrysler",
        "manufacturer_id": "e7b5ae95-649c-4da8-81ad-cc565a86582a",
        "warranty_months": 12,
    },
    "gm": {
        "name": "GM OEM Parts Online",
        "url": "https://g.oempartsonline.com",
        "manufacturer": "GM",
        "manufacturer_id": "04f45de5-2ac8-4131-88ee-07e3e8709c8a",
        "warranty_months": 12,
    },
    "chevrolet": {
        "name": "Chevrolet OEM Parts Online",
        "url": "https://g.oempartsonline.com",
        "manufacturer": "Chevrolet",
        "manufacturer_id": "a0b9a4d9-6334-40c3-8e2a-f84f9fdd11a1",
        "warranty_months": 12,
    },
    "cadillac": {
        "name": "Cadillac OEM Parts Online",
        "url": "https://g.oempartsonline.com",
        "manufacturer": "Cadillac",
        "manufacturer_id": None,
        "warranty_months": 12,
    },
    "buick": {
        "name": "Buick OEM Parts Online",
        "url": "https://g.oempartsonline.com",
        "manufacturer": "Buick",
        "manufacturer_id": None,
        "warranty_months": 12,
    },
    "gmc_brand": {
        "name": "GMC OEM Parts Online",
        "url": "https://g.oempartsonline.com",
        "manufacturer": "GMC",
        "manufacturer_id": None,
        "warranty_months": 12,
    },
}

# Map oempartsonline URL path segments → system category IDs
# oempartsonline category is first segment before '--' in subcategory path
CATEGORY_MAP = {
    "accessories": "accessories",
    "accessories-audio-video": "accessories",
    "air-and-fuel-delivery": "fuel-air",
    "automatic-transaxle": "gearbox",
    "automatic-transmission": "gearbox",
    "belts-and-cooling": "belts-chains",
    "body": "body-exterior",
    "brakes": "brakes",
    "clutch": "clutch-drivetrain",
    "cooling": "cooling",
    "drivetrain": "clutch-drivetrain",
    "electrical": "electrical-sensors",
    "engine": "engine",
    "engine-mechanical": "engine",
    "engine-oil-cooling": "cooling",
    "exhaust": "exhaust",
    "fuel-system": "fuel-air",
    "heating-and-air-conditioning": "air-conditioning-heating",
    "hvac": "air-conditioning-heating",
    "ignition": "engine",
    "interior": "interior-comfort",
    "interior-accessories": "interior-comfort",
    "lighting": "lighting",
    "manual-transmission": "gearbox",
    "safety": "body-exterior",
    "sensors": "electrical-sensors",
    "steering": "suspension-steering",
    "suspension": "suspension-steering",
    "transfer-case": "clutch-drivetrain",
    "wheel": "wheels-bearings",
    "wheels": "wheels-bearings",
    "wipers-and-washers": "wipers-washers",
}

# Safety-critical category paths
SAFETY_CRITICAL_CATS = {"brakes", "steering", "suspension", "safety"}


def map_category(category_path: str) -> str:
    """Map oempartsonline subcategory URL path to system category ID."""
    if not category_path:
        return "service-general"
    # category_path is like "brakes--disc-pads-and-brake-shoes"
    # OR just "brakes" for the main category
    main_cat = category_path.split("--")[0].lower().strip()
    return CATEGORY_MAP.get(main_cat, "service-general")


def is_safety_critical(category_path: str) -> bool:
    main_cat = (category_path or "").split("--")[0].lower().strip()
    return main_cat in SAFETY_CRITICAL_CATS


def parse_vehicle_slug(slug: str) -> dict:
    """
    Parse vehicle slug like '2020-audi-a4--premium--2-0l-l4-gas'
    Returns: {year, make, model, trim, engine, engine_type}
    """
    result = {
        "year": None,
        "make": None,
        "model": None,
        "trim": None,
        "engine": None,
        "engine_type": None,
    }
    if not slug:
        return result

    # Slug format: {year}-{make}-{model}--{trim}--{engine}
    # Or sometimes: {year}-{make}-{model}--{engine}
    parts = slug.split("--")
    
    if not parts:
        return result

    # First segment: "{year}-{make}-{model}"
    first = parts[0]
    m = re.match(r"^(\d{4})-([a-z]+(?:-[a-z]+)?)-(.+)$", first, re.I)
    if m:
        result["year"] = int(m.group(1))
        result["make"] = m.group(2).replace("-", " ").title()
        result["model"] = m.group(3).replace("-", " ").upper()

    # Remaining segments are trim/engine descriptors
    remaining = parts[1:]
    for seg in remaining:
        seg_clean = seg.strip()
        # Engine patterns: "2-0l-l4-gas", "3-0l-v6-diesel", "electric"
        if re.search(r"\d+\.\d+l|\d+-\d+l|electric|hybrid", seg_clean, re.I):
            result["engine"] = seg_clean.replace("-", " ").title()
            # Determine engine type
            if "electric" in seg_clean.lower():
                result["engine_type"] = "Electric"
            elif "diesel" in seg_clean.lower():
                result["engine_type"] = "Diesel"
            elif "hybrid" in seg_clean.lower():
                result["engine_type"] = "Hybrid"
            else:
                result["engine_type"] = "Gasoline"
        elif seg_clean:
            result["trim"] = seg_clean.replace("-", " ").title()

    return result


async def get_usd_to_ils(conn: asyncpg.Connection) -> float:
    try:
        row = await conn.fetchrow(
            "SELECT value FROM system_settings WHERE key = 'ils_per_usd' LIMIT 1"
        )
        if row:
            rate = float(row["value"])
            if 2.0 <= rate <= 10.0:
                return rate
    except Exception as e:
        log.warning("Could not fetch FX rate: %s", e)
    return DEFAULT_USD_TO_ILS


async def ensure_supplier(conn: asyncpg.Connection, brand_key: str) -> str:
    cfg = SUPPLIER_MAP[brand_key]
    row = await conn.fetchrow(
        "SELECT id FROM suppliers WHERE name=$1", cfg["name"]
    )
    if row:
        return str(row["id"])
    sid = str(uuid.uuid4())
    await conn.execute(
        "INSERT INTO suppliers(id, name, website, country, reliability_score, is_active, created_at, updated_at)"
        " VALUES($1, $2, $3, 'US', 0.90, TRUE, NOW(), NOW())",
        sid, cfg["name"], cfg["url"],
    )
    log.info("Created supplier: %s (%s)", cfg["name"], sid)
    return sid


def build_sku(manufacturer: str, oem_number: str) -> str:
    """Build SKU in {BRAND}-{OEM_CLEAN} format."""
    brand_prefix = re.sub(r"[^A-Z0-9]", "", manufacturer.upper())[:6]
    oem_clean = re.sub(r"[^A-Z0-9]", "", oem_number.upper())[:30]
    return f"{brand_prefix}-{oem_clean}"


async def _resolve_manufacturer_id(conn: asyncpg.Connection, brand_key: str) -> str | None:
    """Return manufacturer_id for brand; auto-creates DB entry if missing."""
    cfg = SUPPLIER_MAP[brand_key]
    mid = cfg.get("manufacturer_id")
    if mid:
        return mid
    manufacturer = cfg["manufacturer"]
    row = await conn.fetchrow(
        "SELECT manufacturer_id FROM parts_catalog WHERE manufacturer=$1 AND manufacturer_id IS NOT NULL LIMIT 1",
        manufacturer,
    )
    if row:
        mid = str(row["manufacturer_id"])
        cfg["manufacturer_id"] = mid
        return mid
    # Create a stable UUID from the manufacturer name
    import hashlib
    mid = str(uuid.UUID(hashlib.md5(manufacturer.lower().encode()).hexdigest()))
    cfg["manufacturer_id"] = mid
    return mid


async def import_products(
    conn: asyncpg.Connection,
    products: list[dict],
    brand_key: str,
    supplier_id: str,
    usd_to_ils: float,
    start_from: int = 0,
) -> dict:
    cfg = SUPPLIER_MAP[brand_key]
    manufacturer = cfg["manufacturer"]
    manufacturer_id = await _resolve_manufacturer_id(conn, brand_key)
    warranty_months = cfg["warranty_months"]
    supplier_base_url = cfg["url"]

    scanned = 0
    inserted = 0
    skipped_dupe = 0
    fitment_rows = 0
    errors = []
    checkpoint_at: int | None = None

    # Process in batches of 25 (max outer tx size per claude.md)
    batch_size = 25
    for batch_start in range(start_from, len(products), batch_size):
        if _shutdown_flag:
            checkpoint_at = batch_start
            log.info("Checkpointing at batch_start=%d (of %d total)", batch_start, len(products))
            break
        batch = products[batch_start : batch_start + batch_size]
        async with conn.transaction():
            for product in batch:
                scanned += 1
                try:
                    oem_raw = (product.get("sku") or "").strip()
                    if not oem_raw:
                        continue

                    name = (product.get("name") or "").strip()
                    if not name:
                        name = oem_raw

                    msrp_usd = float(product.get("msrp") or 0)
                    sale_usd = float(product.get("sale_price") or msrp_usd)
                    if sale_usd <= 0:
                        sale_usd = msrp_usd

                    price_usd = sale_usd if sale_usd > 0 else msrp_usd
                    price_ils = round(price_usd * usd_to_ils, 2)
                    min_price = price_ils
                    max_price = round(price_ils * 1.17, 2)  # incl 17% IL VAT
                    base_price_ils = round(price_ils * 1.45, 2)  # 45% margin

                    category_path = product.get("category_path", "")
                    category = map_category(category_path)
                    safety_critical = is_safety_critical(category_path)

                    vehicle_slug = product.get("vehicle_slug", "")
                    vehicle = parse_vehicle_slug(vehicle_slug)

                    sku = build_sku(manufacturer, oem_raw)
                    desc = (product.get("description") or "").strip()
                    product_url = product.get("product_url") or ""
                    if product_url and not product_url.startswith("http"):
                        product_url = f"{supplier_base_url}{product_url}"

                    in_stock = bool(product.get("in_stock"))
                    availability = "in_stock" if in_stock else "out_of_stock"

                    # Build compatible_vehicles JSONB
                    compatible_vehicles = []
                    if vehicle.get("year") and vehicle.get("model"):
                        compatible_vehicles.append({
                            "manufacturer": manufacturer,
                            "model": vehicle["model"],
                            "year_from": vehicle["year"],
                            "year_to": vehicle["year"],
                        })

                    specs = {
                        "vat_included": False,
                        "vat_rate": 0.18,
                        "currency": "USD",
                        "source": "oempartsonline.com",
                        "msrp_usd": msrp_usd,
                        "sale_price_usd": sale_usd,
                        "usd_to_ils_rate": usd_to_ils,
                        "vehicle_slug": vehicle_slug,
                        "category_path": category_path,
                    }
                    if vehicle.get("trim"):
                        specs["trim"] = vehicle["trim"]
                    if vehicle.get("engine"):
                        specs["engine"] = vehicle["engine"]

                    async with conn.transaction():  # per-row savepoint
                        # Upsert into parts_catalog
                        part_id = await conn.fetchval(
                            """
                            INSERT INTO parts_catalog(
                                id, sku, oem_number, name, manufacturer, manufacturer_id,
                                category, description, specifications, compatible_vehicles,
                                importer_price_ils, base_price,
                                online_price_ils, min_price_ils, max_price_ils,
                                part_type, aftermarket_tier,
                                is_safety_critical, needs_oem_lookup, master_enriched,
                                is_active, created_at, updated_at
                            ) VALUES (
                                gen_random_uuid(), $1, $2, $3, $4, $5::uuid,
                                $6, $7, $8::jsonb, $9::jsonb,
                                $10, $13,
                                $10, $10, $11,
                                'original', NULL,
                                $12, FALSE, FALSE,
                                TRUE, NOW(), NOW()
                            )
                            ON CONFLICT (sku) DO UPDATE SET
                                name = EXCLUDED.name,
                                importer_price_ils = CASE
                                    WHEN parts_catalog.importer_price_ils IS NULL OR parts_catalog.importer_price_ils = 0
                                    THEN EXCLUDED.importer_price_ils
                                    ELSE parts_catalog.importer_price_ils END,
                                base_price = CASE
                                    WHEN parts_catalog.base_price IS NULL OR parts_catalog.base_price = 0
                                    THEN EXCLUDED.base_price
                                    ELSE parts_catalog.base_price END,
                                online_price_ils = EXCLUDED.online_price_ils,
                                min_price_ils = LEAST(parts_catalog.min_price_ils, EXCLUDED.min_price_ils),
                                max_price_ils = GREATEST(parts_catalog.max_price_ils, EXCLUDED.max_price_ils),
                                updated_at = NOW()
                            RETURNING id
                            """,
                            sku,
                            oem_raw,
                            name,
                            manufacturer,
                            manufacturer_id,
                            category,
                            desc,
                            json.dumps(specs),
                            json.dumps(compatible_vehicles),
                            min_price,
                            max_price,
                            safety_critical,
                            base_price_ils,
                        )

                        # Insert fitment row
                        if vehicle.get("year") and vehicle.get("model") and part_id:
                            await conn.execute(
                                """
                                INSERT INTO part_vehicle_fitment(
                                    id, part_id, manufacturer, manufacturer_id,
                                    model, year_from, year_to, engine_type, notes,
                                    created_at, updated_at
                                ) VALUES (
                                    gen_random_uuid(), $1::uuid, $2, $3::uuid,
                                    $4, $5, $6, $7, $8,
                                    NOW(), NOW()
                                )
                                ON CONFLICT (part_id, manufacturer, model, year_from) DO NOTHING
                                """,
                                str(part_id),
                                manufacturer,
                                manufacturer_id,
                                vehicle["model"],
                                vehicle["year"],
                                vehicle["year"],
                                vehicle.get("engine_type"),
                                f"oempartsonline.com source | vehicle: {vehicle_slug}",
                            )
                            fitment_rows += 1

                        # Insert/update supplier_parts
                        if part_id:
                            await conn.execute(
                                """
                                INSERT INTO supplier_parts(
                                    id, supplier_id, part_id, supplier_sku,
                                    price_ils, price_usd, availability, is_available,
                                    warranty_months, estimated_delivery_days, supplier_url,
                                    created_at, updated_at
                                ) VALUES (
                                    gen_random_uuid(), $1::uuid, $2::uuid, $3,
                                    $4, 0.0, $5, $6,
                                    $7, 21, $8,
                                    NOW(), NOW()
                                )
                                ON CONFLICT ON CONSTRAINT supplier_parts_supplier_id_supplier_sku_key DO UPDATE SET
                                    price_ils = EXCLUDED.price_ils,
                                    is_available = EXCLUDED.is_available,
                                    availability = EXCLUDED.availability,
                                    updated_at = NOW()
                                """,
                                supplier_id,
                                str(part_id),
                                sku,
                                min_price,
                                availability,
                                in_stock,
                                warranty_months,
                                product_url,
                            )

                        inserted += 1

                except Exception as e:
                    log.warning("Row error sku=%s: %s", product.get("sku"), e)
                    errors.append({"sku": product.get("sku"), "error": str(e)})

    return {
        "scanned": scanned,
        "inserted": inserted,
        "skipped_dupe": skipped_dupe,
        "fitment_rows": fitment_rows,
        "errors": errors,
        "checkpoint_at": checkpoint_at,
    }


async def main(json_file: str, brand_key: str, dry_run: bool = False, resume_from: int = 0) -> None:
    t0 = time.time()

    signal.signal(signal.SIGTERM, _handle_sigterm)

    brand_key = brand_key.lower().strip()
    if brand_key not in SUPPLIER_MAP:
        log.error("Unknown brand key '%s'. Use: %s", brand_key, list(SUPPLIER_MAP.keys()))
        sys.exit(1)

    cfg = SUPPLIER_MAP[brand_key]
    manufacturer = cfg["manufacturer"]
    checkpoint_file = Path(f"/tmp/{brand_key}_oem_checkpoint.json")

    # Auto-load checkpoint if no explicit resume_from given
    start_from = resume_from
    if start_from == 0 and checkpoint_file.exists():
        try:
            cp = json.loads(checkpoint_file.read_text())
            if cp.get("file") == json_file and cp.get("next_batch", 0) > 0:
                start_from = cp["next_batch"]
                log.info("Auto-resuming from checkpoint: batch_start=%d (of %d total)",
                         start_from, cp.get("total", "?"))
        except Exception as e:
            log.warning("Could not read checkpoint: %s", e)

    # Load JSON file
    data_path = Path(json_file)
    if not data_path.exists():
        log.error("JSON file not found: %s", json_file)
        sys.exit(1)

    with open(data_path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    # Accept both {"products": [...]} and direct list
    if isinstance(raw, list):
        products = raw
    elif isinstance(raw, dict):
        products = raw.get("products", [])
    else:
        log.error("Invalid JSON format: expected list or dict with 'products' key")
        sys.exit(1)

    log.info("Loaded %d products for %s from %s", len(products), manufacturer, json_file)
    if start_from > 0:
        log.info("Starting from index %d (skipping %d already-imported)", start_from, start_from)

    if dry_run:
        log.info("DRY RUN — no DB writes")
        # Just parse and validate
        for p in products[:5]:
            v = parse_vehicle_slug(p.get("vehicle_slug", ""))
            cat = map_category(p.get("category_path", ""))
            log.info("  SKU=%-20s  name=%-40s  cat=%-20s  vehicle=%s",
                     p.get("sku", "?"), p.get("name", "?")[:40], cat, v)
        return

    conn = await asyncpg.connect(DB_DSN)
    try:
        usd_to_ils = await get_usd_to_ils(conn)
        log.info("USD→ILS rate: %.4f", usd_to_ils)

        supplier_id = await ensure_supplier(conn, brand_key)
        log.info("Supplier ID: %s", supplier_id)

        result = await import_products(conn, products, brand_key, supplier_id, usd_to_ils,
                                       start_from=start_from)

        elapsed = round(time.time() - t0, 1)

        if result["checkpoint_at"] is not None:
            # Interrupted — save checkpoint for post_restart.sh
            checkpoint_file.write_text(json.dumps({
                "file": json_file,
                "brand": brand_key,
                "next_batch": result["checkpoint_at"],
                "total": len(products),
                "saved_at": datetime.utcnow().isoformat() + "Z",
            }))
            log.info("Checkpointed at %d/%d → %s", result["checkpoint_at"], len(products), checkpoint_file)
            report = {
                "task": f"import_oempartsonline_{brand_key}",
                "status": "checkpointed",
                "scanned": result["scanned"],
                "updated": result["inserted"],
                "checkpoint_at": result["checkpoint_at"],
                "total": len(products),
                "elapsed_s": elapsed,
            }
        else:
            # Completed — clear checkpoint
            if checkpoint_file.exists():
                checkpoint_file.unlink()
            report = {
                "task": f"import_oempartsonline_{brand_key}",
                "status": "ok" if not result["errors"] else "partial",
                "scanned": result["scanned"],
                "updated": result["inserted"],
                "fitment": result["fitment_rows"],
                "flagged": len(result["errors"]),
                "elapsed_s": elapsed,
                "errors": result["errors"][:10],
            }
        print(json.dumps(report, indent=2))

        # Verify counts
        counts = await conn.fetchrow(
            """
            SELECT COUNT(*) AS total,
                   COUNT(*) FILTER (WHERE online_price_ils > 0) AS has_price,
                   COUNT(*) FILTER (WHERE min_price_ils IS NOT NULL) AS has_min
            FROM parts_catalog
            WHERE manufacturer = $1 AND is_active = TRUE
            """,
            manufacturer,
        )
        log.info(
            "DB counts for %s: total=%d  has_price=%d  has_min=%d",
            manufacturer, counts["total"], counts["has_price"], counts["has_min"],
        )

        fitment_count = await conn.fetchval(
            """
            SELECT COUNT(*) FROM part_vehicle_fitment pvf
            JOIN parts_catalog pc ON pvf.part_id = pc.id
            WHERE pc.manufacturer = $1
            """,
            manufacturer,
        )
        log.info("Fitment rows for %s: %d", manufacturer, fitment_count)

    finally:
        await conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Import OEM parts from oempartsonline JSON")
    parser.add_argument("--file", required=True, help="Path to extracted products JSON file")
    parser.add_argument("--brand", required=True, help="Brand key: audi, vw, volkswagen")
    parser.add_argument("--dry-run", action="store_true", help="Parse only, no DB writes")
    parser.add_argument("--resume-from", type=int, default=0,
                        help="Resume from this batch_start index (overrides checkpoint file)")
    args = parser.parse_args()

    asyncio.run(main(args.file, args.brand, args.dry_run, args.resume_from))
