"""
Spareto.com harvester — enriches parts_catalog with aftermarket parts data.
URL pattern: https://spareto.com/products?make={MAKE}&page={N}
Product page: https://spareto.com/products/{brand-slug}-{part-type}/{part-number}

Usage:
    python3 spareto_harvester.py --makes TOYOTA HYUNDAI KIA --pages 50
    python3 spareto_harvester.py --makes TOYOTA --pages 100 --dry-run
"""

import asyncio
import asyncpg
import httpx
import json
import re
import logging
import argparse
import sys
import time
from html import unescape
from typing import Optional

DB_URL = "postgresql://autospare:e4b79d75ca640dbe7f259618f078b82f21573e419308f668beed5e20b26b1d43@postgres_catalog:5432/autospare"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    # Omit Accept-Encoding to avoid Brotli (httpx can't decompress without brotli package)
}

TARGET_MAKES = ["TOYOTA", "HYUNDAI", "KIA", "MAZDA", "FORD", "OPEL",
                "VOLKSWAGEN", "SKODA", "HONDA", "NISSAN", "MITSUBISHI"]

# EUR to ILS approximate rate (updated dynamically when possible)
EUR_TO_ILS_FALLBACK = 3.9

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


def clean_text(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def parse_product_page(url: str, text: str) -> Optional[dict]:
    """Extract structured data from a Spareto product page."""
    product = {"source_url": url}

    # Title: "PART-NUMBER BRAND Part Type | Spareto"
    title_m = re.search(r"<title>([^<]+)</title>", text)
    if title_m:
        title = title_m.group(1).replace(" | Spareto", "").strip()
        product["title_raw"] = title

    # Meta description has part number, brand, part type, and OEM refs
    meta_m = re.search(r'<meta[^>]*name="description"[^>]*content="([^"]+)"', text)
    if meta_m:
        desc = meta_m.group(1)
        product["meta_description"] = desc

        # Extract OEM numbers from description
        # Pattern: "fully compatible with original part numbers X, Y, and Z"
        oems_m = re.search(
            r"original part numbers?\s+(.+?)(?:\.|$)", desc, re.IGNORECASE
        )
        if oems_m:
            oem_str = oems_m.group(1)
            oem_nums = [
                o.strip().rstrip(".,")
                for o in re.split(r",\s+and\s+|,\s+|\s+and\s+", oem_str)
                if o.strip()
            ]
            product["oem_numbers"] = oem_nums

    # Part number and brand from URL slug
    url_m = re.search(r"/products/([^/]+)/([^/?]+)", url)
    if url_m:
        brand_slug = url_m.group(1)
        product["part_number"] = url_m.group(2).upper()
        # Brand slug is "brand-part-type" → extract brand
        # Take first word(s) before part type nouns
        brand_part = brand_slug.replace("-", " ").title()
        product["brand_raw"] = brand_part  # Will be refined below

    # Price — use itemprop first, then € symbol match
    price_m = re.search(r'itemprop=["\']price["\'][^>]*content=["\']([0-9.]+)["\']', text)
    if not price_m:
        price_m = re.search(r'€\s*([0-9]+(?:[.,][0-9]{2})?)', text)
    if price_m:
        try:
            product["price_eur"] = float(price_m.group(1).replace(",", "."))
        except ValueError:
            pass

    # Availability
    product["in_stock"] = "InStock" in text or "in-stock" in text.lower()

    # Product name from title (more precise)
    if product.get("title_raw"):
        title = product["title_raw"]
        # Format: "33 10 9211 SWAG Ignition coil"
        # Extract brand and part type by splitting on known brand names
        product["name"] = title

    # Technical specs from tables
    specs = {}
    tables = re.findall(r"<table[^>]*>(.*?)</table>", text, re.DOTALL)
    for tbl in tables[:4]:
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", tbl, re.DOTALL)
        for row in rows:
            cells = [clean_text(c) for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", row, re.DOTALL)]
            if len(cells) == 2 and cells[0] and cells[1]:
                key = cells[0].lower().replace(" ", "_")
                specs[key] = cells[1]
    if specs:
        product["specs"] = specs

    # Vehicle fitment from tables
    fitment = []
    current_make = None

    # Find make headers and fitment table rows
    # Makes appear as div headers, then table rows with model/year/kw
    make_sections = re.split(r"(?=<[^>]*class=\"[^\"]*make[^\"]*\")", text)

    # Better approach: look for vehicle table content after nav-vehicles section
    nav_veh_idx = text.find("id='nav-vehicles'")
    if nav_veh_idx < 0:
        nav_veh_idx = text.find('id="nav-vehicles"')

    if nav_veh_idx > 0:
        veh_section = text[nav_veh_idx : nav_veh_idx + 50000]

        # Find make names (appear in h5/h6 or specific divs)
        # Pattern: big section headers followed by table rows
        # Simplified: extract all table rows that look like fitment (have year ranges)
        make_matches = re.finditer(
            r"(?:class=\"[^\"]*make-name[^\"]*\"|class=\"[^\"]*brand[^\"]*\").*?>([A-Z][A-Z\s]+[A-Z])<",
            veh_section,
            re.DOTALL,
        )
        make_positions = [(m.start(), m.group(1).strip()) for m in make_matches]

        # Parse fitment table rows
        table_rows = re.findall(r"<tr[^>]*class=\"[^\"]*cars[^\"]*\".*?>(.*?)</tr>", veh_section, re.DOTALL)
        if not table_rows:
            # Try all table rows with year pattern
            table_rows = re.findall(r"<tr[^>]*>(.*?)</tr>", veh_section, re.DOTALL)

        for row in table_rows:
            cells = [clean_text(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL)]
            cells = [c for c in cells if c]
            if len(cells) >= 3:
                year_m = re.search(r"(\d{4})\s*[-–]\s*(\d{4}|\.\.\.)", " ".join(cells))
                if year_m:
                    fitment.append(cells)

        # Also find makes from text structure
        make_in_text = re.findall(
            r"(?:^|\n)\s*([A-Z][A-Z\s]{2,20})\s*\n.*?(\d+)\s+vehicles?",
            veh_section,
        )

        # Simpler: find make headers via DOM structure
        # Look for: <div class="col... make-header">TOYOTA</div> patterns
        all_makes = re.findall(
            r"(?:class=\"[^\"]*(?:make|brand|car-make)[^\"]*\"|<strong>)\s*([A-Z][A-Z\s\-]+)\s*(?:</strong>|<)",
            veh_section,
        )
        product["makes_in_fitment"] = list(set(m.strip() for m in all_makes if len(m.strip()) > 1))

    # Simpler fitment extraction from the visible vehicle table
    fitment_clean = []
    if nav_veh_idx > 0:
        veh_section = text[nav_veh_idx : nav_veh_idx + 50000]
        # Find all rows with year pattern and model name
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", veh_section, re.DOTALL)
        current_make = "Unknown"
        # Find make from div headers above rows
        make_hdrs = re.findall(
            r'<(?:div|h\d)[^>]*class="[^"]*(?:make|brand)[^"]*"[^>]*>\s*([A-Z][A-Z\s]+)\s*<',
            veh_section,
        )
        if make_hdrs:
            current_make = make_hdrs[0].strip()
        for row in rows:
            cells = [clean_text(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL)]
            cells = [c for c in cells if c and c not in ("KW", "HP", "CCM", "Body", "Model", "Produced")]
            if len(cells) >= 3:
                year_col = next((c for c in cells if re.search(r"\d{4}", c)), None)
                model_col = next((c for c in cells if len(c) > 5 and re.search(r"[A-Za-z]", c)), None)
                if year_col and model_col:
                    fitment_clean.append({
                        "make": current_make,
                        "model": model_col,
                        "years": year_col,
                        "kw": cells[-1] if cells[-1].isdigit() else None,
                    })

    product["fitment"] = fitment_clean[:50]

    return product if product.get("part_number") else None


async def get_or_create_aftermarket_brand(conn, brand_name: str) -> Optional[str]:
    """Get or create an aftermarket brand, return its UUID."""
    brand_name = brand_name.strip().upper()
    row = await conn.fetchrow(
        "SELECT id FROM aftermarket_brands WHERE UPPER(name) = $1", brand_name
    )
    if row:
        return str(row["id"])

    # Create new brand
    result = await conn.fetchval(
        """INSERT INTO aftermarket_brands (name, tier, is_active)
           VALUES ($1, 'economy', true)
           ON CONFLICT (name) DO UPDATE SET name=EXCLUDED.name
           RETURNING id""",
        brand_name.title(),
    )
    return str(result) if result else None


async def get_manufacturer_id(conn, make_name: str) -> Optional[str]:
    """Get car_brands UUID for a vehicle make."""
    row = await conn.fetchrow(
        "SELECT id FROM car_brands WHERE LOWER(name) = LOWER($1)", make_name
    )
    return str(row["id"]) if row else None


async def upsert_part(conn, product: dict, eur_rate: float) -> dict:
    """Insert or update a part from Spareto data. Returns {'inserted', 'updated', 'skipped'}."""
    part_number = product.get("part_number", "").upper()
    if not part_number:
        return {"skipped": 1}

    name = product.get("name", "") or product.get("title_raw", "Unknown Part")
    # Clean name: remove part number prefix from title
    if name.startswith(part_number):
        name = name[len(part_number):].strip()
    # Remove extra whitespace
    name = re.sub(r"\s+", " ", name).strip() or "Auto Part"

    price_eur = product.get("price_eur")
    price_ils = float(price_eur) * eur_rate if price_eur else None

    # Determine manufacturer_id — use a generic "Aftermarket" manufacturer
    # We'll use the aftermarket brand UUID directly
    brand_name = product.get("brand_raw", "Unknown").split(" ")[0]
    aftermarket_brand_id = await get_or_create_aftermarket_brand(conn, brand_name)

    # Get the "Aftermarket" manufacturer record
    mfr_row = await conn.fetchrow(
        "SELECT id FROM car_brands WHERE LOWER(name) = 'aftermarket' LIMIT 1"
    )
    # Fallback: use the first generic manufacturer
    if not mfr_row:
        mfr_row = await conn.fetchrow("SELECT id FROM car_brands LIMIT 1")
    manufacturer_id = str(mfr_row["id"]) if mfr_row else None

    if not manufacturer_id:
        return {"skipped": 1}

    specs = product.get("specs", {})
    oem_numbers = product.get("oem_numbers", [])

    try:
        # Upsert into parts_catalog
        existing = await conn.fetchrow(
            "SELECT id FROM parts_catalog WHERE sku = $1 OR oem_number = $1", part_number
        )

        if existing:
            await conn.execute(
                """UPDATE parts_catalog SET
                    name = COALESCE(NULLIF(name, ''), $2),
                    base_price = COALESCE(base_price, $3),
                    online_price_ils = COALESCE(online_price_ils, $4),
                    specifications = COALESCE(specifications, $5::jsonb),
                    aftermarket_brand_id = COALESCE(aftermarket_brand_id, $6::uuid),
                    updated_at = NOW()
                WHERE id = $1""",
                existing["id"], name, price_ils, price_ils,
                json.dumps(specs) if specs else None,
                aftermarket_brand_id,
            )
            part_id = existing["id"]
            action = "updated"
        else:
            part_id = await conn.fetchval(
                """INSERT INTO parts_catalog
                    (id, sku, oem_number, name, manufacturer_id, aftermarket_brand_id,
                     base_price, online_price_ils, specifications, is_active, part_condition)
                   VALUES (gen_random_uuid(), $1, $2, $3, $4::uuid, $5::uuid, $6, $7, $8::jsonb, true, 'new')
                   ON CONFLICT (sku) DO UPDATE SET
                     name = EXCLUDED.name,
                     updated_at = NOW()
                   RETURNING id""",
                part_number, oem_numbers[0] if oem_numbers else None,
                name, manufacturer_id, aftermarket_brand_id,
                price_ils, price_ils,
                json.dumps(specs) if specs else None,
            )
            action = "inserted"

        # Add fitment rows
        fitment = product.get("fitment", [])
        fitment_added = 0
        for fit in fitment[:20]:
            make = fit.get("make", "").strip()
            model = fit.get("model", "").strip()
            years = fit.get("years", "")
            if not make or not model or len(make) < 2 or len(model) < 2:
                continue

            # Parse year range
            year_m = re.search(r"(\d{4})\s*[-–]\s*(?:(\d{4})|\.\.\.)", years)
            if not year_m:
                continue
            year_from = int(year_m.group(1))
            year_to = int(year_m.group(2)) if year_m.group(2) else None

            # Get manufacturer_id for this make
            veh_mfr_id = await get_manufacturer_id(conn, make)

            try:
                await conn.execute(
                    """INSERT INTO part_vehicle_fitment
                        (part_id, manufacturer, model, year_from, year_to, manufacturer_id)
                       VALUES ($1, $2, $3, $4, $5, $6::uuid)
                       ON CONFLICT (part_id, manufacturer, model, year_from) DO NOTHING""",
                    part_id, make, model, year_from, year_to, veh_mfr_id,
                )
                fitment_added += 1
            except Exception as e:
                log.debug("Fitment insert error: %s", e)

        return {action: 1, "fitment": fitment_added}

    except Exception as e:
        log.warning("Upsert failed for %s: %s", part_number, e)
        return {"error": 1}


async def fetch_product_urls(client: httpx.AsyncClient, make: str, page: int) -> list[str]:
    """Fetch product URLs from a listing page."""
    r = await client.get(
        "https://spareto.com/products",
        params={"make": make, "page": str(page)},
        timeout=15,
    )
    if r.status_code != 200:
        return []
    # Extract href="/products/brand-part-type/part-number" — 3 path segments
    all_hrefs = re.findall(r'href="(/products/[^"?#]+)"', r.text)
    links = list(set(h for h in all_hrefs if h.count("/") == 3))
    return links


async def fetch_product(client: httpx.AsyncClient, url: str) -> Optional[dict]:
    """Fetch and parse a product detail page."""
    full_url = f"https://spareto.com{url}"
    try:
        r = await client.get(full_url, timeout=15)
        if r.status_code != 200:
            return None
        return parse_product_page(full_url, r.text)
    except Exception as e:
        log.debug("Fetch error for %s: %s", url, e)
        return None


async def get_eur_rate(conn) -> float:
    """Get EUR→ILS exchange rate from DB or use fallback."""
    try:
        row = await conn.fetchrow(
            "SELECT value FROM system_settings WHERE key = 'eur_to_ils_rate' LIMIT 1"
        )
        if row:
            return float(row["value"])
    except Exception:
        pass
    return EUR_TO_ILS_FALLBACK


async def harvest(makes: list[str], max_pages: int = 50, dry_run: bool = False, concurrency: int = 3):
    """Main harvest loop."""
    conn = await asyncpg.connect(DB_URL)
    eur_rate = await get_eur_rate(conn)
    log.info("EUR/ILS rate: %.2f", eur_rate)

    stats = {"inserted": 0, "updated": 0, "skipped": 0, "error": 0, "fitment": 0}

    async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True) as client:
        for make in makes:
            log.info("Processing make: %s (up to %d pages)", make, max_pages)
            seen_urls: set[str] = set()

            for page in range(1, max_pages + 1):
                product_urls = await fetch_product_urls(client, make, page)
                new_urls = [u for u in product_urls if u not in seen_urls]
                seen_urls.update(product_urls)

                if not new_urls:
                    if page > 2:
                        log.info("  No new products on page %d — stopping %s", page, make)
                        break
                    continue

                log.info("  Page %d: %d new products", page, len(new_urls))

                # Fetch products with bounded concurrency
                sem = asyncio.Semaphore(concurrency)

                async def fetch_with_delay(url):
                    async with sem:
                        await asyncio.sleep(0.4)  # rate limit: ~7 req/s max
                        return await fetch_product(client, url)

                products = await asyncio.gather(*[fetch_with_delay(u) for u in new_urls])

                for p in products:
                    if not p:
                        stats["skipped"] += 1
                        continue

                    if dry_run:
                        log.info("  [DRY] %s — %s — €%.2f — %d fitment",
                                 p.get("part_number", "?"),
                                 p.get("name", "?")[:50],
                                 p.get("price_eur", 0) or 0,
                                 len(p.get("fitment", [])))
                        stats["skipped"] += 1
                        continue

                    result = await upsert_part(conn, p, eur_rate)
                    for k, v in result.items():
                        stats[k] = stats.get(k, 0) + v

                if page % 5 == 0:
                    log.info("  Progress: %s", stats)
                    await asyncio.sleep(1)  # brief pause every 5 pages

    await conn.close()
    return stats


def main():
    parser = argparse.ArgumentParser(description="Harvest Spareto.com aftermarket parts")
    parser.add_argument("--makes", nargs="+", default=TARGET_MAKES[:4], help="Vehicle makes to harvest")
    parser.add_argument("--pages", type=int, default=50, help="Max pages per make")
    parser.add_argument("--dry-run", action="store_true", help="Fetch but don't write to DB")
    parser.add_argument("--concurrency", type=int, default=3, help="Concurrent requests")
    args = parser.parse_args()

    log.info("Starting Spareto harvest: makes=%s pages=%d dry=%s", args.makes, args.pages, args.dry_run)
    stats = asyncio.run(harvest(args.makes, args.pages, args.dry_run, args.concurrency))
    log.info("Final stats: %s", stats)
    return stats


if __name__ == "__main__":
    main()
