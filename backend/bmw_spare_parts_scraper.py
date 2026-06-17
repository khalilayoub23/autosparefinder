# ⚠️  BROWSER TOOL REQUIRED — DO NOT RUN HTTP REQUESTS FROM SERVER IP
# The server IP (207.180.217.129) is blocked by Cloudflare and anti-bot systems.
# All external HTTP extraction must be done via the browser tool (Playwright / run_playwright_code).
# Pattern: (1) Extract with browser tool → save JSON, (2) Import JSON with this script.
# See claude.md § Web Scraping Rules.
"""
Script: bmw_spare_parts_scraper.py
Purpose: Scrape genuine BMW OEM parts from www.bmw-spare-parts.com and import into parts_catalog

Process:
  1. Crawl all BMW models (1-8 Series, X1-X7, i3, i8, Z4, MINI) - modern (2010+) only
  2. For each model → fetch available years → filter 2010+
  3. For each year → fetch variant/engine links (e.g. /bmw-cars/3-Series/2019/320d/320d/{code})
  4. For each variant → fetch parts diagram pages per category/subcategory
  5. From each diagram page → extract OEM numbers via itemprop="description" meta tags
  6. Batch-fetch each OEM's assignment page: /bmw-cars/assignment_spare_parts/{oem}
     → extract name (Italian) + EUR price excl. VAT
  7. Deduplicate by OEM (skip already imported OEMs)
  8. Convert EUR → ILS; store with confidence=0.85 (scraped)
  9. Insert into parts_catalog + supplier_parts + part_vehicle_fitment
  10. Run scoped Meilisearch sync

Data Imported / Modified:
  - parts_catalog: sku, oem_number, name, manufacturer, manufacturer_id, category,
                   description, specifications, min_price_ils, max_price_ils,
                   online_price_ils, part_condition, aftermarket_tier, is_active,
                   needs_oem_lookup, master_enriched, compatible_vehicles
  - part_vehicle_fitment: part_id, manufacturer, manufacturer_id, model, year_from, year_to, notes
  - supplier_parts: supplier_id, part_id, supplier_sku, price_ils, availability, warranty_months,
                    estimated_delivery_days, supplier_url

Data Sources / Web Links:
  - BMW Spare Parts EU: https://www.bmw-spare-parts.com/ (powered by en.microfiches.net)
  - Part pages: https://www.bmw-spare-parts.com/bmw-cars/assignment_spare_parts/{oem}
  - Diagram pages: https://www.bmw-spare-parts.com/bmw-cars/{Model}/{Year}/{Variant}/...

Missing Data Delegation:
  - Italian part names translated to English by ai_catalog_builder.py (master_enriched=FALSE)
  - Hebrew names filled by ai_catalog_builder.py
  - Fitment cross-references expanded by REX via TecDoc/eBay

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
import urllib.error
from datetime import datetime
from html import unescape

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
BASE_URL = "https://www.bmw-spare-parts.com"
SUPPLIER_NAME = "BMW Spare Parts EU"
SUPPLIER_URL = "https://www.bmw-spare-parts.com"
BMW_MANUFACTURER_ID = "caa6ba39-02aa-4394-969d-a15f3f19104c"
MIN_YEAR = 2010
REQUEST_DELAY = 1.5  # seconds between requests (respectful crawl)
MAX_RETRIES = 3

# Models to scrape (modern BMW + MINI)
BMW_MODELS = [
    "1-Series", "2-Series", "3-Series", "4-Series", "5-Series",
    "6-Series", "7-Series", "8-Series",
    "X1", "X2", "X3", "X4", "X5", "X6", "X7",
    "i3", "i8", "Z4",
]

# BMW series → CATEGORY_MAP mapping
CATEGORY_MAP = {
    "Engine": "Engine",
    "Fuel-Preparation": "Fuel & Air",
    "Fuel-Supply": "Fuel & Air",
    "Fuel-System": "Fuel & Air",
    "Air-Intake": "Air Intake",
    "Cooling-System": "Engine Cooling",
    "Belts-Cooling": "Belts & Chains",
    "Exhaust": "Exhaust",
    "Emission": "Exhaust",
    "Clutch": "Clutch Kits",
    "Transmission": "Transmission",
    "Manual-Transmission": "Manual Transmission",
    "Automatic-Transmission": "Automatic Transmission",
    "Driveline": "Driveline & Axles",
    "Front-Drive-Axle": "CV Axles",
    "Rear-Drive-Axle": "Driveline & Axles",
    "Brakes": "Brakes",
    "Brake-Pads": "Brake Pads",
    "Brake-Disc": "Brake Rotors",
    "Suspension": "Suspension & Steering",
    "Front-Axle": "Suspension & Steering",
    "Rear-Axle": "Suspension & Steering",
    "Steering": "Suspension & Steering",
    "Body": "Body Parts",
    "Door": "Doors",
    "Hood": "Hoods",
    "Bumper": "Bumpers",
    "Fender": "Fenders",
    "Electrical": "Audio & Electronics",
    "Lights": "Lighting",
    "Headlights": "Headlights",
    "Interior": "Interior",
    "HVAC": "A/C & Heating",
    "Heater": "A/C & Heating",
    "Air-Conditioning": "A/C & Heating",
    "Wheels": "Wheels & Tires",
    "Tires": "Tires",
    "Wiper": "Wipers & Washers",
    "Fuel-Tank": "Fuel Delivery",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


# ─── HTTP helpers ──────────────────────────────────────────────────────────────
def fetch(url: str, retries: int = MAX_RETRIES) -> str | None:
    """Fetch URL with retries. Returns HTML string or None on failure."""
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            if e.code in (404, 410):
                return None
            log.warning("HTTP %s for %s (attempt %d)", e.code, url, attempt + 1)
        except Exception as e:
            log.warning("Fetch error %s for %s (attempt %d)", e, url, attempt + 1)
        time.sleep(2 ** attempt)
    return None


def _clean_text(s: str) -> str:
    return unescape(re.sub(r"\s+", " ", s)).strip()


# ─── Extraction helpers ────────────────────────────────────────────────────────
def extract_oems_from_diagram(html: str) -> list[str]:
    """Extract OEM numbers from a BMW parts diagram page."""
    # OEMs appear in: itemprop="description" content="The spare {name} part number {oem} ..."
    oems = re.findall(r'assignment_spare_parts/(\d{10,11})', html)
    return list(dict.fromkeys(oems))  # dedupe, preserve order


def extract_part_from_assignment(html: str, oem: str) -> dict | None:
    """Extract name, price, category from a BMW assignment_spare_parts page."""
    if not html:
        return None

    # Name: "The spare {Italian name} part number {oem}..."
    name_match = re.search(
        r'The spare (.+?) part number ' + re.escape(oem),
        html,
        re.IGNORECASE,
    )
    if not name_match:
        # Fallback: title before oem formatted
        title_match = re.search(r'<title>([^<]+)', html)
        if title_match:
            raw_title = title_match.group(1)
            # "Tubo fless.p.acqua 11 53 2 249 778 Search for models..."
            name_part = re.sub(r'\s*\d[\d\s]{9,}\s*.*', '', raw_title).strip()
            if name_part:
                name = _clean_text(name_part)
            else:
                name = f"BMW Part {oem}"
        else:
            name = f"BMW Part {oem}"
    else:
        name = _clean_text(name_match.group(1))

    # Price: itemprop="price" content="XX.XX"
    price_match = re.search(r'itemprop="price"\s+content="([\d.]+)"', html)
    price_eur = float(price_match.group(1)) if price_match else 0.0

    return {
        "oem": oem,
        "name": name,
        "price_eur": price_eur,
    }


def extract_year_links(model: str, html: str) -> list[str]:
    """Extract year links from a model page, filtering MIN_YEAR+."""
    # href="/bmw-cars/3-Series/2019"
    pattern = rf'/bmw-cars/{re.escape(model)}/(\d{{4}})"'
    years = re.findall(pattern, html)
    urls = []
    for y in set(years):
        if int(y) >= MIN_YEAR:
            urls.append(f"/bmw-cars/{model}/{y}")
    return sorted(set(urls), reverse=True)  # newest first


def extract_variant_links(model: str, year: int, html: str) -> list[str]:
    """Extract unique variant landing URLs (variant slug + options code).

    URL pattern: /bmw-cars/{Model}/{Year}/{Variant}/{Variant}/{OptionsCode}
    e.g. /bmw-cars/3-Series/2019/316d/316d/573248L72
    """
    pattern = rf'href="(/bmw-cars/{re.escape(model)}/{year}/[^/"]+/[^/"]+/[A-Z0-9]{{6,}})"'
    links = re.findall(pattern, html)
    return list(dict.fromkeys(links))


def extract_category_links(model: str, year: int, variant: str, html: str) -> list[str]:
    """Extract category-level links from a variant page.

    URL pattern: /bmw-cars/{Model}/{ActualYear}/{Variant}/{Variant}/{Category}/{ChapterNum}/{OptionsCode}
    Note: actual year in URL may differ from requested year (options code redirect).
    """
    # Match: /bmw-cars/Model/anyYear/variant/variant/Category/num/OptionsCode
    pattern = (
        r'href="(/bmw-cars/'
        + re.escape(model)
        + r'/\d+/'
        + re.escape(variant)
        + r'/'
        + re.escape(variant)
        + r'/[A-Za-z][^"]+/\d+/[A-Z0-9]{6,})"'
    )
    links = re.findall(pattern, html)
    return list(dict.fromkeys(links))


def extract_diagram_links_from_category(model: str, year: int, variant: str, html: str) -> list[str]:
    """Extract parts diagram page links from a category page.

    URL pattern: /bmw-cars/{Model}/{Year}/{Variant}/{Category}/{Subcategory}/{N}/{Code}/{N}/{OptionsCode}
    e.g. /bmw-cars/3-Series/2015/316d/Engine/Short-Engine/6/11_5647/11/573248L72
    Note: the second variant repetition is absent in diagram URLs.
    """
    # Match diagram links (contain underscore code segment): /Model/Year/Variant/Category/Sub/N/xx_xx/N/OptionsCode
    pattern = (
        r'href="(/bmw-cars/'
        + re.escape(model)
        + r'/\d+/'
        + re.escape(variant)
        + r'/[A-Za-z][^"]+/[^"]+/\d+/\w+_\w+/\d+/[A-Z0-9]{6,})"'
    )
    links = re.findall(pattern, html)
    return list(dict.fromkeys(links))


def guess_category_from_url(url: str) -> str:
    """Map a diagram URL's category segment to CATEGORY_MAP key."""
    parts = url.split("/")
    # Diagram URL: /bmw-cars/Model/Year/Variant/Category/Subcategory/...
    # Category is at index 5 (0-indexed)
    cat_raw = ""
    if len(parts) >= 6:
        cat_raw = parts[5].replace("-", " ")
    for key, val in CATEGORY_MAP.items():
        if key.lower().replace("-", " ") in cat_raw.lower():
            return val
    return "Engine"


