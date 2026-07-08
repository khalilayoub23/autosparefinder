#!/usr/bin/env python3
"""
BDV (b-d-v.co.il) parts price list scraper.

The wpDataTables table requires a search term to return data (data=[] on empty search).
Strategy: search by each brand name to collect all 597 parts across 10 brands.
Each brand search returns that brand's parts with prices.
"""

import asyncio
import json
import re
import os
import logging
import urllib.parse
import httpx
import asyncpg
from playwright.async_api import async_playwright

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)

BDV_URL = "https://b-d-v.co.il/%D7%9E%D7%97%D7%99%D7%A8%D7%95%D7%9F-%D7%97%D7%9C%D7%A4%D7%99%D7%9D/"
AJAX_URL = "https://b-d-v.co.il/wp-admin/admin-ajax.php?action=get_wdtable&table_id=1"
DB_URL = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")

# Search terms that cover all 10 BDV brands.
# These map to the "Group" (קבוצה) column or part name on the BDV site.
BRAND_SEARCHES = [
    ("Land Rover", "land rover"),
    ("Chrysler", "chrysler"),
    ("Jeep", "jeep"),
    ("Alfa Romeo", "alfa romeo"),
    ("Lexus", "lexus"),
    ("Jaguar", "jaguar"),
    ("Volvo", "volvo"),
    ("Porsche", "porsche"),
    ("Cadillac", "cadillac"),
    ("Infiniti", "infiniti"),
]

# Also try Hebrew brand names in case the group column is Hebrew
HEBREW_SEARCHES = [
    ("Land Rover", "לנד רובר"),
    ("Chrysler", "קריייזלר"),
    ("Jeep", "ג'יפ"),
    ("Alfa Romeo", "אלפא רומיאו"),
    ("Lexus", "לקסוס"),
    ("Jaguar", "יגואר"),
    ("Volvo", "וולוו"),
    ("Porsche", "פורשה"),
    ("Cadillac", "קאדילק"),
    ("Infiniti", "אינפיניטי"),
]


def parse_price(raw: str) -> float | None:
    clean = re.sub(r'<[^>]+>', '', raw)
    clean = re.sub(r'[^\d.,]', '', clean).replace(',', '').strip()
    try:
        return float(clean) if clean else None
    except ValueError:
        return None


def parse_row(row, brand: str = "") -> dict | None:
    if isinstance(row, list):
        sku = str(row[0]).strip() if len(row) > 0 else ""
        name = str(row[1]).strip() if len(row) > 1 else ""
        group = str(row[2]).strip() if len(row) > 2 else ""
        stock = str(row[3]).strip() if len(row) > 3 else ""
        price_raw = str(row[4]).strip() if len(row) > 4 else ""
    elif isinstance(row, dict):
        # wpDataTables often keys by column index as string
        sku = str(row.get("0", row.get(0, ""))).strip()
        name = str(row.get("1", row.get(1, ""))).strip()
        group = str(row.get("2", row.get(2, ""))).strip()
        stock = str(row.get("3", row.get(3, ""))).strip()
        price_raw = str(row.get("4", row.get(4, ""))).strip()
    else:
        return None

    price = parse_price(price_raw)
    if not sku and not name:
        return None

    return {
        "sku": sku,
        "name": name,
        "group": group,
        "stock": stock,
        "price_ils": price,
        "manufacturer": brand,
    }


async def search_brand(client: httpx.AsyncClient, post_body: str, cookies_str: str, brand: str, search_term: str) -> list[dict]:
    """Make an AJAX request with a search term to get brand parts."""
    params = urllib.parse.parse_qs(post_body, keep_blank_values=True)

    # Set the global search value
    params["search[value]"] = [search_term]
    params["search[regex]"] = ["false"]
    params["length"] = ["200"]   # get up to 200 results
    params["start"] = ["0"]
    params["draw"] = ["2"]

    body = urllib.parse.urlencode(params, doseq=True)

    headers = {
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": BDV_URL,
        "Cookie": cookies_str,
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
    }

    try:
        r = await client.post(AJAX_URL, content=body, headers=headers, timeout=20)
        resp = r.json()
        total = int(resp.get("recordsTotal", 0))
        filtered = int(resp.get("recordsFiltered", 0))
        rows = resp.get("data", [])
        log.info(f"  [{brand}] search='{search_term}': filtered={filtered}, rows={len(rows)}")

        parts = []
        for row in rows:
            p = parse_row(row, brand)
            if p:
                parts.append(p)
        return parts
    except Exception as e:
        log.error(f"  [{brand}] search error: {e}")
        return []


async def scrape_all_brands(post_body: str, cookies: list[dict]) -> list[dict]:
    """Search for each brand and collect all parts."""
    cookies_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
    all_parts = {}  # sku -> part (deduplicate)

    async with httpx.AsyncClient() as client:
        for brand, en_term in BRAND_SEARCHES:
            parts = await search_brand(client, post_body, cookies_str, brand, en_term)
            for p in parts:
                all_parts[p["sku"]] = p
            await asyncio.sleep(0.5)

        # If any brand got 0 results, try Hebrew
        brand_counts = {}
        for p in all_parts.values():
            b = p.get("manufacturer", "")
            brand_counts[b] = brand_counts.get(b, 0) + 1

        for brand, heb_term in HEBREW_SEARCHES:
            if brand_counts.get(brand, 0) == 0:
                log.info(f"  [{brand}] trying Hebrew search: '{heb_term}'")
                parts = await search_brand(client, post_body, cookies_str, brand, heb_term)
                for p in parts:
                    all_parts[p["sku"]] = p
                await asyncio.sleep(0.5)

    return list(all_parts.values())


