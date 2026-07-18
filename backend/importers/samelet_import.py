#!/usr/bin/env python3
"""Samelet.com importer — flat 2-char coverage, no deep recursion."""
import asyncio, asyncpg, requests, time, re, os, string

DB_URL = "postgresql://autospare:e4b79d75ca640dbe7f259618f078b82f21573e419308f668beed5e20b26b1d43@postgres_catalog:5432/autospare"

BRANDS = [
    ("alfaromeo","Alfa Romeo","AR"),
    ("jeep","Jeep","JP"),
    ("fiat","Fiat","FI"),
    ("ram","RAM","RM"),
    ("subaru","Subaru","SU"),
    ("abarth","Abarth","AB"),
    ("iveco","Iveco","IV"),
    ("hongqi","Hongqi","HQ"),
    ("wey","WEY","WY"),
]

SINGLE = os.environ.get("SINGLE_BRAND","")
API_CAP = 29
UPPER   = string.ascii_uppercase
ALPHANUM= string.ascii_letters + string.digits
HEBREW  = list("אבגדהוזחטיכלמנסעפצקרשתךםןףץ")

CATEGORY_KW = [
    ("oil filter","Filters"),("air filter","Filters"),("fuel filter","Filters"),("cabin filter","Filters"),("filter","Filters"),
    ("brake pad","Brakes"),("brake disc","Brakes"),("caliper","Brakes"),("brake","Brakes"),("disc brake","Brakes"),
    ("spark plug","Engine"),("camshaft","Engine"),("crankshaft","Engine"),("timing belt","Engine"),
    ("timing chain","Engine"),("piston","Engine"),("valve","Engine"),("gasket","Engine"),
    ("engine mount","Engine"),("engine","Engine"),("belt","Engine"),("chain","Engine"),("seal","Engine"),
    ("oil pump","Engine"),("water pump","Engine"),("hose","Engine"),("pipe","Engine"),("pulley","Engine"),
    ("sensor","Electronics"),("ecu","Electronics"),("module","Electronics"),
    ("airbag","Safety"),("seat belt","Safety"),("abs sensor","Safety"),
    ("shock absorber","Suspension"),("strut","Suspension"),("spring","Suspension"),
    ("control arm","Suspension"),("wishbone","Suspension"),("ball joint","Suspension"),("bearing","Suspension"),
    ("steering","Steering"),("rack","Steering"),("track rod","Steering"),
    ("exhaust","Exhaust"),("muffler","Exhaust"),("catalytic","Exhaust"),
    ("radiator","Cooling"),("coolant","Cooling"),("thermostat","Cooling"),("fan","Cooling"),
    ("transmission","Transmission"),("clutch","Transmission"),("gearbox","Transmission"),
    ("axle","Drivetrain"),("driveshaft","Drivetrain"),("cv joint","Drivetrain"),
    ("headlight","Lighting"),("tail light","Lighting"),("fog light","Lighting"),("lamp","Lighting"),("bulb","Lighting"),
    ("mirror","Body"),("door handle","Body"),("bumper","Body"),("bonnet","Body"),("hood","Body"),
    ("fender","Body"),("windshield","Body"),("wiper","Body"),("panel","Body"),("spoiler","Body"),
    ("fuel pump","Fuel System"),("injector","Fuel System"),("fuel rail","Fuel System"),
    ("battery","Electrical"),("alternator","Electrical"),("starter","Electrical"),("fuse","Electrical"),("relay","Electrical"),
    ("wheel","Wheels & Tires"),("rim","Wheels & Tires"),
    ("seat","Interior"),("trim","Interior"),("dashboard","Interior"),
    ("compressor","HVAC"),("air conditioning","HVAC"),
]

def classify_part(en, he):
    text = (en+" "+he).lower()
    for kw, cat in CATEGORY_KW:
        if kw in text: return cat
    return "General Parts"

