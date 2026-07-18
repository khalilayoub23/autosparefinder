#!/usr/bin/env python3
"""
freesbe_importer.py
Import IL prices from the freesbe.com open Strapi API (admin.freesbe.com).
177K parts from Israeli importer covering Renault/Dacia (RE-), Nissan/Infiniti (NI-),
Chery (CH-), Xpeng (XP-), JAC/JAECOO (JM-), and others.

Strategy:
  1. Page through all parts via pagination
  2. For each part: strip the brand prefix to get raw OEM
  3. Try matching DB with and without prefix
  4. Update max_price_ils for matches
  5. Insert new Dacia/Renault parts for unmatched RE- OEMs

Price fields:
  - price      = ILS incl. VAT (used as max_price_ils)
  - priceWithoutVat = ILS excl. VAT (stored as cost reference)
"""
import asyncio
import asyncpg
import httpx
import json
import os
import re
import sys
import uuid
from datetime import datetime

sys.path.insert(0, '/app')

DB_URL = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")
API_BASE = "https://admin.freesbe.com/api/parts"
PAGE_SIZE = 100
CONCURRENCY = 4
PROGRESS_FILE = "/app/state/freesbe_import_progress.json"

# Map freesbe prefix → our DB manufacturer name(s) for new-part insertion
PREFIX_TO_MANUFACTURER = {
    "RE": "Renault",    # RE- parts serve Renault + Dacia (same group parts)
    "NI": "Nissan",     # NI- serves Nissan + Infiniti
    "CH": "Chery",
    "XP": "Xpeng",
    "JM": "JAC",
}


def load_progress():
    try:
        with open(PROGRESS_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"completed_pages": [], "updated": 0, "inserted": 0, "not_found": 0}


def save_progress(progress):
    os.makedirs(os.path.dirname(PROGRESS_FILE), exist_ok=True)
    with open(PROGRESS_FILE, "w") as f:
        json.dump(progress, f)


def parse_part(item):
    """Extract fields from a Strapi part item."""
    attrs = item["attributes"]
    part_id = attrs["partId"]  # e.g. "RE-7701053319"
    if "-" not in part_id:
        return None
    prefix, raw_oem = part_id.split("-", 1)
    raw_oem = raw_oem.strip()
    if not raw_oem:
        return None
    price_str = attrs.get("price")
    try:
        price_ils = float(price_str) if price_str else None
    except (ValueError, TypeError):
        price_ils = None
    if not price_ils or price_ils <= 0:
        return None
    return {
        "part_id": part_id,
        "prefix": prefix.upper(),
        "raw_oem": raw_oem,
        "price_ils": price_ils,
        "description": attrs.get("description", ""),
        "is_original": attrs.get("isOriginal", True),
        "is_available": attrs.get("isAvailable", True),
    }


def normalize_oem(oem: str) -> str:
    """Strip all non-alphanumeric chars to match idx_parts_oem_normalized."""
    return re.sub(r'[^A-Z0-9]', '', oem.upper())


async def get_brand_id(conn, manufacturer: str) -> str | None:
    """Get car_brands.id for a manufacturer name."""
    row = await conn.fetchrow(
        "SELECT id FROM car_brands WHERE lower(name) = lower($1) AND is_active = TRUE LIMIT 1",
        manufacturer,
    )
    return str(row["id"]) if row else None


