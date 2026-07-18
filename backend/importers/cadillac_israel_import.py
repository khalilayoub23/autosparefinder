#!/usr/bin/env python3
"""
Cadillac Israel official parts price list import.
Source: cadillacFile.xlsx via cadillac.co.il (Universal Motors Israel / יוניברסל מוטורס ישראל)
~6,100 OEM parts at Israeli consumer prices (ILS incl. VAT).
Prices are מחיר לצרכן (retail consumer price including 17% VAT).
Run inside container: python3 /app/importers/cadillac_israel_import.py
"""
from __future__ import annotations
import asyncio, logging, re
import asyncpg
import openpyxl

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

DB_DSN = (
    "postgresql://autospare:e4b79d75ca640dbe7f259618f078b82f21573e419308f668beed5e20b26b1d43"
    "@postgres_catalog:5432/autospare"
)

CADILLAC_BRAND_ID = "6f18818a-3c94-4a70-bab1-1384c19a94fc"
XLSX_PATH = "/tmp/cadillac_parts.xlsx"

# Hebrew → English automotive term map
TERM_MAP = [
    ('דיסקית לגל הינע', 'Drive Shaft Washer'),
    ('קליפס', 'Clip'), ('גומיה לדלת', 'Door Rubber Seal'),
    ('מתג לחץ שמן', 'Oil Pressure Switch'), ('תווית לחץ אויר', 'Tyre Pressure Label'),
    ('מתג למושב', 'Seat Switch'), ('צמת חוטים', 'Wire Harness'),
    ('מנורה לפנס ראשי', 'Headlamp Bulb'), ('מנורה', 'Bulb/Lamp'),
    ('דיסק בלם', 'Brake Disc'), ('רפידות בלם', 'Brake Pads'),
    ('רפידת בלם', 'Brake Pad'), ('בולם זעזועים', 'Shock Absorber'),
    ('בולם', 'Absorber'), ('קפיץ', 'Spring'), ('מיסב', 'Bearing'),
    ('זרוע בקרה', 'Control Arm'), ('זרוע', 'Arm'),
    ('תושבת', 'Bracket'), ('אטם', 'Seal/Gasket'), ('טבעת', 'Ring'),
    ('פילטר שמן', 'Oil Filter'), ('פילטר אוויר', 'Air Filter'),
    ('פילטר', 'Filter'), ('מסנן', 'Filter'),
    ('רדיאטור', 'Radiator'), ('משאבת מים', 'Water Pump'),
    ('תרמוסטט', 'Thermostat'), ('קירור', 'Cooling'),
    ('מנוע', 'Motor/Engine'), ('משאבת שמן', 'Oil Pump'),
    ('משאבת', 'Pump'), ('שסתום', 'Valve'),
    ('חיישן', 'Sensor'), ('חיישן חמצן', 'Oxygen Sensor'),
    ('חיישן טמפ', 'Temperature Sensor'), ('חיישן לחץ', 'Pressure Sensor'),
    ('חיישן ABS', 'ABS Sensor'), ('ממסר', 'Relay'), ('נתיך', 'Fuse'),
    ('כבל', 'Cable'), ('מחבר', 'Connector'), ('ידית', 'Handle'),
    ('מנעול', 'Lock'), ('ציר', 'Hinge'), ('דלת', 'Door'),
    ('פגוש', 'Bumper'), ('כנף', 'Fender/Wing'), ('גג', 'Roof'),
    ('פנל', 'Panel'), ('פנס', 'Lamp'), ('מצלמה', 'Camera'),
    ('מושב', 'Seat'), ('חגורת בטיחות', 'Seatbelt'),
    ('כרית אוויר', 'Airbag'), ('כרית', 'Cushion'),
    ('הגה', 'Steering Wheel'), ('תיבת הגה', 'Steering Box'),
    ('תיבת הילוכים', 'Gearbox'), ('גיר', 'Gear'),
    ('מצמד', 'Clutch'), ('ציריה', 'CV Axle'),
    ('מזגן', 'AC'), ('מאוורר', 'Fan'),
    ('חלון', 'Window'), ('שמשה', 'Windshield'), ('מגב', 'Wiper'),
    ('בורג', 'Bolt'), ('אום', 'Nut'), ('מוט', 'Rod'),
    ('רצועה', 'Belt'), ('שרשרת', 'Chain'), ('גלגלת', 'Pulley'),
    ('סוללה', 'Battery'), ('מצבר', 'Battery'),
    ('מצת', 'Spark Plug'), ('סליל הצתה', 'Ignition Coil'),
    ('משאבת דלק', 'Fuel Pump'), ('מזרק דלק', 'Fuel Injector'),
    ('מנטרל', 'Sensor'), ('בקר', 'Controller'),
    ('יחידת בקרה', 'Control Unit'), ('ECU', 'ECU'), ('ABS', 'ABS'),
]