def get_token(slug):
    for attempt in range(3):
        try:
            r = requests.get(f"https://samelet.com/form/parts-prices/{slug}", timeout=15)
            m = re.search(r'name="token" type="hidden" value="([a-f0-9]+)"', r.text)
            if m: return m.group(1)
        except Exception as e:
            if attempt < 2: time.sleep(2)
    return ""

def search_parts(slug, token, query, opt="2"):
    try:
        r = requests.post("https://samelet.com/api", data={
            "site": slug, "tag": "parts-prices",
            "part_search": query, "part_search_options": opt,
            "token": token, "page_name": "מחירון חלפים",
        }, headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": f"https://samelet.com/form/parts-prices/{slug}",
        }, timeout=15)
        d = r.json()
        if d.get("success") != 1: return []
        parts = d.get("parts", [])
        if isinstance(parts, dict): parts = [parts]
        return parts if isinstance(parts, list) else []
    except: return []

def enumerate_all_parts(slug, token):
    """Flat enumeration — no deep recursion.
    1. Single uppercase A-Z
    2. All 2-char UPPER×ALPHANUM (1,612 queries) — main coverage
    3. Explicit 3-char expansion only for previously observed capped prefixes
    4. Hebrew single chars
    5. SKU digit prefix (opt=1)
    """
    all_parts = {}

    def query(q, opt="2"):
        parts = search_parts(slug, token, q, opt)
        added = 0
        for p in parts:
            mid = p.get("Material","")
            if mid and mid not in all_parts:
                all_parts[mid] = p
                added += 1
        time.sleep(0.08)
        return len(parts), added

    # Phase 1: 2-char UPPER × ALPHANUM (flat, no recursion)
    total_searches = len(UPPER)*len(ALPHANUM)
    print(f"  Phase 1: {total_searches} 2-char searches...")
    capped = []
    done = 0
    for c1 in UPPER:
        for c2 in ALPHANUM:
            n, _ = query(c1+c2)
            if n >= API_CAP:
                capped.append(c1+c2)
            done += 1
            if done % 100 == 0:
                print(f"    {done}/{total_searches} done, {len(all_parts)} unique parts, {len(capped)} capped")

    # Phase 2: 3-char expansion only for capped 2-char prefixes
    if capped:
        print(f"  Phase 2: 3-char expansion for {len(capped)} capped prefixes ({len(capped)*len(ALPHANUM)} searches)...")
        for pfx in capped:
            capped3 = []
            for c3 in ALPHANUM:
                n, _ = query(pfx+c3)
                if n >= API_CAP:
                    capped3.append(pfx+c3)
            # Phase 3: 4-char for still-capped 3-char (safety net, rare)
            for pfx3 in capped3:
                for c4 in ALPHANUM:
                    query(pfx3+c4)

    # Phase 3: Hebrew single chars
    print(f"  Phase 3: Hebrew ({len(HEBREW)} chars)...")
    for h in HEBREW:
        query(h)

    # Phase 4: SKU prefix search (numeric)
    print(f"  Phase 4: SKU digit prefix...")
    for d in string.digits:
        query(d, "1")
    for d1 in string.digits:
        for d2 in string.digits:
            query(d1+d2, "1")

    print(f"  Total unique parts found: {len(all_parts)}")
    return all_parts

async def ensure_brands(conn):
    for name, country in [("Abarth","Italy"),("RAM","USA"),("Iveco","Italy"),("Hongqi","China"),("WEY","China")]:
        ex = await conn.fetchval("SELECT 1 FROM car_brands WHERE LOWER(name)=LOWER($1)", name)
        if not ex:
            await conn.execute("INSERT INTO car_brands(id,name,country,is_active) VALUES(gen_random_uuid(),$1,$2,TRUE) ON CONFLICT DO NOTHING", name, country)
            print(f"  Created brand: {name}")

