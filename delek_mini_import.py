#!/usr/bin/env python3
"""
Delek Motors MINI parts harvest + import.

Harvests all MINI-specific parts from the Delek Motors official price list API
(brandId=3 = BMW Group, filtered to MINI model codes in part name).
Imports directly to parts_catalog + supplier_parts.

MINI model codes:
  R-series (classic): R50, R52, R53, R55, R56, R57, R58, R59, R60, R61, R62
  F-series (modern):  F54, F55, F56, F57, F60, F66
  General MINI terms: MINI, mini, Clubman, Countryman, Paceman, Cooper, ONE, JCW

API:
  GET https://serviceforms.delek-motors.co.il/home/GetPriceListReplacements
  Params: brandId=3, sku={item_prefix}, name={name_search}
  Headers: Origin: https://campaigns.mini.co.il, Referer: https://campaigns.mini.co.il/

Response item fields:
  id           - unique DB row id (for deduplication)
  brandId      - 3
  item         - OEM number (catalog number)
  sku          - internal/supplier SKU
  name         - Hebrew part name (often includes model code)
  foreignName  - English part name
  isOriginal   - "מקורי" (original) / "תחליפי" (aftermarket) / other
  provider     - supplier code ("B"=BMW/Delek, "T"/"FA"=aftermarket)
  priceWithTax - price including 18% VAT (ILS)
  vehicleModel - model ID ("0" = multiple models)
  modelDescription - model description text
  submitDate   - last update date
"""

import asyncio
import logging
import argparse
import re
import time
import uuid
from datetime import datetime

import asyncpg
import httpx

API_URL = "https://serviceforms.delek-motors.co.il/home/GetPriceListReplacements"
BRAND_ID = 3

API_HEADERS = {
    "Origin": "https://campaigns.mini.co.il",
    "Referer": "https://campaigns.mini.co.il/",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
}

MINI_NAME_QUERIES = [
    "R50", "R52", "R53", "R55", "R56", "R57", "R58", "R59",
    "R60", "R61", "R62",
    "F54", "F55", "F56", "F57", "F60", "F66",
    "MINI", "Clubman", "Countryman", "Paceman", "JCW",
]

MINI_CONFIRM_RE = re.compile(
    r"\b(R5[0-9]|R6[0-9]|F5[4567]|F6[06]|MINI|\u05de\u05d9\u05e0\u05d9|Clubman|Countryman|Paceman|JCW)\b",
    re.I
)

DB_URL = (
    "postgresql://autospare:e4b79d75ca640dbe7f259618f078b82f21573e419308f668beed5e20b26b1d43"
    "@localhost:5432/autospare"
)

MINI_BRAND_ID = "47a433bf-4f6f-4f8f-a686-a8c02f7727a8"
SUPPLIER_NAME = "Delek Motors IL"
SUPPLIER_URL  = "https://www.delekmotors.co.il"
SUPPLIER_ID   = None

SKU_PREFIX = "MINI-DL"

IS_ORIGINAL_MAP = {
    "\u05de\u05e7\u05d5\u05e8\u05d9":  ("Original",    None),
    "\u05ea\u05d7\u05dc\u05d9\u05e4\u05d9": ("Aftermarket", "OE_equivalent"),
    "\u05d7\u05dc\u05d9\u05e4\u05d9":  ("Aftermarket", "economy"),
}

log = logging.getLogger("delek_mini")


async def fetch_name_query(client: httpx.AsyncClient, name: str) -> list[dict]:
    params = {"brandId": BRAND_ID, "sku": "", "name": name}
    for attempt in range(3):
        try:
            resp = await client.get(API_URL, params=params, headers=API_HEADERS, timeout=30)
            resp.raise_for_status()
            data = resp.json().get("data", [])
            return data
        except Exception as e:
            log.warning("Attempt %d failed for name=%s: %s", attempt + 1, name, e)
            await asyncio.sleep(2 ** attempt)
    return []


async def harvest_mini_parts() -> list[dict]:
    seen_ids: set[int] = set()
    all_parts: list[dict] = []

    async with httpx.AsyncClient() as client:
        for query in MINI_NAME_QUERIES:
            log.info("Querying name=%s \u2026", query)
            results = await fetch_name_query(client, query)
            new_count = 0
            for part in results:
                pid = part.get("id")
                if pid not in seen_ids:
                    seen_ids.add(pid)
                    all_parts.append(part)
                    new_count += 1
            log.info("  name=%-12s \u2192 %4d results, %4d new (total so far: %d)",
                     query, len(results), new_count, len(all_parts))
            await asyncio.sleep(0.3)

    log.info("Harvest complete: %d unique MINI parts", len(all_parts))
    return all_parts


async def ensure_supplier(conn) -> str:
    row = await conn.fetchrow(
        "SELECT id FROM suppliers WHERE name = $1", SUPPLIER_NAME
    )
    if row:
        sid = str(row["id"])
        log.info("Supplier exists: %s", sid)
        return sid

    sid = str(uuid.uuid4())
    await conn.execute(
        "INSERT INTO suppliers(id,name,website,country,"
        "reliability_score,is_active,created_at,updated_at)"
        " VALUES($1,$2,$3,'IL',0.90,TRUE,NOW(),NOW())",
        sid, SUPPLIER_NAME, SUPPLIER_URL,
    )
    log.info("Created supplier: %s", sid)
    return sid


