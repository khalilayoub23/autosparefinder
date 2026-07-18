#!/usr/bin/env python3
"""
Import prices and fitment for:
1. NIO     - price update (already in DB, 2,269 parts)
2. Voyah   - fresh insert + fitment (1,870 parts)
3. M-Hero  - fresh insert + fitment (merged mhero + mhero2, ~2,348 parts)
4. VAG     - price update for Audi/VW/Skoda/SEAT from champion_motors_parts.json
5. Lexus   - price update from toyota_il_parts.json (1,944 parts with LEXUS model)
"""
import asyncio, gc, json, os, re, sys, time, uuid, asyncpg

DB_URL = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")
VAT = 0.18

MFR_IDS = {
    "NIO":        "a7748117-7388-4b69-b926-2486a90c9c31",
    "Voyah":      "780b9331-7440-44a1-82f1-6c93109cd4d0",
    "M-Hero":     "727a69d6-6ecb-4993-9a4c-8740c48219b4",
    "Audi":       "4a718e3c-5b47-478d-9c62-0b6b5135593e",
    "Volkswagen": "04877cea-0889-4b57-978a-cff0a8f1ed25",
    "Skoda":      "e062ba07-930c-489f-b43e-48bf90a42d11",
    "SEAT":       "ebb4521b-6742-4cc2-b1d0-207903ea085a",
    "Lexus":      "adbe811b-c063-40b7-9cc4-d28b600880c1",
}

HE_TO_BRAND = {
    "אודי": "Audi",
    "vw": "Volkswagen",
    "סקודה": "Skoda",
    "סיאט": "SEAT",
    "מסחריות vw": "Volkswagen",
}

YEAR_RE = re.compile(r'\b(19|20)\d{2}\b')


def load_json(path):
    if not os.path.exists(path):
        return []
    with open(path) as f:
        d = json.load(f)
    return d if isinstance(d, list) else d.get('parts', [])


def clean_model(raw: str):
    s = raw.strip()
    if not s or s in ('מרובה דגמים', ' ', ''):
        return None
    return s


def parse_year(model_str: str):
    m = YEAR_RE.search(model_str)
    return int(m.group(0)) if m else None


# ─── 1. NIO PRICE UPDATE ─────────────────────────────────────────────────────

async def update_nio(conn):
    parts = load_json("/app/nio_parts.json")
    print(f"\n[NIO] {len(parts):,} parts — price update")
    updated = not_found = 0
    spec = json.dumps({"importer": "Delek Motors - NIO Israel", "source": "nio_parts.json"})
    for p in parts:
        oem = str(p.get('oem_number', '') or '').strip()
        cost = float(p.get('price_ils') or 0)
        retail = float(p.get('price_ils_vat') or 0)
        if not oem or cost <= 0:
            continue
        if retail <= 0:
            retail = round(cost * (1 + VAT), 2)
        try:
            res = await conn.execute("""
                UPDATE parts_catalog SET
                    importer_price_ils=$1, max_price_ils=$2,
                    base_price=round(($1 * 1.45)::numeric, 2),
                    specifications=COALESCE(specifications,'{}')::jsonb || $3::jsonb,
                    updated_at=NOW()
                WHERE (oem_number=$4 OR sku=$4) AND manufacturer='NIO' AND is_active=true
            """, cost, retail, spec, oem)
            n = int(res.split()[-1])
            updated += n
            if n == 0:
                not_found += 1
        except Exception as e:
            print(f"  NIO err [{oem}]: {e}")
    after = await conn.fetchval(
        "SELECT COUNT(*) FROM parts_catalog WHERE manufacturer='NIO' AND is_active=true AND importer_price_ils>0")
    print(f"  updated={updated:,} not_found={not_found:,} → NIO priced: {after:,}")
    return updated


# ─── 2. VOYAH FRESH INSERT ───────────────────────────────────────────────────