async def process_page(conn, parts: list, stats: dict, brand_id_cache: dict,
                       lock: asyncio.Lock | None = None):
    """Match and update/insert a batch of parts.

    ``conn`` must be a dedicated connection (from a pool), not shared.
    ``lock`` (optional) serializes writes to shared ``stats`` and ``brand_id_cache``.
    """
    if not parts:
        return

    # Build normalized OEM lookup keys for this batch
    lookup_map = {}  # norm_key -> [part dict, ...]
    for p in parts:
        raw = p["raw_oem"]
        prefix = p["prefix"]
        keys = [
            normalize_oem(raw),           # "7701053319"
            normalize_oem(prefix + raw),  # "RE7701053319"
        ]
        for k in keys:
            if k not in lookup_map:
                lookup_map[k] = []
            lookup_map[k].append(p)

    all_keys = list(lookup_map.keys())

    # Batch DB lookup — uses idx_parts_oem_normalized (partial index on is_active=true)
    db_rows = await conn.fetch(
        """
        SELECT id, oem_number, manufacturer, importer_price_ils, base_price
        FROM parts_catalog
        WHERE regexp_replace(upper(COALESCE(oem_number, '')), '[^A-Z0-9]', '', 'g') = ANY($1::text[])
          AND is_active = TRUE
        """,
        all_keys,
    )

    matched_oems = set()
    for db_row in db_rows:
        norm = normalize_oem(db_row["oem_number"] or "")
        source_parts = lookup_map.get(norm, [])
        for sp in source_parts:
            matched_oems.add(sp["raw_oem"])
            new_price = sp["price_ils"]
            old_price = db_row["importer_price_ils"]
            # Skip if existing importer price is already higher (prefer fresher/higher data)
            if old_price and old_price > 0 and old_price > new_price * 2:
                continue
            await conn.execute(
                """UPDATE parts_catalog
                   SET importer_price_ils = $1, updated_at = NOW()
                   WHERE id = $2""",
                new_price, db_row["id"],
            )
            stats["updated"] += 1

    # Insert unmatched RE- parts as new Renault parts (Dacia uses same Renault OEMs)
    for p in parts:
        if p["raw_oem"] in matched_oems:
            continue
        if p["prefix"] not in ("RE",):
            stats["not_found"] += 1
            continue
        manufacturer = PREFIX_TO_MANUFACTURER[p["prefix"]]
        if manufacturer not in brand_id_cache:
            brand_id_cache[manufacturer] = await get_brand_id(conn, manufacturer)
        brand_id = brand_id_cache.get(manufacturer)
        if not brand_id:
            stats["not_found"] += 1
            continue
        new_id = str(uuid.uuid4())
        name = p["description"] or p["raw_oem"]
        price_inc_vat = p["price_ils"]
        # base_price = cost (ex-VAT) × 1.45 margin per pricing policy
        price_ex_vat = round(price_inc_vat / 1.18, 2)
        base_price = round(price_ex_vat * 1.45, 2)
        sku = f"RE-{p['raw_oem'].lstrip('0') or p['raw_oem']}"
        await conn.execute(
            """
            INSERT INTO parts_catalog (
                id, sku, oem_number, name, manufacturer, manufacturer_id,
                importer_price_ils, base_price, is_active,
                master_enriched, needs_oem_lookup, created_at, updated_at
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,TRUE,FALSE,FALSE,NOW(),NOW())
            ON CONFLICT (sku) DO UPDATE SET
                importer_price_ils = EXCLUDED.importer_price_ils,
                updated_at = NOW()
            """,
            new_id, sku, p["raw_oem"], name, manufacturer, brand_id,
            price_inc_vat, base_price,
        )
        stats["inserted"] += 1


