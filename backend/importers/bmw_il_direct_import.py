#!/usr/bin/env python3
"""
Import BMW IL prices from champion_motors_parts.json (from bmw.co.il scrape).
JSON format: {oem_number, name_he, name, vehicle_make, model, price_ils (excl. VAT), price_ils_vat, is_original}
Pricing policy: importer_price_ils=price_ils (excl. VAT), base_price=NOT written here (base_price_rule), max_price_ils=price_ils*1.18
"""
import asyncio, json, os, sys, time
import asyncpg
import base_price_rule

# ONE category source of truth. This INSERT used to hardcode 'accessories'
# positionally, which is a REAL category — so the self-healing categorizer
# (which only re-processes 'כללי') would never revisit those parts.
from category_map import categorize_on_ingest

DB = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")
VAT = 0.18

async def run():
    src = sys.argv[1] if len(sys.argv) > 1 else "/opt/autosparefinder/champion_motors_parts.json"
    raw = json.load(open(src))
    parts = raw.get("parts", raw) if isinstance(raw, dict) else raw
    print(f"Loaded {len(parts):,} BMW parts from {src}")

    conn = await asyncpg.connect(DB)
    t0 = time.monotonic()
    updated = inserted = skipped = 0
    touched_part_ids = []
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

        part_spec = json.dumps({
            "importer": "BMW Israel (Colmobil Motors)", "source": "bmw.co.il",
            "vat_rate": VAT, "vat_included": False, "model": model
        }, ensure_ascii=False)

        res = await conn.fetch("""
            UPDATE parts_catalog SET
                importer_price_ils = $1,
                max_price_ils      = $2,
                specifications     = COALESCE(specifications,'{}')::jsonb || $3::jsonb,
                updated_at         = NOW()
            WHERE oem_number = $4 AND manufacturer = 'BMW' AND is_active = true
            RETURNING id::text
        """, cost, retail, part_spec, oem)
        n = len(res)
        if n > 0:
            updated += n
            touched_part_ids.extend(r[0] for r in res)
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
                        gen_random_uuid(), $1, $1, $2, $2, 'BMW', $6,
                        0.0, $3, $4, $4,
                        'original', 'new', true,
                        true, false, $5::jsonb,
                        NOW(), NOW()
                    )
                    ON CONFLICT (sku) DO UPDATE SET
                        importer_price_ils = CASE WHEN EXCLUDED.importer_price_ils > 0 THEN EXCLUDED.importer_price_ils ELSE parts_catalog.importer_price_ils END,
                        max_price_ils      = EXCLUDED.max_price_ils,
                        specifications     = EXCLUDED.specifications,
                        updated_at         = NOW()
                """, oem, name_he, cost, retail, part_spec,
                    categorize_on_ingest(name_he=name_he))
                inserted += 1
                touched_part_ids.append(await conn.fetchval("SELECT id::text FROM parts_catalog WHERE sku = $1", oem))
            except Exception:
                skipped += 1

    await base_price_rule.apply_and_report(conn, touched_part_ids, "bmw_il_direct_import")
    elapsed = time.monotonic() - t0
    print(f"Done in {elapsed:.0f}s: updated={updated:,} inserted={inserted:,} skipped={skipped:,}")

    r = await conn.fetchrow(
        "SELECT COUNT(*) t, COUNT(*) FILTER (WHERE importer_price_ils>0) p "
        "FROM parts_catalog WHERE manufacturer='BMW' AND is_active=true"
    )
    print(f"BMW coverage: {r['p']:,}/{r['t']:,} ({100*r['p']//(r['t'] or 1)}%)")
    await conn.close()

asyncio.run(run())
