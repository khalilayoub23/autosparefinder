#!/usr/bin/env python3
"""
BDV (b-d-v.co.il) parts price list scraper.

SOURCE FORMAT (re-verified 2026-11-11): the wpDataTables table (597 rows) answers ONLY an exact part-number lookup:
the page's own filter sends column 0 search value "$<part number>^" (>= 5 characters) and returns the row(s) for that
number; any global/brand search returns data=[] (recordsFiltered is a meaningless constant). The old brand-name
enumeration therefore always found 0 rows. This script now looks up the catalog's own part numbers one by one
(rate-limited) for the BDV brands, and imports only the numbers BDV actually lists.

VAT: BDV states its prices are recommended consumer prices EXCLUDING VAT (see BDV_VAT_EVIDENCE) -> price = cost,
VAT +18% once in the consumer reference, margin once downstream (price_policy).

Usage: python3 /app/scrapers/bdv_playwright_scraper.py [--limit N] [--il-priced] [--sku A --sku B ...]
"""

import asyncio
import json
import re
import price_policy as _pp
import os
import logging
import argparse
import urllib.parse
import httpx
import asyncpg
from price_history_writer import PriceHistoryTracker
from currency_rate import DEFAULT_USD_TO_ILS as _USD_TO_ILS
from playwright.async_api import async_playwright

# ONE warranty source of truth — resolve() returns (months, source).
from warranty_policy import resolve as _warranty_resolve

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)

BDV_URL = "https://b-d-v.co.il/%D7%9E%D7%97%D7%99%D7%A8%D7%95%D7%9F-%D7%97%D7%9C%D7%A4%D7%99%D7%9D/"
AJAX_URL = "https://b-d-v.co.il/wp-admin/admin-ajax.php?action=get_wdtable&table_id=1"
DB_URL = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")

# The 10 brands BDV lists: the catalog numbers of these brands are what gets looked up.
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

# VAT basis of the BDV price list - authoritative source evidence (page text read 2026-11-11):
# "המחירים המוצגים באתר הינם מחירי חברת בכל דרך ושטח (ש.מ) בע\"מ המומלצים לצרכן והם אינם כוללים מע\"מ"
# = the prices are the company's recommended consumer prices and EXCLUDE VAT. Currency: ILS.
BDV_VAT_BASIS = "exclusive"
BDV_VAT_EVIDENCE = {
    "by": "#151-bdv", "url": BDV_URL, "checked": "2026-11-11",
    "quote_he": 'המחירים המוצגים באתר הינם מחירי חברת בכל דרך ושטח (ש.מ) בע"מ המומלצים לצרכן והם אינם כוללים מע"מ',
    "meaning": "recommended consumer price, VAT excluded, ILS",
}


def bdv_price_fields(price: float) -> dict:
    """The ONLY BDV pricing rule. The quoted price is EX-VAT, so it IS the supplier cost (supplier_parts.price_ils);
    VAT and the 45% margin are applied once, later, by price_policy. The Israeli consumer reference
    (parts_catalog.max_price_ils) is the quoted price + VAT once."""
    cost = round(float(price), 2)
    return {"cost_ex_vat": cost, "consumer_reference": round(cost * (1 + _pp.VAT_RATE), 2), "source_price": cost}


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


def lookup_body(post_body: str, sku: str) -> str:
    """The exact request the page's filter sends for one part number (verified against the live UI)."""
    params = urllib.parse.parse_qs(post_body, keep_blank_values=True)
    params["columns[0][search][value]"] = ["$" + sku + "^"]
    params["columns[0][search][regex]"] = ["false"]
    params["search[value]"] = [""]
    params["start"] = ["0"]
    params["length"] = ["-1"]
    return urllib.parse.urlencode(params, doseq=True)


