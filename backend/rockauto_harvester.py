#!/usr/bin/env python3
"""
RockAuto.com parts harvester — Playwright-based (bypasses JS, requires real browser).

NOTE: RockAuto blocks server IPs. Use a residential proxy or run from a non-datacenter IP.
Set env var PROXY_URL=socks5://user:pass@host:port  or  HTTP_PROXY=http://host:port

Usage:
    # Single brand
    python3 rockauto_harvester.py --brand daewoo --max-models 5

    # Multiple brands
    python3 rockauto_harvester.py --brand saab rover ssangyong daewoo --max-models 0

    # With proxy
    PROXY_URL=socks5://127.0.0.1:1080 python3 rockauto_harvester.py --brand saab

RockAuto embeds parts as a large PHP/JS array in the page source — very compact format.
Structure: /en/catalog/{make}/{year}/{model}/{engine}/{category}
"""
from __future__ import annotations

import argparse
import asyncio
import asyncpg
import json
import logging
import os
import re
import time
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

DB_DSN = (
    "postgresql://autospare:e4b79d75ca640dbe7f259618f078b82f21573e419308f668beed5e20b26b1d43"
    "@postgres_catalog:5432/autospare"
)

BASE_URL    = "https://www.rockauto.com"
USD_TO_ILS  = 3.72  # approximate — refreshed at runtime if possible
PROXY_URL   = os.environ.get("PROXY_URL") or os.environ.get("HTTP_PROXY") or ""

BRAND_DB_NAMES = {
    "daewoo":    "Daewoo",
    "saab":      "Saab",
    "rover":     "Rover",
    "ssangyong": "SsangYong",
    "maserati":  "Maserati",
    "daihatsu":  "Daihatsu",
    "tesla":     "Tesla",
}

# RockAuto category slug → our DB category
CAT_MAP = {
    "brake": "brakes", "brakes": "brakes",
    "suspension": "suspension-steering", "steering": "suspension-steering",
    "engine": "engine", "cooling": "cooling",
    "electrical": "electrical-sensors", "sensors": "electrical-sensors",
    "filter": "filters", "filters": "filters",
    "lighting": "lighting", "body": "body-exterior",
    "exhaust": "exhaust", "fuel": "fuel-air",
    "transmission": "gearbox", "clutch": "gearbox",
    "belt": "belts-chains", "timing": "belts-chains",
    "wheel": "suspension-steering", "axle": "suspension-steering",
    "ac": "air-conditioning-heating", "hvac": "air-conditioning-heating",
}


def map_category(slug: str) -> str:
    s = slug.lower()
    for k, v in CAT_MAP.items():
        if k in s:
            return v
    return "accessories"


# ── Playwright fetch ──────────────────────────────────────────────────────────

