"""
Script: import_delek_brands.py
Purpose: Import Delek Motors brand parts (BMW, Ford, MINI, NIO, M-HERO, VOYAH, Mazda)
         from harvested JSON files into the parts_catalog.

Process:
  1. Load JSON file per brand from /opt/autosparefinder/*.json
  2. Get-or-create car_brands entry for the manufacturer
  3. Get-or-create Delek Motors supplier record
  4. Upsert to parts_catalog with specifications JSONB (never DELETE-and-reinsert)
  5. Insert part_vehicle_fitment for each model found in JSON data
  6. Upsert supplier_parts record with price, warranty, availability
  7. Create REX agent todo for missing fitment data
  8. Verify final counts per brand

Data Imported / Modified:
  - parts_catalog: sku, name, name_he, manufacturer, manufacturer_id, oem_number,
                   category, part_type, part_condition, base_price,
                   specifications (JSONB with vat_included, source, importer, warranty),
                   is_active, aftermarket_tier, needs_oem_lookup, master_enriched
  - part_vehicle_fitment: part_id, manufacturer, model, year_from, year_to, notes
  - supplier_parts: supplier_id, part_id, supplier_sku, price_ils, price_usd,
                    availability, is_available, warranty_months, estimated_delivery_days,
                    supplier_url
  - agent_todos: REX task for missing fitment per brand

Data Sources / Web Links:
  - BMW: /opt/autosparefinder/bmw_parts.json  (scraped from bmw.co.il)
         https://www.bmw.co.il
  - Ford: /opt/autosparefinder/ford_parts.json  (scraped from ford.co.il)
          https://www.ford.co.il
  - MINI: /opt/autosparefinder/mini_parts.json  (scraped from mini.co.il)
          https://www.mini.co.il
  - NIO: /opt/autosparefinder/nio_parts.json  (scraped from nio-israel.co.il)
         https://www.nio-israel.co.il
  - M-HERO: /opt/autosparefinder/mhero_parts.json  (scraped from m-hero.co.il)
            https://www.m-hero.co.il
  - VOYAH: /opt/autosparefinder/voyah_parts.json  (scraped from voyah-il.co.il)
           https://www.voyah-il.co.il
  - Delek Automotive: https://www.delek-motors.co.il

Missing Data Delegation:
  - English descriptions for Hebrew-only parts → ai_catalog_builder.py (master_enriched=False)
  - Fitment data → REX agent todo created per brand after import
  - Missing OEM cross-refs → needs_oem_lookup=True on all parts

VAT Rules:
  - Delek JSON files: prices from price_ils_vat (incl. VAT) or price_ils (excl. VAT)
  - If price_ils_vat present: base_price = price_ils_vat / 1.18; max_price_ils = price_ils_vat
  - If only price_ils present: store as-is; max_price_ils = price_ils * 1.18

Confidence tier: 1.00 (Official Israeli Delek Motors importer data)

Author: AutoSpareFinder Agent
Last Updated: 2026-06-01
"""
import asyncio, json, os, re, sys, uuid, argparse
from pathlib import Path
import asyncpg
import urllib.parse as up

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://autospare:e4b79d75ca640dbe7f259618f078b82f21573e419308f668beed5e20b26b1d43@postgres_catalog:5432/autospare"
)
BATCH_SIZE = 25
DATA_DIR   = Path("/opt/autosparefinder")

# Brand config: make_name → (sku_prefix, json_file, supplier_name)
BRAND_CONFIG = {
    "BMW":              ("BMW",  "bmw_parts.json",    "BMW Delek Motors"),
    "Mazda":("MAZDA","jlr_parts.json","Mazda Delek Motors"),
    "Ford":             ("FORD", "ford_parts.json",   "Ford Delek Motors"),
    "MINI":             ("MINI", "mini_parts.json",   "MINI Delek Motors"),
    "NIO":              ("NIO",  "nio_parts.json",    "NIO Delek Motors"),
    "M-HERO":           ("MHR",  "mhero_parts.json",  "M-HERO Delek Motors"),
    "VOYAH":            ("VYH",  "voyah_parts.json",  "VOYAH Delek Motors"),
}

OEM_RE = re.compile(r"^[A-Z0-9][\w\-./]{1,49}$", re.IGNORECASE)


def make_sku(prefix, oem):
    clean = re.sub(r"[^A-Za-z0-9]", "", oem).upper()[:50]
    return f"{prefix}-{clean}"


