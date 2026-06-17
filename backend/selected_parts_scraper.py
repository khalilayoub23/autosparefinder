#!/usr/bin/env python3
"""
Scraper for selected.parts (Israeli Land Rover & Jaguar parts retailer).
Extracts product OEM numbers, names and ILS prices.
Imports into parts_catalog for Land Rover and Jaguar brands.

Run inside container: python3 /app/selected_parts_scraper.py
"""
from __future__ import annotations
import asyncio, logging, re, time
import asyncpg
import requests
from urllib.parse import urljoin

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

BASE_URL = "https://www.selected.parts"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
    "Accept-Language": "he-IL,he;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

DB_DSN = (
    "postgresql://autospare:e4b79d75ca640dbe7f259618f078b82f21573e419308f668beed5e20b26b1d43"
    "@postgres_catalog:5432/autospare"
)

LAND_ROVER_BRAND_ID = "7f060acf-2382-42e1-8413-f9b045cb0836"
JAGUAR_BRAND_ID     = "fde0f2dc-c6fb-4ab6-b699-765044fbc073"

# Main listing pages — paginate through all items
MAIN_PAGES = {
    "Land Rover": "/179509-LAND-ROVER-PARTS",
    "Jaguar":     "/179510-JAGUAR-PARTS",
}

ITEM_RE    = re.compile(r"href='\s*(/items/\d+[^'\s]+)")
CODE_RE    = re.compile(r'class="code_item[^"]*"[^>]*>\s*([A-Za-z0-9\-\.\/]+)\s*<')
ORIG_PRICE_RE = re.compile(r'origin_price_number[^>]*>([\d,]+)\s*₪')
SALE_PRICE_RE = re.compile(r'(?:final_price_number|item_price_number|"price":\s*)([\d,]+)(?:\s*₪|")')
TITLE_RE   = re.compile(r'<title>([^|<]+?)(?:\s*-[^<]+)?</title>')
HEBREW_RE  = re.compile(r'[א-ת]')

TERM_MAP = [
    ('צלחת בלם', 'Brake Disc'), ('צלחות בלם', 'Brake Discs'),
    ('רפידות בלם', 'Brake Pads'), ('רפידת בלם', 'Brake Pad'),
    ('כבל התראת בלמים', 'Brake Warning Cable'),
    ('בלם חניה', 'Parking Brake'), ('מגבר בלם', 'Brake Booster'),
    ('משאבת בלם', 'Brake Pump'), ('גריל קדמי', 'Front Grille'),
    ('תא נוסעים', 'Cabin/Interior'), ('מערכת דלק', 'Fuel System'),
    ('חיישן', 'Sensor'), ('טורבו', 'Turbo'), ('משאבת וואקום', 'Vacuum Pump'),
    ('רצועות ומותחנים', 'Belts and Tensioners'), ('רצועה', 'Belt'),
    ('קפיצים ובולמים', 'Springs and Shocks'), ('קפיץ', 'Spring'),
    ('בולם זעזועים', 'Shock Absorber'), ('מתגים', 'Switches'), ('מתג', 'Switch'),
    ('נורות', 'Bulbs'), ('נורה', 'Bulb'), ('פנס ערפל', 'Fog Light'),
    ('פנס אחורי', 'Rear Light'), ('פנס קדמי', 'Front Light'), ('פנס', 'Light'),
    ('שלט רחוק', 'Remote Control'), ('מסנן שמן מנוע', 'Engine Oil Filter'),
    ('מסנן אוויר', 'Air Filter'), ('מסנן דלק', 'Fuel Filter'),
    ('מסנן מיזוג', 'AC Filter'), ('מסנן תיבת הילוכים', 'Gearbox Filter'),
    ('מסנן', 'Filter'), ('מגב קדמי', 'Front Wiper'), ('מגב אחורי', 'Rear Wiper'),
    ('מגב', 'Wiper'), ('זרוע מתלה', 'Suspension Arm'), ('מנעולי דלתות', 'Door Locks'),
    ('מראות', 'Mirrors'), ('מראה', 'Mirror'), ('סמלים וקישוטים', 'Emblems'),
    ('תושבות', 'Brackets'), ('תושבת', 'Bracket'), ('הגה', 'Steering Wheel'),
    ('מכסה מילוי דלק', 'Fuel Cap'), ('כננת לגלגל רזרבי', 'Spare Wheel Winch'),
    ('מתלים', 'Suspension'), ('ידיות', 'Handles'), ('ידית', 'Handle'),
    ('אביזרים', 'Accessories'), ('מערכת קירור', 'Cooling System'),
    ('מנגנון חלון', 'Window Mechanism'), ('מצמד', 'Clutch'),
    ('קדמי', 'Front'), ('אחורי', 'Rear'), ('ימין', 'Right'), ('שמאל', 'Left'),
    ('עם', 'with'), ('ל', 'for'), ('קטגוריות', 'categories'),
]

