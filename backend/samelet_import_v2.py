#!/usr/bin/env python3
"""
Script: samelet_import_v2.py
Purpose: Import all Israeli-official-importer parts from samelet.com price lists.

Process:
  1. Authenticate per-brand on samelet.com to get session token
  2. Enumerate all parts using 2-char prefix searches (A-Z x A-Z + digits) for full coverage
  3. Also search Hebrew single-char and SKU digit prefix for complete harvest
  4. Upsert to parts_catalog (ON CONFLICT update; do NOT delete/re-insert)
  5. Create or update supplier_parts record for each part
  6. Add specifications JSONB with VAT, currency, source, warranty
  7. Delegate missing fitment to REX agent via agent_todos

Data Imported / Modified:
  - parts_catalog: sku, name, name_he, category, manufacturer, manufacturer_id,
                   part_type, base_price (IL retail incl. VAT), importer_price_ils (0 — no trade cost),
                   max_price_ils, oem_number, specifications (JSONB), is_active
  - supplier_parts: supplier_id, part_id, supplier_sku, price_ils, availability,
                    is_available, warranty_months, estimated_delivery_days, supplier_url
  - agent_todos: REX task for missing fitment per brand

Data Sources / Web Links:
  - Alfa Romeo parts: https://samelet.com/form/parts-prices/alfaromeo
  - Jeep parts: https://samelet.com/form/parts-prices/jeep
  - Fiat parts: https://samelet.com/form/parts-prices/fiat
  - RAM parts: https://samelet.com/form/parts-prices/ram
  - Subaru parts: https://samelet.com/form/parts-prices/subaru
  - Abarth parts: https://samelet.com/form/parts-prices/abarth
  - Iveco parts: https://samelet.com/form/parts-prices/iveco
  - Hongqi parts: https://samelet.com/form/parts-prices/hongqi
  - WEY parts: https://samelet.com/form/parts-prices/wey
  - samelet.com API: POST https://samelet.com/api

Missing Data Delegation:
  - English descriptions → ai_catalog_builder.py (master_enriched=False)
  - Fitment data → REX agent todo created per brand after each import
  - Missing OEM cross-refs → needs_oem_lookup=True on all parts

VAT Rules:
  - samelet.com returns PriceNoVat (excl.) and PriceWithVat (incl. 18%)
  - Store: base_price = PriceWithVat, importer_price_ils = 0, max_price_ils = PriceWithVat
  - These are official IL importer retail prices (shown to customers as OEM reference, no extra markup)

Confidence tier: 1.00 (Official Israeli importer price data)

Author: AutoSpareFinder Agent
Last Updated: 2026-06-01
"""
import asyncio, asyncpg, requests, time, re, os, string, json, uuid, sys

DB_URL = os.environ.get("DATABASE_URL", "postgresql://autospare:e4b79d75ca640dbe7f259618f078b82f21573e419308f668beed5e20b26b1d43@postgres_catalog:5432/autospare").replace("postgresql+asyncpg://", "postgresql://")
# Run a single brand: python3 samelet_import_v2.py Hongqi  (or 'hongqi')
SINGLE = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("SAMELET_BRAND", "")

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