async def fetch_page(client: httpx.AsyncClient, page: int) -> dict | None:
    url = f"{API_BASE}?pagination[page]={page}&pagination[pageSize]={PAGE_SIZE}"
    for attempt in range(3):
        try:
            resp = await client.get(url, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            if attempt == 2:
                print(f"  ERROR page {page}: {e}")
                return None
            await asyncio.sleep(2 ** attempt)


async def main():
    print("=== Freesbe Importer ===")
    print(f"API: {API_BASE}")

    progress = load_progress()
    completed = set(progress["completed_pages"])
    stats = {
        "updated": progress["updated"],
        "inserted": progress["inserted"],
        "not_found": progress["not_found"],
    }

    # Get total pages
    async with httpx.AsyncClient() as client:
        first = await fetch_page(client, 1)
        if not first:
            print("Failed to fetch first page, aborting.")
            return
        total_pages = first["meta"]["pagination"]["pageCount"]
        print(f"Total parts: {first['meta']['pagination']['total']}, Pages: {total_pages}")

    # Single connection — DB writes are sequential (safe); HTTP fetches run concurrently
    conn = await asyncpg.connect(DB_URL)
    brand_id_cache: dict = {}
    http_sem = asyncio.Semaphore(CONCURRENCY)   # limit concurrent HTTP fetches
    db_lock = asyncio.Lock()                     # serialize DB writes on single connection

    fetched: dict[int, list] = {}  # page -> parts list, populated by HTTP coroutines

    async def fetch_one(page: int):
        if page in completed:
            return
        async with http_sem:
            async with httpx.AsyncClient() as client:
                data = await fetch_page(client, page)
        if data:
            raw_parts = [parse_part(item) for item in data.get("data", [])]
            fetched[page] = [p for p in raw_parts if p]

    # Process pages in batches: fetch concurrently, then write serially
    pages = [p for p in range(1, total_pages + 1) if p not in completed]
    print(f"Pages to process: {len(pages)} (skipping {len(completed)} already done)")

    BATCH = CONCURRENCY * 5
    for i in range(0, len(pages), BATCH):
        batch = pages[i : i + BATCH]
        fetched.clear()
        await asyncio.gather(*[fetch_one(p) for p in batch])
        # Write fetched pages serially
        for page in batch:
            if page not in fetched:
                continue
            async with db_lock:
                await process_page(conn, fetched[page], stats, brand_id_cache)
                completed.add(page)
                progress["completed_pages"] = list(completed)
                progress["updated"] = stats["updated"]
                progress["inserted"] = stats["inserted"]
                progress["not_found"] = stats["not_found"]
                if page % 50 == 0 or page == total_pages:
                    save_progress(progress)
                    print(
                        f"  Page {page}/{total_pages} | updated={stats['updated']} "
                        f"inserted={stats['inserted']} not_found={stats['not_found']}"
                    )

    # Normalize base_price for parts that now have importer_price_ils but no base_price
    print("\nNormalizing base_price for newly priced parts...")
    result = await conn.fetch(
        """
        UPDATE parts_catalog
        SET base_price = ROUND((importer_price_ils / 1.18) * 1.45, 2), updated_at = NOW()
        WHERE importer_price_ils > 0
          AND (base_price IS NULL OR base_price = 0)
          AND is_active = TRUE
        RETURNING id
        """
    )
    print(f"base_price normalized for {len(result)} parts")

    # Queue pipeline todos per skills.md POST-IMPORT PIPELINE
    print("\nQueuing pipeline todos...")
    await conn.execute(
        """
        INSERT INTO agent_todos(id, assigned_to_agent, title, description, priority, status, artifacts, created_at, updated_at)
        VALUES
        (gen_random_uuid(), 'db_update_agent', $1, $2, 'high', 'not_started',
         '{"task_names": ["normalize_categories", "fix_base_prices", "normalize_base_price", "fill_car_brands"]}'::jsonb,
         NOW(), NOW()),
        (gen_random_uuid(), 'db_update_agent', $3, $4, 'high', 'not_started',
         '{"task_names": ["enrich_pending_parts", "normalize_imported_manufacturers", "sync_models_from_catalog"]}'::jsonb,
         NOW(), NOW())
        """,
        "Freesbe import: normalize + categorize new parts",
        f"Freesbe import complete: {stats['updated']} updated importer_price_ils, {stats['inserted']} new Renault/Dacia parts inserted. Run normalization + category pass.",
        "Freesbe import: enrich new Renault/Dacia parts",
        f"Freesbe {stats['inserted']} new parts need enrichment: manufacturer sync, model matching, AI enrichment (master_enriched=FALSE).",
    )
    print("Pipeline todos queued.")

    await conn.close()
    save_progress(progress)
    print(f"\n=== Done ===")
    print(f"Updated (importer_price_ils): {stats['updated']}")
    print(f"Inserted (new Renault parts): {stats['inserted']}")
    print(f"Not found:                    {stats['not_found']}")


if __name__ == "__main__":
    asyncio.run(main())
