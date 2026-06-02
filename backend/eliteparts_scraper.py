"""
Script: eliteparts_scraper.py
Purpose: Scrape GWM and Chery OEM parts from www.eliteparts.org (Israeli Shopify store) and import into parts_catalog

Process:
  1. Fetch all products via Shopify /products.json?limit=250&page=N endpoint (8 pages ~1,950 parts)
  2. Detect manufacturer from title/tags (GWM/HAVAL or Chery/Arrizo/Tiggo)
  3. Parse OEM number from variant SKU (first variant = primary SKU)
  4. Extract vehicle fitment from title (e.g. "HAVAL H6", "CHERY ARRIZO 5")
  5. Price is ILS (Israeli Shekel) — store directly, no conversion needed
  6. Insert into parts_catalog + supplier_parts + part_vehicle_fitment
  7. Deduplicate by OEM (skip already imported OEMs)
  8. Run scoped Meilisearch sync for GWM and Chery

Data Imported / Modified:
  - parts_catalog: sku, oem_number, name, manufacturer, manufacturer_id, category,
                   description, specifications, min_price_ils, max_price_ils,
                   online_price_ils, part_condition, aftermarket_tier, part_type,
                   is_active, needs_oem_lookup, master_enriched, compatible_vehicles
  - part_vehicle_fitment: part_id, manufacturer, manufacturer_id, model, year_from, year_to, notes
  - supplier_parts: supplier_id, part_id, supplier_sku, price_ils, availability,
                    warranty_months, estimated_delivery_days, supplier_url

Data Sources / Web Links:
  - Elite Parts: https://www.eliteparts.org
  - Products API: https://www.eliteparts.org/products.json?limit=250&page={N}

Missing Data Delegation:
  - Hebrew names present in product tags — ai_catalog_builder fills name_he for others
  - Fitment model detail expanded by REX via samelet.com/eBay
  - Missing OEM numbers queued to REX

Author: AutoSpareFinder Agent
Last Updated: 2026-06-02
"""

import asyncio
import asyncpg
import json
import logging
import os
import re
import sys
import time
import urllib.request
import uuid
from html.parser import HTMLParser

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

# ─── Config ────────────────────────────────────────────────────────────────────
DB_DSN = os.getenv(
    "DB_DSN",
    "postgresql://autospare:e4b79d75ca640dbe7f259618f078b82f21573e419308f668beed5e20b26b1d43@postgres_catalog:5432/autospare",
)
BASE_URL = "https://www.eliteparts.org"
SUPPLIER_NAME = "Elite Parts"
SUPPLIER_URL = "https://www.eliteparts.org"

# Manufacturer UUIDs (from car_brands table)
GWM_MANUFACTURER_ID = "60e78dce-367f-4041-9157-b5df7f64a41f"       # GWM
GREAT_WALL_MANUFACTURER_ID = "16e9b67b-9bb0-4c31-99dc-f5c359d38109"  # Great Wall (HAVAL parent)
CHERY_MANUFACTURER_ID = "3516329c-29a0-4bf8-aea8-4bc57979561a"       # Chery

REQUEST_DELAY = 0.5  # seconds between page fetches (Shopify API is fast)

# Keywords to detect manufacturer
GWM_KEYWORDS = ["haval", "gwm", "great wall", "h6", "h9", "h2", "h1", "h3", "h4", "h5",
                 "jolion", "wingle", "poer", "ora", "tank", "mhero"]
CHERY_KEYWORDS = ["chery", "arrizo", "tiggo", "exeed", "omoda", "fulwin"]

# GWM/HAVAL vehicle models for fitment extraction
GWM_MODELS = [
    "HAVAL H9", "HAVAL H6 Hybrid", "HAVAL H6 2nd Gen", "HAVAL H6 1st Gen", "HAVAL H6",
    "HAVAL H5", "HAVAL H4", "HAVAL H3", "HAVAL H2S", "HAVAL H2", "HAVAL H1",
    "HAVAL Jolion", "HAVAL Dargo", "HAVAL Big Dog",
    "GWM Poer", "GWM Tank 300", "GWM Tank 500", "GWM ORA",
    "Wingle 7", "Wingle 5", "Wingle 3", "Wingle",
    "Great Wall Hover", "Great Wall",
]