CAT_RULES = [
    (['בלם', 'רפידות', 'צלחת', 'כבל התראת'], 'brakes'),
    (['קפיץ', 'בולם', 'מתלה', 'מתלים'], 'suspension-steering'),
    (['הגה'], 'suspension-steering'),
    (['פנס', 'נורה', 'תאורה', 'ערפל'], 'lighting'),
    (['קירור', 'רדיאטור'], 'cooling'),
    (['מסנן שמן', 'פילטר'], 'engine'),
    (['חיישן', 'מתג', 'שלט רחוק'], 'electrical-sensors'),
    (['פגוש', 'גריל', 'מכסה', 'מראה', 'דלת', 'כנף'], 'body-exterior'),
    (['מגב', 'שמשה'], 'body-exterior'),
    (['מושב', 'ריפוד', 'תא נוסעים'], 'interior'),
    (['מצמד', 'תיבת הילוכים', 'גיר'], 'gearbox'),
    (['דלק', 'הזרקה'], 'fuel-air'),
    (['רצועה', 'שרשרת', 'מותחן'], 'belts-chains'),
    (['טורבו', 'וואקום'], 'engine'),
    (['חלון', 'מנגנון חלון'], 'body-exterior'),
    (['ידית', 'מנעול'], 'body-exterior'),
    (['אביזר', 'כננת', 'גלגל רזרבי'], 'accessories'),
    (['TERRAFIRMA'], 'accessories'),
    (['ARNOTT', 'מתלה אוויר', 'suspension air'], 'suspension-steering'),
    (['MEYLE'], 'accessories'),
]


def categorize(text: str) -> str:
    for keywords, cat in CAT_RULES:
        for kw in keywords:
            if kw.lower() in text.lower():
                return cat
    return 'accessories'


def translate_name(heb: str) -> str:
    s = heb.strip()
    for h, e in TERM_MAP:
        s = s.replace(h, e)
    s = re.sub(r'\s+', ' ', s).strip()
    if HEBREW_RE.search(s):
        return f"LR/JAG Part - {heb.strip()}"[:255]
    return s[:255]


def parse_price(raw: str) -> float | None:
    try:
        return float(raw.replace(',', ''))
    except Exception:
        return None


def get_html(url: str, retries: int = 3) -> str | None:
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            if r.status_code == 200:
                return r.text
            if r.status_code == 404:
                return None
        except Exception as e:
            if attempt == retries - 1:
                log.warning("Failed %s: %s", url, e)
        time.sleep(1 + attempt)
    return None


def scrape_listing_page(path: str) -> list[str]:
    """Return all item URLs found on a listing page."""
    html = get_html(BASE_URL + path)
    if not html:
        return []
    return list(dict.fromkeys(ITEM_RE.findall(html)))  # deduplicate, preserve order


