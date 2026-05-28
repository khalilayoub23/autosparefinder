"""
SNG Barratt Jaguar Parts Importer
===================================
Imports parts from /opt/autosparefinder/jaguar_parts_raw.ndjson into
the autospare PostgreSQL catalog (parts_catalog + supplier_parts).

Usage:
  python sng_barratt_jaguar_import.py
  python sng_barratt_jaguar_import.py --dry-run
  python sng_barratt_jaguar_import.py --limit 1000
  python sng_barratt_jaguar_import.py --skip-fitment

Environment:
  DATABASE_URL   Postgres URL (auto-read from container env)
  GBP_TO_ILS     GBP→ILS conversion rate (default: 4.73)
  VAT_RATE       Israeli VAT (default: 0.18)

Pre-requisites:
  - Run sng_barratt_jaguar_scraper.py first to produce jaguar_parts_raw.ndjson
  - Jaguar car_brand must exist in car_brands table
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path

import asyncpg

INPUT_FILE = Path("/opt/autosparefinder/jaguar_parts_raw.ndjson")
LOGS_DIR = Path("/opt/autosparefinder/logs")

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://autospare:e4b79d75ca640dbe7f259618f078b82f21573e419308f668beed5e20b26b1d43"
    "@postgres_catalog:5432/autospare",
)
DB_DSN = DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")

JAGUAR_BRAND_ID = "fde0f2dc-c6fb-4ab6-b699-765044fbc073"
SUPPLIER_NAME = "SNG Barratt"
SUPPLIER_URL = "https://www.sngbarratt.com"

GBP_TO_ILS = float(os.getenv("GBP_TO_ILS", "4.73"))
VAT_RATE = float(os.getenv("VAT_RATE", "0.18"))
BATCH_SIZE = 25

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(str(LOGS_DIR / "jaguar_import.log")),
    ],
)
log = logging.getLogger("jaguar_import")

JAGUAR_MODEL_YEARS: dict[str, tuple[int, int]] = {
    "E-Type": (1961, 1975), "XKE": (1961, 1975), "XK": (1948, 2014),
    "XK8": (1996, 2006), "XKR": (1996, 2006), "XJS": (1976, 1996),
    "XJ40": (1986, 1994), "XJ6": (1968, 1997), "XJ8": (1998, 2010),
    "XJ": (1968, 2019), "XF": (2008, 2099), "XE": (2015, 2099),
    "F-Type": (2013, 2099), "F-Pace": (2016, 2099), "E-Pace": (2017, 2099),
    "I-Pace": (2018, 2099), "S-Type": (1999, 2008), "X-Type": (2001, 2009),
    "Daimler": (1945, 2005), "Mk II": (1959, 1969), "Mk 2": (1959, 1969),
}


def get_year_range_for_model(model_name: str) -> tuple[int, int]:
    upper = model_name.upper()
    for key, (y_from, y_to) in JAGUAR_MODEL_YEARS.items():
        if key.upper() in upper:
            return (y_from, y_to)
    return (1950, 2099)


def build_sku(part_number: str) -> str:
    clean = part_number.strip().replace("_", "-")
    return f"JAG-{clean}"


def map_part_type(type_name: str) -> str:
    t = (type_name or "").lower()
    if any(x in t for x in ("original", "oem", "genuine")):
        return "Original"
    return "Aftermarket"


def calculate_base_price_ils(price_gbp: float | None) -> float | None:
    if price_gbp is None or price_gbp <= 0:
        return None
    return round(price_gbp * GBP_TO_ILS * (1 + VAT_RATE), 2)


def calculate_supplier_price_usd(price_gbp: float | None) -> float:
    if price_gbp is None or price_gbp <= 0:
        return 0.0
    return round(price_gbp * 1.264, 2)


async def get_or_create_supplier(conn: asyncpg.Connection) -> str:
    row = await conn.fetchrow("SELECT id FROM suppliers WHERE name = $1", SUPPLIER_NAME)
    if row:
        sid = str(row["id"])
        log.info("Supplier '%s' exists: %s", SUPPLIER_NAME, sid)
        return sid
    new_id = str(uuid.uuid4())
    await conn.execute(
        """
        INSERT INTO suppliers (id, name, website, country, currency,
                               lead_time_days, reliability_score, is_active,
                               created_at, updated_at)
        VALUES ($1, $2, $3, 'UK', 'GBP', 14, 4.5, TRUE, NOW(), NOW())
        """,
        new_id, SUPPLIER_NAME, SUPPLIER_URL,
    )
    log.info("Created supplier '%s': %s", SUPPLIER_NAME, new_id)
    return new_id


async def import_batch(
    conn: asyncpg.Connection,
    supplier_id: str,
    batch: list[dict],
    skip_fitment: bool = False,
) -> tuple[int, int]:
    inserted = 0
    skipped = 0
    async with conn.transaction():
        for rec in batch:
            part_number = rec.get("part_number", "")
            sku = build_sku(part_number)
            title = (rec.get("title") or "").strip()
            if not title:
                skipped += 1
                continue
            description = " | ".join(filter(None, [
                rec.get("description", "").strip(),
                rec.get("sales_note", "").strip(),
            ]))
            part_type_str = map_part_type(rec.get("type_name", ""))
            aftermarket_tier = rec.get("aftermarket_tier")
            price_gbp = rec.get("price_gbp")
            base_price_ils = calculate_base_price_ils(price_gbp)
            oem_number = rec.get("base_part_number") or None
            applications = rec.get("applications") or []
            image_url = rec.get("image_url") or None
            stock_status = rec.get("stock_status", "unknown")

            row = await conn.fetchrow(
                """
                INSERT INTO parts_catalog (
                    id, sku, name, category, manufacturer, manufacturer_id,
                    part_type, description, oem_number, aftermarket_tier,
                    base_price, compatible_vehicles,
                    part_condition, is_active, needs_oem_lookup,
                    created_at, updated_at
                ) VALUES (
                    gen_random_uuid(), $1, $2, $3, 'Jaguar', $4,
                    $5, $6, $7, $8,
                    $9, $10::jsonb,
                    'New', TRUE, FALSE,
                    NOW(), NOW()
                )
                ON CONFLICT (sku) DO UPDATE SET
                    name              = EXCLUDED.name,
                    description       = EXCLUDED.description,
                    oem_number        = EXCLUDED.oem_number,
                    aftermarket_tier  = EXCLUDED.aftermarket_tier,
                    base_price        = EXCLUDED.base_price,
                    compatible_vehicles = EXCLUDED.compatible_vehicles,
                    updated_at        = NOW()
                RETURNING id, (xmax = 0) AS was_inserted
                """,
                sku, title, _guess_category(title, description), JAGUAR_BRAND_ID,
                part_type_str, description or None, oem_number, aftermarket_tier,
                base_price_ils, json.dumps(applications),
            )
            if row is None:
                skipped += 1
                continue
            part_id = str(row["id"])
            was_inserted = row["was_inserted"]
            if was_inserted:
                inserted += 1

            price_usd = calculate_supplier_price_usd(price_gbp)
            price_ils = round(float(price_gbp or 0) * GBP_TO_ILS, 2) if price_gbp else None
            is_available = stock_status in ("in_stock", "unknown")
            supplier_url = (
                f"https://www.sngbarratt.com/English/UK/Products/{rec.get('web_product_guid', '')}"
                if rec.get("web_product_guid") else SUPPLIER_URL
            )
            await conn.execute(
                """
                INSERT INTO supplier_parts (
                    id, supplier_id, part_id, supplier_sku,
                    price_usd, price_ils, availability, warranty_months,
                    estimated_delivery_days, is_available, supplier_url,
                    part_type, created_at, updated_at
                ) VALUES (
                    gen_random_uuid(), $1, $2, $3,
                    $4, $5, $6, 12,
                    21, $7, $8,
                    $9, NOW(), NOW()
                )
                ON CONFLICT (supplier_id, supplier_sku) DO UPDATE SET
                    price_usd     = EXCLUDED.price_usd,
                    price_ils     = EXCLUDED.price_ils,
                    is_available  = EXCLUDED.is_available,
                    availability  = EXCLUDED.availability,
                    updated_at    = NOW()
                """,
                supplier_id, part_id, part_number, price_usd, price_ils,
                "In Stock" if stock_status == "in_stock" else "Pre-order",
                is_available, supplier_url, part_type_str,
            )
            if not skip_fitment and applications:
                for app_model in applications[:20]:
                    app_model = app_model.strip()
                    if not app_model:
                        continue
                    y_from, y_to = get_year_range_for_model(app_model)
                    await conn.execute(
                        """
                        INSERT INTO part_vehicle_fitment (
                            id, part_id, manufacturer, manufacturer_id,
                            model, year_from, year_to, notes, created_at, updated_at
                        ) VALUES (
                            gen_random_uuid(), $1, 'Jaguar', $2,
                            $3, $4, $5, $6, NOW(), NOW()
                        )
                        ON CONFLICT (part_id, manufacturer, model, year_from) DO NOTHING
                        """,
                        part_id, JAGUAR_BRAND_ID, app_model, y_from,
                        y_to if y_to < 2090 else None,
                        "Imported from SNG Barratt catalogue",
                    )
    return inserted, skipped


def _guess_category(name: str, description: str) -> str:
    text = f"{name} {description}".lower()
    if any(w in text for w in ("brake", "disc", "pad", "caliper", "master cylinder")):
        return "בלמים"
    if any(w in text for w in ("engine", "piston", "valve", "gasket", "timing", "camshaft", "crankshaft", "oil seal")):
        return "מנוע"
    if any(w in text for w in ("gearbox", "clutch", "transmission", "gear")):
        return "תיבת הילוכים"
    if any(w in text for w in ("suspension", "spring", "shock", "absorber", "arm", "bush", "strut")):
        return "מתלה"
    if any(w in text for w in ("steering", "rack", "column", "tie rod", "wheel bearing")):
        return "היגוי"
    if any(w in text for w in ("cooling", "radiator", "fan", "thermostat", "coolant", "water pump")):
        return "קירור"
    if any(w in text for w in ("fuel", "injector", "carburetor", "pump", "filter element")):
        return "דלק"
    if any(w in text for w in ("electrical", "wiring", "sensor", "switch", "relay", "fuse", "lamp", "light")):
        return "חשמל"
    if any(w in text for w in ("body", "panel", "bumper", "door", "boot", "bonnet", "wing")):
        return "מרכב"
    if any(w in text for w in ("exhaust", "manifold", "silencer", "muffler")):
        return "פליטה"
    if any(w in text for w in ("interior", "carpet", "seat", "trim", "dashboard")):
        return "פנים הרכב"
    if any(w in text for w in ("rubber", "seal", "gasket", "o-ring")):
        return "אטמים"
    return "חלקי חילוף"


async def run_import(dry_run: bool = False, limit: int | None = None, skip_fitment: bool = False):
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    if not INPUT_FILE.exists():
        log.error("Input file not found: %s", INPUT_FILE)
        log.error("Run sng_barratt_jaguar_scraper.py first.")
        sys.exit(1)
    total_lines = sum(1 for _ in open(INPUT_FILE, encoding="utf-8"))
    if limit:
        total_lines = min(total_lines, limit)
    log.info("Input file: %s | %d parts to import", INPUT_FILE, total_lines)
    if dry_run:
        sample = []
        with open(INPUT_FILE, encoding="utf-8") as f:
            for i, line in enumerate(f):
                if limit and i >= limit:
                    break
                rec = json.loads(line)
                sample.append(rec)
        with_price = sum(1 for r in sample if r.get("price_gbp") and r["price_gbp"] > 0)
        in_stock = sum(1 for r in sample if r.get("stock_status") == "in_stock")
        types = {}
        for r in sample:
            t = r.get("type_name", "unknown")
            types[t] = types.get(t, 0) + 1
        brands = {}
        for r in sample:
            b = r.get("brand_name", "unknown")
            brands[b] = brands.get(b, 0) + 1
        log.info("DRY RUN STATS (%d records):", len(sample))
        log.info("  With price: %d / %d", with_price, len(sample))
        log.info("  In stock:   %d / %d", in_stock, len(sample))
        log.info("  Types:      %s", dict(sorted(types.items(), key=lambda x: -x[1])[:10]))
        log.info("  Brands:     %s", dict(sorted(brands.items(), key=lambda x: -x[1])[:10]))
        if sample:
            r = sample[0]
            price_ils = calculate_base_price_ils(r.get("price_gbp"))
            log.info("  Sample:     %s | %s | GBP %.2f \u2192 ILS %.2f",
                     r.get("title"), r.get("type_name"), r.get("price_gbp") or 0, price_ils or 0)
        return
    conn = await asyncpg.connect(DB_DSN)
    try:
        supplier_id = await get_or_create_supplier(conn)
        total_inserted = 0
        total_skipped = 0
        total_processed = 0
        batch: list[dict] = []
        start_time = datetime.utcnow()
        with open(INPUT_FILE, encoding="utf-8") as f:
            for line in f:
                if limit and total_processed >= limit:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                batch.append(rec)
                total_processed += 1
                if len(batch) >= BATCH_SIZE:
                    ins, skp = await import_batch(conn, supplier_id, batch, skip_fitment)
                    total_inserted += ins
                    total_skipped += skp
                    batch.clear()
                    if total_processed % 500 == 0:
                        elapsed = (datetime.utcnow() - start_time).total_seconds()
                        pct = (total_processed / total_lines) * 100 if total_lines else 0
                        rate = total_processed / elapsed if elapsed > 0 else 0
                        log.info(
                            "Progress: %d/%d (%.1f%%) | inserted=%d skipped=%d | %.0f parts/s",
                            total_processed, total_lines, pct, total_inserted, total_skipped, rate,
                        )
        if batch:
            ins, skp = await import_batch(conn, supplier_id, batch, skip_fitment)
            total_inserted += ins
            total_skipped += skp
        elapsed = (datetime.utcnow() - start_time).total_seconds()
        log.info(
            "Import complete | processed=%d inserted=%d skipped=%d | %.1fs",
            total_processed, total_inserted, total_skipped, elapsed,
        )
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM parts_catalog WHERE manufacturer = 'Jaguar' AND is_active"
        )
        log.info("Jaguar parts in catalog: %d", count)
    finally:
        await conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SNG Barratt Jaguar parts importer")
    parser.add_argument("--dry-run", action="store_true", help="Show stats only, no DB writes")
    parser.add_argument("--limit", type=int, default=None, help="Import only first N parts")
    parser.add_argument("--skip-fitment", action="store_true", help="Skip part_vehicle_fitment inserts")
    args = parser.parse_args()
    asyncio.run(run_import(dry_run=args.dry_run, limit=args.limit, skip_fitment=args.skip_fitment))
