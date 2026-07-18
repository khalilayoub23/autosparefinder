"""
VW-Group Fitment Backfill — reads champion_motors_parts.json
and populates part_vehicle_fitment for Volkswagen, Audi, Skoda, SEAT, Cupra.

Run: docker exec autospare_backend python /app/maintenance/backfill_vw_fitment.py
"""
import asyncio, json, re, sys, uuid
import asyncpg
import urllib.parse as up

INPUT_JSON = "/opt/autosparefinder/champion_motors_parts.json"
DATABASE_URL = (
    "postgresql://autospare:e4b79d75ca640dbe7f259618f078b82f21573e419308f668beed5e20b26b1d43"
    "@postgres_catalog:5432/autospare"
)

YEAR_RE = re.compile(r'^(\d{4})-(\d{4})\s+(.+)$')

# Hebrew → English model name normalization
HE_MODEL_MAP = {
    'גולף':    'Golf',        'פולו':     'Polo',
    'טיגואן':  'Tiguan',      'פאסאט':    'Passat',
    "ג'טה":    'Jetta',       'ג׳טה':     'Jetta',
    'טוראן':   'Touran',      'קאדי':     'Caddy',
    'חיפושית': 'Beetle',      'אפ':       'Up',
    'שירוקו':  'Scirocco',    'טוארג':    'Touareg',
    'פֵּיָאטוֹן': 'Phaeton',   'קרוסבורד': 'CrossBlue',
    'טרק':     'T-Roc',       'איוס':     'Eos',
    'ארטיאון': 'Arteon',      'גולף פלוס':'Golf Plus',
    'יאט':     'Jetta',       'איביזה':   'Ibiza',
    'לאון':    'Leon',         'אטקה':     'Ateca',
    'טוואסקן': 'Tavascan',    'אלהמברה':  'Alhambra',
    'פורמנטור':'Formentor',   'אוקטביה':  'Octavia',
    'סופרב':   'Superb',      'פביה':     'Fabia',
    'קמיק':    'Kamiq',        'קרוק':     'Karoq',
    'קודיאק':  'Kodiaq',       'יטי':      'Yeti',
    'A1 אודי': 'A1',          'A3 אודי':  'A3',
    'A4 אודי': 'A4',          'A5 אודי':  'A5',
    'A6 אודי': 'A6',          'A7 אודי':  'A7',
    'A8 אודי': 'A8',          'Q2 אודי':  'Q2',
    'Q3 אודי': 'Q3',          'Q4 ETRON': 'Q4 e-tron',
    'Q5 אודי': 'Q5',          'Q6-ETRON': 'Q6 e-tron',
    'Q7 אודי': 'Q7',          'Q8 אודי':  'Q8',
    'ETRON רכב חשמלי': 'e-tron',
    'Q4 ETRON אאודי': 'Q4 e-tron',
    'Q4 ETRON אודי':  'Q4 e-tron',
}

# VW group makes normalization
MAKE_NORM = {
    'vw': 'Volkswagen', 'volkswagen': 'Volkswagen',
    'מסחריות vw': 'Volkswagen', 'מסחריות': 'Volkswagen',
    'אודי': 'Audi', 'audi': 'Audi',
    'סקודה': 'Skoda', 'skoda': 'Skoda',
    'סיאט': 'SEAT', 'seat': 'SEAT',
    'קופרה': 'Cupra', 'cupra': 'Cupra',
}

def norm_make(raw):
    r = raw.strip().lower()
    if r in MAKE_NORM: return MAKE_NORM[r]
    for k, v in MAKE_NORM.items():
        if k in r: return v
    return None

def split_makes(raw):
    return [norm_make(m.strip()) for m in re.split(r'/', raw) if norm_make(m.strip())]

def norm_model_name(raw):
    """Translate Hebrew model names to English where possible."""
    # Try full match first
    if raw in HE_MODEL_MAP: return HE_MODEL_MAP[raw]
    # Try partial replacements
    result = raw
    for he, en in HE_MODEL_MAP.items():
        result = result.replace(he, en)
    # Remove trailing brand suffixes
    result = re.sub(r'\s+(VW|XW|אודי|אאודי|Audi|סיאט|SEAT|סקודה|Skoda|CUPRA)\s*$', '', result, flags=re.IGNORECASE)
    return result.strip()

