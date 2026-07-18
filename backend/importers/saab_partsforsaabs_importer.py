#!/usr/bin/env python3
"""
Saab parts importer from partsforsaabs.com (WooCommerce Store API v1).
2,955 products — Saab OEM, aftermarket, and genuine parts with GBP prices.

Run inside container: python3 /app/importers/saab_partsforsaabs_importer.py
"""
from __future__ import annotations
import asyncio, logging, re, time
import asyncpg
import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

DB_DSN = (
    "postgresql://autospare:e4b79d75ca640dbe7f259618f078b82f21573e419308f668beed5e20b26b1d43"
    "@postgres_catalog:5432/autospare"
)

SAAB_BRAND_ID = None  # looked up at runtime from car_brands

API_BASE   = "https://www.partsforsaabs.com/wp-json/wc/store/v1/products"
PER_PAGE   = 100
GBP_TO_ILS = 4.78  # approximate rate — GBP→ILS

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}

CROSS_REF_RE = re.compile(
    r'(?:part\s+number[s]?|OEM|reference|equivalent to)[:\s]+([A-Z0-9][\w\-\. ,/]+)',
    re.IGNORECASE
)
HTML_TAG_RE = re.compile(r'<[^>]+>')
SAAB_MODEL_RE = re.compile(r'\b(9-[135]\w*|9000|900|600|99|96|95)\b', re.IGNORECASE)

CAT_RULES = [
    (['brake', 'disc', 'pad', 'caliper', 'abs', 'handbrake'],             'brakes'),
    (['suspension', 'shock', 'spring', 'strut', 'arm', 'bush', 'mount'],  'suspension-steering'),
    (['steering', 'rack', 'tie rod', 'track rod', 'ball joint'],          'suspension-steering'),
    (['filter', 'oil filter', 'air filter', 'fuel filter', 'cabin'],      'filters'),
    (['lamp', 'light', 'bulb', 'led', 'fog'],                             'lighting'),
    (['radiator', 'coolant', 'thermostat', 'water pump', 'cooling'],      'cooling'),
    (['engine', 'timing', 'piston', 'valve', 'gasket', 'head'],           'engine'),
    (['sensor', 'switch', 'relay', 'fuse', 'cable', 'harness', 'ecu'],   'electrical-sensors'),
    (['bumper', 'body', 'wing', 'door', 'panel', 'bonnet', 'boot',
      'grille', 'mirror', 'wiper', 'glass', 'seal'],                      'body-exterior'),
    (['seat', 'interior', 'carpet', 'trim', 'dashboard', 'airbag'],       'interior'),
    (['gearbox', 'clutch', 'gear', 'transmission', 'differential'],       'gearbox'),
    (['exhaust', 'silencer', 'manifold', 'catalytic'],                    'exhaust'),
    (['fuel pump', 'injector', 'throttle', 'carburetor'],                 'fuel-air'),
    (['belt', 'chain', 'tensioner', 'pulley'],                            'belts-chains'),
    (['turbo', 'intercooler', 'compressor'],                               'engine'),
    (['battery', 'alternator', 'starter'],                                 'electrical-sensors'),
    (['wheel', 'tyre', 'hub', 'bearing', 'axle'],                        'suspension-steering'),
    (['ac ', 'air con', 'climate', 'hvac', 'heater'],                     'air-conditioning-heating'),
]


def categorize(name: str, cat_names: list[str]) -> str:
    combined = (name + ' ' + ' '.join(cat_names)).lower()
    for keywords, cat in CAT_RULES:
        if any(kw in combined for kw in keywords):
            return cat
    return 'accessories'


def clean_html(s: str) -> str:
    return re.sub(r'\s+', ' ', HTML_TAG_RE.sub(' ', s)).strip()


def extract_oem_from_desc(desc: str) -> list[str]:
    """Pull cross-reference OEM numbers from product description."""
    clean = clean_html(desc)
    oems: list[str] = []
    for m in CROSS_REF_RE.finditer(clean):
        raw = m.group(1)
        parts = re.split(r'[,\s]+(?:and\s+)?', raw)
        for part in parts:
            part = part.strip().rstrip('.,)')
            if len(part) >= 5 and re.search(r'[A-Z0-9]{4,}', part.upper()):
                oems.append(part.upper())
    return list(dict.fromkeys(oems))[:5]


def price_gbp_pence_to_ils(pence: int) -> float:
    return round((pence / 100) * GBP_TO_ILS, 2)


async def fetch_page(client: httpx.AsyncClient, page: int) -> list[dict]:
    for attempt in range(3):
        try:
            r = await client.get(
                API_BASE,
                params={"per_page": PER_PAGE, "page": page},
                timeout=20,
            )
            if r.status_code == 200:
                return r.json()
            log.warning("Page %d: HTTP %d", page, r.status_code)
        except Exception as e:
            log.warning("Page %d attempt %d failed: %s", page, attempt + 1, e)
            await asyncio.sleep(2 ** attempt)
    return []


