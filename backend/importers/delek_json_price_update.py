#!/usr/bin/env python3
"""
Apply importer_price_ils from Delek brand JSON files to parts_catalog.
Matches by oem_number. Skips cross-contaminated model field (known scraping artifact).
Sources: jlr_parts.json, ford_parts.json, mini_parts.json, land_rover_parts.json
"""
import asyncio, json, os, sys, time, asyncpg
import base_price_rule

DB_URL = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")
VAT = 0.18

SOURCES = [
    # (json_path, [db_manufacturers], importer, price_field, retail_field)
    ("/app/jlr_parts.json",
     ["Jaguar", "Land Rover"],
     "Delek Motors - JLR Israel",
     "price_ils", "price_ils_vat"),

    ("/app/ford_parts.json",
     ["Ford"],
     "Delek Motors - Ford Israel",
     "price_ils", "price_ils_vat"),

    ("/app/mini_parts.json",
     ["MINI"],
     "Delek Motors - MINI Israel",
     "price_ils", "price_ils_vat"),
]


async def update_from_json(conn, path, manufacturers, importer, price_field, retail_field):
    with open(path) as f:
        data = json.load(f)
    parts = data if isinstance(data, list) else data.get('parts', [])
    print(f"\n[{os.path.basename(path)}] {len(parts):,} parts, target: {manufacturers}")

    # Deduplicate by OEM
    deduped = {}
    for p in parts:
        oem = str(p.get('oem_number', '') or '').strip()
        cost = float(p.get(price_field) or 0)
        if not oem or cost <= 0:
            continue
        if oem not in deduped or cost > deduped[oem]['cost']:
            retail = float(p.get(retail_field) or cost * (1 + VAT))
            deduped[oem] = {'cost': cost, 'retail': retail}

    print(f"  Unique OEMs with price: {len(deduped):,}")

    updated = 0
    not_found = 0
    errors = 0
    touched_part_ids = []
    spec_patch = json.dumps({"importer": importer, "vat_included": False, "vat_rate": VAT}, ensure_ascii=False)

    for oem, prices in deduped.items():
        try:
            for mfr in manufacturers:
                res = await conn.fetch("""
                    UPDATE parts_catalog SET
                        importer_price_ils = $1,
                        max_price_ils      = $2,
                        specifications     = COALESCE(specifications,'{}')::jsonb || $3::jsonb,
                        updated_at         = NOW()
                    WHERE oem_number = $4 AND manufacturer = $5 AND is_active = true
                    RETURNING id::text
                """, prices['cost'], prices['retail'], spec_patch, oem, mfr)
                n = len(res)
                updated += n
                touched_part_ids.extend(r_[0] for r_ in res)
                if n == 0 and mfr == manufacturers[-1]:
                    # Also try SKU match (JLR-AJ...)
                    for prefix in ('JLR-', 'FORD-', 'MINI-', 'CM-'):
                        res2 = await conn.fetch("""
                            UPDATE parts_catalog SET
                                importer_price_ils = $1, max_price_ils = $2,
                                specifications = COALESCE(specifications,'{}')::jsonb || $3::jsonb,
                                updated_at = NOW()
                            WHERE sku = $4 AND is_active = true
                            RETURNING id::text
                        """, prices['cost'], prices['retail'], spec_patch, f"{prefix}{oem}")
                        n2 = len(res2)
                        if n2 > 0:
                            updated += n2
                            touched_part_ids.extend(r_[0] for r_ in res2)
                            break
                    else:
                        not_found += 1
        except Exception as e:
            errors += 1
            if errors <= 3:
                print(f"  error [{oem}]: {e}")

    print(f"  updated={updated:,} not_found={not_found:,} errors={errors}")
    await base_price_rule.apply_and_report(conn, touched_part_ids, f"delek_json_price_update:{importer}")   # ONE base_price rule
    return updated


async def run():
    if not DB_URL:
        print("ERROR: DATABASE_URL not set"); sys.exit(1)
    conn = await asyncpg.connect(DB_URL)
    t0 = time.monotonic()
    total = 0
    try:
        for path, mfrs, importer, pf, rf in SOURCES:
            if not os.path.exists(path):
                print(f"SKIP: {path} not found"); continue
            n = await update_from_json(conn, path, mfrs, importer, pf, rf)
            total += n

        # Final IL price counts for these brands
        print(f"\n=== DELEK JSON PRICE UPDATE DONE ({time.monotonic()-t0:.1f}s) ===")
        print(f"  Total rows updated: {total:,}")
        for mfr in ["Jaguar", "Land Rover", "Ford", "MINI"]:
            r = await conn.fetchrow(
                "SELECT COUNT(*) total, COUNT(*) FILTER (WHERE importer_price_ils>0) priced "
                "FROM parts_catalog WHERE manufacturer=$1 AND is_active=true", mfr)
            print(f"  {mfr:<15}: {r['priced']:,}/{r['total']:,} priced ({100*r['priced']//(r['total'] or 1)}%)")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(run())