def detect_mfr_from_model(model_name: str):
    """Try to detect manufacturer from model name text."""
    ml = model_name.lower()
    if any(x in ml for x in ['vw', ' גולף', 'פולו', 'טיגואן', 'פאסאט', "ג'טה", 'טוראן',
                              'קאדי', 'חיפושית', 'id3', 'id4', 'id5', 'id7',
                              'teramont', 't-cross', 'amarok', 'touareg',
                              'transporter', 'multivan', 'caravelle']):
        return 'Volkswagen'
    if any(x in ml for x in ['אודי', 'אאודי', 'audi', ' a1 ', ' a3 ', ' a4 ', ' a5 ',
                              ' a6 ', ' a7 ', ' a8 ', ' q2', ' q3', ' q5', ' q6', ' q7', ' q8',
                              'etron', 'e-tron', 'rs ', 'tt ']):
        return 'Audi'
    if any(x in ml for x in ['סיאט', 'seat', 'ibiza', 'leon', 'ateca', 'tavascan', 'formentor', 'alhambra', 'טוואסקן', 'איביזה', 'לאון']):
        return 'SEAT'
    if any(x in ml for x in ['סקודה', 'skoda', 'octavia', 'superb', 'fabia', 'kamiq', 'kodiaq', 'karoq', 'yeti', 'אוקטביה', 'סופרב', 'פביה', 'קמיק', 'קודיאק']):
        return 'Skoda'
    if any(x in ml for x in ['cupra', 'קופרה']):
        return 'Cupra'
    return None

