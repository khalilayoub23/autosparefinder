#!/usr/bin/env python3
"""
Import Jaguar parts from jaguar_parts_raw.ndjson (SNG Barratt sourced, GBP prices).
Converts GBP → ILS at hardcoded exchange rate for pricing.
Format: {part_number, base_part_number, title, manufacturer, price_gbp, applications, part_origin}
Pricing policy: importer_price_ils=price_gbp*GBP_ILS, base_price=cost*1.45, max_price_ils=cost*1.17
"""
import asyncio, json, os, sys, time
import asyncpg

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
        selling = round(cost * 1.45, 2)
        part_type = "Original" if part_origin == "original" else "OE_Equivalent"

        part_spec = json.dumps({
            "source": "sng_barratt", "brand_name": brand_name,
            "price_gbp": price_gbp, "gbp_ils_rate": GBP_ILS,
            "vat_rate": VAT, "vat_included": False
        }, ensure_ascii=False)

        res = await conn.execute("""
            UPDATE parts_catalog SET
                importer_price_ils = $1,
                max_price_ils      = $2,
                base_price         = $3,
                specifications     = COALESCE(specifications,'{}')::jsonb || $4::jsonb,
                updated_at         = NOW()
            WHERE oem_number = $5 AND manufacturer = 'Jaguar' AND is_active = true
        """, cost, retail, selling, part_spec, oem)
        n = int(res.split()[-1])
        if n > 0:
            updated += n
        else:
            # Try with P_ prefix stripped
            res2 = await conn.execute("""
                UPDATE parts_catalog SET
                    importer_price_ils=$1, max_price_ils=$2, base_price=$3,
                    specifications=COALESCE(specifications,'{}')::jsonb||$4::jsonb, updated_at=NOW()
                WHERE oem_number=$5 AND manufacturer='Jaguar' AND is_active=true
            """, cost, retail, selling, part_spec, part_num)
            n2 = int(res2.split()[-1])
            if n2 > 0:
                updated += n2
            else:
                try:
                    sku = f"JAG-{oem[:60]}"
                    compat = json.dumps(applications[:10]) if applications else "[]"
                    await conn.execute("""
                        INSERT INTO parts_catalog(
                            id, sku, oem_number, name, name_he, manufacturer, category,
                            base_price, importer_price_ils, max_price_ils, min_price_ils,
                            part_type, part_condition, is_active,
                            needs_oem_lookup, master_enriched, specifications,
                            compatible_vehicles, created_at, updated_at
                        ) VALUES(
                            gen_random_uuid(), $1, $2, $3, $3, 'Jaguar', 'accessories',
                            $4, $5, $6, $6,
                            $7, 'new', true,
                            true, false, $8::jsonb,
                            $9::jsonb, NOW(), NOW()
                        )
                        ON CONFLICT (sku) DO UPDATE SET
                            importer_price_ils = EXCLUDED.importer_price_ils,
                            max_price_ils      = EXCLUDED.max_price_ils,
                            base_price         = EXCLUDED.base_price,
                            specifications     = EXCLUDED.specifications,
                            updated_at         = NOW()
                    """, sku, oem, title, selling, cost, retail, part_type, part_spec, compat)
                    inserted += 1
                except Exception:
                    skipped += 1

    elapsed = time.monotonic() - t0
    print(f"Done in {elapsed:.0f}s: updated={updated:,} inserted={inserted:,} skipped={skipped:,}")

    r = await conn.fetchrow(
        "SELECT COUNT(*) t, COUNT(*) FILTER (WHERE importer_price_ils>0) p "
        "FROM parts_catalog WHERE manufacturer='Jaguar' AND is_active=true"
    )
    print(f"Jaguar coverage: {r['p']:,}/{r['t']:,} ({100*r['p']//(r['t'] or 1)}%)")
    await conn.close()

asyncio.run(run())
