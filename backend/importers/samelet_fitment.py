#!/usr/bin/env python3
"""Build vehicle fitment for samelet-imported brands using keyword matching."""
import asyncio, asyncpg, json

DB_URL = "postgresql://autospare:e4b79d75ca640dbe7f259618f078b82f21573e419308f668beed5e20b26b1d43@postgres_catalog:5432/autospare"

# Model → keywords (EN + HE) for matching in part names
BRAND_MODELS = {
    "Jeep": [
        ("Wrangler",    ["wrangler","tj","jk","jl","jku","jeep jk","jeep jl","רנגלר"]),
        ("Grand Cherokee",["grand cherokee","wk","wk2","grandcherokee","grand-cherokee","גרנד צ'ירוקי","גרנד צ'ירוקי"]),
        ("Cherokee",    ["cherokee kl","cherokee xj","kl cherokee","xj cherokee","צ'ירוקי"]),
        ("Compass",     ["compass mp","compass mj","compass","קומפס"]),
        ("Renegade",    ["renegade bu","renegade","רנגייד","ריניגייד"]),
        ("Commander",   ["commander xk","commander","קומנדר"]),
        ("Gladiator",   ["gladiator jt","gladiator","גלדיאטור"]),
        ("Patriot",     ["patriot mk","patriot","פטריוט"]),
        ("Liberty",     ["liberty kj","liberty","ליברטי"]),
        ("Avenger",     ["avenger pj","avenger","אוונג'ר"]),
    ],
    "Alfa Romeo": [
        ("Giulia",      ["giulia","ג'וליה"]),
        ("Stelvio",     ["stelvio","סטלביו"]),
        ("Giulietta",   ["giulietta","ג'וליטה"]),
        ("156",         ["alfa 156","156 alfa","156"]),
        ("159",         ["alfa 159","159 alfa","159"]),
        ("147",         ["alfa 147","147 alfa","147"]),
        ("Mito",        ["mito","מיטו"]),
        ("Brera",       ["brera","ברירה"]),
        ("Spider",      ["alfa spider","spider"]),
        ("GTV",         ["alfa gtv","gtv"]),
        ("Tonale",      ["tonale","טונלה"]),
        ("4C",          ["4c alfa","alfa 4c"]),
    ],
    "Fiat": [
        ("500",         ["fiat 500","500 fiat","חמש מאות"]),
        ("Punto",       ["punto","פונטו"]),
        ("Panda",       ["panda","פנדה"]),
        ("Bravo",       ["bravo","ברבו"]),
        ("Tipo",        ["tipo","טיפו"]),
        ("Doblo",       ["doblo","דובלו"]),
        ("Ducato",      ["ducato","דוקאטו"]),
        ("Stilo",       ["stilo","סטילו"]),
        ("Linea",       ["linea","ליניאה"]),
        ("Egea",        ["egea","אגיה"]),
        ("Fullback",    ["fullback","פולבק"]),
    ],
    "Abarth": [
        ("500",         ["abarth 500","500 abarth"]),
        ("595",         ["595","abarth 595"]),
        ("695",         ["695","abarth 695"]),
        ("Punto",       ["abarth punto","grande punto abarth"]),
        ("124 Spider",  ["abarth 124","124 spider abarth"]),
    ],
    "RAM": [
        ("1500",        ["ram 1500","1500 ram","dt 1500","ds/dj 1500"]),
        ("2500",        ["ram 2500","2500 ram"]),
        ("3500",        ["ram 3500","3500 ram"]),
        ("ProMaster",   ["promaster","פרומסטר"]),
        ("Rebel",       ["rebel","ריבל"]),
        ("TRX",         ["trx","ram trx"]),
    ],
    "Subaru": [
        ("Impreza",     ["impreza","אימפרזה"]),
        ("Forester",    ["forester","פורסטר"]),
        ("Outback",     ["outback","אאוטבק"]),
        ("Legacy",      ["legacy","לגסי"]),
        ("XV",          ["subaru xv","xv subaru","xv"]),
        ("WRX",         ["wrx","wrx sti","sti"]),
        ("BRZ",         ["brz","subaru brz"]),
        ("Crosstrek",   ["crosstrek","קרוסטרק"]),
        ("Ascent",      ["ascent","אסנט"]),
        ("Levorg",      ["levorg","לבורג"]),
    ],
    "Iveco": [
        ("Daily",       ["daily","דיילי"]),
        ("Eurocargo",   ["eurocargo","יורוקרגו"]),
        ("Stralis",     ["stralis","סטרליס"]),
        ("Trakker",     ["trakker","טרקר"]),
        ("Massif",      ["massif","מסיף"]),
    ],
    "Hongqi": [
        ("H5",          ["h5","hongqi h5"]),
        ("H9",          ["h9","hongqi h9"]),
        ("E-HS9",       ["e-hs9","ehs9","hongqi ehs9"]),
        ("HS5",         ["hs5","hongqi hs5"]),
        ("HS7",         ["hs7","hongqi hs7"]),
    ],
    "WEY": [
        ("VV5",         ["vv5","wey vv5"]),
        ("VV6",         ["vv6","wey vv6"]),
        ("VV7",         ["vv7","wey vv7"]),
        ("Coffee 01",   ["coffee 01","coffee01"]),
        ("Macchiato",   ["macchiato","מקיאטו"]),
    ],
}