async def get_total_pages(client: httpx.AsyncClient) -> int:
    r = await client.head(API_BASE, params={"per_page": PER_PAGE}, timeout=15)
    total = int(r.headers.get("X-WP-TotalPages", 1))
    count = int(r.headers.get("X-WP-Total", 0))
    log.info("Total products: %d across %d pages", count, total)
    return total


async def import_products(conn: asyncpg.Connection, products: list[dict], brand_id: str) -> dict:
    inserted = updated = skipped = 0

    for p in products:
        sku_raw: str = (p.get("sku") or "").strip()
        name_raw: str = p.get("name") or ""
        name = re.sub(r'&#\d+;', lambda m: chr(int(m.group()[2:-1])), name_raw)
        name = re.sub(r'&amp;', '&', name).strip()

        if not sku_raw or len(sku_raw) < 3:
            skipped += 1
            continue

        prices_block = p.get("prices") or {}
        price_pence_str = prices_block.get("price", "0") or "0"
        try:
            price_pence = int(price_pence_str)
        except ValueError:
            skipped += 1
            continue
        if price_pence <= 0:
            skipped += 1
            continue

        price_ils = price_gbp_pence_to_ils(price_pence)
        price_ex_vat = round(price_ils / 1.17, 2)

        # OEM number: prefer SKU if it looks like a part number, else from description
        desc_html = p.get("description") or ""
        desc_clean = clean_html(desc_html)

        oem = sku_raw if re.search(r'[A-Z0-9]{5,}', sku_raw.upper()) else None
        if not oem:
            oems = extract_oem_from_desc(desc_clean)
            oem = oems[0] if oems else None
        if not oem:
            oem = f"SAAB-{p.get('id', sku_raw)}"

        cat_names = [c.get("name", "") for c in (p.get("categories") or [])]
        category = categorize(name, cat_names)

        sku = f"SAAB-{re.sub(r'[^A-Z0-9]', '-', sku_raw.upper())}"

        # Build English description
        model_m = SAAB_MODEL_RE.search(name)
        model_hint = model_m.group(0) if model_m else "Saab"
        full_desc = (
            f"{name}. Model: {model_hint}. "
            f"UK retail price: £{price_pence/100:.2f}. "
            f"Source: partsforsaabs.com."
        )[:500]

        try:
            async with conn.transaction():
                row = await conn.fetchrow("""
                    INSERT INTO parts_catalog(
                        id, sku, oem_number, name, manufacturer, manufacturer_id,
                        category, description, specifications,
                        online_price_ils, min_price_ils, max_price_ils,
                        part_type, is_safety_critical, needs_oem_lookup,
                        master_enriched, is_active, created_at, updated_at
                    ) VALUES (
                        gen_random_uuid(), $1, $2, $3, 'Saab', $4::uuid,
                        $5, $6, '{}'::jsonb,
                        $7, $8, $7,
                        'aftermarket', FALSE, FALSE,
                        FALSE, TRUE, NOW(), NOW()
                    )
                    ON CONFLICT (sku) DO UPDATE SET
                        online_price_ils = EXCLUDED.online_price_ils,
                        min_price_ils    = EXCLUDED.min_price_ils,
                        max_price_ils    = EXCLUDED.max_price_ils,
                        name             = EXCLUDED.name,
                        updated_at       = NOW()
                    RETURNING xmax
                """, sku, oem, name[:255], brand_id,
                     category, full_desc, price_ils, price_ex_vat)
                if row:
                    if row["xmax"] == 0:
                        inserted += 1
                    else:
                        updated += 1
        except Exception as e:
            log.warning("Failed %s: %s", sku, e)
            skipped += 1

    return {"inserted": inserted, "updated": updated, "skipped": skipped}


async def main() -> None:
    # Look up Saab brand ID
    conn = await asyncpg.connect(DB_DSN)
    try:
        brand_id = await conn.fetchval(
            "SELECT id::text FROM car_brands WHERE lower(name)='saab' AND is_active=TRUE LIMIT 1"
        )
        if not brand_id:
            log.error("Saab brand not found in car_brands — create it first")
            return
        log.info("Saab brand ID: %s", brand_id)

        async with httpx.AsyncClient(headers=HEADERS) as client:
            total_pages = await get_total_pages(client)

            all_products: list[dict] = []
            for page in range(1, total_pages + 1):
                products = await fetch_page(client, page)
                all_products.extend(products)
                log.info("Fetched page %d/%d — %d products so far", page, total_pages, len(all_products))
                await asyncio.sleep(0.5)  # polite crawl rate

        log.info("Fetched %d total products. Starting DB import...", len(all_products))
        result = await import_products(conn, all_products, brand_id)

        total = await conn.fetchval(
            "SELECT COUNT(*) FROM parts_catalog WHERE manufacturer='Saab' AND is_active=TRUE"
        )
        log.info(
            "Done: inserted=%d updated=%d skipped=%d | DB total Saab=%d",
            result["inserted"], result["updated"], result["skipped"], total,
        )
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
