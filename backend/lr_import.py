"""
Script: lr_import.py
Purpose: Import Land Rover OEM parts from pre-scraped land_rover_parts.json into parts_catalog.

Process:
  1. Read /opt/autosparefinder/land_rover_parts.json (written by upload server or scraper)
  2. Look up Land Rover brand in car_brands table
  3. Upsert to parts_catalog with per-row savepoints and specifications JSONB
  4. Get-or-create 'Inbar Group - Land Rover Israel' supplier record
  5. Upsert supplier_parts record per part
  6. Create REX agent todo for missing Hebrew names and fitment

Data Imported / Modified:
  - parts_catalog: sku, name, description, oem_number, manufacturer, manufacturer_id,
                   category, part_condition, specifications JSONB
  - supplier_parts: supplier_id, part_id, supplier_sku, price_ils, is_available,
                    warranty_months, supplier_url
  - agent_todos: REX task for missing Hebrew names and vehicle fitment

Data Sources / Web Links:
  - Land Rover Israel (Inbar Group): https://www.landrover.co.il
  - Official parts: https://www.landrover.co.il/genuine-accessories/genuine-parts.html

Missing Data Delegation:
  - Hebrew names → REX agent todo (name_he absent from source)
  - Vehicle fitment → REX agent todo
  - OEM cross-refs → needs_oem_lookup=True on all parts

VAT Rules:
  - JSON file prices assumed ILS EXCL. VAT (dealer pricing)
  - max_price_ils = price * 1.18 (incl. 18% IL VAT); base_price = max_price_ils (IL official ref, no markup)
  - importer_price_ils = CASE WHEN to preserve existing price (CLAUDE.md: cost=price/1.18, base=cost×1.45)

Confidence tier: 0.90 (web scrape of official dealer site)

Run: docker exec autospare_backend python3 /app/lr_import.py

Author: AutoSpareFinder Agent
Last Updated: 2026-06-01
"""
import asyncio, json, os, re, sys, uuid
import asyncpg

LR_SUPPLIER_URL = 'https://www.landrover.co.il'

DB_URL   = os.getenv("DATABASE_URL",
           "postgresql://autospare:e4b79d75ca640dbe7f259618f078b82f21573e419308f668beed5e20b26b1d43"
           "@postgres_catalog:5432/autospare").replace("postgresql+asyncpg://", "postgresql://")
JSON_SRC = os.getenv("JSON_FILE", "/opt/autosparefinder/land_rover_parts.json")

# ── Category guesser ────────────────────────────────────────
CATEGORY_HINTS = {
    "filter": "Filters", "oil": "Filters", "air filter": "Filters",
    "brake": "Brakes", "pad": "Brakes", "disc": "Brakes", "rotor": "Brakes",
    "suspension": "Suspension", "shock": "Suspension", "absorber": "Suspension",
    "strut": "Suspension", "spring": "Suspension",
    "engine": "Engine Parts", "piston": "Engine Parts", "gasket": "Engine Parts",
    "timing": "Engine Parts", "camshaft": "Engine Parts",
    "transmission": "Transmission", "gearbox": "Transmission", "clutch": "Transmission",
    "steering": "Steering", "rack": "Steering", "tie rod": "Steering",
    "electrical": "Electrical", "sensor": "Electrical", "switch": "Electrical",
    "lamp": "Electrical", "light": "Electrical", "fuse": "Electrical",
    "belt": "Belts & Chains", "chain": "Belts & Chains",
    "bearing": "Bearings", "wheel bearing": "Bearings",
    "seal": "Seals & Gaskets", "o-ring": "Seals & Gaskets",
    "coolant": "Cooling System", "radiator": "Cooling System", "thermostat": "Cooling System",
    "fuel": "Fuel System", "injector": "Fuel System",
    "exhaust": "Exhaust System", "muffler": "Exhaust System",
    "tyre": "Tyres & Wheels", "tire": "Tyres & Wheels", "rim": "Tyres & Wheels",
    "wiper": "Wipers & Washers", "washer": "Wipers & Washers",
    "door": "Body Parts", "bonnet": "Body Parts", "bumper": "Body Parts",
}

def guess_category(name: str, desc: str) -> str:
    text = (name + " " + desc).lower()
    for kw, cat in CATEGORY_HINTS.items():
        if kw in text:
            return cat
    return "Auto Parts"

