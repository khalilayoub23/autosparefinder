#!/usr/bin/env python3
"""
Import Champion Motors fitment data from pipe-delimited stdin into part_vehicle_fitment.
Format per line: oem|model_string|make_string
"""
import sys
import re
import psycopg2

DB_URL = "postgresql://autospare:e4b79d75ca640dbe7f259618f078b82f21573e419308f668beed5e20b26b1d43@localhost:5432/autospare"

BRAND_HE = {
    'אודי': 'Audi', 'סקודה': 'Skoda', 'סיאט': 'SEAT',
    'vw': 'Volkswagen', 'VW': 'Volkswagen', 'קופרה': 'Cupra',
    'מסחריות': 'Volkswagen',
}

MODEL_HE = {
    'אוקטביה': 'Octavia', 'גולף': 'Golf', 'פאסאט': 'Passat',
    'טיגואן': 'Tiguan', 'קודיאק': 'Kodiaq', 'קאדי': 'Caddy',
    'קרפטר': 'Crafter', 'פולו': 'Polo', 'ליאון': 'Leon',
    'סופרב': 'Superb', 'קארוק': 'Karoq', 'חיפושית': 'Beetle',
    'טרנספורטר': 'Transporter', 'פאבייה': 'Fabia',
    'קורדובה': 'Cordoba', 'אלהמברה': 'Alhambra',
    'ארונה': 'Arona', 'אטקה': 'Ateca', 'איביזה': 'Ibiza',
    'פורמנטור': 'Formentor', 'טולדו': 'Toledo',
    'סקאלה': 'Scala', 'קאמיק': 'Kamiq', 'ענייאק': 'Enyaq',
    'טוארג': 'Touareg', 'שרן': 'Sharan', 'לופו': 'Lupo',
    'ווינט': 'Vento', 'קרבל': 'Caravelle', 'פואו': 'Fox',
    'אאופ': 'Up', 'עמרוק': 'Amarok', 'אלטאה': 'Altea',
    'אקסיאו': 'Exeo', 'מי': 'Mii', 'טארקו': 'Tarraco',
    'ראפיד': 'Rapid', 'רומסטר': 'Roomster', 'ייטי': 'Yeti',
    'מולטיבן': 'Multivan', 'קאליפורניה': 'California',
    'טוראן': 'Touran', 'אניאק': 'Enyaq', 'ENYAQ': 'Enyaq',
    'IDBUZZ': 'ID.Buzz', 'ID7': 'ID.7', 'ID4': 'ID.4',
    'TERAMONT': 'Teramont', 'ETRON': 'e-tron', 'RSQ98': 'RSQ8',
    'RSQ3': 'RSQ3', 'ETRONGT': 'e-tron GT',
}

MAKE_MAP = {
    'אודי': 'Audi', 'audi': 'Audi',
    'skoda': 'Skoda', 'סקודה': 'Skoda',
    'seat': 'SEAT', 'סיאט': 'SEAT',
    'cupra': 'Cupra', 'קופרה': 'Cupra',
    'vw': 'Volkswagen', 'מסחריות vw': 'Volkswagen', 'מסחריות': 'Volkswagen',
}

MFRID = {
    'Volkswagen': '04877cea-0889-4b57-978a-cff0a8f1ed25',
    'Audi':       '4a718e3c-5b47-478d-9c62-0b6b5135593e',
    'SEAT':       'ebb4521b-6742-4cc2-b1d0-207903ea085a',
    'Skoda':      'e062ba07-930c-489f-b43e-48bf90a42d11',
    'Cupra':      '51fcef2d-5756-40b3-823e-0f84984a2e5d',
}


def parse_model_string(s):
    if s == 'מרובה דגמים':
        return 1990, 2030, 'General'
    if 'דגמים ישנים' in s:
        return 1980, 2005, 'General'

    m = re.search(r'(\d{4})-(\d{4})', s)
    if not m:
        return None
    y1, y2 = int(m.group(1)), int(m.group(2))
    year_from, year_to = min(y1, y2), max(y1, y2)

    rest = (s[:m.start()] + ' ' + s[m.end():]).strip()
    model_tokens = []
    for token in rest.split():
        if token in BRAND_HE or token.lower() in BRAND_HE:
            continue
        en = MODEL_HE.get(token, token)
        model_tokens.append(en)

    model_name = ' '.join(model_tokens).strip() or 'General'
    return year_from, year_to, model_name


def parse_makes(make_str):
    makes = []
    for part in re.split(r'\s*/\s*', make_str):
        key = part.strip().lower()
        for k, v in MAKE_MAP.items():
            if k.lower() == key:
                if v not in makes:
                    makes.append(v)
                break
    return makes


def main():
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()

    inserted = 0
    skipped = 0
    total = 0

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        parts = line.split('|', 2)
        oem = parts[0].strip()
        model_str = parts[1].strip() if len(parts) > 1 else ''
        make_str = parts[2].strip() if len(parts) > 2 else ''
        total += 1

        parsed = parse_model_string(model_str)
        if not parsed:
            skipped += 1
            continue

        year_from, year_to, model_name = parsed

        makes = parse_makes(make_str)
        if not makes:
            makes = ['Volkswagen']

        cur.execute(
            "SELECT id FROM parts_catalog WHERE oem_number = %s AND is_active = true",
            (oem,)
        )
        part_rows = cur.fetchall()
        if not part_rows:
            skipped += 1
            continue

        for (part_id,) in part_rows:
            for mfr in makes:
                mfr_id = MFRID.get(mfr)
                if not mfr_id:
                    continue
                try:
                    cur.execute("""
                        INSERT INTO part_vehicle_fitment
                            (part_id, manufacturer, model, year_from, year_to, manufacturer_id)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (part_id, manufacturer, model, year_from) DO NOTHING
                    """, (str(part_id), mfr, model_name, year_from, year_to, mfr_id))
                    if cur.rowcount > 0:
                        inserted += 1
                    else:
                        skipped += 1
                except Exception as e:
                    conn.rollback()
                    skipped += 1

        if total % 200 == 0:
            conn.commit()

    conn.commit()
    cur.close()
    conn.close()
    print(f"inserted:{inserted} skipped:{skipped} total:{total}")


if __name__ == '__main__':
    main()