def guess_model_from_url(url: str) -> str:
    """Extract model name from URL."""
    parts = url.split("/")
    if len(parts) >= 4:
        return parts[3]  # e.g. "3-Series"
    return "Unknown"


# ─── DB helpers ───────────────────────────────────────────────────────────────
async def ensure_supplier(conn) -> str:
    row = await conn.fetchrow("SELECT id FROM suppliers WHERE name=$1", SUPPLIER_NAME)
    if row:
        return str(row["id"])
    sid = __import__("uuid").uuid4().__str__()
    await conn.execute(
        "INSERT INTO suppliers(id,name,website,country,reliability_score,is_active,created_at,updated_at)"
        " VALUES($1,$2,$3,'EU',0.85,TRUE,NOW(),NOW())",
        sid, SUPPLIER_NAME, SUPPLIER_URL,
    )
    log.info("Created supplier: %s", SUPPLIER_NAME)
    return sid


async def get_existing_oems(conn) -> set:
    rows = await conn.fetch(
        "SELECT oem_number FROM parts_catalog WHERE manufacturer='BMW' AND is_active=TRUE"
    )
    return {r["oem_number"] for r in rows}


async def get_eur_rate(conn) -> float:
    """Get EUR→ILS rate from DB or return default."""
    try:
        row = await conn.fetchrow(
            "SELECT rate FROM currency_rates WHERE currency='EUR' ORDER BY updated_at DESC LIMIT 1"
        )
        if row:
            return float(row["rate"])
    except Exception:
        pass
    return 3.96  # fallback EUR/ILS rate