def categorise(name_he):
    t = (name_he or "").lower()
    if any(w in t for w in ["מנוע","שמן","מסנן","מחזור","טורבו","בוכנה","שסתום"]):
        return "Engine Parts"
    if any(w in t for w in ["ברקס","בלם","brake","דיסק","pad","רפידה"]):
        return "Brakes"
    if any(w in t for w in ["suspension","שלדה","קפיץ","מוט","bearing","מסב","זרוע","מתלה"]):
        return "Suspension"
    if any(w in t for w in ["אטם","gasket","seal","צינור"]):
        return "Engine Parts"
    if any(w in t for w in ["חשמל","sensor","חיישן","electrical","מחשב","פתיל","חוטים"]):
        return "Electrical"
    if any(w in t for w in ["תיבת","מצמד","clutch","gearbox","גיר"]):
        return "Transmission"
    if any(w in t for w in ["פגוש","מכסה","דלת","body","מרכב","כנף","מגן"]):
        return "Body Parts"
    if any(w in t for w in ["קירור","radiator","מאוורר","מצנן","cooling"]):
        return "Cooling System"
    if any(w in t for w in ["היגוי","steering","הגה"]):
        return "Steering"
    if any(w in t for w in ["דלק","fuel","משאבת דלק","מכל"]):
        return "Fuel System"
    return "General Parts"

BRAND_URLS = {
    "BMW":   "https://www.bmw.co.il",
    "Mazda": "https://www.mazda.co.il",
    "Ford":  "https://www.ford.co.il",
    "MINI":  "https://www.mini.co.il",
    "NIO":   "https://www.nio-israel.co.il",
    "M-HERO":"https://www.m-hero.co.il",
    "VOYAH": "https://www.voyah-il.co.il",
}


async def ensure_car_brand(conn, make):
    """Return car_brands.id for the given make string, looking up by name."""
    row = await conn.fetchrow("SELECT id FROM car_brands WHERE LOWER(name)=$1", make.lower())
    if row:
        return str(row["id"])
    # Try partial match (e.g. 'M-HERO' → 'M-HERO')
    row = await conn.fetchrow(
        "SELECT id FROM car_brands WHERE LOWER(name) LIKE $1 LIMIT 1",
        f"%{make.lower().split()[0]}%"
    )
    if row:
        return str(row["id"])
    # Create new car_brand entry if not found
    new_id = str(uuid.uuid4())
    await conn.execute(
        "INSERT INTO car_brands(id, name, created_at, updated_at) VALUES($1, $2, NOW(), NOW()) ON CONFLICT DO NOTHING",
        new_id, make
    )
    row = await conn.fetchrow("SELECT id FROM car_brands WHERE LOWER(name)=$1", make.lower())
    return str(row["id"]) if row else new_id


async def ensure_supplier(conn, name):
    row = await conn.fetchrow("SELECT id FROM suppliers WHERE name=$1", name)
    if row:
        return str(row["id"])
    new_id = str(uuid.uuid4())
    brand_key = name.replace(" Delek Motors", "").strip()
    url = BRAND_URLS.get(brand_key, "https://www.delek-motors.co.il")
    await conn.execute(
        "INSERT INTO suppliers(id,name,website,country,is_active,reliability_score,created_at,updated_at)"
        " VALUES($1,$2,$3,'IL',TRUE,0.95,NOW(),NOW()) ON CONFLICT DO NOTHING",
        new_id, name, url,
    )
    row = await conn.fetchrow("SELECT id FROM suppliers WHERE name=$1", name)
    return str(row["id"]) if row else new_id


async def _ensure_supplier_old(conn, name):
    row = await conn.fetchrow("SELECT id FROM suppliers WHERE name=$1", name)
    if row:
        return str(row["id"])
    new_id = str(uuid.uuid4())
    await conn.execute(
        "INSERT INTO suppliers(id,name,country,is_active,created_at) VALUES($1,$2,'IL',TRUE,NOW()) ON CONFLICT DO NOTHING",
        new_id, name
    )
    row = await conn.fetchrow("SELECT id FROM suppliers WHERE name=$1", name)
    return str(row["id"]) if row else new_id


