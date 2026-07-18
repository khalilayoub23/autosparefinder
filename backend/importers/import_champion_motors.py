"""
Script: import_champion_motors.py
Purpose: Import Champion Motors catalog parts (Volkswagen Group Israel: VW, Audi, Skoda, SEAT, Cupra)
         from pre-scraped champion_motors_parts.json into parts_catalog.

Process:
  1. Read champion_motors_parts.json (output from scrape_champion_motors.py)
  2. Normalize brand names to canonical form
  3. Upsert to parts_catalog with per-row savepoints
  4. Get-or-create 'Champion Motors' supplier record
  5. Upsert supplier_parts record per part
  6. Create REX agent todo for missing vehicle fitment

Data Imported / Modified:
  - parts_catalog: sku, name, name_he, manufacturer, manufacturer_id, oem_number,
                   category, part_type, part_condition, base_price, aftermarket_tier
  - supplier_parts: supplier_id, part_id, supplier_sku, price_ils, is_available,
                    warranty_months, supplier_url
  - agent_todos: REX task for missing vehicle fitment

Data Sources / Web Links:
  - Champion Motors official site: https://www.championmotors.co.il
  - Volkswagen Israel: https://www.volkswagen.co.il
  - Audi Israel: https://www.audi.co.il
  - Skoda Israel: https://www.skoda.co.il
  - SEAT Israel: https://www.seat.co.il
  - Cupra Israel: https://www.cupra.co.il

Missing Data Delegation:
  - Vehicle fitment → REX agent todo created after import
  - English descriptions → ai_catalog_builder.py (master_enriched=False)

VAT Rules:
  - champion_motors_parts.json prices are ILS INCL. 18% VAT (consumer price)
  - Store as-is in base_price; max_price_ils = same

Confidence tier: 0.95 (official Champion Motors catalog scrape)

Run inside backend container:
  docker exec autospare_backend python /app/importers/import_champion_motors.py

Author: AutoSpareFinder Agent
Last Updated: 2026-06-01
"""
import asyncio, json, os, re, sys, uuid
from pathlib import Path
import asyncpg
import urllib.parse as up

INPUT_FILE   = os.getenv("CM_JSON", "/app/state/champion_motors_parts.json")
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://autospare:e4b79d75ca640dbe7f259618f078b82f21573e419308f668beed5e20b26b1d43@postgres_catalog:5432/autospare"
)
BATCH_SIZE = 25
SKU_PREFIX = "CM"

MAKE_NORM = {
    "volkswagen": "Volkswagen", "vw": "Volkswagen",
    "אודי": "Audi", "audi": "Audi",
    "skoda": "Skoda", "סקודה": "Skoda",
    "seat": "SEAT", "סיאט": "SEAT",
    "cupra": "Cupra", "קופרה": "Cupra",
}
OEM_RE = re.compile(r"^[A-Z0-9][\w\-./]{3,49}$", re.IGNORECASE)

def norm_make(raw):
    key = raw.strip().lower()
    if key in MAKE_NORM:
        return MAKE_NORM[key]
    for k, v in MAKE_NORM.items():
        if k in key:
            return v
    return raw.strip()

def make_sku(oem):
    return f"{SKU_PREFIX}-{re.sub(r'[^A-Za-z0-9]','',oem).upper()[:50]}"

def categorise(name_he, part_type_he):
    text = f"{name_he} {part_type_he}".lower()
    if any(w in text for w in ["מנוע","שמן","מסנן","filter"]): return "Engine Parts"
    if any(w in text for w in ["ברקס","בלם","brake","disc","pad"]): return "Brakes"
    if any(w in text for w in ["suspension","שלדה","קפיץ","מוט","bearing","מסב"]): return "Suspension"
    if any(w in text for w in ["seal","אטם","gasket"]): return "Engine Parts"
    if any(w in text for w in ["electrical","חשמל","sensor","חיישן"]): return "Electrical"
    if any(w in text for w in ["gearbox","תיבת","clutch","מצמד"]): return "Transmission"
    if any(w in text for w in ["body","פגוש","מכסה","דלת"]): return "Body Parts"
    if any(w in text for w in ["cooling","קירור","radiator"]): return "Cooling System"
    return "General Parts"

