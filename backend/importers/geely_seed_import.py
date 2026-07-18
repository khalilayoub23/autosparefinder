#!/usr/bin/env python3
"""
Geely seed import from OEM data collected via accio.com.
Prices are wholesale USD midpoints. ils = usd * 3.65.
Run: python3 geely_seed_import.py
"""
from __future__ import annotations
import asyncio, logging, re, uuid
import asyncpg

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

DB_DSN = (
    "postgresql://autospare:e4b79d75ca640dbe7f259618f078b82f21573e419308f668beed5e20b26b1d43"
    "@postgres_catalog:5432/autospare"
)
USD_TO_ILS = 3.65

BRAND_IDS = {
    "Geely": "daf5256f-bfb9-415c-8540-f7b5e6643870",
}

CATEGORY_MAP = {
    "engine": "engine", "engine systems": "engine", "engine components": "engine",
    "engine control": "electrical-sensors", "engine parts": "engine", "engine assembly": "engine",
    "fuel": "fuel-air", "fuel system": "fuel-air",
    "cooling": "cooling", "cooling systems": "cooling",
    "brake": "brakes", "brake system": "brakes",
    "suspension": "suspension-steering", "steering": "suspension-steering",
    "transmission": "gearbox", "transmission systems": "gearbox", "drivetrain": "clutch-drivetrain",
    "electrical": "electrical-sensors", "auto electrical systems": "electrical-sensors",
    "sensors": "electrical-sensors",
    "body": "body-exterior", "auto body systems": "body-exterior", "body parts": "body-exterior",
    "body hardware": "body-exterior",
    "lighting": "lighting", "lighting accessories": "lighting",
    "interior systems": "interior",
    "safety components": "body-exterior",
    "wheels": "wheels-bearings",
    "hvac": "air-conditioning-heating",
    "multi-category": "accessories",
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


# ── Geely seed parts collected from accio.com ─────────────────────────────────

GEELY_PARTS = [
    # --- Coolray (Binyue / SX11) ---
    {"oem": "1017034809",   "name": "TPMS Tire Pressure Sensor",              "price_usd": 5.50,   "cat": "Electrical",  "fits": "Coolray, Binyue"},
    {"oem": "6010084800",   "name": "Front Bumper Lower Body Panel",           "price_usd": 40.00,  "cat": "Body Parts",  "fits": "Coolray"},
    {"oem": "2069033100",   "name": "Cooling Radiator",                        "price_usd": 42.50,  "cat": "Cooling",     "fits": "Coolray"},
    {"oem": "8022021100",   "name": "Air Conditioning Blower Motor",           "price_usd": 41.91,  "cat": "HVAC",        "fits": "Coolray, SX11"},
    {"oem": "7054015500",   "name": "Front Fog Lamp Left",                     "price_usd": 13.00,  "cat": "Lighting",    "fits": "Coolray"},
    {"oem": "2013021700",   "name": "Fuel Filter Assembly",                    "price_usd": 1.10,   "cat": "Fuel System", "fits": "Coolray, Binyue"},
    {"oem": "K0120529",     "name": "Timing Chain Component Kit",              "price_usd": 32.50,  "cat": "Engine",      "fits": "Coolray, SX11"},
    {"oem": "2043005800",   "name": "Starter Motor",                           "price_usd": 121.00, "cat": "Electrical",  "fits": "Coolray"},
    {"oem": "8010054000",   "name": "Air Conditioning Cabin Air Filter",       "price_usd": 93.50,  "cat": "HVAC",        "fits": "Coolray, SX11"},
    {"oem": "4050076500",   "name": "Rear Friction Plate Assembly",            "price_usd": 52.00,  "cat": "Brake System","fits": "Coolray"},
    {"oem": "1067000034",   "name": "Outdoor Ambient Temperature Sensor",      "price_usd": 3.25,   "cat": "Sensors",     "fits": "Coolray, Binyue"},
    {"oem": "6010082700",   "name": "Front Bumper Lower Grille",               "price_usd": 16.50,  "cat": "Body Parts",  "fits": "Coolray"},
    {"oem": "6010207200",   "name": "Lower Front Grille Assembly",             "price_usd": 52.50,  "cat": "Body Parts",  "fits": "Coolray"},
    {"oem": "4013078300",   "name": "Front Shock Absorber",                    "price_usd": 51.40,  "cat": "Suspension",  "fits": "Coolray, SX11"},
    {"oem": "6010208600",   "name": "Front Bumper Assembly",                   "price_usd": 13.75,  "cat": "Body Parts",  "fits": "Coolray"},
    {"oem": "5075038100C15","name": "Front Left Door Assembly",                "price_usd": 252.75, "cat": "Body Parts",  "fits": "Coolray"},
    {"oem": "5075038300C15","name": "Front Right Door Assembly",               "price_usd": 252.75, "cat": "Body Parts",  "fits": "Coolray"},
    {"oem": "7072004700",   "name": "High-Pitched Speaker",                    "price_usd": 7.50,   "cat": "Interior",    "fits": "Coolray, Okavango"},
    {"oem": "7045067000",   "name": "Auxiliary Instrument Panel Switch",       "price_usd": 37.00,  "cat": "Electrical",  "fits": "Coolray, Binyue"},
    {"oem": "2036512100",   "name": "Spark Plug Tube Sleeve",                  "price_usd": 3.70,   "cat": "Engine",      "fits": "Coolray, Binyue"},
    {"oem": "1070005700",   "name": "Oil and Gas Separator Assembly",          "price_usd": 37.00,  "cat": "Engine",      "fits": "SX11, Coolray"},
    # --- Emgrand / Atlas / Boyue ---
    {"oem": "6013056300",   "name": "Front Radiator Grille Assembly",          "price_usd": 45.50,  "cat": "Body Parts",  "fits": "Emgrand, Atlas"},
    {"oem": "1116000173",   "name": "Fuel Pump Assembly",                      "price_usd": 53.10,  "cat": "Fuel System", "fits": "Emgrand EC7, GX7, SX7, Vision X3"},
    {"oem": "4082010300",   "name": "CV Axle Drive Shaft Assembly",            "price_usd": 32.81,  "cat": "Drivetrain",  "fits": "Emgrand, Atlas"},
    {"oem": "6608133417",   "name": "Transmission Oil Cooler Radiator",        "price_usd": 35.00,  "cat": "Cooling",     "fits": "Emgrand 2022"},
    {"oem": "4150595080",   "name": "Flywheel Assembly",                       "price_usd": 132.50, "cat": "Engine",      "fits": "Emgrand EC7"},
    {"oem": "8040081700",   "name": "Rear Left Seat Belt Assembly",            "price_usd": 44.30,  "cat": "Body Parts",  "fits": "Emgrand 7"},
    {"oem": "8040081800",   "name": "Rear Right Seat Belt Assembly",           "price_usd": 44.30,  "cat": "Body Parts",  "fits": "Emgrand 7"},
    {"oem": "4082007600",   "name": "Electric Clutch Coupling Rear Differential 4WD", "price_usd": 220.00, "cat": "Drivetrain", "fits": "Atlas, Emgrand X7"},
    {"oem": "5077092400",   "name": "Outside Door Handle Front Left",          "price_usd": 14.50,  "cat": "Body Parts",  "fits": "Emgrand"},
    {"oem": "5077092500",   "name": "Outside Door Handle Front Right",         "price_usd": 14.50,  "cat": "Body Parts",  "fits": "Emgrand"},
    {"oem": "5083057600",   "name": "Outside Door Handle Rear Left",           "price_usd": 14.50,  "cat": "Body Parts",  "fits": "Emgrand"},
    {"oem": "5083057700",   "name": "Outside Door Handle Rear Right",          "price_usd": 14.50,  "cat": "Body Parts",  "fits": "Emgrand"},
    {"oem": "4060039200",   "name": "Electronic Stability Control Module ESC", "price_usd": 378.40, "cat": "Electrical",  "fits": "Emgrand"},
    # --- Geometry C ---
    {"oem": "7057027500",   "name": "Reverse Light Left",                      "price_usd": 34.00,  "cat": "Lighting",    "fits": "Geometry C"},
    {"oem": "5077080900",   "name": "Electric Door Lock Front",                "price_usd": 6.08,   "cat": "Electrical",  "fits": "Geometry C"},
    # --- General Geely ---
    {"oem": "8893044868",   "name": "Automatic Parking Control Module 360 Camera", "price_usd": 184.00, "cat": "Electrical", "fits": "Emgrand, Dihao 2022"},
    {"oem": "5062053300C15","name": "Rear Door Body Assembly",                 "price_usd": 605.75, "cat": "Body Parts",  "fits": "Starray"},
]


async def main() -> None:
    conn = await asyncpg.connect(DB_DSN)
    try:
        mfr_id = BRAND_IDS["Geely"]
        seen_oem: set = set()
        inserted = 0
        skipped = 0

        for raw in GEELY_PARTS:
            oem = clean_oem(raw.get("oem", ""))
            if not oem or oem in seen_oem:
                skipped += 1
                continue
            seen_oem.add(oem)

            sku = build_sku("Geely", oem)
            name = raw["name"][:255]
            price_ils = round(raw["price_usd"] * USD_TO_ILS, 2)
            category = map_cat(raw.get("cat", ""))
            desc = f"{name}. Fits: {raw.get('fits', 'Geely')}."

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
                    """, sku, oem, name, "Geely", mfr_id,
                         category, desc, price_ils, round(price_ils * 1.2, 2))
                    if part_id:
                        inserted += 1
            except Exception as e:
                log.warning("Failed %s: %s", sku, e)
                skipped += 1

        log.info("Geely: inserted=%d skipped=%d", inserted, skipped)

        count = await conn.fetchval(
            "SELECT COUNT(*) FROM parts_catalog WHERE manufacturer='Geely' AND is_active=TRUE"
        )
        log.info("DB total Geely active parts: %d", count)
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
