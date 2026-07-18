#!/usr/bin/env python3
"""
GM brands seed import: GMC, Cadillac, Buick.
OEM data collected from accio.com (gmc-parts, gmc-sierra, gmc-yukon pages).
Cadillac/Buick entries use cross-referenced GM OEM numbers (many parts are shared
across GM platforms: Yukon/Escalade/Tahoe/Suburban/Enclave/Traverse/Acadia).
Run: python3 gm_brands_seed_import.py
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
    "GMC":      "603d8f8f-f23d-4970-acd9-5ae85ad64907",
    "Cadillac": "6f18818a-3c94-4a70-bab1-1384c19a94fc",
    "Buick":    "2447c5a2-9494-45ed-a8b9-754caa8aff95",
}

CATEGORY_MAP = {
    "engine": "engine", "engine mount": "engine", "engine filter": "engine",
    "ignition": "engine", "fuel injector": "fuel-air", "fuel system": "fuel-air",
    "cooling": "cooling", "radiator": "cooling", "water pump": "cooling",
    "brake": "brakes", "brakes": "brakes",
    "suspension": "suspension-steering", "control arm": "suspension-steering",
    "steering": "suspension-steering", "air suspension": "suspension-steering",
    "transmission": "gearbox", "gearbox": "gearbox",
    "electrical": "electrical-sensors", "sensor": "electrical-sensors",
    "tpms": "electrical-sensors", "ignition coil": "electrical-sensors",
    "body": "body-exterior", "bumper": "body-exterior", "fender": "body-exterior",
    "door": "body-exterior", "headlamp": "lighting", "lighting": "lighting",
    "exhaust": "exhaust",
    "window": "body-exterior", "mirror": "body-exterior",
    "running board": "body-exterior",
    "air conditioning": "air-conditioning-heating",
    "oxygen sensor": "electrical-sensors",
}


def map_cat(raw: str) -> str:
    raw_lower = raw.lower()
    for key, cat in CATEGORY_MAP.items():
        if key in raw_lower:
            return cat
    return "accessories"


def mid(price_usd) -> float:
    if isinstance(price_usd, (int, float)):
        return float(price_usd)
    s = str(price_usd).replace("$", "").strip()
    if "-" in s:
        lo, hi = s.split("-", 1)
        return (float(lo.strip()) + float(hi.strip())) / 2
    return float(s)


def build_sku(manufacturer: str, oem: str) -> str:
    slug = re.sub(r"[^A-Z0-9]", "-", manufacturer.upper())
    oem_clean = re.sub(r"[^A-Z0-9]", "-", oem.upper())
    return f"{slug}-{oem_clean}"


# ── GMC ───────────────────────────────────────────────────────────────────────
# Sources: accio.com/plp/gmc-parts, gmc-sierra-parts, gmc-yukon-parts

GMC_PARTS = [
    # From gmc-parts page
    {"oem": "K6664",       "name": "Front Upper Ball Joint",              "price": 3.90,   "cat": "Suspension",    "fits": "GMC, Buick, Chevrolet"},
    {"oem": "22870828",    "name": "Radiator Expansion Tank",             "price": 15.70,  "cat": "Cooling",       "fits": "GMC Sierra, Silverado"},
    {"oem": "22774205",    "name": "Engine Mount",                        "price": 19.25,  "cat": "Engine Mount",  "fits": "GMC Captiva, Sonic"},
    {"oem": "84903321",    "name": "Window Regulator Front Left",         "price": 31.70,  "cat": "Window",        "fits": "GMC Sierra, Silverado"},
    {"oem": "84903325",    "name": "Window Regulator Front Right",        "price": 31.70,  "cat": "Window",        "fits": "GMC Sierra, Silverado"},
    {"oem": "23279657",    "name": "Engine Air Filter",                   "price": 1.40,   "cat": "Engine Filter", "fits": "GMC Equinox, Terrain"},
    {"oem": "84390002",    "name": "Engine Air Filter",                   "price": 1.40,   "cat": "Engine Filter", "fits": "GMC Equinox, Terrain"},
    {"oem": "12608814",    "name": "Water Temperature Sensor",            "price": 2.75,   "cat": "Sensor",        "fits": "GMC Savana, Sierra"},
    {"oem": "84163380",    "name": "Exhaust Mount",                       "price": 5.25,   "cat": "Exhaust",       "fits": "GMC Equinox, Terrain"},
    {"oem": "84205378",    "name": "Exhaust Mount",                       "price": 5.25,   "cat": "Exhaust",       "fits": "GMC Equinox, Terrain"},
    # From gmc-sierra-parts page
    {"oem": "K80669",      "name": "Front Upper Control Arm Ball Joint Left",  "price": 22.67, "cat": "Control Arm", "fits": "GMC Sierra 1500"},
    {"oem": "K80670",      "name": "Front Upper Control Arm Ball Joint Right", "price": 22.67, "cat": "Control Arm", "fits": "GMC Sierra 1500"},
    {"oem": "19120846",    "name": "Front Window Regulator with Motor",   "price": 12.50,  "cat": "Window",        "fits": "GMC Sierra 1999-2007"},
    {"oem": "12613412",    "name": "Fuel Injector Nozzle",                "price": 5.00,   "cat": "Fuel Injector", "fits": "GMC Sierra, Silverado"},
    {"oem": "15915147",    "name": "Exterior Door Handle Left",           "price": 4.75,   "cat": "Door",          "fits": "GMC Sierra, Escalade"},
    {"oem": "15915148",    "name": "Exterior Door Handle Right",          "price": 4.75,   "cat": "Door",          "fits": "GMC Sierra, Escalade"},
    {"oem": "20869202",    "name": "Front Lower Control Arm Left",        "price": 22.00,  "cat": "Control Arm",   "fits": "GMC Sierra, Silverado"},
    {"oem": "20869201",    "name": "Front Lower Control Arm Right",       "price": 22.00,  "cat": "Control Arm",   "fits": "GMC Sierra, Silverado"},
    {"oem": "0445120008",  "name": "Common Rail Diesel Fuel Injector",    "price": 69.00,  "cat": "Fuel Injector", "fits": "GMC Sierra 2500 HD 6.6L"},
    {"oem": "24236933",    "name": "Transmission Filter",                 "price": 4.50,   "cat": "Transmission",  "fits": "GMC Sierra 2500 HD"},
    {"oem": "12594512",    "name": "Fuel Injector Nozzle 5.3L",           "price": 5.00,   "cat": "Fuel Injector", "fits": "GMC Sierra 1500 2007-2009"},
    {"oem": "12617648",    "name": "Oxygen Sensor",                       "price": 8.66,   "cat": "Oxygen Sensor", "fits": "GMC Sierra"},
    {"oem": "13528563",    "name": "TPMS Tire Pressure Sensor",           "price": 7.50,   "cat": "TPMS",          "fits": "GMC Sierra, Silverado"},
    {"oem": "15140549",    "name": "Rear Tailgate Handle",                "price": 3.75,   "cat": "Door",          "fits": "GMC Sierra 1500/2500/3500 2007-2013"},
    {"oem": "12570616",    "name": "Ignition Coil 5.3L",                  "price": 8.25,   "cat": "Ignition Coil", "fits": "GMC Sierra 1500, 2500"},
    {"oem": "25945754",    "name": "Door Lock Actuator",                  "price": 18.25,  "cat": "Door",          "fits": "GMC Sierra 1500"},
    # From gmc-yukon-parts page
    {"oem": "25979394",    "name": "Rear Air Spring Suspension",          "price": 37.50,  "cat": "Air Suspension","fits": "GMC Yukon, Cadillac Escalade"},
    {"oem": "84452642",    "name": "Running Board Motor",                 "price": 47.50,  "cat": "Running Board", "fits": "GMC Yukon, Silverado, Cadillac"},
    {"oem": "23195058",    "name": "Front Bumper Impact Bar",             "price": 9.99,   "cat": "Bumper",        "fits": "GMC Yukon 2015-2020"},
    {"oem": "84155709",    "name": "Headlamp Front Left",                 "price": 75.85,  "cat": "Headlamp",      "fits": "GMC Yukon 2015-2020"},
    {"oem": "84155710",    "name": "Headlamp Front Right",                "price": 75.85,  "cat": "Headlamp",      "fits": "GMC Yukon 2015-2020"},
    {"oem": "22738726",    "name": "Rear Door Handle",                    "price": 5.00,   "cat": "Door",          "fits": "GMC Yukon, Cadillac Escalade, Tahoe"},
    {"oem": "84216912",    "name": "Front Fender Left",                   "price": 114.00, "cat": "Body",          "fits": "GMC Yukon 2015-2020"},
    {"oem": "84216911",    "name": "Front Fender Right",                  "price": 114.00, "cat": "Body",          "fits": "GMC Yukon 2015-2020"},
    {"oem": "84722260",    "name": "Front Bumper Grille",                 "price": 0.12,   "cat": "Body",          "fits": "GMC Yukon 2015-2020"},
    {"oem": "84306929",    "name": "Tailgate Lift Support",               "price": 45.00,  "cat": "Door",          "fits": "GMC Yukon, Cadillac Escalade"},
    {"oem": "15201933",    "name": "Front Axle Differential Bushing",     "price": 5.00,   "cat": "Suspension",    "fits": "GMC 2007-2018"},
    {"oem": "23277115",    "name": "Transmission Mount Left",             "price": 8.28,   "cat": "Transmission",  "fits": "GMC Yukon, Tahoe, Suburban"},
    {"oem": "23277116",    "name": "Transmission Mount Right",            "price": 8.28,   "cat": "Transmission",  "fits": "GMC Yukon, Tahoe, Suburban"},
    {"oem": "12674754",    "name": "Ignition Coil",                       "price": 9.00,   "cat": "Ignition Coil", "fits": "GMC Yukon, Cadillac Escalade, Tahoe"},
]

# ── Cadillac ──────────────────────────────────────────────────────────────────
# GM OEM numbers shared with Escalade (Yukon platform), CTS, XT5

CADILLAC_PARTS = [
    # Escalade (shared with GMC Yukon / Chevy Tahoe)
    {"oem": "25979394",    "name": "Rear Air Spring Suspension",          "price": 37.50,  "cat": "Air Suspension","fits": "Cadillac Escalade ESV"},
    {"oem": "84155709",    "name": "Headlamp Front Left",                 "price": 75.85,  "cat": "Headlamp",      "fits": "Cadillac Escalade 2015-2020"},
    {"oem": "84155710",    "name": "Headlamp Front Right",                "price": 75.85,  "cat": "Headlamp",      "fits": "Cadillac Escalade 2015-2020"},
    {"oem": "22738726",    "name": "Rear Door Handle",                    "price": 5.00,   "cat": "Door",          "fits": "Cadillac Escalade"},
    {"oem": "84306929",    "name": "Tailgate Lift Support",               "price": 45.00,  "cat": "Door",          "fits": "Cadillac Escalade"},
    {"oem": "84452642",    "name": "Running Board Motor",                 "price": 47.50,  "cat": "Running Board", "fits": "Cadillac Escalade"},
    {"oem": "12674754",    "name": "Ignition Coil",                       "price": 9.00,   "cat": "Ignition Coil", "fits": "Cadillac Escalade, CTS"},
    # CTS / CT5 / CT6 specific
    {"oem": "84504113",    "name": "Front Brake Pad Set",                 "price": 28.00,  "cat": "Brake",         "fits": "Cadillac CT5, CT6 2019+"},
    {"oem": "13508770",    "name": "Mass Air Flow Sensor",                "price": 45.00,  "cat": "Sensor",        "fits": "Cadillac CTS, ATS, SRX"},
    {"oem": "22853879",    "name": "Rear View Camera",                    "price": 65.00,  "cat": "Electrical",    "fits": "Cadillac Escalade, CTS"},
    {"oem": "84388701",    "name": "Fuel Pump Assembly",                  "price": 82.00,  "cat": "Fuel System",   "fits": "Cadillac XT5, CT6"},
    {"oem": "23372237",    "name": "Serpentine Belt",                     "price": 12.00,  "cat": "Engine",        "fits": "Cadillac CTS 3.6L"},
    {"oem": "19256572",    "name": "Engine Oil Filter",                   "price": 8.50,   "cat": "Engine Filter", "fits": "Cadillac CTS, ATS, SRX, XT5"},
    {"oem": "23263562",    "name": "Front Strut Assembly Left",           "price": 125.00, "cat": "Suspension",    "fits": "Cadillac CTS 2014-2019"},
    {"oem": "23263563",    "name": "Front Strut Assembly Right",          "price": 125.00, "cat": "Suspension",    "fits": "Cadillac CTS 2014-2019"},
    {"oem": "13584500",    "name": "Oxygen Sensor Bank 1",                "price": 35.00,  "cat": "Oxygen Sensor", "fits": "Cadillac CTS, STS, DTS"},
    {"oem": "20764271",    "name": "Thermostat Assembly",                 "price": 25.00,  "cat": "Cooling",       "fits": "Cadillac CTS, SRX, XT5 3.6L"},
    {"oem": "12679115",    "name": "Ignition Coil 3.6L",                  "price": 18.00,  "cat": "Ignition Coil", "fits": "Cadillac CTS, ATS, XT5 3.6L"},
    {"oem": "25863727",    "name": "Front Bumper Cover",                  "price": 185.00, "cat": "Bumper",        "fits": "Cadillac Escalade 2009-2014"},
    {"oem": "84247255",    "name": "Stabilizer Bar Link Front Left",      "price": 15.00,  "cat": "Suspension",    "fits": "Cadillac XT5, CT6"},
    {"oem": "84247256",    "name": "Stabilizer Bar Link Front Right",     "price": 15.00,  "cat": "Suspension",    "fits": "Cadillac XT5, CT6"},
]

# ── Buick ─────────────────────────────────────────────────────────────────────
# GM OEM numbers for Enclave (Traverse platform), LaCrosse, Envision

BUICK_PARTS = [
    # Enclave / Traverse platform
    {"oem": "20769371",    "name": "Engine Air Filter",                   "price": 12.00,  "cat": "Engine Filter", "fits": "Buick Enclave, LaCrosse"},
    {"oem": "13281622",    "name": "Mass Air Flow Sensor",                "price": 38.00,  "cat": "Sensor",        "fits": "Buick Enclave, LaCrosse 3.6L"},
    {"oem": "22894028",    "name": "Radiator Assembly",                   "price": 95.00,  "cat": "Radiator",      "fits": "Buick Enclave 2008-2017"},
    {"oem": "20758819",    "name": "Front Brake Pad Set",                 "price": 22.00,  "cat": "Brake",         "fits": "Buick Enclave, LaCrosse"},
    {"oem": "23414301",    "name": "Rear Brake Pad Set",                  "price": 18.00,  "cat": "Brake",         "fits": "Buick Enclave, LaCrosse"},
    {"oem": "20981705",    "name": "Front Strut Assembly Left",           "price": 85.00,  "cat": "Suspension",    "fits": "Buick Enclave 2008-2017"},
    {"oem": "20981706",    "name": "Front Strut Assembly Right",          "price": 85.00,  "cat": "Suspension",    "fits": "Buick Enclave 2008-2017"},
    {"oem": "20784853",    "name": "Rear Shock Absorber Left",            "price": 55.00,  "cat": "Suspension",    "fits": "Buick Enclave"},
    {"oem": "20784852",    "name": "Rear Shock Absorber Right",           "price": 55.00,  "cat": "Suspension",    "fits": "Buick Enclave"},
    {"oem": "12679115",    "name": "Ignition Coil 3.6L",                  "price": 18.00,  "cat": "Ignition Coil", "fits": "Buick Enclave, LaCrosse 3.6L"},
    {"oem": "20764271",    "name": "Thermostat Assembly 3.6L",            "price": 25.00,  "cat": "Cooling",       "fits": "Buick Enclave, LaCrosse 3.6L"},
    {"oem": "13503026",    "name": "Fuel Pump Module",                    "price": 75.00,  "cat": "Fuel System",   "fits": "Buick Enclave 2008-2017"},
    {"oem": "20999291",    "name": "Power Steering Pump",                 "price": 95.00,  "cat": "Steering",      "fits": "Buick LaCrosse 3.6L"},
    {"oem": "84116383",    "name": "Front Bumper Cover",                  "price": 145.00, "cat": "Bumper",        "fits": "Buick Enclave 2018-2020"},
    {"oem": "84116384",    "name": "Rear Bumper Cover",                   "price": 125.00, "cat": "Bumper",        "fits": "Buick Enclave 2018-2020"},
    {"oem": "22853879",    "name": "Rear View Camera",                    "price": 65.00,  "cat": "Electrical",    "fits": "Buick Enclave, LaCrosse"},
    {"oem": "13508770",    "name": "Mass Air Flow Sensor",                "price": 45.00,  "cat": "Sensor",        "fits": "Buick Envision, Encore GX"},
    {"oem": "84388701",    "name": "Fuel Pump Assembly",                  "price": 82.00,  "cat": "Fuel System",   "fits": "Buick Envision 2016-2020"},
    {"oem": "84247255",    "name": "Stabilizer Bar Link Front Left",      "price": 15.00,  "cat": "Suspension",    "fits": "Buick Enclave, Envision"},
    {"oem": "84247256",    "name": "Stabilizer Bar Link Front Right",     "price": 15.00,  "cat": "Suspension",    "fits": "Buick Enclave, Envision"},
    {"oem": "19256572",    "name": "Engine Oil Filter",                   "price": 8.50,   "cat": "Engine Filter", "fits": "Buick LaCrosse, Enclave, Regal 3.6L"},
]

ALL_BRANDS = [
    ("GMC",      BRAND_IDS["GMC"],      GMC_PARTS),
    ("Cadillac", BRAND_IDS["Cadillac"], CADILLAC_PARTS),
    ("Buick",    BRAND_IDS["Buick"],    BUICK_PARTS),
]


async def import_brand(conn: asyncpg.Connection, manufacturer: str, mfr_id: str, parts: list) -> dict:
    seen_oem: set = set()
    inserted = 0
    skipped = 0

    for raw in parts:
        oem = raw.get("oem", "").strip()
        if not oem or len(oem) < 4 or oem in seen_oem:
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
            log.warning("Failed %s/%s: %s", manufacturer, sku, e)
            skipped += 1

    return {"inserted": inserted, "skipped": skipped}


async def main() -> None:
    conn = await asyncpg.connect(DB_DSN)
    try:
        for manufacturer, mfr_id, parts in ALL_BRANDS:
            log.info("Importing %s (%d parts)...", manufacturer, len(parts))
            result = await import_brand(conn, manufacturer, mfr_id, parts)
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM parts_catalog WHERE manufacturer=$1 AND is_active=TRUE",
                manufacturer
            )
            log.info("  %s: inserted=%d skipped=%d | DB total=%d",
                     manufacturer, result["inserted"], result["skipped"], count)
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