GENERIC_FITMENT = {"brand": "", "model": "All Models", "years": "2000-2026"}

async def build_fitment_for_brand(conn, brand, models):
    print(f"\n[{brand}] Building fitment...")
    # Fetch all active parts for this brand
    all_parts = await conn.fetch(
        "SELECT id, name, name_he FROM parts_catalog WHERE manufacturer=$1 AND is_active=TRUE",
        brand
    )
    if not all_parts:
        print(f"  No parts found for {brand}")
        return 0

    # Map id → matched models
    matched = {}  # id → list of vehicle entries

    for model_name, keywords in models:
        for part in all_parts:
            pid = part["id"]
            name_en = (part["name"] or "").lower()
            name_he = (part["name_he"] or "").lower()
            combined = name_en + " " + name_he
            if any(kw.lower() in combined for kw in keywords):
                if pid not in matched:
                    matched[pid] = []
                matched[pid].append({
                    "brand": brand,
                    "model": model_name,
                    "years": "2000-2026"
                })

    # Assign generic fitment to unmatched parts
    unmatched = [p["id"] for p in all_parts if p["id"] not in matched]

    print(f"  {len(matched)} parts matched to specific models, {len(unmatched)} get generic fitment")

    # Update in batches
    updated = 0
    BATCH = 25
    # Specific model matches
    items = list(matched.items())
    for i in range(0, len(items), BATCH):
        batch = items[i:i+BATCH]
        for pid, vehicles in batch:
            await conn.execute(
                "UPDATE parts_catalog SET compatible_vehicles=$1::jsonb, updated_at=NOW() WHERE id=$2",
                json.dumps(vehicles), pid
            )
            updated += 1

    # Generic fitment
    generic = json.dumps([{**GENERIC_FITMENT, "brand": brand}])
    for i in range(0, len(unmatched), BATCH):
        batch = unmatched[i:i+BATCH]
        for pid in batch:
            await conn.execute(
                "UPDATE parts_catalog SET compatible_vehicles=$1::jsonb, updated_at=NOW() WHERE id=$2",
                generic, pid
            )
            updated += 1

    print(f"  [{brand}] fitment updated for {updated} parts")
    return updated

async def main():
    print("Samelet Fitment Builder starting...")
    import os
    single = os.environ.get("SINGLE_BRAND", "")

    conn = await asyncpg.connect(DB_URL)
    try:
        brands_to_run = BRAND_MODELS
        if single:
            brands_to_run = {k: v for k, v in BRAND_MODELS.items() if k == single}

        total = 0
        for brand, models in brands_to_run.items():
            n = await conn.fetchval(
                "SELECT COUNT(*) FROM parts_catalog WHERE manufacturer=$1 AND is_active=TRUE", brand
            )
            if n == 0:
                print(f"[{brand}] No parts in DB, skipping")
                continue
            count = await build_fitment_for_brand(conn, brand, models)
            total += count

        print(f"\nFITMENT COMPLETE — total parts updated: {total}")
    finally:
        await conn.close()

if __name__ == "__main__":
    asyncio.run(main())
