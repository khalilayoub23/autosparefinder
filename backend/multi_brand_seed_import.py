#!/usr/bin/env python3
"""
Multi-brand seed import for Daihatsu and SsangYong.
OEM data collected from accio.com.
Run: python3 multi_brand_seed_import.py
"""
from __future__ import annotations
import asyncio, logging, re
import asyncpg

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

DB_DSN = (
    "postgresql://autospare:e4b79d75ca640dbe7f259618f078b82f21573e419308f668beed5e20b26b1d43"
    "@postgres_catalog:5432/autospare"
)
USD_TO_ILS = 3.65

BRAND_IDS = {
    "Daihatsu": "873dbe55-4602-4c5f-a713-f2cbe7b9feb7",
    "SsangYong": "588b0288-fb17-499e-83a8-750e3be2d318",
}

CATEGORY_MAP = {
    "engine": "engine", "engine components": "engine", "engine support": "engine",
    "engine exhaust": "engine",
    "fuel system": "fuel-air", "fuel injection": "fuel-air", "fuel injectors": "fuel-air",
    "filters": "engine", "engine filters": "engine", "air filters": "engine",
    "cooling": "cooling", "cooling systems": "cooling",
    "braking system": "brakes", "brake systems": "brakes",
    "suspension": "suspension-steering", "steering systems": "suspension-steering",
    "steering": "suspension-steering",
    "transmission": "gearbox",
    "electrical": "electrical-sensors", "electronics": "electrical-sensors",
    "sensors": "electrical-sensors", "electrical components": "electrical-sensors",
    "body": "body-exterior",
    "turbocharging": "engine",
    "air conditioning": "air-conditioning-heating", "climate control": "air-conditioning-heating",
    "window components": "body-exterior",
    "interior accessories": "interior",
    "bearings": "wheels-bearings",
    "gaskets & seals": "engine", "gaskets": "engine",
    "belts & pulleys": "belts-chains",
}


def map_cat(raw: str) -> str:
    return CATEGORY_MAP.get(raw.lower().strip(), "accessories")


def clean_oem(raw: str) -> str:
    if not raw or raw.strip() in ("N/A", "n/a", ""):
        return ""
    nums = re.split(r"[,/]", raw)
    first = nums[0].strip()
    first = re.sub(r"\s+", "", first)
    return first if len(first) >= 4 else ""


def build_sku(manufacturer: str, oem: str) -> str:
    slug = re.sub(r"[^A-Z0-9]", "-", manufacturer.upper())
    oem_clean = re.sub(r"[^A-Z0-9]", "-", oem.upper())
    return f"{slug}-{oem_clean}"


def mid(price_usd) -> float:
    """Parse price field which may be a float, int, or '$x-y' string."""
    if isinstance(price_usd, (int, float)):
        return float(price_usd)
    s = str(price_usd).replace("$", "").strip()
    if "-" in s:
        lo, hi = s.split("-", 1)
        return (float(lo.strip()) + float(hi.strip())) / 2
    return float(s)


# ── Daihatsu ─────────────────────────────────────────────────────────────────

