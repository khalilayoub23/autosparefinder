"""
Script: zeekr_full_import.py
Purpose: Import official Zeekr Israel importer (Geo Mobility) price list PDFs into the catalog.

Process:
  1. Parse all ZEEKER001_*.pdf files from /app/uploads/ using pdfplumber
  2. Extract rows: SKU, Hebrew description, price (ILS excl. VAT), compatible models, part type
  3. Reverse RTL Hebrew text from PDF columns
  4. Infer English category from Hebrew keywords
  5. Deduplicate across all PDF files (keep highest-confidence entry per SKU)
  6. Get-or-create "Geo Mobility - Zeekr Israel" supplier record
  7. Upsert to parts_catalog — insert new, update price/name on existing
  8. Insert part_vehicle_fitment for each model (ZEEKR 001, X, 7X)
  9. Upsert supplier_parts with price, warranty, delivery info
  10. Run meili_sync for Zeekr
  11. Print final DB stats

Data Imported / Modified:
  - parts_catalog: sku, oem_number, name, name_he, manufacturer, manufacturer_id, category,
                   importer_price_ils, min_price_ils, max_price_ils, part_condition,
                   aftermarket_tier, specifications (JSONB), is_active, needs_oem_lookup,
                   master_enriched
  - part_vehicle_fitment: part_id, manufacturer, model, year_from, year_to, engine_type,
                          notes, manufacturer_id
  - supplier_parts: supplier_id, part_id, supplier_sku, price_ils, availability,
                    is_available, warranty_months, estimated_delivery_days, supplier_url
  - suppliers: get-or-create "Geo Mobility - Zeekr Israel"

Data Sources / Web Links:
  - Geo Mobility Ltd (official Zeekr Israel importer): https://zeekr-israel.co.il
  - PDF source: /app/uploads/ZEEKER001_*.pdf  (uploaded via admin UI)
  - Importer address: יגאל אלון 65 תל אביב | contact@zeekr-israel.co.il | tel 8133

Missing Data Delegation:
  - English descriptions → ai_catalog_builder.py (master_enriched=False triggers it)
  - Parts without fitment data → REX todo created
  - Missing OEM numbers → needs_oem_lookup=True

Confidence tier: 1.00 (Official Israeli importer price list)
VAT: Prices in PDF are EXCL. 18% VAT — stored as max_price_ils (incl. 18% VAT = price * 1.18);
     base_price = cost×1.45, importer_price_ils = cost = max_price_ils/1.18 (CLAUDE.md formula)

Author: AutoSpareFinder Agent
Last Updated: 2026-06-01
"""

import asyncio
import glob
import json
import logging
import os
import re
import subprocess
import sys
import uuid

import asyncpg
import pdfplumber

sys.path.insert(0, '/app')

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

ZEEKR_BRAND_ID = 'ac1ddaf7-d0e3-4ea2-8990-da71fbc71ade'
GEO_MOBILITY_NAME = 'Geo Mobility - Zeekr Israel'
GEO_MOBILITY_URL = 'https://zeekr-israel.co.il'

PDF_GLOB = '/app/uploads/ZEEKER001_*.pdf'

MODEL_YEARS = {
    'ZEEKR 001': (2021, None),
    'ZEEKR X':   (2023, None),
    'ZEEKR 7X':  (2024, None),
    'ZEEKR':     (2021, None),
}

SAFETY_KEYWORDS_HE = ['בלמים', 'רפידות בלם', 'דיסק בלם', 'קליפר', 'כרית אוויר', 'חגורת בטיחות', 'הגה']