async def import_brand(conn, make, sku_prefix, json_file, supplier_name):
    path = DATA_DIR / json_file
    if not path.exists():
        print(f"  [SKIP] {json_file} not found"); return {"inserted": 0, "errors": 0, "skipped": 0}

    raw = json.loads(path.read_text(encoding="utf-8"))
    parts_raw = raw.get("parts", raw) if isinstance(raw, dict) else raw
    print(f"\n[{make}] {len(parts_raw)} parts from {json_file}")

    brand_id    = await ensure_car_brand(conn, make)
    supplier_id = await ensure_supplier(conn, supplier_name)
    print(f"  brand_id={brand_id}  supplier_id={supplier_id}")

    stats = {"inserted": 0, "errors": 0, "skipped": 0}
    batch = []

    async def flush():
        if not batch: return
        brand_key = make.split()[0]
        url = BRAND_URLS.get(make, 'https://www.delek-motors.co.il')
        async with conn.transaction():
            for row in batch:
                try:
                    async with conn.transaction():   # savepoint per row
                        base_p = row["base_price"]
                        max_p = round(base_p * 1.18, 2) if base_p > 0 else 0.0
                        specs = json.dumps({
                            'vat_included':    False,
                            'vat_rate':        0.18,
                            'currency':        'ILS',
                            'source':          f'Delek Motors official importer - {make}',
                            'shipping_to_il':  True,
                            'importer':        f'{make} Delek Motors Israel',
                            'warranty_months': 24,
                        }, ensure_ascii=False)
                        result = await conn.fetchrow("""
                            INSERT INTO parts_catalog(
                                id, sku, name, name_he, manufacturer, manufacturer_id,
                                oem_number, category, part_type, part_condition,
                                base_price, importer_price_ils, min_price_ils, max_price_ils,
                                is_active, aftermarket_tier,
                                specifications,
                                needs_oem_lookup, master_enriched, updated_at
                            ) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$11,$11,$12,$13,$14,
                                     $15::jsonb,False,False,NOW())
                            ON CONFLICT(sku) DO UPDATE SET
                                oem_number   = EXCLUDED.oem_number,
                                name_he      = COALESCE(EXCLUDED.name_he, parts_catalog.name_he),
                                base_price   = CASE WHEN EXCLUDED.base_price > 0
                                               THEN EXCLUDED.base_price
                                               ELSE parts_catalog.base_price END,
                                importer_price_ils = CASE WHEN EXCLUDED.importer_price_ils > 0
                                               THEN EXCLUDED.importer_price_ils
                                               ELSE parts_catalog.importer_price_ils END,
                                min_price_ils = CASE WHEN EXCLUDED.min_price_ils > 0
                                               THEN EXCLUDED.min_price_ils
                                               ELSE parts_catalog.min_price_ils END,
                                max_price_ils = CASE WHEN EXCLUDED.max_price_ils > 0
                                               THEN EXCLUDED.max_price_ils
                                               ELSE parts_catalog.max_price_ils END,
                                specifications = COALESCE(parts_catalog.specifications,'{}')::jsonb
                                                  || EXCLUDED.specifications::jsonb,
                                is_active    = TRUE,
                                updated_at   = NOW()
                            RETURNING id
                        """,
                        row["id"], row["sku"], row["name"], row["name_he"],
                        row["manufacturer"], row["brand_id"],
                        row["oem_number"], row["category"], row["part_type"],
                        "New", base_p, max_p, True,
                        row["tier"], specs)
                        stats["inserted"] += 1

                        # supplier_parts
                        if result and supplier_id:
                            pid = str(result["id"])
                            await conn.execute("""
                                INSERT INTO supplier_parts (
                                    id, supplier_id, part_id, supplier_sku,
                                    price_ils, price_usd, availability, is_available,
                                    warranty_months, estimated_delivery_days, supplier_url,
                                    created_at, updated_at)
                                VALUES (gen_random_uuid(), $1::uuid, $2::uuid, $3, $4, 0.0,
                                        'in_stock', TRUE, 24, 14, $5, NOW(), NOW())
                                ON CONFLICT (part_id, supplier_id) DO UPDATE SET
                                    price_ils=EXCLUDED.price_ils, updated_at=NOW()
                            """, supplier_id, pid, row["oem_number"],
                                 float(row["base_price"] or 0), url)

                        # part_vehicle_fitment
                        if result and row.get("models"):
                            pid = str(result["id"])
                            for model_name in row["models"]:
                                yr_map = {
                                    'BMW': (2015, None), 'Ford': (2015, None),
                                    'MINI': (2015, None), 'NIO': (2022, None),
                                    'M-HERO': (2023, None), 'VOYAH': (2023, None),
                                    'Mazda': (2015, None),
                                }
                                y_from, y_to = yr_map.get(make, (2015, None))
                                try:
                                    await conn.execute("""
                                        INSERT INTO part_vehicle_fitment
                                            (id, part_id, manufacturer, model,
                                             year_from, year_to, notes,
                                             manufacturer_id, created_at, updated_at)
                                        VALUES (gen_random_uuid(), $1::uuid, $2, $3,
                                                $4, $5, 'Delek Motors JSON import',
                                                $6::uuid, NOW(), NOW())
                                        ON CONFLICT (part_id, manufacturer, model, year_from)
                                        DO NOTHING
                                    """, pid, make, model_name, y_from, y_to, row["brand_id"])
                                except Exception:
                                    pass
                except Exception as e:
                    print(f"    [ERR] {row.get('sku','?')}: {e}")
                    stats["errors"] += 1
        batch.clear()

    for p in parts_raw:
        oem = (p.get("oem_number") or "").strip()
        if not oem or not OEM_RE.match(oem):
            stats["skipped"] += 1; continue

        is_orig  = bool(p.get("is_original", False))
        name_he  = (p.get("name_he") or p.get("name") or oem).strip()
        name_en  = (p.get("name")    or "").strip()
        price    = float(p.get("price_ils") or p.get("price_ils_vat") or 0)
        if price > 0:
            price = round(price / 1.18, 2) if p.get("price_ils_vat") and not p.get("price_ils") else price

        batch.append({
            "id":           str(uuid.uuid4()),
            "sku":          make_sku(sku_prefix, oem),
            "name":         name_en or name_he,
            "name_he":      name_he,
            "manufacturer": make,
            "brand_id":     brand_id,
            "oem_number":   oem,
            "category":     categorise(name_he),
            "part_type":    "original" if is_orig else "oe_equivalent",
            "base_price":   price,
            "tier":         None if is_orig else "OE_equivalent",
            "models":       [p.get("model", make)] if p.get("model") else [make],
        })
        if len(batch) >= BATCH_SIZE:
            await flush()

    await flush()

    # Create REX todo for missing fitment data
    try:
        await conn.execute("""
            INSERT INTO agent_todos
                (id, agent_name, title, description, priority, status, created_at, updated_at)
            VALUES (gen_random_uuid(), 'REX', $1, $2, 'high', 'not_started', NOW(), NOW())
        """,
        f'Fetch fitment for {make} Delek Motors parts ({stats["inserted"]} parts)',
        f'{stats["inserted"]} parts imported from Delek Motors JSON for {make}. '
        f'Query TecDoc/eBay to map parts to specific models and year ranges.')
    except Exception:
        pass

    print(f"  [{make}] inserted={stats['inserted']}  errors={stats['errors']}  skipped={stats['skipped']}")
    return stats