# Chery vehicle models for fitment extraction
CHERY_MODELS = [
    "CHERY Tiggo 8 Pro", "CHERY Tiggo 8 Plus", "CHERY Tiggo 8", "CHERY Tiggo 7 Pro",
    "CHERY Tiggo 7", "CHERY Tiggo 5X", "CHERY Tiggo 4", "CHERY Tiggo 3", "CHERY Tiggo 2",
    "CHERY Arrizo 8", "CHERY Arrizo 6 Pro", "CHERY Arrizo 6", "CHERY Arrizo 5E",
    "CHERY Arrizo 5 Plus", "CHERY Arrizo 5", "CHERY Arrizo 3",
    "CHERY Exeed TXL", "CHERY Exeed TX", "CHERY",
]

# Category guessing from keywords
CATEGORY_KEYWORDS = {
    "mirror": "Body Parts",
    "door": "Doors",
    "bumper": "Bumpers",
    "hood": "Hoods",
    "fender": "Fenders",
    "headlight": "Headlights",
    "tail light": "Tail Lights",
    "fog light": "Fog Lights",
    "light": "Lighting",
    "brake pad": "Brake Pads",
    "brake rotor": "Brake Rotors",
    "brake disc": "Brake Rotors",
    "caliper": "Calipers",
    "brake": "Brakes",
    "shock": "Shocks & Struts",
    "strut": "Shocks & Struts",
    "control arm": "Control Arms",
    "steering": "Suspension & Steering",
    "tie rod": "Tie Rods & Joints",
    "wheel bearing": "Wheel Bearings & Hubs",
    "hub": "Wheel Bearings & Hubs",
    "cv axle": "CV Axles",
    "driveshaft": "Driveshafts",
    "engine": "Engine",
    "oil filter": "Oil Filters",
    "air filter": "Air Filters",
    "fuel pump": "Fuel Delivery",
    "fuel": "Fuel & Air",
    "alternator": "Alternators & Starters",
    "starter": "Alternators & Starters",
    "battery": "Batteries & Power",
    "sensor": "Sensors",
    "oxygen sensor": "Oxygen Sensors",
    "a/c": "A/C & Heating",
    "ac compressor": "A/C Compressors",
    "condenser": "Condensers",
    "radiator": "Radiators",
    "water pump": "Water Pumps",
    "thermostat": "Thermostats",
    "cooling fan": "Cooling Fans",
    "coolant": "Coolants & Antifreeze",
    "transmission": "Transmission",
    "clutch": "Clutch Kits",
    "gearbox": "Manual Transmission",
    "transfer case": "Driveline & Axles",
    "seat": "Seats",
    "wiper": "Wiper Blades",
    "window": "Window Regulators",
    "glass": "Auto Glass",
    "key": "Audio & Electronics",
    "ecu": "Audio & Electronics",
    "module": "Audio & Electronics",
    "camera": "Cameras & GPS",
    "exhaust": "Exhaust",
    "muffler": "Mufflers",
    "catalytic": "Catalytic Converters",
    "timing belt": "Timing Belts",
    "timing chain": "Timing Chains",
    "gasket": "Gaskets & Seals",
}


def guess_category(title: str, tags: list[str]) -> str:
    """Guess category from title and tags."""
    combined = (title + " " + " ".join(tags)).lower()
    for kw, cat in CATEGORY_KEYWORDS.items():
        if kw in combined:
            return cat
    return "Engine"


def detect_manufacturer(title: str, tags: list[str]) -> tuple[str, str]:
    """Returns (manufacturer_name, manufacturer_id)."""
    combined = (title + " " + " ".join(tags)).lower()
    if any(k in combined for k in CHERY_KEYWORDS):
        return "Chery", CHERY_MANUFACTURER_ID
    if any(k in combined for k in GWM_KEYWORDS):
        return "GWM", GWM_MANUFACTURER_ID
    return "GWM", GWM_MANUFACTURER_ID  # default


def extract_fitment_models(title: str, manufacturer: str) -> list[str]:
    """Extract vehicle models from product title."""
    title_upper = title.upper()
    found = []
    models = CHERY_MODELS if manufacturer == "Chery" else GWM_MODELS
    for m in models:
        if m.upper() in title_upper:
            found.append(m)
    return found[:3]  # cap at 3 models per part