def scrape_product(item_path: str, brand: str) -> dict | None:
    html = get_html(BASE_URL + item_path)
    if not html:
        return None

    # OEM / catalog number
    oem_m = CODE_RE.search(html)
    oem = oem_m.group(1).strip() if oem_m else None

    # Prices
    prices_raw = re.findall(r'([\d,]+)\s*₪', html)
    prices = [p for p in (parse_price(x) for x in prices_raw) if p and p > 0]
    if not prices:
        return None
    # Use smallest non-zero price as sale price (most favourable to buyer)
    price = min(prices)

    # Title
    title_m = TITLE_RE.search(html)
    name_raw = title_m.group(1).strip() if title_m else item_path
    # Strip trailing category breadcrumb (e.g. " - צלחות בלם LR")
    name_heb = re.sub(r'\s*-\s*\S+\s*LR\s*$', '', name_raw).strip()
    name_heb = re.sub(r'\s*-\s*\S+\s*JAG\s*$', '', name_heb).strip()

    if not oem:
        # Try to find any part-number-like string in the title
        pn_m = re.search(r'[A-Z]{2,}\d{3,}\w*', name_raw)
        oem = pn_m.group(0) if pn_m else None

    if not oem:
        # Use item ID as fallback
        item_id_m = re.search(r'/items/(\d+)', item_path)
        oem = f"SELECTED-{item_id_m.group(1)}" if item_id_m else None

    # Determine brand
    name_up = name_raw.upper()
    if 'JAGUAR' in name_up or 'JAG' in name_up[-10:]:
        brand_name = 'Jaguar'
        brand_id = JAGUAR_BRAND_ID
        sku_prefix = 'JAG'
    else:
        brand_name = 'Land Rover'
        brand_id = LAND_ROVER_BRAND_ID
        sku_prefix = 'LR'

    return {
        'oem': oem,
        'name_heb': name_heb,
        'price': price,
        'brand': brand_name,
        'brand_id': brand_id,
        'sku_prefix': sku_prefix,
        'item_path': item_path,
    }


SUBCAT_RE = re.compile(r'href="(/\d{5,}[^"]+)"')

def collect_all_items(main_path: str) -> list[str]:
    """Get all subcategory URLs from main page, then collect product items from each."""
    html = get_html(BASE_URL + main_path)
    if not html:
        return []

    # Collect unique subcategory paths (numeric IDs, not items/, not page=)
    all_subcat = [
        p for p in dict.fromkeys(SUBCAT_RE.findall(html))
        if 'page=' not in p and '/items/' not in p and p != main_path
    ]
    log.info("Found %d subcategories at %s", len(all_subcat), main_path)

    all_items: list[str] = []
    for i, subcat in enumerate(all_subcat):
        subcat_html = get_html(BASE_URL + subcat)
        if not subcat_html:
            continue
        items = list(dict.fromkeys(ITEM_RE.findall(subcat_html)))
        # Paginate subcategory if needed
        pages = re.findall(r'page=(\d+)', subcat_html)
        max_page = max((int(p) for p in pages), default=1)
        for page in range(2, max_page + 1):
            pg_html = get_html(f"{BASE_URL}{subcat}?page={page}")
            if pg_html:
                items.extend(ITEM_RE.findall(pg_html))
            time.sleep(0.3)
        all_items.extend(items)
        all_items = list(dict.fromkeys(all_items))
        log.info("Subcat %d/%d (%s): %d items, total %d", i+1, len(all_subcat), subcat[:40], len(items), len(all_items))
        time.sleep(0.4)

    return all_items


