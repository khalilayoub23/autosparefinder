#!/usr/bin/env python3
"""
Script:  importers/asap_import.py
Purpose: Import an ASAP Network brand load sheet (products and/or fitment) into
         the catalog.

Context: ASAP Network is an aggregator — ONE account unlocks many manufacturers'
         ACA-standard data. Brands are approved individually by the manufacturer;
         as of 2026-07-28 four are approved (Banks Power, Fox Factory,
         BDS Suspension, Adams Driveshaft).

         This importer did not exist until 2026-07-28, which is why nothing was
         ever imported even though brand approvals had been arriving since 07-26 —
         `routes/system.asap_collect` relays and stores the CSV but only spawns an
         importer "once importers/asap_import.py exists".

PRICING (verified against the real Fox Factory sheet, 2026-07-28):
         The product sheet DOES carry pricing — `list_price` and `map_price`,
         populated on 96.6% of rows. An earlier note claimed the price sheet was
         empty because the account's `price_level=0`; that was wrong. price_level=0
         means no DEALER/COST tier is exposed, not that there is no price.

         Because ASAP gives us MSRP/MAP and NOT our cost, we must not invent a
         margin off it. `list_price` is a CONSUMER reference price, so it maps to
         `max_price_ils` exactly like the IL importer consumer-price flow:
             cost  = list_price_ils / 1.18      (ex-VAT reference cost)
             base  = cost * 1.45                (45% margin — uniform policy)
             max   = list_price_ils             (consumer reference)
         USD→ILS uses the live rate (currency_rate.get_usd_to_ils_rate).

Process:
  1. Parse the CSV (products or fitment — detected from the header).
  2. Products → upsert parts_catalog + supplier_parts for the ASAP supplier.
  3. Fitment  → upsert part_vehicle_fitment for SKUs already in the catalog.

Data Modified: parts_catalog, supplier_parts, part_vehicle_fitment, parts_images
Data Sources:  asapnetwork.org data sheets (relayed by the owner's browser)
Last Updated:  2026-07-28
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import sys
from typing import Dict, List, Optional

import asyncpg

from category_map import categorize_on_ingest

# ONE warranty source of truth — resolve() returns (months, source);
# never hardcode a warranty or drop its provenance. See warranty_policy.py.
from warranty_policy import resolve as _warranty_resolve

DB = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")
SUPPLIER_NAME = "ASAP Network"
VAT = 0.18
MARGIN = 1.45

# Header signatures — a fitment sheet has make/model/year, a product sheet has a price.
_FITMENT_COLS = {"make", "model"}
_PRODUCT_COLS = {"list_price", "title"}


def _f(row: Dict[str, str], *names: str) -> str:
    for n in names:
        v = (row.get(n) or "").strip()
        if v:
            return v
    return ""


def _num(v: str) -> float:
    try:
        return float(str(v).replace(",", "").replace("$", "").strip())
    except Exception:
        return 0.0


async def _ensure_supplier(conn: asyncpg.Connection) -> str:
    row = await conn.fetchrow("SELECT id FROM suppliers WHERE name=$1", SUPPLIER_NAME)
    if row:
        return str(row["id"])
    return str(await conn.fetchval(
        """
        INSERT INTO suppliers (id, name, country, website, reliability_score,
                               is_active, priority, supports_express,
                               rate_limit_per_minute, is_manufacturer,
                               created_at, updated_at)
        VALUES (gen_random_uuid(), $1, 'US', 'https://asapnetwork.org', 0.9,
                TRUE, 5, FALSE, 60, FALSE, NOW(), NOW())
        RETURNING id
        """,
        SUPPLIER_NAME,
    ))


async def _ensure_brand(conn: asyncpg.Connection, name: str,
                        cache: Dict[str, str]) -> str:
    """
    Resolve a brand name → car_brands.id, creating the row if needed.

    `parts_catalog.manufacturer_id` is a NOT NULL FK to car_brands(id), and ASAP
    ships PARTS brands (Fox Factory, Banks Power, BDS Suspension) which are not
    vehicle makes — so they must be registered here or every insert fails with
    "null value in column manufacturer_id violates not-null constraint"
    (observed on the first real run, 2026-07-28).

    The lookup is CASE-INSENSITIVE on purpose: the unique index is
    ux_car_brands_name_ci_active on lower(btrim(name)), so a case-sensitive
    existence check would miss 'fox factory' and then fail to insert
    'Fox Factory' — exactly the duplicate-brand bug already in the mistake log.
    """
    key = (name or "").strip().lower()
    if not key:
        key, name = "unknown", "Unknown"
    if key in cache:
        return cache[key]

    row = await conn.fetchrow(
        "SELECT id FROM car_brands WHERE lower(btrim(name)) = $1 "
        "ORDER BY is_active DESC LIMIT 1", key)
    if row:
        cache[key] = str(row["id"])
        return cache[key]

    bid = await conn.fetchval(
        """
        INSERT INTO car_brands (id, name, is_active, created_at, updated_at)
        VALUES (gen_random_uuid(), $1, TRUE, NOW(), NOW())
        ON CONFLICT DO NOTHING
        RETURNING id
        """,
        name.strip()[:120],
    )
    if not bid:   # lost a race — re-read
        bid = await conn.fetchval(
            "SELECT id FROM car_brands WHERE lower(btrim(name)) = $1 LIMIT 1", key)
    cache[key] = str(bid)
    return cache[key]


async def import_products(conn: asyncpg.Connection, rows: List[Dict[str, str]],
                          brand_name: str, usd_ils: float) -> Dict[str, int]:
    supplier_id = await _ensure_supplier(conn)
    stats = {"inserted": 0, "updated": 0, "skipped": 0, "priced": 0, "images": 0}
    brand_cache: Dict[str, str] = {}

    for r in rows:
        sku = _f(r, "sku")
        title = _f(r, "title")
        if not sku or not title:
            stats["skipped"] += 1
            continue

        oem = _f(r, "mfg_original_sku", "internal_part_number") or None
        brand = _f(r, "brand", "brand_l", "sub_brand") or brand_name
        upc = _f(r, "upc") or None
        desc = _f(r, "description")[:4000] or None
        # 'Discontinued' rows stay in the catalog (searchable, per platform rule)
        # but must not be advertised as in stock.
        #
        # The ACA sheet carries this as `discontinued_item` ('true'/'false'); there
        # is NO `availability` column. Reading only `availability` therefore made
        # `in_stock` True for EVERY row and marked 566 discontinued parts (193 Banks
        # + 373 Fox) as buyable. Read the real column first, keep `availability` as
        # a fallback for sheets that do carry it.
        discontinued = _f(r, "discontinued_item").strip().lower() in ("true", "1", "yes", "y")
        avail = _f(r, "availability").lower()
        in_stock = (not discontinued) and avail not in (
            "discontinued", "out of stock", "unavailable", "0")

        list_usd = _num(_f(r, "list_price"))
        map_usd = _num(_f(r, "map_price"))
        consumer_usd = list_usd or map_usd

        # Consumer reference price → cost → our price. Same shape as the IL
        # importer flow. ASAP gives MSRP/MAP, never our cost, so we derive the
        # ex-VAT reference cost rather than inventing a margin off MSRP.
        if consumer_usd > 0:
            max_ils = round(consumer_usd * usd_ils, 2)
            cost_ils = round(max_ils / (1 + VAT), 2)
            base_ils = round(cost_ils * MARGIN, 2)
            stats["priced"] += 1
        else:
            max_ils = cost_ils = base_ils = 0.0

        category = categorize_on_ingest(name=title)
        manufacturer_id = await _ensure_brand(conn, brand, brand_cache)

        existing = await conn.fetchrow(
            "SELECT id FROM parts_catalog WHERE sku=$1 LIMIT 1", sku)

        try:
            async with conn.transaction():   # per-row savepoint
                if existing:
                    part_id = existing["id"]
                    await conn.execute(
                        """
                        UPDATE parts_catalog SET
                            name = $2,
                            description = COALESCE($3, description),
                            manufacturer = $4,
                            oem_number = COALESCE($5, oem_number),
                            barcode = COALESCE($6, barcode),
                            category = CASE WHEN category IN ('כללי','general','service-general','accessories')
                                            THEN $7 ELSE category END,
                            max_price_ils = CASE WHEN $8 > 0 THEN $8 ELSE max_price_ils END,
                            base_price    = CASE WHEN $9 > 0 THEN $9 ELSE base_price END,
                            importer_price_ils = CASE WHEN $10 > 0
                                                 THEN $10 ELSE importer_price_ils END,
                            is_active = TRUE,
                            updated_at = NOW()
                        WHERE id = $1
                        """,
                        part_id, title[:500], desc, brand[:120], oem, upc,
                        category, max_ils, base_ils, cost_ils,
                    )
                    stats["updated"] += 1
                else:
                    part_id = await conn.fetchval(
                        """
                        INSERT INTO parts_catalog
                            (id, sku, name, description, manufacturer, manufacturer_id,
                             oem_number, barcode, category, part_condition, part_type,
                             is_safety_critical, needs_oem_lookup, master_enriched,
                             specifications,
                             max_price_ils, base_price, importer_price_ils,
                             is_active, created_at, updated_at)
                        VALUES (gen_random_uuid(), $1, $2, $3, $4, $5::uuid, $6, $7, $8,
                                'new', 'aftermarket', FALSE, FALSE, FALSE, $12::jsonb,
                                $9, $10, $11, TRUE, NOW(), NOW())
                        RETURNING id
                        """,
                        sku, title[:500], desc, brand[:120], manufacturer_id,
                        oem, upc, category, max_ils, base_ils, cost_ils,
                        json.dumps({"source": "asap_network", "brand": brand,
                                    "sheet_brand": brand_name,
                                    "discontinued": discontinued}),
                    )
                    stats["inserted"] += 1

                if cost_ils > 0:
                    # price_usd is NOT NULL on supplier_parts. We have the real
                    # USD figure from the sheet, so store the ex-VAT USD cost
                    # rather than a placeholder.
                    cost_usd = round(consumer_usd / (1 + VAT), 2)
                    await conn.execute(
                        """
                        INSERT INTO supplier_parts
                            (id, part_id, supplier_id, supplier_sku, price_ils,
                             price_usd, is_available, warranty_months,
                             warranty_source, created_at, updated_at)
                        VALUES (gen_random_uuid(), $1, $2, $3, $4, $5, $6, $7, $8, NOW(), NOW())
                        ON CONFLICT ON CONSTRAINT supplier_parts_supplier_id_supplier_sku_key
                        DO UPDATE SET price_ils = EXCLUDED.price_ils,
                                      price_usd = EXCLUDED.price_usd,
                                      is_available = EXCLUDED.is_available,
                                      updated_at = NOW()
                        """,
                        part_id, supplier_id, sku, cost_ils, cost_usd, in_stock,
                        *_warranty_resolve(_f(r, 'warranty_months'),
                                           _f(r, 'warranty')),
                    )

                img = _f(r, "images").split("|")[0].split(",")[0].strip()
                if img.startswith("http"):
                    # Feeds the thumbnail pipeline (it scans parts lacking a
                    # part_thumbnails row). No unique index on (part_id,url) —
                    # NOT EXISTS guard, never ON CONFLICT. url is varchar → cast.
                    await conn.execute(
                        """
                        INSERT INTO parts_images (id, part_id, url, is_primary, created_at)
                        SELECT gen_random_uuid(), $1, $2::varchar, TRUE, NOW()
                        WHERE NOT EXISTS (
                            SELECT 1 FROM parts_images WHERE part_id=$1 AND url=$2::varchar)
                        """,
                        part_id, img[:1000],
                    )
                    stats["images"] += 1
        except Exception as exc:
            stats["skipped"] += 1
            print(f"  [row error] {sku}: {str(exc)[:150]}", flush=True)

    return stats


async def import_fitment(conn: asyncpg.Connection, rows: List[Dict[str, str]]) -> Dict[str, int]:
    stats = {"linked": 0, "no_part": 0, "skipped": 0}
    cache: Dict[str, Optional[str]] = {}

    for r in rows:
        sku = _f(r, "sku")
        make = _f(r, "make")
        model = _f(r, "model")
        if not sku or not make or not model:
            stats["skipped"] += 1
            continue

        if sku not in cache:
            row = await conn.fetchrow(
                "SELECT id FROM parts_catalog WHERE sku=$1 LIMIT 1", sku)
            cache[sku] = str(row["id"]) if row else None
        part_id = cache[sku]
        if not part_id:
            stats["no_part"] += 1
            continue

        y_from = int(_num(_f(r, "from", "year_from", "start_year")) or 0) or None
        y_to = int(_num(_f(r, "to", "year_to", "end_year")) or 0) or None
        # year_from is NOT NULL. Match car_parts_ie_import_generic and fall back
        # to 1990 rather than discarding an otherwise-valid fitment row.
        if y_from is None:
            y_from = y_to or 1990

        try:
            async with conn.transaction():
                await conn.execute(
                    """
                    -- Dedupe key is uix_pvf_part_mfr_model_year_from, i.e.
                    -- (part_id, manufacturer, model, year_from) — it does NOT
                    -- include year_to. A NOT EXISTS that also compared year_to
                    -- therefore let through rows that then violated the index
                    -- (1,518 of them on the first Fox run). Let the DB dedupe.
                    --
                    -- That key is a bare UNIQUE INDEX, not a table CONSTRAINT, so
                    -- `ON CONFLICT ON CONSTRAINT <name>` raises "constraint does
                    -- not exist" and every single row fails (8,210/8,210 on the
                    -- first Banks run). Unique indexes must be targeted by COLUMN
                    -- INFERENCE instead.
                    INSERT INTO part_vehicle_fitment
                        (id, part_id, manufacturer, model, year_from, year_to, created_at)
                    VALUES (gen_random_uuid(), $1::uuid, $2::varchar, $3::varchar,
                            $4::int, $5::int, NOW())
                    ON CONFLICT (part_id, manufacturer, model, year_from)
                    DO UPDATE SET year_to = GREATEST(
                        COALESCE(part_vehicle_fitment.year_to, 0),
                        COALESCE(EXCLUDED.year_to, 0))
                    """,
                    part_id, make[:100], model[:150], y_from, y_to,
                )
                stats["linked"] += 1
        except Exception as exc:
            stats["skipped"] += 1
            print(f"  [fitment error] {sku}: {str(exc)[:120]}", flush=True)

    return stats


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("csv_path")
    ap.add_argument("--brand-id", default="")
    ap.add_argument("--brand-name", default="")
    args = ap.parse_args()

    if not os.path.exists(args.csv_path):
        print(f"ERROR: {args.csv_path} not found", file=sys.stderr)
        sys.exit(1)

    with open(args.csv_path, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        print("[asap_import] empty CSV — nothing to do")
        return

    cols = {c.lower() for c in rows[0].keys()}
    is_fitment = _FITMENT_COLS.issubset(cols) and "list_price" not in cols
    kind = "FITMENT" if is_fitment else "PRODUCTS"
    print(f"[asap_import] {args.brand_name or args.brand_id}: {len(rows):,} rows ({kind})",
          flush=True)

    conn = await asyncpg.connect(DB, statement_cache_size=0)
    try:
        if is_fitment:
            stats = await import_fitment(conn, rows)
        else:
            try:
                from currency_rate import get_usd_to_ils_rate
                usd_ils = await get_usd_to_ils_rate()
            except Exception:
                usd_ils = float(os.getenv("ILS_PER_USD", "3.72"))
            print(f"[asap_import] USD→ILS = {usd_ils}", flush=True)
            stats = await import_products(conn, rows, args.brand_name, usd_ils)
        print(f"[asap_import] DONE {kind}: {stats}", flush=True)
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