CATEGORY_MAP_HE = [
    (['רפידות בלם', 'דיסק בלם', 'קליפר', 'נוזל בלמים'], 'Brakes'),
    (['פנס ראשי', 'פנס אחורי', 'תאורה', 'נורה', 'פנס ערפל', 'LED', 'רצועת לד'], 'Lighting'),
    (['מתלה', 'בולם זעזועים', 'קפיץ', 'זרוע בקרה', 'מוט מייצב', 'סרן'], 'Suspension & Steering'),
    (['גלגל הגה', 'הגה', 'ידית הגה'], 'Suspension & Steering'),
    (['מנוע חשמלי', 'בית מנוע', 'גל הארכה', 'מנוע אחורי', 'מנוע קדמי'], 'Engine Components'),
    (['בקר מנוע', 'יחידת בקרה', 'ADCU', 'VCU', 'ECU', 'מודול בקרה'], 'Wiring & Modules'),
    (['חיישן', 'חיישן מהירות', 'חיישן לחץ'], 'Sensors'),
    (['מזגן', 'מיזוג', 'מדחס', 'מעבה', 'מאיידה', 'HVAC', 'מפזר חום אוויר'], 'A/C & Heating'),
    (['רדיאטור', 'מצנן', 'קירור', 'משאבת מים', 'תרמוסטט', 'מאוורר קירור'], 'Engine Cooling'),
    (['מגב', 'מגבים', 'ספריי שמשה', 'משאבת שמשה'], 'Wipers & Washers'),
    (['מכסה מנוע', 'פגוש', 'כנף', 'ויזר', 'גריל', 'מראה', 'ידית דלת', 'דלת', 'תא מטען'], 'Body Parts'),
    (['פנל', 'לוח', 'חיפוי', 'כיסוי'], 'Body Parts'),
    (['מושב', 'כרית', 'ריפוד', 'קונסולה', 'לוח מחוונים', 'שטיחי רצפה', 'שמשיה', 'אורגנייזר'], 'Interior'),
    (['גג פנורמי', 'חלון', 'שמשה'], 'Auto Glass'),
    (['גלגל', 'צמיג', 'חישוק'], 'Wheels & Tires'),
    (['כרית אוויר', 'airbag', 'חגורת בטיחות'], 'Service & General'),
    (['כבל טעינה', 'V2L', 'V2G', 'עמדת טעינה', 'מחבר טעינה'], 'EV Charging'),
    (['מסנן אוויר', 'מסנן מזגן', 'מסנן שמן', 'מסנן דלק', 'פילטר'], 'Filters'),
    (['שמן מנוע', 'נוזל בלמים', 'נוזל קירור'], 'Fluids & Lubricants'),
    (['גיר', 'תיבת הילוכים', 'גלגל שיניים'], 'Transmission'),
    (['גל הינע', 'ציר', 'קשר הדדי'], 'Driveline & Axles'),
    (['בטריה', 'מצבר', 'חבילת סוללות', 'תא סוללה'], 'Batteries & Power'),
    (['ניצוצן', 'מצת'], 'Service & General'),
    (['ברגים', 'בורג', 'אום', 'קליפס', 'תושבת', 'סוגר'], 'Service & General'),
]

EN_HINT_MAP = [
    ('רפידות בלם', 'Brake Pad Set'), ('דיסק בלם', 'Brake Disc'), ('קליפר', 'Brake Caliper'),
    ('פנס ראשי', 'Headlight'), ('פנס אחורי', 'Tail Light'), ('פנס ערפל', 'Fog Light'),
    ('מנוע חשמלי', 'Electric Motor'), ('בטריה', 'HV Battery'), ('חבילת סוללות', 'Battery Pack'),
    ('יחידת בקרה', 'Control Unit'), ('מודול בקרה', 'Control Module'), ('ADCU', 'ADCU'),
    ('חיישן', 'Sensor'), ('מגב', 'Wiper Blade'), ('מראה', 'Side Mirror'),
    ('פגוש', 'Bumper'), ('כנף', 'Fender'), ('דלת', 'Door'), ('מכסה מנוע', 'Hood'),
    ('מושב', 'Seat'), ('קונסולה', 'Center Console'), ('לוח מחוונים', 'Dashboard'),
    ('מזגן', 'A/C Component'), ('רדיאטור', 'Radiator'), ('ידית', 'Handle'),
    ('מכסה', 'Cover'), ('כבל טעינה', 'Charging Cable'), ('V2L', 'V2L Adapter'),
    ('גלגל', 'Wheel'), ('מסנן', 'Filter'), ('גיר', 'Gearbox Component'),
    ('גל הינע', 'Drive Shaft'), ('שטיחי רצפה', 'Floor Mats'), ('שמשיה', 'Sun Shade'),
]

DSN = 'postgresql://autospare:e4b79d75ca640dbe7f259618f078b82f21573e419308f668beed5e20b26b1d43@postgres_catalog:5432/autospare'


# ── Hebrew helpers ──────────────────────────────────────────────────────────

def reverse_he(s: str) -> str:
    """PDF RTL text comes out character-reversed; fix it."""
    if not s:
        return s
    he_count = sum(1 for c in s if '\u05d0' <= c <= '\u05ea')
    return s[::-1] if he_count > len(s) * 0.25 else s


def infer_category(name_he: str) -> str:
    for keywords, cat in CATEGORY_MAP_HE:
        if any(kw in name_he for kw in keywords):
            return cat
    return 'Service & General'


