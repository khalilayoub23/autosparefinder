#!/usr/bin/env python3
"""
Import Champion Motors VW-group parts from pipe-delimited stdin.
Format per line: oem|vehicle_make|is_orig|price_cents|name

Usage:
  echo "OEM|make|1|50000|part name" | python3 cm_pipe_import.py
  python3 cm_pipe_import.py < batch.pipe
"""
import asyncio, asyncpg, uuid, sys, re

DB = "postgresql://autospare:e4b79d75ca640dbe7f259618f078b82f21573e419308f668beed5e20b26b1d43@localhost:5432/autospare"

BRAND_IDS = {
    'Volkswagen': '04877cea-0889-4b57-978a-cff0a8f1ed25',
    'Audi':       '4a718e3c-5b47-478d-9c62-0b6b5135593e',
    'SEAT':       'ebb4521b-6742-4cc2-b1d0-207903ea085a',
    'Skoda':      'e062ba07-930c-489f-b43e-48bf90a42d11',
    'Cupra':      '51fcef2d-5756-40b3-823e-0f84984a2e5d',
}

BRAND_MAP = {
    'אודי': 'Audi', 'audi': 'Audi',
    'skoda': 'Skoda', 'סקודה': 'Skoda',
    'seat': 'SEAT', 'סיאט': 'SEAT',
    'cupra': 'Cupra', 'קופרה': 'Cupra',
    'vw': 'Volkswagen', 'מסחריות vw': 'Volkswagen',
    'מסחריות': 'Volkswagen',
}

OEM_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9 \-\.\/]{1,59}$')


def resolve_brands(make_str):
    tokens = [m.strip() for m in make_str.split('/') if m.strip()] if '/' in make_str else (
        [make_str.strip()] if make_str.strip() else [])
    if not tokens:
        return ['Volkswagen']
    brands = set()
    for token in tokens:
        t = token.strip().lower()
        brand = BRAND_MAP.get(t)
        if not brand:
            for k, v in BRAND_MAP.items():
                if k in t:
                    brand = v
                    break
        if brand:
            brands.add(brand)
    return list(brands) if brands else ['Volkswagen']


async def run():
    lines = [l.strip() for l in sys.stdin.read().splitlines() if l.strip()]
    if not lines:
        print("inserted:0 skipped:0")
        return

    conn = await asyncpg.connect(DB)
    existing_skus = set(
        r['sku'] for r in await conn.fetch(
            "SELECT sku FROM parts_catalog WHERE manufacturer IN "
            "('Volkswagen','Audi','Skoda','SEAT','Cupra') AND is_active=true"
        )
    )

    batch = []
    skipped = 0

    for line in lines:
        parts = line.split('|')
        if len(parts) < 5:
            continue
        oem, make, orig_s, price_s, name = parts[0], parts[1], parts[2], parts[3], '|'.join(parts[4:])
        oem = oem.strip()
        if not oem or not OEM_RE.match(oem):
            continue
        is_orig = (orig_s.strip() == '1')
        try:
            price = int(price_s.strip()) / 100.0
        except ValueError:
            price = 0.0
        aft_tier = None if is_orig else 'OE_equivalent'
        name = name.strip() or oem

        brands = resolve_brands(make)
        for brand in brands:
            sku = f"{brand[:4].upper()}-CM-{oem[:30]}"
            if sku in existing_skus:
                skipped += 1
                continue
            existing_skus.add(sku)
            batch.append((
                str(uuid.uuid4()), sku, name, name,
                brand, BRAND_IDS[brand], oem,
                'New', price,
                'original' if is_orig else 'oe_equivalent',
                aft_tier,
            ))

    inserted = 0
    if batch:
        await conn.executemany("""
            INSERT INTO parts_catalog
                (id, sku, name, description, manufacturer, manufacturer_id, oem_number,
                 part_condition, base_price, part_type, aftermarket_tier,
                 is_active, created_at, updated_at)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,TRUE,NOW(),NOW())
            ON CONFLICT (sku) DO NOTHING
        """, batch)
        inserted = len(batch)

    await conn.close()
    print(f"inserted:{inserted} skipped:{skipped}")


if __name__ == '__main__':
    asyncio.run(run())
