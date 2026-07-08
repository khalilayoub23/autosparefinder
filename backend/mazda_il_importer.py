"""
Script: mazda_il_importer.py
Purpose: Harvest ALL Mazda OEM parts from Delek Motors Israel official API and
         import into parts_catalog, part_vehicle_fitment, and supplier_parts.

Process:
  1. Query Delek Motors API (serviceforms.delek-motors.co.il) with all Hebrew +
     Latin + digit seeds to collect every Mazda part (brandId=1)
  2. Deduplicate by OEM number (item/sku field)
  3. For each part (per-row savepoint):
     a. Upsert into parts_catalog with ILS price (priceWithTax = incl. VAT 18%)
     b. Parse vehicleModel / modelDescription for fitment
     c. Upsert into supplier_parts
  4. Print standard result JSON

Data Imported / Modified:
  - parts_catalog: oem_number, sku, name_he, name (foreignName), manufacturer,
                   manufacturer_id, category, importer_price_ils, min_price_ils,
                   max_price_ils, specifications, compatible_vehicles, part_condition,
                   aftermarket_tier, is_active, needs_oem_lookup, master_enriched
  - part_vehicle_fitment: part_id, manufacturer, manufacturer_id, model, year_from, notes
  - supplier_parts: supplier_id, part_id, supplier_sku, price_ils, price_usd,
                    availability, is_available, warranty_months, estimated_delivery_days

Data Sources / Web Links:
  - Delek Motors Israel API: https://serviceforms.delek-motors.co.il/home/GetPriceListReplacements
  - Mazda Israel website: https://www.mazda.co.il

Missing Data Delegation:
  - Fitment detail (year ranges): REX todo queued for samelet.com / autodoc lookup
  - AI translation/enrichment: ai_catalog_builder.py fills master_enriched

Author: AutoSpareFinder Agent
Last Updated: 2026-06-02
"""

import asyncio
import json
import logging
import time
import urllib.parse
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

import asyncpg

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("mazda_il_importer")

DSN = "postgresql://autospare:e4b79d75ca640dbe7f259618f078b82f21573e419308f668beed5e20b26b1d43@postgres_catalog:5432/autospare"

MANUFACTURER = "Mazda"
MANUFACTURER_ID = "72dd2cd7-a452-471c-8ea8-a376ff905c45"
SUPPLIER_NAME = "Mazda Israel - Delek Motors"
SUPPLIER_URL = "https://www.mazda.co.il"
DELEK_BRAND_ID = 1
VAT_RATE = 0.18  # current Israeli VAT rate
WARRANTY_MONTHS = 24  # official Israeli importer
DELIVERY_DAYS = 3
API_URL = "https://serviceforms.delek-motors.co.il/home/GetPriceListReplacements"
API_HEADERS = {
    "Referer": "https://www.mazda.co.il/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
}

