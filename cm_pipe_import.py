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
    'אודי': 'Audi', 'audi': 'Audi', 'skoda': 'Skoda', 'סקודה': 'Skoda',
    'seat': 'SEAT', 'סיאט': 'SEAT', 'cupra': 'Cupra', 'קופרה': 'Cupra',
    'vw': 'Volkswagen', 'מסחריות vw': 'Volkswagen', 'מסחריות': 'Volkswagen',
}
OEM_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9 \-\.\/]{1,59}$')

def resolve_brands(make):
    tokens = [m.strip() for m in make.split('/') if m.strip()] if '/' in make else ([make.strip()] if make.strip() else [])
    if not tokens: return ['Volkswagen']
    brands = set()
    for t in tokens:
        b = BRAND_MAP.get(t.lower())
        if not b:
            for k,v in BRAND_MAP.items():
                if k in t.lower(): b=v; break
        if b: brands.add(b)
    return list(brands) if brands else ['Volkswagen']

async def run():
    lines = [l.strip() for l in sys.stdin.read().splitlines() if l.strip()]
    if not lines: print("inserted:0 skipped:0"); return
    conn = await asyncpg.connect(DB)
    existing = set(r['sku'] for r in await conn.fetch(
        "SELECT sku FROM parts_catalog WHERE manufacturer IN ('Volkswagen','Audi','Skoda','SEAT','Cupra') AND is_active=true"))
    batch=[]; skipped=0
    for line in lines:
        p = line.split('|')
        if len(p) < 5: continue
        oem=p[0].strip()
        if not oem or not OEM_RE.match(oem): continue
        make,orig_s,price_s,name = p[1],p[2],p[3],'|'.join(p[4:]).strip()
        is_orig=(orig_s.strip()=='1')
        try: price=int(price_s.strip())/100.0
        except: price=0.0
        for brand in resolve_brands(make):
            sku=f"{brand[:4].upper()}-CM-{oem[:30]}"
            if sku in existing: skipped+=1; continue
            existing.add(sku)
            batch.append((str(uuid.uuid4()),sku,name or oem,name or oem,brand,BRAND_IDS[brand],oem,'New',price,'original' if is_orig else 'oe_equivalent',None if is_orig else 'OE_equivalent'))
    inserted=0
    for i in range(0,len(batch),25):
        chunk=batch[i:i+25]
        await conn.executemany("INSERT INTO parts_catalog(id,sku,name,name_he,manufacturer,manufacturer_id,oem_number,part_condition,base_price,part_type,aftermarket_tier,is_active,created_at,updated_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,true,NOW(),NOW()) ON CONFLICT(sku) DO NOTHING", chunk)
        inserted+=len(chunk)
    await conn.close()
    print(f"inserted:{inserted} skipped:{skipped} total:{len(lines)}")

asyncio.run(run())