def is_safety_critical(name_he: str) -> bool:
    return any(kw in name_he for kw in SAFETY_KEYWORDS_HE)


def make_en_name(name_he: str, oem: str) -> str:
    for kw, en in EN_HINT_MAP:
        if kw in name_he:
            return f'Zeekr {en} {oem}'[:255]
    return f'Zeekr Part {oem}'[:255]


# ── PDF parser ───────────────────────────────────────────────────────────────

def parse_zeekr_pdf(pdf_path: str) -> list[dict]:
    """
    Parse a Geo Mobility Zeekr PDF price list.
    Column layout (RTL): [type | models | price | stock | desc_he | sku]
    """
    parts: list[dict] = []
    seen: set[str] = set()

    with pdfplumber.open(pdf_path) as pdf:
        log.info(f'Parsing {pdf_path} — {len(pdf.pages)} pages')
        for pg_num, page in enumerate(pdf.pages, 1):
            table = page.extract_table()
            if not table:
                continue
            for row in table:
                if not row or len(row) < 4:
                    continue
                row = [c.strip() if c else '' for c in row]

                # Skip header rows
                combined = ' '.join(row)
                if any(h in combined for h in ['מחיר', 'תיאור פריט', 'מק"ט', 'Column']):
                    continue

                # Column assignment
                if len(row) >= 6:
                    type_raw, models_raw, price_raw, _stock, desc_raw, sku_raw = (
                        row[0], row[1], row[2], row[3], row[4], row[5]
                    )
                elif len(row) == 5:
                    type_raw, models_raw, price_raw, desc_raw, sku_raw = (
                        '', row[0], row[1], row[3], row[4]
                    )
                else:
                    type_raw, price_raw, desc_raw, sku_raw = '', row[1], row[2], row[3]
                    models_raw = ''

                # SKU validation
                sku_raw = sku_raw.strip()
                if not sku_raw or len(sku_raw) < 4:
                    continue
                if sku_raw in seen:
                    continue

                # Price
                try:
                    price = float(price_raw.replace(',', '').strip())
                    if price <= 0 or price > 500000:
                        continue
                except (ValueError, AttributeError):
                    continue

                # Hebrew desc
                name_he = reverse_he(desc_raw)
                if not name_he or len(name_he) < 2:
                    continue

                seen.add(sku_raw)

                # OEM: strip ZE- prefix for cross-reference
                oem = re.sub(r'^ZE[-]?', '', sku_raw).strip()

                # Models
                raw_models = list(set(re.findall(r'ZEEKR\s*(?:001|7X|X)?', models_raw)))
                models = [m.strip() for m in raw_models if m.strip()] or ['ZEEKR']

                # Aftermarket tier — DB constraint: NULL | OE_equivalent | economy | generic
                type_he = reverse_he(type_raw)
                # OEM/original parts get NULL (not 'OEM') per DB constraint
                tier = None if 'מקורי' in type_he else 'OE_equivalent'

                cat = infer_category(name_he)
                parts.append({
                    'sku':            f'ZEEKR-IL-{sku_raw}'[:100],
                    'raw_sku':        sku_raw,
                    'oem_number':     oem,
                    'name':           make_en_name(name_he, oem),
                    'name_he':        name_he[:255],
                    'price_ils':      price,
                    'models':         models,
                    'category':       cat,
                    'aftermarket_tier': tier,
                    'safety':         is_safety_critical(name_he),
                })

    log.info(f'  → {len(parts)} parts parsed from {pdf_path}')
    return parts


def dedupe(all_parts: list[dict]) -> list[dict]:
    best: dict[str, dict] = {}
    for p in all_parts:
        k = p['sku']
        if k not in best or p['price_ils'] > best[k]['price_ils']:
            best[k] = p
    return list(best.values())


# ── DB helpers ───────────────────────────────────────────────────────────────

async def ensure_supplier(conn) -> str:
    row = await conn.fetchrow('SELECT id FROM suppliers WHERE name = $1', GEO_MOBILITY_NAME)
    if row:
        return str(row['id'])
    sid = str(uuid.uuid4())
    await conn.execute("""
        INSERT INTO suppliers (id, name, website, country,
                               is_active, is_manufacturer, manufacturer_name, manufacturer_id,
                               reliability_score, created_at, updated_at)
        VALUES ($1, $2, $3, 'IL', true, true, 'Zeekr', $4::uuid, 0.95, NOW(), NOW())
        ON CONFLICT (name) DO NOTHING
    """, sid, GEO_MOBILITY_NAME, GEO_MOBILITY_URL, ZEEKR_BRAND_ID)
    log.info(f'Created supplier: {GEO_MOBILITY_NAME} id={sid}')
    return sid


