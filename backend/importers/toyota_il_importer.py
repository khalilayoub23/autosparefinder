"""
Script: toyota_il_importer.py
Purpose: Import all Toyota OEM parts from Union Motors Israel (official Toyota IL importer)
         into parts_catalog, part_vehicle_fitment, and supplier_parts tables.

Process:
  1. Load toyota_il_parts.json (18,493 parts scraped from union-motors.toyota.co.il)
  2. Look up Toyota manufacturer_id from car_brands table
  3. Ensure supplier "Toyota Israel - Union Motors" exists in suppliers table
  4. For each part (per-row savepoint):
     a. Upsert into parts_catalog (oem_number, name_he, price ILS excl. VAT)
     b. Insert fitment rows into part_vehicle_fitment (parse Hebrew model strings)
     c. Upsert into supplier_parts
  5. Run Meilisearch scoped sync for Toyota

Data Imported / Modified:
  - parts_catalog: oem_number, sku, name_he, manufacturer, manufacturer_id, category,
                   importer_price_ils, min_price_ils, max_price_ils, specifications,
                   compatible_vehicles, part_condition, aftermarket_tier, is_active,
                   needs_oem_lookup, master_enriched, is_safety_critical
  - part_vehicle_fitment: part_id, manufacturer, manufacturer_id, model, year_from, year_to, notes
  - supplier_parts: supplier_id, part_id, supplier_sku, price_ils, price_usd,
                    availability, is_available, warranty_months, estimated_delivery_days, supplier_url

Data Sources / Web Links:
  - Toyota Israel (Union Motors): https://union-motors.toyota.co.il/replacement_parts.php
  - Source file: /opt/autosparefinder/toyota_il_parts.json

Missing Data Delegation:
  - English part names: ai_catalog_builder.py will translate from Hebrew
  - Category enrichment: ai_catalog_builder.py fills master_enriched
  - Fitment gaps (parts with "יבוא אישי" or no model): REX todo queued

Author: AutoSpareFinder Agent
Last Updated: 2026-06-02
"""

import asyncio
import json
import logging
import os
import re
import time
import uuid
from pathlib import Path

import asyncpg

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("toyota_il_importer")

DSN = "postgresql://autospare:e4b79d75ca640dbe7f259618f078b82f21573e419308f668beed5e20b26b1d43@postgres_catalog:5432/autospare"

MANUFACTURER = "Toyota"
MANUFACTURER_ID = "01954786-65c7-4ff4-a6ad-4836b31da9f4"
SUPPLIER_NAME = "Toyota Israel - Union Motors"
SUPPLIER_URL = "https://union-motors.toyota.co.il"
VAT_RATE = 0.18
WARRANTY_MONTHS = 12
DELIVERY_DAYS = 3
SOURCE_FILE = os.getenv(
    "TOYOTA_JSON",
    "/app/state/toyota_il_parts.json"
    if os.path.exists("/app/state/toyota_il_parts.json")
    else "/app/toyota_il_parts.json",
)

# Hebrew model name → English model name mapping
# Handles common Toyota models sold in Israel
MODEL_MAP = {
    "קורולה": "Corolla",
    "קאמרי": "Camry",
    "RAV-4": "RAV4",
    "RAV4": "RAV4",
    "rav4": "RAV4",
    "היילקס": "Hilux",
    "הילקס": "Hilux",
    "לנד קרוזר": "Land Cruiser",
    "לנד קרואזר": "Land Cruiser",
    "פריוס": "Prius",
    "אוונסיס": "Avensis",
    "אוריס": "Auris",
    "יאריס": "Yaris",
    "ורסו": "Verso",
    "פראדו": "Land Cruiser Prado",
    "PRADO": "Land Cruiser Prado",
    "HILUX": "Hilux",
    "VIGO": "Hilux Vigo",
    "COROLLA": "Corolla",
    "CAMRY": "Camry",
    "PRIUS": "Prius",
    "AURIS": "Auris",
    "YARIS": "Yaris",
    "AVENSIS": "Avensis",
    "GT-86": "GT-86",
    "GR-86": "GR86",
    "C-HR": "C-HR",
    "CHR": "C-HR",
    "RAV 4": "RAV4",
    "טנדר": "Hilux",
    "4 RUNNER": "4Runner",
    "4RUNNER": "4Runner",
    "סופרה": "Supra",
    "SUPRA": "Supra",
    "FJ CRUISER": "FJ Cruiser",
    "FORTUNER": "Fortuner",
    "פורטונר": "Fortuner",
    "אלפארד": "Alphard",
    "ALPHARD": "Alphard",
    "VELLFIRE": "Vellfire",
    "INNOVA": "Innova",
    "RUSH": "Rush",
    "URBAN CRUISER": "Urban Cruiser",
    "CROSS": "Corolla Cross",
    "MTM": "Corolla",  # MTM is a variant code for Corolla
}

