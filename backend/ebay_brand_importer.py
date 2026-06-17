#!/usr/bin/env python3
"""
eBay Browse API importer for 0-parts brands: Rover, Daewoo, Maserati.

Uses the eBay Browse API (client_credentials) to search category 33559 (Car Parts)
on EBAY_GB with many targeted queries per brand, pages through results,
extracts part numbers from titles/aspects, and imports into parts_catalog.

Requires env vars: EBAY_CLIENT_ID, EBAY_CLIENT_SECRET
Run inside backend container: python3 /app/ebay_brand_importer.py
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import time
import httpx
import asyncpg

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

DB_DSN = (
    "postgresql://autospare:e4b79d75ca640dbe7f259618f078b82f21573e419308f668beed5e20b26b1d43"
    "@postgres_catalog:5432/autospare"
)

EBAY_CLIENT_ID     = os.environ.get("EBAY_CLIENT_ID", "")
EBAY_CLIENT_SECRET = os.environ.get("EBAY_CLIENT_SECRET", "")
EBAY_TOKEN_URL     = "https://api.ebay.com/identity/v1/oauth2/token"
EBAY_SEARCH_URL    = "https://api.ebay.com/buy/browse/v1/item_summary/search"
EBAY_MARKETPLACE   = "EBAY_GB"
EBAY_PARTS_CAT     = "33559"  # Vehicle Parts & Accessories > Car Parts

GBP_TO_ILS = 4.78

# Per-brand search queries. More queries = broader coverage.
# Each query can return up to 200 items (limit=200, offset pagination not useful after that
# since Browse API max offset=9800). We use varied search strings to get different items.
BRAND_QUERIES: dict[str, dict] = {
    "Rover": {
        "db_name": "Rover",
        "queries": [
            "rover 25 parts",
            "rover 45 parts",
            "rover 75 parts",
            "rover 200 parts",
            "rover 400 parts",
            "rover 600 parts",
            "rover 800 parts",
            "rover 820 parts",
            "rover 214 parts",
            "rover 216 parts",
            "rover 218 parts",
            "rover 220 parts",
            "rover 416 parts",
            "rover 420 parts",
            "rover 620 parts",
            "rover streetwise parts",
            "mg rover zr parts",
            "mg rover zt parts",
            "rover genuine oem",
            "rover brake disc",
            "rover suspension parts",
            "rover engine parts",
            "rover gearbox parts",
            "rover cooling parts",
            "rover body panels",
        ],
    },
    "Daewoo": {
        "db_name": "Daewoo",
        "queries": [
            "daewoo lanos parts",
            "daewoo matiz parts",
            "daewoo nubira parts",
            "daewoo nexia parts",
            "daewoo espero parts",
            "daewoo leganza parts",
            "daewoo kalos parts",
            "daewoo tacuma parts",
            "daewoo rezzo parts",
            "daewoo cielo parts",
            "daewoo genuine oem",
            "daewoo brake disc",
            "daewoo engine parts",
            "daewoo suspension parts",
            "daewoo gearbox parts",
        ],
    },
    "Maserati": {
        "db_name": "Maserati",
        "queries": [
            "maserati ghibli parts",
            "maserati quattroporte parts",
            "maserati granturismo parts",
            "maserati grancabrio parts",
            "maserati levante parts",
            "maserati 3200 parts",
            "maserati 4200 parts",
            "maserati spyder parts",
            "maserati genuine oem",
            "maserati brake disc",
            "maserati suspension parts",
            "maserati engine parts",
            "maserati gearbox parts",
            "maserati body parts",
            "maserati electrical parts",
        ],
    },
    "Dacia": {
        "db_name": "Dacia",
        "queries": [
            "dacia sandero parts",
            "dacia duster parts",
            "dacia logan parts",
            "dacia jogger parts",
            "dacia spring parts",
            "dacia bigster parts",
            "dacia genuine oem",
            "dacia brake disc",
            "dacia suspension parts",
            "dacia engine parts",
            "dacia radiator",
            "dacia alternator",
            "dacia gearbox parts",
            "dacia body parts",
            "dacia electrical parts",
            "dacia filters",
            "dacia clutch kit",
            "dacia timing belt",
            "dacia water pump",
            "dacia oem spare parts",
        ],
    },
    "Chrysler": {
        "db_name": "Chrysler",
        "queries": [
            "chrysler 300c parts",
            "chrysler voyager parts",
            "chrysler grand voyager parts",
            "chrysler sebring parts",
            "chrysler pt cruiser parts",
            "chrysler 300 parts",
            "chrysler genuine oem",
            "chrysler brake disc",
            "chrysler suspension parts",
            "chrysler engine parts",
            "chrysler gearbox parts",
            "chrysler electrical parts",
            "chrysler body parts",
        ],
    },
    "Infiniti": {
        "db_name": "Infiniti",
        "queries": [
            "infiniti q50 parts",
            "infiniti q60 parts",
            "infiniti qx50 parts",
            "infiniti qx60 parts",
            "infiniti qx70 parts",
            "infiniti fx35 parts",
            "infiniti g35 parts",
            "infiniti g37 parts",
            "infiniti ex35 parts",
            "infiniti m35 parts",
            "infiniti genuine oem",
            "infiniti brake disc",
            "infiniti suspension parts",
            "infiniti engine parts",
            "infiniti gearbox parts",
            "infiniti electrical parts",
            "infiniti body parts",
            "infiniti filters",
            "infiniti clutch",
            "infiniti timing chain",
        ],
    },
}

# Category mapping from title keywords
CAT_RULES: list[tuple[list[str], str]] = [
    (["brake", "caliper", "disc", "pad", "servo", "abs", "handbrake"],       "brakes"),
    (["suspension", "shock", "absorber", "spring", "strut", "arm", "bush",
      "bearing", "ball joint", "tie rod", "track rod", "wishbone"],          "suspension-steering"),
    (["steering", "rack", "pump", "column", "wheel"],                        "suspension-steering"),
    (["engine", "timing", "piston", "valve", "gasket", "head", "crankshaft",
      "camshaft", "flywheel", "sump", "rocker"],                             "engine"),
    (["filter", "air filter", "oil filter", "fuel filter", "cabin"],         "filters"),
    (["radiator", "coolant", "thermostat", "water pump", "cooling",
      "intercooler", "fan"],                                                  "cooling"),
    (["alternator", "starter", "battery", "sensor", "switch", "relay",
      "ecu", "module", "harness", "cable"],                                  "electrical-sensors"),
    (["lamp", "light", "bulb", "led", "fog", "headlight", "taillight"],      "lighting"),
    (["bumper", "bonnet", "door", "wing", "panel", "grille", "spoiler",
      "mirror", "glass", "seal", "sill"],                                    "body-exterior"),
    (["exhaust", "silencer", "manifold", "catalytic", "dpf", "flexi"],       "exhaust"),
    (["fuel pump", "injector", "throttle", "carburetor", "fuel rail"],       "fuel-air"),
    (["gearbox", "clutch", "gear", "transmission", "differential",
      "driveshaft", "propshaft"],                                            "gearbox"),
    (["belt", "chain", "tensioner", "pulley", "timing"],                     "belts-chains"),
    (["turbo", "supercharger", "boost"],                                     "engine"),
    (["ac ", "air con", "climate", "hvac", "heater", "blower"],              "air-conditioning-heating"),
    (["seat", "interior", "carpet", "trim", "dashboard"],                    "interior"),
    (["wheel", "tyre", "hub", "axle"],                                       "suspension-steering"),
]

# Regex to extract OEM part numbers from eBay titles
PART_NUM_RE = re.compile(
    r'\b([A-Z]{1,4}[\d]{4,}[A-Z\d\-]*|'
    r'[A-Z\d]{2,4}[-\s]?\d{4,}[A-Z\d\-]*|'
    r'\d{6,10}[A-Z\d\-]*)\b'
)


def categorize(title: str) -> str:
    t = title.lower()
    for keywords, cat in CAT_RULES:
        for kw in keywords:
            if kw in t:
                return cat
    return "accessories"


def extract_part_number(title: str, aspects: list[dict] | None = None) -> str:
    """Try to extract a manufacturer part number from title or aspects."""
    # Check aspects first
    if aspects:
        for asp in aspects:
            name = (asp.get("localizedName") or "").lower()
            if any(kw in name for kw in ("part number", "oem", "manufacturer", "reference")):
                vals = asp.get("localizedValues") or []
                if vals:
                    return str(vals[0]).strip().upper()[:50]

    # Extract from title using regex
    matches = PART_NUM_RE.findall(title.upper())
    # Filter out year-like matches (4-digit standalone numbers)
    for m in matches:
        if re.match(r'^\d{4}$', m):
            continue
        if len(m) >= 5:
            return m.strip()
    return ""


async def get_oauth_token(client: httpx.AsyncClient) -> str:
    creds = base64.b64encode(f"{EBAY_CLIENT_ID}:{EBAY_CLIENT_SECRET}".encode()).decode()
    resp = await client.post(
        EBAY_TOKEN_URL,
        data={
            "grant_type": "client_credentials",
            "scope": "https://api.ebay.com/oauth/api_scope",
        },
        headers={
            "Authorization": f"Basic {creds}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        timeout=20,
    )
    resp.raise_for_status()
    tok = resp.json()
    log.info("eBay token obtained, expires in %ds", tok.get("expires_in", 0))
    return tok["access_token"]


async def search_ebay(
    client: httpx.AsyncClient,
    token: str,
    query: str,
    limit: int = 200,
) -> list[dict]:
    """Fetch up to `limit` items from eBay for a query."""
    headers = {
        "Authorization": f"Bearer {token}",
        "X-EBAY-C-MARKETPLACE-ID": EBAY_MARKETPLACE,
    }
    items: list[dict] = []
    offset = 0
    page_size = min(limit, 200)

    while len(items) < limit:
        params = {
            "q": query,
            "category_ids": EBAY_PARTS_CAT,
            "limit": str(page_size),
            "offset": str(offset),
            "fieldgroups": "MATCHING_ITEMS",
        }
        for attempt in range(3):
            try:
                resp = await client.get(EBAY_SEARCH_URL, params=params, headers=headers, timeout=20)
                if resp.status_code == 200:
                    break
                if resp.status_code == 429:
                    await asyncio.sleep(5)
                    continue
                log.warning("eBay HTTP %d for query %r", resp.status_code, query)
                return items
            except Exception as e:
                log.warning("eBay request error (attempt %d): %s", attempt + 1, e)
                await asyncio.sleep(2)
        else:
            return items

        data = resp.json()
        batch = data.get("itemSummaries", [])
        if not batch:
            break
        items.extend(batch)
        total = data.get("total", 0)
        offset += len(batch)
        if offset >= total or offset >= 200:  # Browse API max offset = 9800, but 200 per query is enough
            break
        await asyncio.sleep(0.3)

    return items


async def harvest_brand(
    conn: asyncpg.Connection,
    client: httpx.AsyncClient,
    token: str,
    brand_key: str,
    brand_id: str,
) -> dict:
    cfg = BRAND_QUERIES[brand_key]
    brand_name = cfg["db_name"]
    queries = cfg["queries"]

    all_items: list[dict] = []
    seen_ids: set[str] = set()

    log.info("=== Harvesting eBay: %s (%d queries) ===", brand_name, len(queries))

    for i, query in enumerate(queries):
        log.info("[%d/%d] Query: %r", i + 1, len(queries), query)
        items = await search_ebay(client, token, query, limit=200)
        new = 0
        for item in items:
            item_id = item.get("itemId", "")
            if item_id and item_id not in seen_ids:
                seen_ids.add(item_id)
                all_items.append(item)
                new += 1
        log.info("  Got %d new items (total %d unique)", new, len(all_items))
        await asyncio.sleep(0.5)

    log.info("Total unique eBay items for %s: %d", brand_name, len(all_items))

    # Import to DB
    inserted = updated = skipped = 0
    for item in all_items:
        title = (item.get("title") or "").strip()
        if not title:
            skipped += 1
            continue

        price_block = item.get("price") or {}
        price_val = price_block.get("value")
        currency = price_block.get("currency", "GBP")

        if not price_val:
            skipped += 1
            continue

        try:
            price_float = float(price_val)
        except (ValueError, TypeError):
            skipped += 1
            continue

        if price_float <= 0:
            skipped += 1
            continue

        # Convert price to ILS
        if currency == "GBP":
            price_ils = round(price_float * GBP_TO_ILS, 2)
        elif currency == "EUR":
            price_ils = round(price_float * 3.9, 2)
        elif currency == "USD":
            price_ils = round(price_float * 3.72, 2)
        elif currency == "ILS":
            price_ils = round(price_float, 2)
        else:
            price_ils = round(price_float * GBP_TO_ILS, 2)  # assume GBP

        # eBay price has no IL VAT component (international source).
        # min_price_ils = online_price_ils = max_price_ils = raw eBay price.
        # normalize_base_price will later compute base_price = max_price_ils * 1.45.
        price_ex_vat = price_ils

        item_id = item.get("itemId", "")
        aspects = item.get("localizedAspects") or []
        oem = extract_part_number(title, aspects)
        if not oem:
            oem = f"EBAY-{item_id}"

        category = categorize(title)
        sku = f"EBAY-{brand_name[:3].upper()}-{re.sub(r'[^A-Z0-9]', '-', oem.upper())}"
        if len(sku) > 100:
            sku = sku[:100]

        name = title[:255]
        description = (
            f"{title}. "
            f"Source: eBay {EBAY_MARKETPLACE}. "
            f"eBay item ID: {item_id}. "
            f"Currency: {currency}, original price: {price_float:.2f}. "
            f"Category: {category}."
        )[:500]

        try:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
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
                        'aftermarket', FALSE, TRUE,
                        FALSE, TRUE, NOW(), NOW()
                    )
                    ON CONFLICT (sku) DO UPDATE SET
                        online_price_ils = EXCLUDED.online_price_ils,
                        min_price_ils    = EXCLUDED.min_price_ils,
                        max_price_ils    = EXCLUDED.max_price_ils,
                        name             = EXCLUDED.name,
                        updated_at       = NOW()
                    RETURNING xmax
                    """,
                    sku, oem[:100], name, brand_name, brand_id,
                    category, description, price_ils, price_ex_vat,
                )
                if row:
                    if row["xmax"] == 0:
                        inserted += 1
                    else:
                        updated += 1
        except Exception as e:
            log.warning("Failed %s: %s", sku, e)
            skipped += 1

    db_total = await conn.fetchval(
        "SELECT COUNT(*) FROM parts_catalog WHERE manufacturer=$1 AND is_active=TRUE",
        brand_name,
    )
    log.info(
        "Done %s: inserted=%d updated=%d skipped=%d | DB total=%d",
        brand_name, inserted, updated, skipped, db_total,
    )
    return {"brand": brand_name, "inserted": inserted, "updated": updated,
            "skipped": skipped, "db_total": db_total}


async def main() -> None:
    if not EBAY_CLIENT_ID or not EBAY_CLIENT_SECRET:
        log.error("EBAY_CLIENT_ID and EBAY_CLIENT_SECRET must be set")
        return

    conn = await asyncpg.connect(DB_DSN)
    try:
        async with httpx.AsyncClient() as client:
            token = await get_oauth_token(client)

            for brand_key in ("Rover", "Daewoo", "Maserati", "Dacia", "Chrysler", "Infiniti"):
                brand_name = BRAND_QUERIES[brand_key]["db_name"]
                brand_id = await conn.fetchval(
                    "SELECT id::text FROM car_brands WHERE lower(name)=$1 AND is_active=TRUE LIMIT 1",
                    brand_name.lower(),
                )
                if not brand_id:
                    log.warning("Brand %r not found in car_brands — skipping", brand_name)
                    continue

                await harvest_brand(conn, client, token, brand_key, brand_id)
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
