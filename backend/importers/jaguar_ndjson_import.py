#!/usr/bin/env python3
"""
Import Jaguar parts from jaguar_parts_raw.ndjson (SNG Barratt sourced, GBP prices).
Converts GBP → ILS at hardcoded exchange rate for pricing.
Format: {part_number, base_part_number, title, manufacturer, price_gbp, applications, part_origin}
Pricing policy: importer_price_ils=price_gbp*GBP_ILS, base_price=NOT written here (base_price_rule), max_price_ils=cost*1.17
"""
import asyncio, json, os, sys, time
import asyncpg
import base_price_rule

# ONE category source of truth. This INSERT hardcoded 'accessories' —
# a REAL category, so the self-healing categorizer (which only revisits
# 'כללי') would never correct those parts.
from category_map import categorize_on_ingest

DB = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")
GBP_ILS = 4.8   # approximate GBP/ILS exchange rate
VAT = 0.18

async def run():
    src = sys.argv[1] if len(sys.argv) > 1 else "/opt/autosparefinder/jaguar_parts_raw.ndjson"
    parts = []
    with open(src) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                parts.append(json.loads(line))
            except Exception:
                pass
    print(f"Loaded {len(parts):,} Jaguar parts from {src}")

    conn = await asyncpg.connect(DB)
    t0 = time.monotonic()
    updated = inserted = skipped = 0
    touched_part_ids = []

    for p in parts:
        oem = str(p.get("base_part_number", "") or "").strip()
        part_num = str(p.get("part_number", "") or "").strip()
        title = str(p.get("title", "") or oem).strip()
        price_gbp = float(p.get("price_gbp", 0) or 0)
        part_origin = str(p.get("part_origin", "aftermarket")).strip()
        applications = p.get("applications", [])
        brand_name = str(p.get("brand_name", "") or "").strip()

        if not oem or price_gbp <= 0:
            skipped += 1
            continue

        cost    = round(price_gbp * GBP_ILS, 2)
        retail  = round(cost * (1 + VAT), 2)
        from variant_identity import catalog_part_type
        part_type = catalog_part_type("original" if part_origin == "original" else "oe_equivalent")

        part_spec = json.dumps({
            "source": "sng_barratt", "brand_name": brand_name,
            "price_gbp": price_gbp, "gbp_ils_rate": GBP_ILS,
            "vat_rate": VAT, "vat_included": False
        }, ensure_ascii=False)

        res = await conn.fetch("""
            UPDATE parts_catalog SET
                importer_price_ils = $1,
                max_price_ils      = $2,
                specifications     = COALESCE(specifications,'{}')::jsonb || $3::jsonb,
                updated_at         = NOW()
            WHERE oem_number = $4 AND manufacturer = 'Jaguar' AND is_active = true
            RETURNING id::text
        """, cost, retail, part_spec, oem)
        n = len(res)
        if n > 0:
            updated += n
            touched_part_ids.extend(r_[0] for r_ in res)
        else:
            # Try with P_ prefix stripped
            res2 = await conn.fetch("""
                UPDATE parts_catalog SET
                    importer_price_ils=$1, max_price_ils=$2,
                    specifications=COALESCE(specifications,'{}')::jsonb||$3::jsonb, updated_at=NOW()
                WHERE oem_number=$4 AND manufacturer='Jaguar' AND is_active=true
                RETURNING id::text
            """, cost, retail, part_spec, part_num)
            n2 = len(res2)
            if n2 > 0:
                updated += n2
                touched_part_ids.extend(r_[0] for r_ in res2)
            else:
                try:
                    sku = f"JAG-{oem[:60]}"
                    compat = json.dumps(applications[:10]) if applications else "[]"
                    new_id = await conn.fetchval("""
                        INSERT INTO parts_catalog(
                            id, sku, oem_number, name, name_he, manufacturer, category,
                            base_price, importer_price_ils, max_price_ils, min_price_ils,
                            part_type, part_condition, is_active,
                            needs_oem_lookup, master_enriched, specifications,
                            compatible_vehicles, created_at, updated_at
                        ) VALUES(
                            gen_random_uuid(), $1, $2, $3, $3, 'Jaguar', $9,
                            0.0, $4, $5, $5,
                            $6, 'new', true,
                            true, false, $7::jsonb,
                            $8::jsonb, NOW(), NOW()
                        )
                        ON CONFLICT (sku) DO UPDATE SET
                            importer_price_ils = CASE WHEN EXCLUDED.importer_price_ils > 0 THEN EXCLUDED.importer_price_ils ELSE parts_catalog.importer_price_ils END,
                            max_price_ils      = EXCLUDED.max_price_ils,
                            specifications     = EXCLUDED.specifications,
                            updated_at         = NOW()
                        RETURNING id::text
                    """, sku, oem, title, cost, retail, part_type, part_spec, compat,
                        categorize_on_ingest(name=title))
                    inserted += 1
                    if new_id:
                        touched_part_ids.append(new_id)
                except Exception:
                    skipped += 1

    await base_price_rule.apply_and_report(conn, touched_part_ids, "jaguar_ndjson_import")
    elapsed = time.monotonic() - t0
    print(f"Done in {elapsed:.0f}s: updated={updated:,} inserted={inserted:,} skipped={skipped:,}")

    r = await conn.fetchrow(
        "SELECT COUNT(*) t, COUNT(*) FILTER (WHERE importer_price_ils>0) p "
        "FROM parts_catalog WHERE manufacturer='Jaguar' AND is_active=true"
    )
    print(f"Jaguar coverage: {r['p']:,}/{r['t']:,} ({100*r['p']//(r['t'] or 1)}%)")
    await conn.close()

asyncio.run(run())