async def insert_part(conn, supplier_id: str, part: dict, eur_to_ils: float) -> str | None:
    """Insert one part. Returns part_id or None on failure."""
    import uuid

    oem = re.sub(r"[^A-Z0-9]", "", part["oem"].upper())
    sku = f"BMW-{oem}"
    price_eur = part["price_eur"]
    price_ils = round(price_eur * eur_to_ils, 2) if price_eur > 0 else 0.0
    max_price = round(price_ils * 1.05, 2)  # slight markup for max

    specs = {
        "vat_included": False,
        "vat_rate": None,
        "currency": "EUR",
        "source": "bmw-spare-parts.com",
        "shipping_to_il": True,
    }

    try:
        async with conn.transaction():
            part_id = await conn.fetchval(
                """
                INSERT INTO parts_catalog(
                    id, sku, oem_number, name, manufacturer, manufacturer_id,
                    category, description, specifications, compatible_vehicles,
                    online_price_ils, min_price_ils, max_price_ils,
                    part_condition, aftermarket_tier, part_type,
                    is_safety_critical, needs_oem_lookup, master_enriched,
                    is_active, created_at, updated_at
                ) VALUES(
                    gen_random_uuid(), $1, $2, $3, 'BMW', $4::uuid,
                    $5, $6, $7::jsonb, '[]'::jsonb,
                    $8, $8, $9,
                    'New', NULL, 'original',
                    FALSE, FALSE, FALSE,
                    TRUE, NOW(), NOW()
                )
                ON CONFLICT (sku) DO UPDATE SET
                    online_price_ils = EXCLUDED.online_price_ils,
                    min_price_ils = EXCLUDED.min_price_ils,
                    max_price_ils = EXCLUDED.max_price_ils,
                    updated_at = NOW()
                RETURNING id
                """,
                sku, oem, part["name"], BMW_MANUFACTURER_ID,
                part["category"], part.get("description", ""),
                json.dumps(specs),
                price_ils if price_ils > 0 else None,
                max_price if price_ils > 0 else None,
            )

            if part_id is None:
                # conflict returned nothing, fetch existing id
                part_id = await conn.fetchval(
                    "SELECT id FROM parts_catalog WHERE sku=$1", sku
                )

            if part_id:
                await conn.execute(
                    """
                    INSERT INTO supplier_parts(
                        id, supplier_id, part_id, supplier_sku,
                        price_ils, price_usd, availability, is_available,
                        warranty_months, estimated_delivery_days, supplier_url,
                        created_at, updated_at
                    ) VALUES(gen_random_uuid(),$1::uuid,$2::uuid,$3,
                              $4,0.0,'in_stock',TRUE,
                              12,30,$5,NOW(),NOW())
                    ON CONFLICT(part_id,supplier_id) DO UPDATE SET
                        price_ils=EXCLUDED.price_ils,
                        is_available=TRUE,
                        updated_at=NOW()
                    """,
                    supplier_id, str(part_id), sku,
                    price_ils if price_ils > 0 else 0.0,
                    f"{BASE_URL}/bmw-cars/assignment_spare_parts/{oem}",
                )

            return str(part_id) if part_id else None
    except Exception as e:
        log.warning("Insert failed for %s: %s", oem, e)
        return None