# All seeds: Hebrew letters + Latin letters + digits
SEEDS = (
    list("אבגדהוזחטיכלמנסעפצקרשת")
    + list("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    + list("0123456789")
)

# Mazda model name → English canonical
MAZDA_MODELS = {
    "CX5": "CX-5", "CX-5": "CX-5",
    "CX3": "CX-3", "CX-3": "CX-3",
    "CX30": "CX-30", "CX-30": "CX-30",
    "CX60": "CX-60", "CX-60": "CX-60",
    "CX90": "CX-90", "CX-90": "CX-90",
    "M3": "Mazda3", "MAZDA3": "Mazda3",
    "M6": "Mazda6", "MAZDA6": "Mazda6",
    "M2": "Mazda2", "MAZDA2": "Mazda2",
    "MX5": "MX-5", "MX-5": "MX-5",
    "BT50": "BT-50", "BT-50": "BT-50",
    "B2500": "B2500",
    "626": "626", "323": "323",
    "MPV": "MPV", "TRIBUTE": "Tribute",
    "מאזדה3": "Mazda3", "מאזדה6": "Mazda6",
    "מאזדה2": "Mazda2",
}

SAFETY_KEYWORDS = ["בלם", "דיסק", "כרית אוויר", "חגורה", "ABS", "ESP", "airbag"]


def fetch_seed(seed: str) -> list:
    url = f"{API_URL}?brandId={DELEK_BRAND_ID}&sku=&name={urllib.parse.quote(seed)}"
    req = urllib.request.Request(url, headers=API_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read()).get("data") or []
    except Exception as e:
        log.warning("seed %r: %s", seed, e)
        return []


def harvest_all() -> list:
    """Fetch all parts from API using all seeds, deduplicate by OEM."""
    seen = set()
    parts = []
    log.info("Harvesting %d seeds from Delek Mazda API...", len(SEEDS))
    with ThreadPoolExecutor(max_workers=10) as ex:
        futures = {ex.submit(fetch_seed, s): s for s in SEEDS}
        done = 0
        for fut in as_completed(futures):
            seed = futures[fut]
            items = fut.result()
            for p in items:
                oem = (p.get("item") or "").strip().upper()
                if not oem or len(oem) < 3 or oem in seen:
                    continue
                seen.add(oem)
                parts.append(p)
            done += 1
            if done % 10 == 0:
                log.info("Seeds done: %d/%d | unique parts: %d", done, len(SEEDS), len(parts))
    log.info("Harvest complete: %d unique parts", len(parts))
    return parts


def guess_category(name_he: str, name_en: str) -> str:
    text = (name_he + " " + name_en).upper()
    if any(k in text for k in ["BRAKE", "DISC", "בלם", "דיסק"]):
        return "brakes"
    if any(k in text for k in ["ENGINE", "SEAL", "GASKET", "מנוע", "אטם"]):
        return "engine"
    if any(k in text for k in ["FILTER", "OIL FILTER", "פילטר", "מסנן"]):
        return "filters"
    if any(k in text for k in ["TRANSMISSION", "CLUTCH", "GEARBOX", "גיר", "מצמד"]):
        return "transmission"
    if any(k in text for k in ["SUSPENSION", "SPRING", "SHOCK", "מתלה", "קפיץ", "בולם"]):
        return "suspension"
    if any(k in text for k in ["ELECTRICAL", "SENSOR", "חיישן", "חשמל"]):
        return "electrical"
    if any(k in text for k in ["COOLING", "RADIATOR", "THERMOSTAT", "קירור", "ראדיאטור"]):
        return "cooling"
    if any(k in text for k in ["FUEL", "INJECTOR", "PUMP", "דלק", "מזרק"]):
        return "fuel_system"
    if any(k in text for k in ["STEERING", "הגה"]):
        return "steering"
    if any(k in text for k in ["AIRBAG", "BELT", "כרית", "חגורה"]):
        return "safety"
    if any(k in text for k in ["LIGHT", "LAMP", "BULB", "פנס", "נורה"]):
        return "lighting"
    if any(k in text for k in ["AC", "AIR CON", "CLIMATE", "מזגן"]):
        return "air_conditioning"
    if any(k in text for k in ["BODY", "BUMPER", "DOOR", "גוף", "פגוש", "דלת"]):
        return "body_parts"
    return "other_parts"


def parse_fitment(name_he: str, model_desc: str) -> list[dict]:
    """Extract model fitment from Hebrew name prefix and modelDescription."""
    fitments = []
    text = (name_he + " " + model_desc).upper()

    for key, model_en in MAZDA_MODELS.items():
        if key.upper() in text:
            fitments.append({"model_en": model_en, "year_from": 2000, "year_to": None})
            break  # one model per part (name prefix)

    return fitments


async def ensure_supplier(conn) -> str:
    row = await conn.fetchrow("SELECT id FROM suppliers WHERE name=$1", SUPPLIER_NAME)
    if row:
        return str(row["id"])
    sid = str(uuid.uuid4())
    await conn.execute(
        "INSERT INTO suppliers(id,name,website,country,reliability_score,is_active,created_at,updated_at)"
        " VALUES($1,$2,$3,'IL',0.95,TRUE,NOW(),NOW())",
        sid, SUPPLIER_NAME, SUPPLIER_URL,
    )
    log.info("Created supplier: %s -> %s", SUPPLIER_NAME, sid)
    return sid


async def main():
    start = time.time()

    # Step 1: Harvest
    raw_parts = harvest_all()
    log.info("Importing %d parts into DB...", len(raw_parts))

    conn = await asyncpg.connect(DSN)
    supplier_id = await ensure_supplier(conn)

    inserted = updated = fitment_count = errors_count = 0
    errors = []

    for i, p in enumerate(raw_parts):
        oem = (p.get("item") or "").strip()
        name_he = (p.get("name") or "").strip()
        name_en = (p.get("foreignName") or "").strip()
        price_with_tax = float(p.get("priceWithTax") or 0)
        model_desc = (p.get("modelDescription") or "").strip()
        is_original = (p.get("isOriginal") or "") == "מקורי"

        if not oem or not name_he:
            continue

        # Price: priceWithTax is ILS incl. VAT — IL market consumer price
        il_retail = round(price_with_tax, 2) if price_with_tax > 0 else 0.0
        # Derive excl-VAT cost and our selling price
        il_cost   = round(il_retail / (1 + VAT_RATE), 2) if il_retail > 0 else 0.0
        il_selling = round(il_cost * 1.45, 2) if il_cost > 0 else 0.0
        min_price = il_cost if il_cost > 0 else None
        max_price = il_retail if il_retail > 0 else None

        sku = f"MAZDA-{oem}"
        category = guess_category(name_he, name_en)
        safety = any(k in name_he.upper() for k in [k.upper() for k in SAFETY_KEYWORDS])
        aftermarket_tier = None if is_original else "OE_equivalent"
        fitments = parse_fitment(name_he, model_desc)

        compat_vehicles = [
            {
                "manufacturer": MANUFACTURER,
                "model": f["model_en"],
                "year_from": f["year_from"],
                "year_to": f["year_to"],
            }
            for f in fitments
        ]

        specs = json.dumps({
            "vat_included": False,
            "vat_rate": VAT_RATE,
            "price_with_tax_ils": price_with_tax,
            "currency": "ILS",
            "source": "delek-motors.co.il",
            "importer": "Delek Motors Israel",
            "warranty_months": WARRANTY_MONTHS,
            "shipping_to_il": True,
            "model_description": model_desc,
        })

        try:
            async with conn.transaction():
                row = await conn.fetchrow(
                    "SELECT id FROM parts_catalog WHERE oem_number=$1 AND manufacturer=$2",
                    oem, MANUFACTURER,
                )
                if row:
                    part_id = str(row["id"])
                    await conn.execute("""
                        UPDATE parts_catalog SET
                            base_price=$1, importer_price_ils=$2, min_price_ils=$2, max_price_ils=$3,
                            specifications=$4::jsonb, compatible_vehicles=$5::jsonb,
                            is_safety_critical=$6, updated_at=NOW()
                        WHERE id=$7
                    """, il_selling, il_cost, il_retail,
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
                            $8,$9,$10,$10,$11,
                            $12::jsonb,$13::jsonb,$14,$15,
                            $16,FALSE,FALSE,
                            TRUE,NOW(),NOW()
                        )
                    """, part_id, sku, oem,
                        name_en or name_he, name_he,
                        MANUFACTURER, MANUFACTURER_ID,
                        category, il_selling, il_cost, il_retail,
                        specs, json.dumps(compat_vehicles),
                        "new", aftermarket_tier, safety)
                    inserted += 1

                # Fitment rows
                for f in fitments:
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
                        "Delek Motors Israel source")
                    fitment_count += 1

                # Upsert supplier_parts
                avail = "in_stock" if price_with_tax > 0 else "out_of_stock"
                await conn.execute("""
                    INSERT INTO supplier_parts(
                        id, supplier_id, part_id, supplier_sku,
                        price_ils, price_usd, availability, is_available,
                        warranty_months, estimated_delivery_days, supplier_url,
                        created_at, updated_at
                    ) VALUES(
                        gen_random_uuid(), $1::uuid, $2::uuid, $3,
                        $4, 0.0, $5, $6, $7, $8, $9, NOW(), NOW()
                    )
                    ON CONFLICT(part_id, supplier_id) DO UPDATE SET
                        price_ils=EXCLUDED.price_ils,
                        availability=EXCLUDED.availability,
                        is_available=EXCLUDED.is_available,
                        updated_at=NOW()
                """, supplier_id, part_id, sku,
                    price_with_tax, avail, price_with_tax > 0,
                    WARRANTY_MONTHS, DELIVERY_DAYS, SUPPLIER_URL)

        except Exception as e:
            errors_count += 1
            errors.append({"oem": oem, "error": str(e)})
            if len(errors) <= 5:
                log.warning("Row error %s: %s", oem, e)

        if (i + 1) % 500 == 0:
            log.info("Progress: %d/%d | inserted=%d updated=%d fitment=%d errors=%d",
                     i + 1, len(raw_parts), inserted, updated, fitment_count, errors_count)

    await conn.close()
    elapsed = round(time.time() - start, 1)
    result = {
        "task": "import_mazda_il",
        "status": "ok" if not errors else "partial",
        "scanned": len(raw_parts),
        "inserted": inserted,
        "updated": updated,
        "fitment": fitment_count,
        "flagged": errors_count,
        "elapsed_s": elapsed,
        "errors": errors[:10],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