def extract_year_range(title: str, body_html: str) -> tuple[int | None, int | None]:
    """Extract year range from title or description."""
    # Pattern: "2019-2023", "2019 - 2023", "2019 to present"
    year_range = re.search(r'(20\d{2})\s*[-–to]+\s*(20\d{2}|[Pp]resent)', title + " " + body_html)
    if year_range:
        yr_from = int(year_range.group(1))
        yr_to_raw = year_range.group(2)
        yr_to = None if yr_to_raw.lower() in ("present", "Present") else int(yr_to_raw)
        return yr_from, yr_to
    # Single year
    single = re.search(r'\b(20\d{2})\b', title)
    if single:
        return int(single.group(1)), None
    return None, None


def clean_html_tags(html: str) -> str:
    """Strip HTML tags from a string."""
    return re.sub(r"<[^>]+>", " ", html).strip()


# ─── DB helpers ───────────────────────────────────────────────────────────────
async def ensure_supplier(conn) -> str:
    row = await conn.fetchrow("SELECT id FROM suppliers WHERE name=$1", SUPPLIER_NAME)
    if row:
        return str(row["id"])
    sid = str(uuid.uuid4())
    await conn.execute(
        "INSERT INTO suppliers(id,name,website,country,reliability_score,is_active,created_at,updated_at)"
        " VALUES($1,$2,$3,'IL',0.90,TRUE,NOW(),NOW())",
        sid, SUPPLIER_NAME, SUPPLIER_URL,
    )
    log.info("Created supplier: %s", SUPPLIER_NAME)
    return sid


async def get_existing_oems(conn, manufacturer: str) -> set:
    rows = await conn.fetch(
        "SELECT oem_number FROM parts_catalog WHERE manufacturer=$1 AND is_active=TRUE",
        manufacturer,
    )
    return {r["oem_number"] for r in rows}