async def import_voyah(conn):
    parts = load_json("/app/voyah_parts.json")
    print(f"\n[Voyah] {len(parts):,} parts — fresh insert")
    mfr_id = MFR_IDS["Voyah"]
    inserted = skipped = fitment = 0
    for p in parts:
        oem = str(p.get('oem_number', '') or '').strip()
        cost = float(p.get('price_ils') or 0)
        retail = float(p.get('price_ils_vat') or 0)
        name_he = str(p.get('name_he', '') or '').strip()
        name_en = str(p.get('name', '') or '').strip()
        if not oem or cost <= 0:
            skipped += 1
            continue
        if retail <= 0:
            retail = round(cost * (1 + VAT), 2)
        model = clean_model(str(p.get('model', '') or ''))
        spec = json.dumps({"importer": "Delek Motors - Voyah Israel", "source": "voyah_parts.json",
                           "is_original": p.get('is_original', True)})
        part_id = str(uuid.uuid4())
        try:
            row = await conn.fetchrow("""
                INSERT INTO parts_catalog (
                    id, sku, oem_number, name_he, name, manufacturer, manufacturer_id,
                    importer_price_ils, max_price_ils, base_price,
                    is_active, specifications, created_at, updated_at
                ) VALUES ($1::uuid, $2, $3, $4, $5, 'Voyah', $6::uuid,
                          $7, $8, round(($7 * 1.45)::numeric, 2), true, $9::jsonb, NOW(), NOW())
                ON CONFLICT (sku) DO UPDATE SET
                    importer_price_ils=$7, max_price_ils=$8,
                    base_price=round(($7 * 1.45)::numeric, 2),
                    specifications=COALESCE(parts_catalog.specifications,'{}')::jsonb || $9::jsonb,
                    updated_at=NOW()
                RETURNING id
            """, part_id, f"VOYAH-{oem}", oem, name_he or name_en, name_en,
                mfr_id, cost, retail, spec)
            if row:
                pid = str(row["id"])
                inserted += 1
                if model:
                    yr = parse_year(model) or 2020
                    try:
                        await conn.execute("""
                            INSERT INTO part_vehicle_fitment (
                                id, part_id, manufacturer, manufacturer_id,
                                model, year_from, year_to, notes, created_at, updated_at
                            ) VALUES (gen_random_uuid(), $1::uuid, 'Voyah', $2::uuid,
                                      $3, $4, NULL, 'Voyah IL import', NOW(), NOW())
                            ON CONFLICT (part_id, manufacturer, model, year_from) DO NOTHING
                        """, pid, mfr_id, model, yr)
                        fitment += 1
                    except Exception:
                        pass
        except Exception as e:
            skipped += 1
            if skipped <= 3:
                print(f"  Voyah err [{oem}]: {e}")
    print(f"  inserted/updated={inserted:,} skipped={skipped:,} fitment={fitment:,}")
    return inserted


# ─── 3. M-HERO FRESH INSERT ──────────────────────────────────────────────────

async def import_mhero(conn):
    parts1 = load_json("/app/mhero_parts.json")
    parts2 = load_json("/app/mhero2_parts.json")
    deduped = {}
    for p in parts1 + parts2:
        oem = str(p.get('oem_number', '') or '').strip()
        if oem:
            deduped.setdefault(oem, p)
    parts = list(deduped.values())
    print(f"\n[M-Hero] {len(parts):,} unique parts (merged) — fresh insert")
    mfr_id = MFR_IDS["M-Hero"]
    inserted = skipped = fitment = 0
    for p in parts:
        oem = str(p.get('oem_number', '') or '').strip()
        cost = float(p.get('price_ils') or 0)
        retail = float(p.get('price_ils_vat') or 0)
        name_he = str(p.get('name_he', '') or '').strip()
        name_en = str(p.get('name', '') or '').strip()
        if not oem or cost <= 0:
            skipped += 1
            continue
        if retail <= 0:
            retail = round(cost * (1 + VAT), 2)
        model = clean_model(str(p.get('model', '') or ''))
        spec = json.dumps({"importer": "Delek Motors - M-Hero Israel", "source": "mhero_parts.json",
                           "is_original": p.get('is_original', True)})
        part_id = str(uuid.uuid4())
        try:
            row = await conn.fetchrow("""
                INSERT INTO parts_catalog (
                    id, sku, oem_number, name_he, name, manufacturer, manufacturer_id,
                    importer_price_ils, max_price_ils, base_price,
                    is_active, specifications, created_at, updated_at
                ) VALUES ($1::uuid, $2, $3, $4, $5, 'M-Hero', $6::uuid,
                          $7, $8, round(($7 * 1.45)::numeric, 2), true, $9::jsonb, NOW(), NOW())
                ON CONFLICT (sku) DO UPDATE SET
                    importer_price_ils=$7, max_price_ils=$8,
                    base_price=round(($7 * 1.45)::numeric, 2),
                    specifications=COALESCE(parts_catalog.specifications,'{}')::jsonb || $9::jsonb,
                    updated_at=NOW()
                RETURNING id
            """, part_id, f"MHERO-{oem}", oem, name_he or name_en, name_en,
                mfr_id, cost, retail, spec)
            if row:
                pid = str(row["id"])
                inserted += 1
                if model:
                    yr = parse_year(model) or 2021
                    try:
                        await conn.execute("""
                            INSERT INTO part_vehicle_fitment (
                                id, part_id, manufacturer, manufacturer_id,
                                model, year_from, year_to, notes, created_at, updated_at
                            ) VALUES (gen_random_uuid(), $1::uuid, 'M-Hero', $2::uuid,
                                      $3, $4, NULL, 'M-Hero IL import', NOW(), NOW())
                            ON CONFLICT (part_id, manufacturer, model, year_from) DO NOTHING
                        """, pid, mfr_id, model, yr)
                        fitment += 1
                    except Exception:
                        pass
        except Exception as e:
            skipped += 1
            if skipped <= 3:
                print(f"  M-Hero err [{oem}]: {e}")
    print(f"  inserted/updated={inserted:,} skipped={skipped:,} fitment={fitment:,}")
    return inserted