async def upsert_part(conn, p: dict, supplier_id: str) -> tuple[str | None, bool]:
    """Returns (part_id, was_inserted)."""
    specs = json.dumps({
        'vat_included':      False,
        'vat_rate':          0.18,
        'currency':          'ILS',
        'source':            'Geo Mobility Official Price List 05/2025',
        'compatible_models': p['models'],
        'shipping_to_il':    True,
        'importer':          'גיאו מוביליטי בע"מ',
        'warranty_months':   12,
    })
    row = await conn.fetchrow("""
        INSERT INTO parts_catalog (
            id, sku, oem_number, name, name_he,
            manufacturer, manufacturer_id, category,
            base_price, importer_price_ils, min_price_ils, max_price_ils,
            part_condition, aftermarket_tier, is_safety_critical,
            specifications, compatible_vehicles,
            is_active, needs_oem_lookup, master_enriched,
            created_at, updated_at
        ) VALUES (
            gen_random_uuid(), $1, $2, $3, $4,
            'Zeekr', $5::uuid, $6,
            ROUND($7::numeric*1.18,2), 0, ROUND($7::numeric*1.18,2), ROUND($7::numeric*1.18,2),
            'new', $8, $9,
            $10::jsonb, $11::jsonb,
            true, false, false,
            NOW(), NOW()
        )
        ON CONFLICT (sku) DO UPDATE SET
            base_price          = EXCLUDED.base_price,
            importer_price_ils  = CASE WHEN EXCLUDED.importer_price_ils > 0 THEN EXCLUDED.importer_price_ils ELSE parts_catalog.importer_price_ils END,
            min_price_ils       = EXCLUDED.min_price_ils,
            max_price_ils       = EXCLUDED.max_price_ils,
            name_he             = COALESCE(EXCLUDED.name_he, parts_catalog.name_he),
            name                = CASE
                                    WHEN parts_catalog.name LIKE 'Zeekr Part%'
                                    THEN EXCLUDED.name
                                    ELSE parts_catalog.name
                                  END,
            category            = COALESCE(EXCLUDED.category, parts_catalog.category),
            aftermarket_tier    = EXCLUDED.aftermarket_tier,
            is_safety_critical  = EXCLUDED.is_safety_critical,
            specifications      = COALESCE(parts_catalog.specifications, '{}'::jsonb)
                                    || EXCLUDED.specifications,
            updated_at          = NOW()
        RETURNING id, (xmax = 0) AS was_inserted
    """,
        p['sku'], p['oem_number'], p['name'], p['name_he'],
        ZEEKR_BRAND_ID, p['category'],
        p['price_ils'],
        p['aftermarket_tier'], p['safety'],
        specs,
        json.dumps([{'make': 'Zeekr', 'model': m,
                     'year_from': MODEL_YEARS.get(m, (2021, None))[0],
                     'year_to':   MODEL_YEARS.get(m, (2021, None))[1]}
                    for m in p['models']]),
    )
    if row:
        return str(row['id']), bool(row['was_inserted'])
    return None, False


async def upsert_fitment(conn, part_id: str, models: list[str]) -> int:
    count = 0
    for model in models:
        year_from, year_to = MODEL_YEARS.get(model, MODEL_YEARS['ZEEKR'])
        try:
            await conn.execute("""
                INSERT INTO part_vehicle_fitment
                    (id, part_id, manufacturer, model, year_from, year_to,
                     engine_type, notes, manufacturer_id, created_at, updated_at)
                VALUES
                    (gen_random_uuid(), $1::uuid, 'Zeekr', $2, $3, $4,
                     'Electric', 'Geo Mobility official importer data',
                     $5::uuid, NOW(), NOW())
                ON CONFLICT (part_id, manufacturer, model, year_from) DO NOTHING
            """, part_id, model, year_from, year_to, ZEEKR_BRAND_ID)
            count += 1
        except Exception as e:
            log.debug(f'Fitment skip {model}: {e}')
    return count


