#!/usr/bin/env python3
"""
Post-import fitment writer for Cadillac, GMC, Buick, Isuzu.
Reads model from specifications JSON → writes part_vehicle_fitment rows.
Run after brand importers complete.
"""
import asyncio, asyncpg, os, re, time, json

DB = os.environ.get("DATABASE_URL","").replace("postgresql+asyncpg://","postgresql://")

BRAND_MODEL_MAP = {
    "Cadillac": {
        "Escalade": (2002, 2024), "CT5": (2020, 2024), "CT4": (2020, 2024),
        "CT6": (2016, 2023), "XT5": (2016, 2024), "XT6": (2020, 2024),
        "XT4": (2019, 2024), "XTS": (2013, 2019), "ATS": (2013, 2019),
        "CTS": (2003, 2019), "SRX": (2004, 2016), "STS": (2005, 2011),
        "DTS": (2006, 2011), "BLS": (2006, 2010),
    },
    "GMC": {
        "Suburban": (1992, 2024), "Yukon": (1992, 2024), "Sierra": (1988, 2024),
        "Canyon": (2004, 2024), "Terrain": (2010, 2024), "Acadia": (2007, 2024),
        "Envoy": (2002, 2009), "Sonoma": (1994, 2004),
    },
    "Buick": {
        "Enclave": (2008, 2024), "Encore": (2013, 2024), "Envision": (2016, 2024),
        "LaCrosse": (2005, 2019), "Verano": (2012, 2017), "Regal": (2011, 2017),
        "Skylark": (1994, 1998), "Century": (1997, 2005), "Park Avenue": (1991, 2005),
    },
    "Isuzu": {
        "D-MAX": (2002, 2024), "Trooper": (1992, 2002), "Rodeo": (1989, 2004),
        "Frontera": (1991, 2004), "Axiom": (2002, 2004), "Amigo": (1989, 1994),
        "Pickup": (1988, 1997), "Stylus": (1990, 1993), "Gemini": (1985, 1990),
    },
}

def extract_model_from_specs(specs: str | dict, manufacturer: str) -> str | None:
    if not specs:
        return None
    d = specs if isinstance(specs, dict) else {}
    if isinstance(specs, str):
        try:
            d = json.loads(specs)
        except Exception:
            # Try regex on raw string
            m = re.search(r'Model:\s*([A-Za-z0-9 \-]+)', specs)
            return m.group(1).strip() if m else None
    
    # Try common keys
    for key in ("model", "vehicle_model", "degem_nm", "Model"):
        if key in d:
            return str(d[key]).strip()
    
    # Try description field
    desc = d.get("description", "") or ""
    m = re.search(r'Model:\s*([A-Za-z0-9 \-]+)', desc)
    if m:
        return m.group(1).strip()
    return None


async def main():
    conn = await asyncpg.connect(DB)
    total_fitment = 0
    
    for brand, model_years in BRAND_MODEL_MAP.items():
        # Get manufacturer_id
        mfr = await conn.fetchrow("SELECT id FROM car_brands WHERE LOWER(name)=LOWER($1) LIMIT 1", brand)
        if not mfr:
            print(f"  [{brand}] not in car_brands, skipping", flush=True)
            continue
        mfr_id = str(mfr["id"])
        
        # Find parts imported in last 2h with no fitment
        parts = await conn.fetch("""
            SELECT pc.id::text, pc.oem_number, pc.specifications
            FROM parts_catalog pc
            WHERE pc.manufacturer = $1
              AND pc.is_active = true
              AND pc.updated_at > NOW() - INTERVAL '2 hours'
              AND NOT EXISTS (
                SELECT 1 FROM part_vehicle_fitment pvf WHERE pvf.part_id = pc.id
              )
        """, brand)
        
        if not parts:
            print(f"  [{brand}] no newly imported parts without fitment", flush=True)
            continue
        
        print(f"  [{brand}] {len(parts):,} new parts to add fitment", flush=True)
        inserted = 0
        
        for p in parts:
            specs = p["specifications"]
            model_hint = extract_model_from_specs(specs, brand)
            
            # Find matching models
            if model_hint:
                # Try to match to known models
                matched_models = []
                for model_name, (yr_from, yr_to) in model_years.items():
                    if model_name.lower() in model_hint.lower() or model_hint.lower() in model_name.lower():
                        matched_models.append((model_name, yr_from, yr_to))
                
                if not matched_models:
                    # Use model hint directly
                    matched_models = [(model_hint, 2000, 2024)]
            else:
                # No model hint — assign to all brand models (generic)
                matched_models = [(m, y1, y2) for m, (y1, y2) in list(model_years.items())[:3]]
            
            for model_name, yr_from, yr_to in matched_models:
                try:
                    await conn.execute("""
                        INSERT INTO part_vehicle_fitment(
                            id, part_id, manufacturer, manufacturer_id,
                            model, year_from, year_to, notes,
                            created_at, updated_at
                        ) VALUES (
                            gen_random_uuid(), $1::uuid, $2, $3::uuid,
                            $4, $5, $6, 'IL importer catalog fitment',
                            NOW(), NOW()
                        ) ON CONFLICT (part_id, manufacturer, model, year_from) DO NOTHING
                    """, p["id"], brand, mfr_id, model_name, yr_from, yr_to)
                    inserted += 1
                except Exception as e:
                    pass
        
        total_fitment += inserted
        print(f"  [{brand}] fitment rows inserted: {inserted:,}", flush=True)
    
    print(f"\n[fitment_backfill] DONE: {total_fitment:,} fitment rows total", flush=True)
    await conn.close()

asyncio.run(main())