async def import_products(conn: asyncpg.Connection, products: list[dict]) -> dict:
    inserted = updated = skipped = 0
    HEBREW_RE_local = re.compile(r'[א-ת]')

    for p in products:
        oem = p['oem']
        if not oem or len(oem) < 3:
            skipped += 1
            continue

        name_heb = p['name_heb']
        price = p['price']
        brand = p['brand']
        brand_id = p['brand_id']
        sku_prefix = p['sku_prefix']

        sku_clean = re.sub(r'[^A-Z0-9]', '-', oem.upper())
        sku = f"{sku_prefix}-{sku_clean}"

        eng_name = translate_name(name_heb)
        category = categorize(name_heb)

        # Price is Israeli consumer price incl. 17% VAT → normalize to 18% IL VAT (no markup — IL retailer ref)
        il_retail = round(price / 1.17 * 1.18, 2)

        desc = (
            f"{eng_name}. Hebrew: {name_heb}. "
            f"Israeli retail price (incl. VAT): {il_retail:.2f} ILS. "
            f"Source: selected.parts (Israeli Land Rover/Jaguar specialist)."
        )[:500]

        try:
            async with conn.transaction():
                row = await conn.fetchrow("""
                    INSERT INTO parts_catalog(
                        id, sku, oem_number, name, manufacturer, manufacturer_id,
                        category, description, specifications,
                        base_price, importer_price_ils, min_price_ils, max_price_ils,
                        part_type, is_safety_critical, needs_oem_lookup,
                        master_enriched, is_active, created_at, updated_at
                    ) VALUES (
                        gen_random_uuid(), $1, $2, $3, $4, $5::uuid,
                        $6, $7, '{}'::jsonb,
                        $8, 0, $8, $8,
                        'aftermarket', FALSE, FALSE,
                        FALSE, TRUE, NOW(), NOW()
                    )
                    ON CONFLICT (sku) DO UPDATE SET
                        base_price = EXCLUDED.base_price,
                        importer_price_ils = 0,
                        min_price_ils = EXCLUDED.min_price_ils,
                        max_price_ils = EXCLUDED.max_price_ils,
                        name = EXCLUDED.name,
                        updated_at = NOW()
                    RETURNING xmax
                """, sku, oem, eng_name[:255], brand, brand_id,
                     category, desc, il_retail)
                if row:
                    if row['xmax'] == 0:
                        inserted += 1
                    else:
                        updated += 1
        except Exception as e:
            log.warning("Failed %s (%s): %s", sku, oem, e)
            skipped += 1

    return {'inserted': inserted, 'updated': updated, 'skipped': skipped}


async def main() -> None:
    # Step 1: Collect all product URLs from both main listing pages
    all_items: dict[str, str] = {}  # path → brand hint

    for brand_label, main_path in MAIN_PAGES.items():
        log.info("Collecting items for %s ...", brand_label)
        items = collect_all_items(main_path)
        log.info("Found %d unique items for %s", len(items), brand_label)
        for path in items:
            all_items[path] = brand_label

    log.info("Total unique item paths: %d", len(all_items))

    # Step 2: Scrape each product page
    products = []
    for i, (item_path, brand) in enumerate(all_items.items()):
        p = scrape_product(item_path, brand)
        if p:
            products.append(p)
        if (i + 1) % 20 == 0:
            log.info("Scraped %d/%d products (%d valid)", i+1, len(all_items), len(products))
        time.sleep(0.4)

    log.info("Scraping complete: %d valid products out of %d", len(products), len(all_items))

    # Step 3: Import to DB
    conn = await asyncpg.connect(DB_DSN)
    try:
        result = await import_products(conn, products)
        lr_total = await conn.fetchval(
            "SELECT COUNT(*) FROM parts_catalog WHERE manufacturer='Land Rover' AND is_active=TRUE"
        )
        jag_total = await conn.fetchval(
            "SELECT COUNT(*) FROM parts_catalog WHERE manufacturer='Jaguar' AND is_active=TRUE"
        )
        log.info(
            "Import done: inserted=%d updated=%d skipped=%d | "
            "DB total Land Rover=%d Jaguar=%d",
            result['inserted'], result['updated'], result['skipped'],
            lr_total, jag_total
        )
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