# ─── 4. VAG PRICE UPDATE ─────────────────────────────────────────────────────

def extract_brands(vehicle_make: str) -> list:
    raw = vehicle_make.lower()
    brands = []
    for he, en in HE_TO_BRAND.items():
        if he in raw and en not in brands:
            brands.append(en)
    if not brands and 'vw' in raw:
        brands.append("Volkswagen")
    return brands


async def update_vag(conn):
    parts = load_json("/app/champion_motors_parts.json")
    non_bmw = [p for p in parts if p.get('vehicle_make', '') != 'BMW' and float(p.get('price_ils') or 0) > 0]
    print(f"\n[VAG Champion Motors] {len(non_bmw):,} non-BMW parts")

    deduped = {}
    for p in non_bmw:
        oem = str(p.get('oem_number', '') or '').strip()
        if not oem:
            continue
        cost = float(p.get('price_ils') or 0)
        retail = float(p.get('price_ils_vat') or 0)
        brands = extract_brands(p.get('vehicle_make', ''))
        if not brands:
            brands = ["Audi", "Volkswagen", "Skoda", "SEAT"]
        if retail <= 0:
            retail = round(cost * (1 + VAT), 2)
        if oem not in deduped or cost > deduped[oem]['cost']:
            deduped[oem] = {'cost': cost, 'retail': retail, 'brands': brands}

    print(f"  Unique OEMs: {len(deduped):,}")
    spec = json.dumps({"importer": "Champion Motors Israel", "source": "champion_motors_parts.json",
                       "vat_rate": VAT})
    updated = not_found = 0
    for oem, d in deduped.items():
        found = False
        for brand in d['brands']:
            try:
                res = await conn.execute("""
                    UPDATE parts_catalog SET
                        importer_price_ils=$1, max_price_ils=$2,
                        base_price=round(($1 * 1.45)::numeric, 2),
                        specifications=COALESCE(specifications,'{}')::jsonb || $3::jsonb,
                        updated_at=NOW()
                    WHERE oem_number=$4 AND manufacturer=$5 AND is_active=true
                """, d['cost'], d['retail'], spec, oem, brand)
                n = int(res.split()[-1])
                if n > 0:
                    updated += n
                    found = True
            except Exception as e:
                print(f"  VAG err [{oem}/{brand}]: {e}")
        if not found:
            not_found += 1

    for mfr in ["Audi", "Volkswagen", "Skoda", "SEAT"]:
        r = await conn.fetchrow(
            "SELECT COUNT(*) total, COUNT(*) FILTER (WHERE importer_price_ils>0) priced "
            "FROM parts_catalog WHERE manufacturer=$1 AND is_active=true", mfr)
        print(f"  {mfr:<12}: {r['priced']:,}/{r['total']:,} ({100*r['priced']//(r['total'] or 1)}%)")
    print(f"  updated={updated:,} not_found={not_found:,}")
    return updated


# ─── 5. LEXUS FROM TOYOTA IL FILE ────────────────────────────────────────────