async def insert_part(conn, supplier_id: str, product: dict, variant: dict,
                      manufacturer: str, mfr_id: str) -> str | None:
    """Insert one part variant. Returns part_id or None on failure."""
    raw_sku = variant.get("sku", "").strip()
    if not raw_sku:
        return None

    # Handle multi-OEM SKUs (e.g. "SKU1 / SKU2 + SKU3") — take first token only
    # Normalise common separators and take first OEM
    first_sku = re.split(r"[/+&,]", raw_sku)[0].strip()
    # Strip leading labels like "Steering Rack Model: "
    first_sku = re.sub(r"^[^0-9a-zA-Z]+", "", first_sku)
    # Keep only alphanumeric + hyphens
    first_sku = re.sub(r"[^0-9A-Za-z\-]", "", first_sku)
    oem_clean = first_sku.upper()[:90]  # oem_number is VARCHAR(100)
    if not oem_clean:
        return None

    sku = f"{manufacturer[:3].upper()}-{oem_clean}"[:100]  # sku is VARCHAR(100)

    title = product["title"].strip()[:255]
    price_ils_str = variant.get("price", "0") or "0"
    compare_ils_str = variant.get("compare_at_price") or price_ils_str
    price_ils = round(float(price_ils_str), 2)
    compare_ils = round(float(compare_ils_str), 2)

    # min = sale price (or compare if no sale), max = compare_at_price
    min_price = price_ils if price_ils > 0 else None
    max_price = compare_ils if compare_ils >= price_ils else price_ils

    tags = product.get("tags", [])
    category = guess_category(title, tags)
    description = clean_html_tags(product.get("body_html", ""))[:2000]

    # Check availability
    available = variant.get("available", True)
    avail_str = "in_stock" if available else "out_of_stock"

    year_from, year_to = extract_year_range(title, product.get("body_html", ""))
    fitment_models = extract_fitment_models(title, manufacturer)

    compatible_vehicles = []
    for m in fitment_models:
        model_name = m.replace("HAVAL ", "").replace("CHERY ", "").replace("GWM ", "")
        entry = {"manufacturer": manufacturer, "model": model_name}
        if year_from:
            entry["year_from"] = year_from
        if year_to:
            entry["year_to"] = year_to
        compatible_vehicles.append(entry)

    specs = {
        "vat_included": True,
        "vat_rate": 0.18,
        "currency": "ILS",
        "source": "eliteparts.org",
        "original_price_ils": price_ils,
    }

    # Part type: all eliteparts products are "original" genuine OEM or OE equivalent
    part_type = "original"
    aftermarket_tier = None  # genuine OEM

    try:
        async with conn.transaction():
            part_id = await conn.fetchval(
                """
                INSERT INTO parts_catalog(
                    id, sku, oem_number, name, manufacturer, manufacturer_id,
                    category, description, specifications, compatible_vehicles,
                    importer_price_ils, online_price_ils, min_price_ils, max_price_ils,
                    part_condition, aftermarket_tier, part_type,
                    is_safety_critical, needs_oem_lookup, master_enriched,
                    is_active, created_at, updated_at
                ) VALUES(
                    gen_random_uuid(), $1, $2, $3, $4, $5::uuid,
                    $6, $7, $8::jsonb, $9::jsonb,
                    $10, $10, $10, $11,
                    'New', NULL, 'original',
                    FALSE, FALSE, FALSE,
                    TRUE, NOW(), NOW()
                )
                ON CONFLICT (sku) DO UPDATE SET
                    online_price_ils = EXCLUDED.online_price_ils,
                    min_price_ils = EXCLUDED.min_price_ils,
                    max_price_ils = EXCLUDED.max_price_ils,
                    importer_price_ils = EXCLUDED.importer_price_ils,
                    updated_at = NOW()
                RETURNING id
                """,
                sku, oem_clean, title, manufacturer, mfr_id,
                category, description,
                json.dumps(specs),
                json.dumps(compatible_vehicles),
                min_price,
                max_price if max_price else min_price,
            )

            if part_id is None:
                part_id = await conn.fetchval(
                    "SELECT id FROM parts_catalog WHERE sku=$1", sku
                )

            if part_id:
                product_url = f"{BASE_URL}/products/{product['handle']}"
                await conn.execute(
                    """
                    INSERT INTO supplier_parts(
                        id, supplier_id, part_id, supplier_sku,
                        price_ils, price_usd, availability, is_available,
                        warranty_months, estimated_delivery_days, supplier_url,
                        created_at, updated_at
                    ) VALUES(gen_random_uuid(),$1::uuid,$2::uuid,$3,
                              $4,0.0,$5,$6,
                              12,30,$7,NOW(),NOW())
                    ON CONFLICT(part_id,supplier_id) DO UPDATE SET
                        price_ils=EXCLUDED.price_ils,
                        is_available=EXCLUDED.is_available,
                        updated_at=NOW()
                    """,
                    supplier_id, str(part_id), raw_sku[:100],  # truncate supplier_sku to VARCHAR(100)
                    price_ils if price_ils > 0 else 0.0,
                    avail_str, available,
                    product_url,
                )

            return str(part_id) if part_id else None
    except Exception as e:
        log.warning("Insert failed for %s: %s", sku, e)
        return None


async def insert_fitment(conn, part_id: str, manufacturer: str, mfr_id: str,
                         model_name: str, year_from: int | None, year_to: int | None):
    """Insert a fitment row."""
    if not year_from:
        return
    try:
        await conn.execute(
            """
            INSERT INTO part_vehicle_fitment(
                id, part_id, manufacturer, manufacturer_id, model,
                year_from, year_to, engine_type, notes, created_at, updated_at
            ) VALUES(gen_random_uuid(),$1::uuid,$2,$3::uuid,$4,$5,$6,NULL,'eliteparts.org',NOW(),NOW())
            ON CONFLICT(part_id, manufacturer, model, year_from) DO NOTHING
            """,
            part_id, manufacturer, mfr_id, model_name, year_from, year_to,
        )
    except Exception as e:
        log.debug("Fitment skip %s %s: %s", part_id, model_name, e)


