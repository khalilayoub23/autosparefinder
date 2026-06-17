#!/usr/bin/env python3
"""
RockAuto browser-scraped parts importer.

Reads JSON exported from the browser scraper (window.raAllParts) and imports
into parts_catalog as Daewoo (or Rover) parts.

Input: /tmp/rockauto_parts.json
Format: [{"brand": "FAMOUS BRAND", "partNum": "NAD1321", "price": 5.44,
          "year": "2002", "model": "lanos", "subcat": "brake pad", "fitment": "..."}, ...]

Run inside backend container:
  python3 /app/rockauto_browser_import.py --make Daewoo --file /tmp/rockauto_daewoo.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys

import asyncpg

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

DB_DSN = (
    "postgresql://autospare:e4b79d75ca640dbe7f259618f078b82f21573e419308f668beed5e20b26b1d43"
    "@postgres_catalog:5432/autospare"
)

USD_TO_ILS = 3.72

# Map RockAuto sub-category names → DB category slugs
SUBCAT_MAP: dict[str, str] = {
    "brake pad": "brakes",
    "brake shoe": "brakes",
    "brake disc": "brakes",
    "rotor": "brakes",
    "drum": "brakes",
    "caliper": "brakes",
    "brake hose": "brakes",
    "brake bleeder": "brakes",
    "brake fluid": "brakes",
    "wheel cylinder": "brakes",
    "master cylinder": "brakes",
    "wheel hub": "brakes",
    "wheel bearing": "suspension-steering",
    "hub assembly": "suspension-steering",
    "abs": "brakes",
    "tie rod": "suspension-steering",
    "ball joint": "suspension-steering",
    "control arm": "suspension-steering",
    "shock": "suspension-steering",
    "strut": "suspension-steering",
    "spring": "suspension-steering",
    "sway bar": "suspension-steering",
    "bushing": "suspension-steering",
    "cv axle": "gearbox",
    "cv boot": "gearbox",
    "rack and pinion": "suspension-steering",
    "power steering": "suspension-steering",
    "steering": "suspension-steering",
    "engine mount": "engine",
    "timing belt": "belts-chains",
    "timing chain": "belts-chains",
    "timing cover": "engine",
    "valve cover": "engine",
    "valve stem": "engine",
    "camshaft": "engine",
    "crankshaft": "engine",
    "cylinder head": "engine",
    "piston": "engine",
    "gasket": "engine",
    "oil pan": "engine",
    "oil pump": "engine",
    "oil filter": "filters",
    "air filter": "filters",
    "fuel filter": "filters",
    "cabin filter": "filters",
    "filter": "filters",
    "spark plug": "electrical-sensors",
    "spark plug wire": "electrical-sensors",
    "ignition coil": "electrical-sensors",
    "ignition": "electrical-sensors",
    "alternator": "electrical-sensors",
    "starter": "electrical-sensors",
    "battery": "electrical-sensors",
    "sensor": "electrical-sensors",
    "switch": "electrical-sensors",
    "relay": "electrical-sensors",
    "fuse": "electrical-sensors",
    "connector": "electrical-sensors",
    "wiring": "electrical-sensors",
    "radiator": "cooling",
    "thermostat": "cooling",
    "water pump": "cooling",
    "coolant": "cooling",
    "cooling": "cooling",
    "belt tensioner": "belts-chains",
    "idler pulley": "belts-chains",
    "serpentine belt": "belts-chains",
    "belt": "belts-chains",
    "exhaust": "exhaust",
    "muffler": "exhaust",
    "catalytic": "exhaust",
    "transmission": "gearbox",
    "gearbox": "gearbox",
    "clutch": "gearbox",
    "differential": "gearbox",
    "driveshaft": "gearbox",
    "fuel pump": "fuel-air",
    "fuel injector": "fuel-air",
    "throttle": "fuel-air",
    "fuel rail": "fuel-air",
    "evap": "fuel-air",
    "vapor canister": "fuel-air",
    "egr": "fuel-air",
    "air intake": "fuel-air",
    "oxygen": "electrical-sensors",
    "o2 sensor": "electrical-sensors",
    "lamp": "lighting",
    "light": "lighting",
    "bulb": "lighting",
    "headlamp": "lighting",
    "tail lamp": "lighting",
    "fog": "lighting",
    "bumper": "body-exterior",
    "door": "body-exterior",
    "window": "body-exterior",
    "mirror": "body-exterior",
    "hood": "body-exterior",
    "fender": "body-exterior",
    "grille": "body-exterior",
    "wiper": "body-exterior",
    "ac ": "air-conditioning-heating",
    "a/c": "air-conditioning-heating",
    "heater": "air-conditioning-heating",
    "blower": "air-conditioning-heating",
    "compressor": "air-conditioning-heating",
    "seat": "interior",
    "floor mat": "interior",
    "speaker": "interior",
    "antenna": "interior",
}


def map_category(subcat: str) -> str:
    s = subcat.lower()
    for kw, cat in SUBCAT_MAP.items():
        if kw in s:
            return cat
    return "accessories"


def clean_brand(brand: str) -> str:
    """Normalize RockAuto brand names."""
    b = brand.strip().upper()
    # "FAMOUS BRAND" is RockAuto's private label — use as-is
    if "FAMOUS BRAND" in b:
        return "Famous Brand"
    return brand.strip().title()


def make_sku(make: str, part_num: str, brand: str) -> str:
    pn = re.sub(r"[^A-Z0-9]", "-", part_num.upper())
    br = re.sub(r"[^A-Z0-9]", "", brand.upper())[:6]
    sku = f"RA-{make[:3].upper()}-{br}-{pn}"
    return sku[:100]


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--make", default="Daewoo", help="Manufacturer name (must exist in car_brands)")
    parser.add_argument("--file", default="/tmp/rockauto_daewoo.json", help="Path to JSON file")
    args = parser.parse_args()

    try:
        with open(args.file) as f:
            raw_parts: list[dict] = json.load(f)
    except FileNotFoundError:
        log.error("File not found: %s", args.file)
        sys.exit(1)
    except json.JSONDecodeError as e:
        log.error("JSON parse error: %s", e)
        sys.exit(1)

    log.info("Loaded %d raw parts from %s", len(raw_parts), args.file)

    # Deduplicate by SKU
    seen_skus: set[str] = set()
    parts: list[dict] = []
    for p in raw_parts:
        part_num = (p.get("partNum") or "").strip()
        brand = (p.get("brand") or "Unknown").strip()
        if not part_num or not brand:
            continue
        price_usd = float(p.get("price") or 0)
        if price_usd <= 0:
            continue
        sku = make_sku(args.make, part_num, brand)
        if sku not in seen_skus:
            seen_skus.add(sku)
            parts.append({
                "sku": sku,
                "part_num": part_num,
                "brand": clean_brand(brand),
                "price_usd": price_usd,
                "price_ils": round(price_usd * USD_TO_ILS, 2),
                "year": p.get("year", ""),
                "model": p.get("model", "").replace("+", " ").title(),
                "subcat": p.get("subcat", ""),
                "fitment": p.get("fitment", ""),
                "category": map_category(p.get("subcat", "")),
            })

    log.info("Unique parts after dedup: %d", len(parts))

    conn = await asyncpg.connect(DB_DSN)
    try:
        brand_id = await conn.fetchval(
            "SELECT id::text FROM car_brands WHERE lower(name)=$1 AND is_active=TRUE LIMIT 1",
            args.make.lower(),
        )
        if not brand_id:
            log.error("Brand %r not found in car_brands — aborting", args.make)
            return
        log.info("%s brand ID: %s", args.make, brand_id)

        inserted = updated = skipped = 0

        for p in parts:
            price_ex_vat = round(p["price_ils"] / 1.17, 2)
            name = f"{p['brand']} {p['part_num']}"
            if p["model"]:
                name = f"{args.make} {p['model']} - {name}"
            name = name[:255]

            description = (
                f"{p['subcat'].replace('+', ' ').title()}. "
                f"Brand: {p['brand']}. Part: {p['part_num']}. "
                + (f"Fitment: {p['fitment']}. " if p["fitment"] else "")
                + (f"Year: {p['year']}, Model: {p['model']}. " if p["year"] else "")
                + f"Price USD: ${p['price_usd']:.2f}. Source: RockAuto."
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
                            name             = EXCLUDED.name,
                            updated_at       = NOW()
                        RETURNING xmax
                        """,
                        p["sku"], p["part_num"][:100], name, args.make, brand_id,
                        p["category"], description, p["price_ils"], price_ex_vat,
                    )
                    if row:
                        if row["xmax"] == 0:
                            inserted += 1
                        else:
                            updated += 1
            except Exception as e:
                log.warning("Failed %s: %s", p["sku"], e)
                skipped += 1

        db_total = await conn.fetchval(
            "SELECT COUNT(*) FROM parts_catalog WHERE manufacturer=$1 AND is_active=TRUE",
            args.make,
        )
        log.info(
            "Done: inserted=%d updated=%d skipped=%d | DB total %s=%d",
            inserted, updated, skipped, args.make, db_total,
        )
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
