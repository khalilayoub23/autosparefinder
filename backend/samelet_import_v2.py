#!/usr/bin/env python3
"""
Samelet.com parts catalog importer v2 - full 2-char prefix coverage.
Alfa Romeo: ~5,673 parts (vs 626 from single-char search).
"""
import asyncio, asyncpg, requests, time, re, os, string

DB_URL = "postgresql://autospare:e4b79d75ca640dbe7f259618f078b82f21573e419308f668beed5e20b26b1d43@postgres_catalog:5432/autospare"

BRANDS = [
    ("alfaromeo","Alfa Romeo","Italy","AR"),
    ("jeep","Jeep","USA","JP"),
    ("fiat","Fiat","Italy","FI"),
    ("ram","RAM","USA","RM"),
    ("subaru","Subaru","Japan","SU"),
    ("abarth","Abarth","Italy","AB"),
    ("iveco","Iveco","Italy","IV"),
    ("hongqi","Hongqi","China","HQ"),
    ("wey","WEY","China","WY"),
]

SINGLE = os.environ.get("SINGLE_BRAND","")
API_CAP = 29
HEBREW = list("אבגדהוזחטיכלמנסעפצקרשתךםןףץ")

CATEGORY_KW = [
    ("filter","Filters"),("oil filter","Filters"),("air filter","Filters"),("fuel filter","Filters"),
    ("brake","Brakes"),("disc","Brakes"),(" pad","Brakes"),("caliper","Brakes"),("rotor","Brakes"),
    ("spark","Engine"),("engine","Engine"),("camshaft","Engine"),("crankshaft","Engine"),
    ("timing","Engine"),("piston","Engine"),("valve","Engine"),("belt","Engine"),
    ("chain","Engine"),("gasket","Engine"),("seal","Engine"),("pump","Engine"),
    ("hose","Engine"),("pipe","Engine"),("pulley","Engine"),("injector","Fuel System"),
    ("sensor","Electronics"),("ecu","Electronics"),("module","Electronics"),("telematic","Electronics"),
    ("airbag","Safety"),("seatbelt","Safety"),(" abs","Safety"),
    ("suspension","Suspension"),("shock","Suspension"),("strut","Suspension"),
    ("spring","Suspension"),("arm","Suspension"),("bearing","Suspension"),("bush","Suspension"),
    ("steering","Steering"),("rack","Steering"),("tie rod","Steering"),
    ("exhaust","Exhaust"),("muffler","Exhaust"),("catalytic","Exhaust"),("dpf","Exhaust"),
    ("radiator","Cooling"),("coolant","Cooling"),("thermostat","Cooling"),("fan","Cooling"),
    ("intercooler","Cooling"),
    ("transmission","Transmission"),("clutch","Transmission"),("gearbox","Transmission"),
    ("axle","Drivetrain"),("driveshaft","Drivetrain"),("cv joint","Drivetrain"),
    ("light","Lighting"),("lamp","Lighting"),("headlight","Lighting"),("bulb","Lighting"),
    ("indicator","Lighting"),("fog","Lighting"),
    ("mirror","Body"),("door","Body"),("bumper","Body"),("hood","Body"),("bonnet","Body"),
    ("fender","Body"),("windshield","Body"),("window","Body"),("glass","Body"),
    ("wiper","Body"),("panel","Body"),("cover","Body"),("grille","Body"),
    ("fuel","Fuel System"),("tank","Fuel System"),
    ("battery","Electrical"),("alternator","Electrical"),("starter","Electrical"),
    ("fuse","Electrical"),("relay","Electrical"),("cable","Electrical"),("switch","Electrical"),
    ("wheel","Wheels & Tires"),("tire","Wheels & Tires"),("rim","Wheels & Tires"),
    ("seat","Interior"),("carpet","Interior"),("trim","Interior"),
    ("compressor","HVAC"),("air condition","HVAC"),("evaporator","HVAC"),
    ("tool","Tools & Accessories"),("oil","Engine"),
]

def classify_part(en, he):
    text = (en + " " + he).lower()
    for kw, cat in CATEGORY_KW:
        if kw in text:
            return cat
    return "General Parts"

def get_token(slug):
    try:
        r = requests.get(f"https://samelet.com/form/parts-prices/{slug}", timeout=15)
        m = re.search(r'name="token" type="hidden" value="([a-f0-9]+)"', r.text)
        if m: return m.group(1)
    except Exception as e:
        print(f"  [WARN] token error: {e}")
    return ""

SESSION = requests.Session()

