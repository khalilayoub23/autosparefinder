#!/usr/bin/env python3
"""
Extract Kia vehicle fitment from part names (Hebrew & English).
kia-israel.co.il parts embed the model in the Hebrew description.
Also handles English model names from other sources.
"""
import asyncio
import re
import os
import asyncpg

DB = os.environ.get('DATABASE_URL', '').replace('postgresql+asyncpg://', 'postgresql://')

# (pattern, model_en, year_from, year_to)
# Ordered: longest/most-specific match first
KIA_MODEL_PATTERNS = [
    # Hebrew model names
    (r'ספורטג\'|ספורטג(?:[\'`])?', 'Sportage', 2000, None),
    (r'סורנטו', 'Sorento', 2000, None),
    (r'קרנ[ס׳\'`]?06|קרנ[ס׳\'`]\s*06', 'Carens', 2004, 2013),
    (r'קרנ[ס׳\'`]07|קרנ[ס׳\'`]\s*07', 'Carens', 2004, 2013),
    (r'קרנ[ס׳]', 'Carens', 2000, None),
    (r'קרניבל', 'Carnival', 2000, None),
    (r'נירו\s*פלוס\s*2[0-9]', 'Niro Plus', 2021, None),
    (r'נירו\s*פלוס', 'Niro Plus', 2021, None),
    (r'נירו\s*2[0-9]', 'Niro', 2016, None),
    (r'נירו', 'Niro', 2016, None),
    (r'פיקנטו', 'Picanto', 2000, None),
    (r'פורטה', 'Forte', 2008, None),
    (r'סטינגר', 'Stinger', 2017, None),
    (r'סלטוס', 'Seltos', 2019, None),
    (r'טלוריד', 'Telluride', 2019, None),
    (r'אופטימה', 'Optima', 2000, None),
    (r'K900|ק\'900|קיי900', 'K900', 2012, None),
    (r'\bK5\b|קיי5\b', 'K5', 2020, None),
    (r'\bK4\b|קיי4\b', 'K4', 2023, None),
    (r'\bK8\b|קיי8\b', 'K8', 2021, None),
    (r'קדנ[\'`]?ז|cadenza', 'Cadenza', 2010, None),
    (r'סאול|^\s*soul|,\s*soul|soul\s*\d', 'Soul', 2008, None),
    (r'צ\'יד|ceed\b', 'Ceed', 2006, None),
    (r'ריו\s*\+|\bRio\s*\+|\bריו\s*פלוס', 'Rio', 2011, None),
    (r'ריו\s*2[0-9]|rio\s*2[0-9]', 'Rio', 2005, None),
    (r'ריו\b|,ריו|\brio\b', 'Rio', 2000, None),
    (r'EV6\b', 'EV6', 2021, None),
    (r'EV9\b', 'EV9', 2023, None),
    (r'EV5\b', 'EV5', 2024, None),
    # English model names (case-insensitive)
    (r'\bsportage\b', 'Sportage', 2000, None),
    (r'\bsorento\b', 'Sorento', 2000, None),
    (r'\bcarnival\b', 'Carnival', 2000, None),
    (r'\bcarens\b', 'Carens', 2000, None),
    (r'\bniro\b', 'Niro', 2016, None),
    (r'\bpicanto\b', 'Picanto', 2000, None),
    (r'\bforte\b', 'Forte', 2008, None),
    (r'\bstinger\b', 'Stinger', 2017, None),
    (r'\bseltos\b', 'Seltos', 2019, None),
    (r'\btelluride\b', 'Telluride', 2019, None),
    (r'\boptima\b', 'Optima', 2000, None),
    (r'\bsoul\b', 'Soul', 2008, None),
]

# Compile patterns
_COMPILED = [(re.compile(p, re.IGNORECASE | re.UNICODE), m, yf, yt)
             for p, m, yf, yt in KIA_MODEL_PATTERNS]


def extract_models(name: str) -> list[dict]:
    """Extract model names from part name/description."""
    if not name:
        return []
    found = []
    seen = set()
    for pat, model, year_from, year_to in _COMPILED:
        if pat.search(name):
            # Try to extract specific year from name
            yr_match = re.search(r'\b(20\d\d|0[0-9]|1[0-9])\b', name)
            yf = year_from
            yt = year_to
            if yr_match:
                y_str = yr_match.group(1)
                y = int(y_str) if len(y_str) == 4 else (2000 + int(y_str))
                if 2000 <= y <= 2030:
                    yf = y
                    yt = y + 6  # typical model run

            key = (model, yf)
            if key not in seen:
                seen.add(key)
                found.append({"model": model, "year_from": yf, "year_to": yt})
    return found


async def run():
    conn = await asyncpg.connect(DB)

    mfr = await conn.fetchrow("SELECT id FROM car_brands WHERE LOWER(name)='kia' LIMIT 1")
    if not mfr:
        print("Kia not in car_brands"); return
    mfr_id = str(mfr['id'])

    # Get ALL Kia parts without fitment (including Wurth — we'll skip those by checking no model match)
    parts = await conn.fetch("""
        SELECT id, name, oem_number, sku
        FROM parts_catalog
        WHERE manufacturer = 'Kia'
          AND is_active = true
          AND name IS NOT NULL
          AND sku NOT LIKE 'KIA-WURTH%'
          AND NOT EXISTS (
              SELECT 1 FROM part_vehicle_fitment WHERE part_id = parts_catalog.id
          )
    """)

    print(f"Kia parts without fitment (excl. Wurth): {len(parts):,}")

    inserted = 0
    skipped_no_model = 0
    errors = 0

    for i, part in enumerate(parts):
        pid = str(part['id'])
        name = str(part['name'] or '')

        models = extract_models(name)

        if not models:
            skipped_no_model += 1
            continue

        for entry in models:
            try:
                await conn.execute("""
                    INSERT INTO part_vehicle_fitment (
                        id, part_id, manufacturer, manufacturer_id,
                        model, year_from, year_to, notes,
                        created_at, updated_at
                    ) VALUES (
                        gen_random_uuid(), $1::uuid, 'Kia', $2::uuid,
                        $3, $4, $5,
                        'Fitment extracted from part name (kia-israel.co.il + catalog data)',
                        NOW(), NOW()
                    ) ON CONFLICT (part_id, manufacturer, model, year_from) DO NOTHING
                """, pid, mfr_id, entry["model"], entry["year_from"], entry.get("year_to"))
                inserted += 1
            except Exception as e:
                errors += 1
                if errors <= 3:
                    print(f"  error: {e}")

        if (i + 1) % 10000 == 0:
            print(f"  {i+1:,}/{len(parts):,} processed, fitment_rows={inserted:,} skipped={skipped_no_model:,}")

    # Verify results
    after = await conn.fetchval("""
        SELECT COUNT(DISTINCT pc.id) FROM parts_catalog pc
        JOIN part_vehicle_fitment pvf ON pvf.part_id=pc.id
        WHERE pc.manufacturer='Kia' AND pc.sku NOT LIKE 'KIA-WURTH%'
    """)

    print(f"\n=== KIA FITMENT FROM NAMES ===")
    print(f"  Parts processed: {len(parts):,}")
    print(f"  Fitment rows inserted: {inserted:,}")
    print(f"  Parts with no model in name: {skipped_no_model:,}")
    print(f"  Errors: {errors}")
    print(f"  Real Kia parts with fitment now: {after:,}")

    await conn.close()


if __name__ == "__main__":
    asyncio.run(run())