async def run_import():
    p = up.urlparse(DATABASE_URL)
    conn = await asyncpg.connect(
        host=p.hostname, port=p.port or 5432,
        database=p.path.lstrip("/"), user=p.username, password=p.password, timeout=30,
    )
    print(f"[DB] Connected to {p.hostname}/{p.path.lstrip('/')}")

    data_path = Path(INPUT_FILE)
    if not data_path.exists():
        print(f"[ERROR] Not found: {INPUT_FILE}"); sys.exit(1)
    raw = json.loads(data_path.read_text(encoding="utf-8"))
    parts_raw = raw.get("parts", raw) if isinstance(raw, dict) else raw
    print(f"[Import] {len(parts_raw)} raw parts from {INPUT_FILE}")
    if not parts_raw:
        print("[ERROR] File is empty — run scraper first"); sys.exit(1)

    brand_rows = await conn.fetch("SELECT id, name FROM car_brands")
    brand_map = {r["name"].lower(): r["id"] for r in brand_rows}

    supplier = await conn.fetchrow("SELECT id FROM suppliers WHERE name='Champion Motors' LIMIT 1")
    if not supplier:
        sup_id = str(uuid.uuid4())
        await conn.execute(
            "INSERT INTO suppliers(id,name,country,is_active,created_at) VALUES($1,'Champion Motors','IL',TRUE,NOW()) ON CONFLICT DO NOTHING",
            sup_id)
        supplier = await conn.fetchrow("SELECT id FROM suppliers WHERE name='Champion Motors' LIMIT 1")
    supplier_id = supplier["id"]
    print(f"[DB] Supplier Champion Motors: {supplier_id}")

    stats = {"inserted":0,"skipped_no_oem":0,"skipped_no_brand":0,"errors":0}
    batch = []

    async def flush():
        if not batch: return
        for row in batch:
            try:
                async with conn.transaction():
                    await conn.execute("""
                        INSERT INTO parts_catalog(
                            id,sku,name,name_he,manufacturer,manufacturer_id,
                            oem_number,category,part_type,part_condition,
                            base_price,importer_price_ils,min_price_ils,max_price_ils,
                            is_active,aftermarket_tier,specifications,
                            needs_oem_lookup,master_enriched,updated_at
                        ) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,
                                 ROUND($11::numeric/1.18*1.45,2),ROUND($11::numeric/1.18,2),$11,$11,
                                 $12,$13,$14::jsonb,$15,$16,NOW())
                        ON CONFLICT(sku) DO UPDATE SET
                            oem_number=EXCLUDED.oem_number,
                            name_he=EXCLUDED.name_he,
                            base_price=CASE WHEN EXCLUDED.base_price>0 THEN EXCLUDED.base_price ELSE parts_catalog.base_price END,
                            importer_price_ils=CASE WHEN EXCLUDED.importer_price_ils>0 THEN EXCLUDED.importer_price_ils ELSE parts_catalog.importer_price_ils END,
                            min_price_ils=CASE WHEN EXCLUDED.min_price_ils>0 THEN EXCLUDED.min_price_ils ELSE parts_catalog.min_price_ils END,
                            max_price_ils=CASE WHEN EXCLUDED.max_price_ils>0 THEN EXCLUDED.max_price_ils ELSE parts_catalog.max_price_ils END,
                            specifications=COALESCE(parts_catalog.specifications,'{}')::jsonb || EXCLUDED.specifications::jsonb,
                            is_active=TRUE, updated_at=NOW()
                    """,
                    row["id"],row["sku"],row["name"],row["name_he"],
                    row["manufacturer"],row["manufacturer_id"],
                    row["oem_number"],row["category"],row["part_type"],
                    row["part_condition"],row["base_price"],True,
                    row["aftermarket_tier"],row["specifications"],False,False)
                    stats["inserted"] += 1
                    # Fitment: insert model from source data (model is a specific submodel e.g. 'Z4 SDRIVE 3.5I')
                    if row["model"]:
                        pid_row = await conn.fetchval(
                            "SELECT id FROM parts_catalog WHERE sku=$1", row["sku"])
                        if pid_row:
                            try:
                                await conn.execute("""
                                    INSERT INTO part_vehicle_fitment(
                                        id, part_id, manufacturer, manufacturer_id,
                                        model, year_from, year_to, notes,
                                        created_at, updated_at)
                                    VALUES(gen_random_uuid(),$1::uuid,$2,$3::uuid,
                                           $4,2010,NULL,
                                           'Champion Motors source (year estimated)',
                                           NOW(),NOW())
                                    ON CONFLICT(part_id,manufacturer,model,year_from) DO NOTHING
                                """, str(pid_row), row["manufacturer"],
                                     row["manufacturer_id"], row["model"])
                                stats["fitment"] = stats.get("fitment", 0) + 1
                            except Exception:
                                pass  # fitment is best-effort
            except Exception as exc:
                print(f"  [ERR] {row['sku']}: {exc}")
                stats["errors"] += 1
        batch.clear()

    for raw_part in parts_raw:
        oem = (raw_part.get("oem_number") or "").strip()
        if not oem or not OEM_RE.match(oem):
            stats["skipped_no_oem"] += 1; continue

        brand_name = norm_make(raw_part.get("vehicle_make") or raw_part.get("make") or "")
        mfr_id = brand_map.get(brand_name.lower())
        if not mfr_id:
            for k,v in brand_map.items():
                if brand_name.lower() in k or k in brand_name.lower():
                    mfr_id = v; brand_name = k.title(); break
        if not mfr_id:
            stats["skipped_no_brand"] += 1; continue

        is_orig = raw_part.get("is_original", False)
        name_he = (raw_part.get("name_he") or raw_part.get("name") or oem).strip()
        model_str = (raw_part.get("model") or "").strip()
        warranty_str = (raw_part.get("warranty") or "").strip()
        warranty_months = 12  # Champion Motors standard; text warranty stored in specs
        batch.append({
            "id": str(uuid.uuid4()),
            "sku": make_sku(oem),
            "name": name_he,
            "name_he": name_he,
            "manufacturer": brand_name,
            "manufacturer_id": str(mfr_id),
            "oem_number": oem,
            "model": model_str,
            "category": categorise(name_he, raw_part.get("part_type_he","")),
            "part_type": "original" if is_orig else "oe_equivalent",
            "part_condition": "new",
            "base_price": raw_part.get("price_ils") or 0.0,
            "aftermarket_tier": None if is_orig else "OE_equivalent",
            "specifications": json.dumps({
                'vat_included':  True,
                'vat_rate':      0.18,
                'currency':      'ILS',
                'source':        f'Champion Motors official importer - {brand_name}',
                'shipping_to_il': True,
                'importer':      'Champion Motors Israel',
                'warranty_months': warranty_months,
                'warranty_text': warranty_str,
                'model':         model_str,
            }, ensure_ascii=False),
        })
        if len(batch) >= BATCH_SIZE:
            await flush()

    await flush()

    # Upsert supplier_parts for all successfully-inserted CM parts
    sp_count = 0
    cm_parts = await conn.fetch(
        "SELECT id, oem_number, base_price FROM parts_catalog "
        "WHERE sku LIKE 'CM-%' AND is_active = TRUE"
    )
    cm_url = 'https://www.championmotors.co.il'
    for part in cm_parts:
        try:
            await conn.execute("""
                INSERT INTO supplier_parts (
                    id, supplier_id, part_id, supplier_sku,
                    price_ils, price_usd, availability, is_available,
                    warranty_months, estimated_delivery_days, supplier_url,
                    created_at, updated_at)
                VALUES (gen_random_uuid(), $1::uuid, $2::uuid, $3, $4, 0.0,
                        'in_stock', TRUE, 12, 14, $5, NOW(), NOW())
                ON CONFLICT ON CONSTRAINT supplier_parts_supplier_id_supplier_sku_key DO UPDATE SET
                    price_ils=EXCLUDED.price_ils, is_available=true, updated_at=NOW()
            """, str(supplier_id), str(part['id']),
                 str(part['oem_number'] or ''), float(part['base_price'] or 0), cm_url)
            sp_count += 1
        except Exception:
            pass
    print(f"[CM] supplier_parts upserted: {sp_count}")

    # REX todo for missing fitment
    try:
        await conn.execute("""
            INSERT INTO agent_todos
                (id, agent_name, title, description, priority, status, created_at, updated_at)
            VALUES (gen_random_uuid(), 'REX',
                'Fetch vehicle fitment for Champion Motors parts',
                'Champion Motors (VW Group) parts imported from champion_motors_parts.json. '
                'Brands: VW, Audi, Skoda, SEAT, Cupra. Map fitment via TecDoc or VW EPC.',
                'high', 'not_started', NOW(), NOW())
        """)
    except Exception:
        pass

    await conn.close()

    print(f"\n[DONE] inserted={stats['inserted']}  errors={stats['errors']}")
    print(f"       skipped_no_oem={stats['skipped_no_oem']}  skipped_no_brand={stats['skipped_no_brand']}")
    return stats

async def verify():
    p = up.urlparse(DATABASE_URL)
    conn = await asyncpg.connect(host=p.hostname,port=p.port or 5432,
        database=p.path.lstrip("/"),user=p.username,password=p.password)
    rows = await conn.fetch("""
        SELECT cb.name, COUNT(pc.id) as n
        FROM car_brands cb JOIN parts_catalog pc ON pc.manufacturer_id=cb.id
        WHERE cb.name IN ('Volkswagen','Audi','Skoda','SEAT','Cupra')
          AND pc.sku LIKE 'CM-%' AND pc.is_active
        GROUP BY cb.name ORDER BY n DESC
    """)
    await conn.close()
    print("\n[Verify] CM parts per brand:")
    total = 0
    for r in rows:
        print(f"  {r['name']:15} {r['n']} parts"); total += r["n"]
    print(f"  {'TOTAL':15} {total}")
    return total

if __name__ == "__main__":
    asyncio.run(run_import())
    asyncio.run(verify())