DAIHATSU_PARTS = [
    {"oem": "11792-B1020",   "name": "Crankshaft Washer",                    "price": 5.00,   "cat": "Engine",          "fits": "Materia M401/M402/M412"},
    {"oem": "47550-B1010",   "name": "Brake Slave Pump",                     "price": 65.00,  "cat": "Braking System",  "fits": "Sirion 2005, Materia"},
    {"oem": "04906-B1030",   "name": "Rear Cylinder Brake Cup Kit",           "price": 7.00,   "cat": "Braking System",  "fits": "Sirion M300/M301/M311/M303"},
    {"oem": "12373-8720",    "name": "Engine Mount",                          "price": 3.60,   "cat": "Engine Support",  "fits": "Feroza, Applause"},
    {"oem": "12305-BZ080",   "name": "Rubber Engine Mount",                  "price": 10.74,  "cat": "Engine Support",  "fits": "Sirion"},
    {"oem": "12305-87209",   "name": "Rubber Engine Mounting",               "price": 8.00,   "cat": "Engine Support",  "fits": "Daihatsu"},
    {"oem": "12361-B4010",   "name": "Engine Mount",                         "price": 6.15,   "cat": "Engine Support",  "fits": "Terios 2006-2014"},
    {"oem": "12305-97210",   "name": "Front Engine Mount",                   "price": 5.99,   "cat": "Engine Support",  "fits": "Storia, Sirion"},
    {"oem": "12362-87401",   "name": "Front Right Engine Motor Mount",       "price": 4.50,   "cat": "Engine Support",  "fits": "Daihatsu"},
    {"oem": "48609-BZ020",   "name": "Front Shock Absorber Strut Mount",     "price": 4.50,   "cat": "Suspension",      "fits": "Daihatsu 2005-2006"},
    {"oem": "13101-87216",   "name": "Engine Piston Set EF Engine",          "price": 7.64,   "cat": "Engine",          "fits": "Daihatsu EF engine"},
    {"oem": "12305-B1010",   "name": "Engine Mount",                         "price": 6.75,   "cat": "Engine Support",  "fits": "Sirion 2008+"},
    {"oem": "11701-B1021",   "name": "Engine Parts Assembly",                "price": 6.89,   "cat": "Engine",          "fits": "Sirion"},
    {"oem": "67005-B4110",   "name": "Panel Sub-Assembly Back Door",         "price": 138.00, "cat": "Body",            "fits": "Terios J200/J210/J211"},
]

# ── SsangYong ────────────────────────────────────────────────────────────────

SSANGYONG_PARTS = [
    {"oem": "2521225010",    "name": "Multi Wedge Belt 6PK2160",             "price": 8.15,   "cat": "Belts & Pulleys", "fits": "SsangYong 2.0"},
    {"oem": "4650009003",    "name": "Steering Rack",                        "price": 115.00, "cat": "Steering",        "fits": "SsangYong"},
    {"oem": "2073008C00",    "name": "Front Engine Motor Mount",             "price": 15.00,  "cat": "Engine Support",  "fits": "Rexton 2006-2012"},
    {"oem": "6711803009",    "name": "Engine Oil Filter",                    "price": 0.92,   "cat": "Engine Filters",  "fits": "SsangYong"},
    {"oem": "7231109003",    "name": "Window Regulator Wheel",               "price": 1.54,   "cat": "Window Components","fits": "Actyon Sports, Kyron"},
    {"oem": "6715420017",    "name": "Temperature MAP Sensor Assembly",      "price": 5.52,   "cat": "Sensors",         "fits": "Rodius, Stavic, Actyon, Rexton, Korando"},
    {"oem": "6650160000",    "name": "Cylinder Head Engine Gasket",          "price": 30.00,  "cat": "Gaskets & Seals", "fits": "Actyon, Kyron, Rexton, Korando, Musso, Rodius, Tivoli"},
    {"oem": "8573008001",    "name": "Door Contact Switch",                  "price": 5.05,   "cat": "Electrical Components", "fits": "Rexton, Kyron, Stavic"},
    {"oem": "6711800701",    "name": "Engine Oil Pump",                      "price": 85.50,  "cat": "Engine",          "fits": "Korando C"},
    {"oem": "6651305011",    "name": "AC Compressor",                        "price": 80.00,  "cat": "Air Conditioning", "fits": "Rexton 2.7L"},
    {"oem": "EJBR04601D",    "name": "Diesel Fuel Injector Nozzle",          "price": 75.00,  "cat": "Fuel Injection",  "fits": "SsangYong, Rexton, Kyron"},
    {"oem": "6652300011",    "name": "AC Compressor 10PA17C 6PK",           "price": 75.00,  "cat": "Air Conditioning", "fits": "Rexton"},
    {"oem": "4450109100",    "name": "Front Lower Control Arm",              "price": 8.47,   "cat": "Suspension",      "fits": "Rexton, Korando"},
    {"oem": "6711400460",    "name": "EGR Valve",                            "price": 73.95,  "cat": "Engine Exhaust",  "fits": "SsangYong"},
    {"oem": "46500-0900D",   "name": "Hydraulic Steering Rack",              "price": 89.50,  "cat": "Steering Systems", "fits": "SsangYong"},
    {"oem": "49189-07131",   "name": "Turbo Core Cartridge",                 "price": 49.00,  "cat": "Turbocharging",   "fits": "Rexton D27DT"},
    {"oem": "4450209100",    "name": "Front Lower Control Arm Right",        "price": 34.20,  "cat": "Suspension",      "fits": "Rexton, Korando"},
    {"oem": "6714603280",    "name": "Power Steering Pump",                  "price": 47.00,  "cat": "Steering Systems", "fits": "Rexton"},
    {"oem": "6650170221",    "name": "Fuel Injector Nozzle EJBR04501D",     "price": 6.00,   "cat": "Fuel Injection",  "fits": "Kyron, Actyon"},
    {"oem": "6650943048",    "name": "Mass Air Flow Sensor MAF",             "price": 12.00,  "cat": "Sensors",         "fits": "SsangYong"},
]


