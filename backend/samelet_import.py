#!/usr/bin/env python3
"""
Samelet.com parts catalog importer for Stellantis/other brands.
Scrapes the samelet.com/api parts-prices endpoint for 9 brands and inserts into parts_catalog.

Brands: Alfa Romeo, Jeep, Fiat, RAM, Subaru, Abarth, Iveco, Hongqi, WEY
"""
import asyncio
import asyncpg
import requests
import time
import json
import re
import sys
from collections import defaultdict

DB_URL = "postgresql://autospare:e4b79d75ca640dbe7f259618f078b82f21573e419308f668beed5e20b26b1d43@postgres_catalog:5432/autospare"

BRANDS = [
    ("alfaromeo",  "Alfa Romeo", "Italy"),
    ("jeep",       "Jeep",       "USA"),
    ("fiat",       "Fiat",       "Italy"),
    ("ram",        "RAM",        "USA"),
    ("subaru",     "Subaru",     "Japan"),
    ("abarth",     "Abarth",     "Italy"),
    ("iveco",      "Iveco",      "Italy"),
    ("hongqi",     "Hongqi",     "China"),
    ("wey",        "WEY",        "China"),
]

API_CAP = 29
HEBREW_CHARS = list("אבגדהוזחטיכלמנסעפצקרשת" + "ךםןףץ")
ENGLISH_UPPER = list("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
ENGLISH_LOWER = list("abcdefghijklmnopqrstuvwxyz")
DIGITS = list("0123456789")

CATEGORY_MAP = {
    "filter": "Filters", "oil filter": "Filters", "air filter": "Filters",
    "fuel filter": "Filters", "brake": "Brakes", "disc": "Brakes",
    "pad": "Brakes", "caliper": "Brakes", "oil": "Engine", "spark": "Engine",
    "engine": "Engine", "camshaft": "Engine", "crankshaft": "Engine",
    "timing": "Engine", "piston": "Engine", "valve": "Engine",
    "belt": "Engine", "chain": "Engine", "gasket": "Engine",
    "seal": "Engine", "pump": "Engine", "sensor": "Electronics",
    "ecu": "Electronics", "module": "Electronics", "control": "Electronics",
    "airbag": "Safety", "seatbelt": "Safety", "abs": "Safety",
    "suspension": "Suspension", "shock": "Suspension", "strut": "Suspension",
    "spring": "Suspension", "arm": "Suspension", "bearing": "Suspension",
    "steering": "Steering", "exhaust": "Exhaust", "muffler": "Exhaust",
    "catalytic": "Exhaust", "radiator": "Cooling", "coolant": "Cooling",
    "thermostat": "Cooling", "fan": "Cooling", "transmission": "Transmission",
    "clutch": "Transmission", "gearbox": "Transmission", "axle": "Drivetrain",
    "drive": "Drivetrain", "driveshaft": "Drivetrain", "diff": "Drivetrain",
    "light": "Lighting", "lamp": "Lighting", "headlight": "Lighting",
    "bulb": "Lighting", "mirror": "Body", "door": "Body", "bumper": "Body",
    "hood": "Body", "fender": "Body", "windshield": "Body", "window": "Body",
    "glass": "Body", "wiper": "Body", "panel": "Body", "fuel": "Fuel System",
    "injector": "Fuel System", "battery": "Electrical", "alternator": "Electrical",
    "starter": "Electrical", "fuse": "Electrical", "relay": "Electrical",
    "cable": "Electrical", "wheel": "Wheels & Tires", "tire": "Wheels & Tires",
    "rim": "Wheels & Tires", "interior": "Interior", "seat": "Interior",
    "carpet": "Interior", "hvac": "HVAC", "ac": "HVAC",
    "air condition": "HVAC", "compressor": "HVAC", "hose": "Engine",
    "pipe": "Engine", "tool": "Tools & Accessories", "kit": "Maintenance",
}

def classify_part(en_desc: str, he_desc: str) -> str:
    text = (en_desc + " " + he_desc).lower()
    for kw, cat in CATEGORY_MAP.items():
        if kw in text:
            return cat
    return "General Parts"

def get_token(slug: str) -> str:
    try:
        r = requests.get(f"https://samelet.com/form/parts-prices/{slug}", timeout=15)
        m = re.search(r'name="token"\s+type="hidden"\s+value="([a-f0-9]+)"', r.text)
        if m:
            return m.group(1)
    except Exception as e:
        print(f"  [WARN] Could not fetch token for {slug}: {e}")
    return ""

def search_parts(slug: str, token: str, query: str, search_option: str = "2") -> list:
    try:
        r = requests.post(
            "https://samelet.com/api",
            data={
                "site": slug,
                "tag": "parts-prices",
                "part_search": query,
                "part_search_options": search_option,
                "token": token,
                "page_name": "מחירון חלפים",
            },
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": f"https://samelet.com/form/parts-prices/{slug}",
            },
            timeout=15,
        )
        d = r.json()
        if d.get("success") != 1:
            return []
        parts = d.get("parts", [])
        if isinstance(parts, dict):
            parts = [parts]
        return parts if isinstance(parts, list) else []
    except Exception as e:
        print(f"    [WARN] search error for '{query}': {e}")
        return []