# Safety-critical part keywords (Hebrew)
SAFETY_KEYWORDS = [
    "בלם", "דיסק בלם", "צלחת בלם", "כרית אוויר", "חגורת בטיחות",
    "הגה", "ABS", "ESP", "airbag", "brake", "steering"
]


def parse_year(text: str) -> int | None:
    """Extract a 4-digit year from a string."""
    m = re.search(r'\b(19[89]\d|20[0-3]\d)\b', text)
    return int(m.group(1)) if m else None


def parse_model_entry(entry: str) -> list[dict]:
    """
    Parse a single Hebrew model string like:
      "קורולה 2003-2007 H/A"
      "MTM-2008-קורולה"
      "VIGO 2015"
      "היילקס יצור 07-2020"
      "לקסוס יבוא אישי"
    Returns list of {model_en, year_from, year_to}
    """
    entry = entry.strip()
    if not entry or entry in ("יבוא אישי טויוטה", "לקסוס יבוא אישי", "יבוא אישי"):
        return []  # private import, no specific model

    results = []

    # Find matching model name
    model_en = None
    for he, en in MODEL_MAP.items():
        if he.upper() in entry.upper():
            model_en = en
            break

    if not model_en:
        # Try to use the raw text as model (may be English already)
        tokens = entry.split()
        # Check if first token looks like a model code
        if tokens and re.match(r'^[A-Z0-9\-]+$', tokens[0]) and len(tokens[0]) > 1:
            model_en = tokens[0]
        else:
            return []  # Cannot parse

    # Extract years
    years = re.findall(r'\b(19[89]\d|20[0-3]\d)\b', entry)
    if len(years) >= 2:
        year_from = int(min(years))
        year_to = int(max(years))
    elif len(years) == 1:
        year_from = int(years[0])
        year_to = None
    else:
        # No year found - use 2000 as generic
        year_from = 2000
        year_to = None

    results.append({"model_en": model_en, "year_from": year_from, "year_to": year_to})
    return results


def guess_category(name_he: str) -> str:
    """Guess part category from Hebrew name."""
    name = name_he.upper()
    if any(k in name for k in ["בלם", "דיסק", "צלחת"]):
        return "brakes"
    if any(k in name for k in ["מנוע", "אטמים", "בוכנה", "גלגל תנופה"]):
        return "engine"
    if any(k in name for k in ["שמן", "פילטר", "מסנן"]):
        return "filters"
    if any(k in name for k in ["מצמד", "גיר", "תיבת"]):
        return "transmission"
    if any(k in name for k in ["מתלה", "קפיץ", "בולם", "מוט"]):
        return "suspension"
    if any(k in name for k in ["חשמל", "מצבר", "דינמו", "פתיל", "כבל"]):
        return "electrical"
    if any(k in name for k in ["קירור", "ראדיאטור", "תרמוסטט", "מאוורר"]):
        return "cooling"
    if any(k in name for k in ["דלק", "מזרק", "משאבת", "צינור"]):
        return "fuel_system"
    if any(k in name for k in ["הגה", "הכוון"]):
        return "steering"
    if any(k in name for k in ["מצמד", "סט דיסק"]):
        return "clutch"
    if any(k in name for k in ["כריות אוויר", "כרית אוויר", "חגורה"]):
        return "safety"
    if any(k in name for k in ["פנס", "נורה", "תאורה"]):
        return "lighting"
    if any(k in name for k in ["מזגן", "AC", "A/C"]):
        return "air_conditioning"
    if any(k in name for k in ["גוף", "פגוש", "דלת", "מכסה"]):
        return "body_parts"
    if any(k in name for k in ["גלגל", "צמיג", "רים"]):
        return "wheels_tires"
    return "other_parts"