# Category rules: Hebrew keyword → slug
CAT_RULES = [
    (['דיסק בלם', 'רפידות בלם', 'רפידת בלם', 'בלם', 'ABS'], 'brakes'),
    (['בולם זעזועים', 'קפיץ', 'מיסב', 'זרוע', 'מתלה'], 'suspension-steering'),
    (['הגה', 'תיבת הגה'], 'suspension-steering'),
    (['פנס', 'מנורה', 'תאורה', 'LED', 'נורה'], 'lighting'),
    (['רדיאטור', 'משאבת מים', 'תרמוסטט', 'קירור', 'מאוורר'], 'cooling'),
    (['מסנן', 'פילטר', 'אוויר', 'שמן מנוע'], 'engine'),
    (['מנוע', 'בוכנה', 'גל ארכובה', 'ראש גליל'], 'engine'),
    (['חיישן', 'ממסר', 'נתיך', 'כבל', 'צמת', 'ECU', 'יחידת בקרה', 'סוללה', 'מצבר'], 'electrical-sensors'),
    (['פגוש', 'דלת', 'כנף', 'גג', 'פנל', 'גוף'], 'body-exterior'),
    (['שמשה', 'חלון', 'מגב'], 'body-exterior'),
    (['מושב', 'ריפוד', 'שטיח'], 'interior'),
    (['כרית אוויר', 'חגורת בטיחות'], 'body-exterior'),
    (['תיבת הילוכים', 'גיר', 'מצמד', 'ציריה'], 'gearbox'),
    (['מזגן', 'HVAC'], 'air-conditioning-heating'),
    (['מצת', 'סליל הצתה', 'מזרק דלק', 'משאבת דלק'], 'fuel-air'),
    (['רצועה', 'שרשרת', 'גלגלת'], 'belts-chains'),
    (['ציריה', 'גל הינע'], 'clutch-drivetrain'),
]

HEBREW_RE = re.compile(r'[א-ת]')


def categorize(desc: str) -> str:
    for keywords, cat in CAT_RULES:
        for kw in keywords:
            if kw in desc:
                return cat
    return 'accessories'


def translate_name(heb: str) -> str:
    s = heb.strip()
    for heb_term, eng_term in TERM_MAP:
        s = s.replace(heb_term, eng_term)
    s = re.sub(r'\s+', ' ', s).strip()
    # If still Hebrew, prefix with OEM
    if HEBREW_RE.search(s):
        return f"OEM Part - {heb.strip()}"[:255]
    return s[:255]


def build_sku(part_num: str) -> str:
    clean = re.sub(r"[^A-Z0-9]", "-", part_num.upper().strip())
    return f"CADILLAC-{clean}"


def parse_price(val) -> float | None:
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    try:
        return float(str(val).replace(',', ''))
    except ValueError:
        return None


async def import_parts(conn: asyncpg.Connection, rows: list) -> dict:
    inserted = 0
    skipped = 0
    seen_sku: set = set()

    for row in rows:
        importer, part_num, heb_desc, part_type, stock, price_ils, warranty, model = row

        if not part_num or not heb_desc:
            skipped += 1
            continue

        part_num = str(part_num).strip()
        if not part_num or len(part_num) < 3:
            skipped += 1
            continue

        price = parse_price(price_ils)
        if price is None or price <= 0:
            skipped += 1
            continue

        sku = build_sku(part_num)
        if sku in seen_sku:
            skipped += 1
            continue
        seen_sku.add(sku)

        heb_str = str(heb_desc).strip()
        eng_name = translate_name(heb_str)
        category = categorize(heb_str)
        model_str = str(model).strip() if model else 'Cadillac'
        # Clean model name
        model_str = re.sub(r'^(IL\s+)?CADILL?AC\s*', '', model_str, flags=re.I).strip() or model_str

        desc = (f"{eng_name}. Hebrew: {heb_str}. "
                f"Model: {model_str}. Israeli retail price (incl. VAT): {price:.2f} ILS.")[:500]

        # Consumer retail incl. 17% VAT → cost excl. VAT → normalize to 18% VAT
        cost      = round(price / 1.17, 2)           # excl. VAT (our cost reference)
        il_retail = round(cost * 1.18, 2)            # consumer price at 18% VAT
        selling   = round(cost * 1.45, 2)            # our selling price (45% margin)

        try:
            async with conn.transaction():
                pid = await conn.fetchval("""
                    INSERT INTO parts_catalog(
                        id, sku, oem_number, name, manufacturer, manufacturer_id,
                        category, description, specifications,
                        base_price, importer_price_ils, min_price_ils, max_price_ils,
                        part_type, is_safety_critical, needs_oem_lookup,
                        master_enriched, is_active, created_at, updated_at
                    ) VALUES (
                        gen_random_uuid(), $1, $2, $3, 'Cadillac', $4::uuid,
                        $5, $6, '{}'::jsonb,
                        $8, $7, $7, $9,
                        'original', FALSE, FALSE,
                        FALSE, TRUE, NOW(), NOW()
                    )
                    ON CONFLICT (sku) DO UPDATE SET
                        name = EXCLUDED.name,
                        base_price = EXCLUDED.base_price,
                        importer_price_ils = EXCLUDED.importer_price_ils,
                        min_price_ils = EXCLUDED.min_price_ils,
                        max_price_ils = EXCLUDED.max_price_ils,
                        updated_at = NOW()
                    RETURNING id
                """, sku, part_num, eng_name[:255], CADILLAC_BRAND_ID,
                     category, desc, cost, selling, il_retail)
                if pid:
                    inserted += 1
        except Exception as e:
            log.warning("Failed %s: %s", sku, e)
            skipped += 1

    return {"inserted": inserted, "skipped": skipped}


async def main() -> None:
    log.info("Loading Cadillac parts Excel...")
    wb = openpyxl.load_workbook(XLSX_PATH, read_only=True)
    ws = wb.active
    all_rows = list(ws.iter_rows(values_only=True))
    # Skip first 2 rows (empty header + column headers)
    data_rows = all_rows[2:]
    log.info("Excel loaded: %d data rows", len(data_rows))

    conn = await asyncpg.connect(DB_DSN)
    try:
        result = await import_parts(conn, data_rows)
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM parts_catalog WHERE manufacturer='Cadillac' AND is_active=TRUE"
        )
        log.info("Done: inserted=%d skipped=%d | DB total Cadillac=%d",
                 result["inserted"], result["skipped"], count)
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