def parse_lexus_model(raw: str):
    s = re.sub(r'\bLEXUS\b', '', raw, flags=re.IGNORECASE).strip()
    yr = parse_year(s) or 2015
    s = YEAR_RE.sub('', s).strip()
    model = re.sub(r'\s+', ' ', s).strip()
    return model, yr


async def update_lexus(conn):
    all_parts = load_json("/app/toyota_il_parts.json")
    lexus_parts = []
    for p in all_parts:
        models = [m for m in (p.get('models') or []) if 'LEXUS' in m.upper()]
        if models:
            lexus_parts.append({**p, '_lexus_models': models})
    print(f"\n[Lexus from Toyota IL] {len(lexus_parts):,} parts with Lexus models")
    mfr_id = MFR_IDS["Lexus"]
    spec = json.dumps({"importer": "Lexus Israel (Union Motors)", "source": "toyota_il_parts.json",
                       "vat_rate": VAT})
    updated = fitment_inserted = not_found = 0
    for p in lexus_parts:
        oem = str(p.get('oem', '') or '').strip()
        price_raw = float(p.get('price') or 0)
        if not oem or price_raw <= 0:
            continue
        # Toyota IL prices are retail ILS (incl. VAT)
        retail = price_raw
        cost = round(retail / (1 + VAT), 2)
        try:
            res = await conn.execute("""
                UPDATE parts_catalog SET
                    importer_price_ils=$1, max_price_ils=$2,
                    base_price=round(($1 * 1.45)::numeric, 2),
                    specifications=COALESCE(specifications,'{}')::jsonb || $3::jsonb,
                    updated_at=NOW()
                WHERE oem_number=$4 AND manufacturer='Lexus' AND is_active=true
            """, cost, retail, spec, oem)
            n = int(res.split()[-1])
            updated += n
            if n == 0:
                not_found += 1
                continue
            rows = await conn.fetch(
                "SELECT id FROM parts_catalog WHERE oem_number=$1 AND manufacturer='Lexus' AND is_active=true", oem)
            for row in rows:
                pid = str(row["id"])
                for model_raw in p['_lexus_models']:
                    model, yr = parse_lexus_model(model_raw)
                    if not model:
                        continue
                    try:
                        await conn.execute("""
                            INSERT INTO part_vehicle_fitment (
                                id, part_id, manufacturer, manufacturer_id,
                                model, year_from, year_to, notes, created_at, updated_at
                            ) VALUES (gen_random_uuid(), $1::uuid, 'Lexus', $2::uuid,
                                      $3, $4, NULL, 'Toyota IL fitment', NOW(), NOW())
                            ON CONFLICT (part_id, manufacturer, model, year_from) DO NOTHING
                        """, pid, mfr_id, model, yr)
                        fitment_inserted += 1
                    except Exception:
                        pass
        except Exception as e:
            print(f"  Lexus err [{oem}]: {e}")

    after = await conn.fetchval(
        "SELECT COUNT(*) FROM parts_catalog WHERE manufacturer='Lexus' AND is_active=true AND importer_price_ils>0")
    after_fit = await conn.fetchval(
        "SELECT COUNT(DISTINCT pc.id) FROM parts_catalog pc "
        "JOIN part_vehicle_fitment pvf ON pvf.part_id=pc.id WHERE pc.manufacturer='Lexus'")
    print(f"  updated={updated:,} not_found={not_found:,} fitment={fitment_inserted:,}")
    print(f"  Lexus priced: {after:,}  with fitment: {after_fit:,}")
    return updated


# ─── MAIN ────────────────────────────────────────────────────────────────────

async def run():
    if not DB_URL:
        print("ERROR: DATABASE_URL not set"); sys.exit(1)
    conn = await asyncpg.connect(DB_URL)
    t0 = time.monotonic()
    try:
        n_nio    = await update_nio(conn)
        n_voyah  = await import_voyah(conn)
        n_mhero  = await import_mhero(conn)
        n_vag    = await update_vag(conn)
        n_lexus  = await update_lexus(conn)
        print(f"\n=== ALL DONE ({time.monotonic()-t0:.1f}s) ===")
        print(f"  NIO priced:    {n_nio:,}")
        print(f"  Voyah inserts: {n_voyah:,}")
        print(f"  M-Hero inserts:{n_mhero:,}")
        print(f"  VAG updated:   {n_vag:,}")
        print(f"  Lexus updated: {n_lexus:,}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(run())