ALL_BRANDS = [
    ("Daihatsu", DAIHATSU_PARTS),
    ("SsangYong", SSANGYONG_PARTS),
]


async def import_brand(conn: asyncpg.Connection, manufacturer: str, parts: list) -> dict:
    mfr_id = BRAND_IDS[manufacturer]
    seen_oem: set = set()
    inserted = 0
    skipped = 0

    for raw in parts:
        oem = clean_oem(str(raw.get("oem", "")))
        if not oem or oem in seen_oem:
            skipped += 1
            continue
        seen_oem.add(oem)

        sku = build_sku(manufacturer, oem)
        name = raw["name"][:255]
        price_ils = round(mid(raw["price"]) * USD_TO_ILS, 2)
        category = map_cat(raw.get("cat", ""))
        desc = f"{name}. Fits: {raw.get('fits', manufacturer)}."

        try:
            async with conn.transaction():
                part_id = await conn.fetchval("""
                    INSERT INTO parts_catalog(
                        id, sku, oem_number, name, manufacturer, manufacturer_id,
                        category, description, specifications,
                        online_price_ils, min_price_ils, max_price_ils,
                        part_type, is_safety_critical, needs_oem_lookup,
                        master_enriched, is_active, created_at, updated_at
                    ) VALUES (
                        gen_random_uuid(), $1, $2, $3, $4, $5::uuid,
                        $6, $7, '{}'::jsonb,
                        $8, $8, $9,
                        'original', FALSE, FALSE,
                        FALSE, TRUE, NOW(), NOW()
                    )
                    ON CONFLICT (sku) DO UPDATE SET
                        name = EXCLUDED.name,
                        online_price_ils = EXCLUDED.online_price_ils,
                        min_price_ils = EXCLUDED.min_price_ils,
                        updated_at = NOW()
                    RETURNING id
                """, sku, oem, name, manufacturer, mfr_id,
                     category, desc, price_ils, round(price_ils * 1.2, 2))
                if part_id:
                    inserted += 1
        except Exception as e:
            log.warning("Failed %s: %s", sku, e)
            skipped += 1

    return {"inserted": inserted, "skipped": skipped}


async def main() -> None:
    conn = await asyncpg.connect(DB_DSN)
    try:
        for manufacturer, parts in ALL_BRANDS:
            log.info("Importing %s (%d parts)...", manufacturer, len(parts))
            result = await import_brand(conn, manufacturer, parts)
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM parts_catalog WHERE manufacturer=$1 AND is_active=TRUE",
                manufacturer
            )
            log.info("  %s: inserted=%d skipped=%d | DB total=%d", manufacturer, result["inserted"], result["skipped"], count)
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