async def capture_ajax_setup() -> tuple[str, list[dict]]:
    """Launch browser, capture POST body and cookies."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = await context.new_page()

        captured_body = []

        async def on_request(request):
            if "admin-ajax" in request.url and request.method == "POST" and not captured_body:
                captured_body.append(request.post_data or "")

        page.on("request", on_request)
        await page.goto(BDV_URL, wait_until="networkidle", timeout=30000)
        await asyncio.sleep(2)

        cookies = await context.cookies()
        await browser.close()

        return captured_body[0] if captured_body else "", cookies


async def import_to_db(conn: asyncpg.Connection, parts: list[dict]):
    """Import BDV parts into DB."""
    # Get or create BDV supplier
    supplier = await conn.fetchrow(
        "SELECT id FROM suppliers WHERE name ILIKE '%bdv%' OR name ILIKE '%בכל דרך%' LIMIT 1"
    )
    if not supplier:
        supplier_id = await conn.fetchval("""
            INSERT INTO suppliers (id, name, country, currency, url, is_active, created_at)
            VALUES (gen_random_uuid(), 'BDV - בכל דרך ושטח', 'IL', 'ILS', $1, TRUE, NOW())
            RETURNING id
        """, BDV_URL)
        log.info(f"Created BDV supplier: {supplier_id}")
    else:
        supplier_id = supplier["id"]
        log.info(f"Using existing BDV supplier: {supplier_id}")

    updated_catalog = 0
    supplier_rows = 0
    not_matched = 0

    for part in parts:
        sku = part["sku"]
        price = part.get("price_ils")
        brand = part.get("manufacturer", "")

        if not price or price <= 0:
            continue

        # BDV states prices are excl. VAT — add 17%
        price_with_vat = round(price * 1.17, 2)
        in_stock = part.get("stock", "").strip() not in ["אזל", "אין מלאי", "0", ""]

        # Match by SKU/OEM
        matches = await conn.fetch("""
            SELECT id, manufacturer, name
            FROM parts_catalog
            WHERE (UPPER(REPLACE(REPLACE(oem_number, ' ', ''), '-', '')) =
                   UPPER(REPLACE(REPLACE($1, ' ', ''), '-', ''))
                   OR UPPER(sku) = UPPER($1))
              AND is_active = TRUE
            LIMIT 3
        """, sku)

        if not matches and brand:
            matches = await conn.fetch("""
                SELECT id, manufacturer, name
                FROM parts_catalog
                WHERE manufacturer = $1
                  AND is_active = TRUE
                  AND (UPPER(REPLACE(REPLACE(oem_number, ' ', ''), '-', '')) LIKE '%' || UPPER($2) || '%'
                       OR UPPER(sku) LIKE '%' || UPPER($2) || '%')
                LIMIT 1
            """, brand, sku[:8])

        if not matches:
            not_matched += 1
            continue

        for m in matches:
            await conn.execute("""
                UPDATE parts_catalog
                SET max_price_ils = GREATEST(COALESCE(max_price_ils, 0), $1),
                    updated_at = NOW()
                WHERE id = $2
            """, price_with_vat, str(m["id"]))
            updated_catalog += 1

            await conn.execute("""
                INSERT INTO supplier_parts
                    (id, part_id, supplier_id, sku, price_ils, currency, in_stock, created_at, updated_at)
                VALUES (gen_random_uuid(), $1, $2, $3, $4, 'ILS', $5, NOW(), NOW())
                ON CONFLICT ON CONSTRAINT supplier_parts_supplier_id_supplier_sku_key DO UPDATE
                SET price_ils = EXCLUDED.price_ils, in_stock = EXCLUDED.in_stock, updated_at = NOW()
            """, str(m["id"]), str(supplier_id), sku, price_with_vat, in_stock)
            supplier_rows += 1

    log.info(f"DB import: catalog_updated={updated_catalog}, supplier_rows={supplier_rows}, not_matched={not_matched}")
    return updated_catalog, supplier_rows


async def main():
    log.info("=== BDV Scraper v3 (search-by-brand) ===")

    post_body, cookies = await capture_ajax_setup()
    if not post_body:
        log.error("Failed to capture POST body")
        return

    log.info(f"POST body ({len(post_body)} chars), cookies: {len(cookies)}")

    all_parts = await scrape_all_brands(post_body, cookies)

    log.info(f"\n=== Results ===")
    log.info(f"Total unique parts: {len(all_parts)}")
    with_price = sum(1 for p in all_parts if p.get("price_ils"))
    log.info(f"Parts with price: {with_price}")

    from collections import Counter
    for mfr, cnt in Counter(p.get("manufacturer", "?") for p in all_parts).most_common():
        priced = sum(1 for p in all_parts if p.get("manufacturer") == mfr and p.get("price_ils"))
        log.info(f"  {mfr}: {cnt} parts ({priced} with price)")

    with open("/tmp/bdv_parts.json", "w", encoding="utf-8") as f:
        json.dump(all_parts, f, ensure_ascii=False, indent=2)
    log.info("Saved /tmp/bdv_parts.json")

    # Show sample
    for p in all_parts[:5]:
        log.info(f"  Sample: {p}")

    if with_price > 0:
        conn = await asyncpg.connect(DB_URL)
        await import_to_db(conn, all_parts)
        await conn.close()
    else:
        log.warning("No parts with prices — check search terms")


if __name__ == "__main__":
    asyncio.run(main())
