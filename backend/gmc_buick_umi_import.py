#!/usr/bin/env python3
"""
GMC & Buick official UMI (Universal Motors Israel) parts price list import.
Source: /tmp/umi_full_text.txt (pdftotext output of umiprice.pdf, 1,295 pages)
~845 GMC + ~2,493 Buick OEM parts at Israeli retail prices (ILS incl. 17% VAT).
Run inside container: python3 /app/gmc_buick_umi_import.py
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

GMC_BRAND_ID   = "603d8f8f-f23d-4970-acd9-5ae85ad64907"
BUICK_BRAND_ID = "2447c5a2-9494-45ed-a8b9-754caa8aff95"
TEXT_PATH = "/tmp/umi_full_text.txt"

LTR      = '‪'
PDF_MARK = '‬'
RTL      = '‫'

ltr_re = re.compile(f'{re.escape(LTR)}(.*?){re.escape(PDF_MARK)}')
rtl_re = re.compile(f'{re.escape(RTL)}(.*?){re.escape(PDF_MARK)}')

TERM_MAP = [
    ('קליפס לדלת', 'Door Clip'), ('קפיץ ידית מהלכים', 'Gear Selector Spring'),
    ('אום כוון לפנס ראשי', 'Headlamp Aim Nut'), ('פלשר לאתות', 'Turn Signal Flasher'),
    ('תותב למוט מכסה מנוע', 'Hood Rod Bracket'), ('מדיד שמן מנוע', 'Engine Oil Dipstick'),
    ('דיסק בלם', 'Brake Disc'), ('רפידות בלם', 'Brake Pads'), ('רפידת בלם', 'Brake Pad'),
    ('בולם זעזועים', 'Shock Absorber'), ('בולם', 'Absorber'), ('קפיץ', 'Spring'),
    ('מיסב', 'Bearing'), ('זרוע בקרה', 'Control Arm'), ('זרוע', 'Arm'),
    ('תושבת', 'Bracket'), ('אטם', 'Seal/Gasket'), ('טבעת', 'Ring'),
    ('פילטר שמן', 'Oil Filter'), ('פילטר אוויר', 'Air Filter'), ('פילטר', 'Filter'),
    ('מסנן', 'Filter'), ('רדיאטור', 'Radiator'), ('משאבת מים', 'Water Pump'),
    ('תרמוסטט', 'Thermostat'), ('קירור', 'Cooling'), ('מנוע', 'Motor/Engine'),
    ('משאבת שמן', 'Oil Pump'), ('משאבת דלק', 'Fuel Pump'), ('משאבת', 'Pump'),
    ('שסתום', 'Valve'), ('חיישן חמצן', 'Oxygen Sensor'), ('חיישן טמפ', 'Temperature Sensor'),
    ('חיישן לחץ', 'Pressure Sensor'), ('חיישן ABS', 'ABS Sensor'), ('חיישן', 'Sensor'),
    ('ממסר', 'Relay'), ('נתיך', 'Fuse'), ('כבל', 'Cable'), ('מחבר', 'Connector'),
    ('ידית', 'Handle'), ('מנעול', 'Lock'), ('ציר', 'Hinge'), ('דלת', 'Door'),
    ('פגוש', 'Bumper'), ('כנף', 'Fender/Wing'), ('גג', 'Roof'), ('פנל', 'Panel'),
    ('פנס', 'Lamp'), ('מנורה', 'Bulb/Lamp'), ('מצלמה', 'Camera'),
    ('מושב', 'Seat'), ('חגורת בטיחות', 'Seatbelt'), ('כרית אוויר', 'Airbag'),
    ('הגה', 'Steering Wheel'), ('תיבת הגה', 'Steering Box'),
    ('תיבת הילוכים', 'Gearbox'), ('גיר', 'Gear'), ('מצמד', 'Clutch'),
    ('ציריה', 'CV Axle'), ('מזגן', 'AC'), ('מאוורר', 'Fan'),
    ('חלון', 'Window'), ('שמשה', 'Windshield'), ('מגב', 'Wiper'),
    ('בורג', 'Bolt'), ('אום', 'Nut'), ('מוט', 'Rod'),
    ('רצועה', 'Belt'), ('שרשרת', 'Chain'), ('גלגלת', 'Pulley'),
    ('סוללה', 'Battery'), ('מצבר', 'Battery'), ('מצת', 'Spark Plug'),
    ('סליל הצתה', 'Ignition Coil'), ('מזרק דלק', 'Fuel Injector'),
    ('יחידת בקרה', 'Control Unit'), ('כסוי', 'Cover'), ('כיסוי', 'Cover'),
    ('שטיח ריצפה', 'Floor Mat'), ('גומיה', 'Rubber/Gasket'),
    ('סורג', 'Grille'), ('לוח שעונים', 'Dashboard Panel'),
    ('חישוק', 'Wheel Rim'), ('גלגל רזרבי', 'Spare Wheel'),
    ('פולי גל ארכובה', 'Crankshaft Pulley'), ('גל ארכובה', 'Crankshaft'),
    ('מעגל משולב', 'Integrated Circuit'), ('מחזיק', 'Holder/Bracket'),
    ('מחזיק לגלגלת', 'Pulley Bracket'), ('צינור שמן', 'Oil Pipe'),
    ('צינור', 'Pipe/Hose'), ('מוביל', 'Guide'), ('ריפוד', 'Upholstery'),
    ('כסוי עליון מעל מושב', 'Seat Top Cover'), ('מתג אור בלם', 'Brake Light Switch'),
    ('מתג', 'Switch'), ('לוח', 'Panel/Board'), ('מדיד', 'Dipstick/Gauge'),
    ('אבטחה', 'Lock/Retention'), ('בורג ראש מנוע', 'Cylinder Head Bolt'),
]

CAT_RULES = [
    (['דיסק בלם', 'רפידות בלם', 'רפידת בלם', 'בלם', 'ABS'], 'brakes'),
    (['בולם זעזועים', 'קפיץ', 'מיסב', 'זרוע', 'מתלה'], 'suspension-steering'),
    (['הגה', 'תיבת הגה'], 'suspension-steering'),
    (['פנס', 'מנורה', 'תאורה', 'LED', 'נורה'], 'lighting'),
    (['רדיאטור', 'משאבת מים', 'תרמוסטט', 'קירור', 'מאוורר'], 'cooling'),
    (['מסנן', 'פילטר', 'שמן מנוע'], 'engine'),
    (['מנוע', 'בוכנה', 'גל ארכובה', 'ראש גליל', 'סליל הצתה'], 'engine'),
    (['חיישן', 'ממסר', 'נתיך', 'כבל', 'צמת', 'ECU', 'יחידת בקרה', 'סוללה', 'מצבר', 'מעגל משולב'], 'electrical-sensors'),
    (['פגוש', 'כנף', 'גג', 'פנל', 'גוף', 'כסוי', 'כיסוי'], 'body-exterior'),
    (['שמשה', 'חלון', 'מגב'], 'body-exterior'),
    (['מושב', 'ריפוד', 'שטיח'], 'interior'),
    (['כרית אוויר', 'חגורת בטיחות'], 'body-exterior'),
    (['תיבת הילוכים', 'גיר', 'מצמד', 'ציריה'], 'gearbox'),
    (['מזגן', 'HVAC'], 'air-conditioning-heating'),
    (['מצת', 'מזרק דלק', 'משאבת דלק'], 'fuel-air'),
    (['רצועה', 'שרשרת', 'גלגלת'], 'belts-chains'),
    (['חישוק', 'גלגל רזרבי', 'צמיג'], 'wheels-tyres'),
    (['דלת'], 'body-exterior'),
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
    if HEBREW_RE.search(s):
        return f"OEM Part - {heb.strip()}"[:255]
    return s[:255]


def build_sku(brand_prefix: str, part_num: str) -> str:
    clean = re.sub(r"[^A-Z0-9]", "-", part_num.upper().strip())
    return f"{brand_prefix}-{clean}"


def parse_line(line: str) -> dict | None:
    ltr_b = [v.strip() for v in ltr_re.findall(line)]
    rtl_b = [v.strip() for v in rtl_re.findall(line)]

    if len(ltr_b) < 6 or len(rtl_b) < 3:
        return None

    part_num = ltr_b[1]
    model = ltr_b[-1]

    # Price = ltr[-4] (consistent across all block-count variants)
    price_raw = ltr_b[-4].replace(',', '').strip()
    try:
        price = float(price_raw)
        if price <= 0:
            return None
    except (ValueError, TypeError):
        return None

    # Hebrew description: rtl[2], strip embedded directional marks
    heb_raw = rtl_b[2] if len(rtl_b) > 2 else ''
    heb_desc = re.sub(f'[{re.escape(LTR)}{re.escape(RTL)}{re.escape(PDF_MARK)}]', '',
                      heb_raw).strip()

    # Determine brand from model string
    model_up = model.upper()
    if 'GMC' in model_up:
        brand = 'GMC'
        brand_id = GMC_BRAND_ID
        sku_prefix = 'GMC'
    elif 'BUICK' in model_up:
        brand = 'Buick'
        brand_id = BUICK_BRAND_ID
        sku_prefix = 'BUICK'
    else:
        return None

    return {
        'part_num': part_num,
        'heb_desc': heb_desc,
        'price': price,
        'model': model,
        'brand': brand,
        'brand_id': brand_id,
        'sku_prefix': sku_prefix,
    }


def load_parts(text_path: str) -> list[dict]:
    with open(text_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    raw = []
    for line in lines:
        if 'GMC' not in line and 'BUICK' not in line:
            continue
        r = parse_line(line)
        if r:
            raw.append(r)

    # Deduplicate by part_num: keep first occurrence (preserves model association)
    seen: dict[str, dict] = {}
    for r in raw:
        pn = r['part_num']
        if pn not in seen:
            seen[pn] = r

    return list(seen.values())


async def import_parts(conn: asyncpg.Connection, parts: list[dict]) -> dict:
    inserted = 0
    updated = 0
    skipped = 0

    for r in parts:
        part_num = r['part_num']
        if not part_num or len(part_num) < 3:
            skipped += 1
            continue

        price = r['price']
        heb_desc = r['heb_desc']
        model = r['model']
        brand = r['brand']
        brand_id = r['brand_id']
        sku_prefix = r['sku_prefix']

        sku = build_sku(sku_prefix, part_num)
        eng_name = translate_name(heb_desc) if heb_desc else f"OEM Part {part_num}"
        category = categorize(heb_desc) if heb_desc else 'accessories'

        desc = (
            f"{eng_name}. Hebrew: {heb_desc}. "
            f"Model: {model}. Israeli retail price (incl. VAT): {price:.2f} ILS."
        )[:500]

        # IL retail incl. 17% VAT → normalize to 18% IL VAT (no markup — IL official ref)
        il_retail = round(price / 1.17 * 1.18, 2)

        try:
            async with conn.transaction():
                result = await conn.fetchrow("""
                    INSERT INTO parts_catalog(
                        id, sku, oem_number, name, manufacturer, manufacturer_id,
                        category, description, specifications,
                        base_price, importer_price_ils, min_price_ils, max_price_ils,
                        part_type, is_safety_critical, needs_oem_lookup,
                        master_enriched, is_active, created_at, updated_at
                    ) VALUES (
                        gen_random_uuid(), $1, $2, $3, $4, $5::uuid,
                        $6, $7, '{}'::jsonb,
                        $8, 0, $8, $8,
                        'original', FALSE, FALSE,
                        FALSE, TRUE, NOW(), NOW()
                    )
                    ON CONFLICT (sku) DO UPDATE SET
                        name = EXCLUDED.name,
                        base_price = EXCLUDED.base_price,
                        importer_price_ils = CASE WHEN EXCLUDED.importer_price_ils > 0 THEN EXCLUDED.importer_price_ils ELSE parts_catalog.importer_price_ils END,
                        min_price_ils = EXCLUDED.min_price_ils,
                        max_price_ils = EXCLUDED.max_price_ils,
                        updated_at = NOW()
                    RETURNING xmax
                """, sku, part_num, eng_name[:255], brand, brand_id,
                     category, desc, il_retail)
                if result:
                    # xmax=0 means inserted, >0 means updated
                    if result['xmax'] == 0:
                        inserted += 1
                    else:
                        updated += 1
        except Exception as e:
            log.warning("Failed %s: %s", sku, e)
            skipped += 1

    return {"inserted": inserted, "updated": updated, "skipped": skipped}


async def main() -> None:
    log.info("Loading UMI PDF text from %s ...", TEXT_PATH)
    parts = load_parts(TEXT_PATH)
    gmc_count = sum(1 for p in parts if p['brand'] == 'GMC')
    buick_count = sum(1 for p in parts if p['brand'] == 'Buick')
    log.info("Parsed %d unique parts: GMC=%d, Buick=%d", len(parts), gmc_count, buick_count)

    conn = await asyncpg.connect(DB_DSN)
    try:
        result = await import_parts(conn, parts)
        gmc_total = await conn.fetchval(
            "SELECT COUNT(*) FROM parts_catalog WHERE manufacturer='GMC' AND is_active=TRUE"
        )
        buick_total = await conn.fetchval(
            "SELECT COUNT(*) FROM parts_catalog WHERE manufacturer='Buick' AND is_active=TRUE"
        )
        log.info(
            "Done: inserted=%d updated=%d skipped=%d | "
            "DB total GMC=%d Buick=%d",
            result["inserted"], result["updated"], result["skipped"],
            gmc_total, buick_total
        )
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