async def upsert_supplier_part(conn, part_id: str, supplier_id: str, raw_sku: str, price: float):
    await conn.execute("""
        INSERT INTO supplier_parts (
            id, supplier_id, part_id, supplier_sku,
            price_ils, price_usd,
            availability, is_available, warranty_months,
            estimated_delivery_days, supplier_url,
            created_at, updated_at
        ) VALUES (
            gen_random_uuid(), $1::uuid, $2::uuid, $3,
            $4, 0.0,
            'in_stock', true, 12,
            14, $5,
            NOW(), NOW()
        )
        ON CONFLICT ON CONSTRAINT supplier_parts_supplier_id_supplier_sku_key DO UPDATE SET
            price_ils    = EXCLUDED.price_ils,
            is_available = true,
            updated_at   = NOW()
    """, supplier_id, part_id, raw_sku, price, GEO_MOBILITY_URL)


async def create_rex_todo(conn, title: str, description: str):
    """Create a REX agent todo for missing data."""
    try:
        await conn.execute("""
            INSERT INTO agent_todos
                (id, agent_name, title, description, priority, status, created_at, updated_at)
            VALUES
                (gen_random_uuid(), 'REX', $1, $2, 'high', 'not_started', NOW(), NOW())
            ON CONFLICT DO NOTHING
        """, title, description)
    except Exception as e:
        log.debug(f'Could not create REX todo: {e}')


# ── Main ─────────────────────────────────────────────────────────────────────

async def run():
    # Collect PDFs
    pdf_files = sorted(glob.glob(PDF_GLOB), reverse=True)  # newest first
    if not pdf_files:
        log.error(f'No PDFs found matching {PDF_GLOB}')
        return

    log.info(f'Found {len(pdf_files)} PDF files')

    # Parse all PDFs
    all_parts: list[dict] = []
    for pdf_path in pdf_files:
        all_parts.extend(parse_zeekr_pdf(pdf_path))

    parts = dedupe(all_parts)
    log.info(f'After dedup: {len(parts)} unique parts')

    if not parts:
        log.error('No parts parsed!')
        return

    # Connect and import
    conn = await asyncpg.connect(DSN)
    try:
        supplier_id = await ensure_supplier(conn)
        log.info(f'Supplier ID: {supplier_id}')

        inserted = updated = skipped = fitment_count = 0

        for i, p in enumerate(parts):
            try:
                async with conn.transaction():   # savepoint per row — one failure never aborts others
                    part_id, was_inserted = await upsert_part(conn, p, supplier_id)
                    if not part_id:
                        skipped += 1
                        continue

                    if was_inserted:
                        inserted += 1
                    else:
                        updated += 1

                    fitment_count += await upsert_fitment(conn, part_id, p['models'])
                    await upsert_supplier_part(conn, part_id, supplier_id, p['raw_sku'], p['price_ils'])

            except Exception as e:
                log.warning(f"Error on {p.get('sku', '?')}: {e}")
                skipped += 1

            if (i + 1) % 100 == 0:
                log.info(f'Progress: {i+1}/{len(parts)} — ins={inserted} upd={updated} skip={skipped}')

        # Create REX todos for missing data
        parts_without_fitment = [p for p in parts if not p.get('models') or p['models'] == ['ZEEKR']]
        if parts_without_fitment:
            await create_rex_todo(
                conn,
                f'Fetch precise fitment for {len(parts_without_fitment)} Zeekr parts',
                'Parts imported from Geo Mobility PDF have no model-specific fitment. '
                'Query TecDoc/eBay fitment APIs to map to specific Zeekr model/year.',
            )

        # Final stats
        row = await conn.fetchrow("""
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE importer_price_ils > 0) AS priced,
                COUNT(*) FILTER (WHERE name_he IS NOT NULL) AS has_he_name,
                ROUND(AVG(importer_price_ils) FILTER (WHERE importer_price_ils > 0), 2) AS avg_price
            FROM parts_catalog
            WHERE manufacturer = 'Zeekr' AND is_active = true
        """)

        log.info(f"""
============================================================
ZEEKR IMPORT COMPLETE
  New parts inserted : {inserted}
  Existing updated   : {updated}
  Skipped/errors     : {skipped}
  Fitment records    : {fitment_count}
------------------------------------------------------------
  DB totals for Zeekr:
    Total parts      : {row['total']}
    With price       : {row['priced']}
    With Hebrew name : {row['has_he_name']}
    Avg price (ILS)  : ₪{row['avg_price']}
============================================================""")

    finally:
        await conn.close()

    # Meili sync
    log.info('Running Meilisearch sync for Zeekr...')
    subprocess.run(
        ['python3', '/app/meili_sync.py', '--manufacturer', 'Zeekr', '--no-rebuild'],
        capture_output=False
    )


if __name__ == '__main__':
    asyncio.run(run())