def normalize_part(raw: dict) -> dict | None:
    oem = (raw.get("item") or "").strip()
    if not oem:
        return None

    name_he = (raw.get("name") or "").strip()
    name_en = (raw.get("foreignName") or "").strip()
    is_orig_he = (raw.get("isOriginal") or "\u05de\u05e7\u05d5\u05e8\u05d9").strip()
    price = raw.get("priceWithTax") or 0.0
    model_desc = (raw.get("modelDescription") or "").strip()
    submit_date = raw.get("submitDate")

    part_type, aftermarket_tier = IS_ORIGINAL_MAP.get(is_orig_he, ("Original", None))

    sku = f"{SKU_PREFIX}-{oem}"
    sku = re.sub(r"[\s\-]+", "_", sku)

    return {
        "oem_number": oem,
        "sku": sku,
        "name_he": name_he,
        "name_en": name_en,
        "part_type": part_type,
        "aftermarket_tier": aftermarket_tier,
        "base_price": float(price),
        "model_description": model_desc,
        "submit_date": submit_date,
        "raw_id": raw.get("id"),
        "provider": raw.get("provider"),
    }


async def upsert_part(conn, part: dict, supplier_id: str, dry_run: bool) -> str:
    oem = part["oem_number"]
    sku = part["sku"]
    name_he = part["name_he"]
    name_en = part["name_en"]
    part_type = part["part_type"]
    aftermarket_tier = part["aftermarket_tier"]
    base_price = part["base_price"]
    model_desc = part["model_description"]

    if dry_run:
        return "ins"

    existing = await conn.fetchrow(
        "SELECT id FROM parts_catalog WHERE sku = $1", sku
    )

    if existing:
        part_id = str(existing["id"])
        await conn.execute(
            """UPDATE parts_catalog
               SET name=$1, description=$2, part_type=$3, aftermarket_tier=$4,
                   updated_at=NOW()
               WHERE sku=$5""",
            name_he, name_en, part_type, aftermarket_tier, sku,
        )
        action = "upd"
    else:
        part_id = str(uuid.uuid4())
        await conn.execute(
            """INSERT INTO parts_catalog(
                id, sku, oem_number, name, description,
                part_type, aftermarket_tier,
                manufacturer_id, manufacturer,
                is_available, created_at, updated_at
            ) VALUES($1,$2,$3,$4,$5,$6,$7,$8,'MINI',TRUE,NOW(),NOW())""",
            part_id, sku, oem, name_he, name_en,
            part_type, aftermarket_tier, MINI_BRAND_ID,
        )
        action = "ins"

    sp_existing = await conn.fetchrow(
        "SELECT id FROM supplier_parts WHERE part_id=$1 AND supplier_id=$2",
        part_id, supplier_id,
    )
    if sp_existing:
        await conn.execute(
            """UPDATE supplier_parts
               SET price=$1, currency='ILS', in_stock=TRUE, updated_at=NOW()
               WHERE id=$2""",
            base_price, str(sp_existing["id"]),
        )
    else:
        await conn.execute(
            """INSERT INTO supplier_parts(
                id, part_id, supplier_id, supplier_sku,
                price, currency, in_stock, created_at, updated_at
            ) VALUES($1,$2,$3,$4,$5,'ILS',TRUE,NOW(),NOW())""",
            str(uuid.uuid4()), part_id, supplier_id, sku, base_price,
        )

    return action


async def run(dry_run: bool = False, limit: int = 0):
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(message)s",
        level=logging.INFO,
    )

    parts_raw = await harvest_mini_parts()
    if limit:
        parts_raw = parts_raw[:limit]
        log.info("Limited to %d parts", limit)

    parts = [p for raw in parts_raw if (p := normalize_part(raw)) is not None]
    log.info("Normalized: %d parts (dropped %d null-OEM)", len(parts), len(parts_raw) - len(parts))

    original_count = sum(1 for p in parts if p["part_type"] == "Original")
    aftermarket_count = sum(1 for p in parts if p["part_type"] == "Aftermarket")
    priced = sum(1 for p in parts if p["base_price"] > 0)
    log.info("  Original: %d | Aftermarket: %d | With price: %d",
             original_count, aftermarket_count, priced)

    if dry_run:
        log.info("DRY-RUN \u2014 no DB writes.")
        log.info("  Sample SKU: %s | %s | \u20aa%.2f",
                 parts[0]["sku"] if parts else "N/A",
                 parts[0]["name_he"] if parts else "",
                 parts[0]["base_price"] if parts else 0)
        return

    conn = await asyncpg.connect(DB_URL)
    try:
        supplier_id = await ensure_supplier(conn)
        inserted = updated = errors = 0
        t0 = datetime.utcnow()

        for i, part in enumerate(parts, 1):
            try:
                action = await upsert_part(conn, part, supplier_id, dry_run=False)
                if action == "ins":
                    inserted += 1
                elif action == "upd":
                    updated += 1
            except Exception as e:
                errors += 1
                log.error("Error on part %s: %s", part.get("sku"), e)
                if errors > 20:
                    log.error("Too many errors, aborting")
                    break

            if i % 100 == 0:
                elapsed = (datetime.utcnow() - t0).total_seconds()
                rate = i / elapsed if elapsed > 0 else 0
                log.info("Progress %d/%d | ins=%d upd=%d err=%d | %.0f/s",
                         i, len(parts), inserted, updated, errors, rate)

        elapsed = (datetime.utcnow() - t0).total_seconds()
        log.info("DONE MINI | inserted=%d updated=%d errors=%d | %.1fs",
                 inserted, updated, errors, elapsed)

        row = await conn.fetchrow(
            "SELECT COUNT(*) FROM parts_catalog WHERE manufacturer='MINI'"
        )
        log.info("  DB count MINI: %d", row[0])

    finally:
        await conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Delek Motors MINI parts import")
    parser.add_argument("--dry-run", action="store_true", help="Don't write to DB")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of parts")
    args = parser.parse_args()
    asyncio.run(run(dry_run=args.dry_run, limit=args.limit))