async def lookup_skus(post_body: str, cookies: list[dict], skus: list[str], delay: float = 0.5) -> list[dict]:
    """Exact part-number lookups. Numbers shorter than 5 chars are refused by the site's own form and skipped."""
    cookies_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
    headers = {"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8", "X-Requested-With": "XMLHttpRequest",
               "Referer": BDV_URL, "Cookie": cookies_str, "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}
    found: dict[str, dict] = {}
    async with httpx.AsyncClient() as client:
        for sku in skus:
            sku = (sku or "").strip()
            if len(sku) < 5:
                continue
            try:
                r = await client.post(AJAX_URL, content=lookup_body(post_body, sku), headers=headers, timeout=20)
                for row in r.json().get("data", []):
                    part = parse_row(row, "")
                    if part:
                        found[part["sku"]] = part
            except Exception as e:
                log.error(f"lookup {sku}: {e}")
            await asyncio.sleep(delay)
    return list(found.values())


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
            INSERT INTO suppliers (id, name, country, website, is_active, vat_price_basis, vat_basis_evidence, created_at)
            VALUES (gen_random_uuid(), 'BDV - בכל דרך ושטח', 'IL', $1, TRUE, $2, $3::jsonb, NOW())
            RETURNING id
        """, BDV_URL, BDV_VAT_BASIS, json.dumps(BDV_VAT_EVIDENCE))
        log.info(f"Created BDV supplier: {supplier_id}")
    else:
        supplier_id = supplier["id"]
        log.info(f"Using existing BDV supplier: {supplier_id}")
    # Record the verified basis + evidence on the supplier (price_policy reads suppliers.vat_price_basis).
    try:
        await conn.execute("UPDATE suppliers SET vat_price_basis=$2, vat_basis_evidence=$3::jsonb WHERE id=$1 "
                           "AND (vat_price_basis IS DISTINCT FROM $2)", supplier_id, BDV_VAT_BASIS, json.dumps(BDV_VAT_EVIDENCE))
    except Exception as e:  # column missing in an out-of-band schema: policy then reads the legacy exclusive default
        log.warning(f"could not store vat_price_basis: {e}")

    _ph = PriceHistoryTracker(conn, "bdv_playwright_scraper"); await _ph.snapshot(supplier_id)
    updated_catalog = 0
    supplier_rows = 0
    not_matched = 0
    skip_reasons: dict = {}

    for part in parts:
        sku = part["sku"]
        price = part.get("price_ils")
        brand = part.get("manufacturer", "")

        if not price or price <= 0:
            skip_reasons["no_price"] = skip_reasons.get("no_price", 0) + 1
            continue

        # The site states its prices EXCLUDE VAT (BDV_VAT_EVIDENCE): the quoted price is the cost; the reference adds VAT once.
        _f = bdv_price_fields(price)
        price_cost = _f["cost_ex_vat"]
        price_with_vat = _f["consumer_reference"]
        in_stock = part.get("stock", "").strip() not in ["אזל", "אין מלאי", "0", ""]

        # EXACT part-number identity only (owner policy: a SKU match must be established, never guessed).
        # The normalized number must equal the catalog OEM number or SKU; a row that names a brand must match
        # that brand when more than one catalog part carries the number. No prefix / substring fallback: the old
        # "first 8 characters, same brand" branch could attach a BDV price to a different part, and LIMIT 3 wrote
        # the Israeli reference price onto every part that happened to share the number.
        matches = await conn.fetch("""
            SELECT id, manufacturer, name
            FROM parts_catalog
            WHERE (UPPER(REPLACE(REPLACE(oem_number, ' ', ''), '-', '')) =
                   UPPER(REPLACE(REPLACE($1, ' ', ''), '-', ''))
                   OR UPPER(sku) = UPPER($1))
              AND is_active = TRUE
            LIMIT 10
        """, sku)
        if len(matches) > 1 and brand:
            same_brand = [m for m in matches if (m["manufacturer"] or "").strip().lower() == brand.strip().lower()]
            matches = same_brand or matches

        if not matches:
            not_matched += 1
            skip_reasons["no_exact_catalog_match"] = skip_reasons.get("no_exact_catalog_match", 0) + 1
            continue
        if len(matches) > 1:
            # several catalog parts carry this number and the row does not say which: unresolved, nothing written
            not_matched += 1
            skip_reasons["ambiguous_catalog_match"] = skip_reasons.get("ambiguous_catalog_match", 0) + 1
            log.warning(f"BDV {sku}: {len(matches)} catalog parts share this number "
                        f"({sorted({m['manufacturer'] for m in matches})}) - not written")
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
                    (id, part_id, supplier_id, supplier_sku, price_ils, price_usd, is_available,
                     warranty_months, warranty_source, created_at, updated_at)
                VALUES (gen_random_uuid(), $1, $2, $3, $4, $5, $6, $7, $8, NOW(), NOW())
                ON CONFLICT ON CONSTRAINT supplier_parts_supplier_id_supplier_sku_key DO UPDATE
                SET price_ils = EXCLUDED.price_ils, price_usd = EXCLUDED.price_usd, is_available = EXCLUDED.is_available, updated_at = NOW()
            """, str(m["id"]), str(supplier_id), sku, price_cost,
                round(price_cost / _USD_TO_ILS, 2),  # price_usd is NOT NULL: the insert never worked without it
                in_stock, *_warranty_resolve(None))
            supplier_rows += 1

    await _ph.flush()  # price_history for changed prices (price_history_writer)
    log.info(f"DB import: catalog_updated={updated_catalog}, supplier_rows={supplier_rows}, not_matched={not_matched}, "
             f"skip_reasons={skip_reasons}")
    return updated_catalog, supplier_rows


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=int(os.getenv("BDV_LOOKUP_LIMIT", "500")))
    ap.add_argument("--sku", action="append", default=[])
    ap.add_argument("--il-priced", action="store_true",
                    help="look up catalog numbers that already carry an Israeli importer cost (likelier to be on an IL supplier's list)")
    args = ap.parse_args()
    log.info("=== BDV Scraper v4 (exact part-number lookup) ===")

    post_body, cookies = await capture_ajax_setup()
    if not post_body:
        log.error("Failed to capture POST body")
        return
    log.info(f"POST body ({len(post_body)} chars), cookies: {len(cookies)}")

    skus = list(args.sku)
    if not skus:
        conn = await asyncpg.connect(DB_URL)
        # catalog numbers of BDV's brands that have no Israeli cost yet (BDV is an Israeli source), longest-unchecked first
        skus = [r[0] for r in await conn.fetch(
            "SELECT oem_number FROM parts_catalog WHERE is_active AND oem_number IS NOT NULL AND length(oem_number) >= 5 "
            "AND manufacturer = ANY($1::text[]) AND (COALESCE(importer_price_ils, 0) > 0) = $3 ORDER BY updated_at ASC LIMIT $2",
            [b for b, _ in BRAND_SEARCHES], args.limit, bool(args.il_priced))]
        await conn.close()
    log.info(f"looking up {len(skus)} part numbers")
    all_parts = await lookup_skus(post_body, cookies, skus)

    log.info(f"=== Results === BDV lists {len(all_parts)} of {len(skus)} looked-up numbers")
    with open("/tmp/bdv_parts.json", "w", encoding="utf-8") as f:
        json.dump(all_parts, f, ensure_ascii=False, indent=2)
    for p in all_parts[:5]:
        log.info(f"  Sample: {p}")

    if any(p.get("price_ils") for p in all_parts):
        conn = await asyncpg.connect(DB_URL)
        await import_to_db(conn, all_parts)
        await conn.close()
    else:
        log.warning("BDV listed none of the looked-up numbers (no rows to import)")


if __name__ == "__main__":
    asyncio.run(main())