async def run():
    print(f"[JSON] Loading {INPUT_JSON}...")
    data = json.loads(open(INPUT_JSON, encoding='utf-8').read())
    all_parts = data['parts'] if isinstance(data, dict) else data
    print(f"[JSON] {len(all_parts)} total parts")

    p = up.urlparse(DATABASE_URL)
    conn = await asyncpg.connect(
        host=p.hostname, port=p.port or 5432,
        database=p.path.lstrip('/'), user=p.username, password=p.password,
    )
    print("[DB] Connected")

    # Build OEM → part_id map for VW group parts
    rows = await conn.fetch("""
        SELECT id, oem_number, manufacturer
        FROM parts_catalog
        WHERE manufacturer IN ('Volkswagen','Audi','Skoda','SEAT','Cupra')
          AND is_active = TRUE
          AND oem_number IS NOT NULL AND oem_number != ''
    """)
    # Key: (oem_number_clean, manufacturer) → part_id
    oem_map = {}
    for r in rows:
        key = (re.sub(r'\s+','',r['oem_number']).upper(), r['manufacturer'])
        oem_map[key] = str(r['id'])
    print(f"[DB] Loaded {len(oem_map)} VW-group catalog OEM entries")

    # Existing PVF part_ids (skip if already has fitment from this source)
    existing_pvf = set(str(r['part_id']) for r in await conn.fetch(
        "SELECT DISTINCT part_id FROM part_vehicle_fitment "
        "WHERE manufacturer IN ('Volkswagen','Audi','Skoda','SEAT','Cupra') "
        "AND notes='champion_motors'"
    ))
    print(f"[DB] {len(existing_pvf)} parts already have CM fitment — will skip")

    stats = {'processed':0,'inserted':0,'skipped_generic':0,'skipped_no_oem':0,'skipped_no_catalog':0,'errors':0}
    batch = []

    async def flush_batch():
        if not batch: return
        for row in batch:
            try:
                await conn.execute("""
                    INSERT INTO part_vehicle_fitment(
                        id, part_id, manufacturer, model, year_from, year_to,
                        manufacturer_id, notes, created_at
                    ) VALUES($1,$2,$3::varchar,$4,$5,$6,
                        COALESCE((SELECT id FROM car_brands WHERE name=$3::varchar LIMIT 1),
                                 '00000000-0000-0000-0000-000000000000'::uuid),
                        'champion_motors', NOW())
                    ON CONFLICT(part_id,manufacturer,model,year_from) DO NOTHING
                """, str(uuid.uuid4()), row['part_id'], row['manufacturer'],
                     row['model'], row['year_from'], row['year_to'])
                stats['inserted'] += 1
            except Exception as e:
                print(f"  [ERR] {row}: {e}")
                stats['errors'] += 1
        batch.clear()

    for part in all_parts:
        raw_make = str(part.get('vehicle_make') or '')
        makes = split_makes(raw_make)
        if not makes:
            continue

        model_raw = str(part.get('model') or '').strip()
        if not model_raw or model_raw == 'מרובה דגמים':
            stats['skipped_generic'] += 1
            continue

        m = YEAR_RE.match(model_raw)
        if not m:
            stats['skipped_generic'] += 1
            continue

        y1, y2, model_name_raw = int(m.group(1)), int(m.group(2)), m.group(3).strip()
        year_from, year_to = min(y1, y2), max(y1, y2)
        model_name = norm_model_name(model_name_raw)

        # Determine which manufacturer this fitment belongs to
        model_mfr = detect_mfr_from_model(model_name_raw)

        oem_raw = str(part.get('oem_number') or '').strip()
        oem_clean = re.sub(r'\s+', '', oem_raw).upper()
        if not oem_clean:
            stats['skipped_no_oem'] += 1
            continue

        stats['processed'] += 1

        # Try to find catalog rows for each brand in composite make
        for make_brand in makes:
            pid = oem_map.get((oem_clean, make_brand))
            if not pid:
                stats['skipped_no_catalog'] += 1
                continue
            if pid in existing_pvf:
                continue

            # Assign fitment manufacturer: use model-detected if available, else catalog brand
            fitment_mfr = model_mfr or make_brand

            batch.append({
                'part_id':    pid,
                'manufacturer': fitment_mfr,
                'model':      model_name,
                'year_from':  year_from,
                'year_to':    year_to,
            })

        if len(batch) >= 50:
            await flush_batch()

    await flush_batch()

    # Now merge PVF → compatible_vehicles
    print("\n[MERGE] Updating compatible_vehicles from new PVF rows...")
    result = await conn.execute("""
        UPDATE parts_catalog pc
        SET compatible_vehicles = sub.cv
        FROM (
            SELECT pvf.part_id,
                jsonb_agg(DISTINCT jsonb_build_object(
                    'manufacturer', pvf.manufacturer,
                    'model',        pvf.model,
                    'year_from',    pvf.year_from,
                    'year_to',      pvf.year_to,
                    'source',       pvf.notes
                ) ORDER BY jsonb_build_object(
                    'manufacturer', pvf.manufacturer,
                    'model',        pvf.model,
                    'year_from',    pvf.year_from,
                    'year_to',      pvf.year_to,
                    'source',       pvf.notes
                )) AS cv
            FROM part_vehicle_fitment pvf
            WHERE pvf.manufacturer IN ('Volkswagen','Audi','Skoda','SEAT','Cupra')
              AND pvf.notes = 'champion_motors'
            GROUP BY pvf.part_id
        ) sub
        WHERE pc.id = sub.part_id
          AND pc.manufacturer IN ('Volkswagen','Audi','Skoda','SEAT','Cupra')
    """)
    print(f"[MERGE] {result}")

    await conn.close()

    print(f"\n=== DONE ===")
    print(f"  Parts processed       : {stats['processed']}")
    print(f"  PVF rows inserted     : {stats['inserted']}")
    print(f"  Skipped generic model : {stats['skipped_generic']}")
    print(f"  Skipped no OEM        : {stats['skipped_no_oem']}")
    print(f"  Skipped no catalog    : {stats['skipped_no_catalog']}")
    print(f"  Errors                : {stats['errors']}")

    # Final coverage report
    p2 = up.urlparse(DATABASE_URL)
    conn2 = await asyncpg.connect(host=p2.hostname, port=p2.port or 5432,
        database=p2.path.lstrip('/'), user=p2.username, password=p2.password)
    rows = await conn2.fetch("""
        SELECT manufacturer,
               COUNT(*) as total,
               COUNT(*) FILTER (WHERE jsonb_array_length(compatible_vehicles) > 0) as has_cv,
               ROUND(100.0 * COUNT(*) FILTER (WHERE jsonb_array_length(compatible_vehicles) > 0)/COUNT(*),1) as pct
        FROM parts_catalog
        WHERE manufacturer IN ('Volkswagen','Audi','Skoda','SEAT','Cupra')
          AND is_active=TRUE
        GROUP BY manufacturer ORDER BY total DESC
    """)
    await conn2.close()
    print("\n=== VW Group CV Coverage ===")
    for r in rows:
        print(f"  {r['manufacturer']:12} total={r['total']}  has_cv={r['has_cv']}  {r['pct']}%")

if __name__ == '__main__':
    asyncio.run(run())