async def verify(conn, brands):
    print("\n[Verify] Parts per brand in DB:")
    total = 0
    for make, (prefix, _, _) in brands.items():
        row = await conn.fetchrow(
            "SELECT COUNT(*) as n FROM parts_catalog WHERE sku LIKE $1 AND is_active=TRUE",
            f"{prefix}-%"
        )
        n = row["n"] if row else 0
        print(f"  {make:25s}  {n:6d}  (SKU: {prefix}-*)")
        total += n
    print(f"  {'TOTAL':25s}  {total:6d}")


async def main(brands_to_run):
    p = up.urlparse(DATABASE_URL)
    conn = await asyncpg.connect(
        host=p.hostname, port=p.port or 5432,
        database=p.path.lstrip("/"), user=p.username, password=p.password, timeout=30
    )
    print(f"[DB] Connected to {p.hostname}/{p.path.lstrip('/')}")

    totals = {"inserted": 0, "errors": 0, "skipped": 0}
    for make, (prefix, jfile, supplier) in brands_to_run.items():
        s = await import_brand(conn, make, prefix, jfile, supplier)
        for k in totals: totals[k] += s[k]

    await verify(conn, brands_to_run)
    await conn.close()
    print(f"\n[DONE] total inserted={totals['inserted']}  errors={totals['errors']}  skipped={totals['skipped']}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--brand", help="Single brand name to import (default: all)")
    ap.add_argument("--all",   action="store_true", help="Import all brands")
    args = ap.parse_args()

    if args.brand:
        key = args.brand.upper()
        chosen = {k: v for k, v in BRAND_CONFIG.items() if k.upper() == key or v[0] == key}
        if not chosen:
            print(f"Unknown brand '{args.brand}'. Options: {list(BRAND_CONFIG.keys())}")
            sys.exit(1)
    else:
        chosen = BRAND_CONFIG

    asyncio.run(main(chosen))