async def _playwright_get(url: str, timeout_ms: int = 25000) -> Optional[str]:
    """Load a RockAuto page using Playwright chromium (headless).
    Returns HTML or None on failure."""
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        log.error("playwright not installed — run: pip install playwright && playwright install chromium")
        return None

    try:
        async with async_playwright() as p:
            launch_kwargs = {
                "headless": True,
                "args": ["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"],
            }
            browser = await p.chromium.launch(**launch_kwargs)
            ctx_kwargs: dict = {
                "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "locale": "en-US",
            }
            if PROXY_URL:
                ctx_kwargs["proxy"] = {"server": PROXY_URL}
                log.info("Using proxy: %s", PROXY_URL)
            ctx = await browser.new_context(**ctx_kwargs)
            page = await ctx.new_page()

            # Stealth: remove webdriver flag
            await ctx.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            """)

            response = await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            await page.wait_for_timeout(2500)
            html = await page.content()
            await browser.close()

            if response and response.status == 200:
                return html
            log.warning("RockAuto %s returned HTTP %s", url, response.status if response else "?")
            return None
    except Exception as e:
        log.error("Playwright error for %s: %s", url, e)
        return None


# ── RockAuto page parsers ─────────────────────────────────────────────────────

def parse_make_page(html: str, make: str) -> list[dict]:
    """Extract model/year links from /en/catalog/{make}"""
    # RockAuto lists models as links like: /en/catalog/daewoo/1999/lanos
    pattern = re.compile(
        r'href="(/en/catalog/' + re.escape(make) + r'/(\d{4})/([^/"]+))"',
        re.IGNORECASE
    )
    seen: dict[str, dict] = {}
    for m in pattern.finditer(html):
        path, year, model = m.group(1), m.group(2), m.group(3)
        key = f"{year}/{model}"
        if key not in seen:
            seen[key] = {"path": path, "year": int(year), "model": model}
    return list(seen.values())


def parse_model_page(html: str, make: str, year: int, model: str) -> list[dict]:
    """Extract engine/trim links from /en/catalog/{make}/{year}/{model}"""
    pattern = re.compile(
        r'href="(/en/catalog/' + re.escape(make) + r'/' + str(year) + r'/' + re.escape(model) + r'/([^/"]+)/([^/"]+))"',
        re.IGNORECASE
    )
    seen: dict[str, dict] = {}
    for m in pattern.finditer(html):
        path, engine, cat = m.group(1), m.group(2), m.group(3)
        key = f"{engine}/{cat}"
        if key not in seen:
            seen[key] = {"path": path, "engine": engine, "category": cat}
    return list(seen.values())


def parse_parts_page(html: str, make: str, year: int, model: str, engine: str, category: str) -> list[dict]:
    """Extract parts from a RockAuto parts listing page.

    RockAuto embeds part data as a large var[N] = [...] JavaScript array in the page.
    Each entry: [partNumber, brand, name, priceUSD, ...]
    """
    parts: list[dict] = []

    # Method 1: var[N] = [...] arrays (the main data source)
    var_pattern = re.compile(r'var\w*\s*=\s*\[([^\]]{10,})\]', re.S)
    for m in var_pattern.finditer(html):
        raw = m.group(1)
        # Look for entries that look like part data: ["partnum", "brand", "name", price, ...]
        entry_pattern = re.compile(r'\[([^\[\]]+)\]')
        for entry_m in entry_pattern.finditer(raw):
            vals_raw = entry_m.group(1)
            try:
                vals = json.loads(f"[{vals_raw}]")
            except Exception:
                continue
            if len(vals) >= 4 and isinstance(vals[0], str) and isinstance(vals[3], (int, float)):
                part_num, brand_name, name_val, price_usd = vals[0], vals[1], vals[2], vals[3]
                if price_usd > 0 and len(part_num) >= 4:
                    parts.append({
                        "part_number": str(part_num).strip(),
                        "brand_name":  str(brand_name).strip(),
                        "name":        str(name_val).strip(),
                        "price_usd":   float(price_usd),
                        "year":        year,
                        "model":       model,
                        "engine":      engine,
                        "category":    category,
                        "make":        make,
                    })

    # Method 2: JSON-LD structured data
    ld_pattern = re.compile(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', re.S)
    for ld_m in ld_pattern.finditer(html):
        try:
            data = json.loads(ld_m.group(1))
            if isinstance(data, list):
                items = data
            elif isinstance(data, dict):
                items = data.get("@graph", [data])
            else:
                continue
            for item in items:
                if item.get("@type") in ("Product", "Offer"):
                    pn = item.get("sku") or item.get("productID") or ""
                    nm = item.get("name") or ""
                    price = None
                    offers = item.get("offers") or item.get("offer") or {}
                    if isinstance(offers, dict):
                        price = offers.get("price")
                    elif isinstance(offers, list) and offers:
                        price = offers[0].get("price")
                    if pn and nm and price:
                        parts.append({
                            "part_number": str(pn).strip(),
                            "brand_name":  item.get("brand", {}).get("name", "") if isinstance(item.get("brand"), dict) else "",
                            "name":        str(nm).strip(),
                            "price_usd":   float(price),
                            "year":        year,
                            "model":       model,
                            "engine":      engine,
                            "category":    category,
                            "make":        make,
                        })
        except Exception:
            continue

    return parts


# ── DB helpers ────────────────────────────────────────────────────────────────

async def upsert_parts(conn: asyncpg.Connection, parts: list[dict], brand_id: str, brand_name: str) -> dict:
    inserted = updated = skipped = 0
    for p in parts:
        pn = (p.get("part_number") or "").strip()
        if not pn or len(pn) < 3:
            skipped += 1
            continue

        price_ils = round(p["price_usd"] * USD_TO_ILS, 2)
        price_ex  = round(price_ils / 1.17, 2)
        sku_clean = re.sub(r"[^A-Z0-9]", "-", pn.upper())
        sku       = f"{brand_name[:3].upper()}-{sku_clean}"
        name      = (p.get("name") or pn)[:255]
        category  = map_category(p.get("category", ""))
        desc = (
            f"{name}. {brand_name} {p.get('year','')} {p.get('model','').replace('-',' ')} "
            f"{p.get('engine','')}. "
            f"Part brand: {p.get('brand_name','')}. "
            f"Category: {p.get('category','')}. "
            f"USD price: ${p['price_usd']:.2f}. Source: rockauto.com."
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
                        gen_random_uuid(), $1, $2, $3, $4, $5::uuid,
                        $6, $7, '{}'::jsonb,
                        $8, $9, $8,
                        'aftermarket', FALSE, FALSE,
                        FALSE, TRUE, NOW(), NOW()
                    )
                    ON CONFLICT (sku) DO UPDATE SET
                        online_price_ils = EXCLUDED.online_price_ils,
                        min_price_ils    = EXCLUDED.min_price_ils,
                        name             = EXCLUDED.name,
                        updated_at       = NOW()
                    RETURNING xmax
                """, sku, pn, name, brand_name, brand_id,
                     category, desc, price_ils, price_ex)
                if row:
                    if row["xmax"] == 0:
                        inserted += 1
                    else:
                        updated += 1
        except Exception as e:
            log.warning("Upsert failed %s: %s", sku, e)
            skipped += 1
    return {"inserted": inserted, "updated": updated, "skipped": skipped}