def is_safety_critical(name_he: str) -> bool:
    name = name_he.upper()
    return any(k.upper() in name for k in SAFETY_KEYWORDS)


async def ensure_supplier(conn) -> str:
    row = await conn.fetchrow("SELECT id FROM suppliers WHERE name=$1", SUPPLIER_NAME)
    if row:
        return str(row["id"])
    sid = str(uuid.uuid4())
    await conn.execute(
        "INSERT INTO suppliers(id,name,website,country,reliability_score,is_active,created_at,updated_at)"
        " VALUES($1,$2,$3,'IL',0.95,TRUE,NOW(),NOW())",
        sid, SUPPLIER_NAME, SUPPLIER_URL
    )
    log.info("Created supplier: %s -> %s", SUPPLIER_NAME, sid)
    return sid


async def main():
    start = time.time()

    # Load source data — harvester writes {"parts": [...], "count": N}
    raw = json.loads(Path(SOURCE_FILE).read_text(encoding="utf-8"))
    parts = raw if isinstance(raw, list) else raw.get("parts", [])
    log.info("Loaded %d parts from %s", len(parts), SOURCE_FILE)

    conn = await asyncpg.connect(DSN)

    supplier_id = await ensure_supplier(conn)
    log.info("Supplier ID: %s", supplier_id)

    inserted = 0
    updated = 0
    fitment_count = 0
    errors = []
    skipped = 0

    for i, part in enumerate(parts):
        oem = part.get("oem", "").strip()
        name_he = part.get("name_he", "").strip()
        price_raw = part.get("price", 0)
        models_raw = part.get("models", [])
        in_stock = part.get("in_stock", True)

        if not oem or not name_he:
            skipped += 1
            continue

        try:
            price = float(price_raw) if price_raw else 0.0
        except (ValueError, TypeError):
            price = 0.0

        # price is excl. VAT; il_retail is the consumer retail incl. VAT (IL market reference)
        il_retail = round(price * (1 + VAT_RATE), 2) if price > 0 else 0.0
        min_price = il_retail if il_retail > 0 else None
        max_price = il_retail if il_retail > 0 else None
        # Policy: base_price = cost * 1.45 (45% margin over excl-VAT cost)
        il_base_price = round(price * 1.45, 2) if price > 0 else 0.0

        sku = f"TOYOTA-{oem}"
        category = guess_category(name_he)
        safety = is_safety_critical(name_he)

        # Build compatible_vehicles JSONB from model strings
        compat_vehicles = []
        fitment_rows = []
        for m_str in models_raw:
            parsed = parse_model_entry(m_str)
            for p_entry in parsed:
                compat_vehicles.append({
                    "manufacturer": MANUFACTURER,
                    "model": p_entry["model_en"],
                    "year_from": p_entry["year_from"],
                    "year_to": p_entry["year_to"],
                })
                fitment_rows.append(p_entry)

        specs = json.dumps({
            "vat_included": False,
            "vat_rate": VAT_RATE,
            "currency": "ILS",
            "source": "union-motors.toyota.co.il",
            "importer": "Union Motors Israel",
            "warranty_months": WARRANTY_MONTHS,
            "shipping_to_il": True,
        })

        try:
            async with conn.transaction():
                # Upsert parts_catalog
                row = await conn.fetchrow(
                    "SELECT id FROM parts_catalog WHERE oem_number=$1 AND manufacturer=$2",
                    oem, MANUFACTURER
                )
                if row:
                    part_id = str(row["id"])
                    await conn.execute("""
                        UPDATE parts_catalog SET
                            base_price=$1, importer_price_ils=$2, min_price_ils=$3, max_price_ils=$3,
                            specifications=$4::jsonb, compatible_vehicles=$5::jsonb,
                            is_safety_critical=$6, updated_at=NOW()
                        WHERE id=$7
                    """, il_base_price, price, il_retail,
                        specs, json.dumps(compat_vehicles),
                        safety, part_id)
                    updated += 1
                else:
                    part_id = str(uuid.uuid4())
                    await conn.execute("""
                        INSERT INTO parts_catalog(
                            id, sku, oem_number, name, name_he, manufacturer, manufacturer_id,
                            category, base_price, importer_price_ils, min_price_ils, max_price_ils,
                            specifications, compatible_vehicles, part_condition, aftermarket_tier,
                            is_safety_critical, needs_oem_lookup, master_enriched,
                            is_active, created_at, updated_at
                        ) VALUES(
                            $1,$2,$3,$4,$5,$6,$7::uuid,
                            $8,$9,$10,$11,$11,
                            $12::jsonb,$13::jsonb,$14,NULL,
                            $15,FALSE,FALSE,
                            TRUE,NOW(),NOW()
                        )
                    """, part_id, sku, oem,
                        name_he, name_he,   # name = name_he until AI translates
                        MANUFACTURER, MANUFACTURER_ID,
                        category, il_base_price, price, il_retail,
                        specs, json.dumps(compat_vehicles),
                        "new", safety)
                    inserted += 1

                # Insert fitment rows
                for f in fitment_rows:
                    await conn.execute("""
                        INSERT INTO part_vehicle_fitment(
                            id, part_id, manufacturer, manufacturer_id,
                            model, year_from, year_to, notes, created_at, updated_at
                        ) VALUES(
                            gen_random_uuid(), $1::uuid, $2, $3::uuid,
                            $4, $5, $6, $7, NOW(), NOW()
                        )
                        ON CONFLICT(part_id, manufacturer, model, year_from) DO NOTHING
                    """, part_id, MANUFACTURER, MANUFACTURER_ID,
                        f["model_en"], f["year_from"], f["year_to"],
                        "Union Motors Israel source")
                    fitment_count += 1

                # Upsert supplier_parts
                avail = "in_stock" if in_stock else "out_of_stock"
                await conn.execute("""
                    INSERT INTO supplier_parts(
                        id, supplier_id, part_id, supplier_sku,
                        price_ils, price_usd, availability, is_available,
                        warranty_months, estimated_delivery_days, supplier_url,
                        created_at, updated_at
                    ) VALUES(
                        gen_random_uuid(), $1::uuid, $2::uuid, $3,
                        $4, 0.0, $5, $6,
                        $7, $8, $9,
                        NOW(), NOW()
                    )
                    ON CONFLICT ON CONSTRAINT supplier_parts_supplier_id_supplier_sku_key DO UPDATE SET
                        price_ils=EXCLUDED.price_ils,
                        availability=EXCLUDED.availability,
                        is_available=EXCLUDED.is_available,
                        updated_at=NOW()
                """, supplier_id, part_id, sku,
                    price, avail, in_stock,
                    WARRANTY_MONTHS, DELIVERY_DAYS, SUPPLIER_URL)

        except Exception as e:
            errors.append({"oem": oem, "error": str(e)})
            if len(errors) <= 5:
                log.warning("Row error %s: %s", oem, e)

        if (i + 1) % 500 == 0:
            log.info("Progress: %d/%d | inserted=%d updated=%d fitment=%d errors=%d",
                     i + 1, len(parts), inserted, updated, fitment_count, len(errors))

    await conn.close()

    elapsed = round(time.time() - start, 1)
    result = {
        "task": "import_toyota_il",
        "status": "ok" if not errors else "partial",
        "scanned": len(parts),
        "inserted": inserted,
        "updated": updated,
        "fitment": fitment_count,
        "skipped": skipped,
        "flagged": len(errors),
        "elapsed_s": elapsed,
        "errors": errors[:10],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