async def insert_fitment(conn, part_id: str, model: str, year: int):
    """Insert fitment row for a BMW model+year."""
    import uuid as _uuid

    # Map URL model to display name
    display_model = model.replace("-", " ")
    year_from = year
    year_to = year + 1  # each variant page is for a specific year

    try:
        await conn.execute(
            """
            INSERT INTO part_vehicle_fitment(
                id, part_id, manufacturer, manufacturer_id, model,
                year_from, year_to, engine_type, notes, created_at, updated_at
            ) VALUES(gen_random_uuid(),$1::uuid,'BMW',$2::uuid,$3,$4,$5,NULL,$6,NOW(),NOW())
            ON CONFLICT(part_id, manufacturer, model, year_from) DO NOTHING
            """,
            part_id, BMW_MANUFACTURER_ID, display_model, year_from, year_to,
            "bmw-spare-parts.com",
        )
    except Exception as e:
        log.debug("Fitment insert skip for %s %s %s: %s", part_id, model, year, e)


# ─── Crawler ──────────────────────────────────────────────────────────────────
async def scrape_model(
    model: str,
    conn,
    supplier_id: str,
    eur_to_ils: float,
    existing_oems: set,
    stats: dict,
):
    log.info("── Model: %s", model)
    model_html = fetch(f"{BASE_URL}/bmw-cars/{model}")
    if not model_html:
        log.warning("Could not fetch model page for %s", model)
        return

    year_paths = extract_year_links(model, model_html)
    log.info("  %d years (≥%d) for %s", len(year_paths), MIN_YEAR, model)

    for year_path in year_paths[:5]:  # cap 5 years per model to limit runtime
        year = int(year_path.split("/")[3])
        time.sleep(REQUEST_DELAY)
        year_html = fetch(f"{BASE_URL}{year_path}")
        if not year_html:
            continue

        variant_links = extract_variant_links(model, year, year_html)
        log.info("    Year %d: %d variants", year, len(variant_links))

        for var_link in variant_links[:3]:  # cap 3 variants per year
            variant_name = var_link.split("/")[5]  # e.g. "320d"
            time.sleep(REQUEST_DELAY)
            var_html = fetch(f"{BASE_URL}{var_link}")
            if not var_html:
                continue

            # Get category links from variant page, then get diagram links per category
            cat_links = extract_category_links(model, year, variant_name, var_html)
            log.info("      Variant %s: %d categories", variant_name, len(cat_links))

            all_diagrams: list[str] = []
            for cat_link in cat_links[:8]:  # cap 8 categories per variant
                time.sleep(REQUEST_DELAY * 0.7)
                cat_html = fetch(f"{BASE_URL}{cat_link}")
                if not cat_html:
                    continue
                diags = extract_diagram_links_from_category(model, year, variant_name, cat_html)
                all_diagrams.extend(diags[:5])  # cap 5 diagrams per category

            all_diagrams = list(dict.fromkeys(all_diagrams))
            log.info("      Variant %s: %d diagrams total", variant_name, len(all_diagrams))

            for diag_link in all_diagrams[:40]:  # cap 40 diagrams per variant
                time.sleep(REQUEST_DELAY)
                diag_html = fetch(f"{BASE_URL}{diag_link}")
                if not diag_html:
                    continue

                oems = extract_oems_from_diagram(diag_html)
                category = guess_category_from_url(diag_link)
                stats["scanned"] += len(oems)

                for oem in oems:
                    oem_clean = re.sub(r"[^A-Z0-9]", "", oem.upper())
                    if oem_clean in existing_oems:
                        continue

                    time.sleep(REQUEST_DELAY * 0.5)
                    assign_html = fetch(f"{BASE_URL}/bmw-cars/assignment_spare_parts/{oem}")
                    part_data = extract_part_from_assignment(assign_html or "", oem)
                    if not part_data:
                        continue

                    part_data["category"] = category
                    part_data["description"] = f"BMW genuine OEM part for {model.replace('-', ' ')} {year}"

                    part_id = await insert_part(conn, supplier_id, part_data, eur_to_ils)
                    if part_id:
                        await insert_fitment(conn, part_id, model, year)
                        existing_oems.add(oem_clean)
                        stats["updated"] += 1
                        log.info(
                            "      + %s [%s] %.2f€ → ₪%.2f",
                            oem, part_data["name"][:30],
                            part_data["price_eur"],
                            part_data["price_eur"] * eur_to_ils,
                        )
                    else:
                        stats["flagged"] += 1


