#!/usr/bin/env python3
"""
Import Champion Motors VW-group parts from NDJSON relay file.
OEM numbers may have Hebrew vehicle_make suffix - strip it.
"""
import json, re, sys, asyncio
from datetime import datetime
import asyncpg

DB_URL = "postgresql://autospare:e4b79d75ca640dbe7f259618f078b82f21573e419308f668beed5e20b26b1d43@localhost:5432/autospare"
SUPPLIER_ID = "9f7c3f7b-5d58-4dba-b67a-bcba3890d827"  # Champion Motors

# Hebrew character range
HEB_RE = re.compile(r'[\u05d0-\u05ea]')

VW_BRANDS = {
    'אודי': ('Audi', '4a718e3c-5b47-478d-9c62-0b6b5135593e'),
    'audi': ('Audi', '4a718e3c-5b47-478d-9c62-0b6b5135593e'),
    'סיאט': ('SEAT', 'ebb4521b-6742-4cc2-b1d0-207903ea085a'),
    'seat': ('SEAT', 'ebb4521b-6742-4cc2-b1d0-207903ea085a'),
    'סקודה': ('Skoda', 'e062ba07-930c-489f-b43e-48bf90a42d11'),
    'skoda': ('Skoda', 'e062ba07-930c-489f-b43e-48bf90a42d11'),
    'vw': ('Volkswagen', '04877cea-0889-4b57-978a-cff0a8f1ed25'),
    'מסחריות': ('Volkswagen', '04877cea-0889-4b57-978a-cff0a8f1ed25'),
    'קופרה': ('Cupra', '51fcef2d-5756-40b3-823e-0f84984a2e5d'),
    'cupra': ('Cupra', '51fcef2d-5756-40b3-823e-0f84984a2e5d'),
}

def extract_oem_and_make(raw_oem: str):
    """Split 'ABC123Dאודי' → ('ABC123D', 'אודי')"""
    m = HEB_RE.search(raw_oem)
    if not m:
        return raw_oem.strip(), None
    oem = raw_oem[:m.start()].strip()
    make = raw_oem[m.start():].strip()
    return oem, make

def detect_manufacturer(make_str: str, model_str: str):
    """Return list of (manufacturer_name, brand_id) from make/model strings."""
    combined = (make_str or '') + ' ' + (model_str or '')
    combined_lower = combined.lower()
    results = []
    seen = set()
    for key, val in VW_BRANDS.items():
        if key in combined or key in combined_lower:
            if val[0] not in seen:
                results.append(val)
                seen.add(val[0])
    if not results:
        # Default: Volkswagen
        return [('Volkswagen', '04877cea-0889-4b57-978a-cff0a8f1ed25')]
    return results

def sku_for(oem: str, mfr_name: str) -> str:
    prefix_map = {
        'Volkswagen': 'VW', 'Audi': 'AUDI', 'Skoda': 'SKODA',
        'SEAT': 'SEAT', 'Cupra': 'CUPRA',
    }
    p = prefix_map.get(mfr_name, 'VW')
    return f"{p}-CM-{oem[:80]}"

async def import_ndjson(ndjson_file: str):
    conn = await asyncpg.connect(DB_URL)
    total_rows = 0
    inserted = 0
    skipped = 0
    errors = []

    with open(ndjson_file, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    # Filter valid JSON lines
    records = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith('COUNT:') or line.startswith('STATS:'):
            continue
        try:
            records.append(json.loads(line))
        except:
            pass

    print(f"[CM Import] {len(records)} records to process")

    batch = []
    for rec in records:
        total_rows += 1
        raw_oem = rec.get('o', '')
        if not raw_oem:
            continue
        oem, make_suffix = extract_oem_and_make(raw_oem)
        if not oem or len(oem) < 3:
            continue

        name_he = rec.get('n', '')[:200]
        is_original = bool(rec.get('t', 0))
        model_str = rec.get('m', '') or ''
        price_ils = round(rec.get('p', 0) / 100, 2)

        # Determine manufacturer(s)
        manufacturers = detect_manufacturer(make_suffix or '', model_str)

        for mfr_name, brand_id in manufacturers:
            sku = sku_for(oem, mfr_name)
            batch.append({
                'oem': oem,
                'name_he': name_he or oem,
                'mfr': mfr_name,
                'price': price_ils,
                'sku': sku,
                'is_original': is_original,
            })

    print(f"[CM Import] {len(batch)} insert candidates")

    # Batch insert parts_catalog
    chunk_size = 50
    for i in range(0, len(batch), chunk_size):
        chunk = batch[i:i+chunk_size]
        async with conn.transaction():
            for p in chunk:
                try:
                    # Upsert part
                    row = await conn.fetchrow("""
                        INSERT INTO parts_catalog
                          (oem_number, name_he, manufacturer, price_ils, sku,
                           part_origin, is_active, created_at, updated_at)
                        VALUES ($1,$2,$3,$4,$5,$6,true,NOW(),NOW())
                        ON CONFLICT (sku) DO UPDATE SET
                          price_ils = EXCLUDED.price_ils,
                          updated_at = NOW()
                        RETURNING id
                    """,
                        p['oem'], p['name_he'], p['mfr'], p['price'],
                        p['sku'],
                        'original' if p['is_original'] else 'aftermarket'
                    )
                    part_id = row['id']
                    # Upsert supplier_parts
                    await conn.execute("""
                        INSERT INTO supplier_parts (part_id, supplier_id, supplier_sku, price, is_active, created_at, updated_at)
                        VALUES ($1,$2,$3,$4,true,NOW(),NOW())
                        ON CONFLICT DO NOTHING
                    """, part_id, SUPPLIER_ID, p['sku'], p['price'])
                    inserted += 1
                except Exception as e:
                    errors.append(str(e)[:80])
                    skipped += 1

    await conn.close()
    print(f"[CM Import] Done: {inserted} inserted/updated, {skipped} skipped")
    if errors:
        print(f"  First 5 errors: {errors[:5]}")
    return inserted

if __name__ == '__main__':
    ndjson = sys.argv[1] if len(sys.argv) > 1 else '/opt/autosparefinder/cm_vw_full.ndjson'
    count = asyncio.run(import_ndjson(ndjson))
    print(f"[DONE] {count} parts processed")