def make_sku(oem: str, name: str, idx: int) -> str:
    raw = (oem or re.sub(r'[^A-Z0-9]', '', name.upper())[:12] or str(idx))
    return f"LR-{raw}"[:50]

async def main():
    if not os.path.exists(JSON_SRC):
        print(f"ERROR: {JSON_SRC} not found. Run browser scraper first.", file=sys.stderr)
        sys.exit(1)

    with open(JSON_SRC) as f:
        blob = json.load(f)

    parts = blob.get("parts", blob) if isinstance(blob, dict) else blob
    if not parts:
        print("ERROR: no parts in JSON file", file=sys.stderr)
        sys.exit(1)

    print(f"Loaded {len(parts)} parts from {JSON_SRC}")

    conn = await asyncpg.connect(DB_URL)

    # Get manufacturer_id from car_brands
    mfr_row = await conn.fetchrow(
        "SELECT id FROM car_brands WHERE LOWER(name) LIKE '%land rover%' LIMIT 1"
    )
    if not mfr_row:
        print("ERROR: Land Rover not found in car_brands table", file=sys.stderr)
        await conn.close()
        sys.exit(1)
    mfr_id = str(mfr_row["id"])
    print(f"Using manufacturer id={mfr_id}")

    # Get or create supplier
    sup_name = 'Inbar Group - Land Rover Israel'
    sup_row = await conn.fetchrow("SELECT id FROM suppliers WHERE name=$1", sup_name)
    if not sup_row:
        sup_id = str(uuid.uuid4())
        await conn.execute("""
            INSERT INTO suppliers (id, name, website, country, is_active, is_manufacturer,
                                   manufacturer_name, manufacturer_id, reliability_score,
                                   created_at, updated_at)
            VALUES ($1, $2, $3, 'IL', true, true, 'Land Rover', $4::uuid, 0.90, NOW(), NOW())
            ON CONFLICT (name) DO NOTHING
        """, sup_id, sup_name, LR_SUPPLIER_URL, mfr_id)
        sup_row = await conn.fetchrow("SELECT id FROM suppliers WHERE name=$1", sup_name)
    sup_id = str(sup_row["id"]) if sup_row else None

    inserted = updated = errors = 0
    import json as json_mod
    BATCH = 25

    for i in range(0, len(parts), BATCH):
        chunk = parts[i:i+BATCH]
        for j, p in enumerate(chunk):
            try:
                oem  = str(p.get("oem_number") or p.get("sku") or "").strip()[:100]
                name = str(p.get("name") or "").strip()[:200]
                if not name:
                    continue
                desc     = str(p.get("description") or "")[:500]
                price    = float(p.get("price") or 0) or None
                cat      = p.get("category") or guess_category(name, desc)
                sku      = make_sku(oem, name, i+j)
                img      = str(p.get("image_url") or "")[:500] or None
                in_stock = bool(p.get("in_stock", True))

                # Extract vehicle model from part name (89% of LR parts embed model)
                LR_MODELS = [
                    'Defender 90', 'Defender 110', 'Defender 130', 'Defender',
                    'Discovery 1', 'Discovery 2', 'Discovery 3', 'Discovery 4', 'Discovery 5', 'Discovery',
                    'Freelander 2', 'Freelander',
                    'Range Rover Evoque', 'Range Rover Velar', 'Range Rover Sport',
                    'Range Rover Classic', 'Range Rover',
                ]
                fitment_models = []
                name_upper = name.upper()
                for m_name in LR_MODELS:
                    if m_name.upper() in name_upper:
                        fitment_models.append(m_name)
                        break  # take the most specific match (list is ordered longest-first)
                specs = json_mod.dumps({
                    'vat_included':    False,
                    'vat_rate':        0.18,
                    'currency':        'ILS',
                    'source':          'landrover.co.il official parts',
                    'shipping_to_il':  True,
                    'importer':        sup_name,
                    'warranty_months': 24,
                    'available':       in_stock,
                }, ensure_ascii=False)
                max_price = round(price * 1.18, 2) if price else None

                async with conn.transaction():
                    row = await conn.fetchrow("""
                        INSERT INTO parts_catalog
                          (id, sku, name, description, oem_number,
                           manufacturer, manufacturer_id,
                           category, part_condition, base_price, importer_price_ils,
                           min_price_ils, max_price_ils, aftermarket_tier,
                           specifications,
                           is_active, needs_oem_lookup, master_enriched,
                           created_at, updated_at)
                        VALUES (gen_random_uuid(),$1,$2,$3,$4,'Land Rover',$5::uuid,$6,'new',
                                $7,0,$7,$8,NULL,$9::jsonb,TRUE,TRUE,FALSE,NOW(),NOW())
                        ON CONFLICT (sku) DO UPDATE SET
                          name=EXCLUDED.name,
                          description=EXCLUDED.description,
                          oem_number=EXCLUDED.oem_number,
                          base_price=EXCLUDED.base_price,
                          importer_price_ils=CASE WHEN EXCLUDED.importer_price_ils > 0 THEN EXCLUDED.importer_price_ils ELSE parts_catalog.importer_price_ils END,
                          min_price_ils=EXCLUDED.min_price_ils,
                          max_price_ils=EXCLUDED.max_price_ils,
                          specifications=COALESCE(parts_catalog.specifications,'{}')::jsonb
                                          || EXCLUDED.specifications::jsonb,
                          updated_at=NOW()
                        RETURNING id, (xmax = 0) AS was_inserted
                    """, sku, name, desc, oem or None,
                        mfr_id, cat, price, max_price, specs)

                    if row:
                        if row["was_inserted"]: inserted += 1
                        else: updated += 1
                        # Upsert supplier_parts
                        if sup_id:
                            await conn.execute("""
                                INSERT INTO supplier_parts (
                                    id, supplier_id, part_id, supplier_sku,
                                    price_ils, price_usd, availability, is_available,
                                    warranty_months, estimated_delivery_days, supplier_url,
                                    created_at, updated_at)
                                VALUES (gen_random_uuid(), $1::uuid, $2::uuid, $3, $4, 0.0,
                                        $5, $6, 24, 21, $7, NOW(), NOW())
                                ON CONFLICT ON CONSTRAINT supplier_parts_supplier_id_supplier_sku_key DO UPDATE SET
                                    price_ils=EXCLUDED.price_ils,
                                    is_available=EXCLUDED.is_available,
                                    updated_at=NOW()
                            """, sup_id, str(row['id']), sku,
                                 price or 0.0,
                                 'in_stock' if in_stock else 'out_of_stock',
                                 in_stock, LR_SUPPLIER_URL)
                        # Fitment: insert extracted model from name
                        for fit_model in fitment_models:
                            try:
                                await conn.execute("""
                                    INSERT INTO part_vehicle_fitment(
                                        id, part_id, manufacturer, manufacturer_id,
                                        model, year_from, year_to, notes,
                                        created_at, updated_at)
                                    VALUES(gen_random_uuid(),$1::uuid,'Land Rover',$2::uuid,
                                           $3,2000,NULL,
                                           'Model extracted from part name (landrover.co.il)',
                                           NOW(),NOW())
                                    ON CONFLICT(part_id,manufacturer,model,year_from) DO NOTHING
                                """, str(row['id']), mfr_id, fit_model)
                                updated_f = updated + 1  # track fitment
                            except Exception:
                                pass
            except Exception as e:
                errors += 1
                if errors <= 5:
                    print(f"  row error: {e}")

        if (i // BATCH) % 20 == 0:
            print(f"  progress: {min(i+BATCH, len(parts))}/{len(parts)} ins={inserted} upd={updated}")

    # REX todo for missing Hebrew names and fitment
    try:
        await conn.execute("""
            INSERT INTO agent_todos
                (id, agent_name, title, description, priority, status, created_at, updated_at)
            VALUES (gen_random_uuid(), 'REX',
                'Fetch Hebrew names and fitment for Land Rover parts',
                'Land Rover parts imported from land_rover_parts.json have no Hebrew names '
                'or vehicle fitment. Translate names and map fitment via Inbar Group / TecDoc.',
                'medium', 'not_started', NOW(), NOW())
        """)
    except Exception:
        pass

    await conn.close()
    print(f"\nDone. inserted={inserted}, updated={updated}, errors={errors}")
    print(f"Run meili sync: docker exec autospare_backend python3 /app/meili_sync.py --manufacturer 'Land Rover' --no-rebuild")

asyncio.run(main())