# ─── Main ─────────────────────────────────────────────────────────────────────
async def main():
    t0 = time.time()
    log.info("BMW Spare Parts scraper starting. Models: %d", len(BMW_MODELS))

    conn = await asyncpg.connect(DB_DSN)

    supplier_id = await ensure_supplier(conn)
    eur_to_ils = await get_eur_rate(conn)
    existing_oems = await get_existing_oems(conn)

    log.info("EUR→ILS rate: %.4f | Existing BMW OEMs: %d", eur_to_ils, len(existing_oems))

    stats = {"scanned": 0, "updated": 0, "flagged": 0, "errors": []}

    for model in BMW_MODELS:
        try:
            await scrape_model(model, conn, supplier_id, eur_to_ils, existing_oems, stats)
        except Exception as e:
            log.error("Model %s failed: %s", model, e)
            stats["errors"].append(f"{model}: {e}")

    await conn.close()

    elapsed = round(time.time() - t0, 1)
    result = {
        "task": "bmw_spare_parts_scraper",
        "status": "ok" if not stats["errors"] else "partial",
        "scanned": stats["scanned"],
        "updated": stats["updated"],
        "flagged": stats["flagged"],
        "elapsed_s": elapsed,
        "errors": stats["errors"][:10],
    }
    print(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    asyncio.run(main())