# ─── Fetch all products ────────────────────────────────────────────────────────
def fetch_all_products() -> list[dict]:
    """Fetch all products from Shopify products.json endpoint."""
    all_products = []
    page = 1
    headers = {"User-Agent": "Mozilla/5.0"}

    while True:
        url = f"{BASE_URL}/products.json?limit=250&page={page}"
        req = urllib.request.Request(url, headers=headers)
        try:
            r = urllib.request.urlopen(req, timeout=20)
            data = json.loads(r.read())
            products = data.get("products", [])
            if not products:
                break
            all_products.extend(products)
            log.info("  Page %d: %d products (total: %d)", page, len(products), len(all_products))
            if len(products) < 250:
                break
            page += 1
            time.sleep(REQUEST_DELAY)
        except Exception as e:
            log.error("Failed to fetch page %d: %s", page, e)
            break

    return all_products


# ─── Main ─────────────────────────────────────────────────────────────────────
async def main():
    import time as _time
    t0 = _time.time()
    log.info("Elite Parts scraper starting (GWM + Chery from eliteparts.org)")

    log.info("Fetching all products from Shopify API...")
    all_products = fetch_all_products()
    log.info("Total products fetched: %d", len(all_products))

    conn = await asyncpg.connect(DB_DSN)
    supplier_id = await ensure_supplier(conn)

    # Load existing OEMs to avoid duplicates (also check 'Haval' — prior import used that name)
    existing_gwm = await get_existing_oems(conn, "GWM")
    existing_chery = await get_existing_oems(conn, "Chery")
    existing_haval = await get_existing_oems(conn, "Haval")
    existing_all = existing_gwm | existing_chery | existing_haval
    log.info("Existing OEMs: GWM=%d, Chery=%d, Haval(legacy)=%d",
             len(existing_gwm), len(existing_chery), len(existing_haval))

    stats = {
        "scanned": 0, "updated": 0, "fitment": 0, "skipped": 0,
        "flagged": 0, "errors": [],
    }

    for product in all_products:
        title = product.get("title", "")
        tags = product.get("tags", [])
        manufacturer, mfr_id = detect_manufacturer(title, tags)
        stats["scanned"] += 1

        # Process each variant (each is a distinct SKU/OEM)
        for variant in product.get("variants", []):
            raw_sku = (variant.get("sku") or "").strip()
            if not raw_sku:
                stats["flagged"] += 1
                continue

            # same normalization as insert_part
            first_sku = re.split(r"[/+&,]", raw_sku)[0].strip()
            first_sku = re.sub(r"^[^0-9a-zA-Z]+", "", first_sku)
            first_sku = re.sub(r"[^0-9A-Za-z\-]", "", first_sku)
            oem_clean = first_sku.upper()
            if not oem_clean or oem_clean in existing_all:
                stats["skipped"] += 1
                continue

            part_id = await insert_part(
                conn, supplier_id, product, variant, manufacturer, mfr_id
            )
            if not part_id:
                stats["flagged"] += 1
                continue

            existing_all.add(oem_clean)
            stats["updated"] += 1

            # Insert fitment for detected models
            fitment_models = extract_fitment_models(title, manufacturer)
            year_from, year_to = extract_year_range(title, product.get("body_html", ""))
            for m in fitment_models:
                model_name = m.replace("HAVAL ", "").replace("CHERY ", "").replace("GWM ", "")
                if year_from:
                    await insert_fitment(conn, part_id, manufacturer, mfr_id,
                                         model_name, year_from, year_to)
                    stats["fitment"] += 1

            if stats["updated"] % 100 == 0:
                log.info("Progress: %d inserted, %d skipped", stats["updated"], stats["skipped"])

    await conn.close()

    elapsed = round(_time.time() - t0, 1)
    result = {
        "task": "eliteparts_scraper",
        "status": "ok" if not stats["errors"] else "partial",
        "scanned": stats["scanned"],
        "updated": stats["updated"],
        "fitment": stats["fitment"],
        "skipped": stats["skipped"],
        "flagged": stats["flagged"],
        "elapsed_s": elapsed,
        "errors": stats["errors"][:10],
    }
    print(json.dumps(result, indent=2))

    # Post-import: run Meilisearch sync for both manufacturers
    log.info("Running Meilisearch sync for GWM and Chery...")
    import subprocess
    for brand in ["GWM", "Chery"]:
        subprocess.run(
            ["python3", "/app/meili_sync.py", "--manufacturer", brand, "--no-rebuild"],
            check=False,
        )

    return result


if __name__ == "__main__":
    asyncio.run(main())