def enumerate_all_parts(slug: str, token: str) -> dict:
    all_parts = {}
    seen_queries = set()

    def do_search(query: str, option: str, depth: int = 0):
        if query in seen_queries or depth > 3:
            return
        seen_queries.add(query)
        parts = search_parts(slug, token, query, option)
        for p in parts:
            mid = p.get("Material", "")
            if mid and mid not in all_parts:
                all_parts[mid] = p
        time.sleep(0.25)
        if len(parts) >= API_CAP and depth < 3:
            extensions = HEBREW_CHARS + ENGLISH_UPPER + ENGLISH_LOWER + DIGITS
            for ext in extensions:
                do_search(query + ext, option, depth + 1)

    print(f"  Phase 1: Single-char description searches...")
    for ch in HEBREW_CHARS + ENGLISH_UPPER + ENGLISH_LOWER:
        do_search(ch, "2", depth=0)
    print(f"  Phase 2: Single-digit SKU searches...")
    for d in DIGITS:
        do_search(d, "1", depth=0)
    print(f"  Phase 3: 2-char English prefix searches...")
    common_prefixes = [
        "Br", "Fr", "Re", "En", "Mo", "Se", "Su", "Pa", "Tr", "Co",
        "Fi", "Fl", "Dr", "Sp", "St", "Ba", "Bu", "Cl", "Gr", "Gu",
        "Ha", "He", "In", "Ki", "Le", "Li", "Lo", "Lu", "Ma", "Mi",
        "Na", "Nu", "Oi", "Op", "Or", "Ou", "Pl", "Po", "Pr", "Pu",
        "Qu", "Ro", "Ru", "Sa", "Sc", "Sh", "Si", "Sk", "Sl", "Sm",
        "Sn", "So", "Sq", "Sw", "Ta", "Te", "Ti", "To", "Tu", "Ty",
        "Va", "Ve", "Vi", "Wa", "We", "Wi", "Wo", "Ye",
    ]
    for prefix in common_prefixes:
        if prefix not in seen_queries:
            do_search(prefix, "2", depth=1)
    return all_parts

async def ensure_brands(conn):
    missing = [("Abarth", "Italy"), ("RAM", "USA")]
    for name, country in missing:
        exists = await conn.fetchval(
            "SELECT 1 FROM car_brands WHERE LOWER(name) = LOWER($1)", name
        )
        if not exists:
            await conn.execute(
                """INSERT INTO car_brands (id, name, country, is_active)
                   VALUES (gen_random_uuid(), $1, $2, TRUE)
                   ON CONFLICT DO NOTHING""",
                name, country,
            )
            print(f"  Created car_brand: {name}")
        else:
            print(f"  car_brand already exists: {name}")

async def import_brand(conn, slug: str, brand_name: str):
    print(f"\n{'='*60}")
    print(f"Importing: {brand_name} (slug={slug})")
    print(f"{'='*60}")
    token = get_token(slug)
    if not token:
        print(f"  [ERROR] No token found for {slug}, skipping.")
        return 0
    print(f"  Token: {token[:10]}...")
    parts_dict = enumerate_all_parts(slug, token)
    if not parts_dict:
        print(f"  [WARN] No parts found for {brand_name}")
        return 0
    print(f"  Enumerated {len(parts_dict)} unique parts, inserting...")
    inserted = 0
    updated = 0
    errors = 0
    for material_id, p in parts_dict.items():
        try:
            sku = material_id.lstrip("0") or material_id
            name_en = p.get("MatDescEn", "").strip()
            name_he = p.get("MatDescHe", "").strip()
            name = name_en or name_he or sku
            price_no_vat_str = p.get("PriceNoVat", "0") or "0"
            price_with_vat_str = p.get("PriceWithVat", "0") or "0"
            try:
                base_price = float(price_no_vat_str)
                importer_price = float(price_with_vat_str)
            except (ValueError, TypeError):
                base_price = 0.0
                importer_price = 0.0
            category = classify_part(name_en, name_he)
            mat_type = p.get("MaterialType", "01")
            part_type = "original" if mat_type == "01" else "aftermarket"
            result = await conn.fetchrow(
                """INSERT INTO parts_catalog
                   (id, sku, name, name_he, category, manufacturer, part_type,
                    base_price, importer_price_ils, oem_number, is_active,
                    created_at, updated_at)
                   VALUES (gen_random_uuid(), $1, $2, $3, $4, $5, $6,
                           $7, $8, $9, TRUE, NOW(), NOW())
                   ON CONFLICT (sku) DO UPDATE SET
                     name = EXCLUDED.name,
                     name_he = EXCLUDED.name_he,
                     category = EXCLUDED.category,
                     base_price = EXCLUDED.base_price,
                     importer_price_ils = EXCLUDED.importer_price_ils,
                     part_type = EXCLUDED.part_type,
                     updated_at = NOW()
                   RETURNING (xmax = 0) AS is_insert""",
                sku, name, name_he, category, brand_name, part_type,
                base_price, importer_price, material_id,
            )
            if result and result["is_insert"]:
                inserted += 1
            else:
                updated += 1
        except Exception as e:
            errors += 1
            if errors <= 5:
                print(f"  [ERROR] row {material_id}: {e}")
    print(f"  {brand_name}: inserted={inserted}, updated={updated}, errors={errors}")
    return inserted + updated

async def main():
    print("Samelet Catalog Importer starting...")
    conn = await asyncpg.connect(DB_URL)
    try:
        print("\nChecking car_brands...")
        await ensure_brands(conn)
        total = 0
        brand_counts = {}
        for slug, brand_name, _country in BRANDS:
            count = await import_brand(conn, slug, brand_name)
            brand_counts[brand_name] = count
            total += count
            time.sleep(2)
        print(f"\n{'='*60}")
        print("IMPORT COMPLETE")
        print(f"{'='*60}")
        for brand, cnt in brand_counts.items():
            print(f"  {brand}: {cnt} parts")
        print(f"  TOTAL: {total} parts")
    finally:
        await conn.close()

if __name__ == "__main__":
    asyncio.run(main())
