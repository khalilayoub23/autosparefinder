#!/usr/bin/env python3
"""
Import BMW IL prices from champion_motors_parts.json (from bmw.co.il scrape).
JSON format: {oem_number, name_he, name, vehicle_make, model, price_ils (excl. VAT), price_ils_vat, is_original}
Pricing policy: importer_price_ils=price_ils (excl. VAT), base_price=price_ils*1.45, max_price_ils=price_ils*1.18
"""
import asyncio, json, os, sys, time
import price_policy as _pp  # the one base_price formula (price_policy.base_price_for_cost)
import asyncpg

# ONE category source of truth. This INSERT used to hardcode 'accessories'
# positionally, which is a REAL category — so the self-healing categorizer
# (which only re-processes 'כללי') would never revisit those parts.
from category_map import categorize_on_ingest

DB = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")
VAT = 0.18

async def run():
    import app_paths
    src = app_paths.require_file(app_paths.cli_or(sys.argv, 1, "CM_JSON", "champion_motors_parts.json", base=app_paths.state_dir()), "bmw_il_direct_import")   # champion_motors_harvester output
    raw = json.load(open(src))
    parts = raw.get("parts", raw) if isinstance(raw, dict) else raw
    print(f"Loaded {len(parts):,} BMW parts from {src}")

    conn = await asyncpg.connect(DB)
    t0 = time.monotonic()
    updated = inserted = skipped = 0
    spec = json.dumps({
        "importer": "BMW Israel (Colmobil Motors)", "source": "bmw.co.il",
        "vat_rate": VAT, "vat_included": False
    }, ensure_ascii=False)

    for p in parts:
        oem = str(p.get("oem_number", "")).strip()
        name_he = str(p.get("name_he") or p.get("name") or oem).strip()
        price = float(p.get("price_ils", 0) or 0)
        model = str(p.get("model", "") or "").strip()

        if not oem or price <= 0:
            skipped += 1
            continue

        cost    = round(price, 2)
        retail  = round(cost * (1 + VAT), 2)
        selling = _pp.base_price_for_cost(cost)

        part_spec = json.dumps({
            "importer": "BMW Israel (Colmobil Motors)", "source": "bmw.co.il",
            "vat_rate": VAT, "vat_included": False, "model": model
        }, ensure_ascii=False)

        res = await conn.execute("""
            UPDATE parts_catalog SET
                importer_price_ils = $1,
                max_price_ils      = $2,
                base_price         = CASE WHEN base_price IS NULL OR base_price = 0 THEN CAST($3 AS numeric) ELSE base_price END,
                specifications     = COALESCE(specifications,'{}')::jsonb || $4::jsonb,
                updated_at         = NOW()
            WHERE oem_number = $5 AND manufacturer = 'BMW' AND is_active = true
        """, cost, retail, selling, part_spec, oem)
        n = int(res.split()[-1])
        if n > 0:
            updated += n
        else:
            try:
                await conn.execute("""
                    INSERT INTO parts_catalog(
                        id, sku, oem_number, name, name_he, manufacturer, category,
                        base_price, importer_price_ils, max_price_ils, min_price_ils,
                        part_type, part_condition, is_active,
                        needs_oem_lookup, master_enriched, specifications,
                        created_at, updated_at
                    ) VALUES(
                        gen_random_uuid(), $1, $1, $2, $2, 'BMW', $7,
                        $3, $4, $5, $5,
                        'original', 'new', true,
                        true, false, $6::jsonb,
                        NOW(), NOW()
                    )
                    ON CONFLICT (sku) DO UPDATE SET
                        importer_price_ils = CASE WHEN EXCLUDED.importer_price_ils > 0 THEN EXCLUDED.importer_price_ils ELSE parts_catalog.importer_price_ils END,
                        max_price_ils      = EXCLUDED.max_price_ils,
                        base_price         = CASE WHEN parts_catalog.base_price IS NULL OR parts_catalog.base_price = 0 THEN EXCLUDED.base_price ELSE parts_catalog.base_price END,
                        specifications     = EXCLUDED.specifications,
                        updated_at         = NOW()
                """, oem, name_he, selling, cost, retail, part_spec,
                    categorize_on_ingest(name_he=name_he))
                inserted += 1
            except Exception:
                skipped += 1

    elapsed = time.monotonic() - t0
    print(f"Done in {elapsed:.0f}s: updated={updated:,} inserted={inserted:,} skipped={skipped:,}")

    r = await conn.fetchrow(
        "SELECT COUNT(*) t, COUNT(*) FILTER (WHERE importer_price_ils>0) p "
        "FROM parts_catalog WHERE manufacturer='BMW' AND is_active=true"
    )
    print(f"BMW coverage: {r['p']:,}/{r['t']:,} ({100*r['p']//(r['t'] or 1)}%)")
    await conn.close()

asyncio.run(run())