def search_parts(slug, token, query, opt="2"):
    try:
        r = SESSION.post("https://samelet.com/api", data={
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
    except Exception as e:
        return []

def enumerate_all_parts(slug, token):
    all_parts = {}
    seen = set()

    def do_search(query, opt, depth=0):
        key = f"{opt}:{query}"
        if key in seen or depth > 3: return
        seen.add(key)
        parts = search_parts(slug, token, query, opt)
        new = 0
        for p in parts:
            mid = p.get("Material","")
            if mid and mid not in all_parts:
                all_parts[mid] = p
                new += 1
        time.sleep(0.12)
        # If capped, go deeper
        if len(parts) >= API_CAP and depth < 3:
            ext_chars = string.ascii_letters + string.digits
            for ext in ext_chars:
                do_search(query + ext, opt, depth + 1)

    upper = string.ascii_uppercase

    # Phase 1: All 2-char combinations starting with uppercase letter
    # This gives comprehensive coverage: A-Z x (A-Z + a-z + 0-9) = 1,612 searches
    print(f"  Phase 1: 2-char English prefix searches (1,612 queries)...")
    for i, c1 in enumerate(upper):
        for c2 in string.ascii_letters + string.digits:
            do_search(c1 + c2, "2")
        pct = (i+1) * 100 // 26
        print(f"    After {c1}*: {len(all_parts)} unique parts [{pct}%]", flush=True)

    # Phase 2: Hebrew single-char (covers Hebrew-only descriptions)
    print(f"  Phase 2: Hebrew single-char ({len(HEBREW)} queries)...")
    for ch in HEBREW:
        do_search(ch, "2")

    # Phase 3: SKU digit prefix (for numeric-only SKUs)
    print(f"  Phase 3: SKU digit prefix (10 queries)...")
    for d in string.digits:
        do_search(d, "1", depth=0)

    return all_parts

async def ensure_brands(conn):
    for name, country in [("Abarth","Italy"),("RAM","USA")]:
        ex = await conn.fetchval("SELECT 1 FROM car_brands WHERE LOWER(name)=LOWER($1)", name)
        if not ex:
            await conn.execute("INSERT INTO car_brands(id,name,country,is_active) VALUES(gen_random_uuid(),$1,$2,TRUE) ON CONFLICT DO NOTHING", name, country)
            print(f"  Created brand: {name}")

async def import_brand(conn, slug, brand_name, prefix):
    print(f"\n{'='*60}\nImporting {brand_name} (slug={slug}, prefix={prefix}-)\n{'='*60}")
    token = get_token(slug)
    if not token:
        print(f"  [ERROR] No token for {slug}")
        return 0
    print(f"  Token: {token[:10]}...")

    # Delete existing parts for this brand first (clean re-import)
    deleted = await conn.fetchval(
        "DELETE FROM parts_catalog WHERE manufacturer=$1 RETURNING COUNT(*)", brand_name
    )
    # fetchval on DELETE ... RETURNING won't work directly, use execute
    await conn.execute("DELETE FROM parts_catalog WHERE manufacturer=$1", brand_name)
    print(f"  Cleared existing {brand_name} parts")

    parts_dict = enumerate_all_parts(slug, token)
    if not parts_dict:
        print(f"  [WARN] No parts found")
        return 0
    print(f"  {len(parts_dict)} unique parts, inserting...")

    ins = err = 0
    batch = []
    for mid, p in parts_dict.items():
        try:
            raw_sku = mid.lstrip("0") or mid
            sku = f"{prefix}-{raw_sku}"
            name_en = (p.get("MatDescEn","") or "").strip()
            name_he = (p.get("MatDescHe","") or "").strip()
            name = name_en or name_he or sku
            try:
                base_price = float(p.get("PriceNoVat","0") or "0")
                imp_price  = float(p.get("PriceWithVat","0") or "0")
            except:
                base_price = imp_price = 0.0
            category  = classify_part(name_en, name_he)
            part_type = "original" if p.get("MaterialType","01") == "01" else "aftermarket"
            batch.append((sku, name, name_he, category, brand_name, part_type,
                          base_price, imp_price, mid))
        except Exception as e:
            err += 1
            if err <= 3: print(f"  [ERR] prep {mid}: {e}")

    # Insert in batches of 25
    for i in range(0, len(batch), 25):
        chunk = batch[i:i+25]
        try:
            await conn.executemany("""
                INSERT INTO parts_catalog(id,sku,name,name_he,category,manufacturer,part_type,
                   base_price,importer_price_ils,oem_number,is_active,created_at,updated_at)
                   VALUES(gen_random_uuid(),$1,$2,$3,$4,$5,$6,$7,$8,$9,TRUE,NOW(),NOW())
                   ON CONFLICT(sku) DO UPDATE SET name=EXCLUDED.name,name_he=EXCLUDED.name_he,
                   category=EXCLUDED.category,manufacturer=EXCLUDED.manufacturer,
                   base_price=EXCLUDED.base_price,importer_price_ils=EXCLUDED.importer_price_ils,
                   part_type=EXCLUDED.part_type,updated_at=NOW()
            """, chunk)
            ins += len(chunk)
        except Exception as e:
            err += len(chunk)
            if err <= 30: print(f"  [ERR] batch {i}: {e}")

    print(f"  {brand_name}: inserted={ins} errors={err}")
    return ins

async def main():
    print("Samelet Catalog Importer v2 (full 2-char coverage) starting...")
    conn = await asyncpg.connect(DB_URL)
    try:
        await ensure_brands(conn)
        total = 0
        counts = {}
        brands_to_run = [(s,b,c,p) for s,b,c,p in BRANDS if not SINGLE or b==SINGLE or s==SINGLE]
        for slug, brand, country, prefix in brands_to_run:
            c = await import_brand(conn, slug, brand, prefix)
            counts[brand] = c
            total += c
            time.sleep(3)
        print(f"\n{'='*60}\nIMPORT COMPLETE")
        for b,c in counts.items(): print(f"  {b}: {c}")
        print(f"  TOTAL: {total}")
    finally:
        await conn.close()

if __name__ == "__main__":
    asyncio.run(main())