# ── Main harvest pipeline ─────────────────────────────────────────────────────

async def harvest_brand(
    conn: asyncpg.Connection,
    make: str,
    brand_id: str,
    brand_name: str,
    max_models: int = 0,
) -> dict:
    total_parts: list[dict] = []

    log.info("=== Harvesting RockAuto: %s (max_models=%s) ===", make, max_models or "all")

    # Step 1: get model/year list
    make_url = f"{BASE_URL}/en/catalog/{make}"
    html = await _playwright_get(make_url)
    if not html:
        return {"error": f"Failed to load {make_url}"}

    model_variants = parse_make_page(html, make)
    if not model_variants:
        log.warning("No models found for %s — page may be blocked or empty", make)
        return {"error": "no_models_found"}

    log.info("Found %d model/year combinations for %s", len(model_variants), make)
    if max_models:
        model_variants = model_variants[:max_models]

    # Step 2: for each model/year, get categories and parts
    for i, mv in enumerate(model_variants):
        model_url = f"{BASE_URL}{mv['path']}"
        log.info("[%d/%d] Loading model: %s", i + 1, len(model_variants), mv["path"])
        model_html = await _playwright_get(model_url)
        if not model_html:
            await asyncio.sleep(2)
            continue

        cat_variants = parse_model_page(model_html, make, mv["year"], mv["model"])
        if not cat_variants:
            # Page might already be a parts listing
            page_parts = parse_parts_page(model_html, make, mv["year"], mv["model"], "", "unknown")
            total_parts.extend(page_parts)
            await asyncio.sleep(1.5)
            continue

        for cv in cat_variants[:10]:  # limit categories per model
            cat_url = f"{BASE_URL}{cv['path']}"
            cat_html = await _playwright_get(cat_url)
            if not cat_html:
                await asyncio.sleep(1)
                continue
            page_parts = parse_parts_page(cat_html, make, mv["year"], mv["model"], cv["engine"], cv["category"])
            total_parts.extend(page_parts)
            log.info("  %s/%s: %d parts", cv["engine"], cv["category"], len(page_parts))
            await asyncio.sleep(1.0)

        await asyncio.sleep(2.0)

    # Deduplicate by part_number
    seen: dict[str, dict] = {}
    for p in total_parts:
        k = p["part_number"].upper()
        if k not in seen:
            seen[k] = p
    deduped = list(seen.values())
    log.info("Unique parts: %d (from %d raw)", len(deduped), len(total_parts))

    result = await upsert_parts(conn, deduped, brand_id, brand_name)
    db_count = await conn.fetchval(
        "SELECT COUNT(*) FROM parts_catalog WHERE manufacturer=$1 AND is_active=TRUE",
        brand_name,
    )
    log.info("DB total %s: %d | inserted=%d updated=%d skipped=%d",
             brand_name, db_count, result["inserted"], result["updated"], result["skipped"])
    return result


async def main() -> None:
    ap = argparse.ArgumentParser(description="Harvest RockAuto.com parts via Playwright")
    ap.add_argument("--brand", nargs="+", default=list(BRAND_DB_NAMES.keys()),
                    help="RockAuto make slugs to harvest")
    ap.add_argument("--max-models", type=int, default=0,
                    help="Max model/year combinations per brand (0=all)")
    args = ap.parse_args()

    if not PROXY_URL:
        log.warning(
            "No PROXY_URL set. RockAuto blocks datacenter IPs — this may time out. "
            "Set PROXY_URL=socks5://host:port to use a residential proxy."
        )

    conn = await asyncpg.connect(DB_DSN)
    try:
        for make in args.brand:
            make = make.lower()
            brand_name = BRAND_DB_NAMES.get(make, make.title())
            brand_id = await conn.fetchval(
                "SELECT id::text FROM car_brands WHERE lower(name)=$1 AND is_active=TRUE LIMIT 1",
                brand_name.lower(),
            )
            if not brand_id:
                log.warning("Brand %r not found in car_brands — skipping", brand_name)
                continue
            await harvest_brand(conn, make, brand_id, brand_name, args.max_models)
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