async def import_brand(conn, slug, brand_name, prefix):
    print(f"\n{'='*60}\nImporting {brand_name} (slug={slug}, prefix={prefix}-)")
    token = get_token(slug)
    if not token:
        print(f"  [ERROR] No token for {slug}")
        return 0
    print(f"  Token: {token[:10]}...")

    old_count = await conn.fetchval("SELECT COUNT(*) FROM parts_catalog WHERE manufacturer=$1", brand_name)
    await conn.execute("DELETE FROM parts_catalog WHERE manufacturer=$1", brand_name)
    print(f"  Cleared {old_count} old rows")

    parts_dict = enumerate_all_parts(slug, token)
    if not parts_dict:
        print(f"  [WARN] No parts found")
        return 0

    ins = err = 0
    rows = list(parts_dict.items())
    print(f"  Inserting {len(rows)} parts...")
    BATCH = 25
    for i in range(0, len(rows), BATCH):
        batch = rows[i:i+BATCH]
        for mid, p in batch:
            try:
                raw_sku = mid.lstrip("0") or mid
                sku = f"{prefix}-{raw_sku}"
                name_en = (p.get("MatDescEn","") or "").strip()
                name_he = (p.get("MatDescHe","") or "").strip()
                name = name_en or name_he or sku
                try:
                    il_retail = float(p.get("PriceWithVat","0") or "0")  # consumer retail incl. 17% VAT
                except: il_retail = 0.0
                VAT = 0.18
                il_cost    = round(il_retail / (1 + VAT), 2) if il_retail > 0 else 0.0
                il_selling = round(il_cost * 1.45, 2) if il_cost > 0 else 0.0
                category = classify_part(name_en, name_he)
                part_type = "original" if p.get("MaterialType","01") == "01" else "aftermarket"
                await conn.execute(
                    """INSERT INTO parts_catalog(id,sku,name,name_he,category,manufacturer,part_type,
                       base_price,importer_price_ils,max_price_ils,min_price_ils,oem_number,is_active,created_at,updated_at)
                       VALUES(gen_random_uuid(),$1,$2,$3,$4,$5,$6,$7,$8,$9,$8,$10,TRUE,NOW(),NOW())
                       ON CONFLICT(sku) DO UPDATE SET name=EXCLUDED.name,name_he=EXCLUDED.name_he,
                       category=EXCLUDED.category,manufacturer=EXCLUDED.manufacturer,
                       base_price=EXCLUDED.base_price,importer_price_ils=EXCLUDED.importer_price_ils,
                       max_price_ils=EXCLUDED.max_price_ils,min_price_ils=EXCLUDED.min_price_ils,
                       part_type=EXCLUDED.part_type,updated_at=NOW()""",
                    sku,name,name_he,category,brand_name,part_type,il_selling,il_cost,il_retail,mid)
                ins += 1
            except Exception as e:
                err += 1
                if err <= 5: print(f"  [ERR] {mid}: {e}")
    print(f"  {brand_name}: inserted={ins} errors={err}")
    return ins

async def main():
    print("Samelet Importer v3 (flat 2-char) starting...")
    conn = await asyncpg.connect(DB_URL)
    try:
        await ensure_brands(conn)
        brands_to_run = BRANDS
        if SINGLE:
            brands_to_run = [(s,b,p) for s,b,p in BRANDS if b==SINGLE or s==SINGLE or p==SINGLE]
        counts = {}
        for slug, brand, prefix in brands_to_run:
            t0 = time.time()
            c = await import_brand(conn, slug, brand, prefix)
            counts[brand] = c
            elapsed = time.time()-t0
            print(f"  [{brand}] {c} parts in {elapsed:.0f}s")
            time.sleep(2)
        print(f"\n{'='*60}\nIMPORT COMPLETE")
        for b,c in counts.items(): print(f"  {b}: {c}")
        print(f"  TOTAL: {sum(counts.values())}")
    finally:
        await conn.close()

if __name__ == "__main__":
    asyncio.run(main())