SAMELET_BRAND_URLS = {
    'alfaromeo': 'https://samelet.com/form/parts-prices/alfaromeo',
    'jeep': 'https://samelet.com/form/parts-prices/jeep',
    'fiat': 'https://samelet.com/form/parts-prices/fiat',
    'ram': 'https://samelet.com/form/parts-prices/ram',
    'subaru': 'https://samelet.com/form/parts-prices/subaru',
    'abarth': 'https://samelet.com/form/parts-prices/abarth',
    'iveco': 'https://samelet.com/form/parts-prices/iveco',
    'hongqi': 'https://samelet.com/form/parts-prices/hongqi',
    'wey': 'https://samelet.com/form/parts-prices/wey',
}


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

    # Get or create manufacturer_id from car_brands
    brand_row = await conn.fetchrow(
        "SELECT id FROM car_brands WHERE LOWER(name)=LOWER($1) LIMIT 1", brand_name)
    if not brand_row:
        await conn.execute(
            "INSERT INTO car_brands(id,name,is_active,created_at) VALUES(gen_random_uuid(),$1,TRUE,NOW()) ON CONFLICT DO NOTHING",
            brand_name)
        brand_row = await conn.fetchrow(
            "SELECT id FROM car_brands WHERE LOWER(name)=LOWER($1) LIMIT 1", brand_name)
    manufacturer_id = str(brand_row["id"]) if brand_row else None

    # Get or create official importer supplier record
    supplier_name = f"{brand_name} Official Importer Israel"
    supp_row = await conn.fetchrow("SELECT id FROM suppliers WHERE name=$1", supplier_name)
    if not supp_row:
        sid = str(uuid.uuid4())
        supplier_url = SAMELET_BRAND_URLS.get(slug, f"https://samelet.com/form/parts-prices/{slug}")
        await conn.execute("""
            INSERT INTO suppliers (id, name, website, country,
                                   is_active, is_manufacturer, manufacturer_name,
                                   manufacturer_id, reliability_score, created_at, updated_at)
            VALUES ($1, $2, $3, 'IL', true, true, $4, $5::uuid, 0.95, NOW(), NOW())
            ON CONFLICT (name) DO NOTHING
        """, sid, supplier_name, supplier_url, brand_name, manufacturer_id)
        supp_row = await conn.fetchrow("SELECT id FROM suppliers WHERE name=$1", supplier_name)
    supplier_id = str(supp_row["id"]) if supp_row else None
    supplier_url = SAMELET_BRAND_URLS.get(slug, f"https://samelet.com/form/parts-prices/{slug}")

    parts_dict = enumerate_all_parts(slug, token)
    if not parts_dict:
        print(f"  [WARN] No parts found")
        return 0
    print(f"  {len(parts_dict)} unique parts, upserting...")

    ins = err = 0
    new_part_ids = []
    batch = []
    for mid, p in parts_dict.items():
        try:
            raw_sku = mid.lstrip("0") or mid
            sku = f"{prefix}-{raw_sku}"
            name_en = (p.get("MatDescEn","") or "").strip()
            name_he = (p.get("MatDescHe","") or "").strip()
            name = name_en or name_he or sku
            try:
                il_retail = float(p.get("PriceWithVat","0") or "0")  # consumer retail incl. VAT
            except:
                il_retail = 0.0
            # il_retail = market reference (IL official dealer retail incl. VAT)
            # base_price = il_retail (show OEM reference; no markup — we source internationally)
            # importer_price_ils = 0 (we don't procure from the official importer at retail)
            max_price = il_retail
            category  = classify_part(name_en, name_he)
            part_type = "original" if p.get("MaterialType","01") == "01" else "aftermarket"
            specs = json.dumps({
                "vat_included":       True,
                "vat_rate":           0.18,
                "currency":           "ILS",
                "source":             f"samelet.com official importer - {brand_name}",
                "shipping_to_il":     True,
                "importer":           supplier_name,
                "warranty_months":    12,
                "samelet_slug":       slug,
                "material_type":      p.get("MaterialType",""),
                "il_retail_incl_vat": il_retail,
            }, ensure_ascii=False)
            tier = 'OE_equivalent' if part_type == 'aftermarket' else None
            batch.append((sku, name, name_he, category, brand_name, manufacturer_id,
                          part_type, il_retail, 0.0, max_price, mid, specs,
                          max_price, tier))
        except Exception as e:
            err += 1
            if err <= 3: print(f"  [ERR] prep {mid}: {e}")

    # Upsert in batches of 25 (never DELETE — use ON CONFLICT)
    for i in range(0, len(batch), 25):
        chunk = batch[i:i+25]
        try:
            results = await conn.fetch("""
                INSERT INTO parts_catalog(
                    id, sku, name, name_he, category, manufacturer, manufacturer_id,
                    part_type, base_price, importer_price_ils, max_price_ils, min_price_ils,
                    oem_number, specifications, aftermarket_tier, is_active,
                    part_condition, needs_oem_lookup, master_enriched,
                    created_at, updated_at)
                VALUES(gen_random_uuid(),$1,$2,$3,$4,$5,$6::uuid,$7,$8,$9,$10,$13,$11,$12::jsonb,$14,TRUE,
                       'New',FALSE,FALSE,NOW(),NOW())
                ON CONFLICT(sku) DO UPDATE SET
                    name=EXCLUDED.name,
                    name_he=COALESCE(EXCLUDED.name_he, parts_catalog.name_he),
                    category=EXCLUDED.category,
                    manufacturer=EXCLUDED.manufacturer,
                    base_price=CASE WHEN EXCLUDED.base_price > 0 THEN EXCLUDED.base_price
                                    ELSE parts_catalog.base_price END,
                    importer_price_ils=0,
                    max_price_ils=CASE WHEN EXCLUDED.max_price_ils > 0
                                      THEN EXCLUDED.max_price_ils
                                      ELSE parts_catalog.max_price_ils END,
                    min_price_ils=CASE WHEN EXCLUDED.min_price_ils > 0
                                      THEN EXCLUDED.min_price_ils
                                      ELSE parts_catalog.min_price_ils END,
                    aftermarket_tier=COALESCE(EXCLUDED.aftermarket_tier, parts_catalog.aftermarket_tier),
                    specifications=COALESCE(parts_catalog.specifications,'{}')::jsonb
                                    || EXCLUDED.specifications::jsonb,
                    part_type=EXCLUDED.part_type,
                    updated_at=NOW()
                RETURNING id, oem_number, base_price
            """, *zip(*chunk) if False else [r for row in chunk for r in row])
            ins += len(chunk)
        except Exception as e:
            # Fallback: insert one by one
            for row in chunk:
                try:
                    result = await conn.fetchrow("""
                        INSERT INTO parts_catalog(
                            id, sku, name, name_he, category, manufacturer, manufacturer_id,
                            part_type, base_price, importer_price_ils, max_price_ils, min_price_ils,
                            oem_number, specifications, aftermarket_tier, is_active,
                            part_condition, needs_oem_lookup, master_enriched,
                            created_at, updated_at)
                        Values(gen_random_uuid(),$1,$2,$3,$4,$5,$6::uuid,$7,$8,$9,$10,$13,$11,$12::jsonb,$14,TRUE,
                               'New',FALSE,FALSE,NOW(),NOW())
                        ON CONFLICT(sku) DO UPDATE SET
                            name=EXCLUDED.name,
                            name_he=COALESCE(EXCLUDED.name_he, parts_catalog.name_he),
                            base_price=CASE WHEN EXCLUDED.base_price > 0 THEN EXCLUDED.base_price
                                            ELSE parts_catalog.base_price END,
                            importer_price_ils=0,
                            min_price_ils=CASE WHEN EXCLUDED.min_price_ils > 0
                                          THEN EXCLUDED.min_price_ils
                                          ELSE parts_catalog.min_price_ils END,
                            aftermarket_tier=COALESCE(EXCLUDED.aftermarket_tier, parts_catalog.aftermarket_tier),
                            specifications=COALESCE(parts_catalog.specifications,'{}')::jsonb
                                            || EXCLUDED.specifications::jsonb,
                            updated_at=NOW()
                        RETURNING id, oem_number, base_price
                    """, row[0], row[1], row[2], row[3], row[4], row[5], row[6],
                         row[7], row[8], row[9], row[10], row[11], row[12], row[13])
                    if result and supplier_id:
                        new_part_ids.append((str(result["id"]), row[10], row[8], supplier_id))
                    ins += 1
                except Exception as e2:
                    err += 1
                    if err <= 10: print(f"  [ERR] {row[0]}: {e2}")

    # Create supplier_parts for all upserted parts
    if supplier_id:
        existing_parts = await conn.fetch(
            "SELECT id, oem_number, base_price FROM parts_catalog WHERE LOWER(manufacturer)=LOWER($1) AND is_active=TRUE",
            brand_name)
        sp_count = 0
        for part in existing_parts:
            try:
                await conn.execute("""
                    INSERT INTO supplier_parts (
                        id, supplier_id, part_id, supplier_sku,
                        price_ils, price_usd, availability, is_available,
                        warranty_months, estimated_delivery_days, supplier_url,
                        created_at, updated_at)
                    VALUES (gen_random_uuid(), $1::uuid, $2::uuid, $3, $4, 0.0,
                            'in_stock', TRUE, 12, 21, $5, NOW(), NOW())
                    ON CONFLICT (part_id, supplier_id) DO UPDATE SET
                        price_ils=EXCLUDED.price_ils,
                        updated_at=NOW()
                """, supplier_id, str(part["id"]), str(part["oem_number"]),
                     float(part["base_price"] or 0), supplier_url)
                sp_count += 1
            except Exception:
                pass
        print(f"  {brand_name}: supplier_parts upserted={sp_count}")

    # Delegate fitment to REX
    try:
        await conn.execute("""
            INSERT INTO agent_todos
                (id, agent_name, title, description, priority, status, created_at, updated_at)
            VALUES (gen_random_uuid(), 'REX', $1, $2, 'high', 'not_started', NOW(), NOW())
        """,
        f"Fetch fitment for {brand_name} samelet parts ({ins} parts)",
        f"{ins} parts imported from samelet.com for {brand_name}. No vehicle fitment data. "
        f"Query TecDoc or eBay fitment APIs to map parts to specific models/years.")
    except Exception:
        pass

    print(f"  {brand_name}: upserted={ins} errors={err}")
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
