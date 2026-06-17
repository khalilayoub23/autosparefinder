#!/usr/bin/env python3
"""
Direct Kia IL price importer.
Reads /tmp/Kia_oem.json (format: [{sku, name, price}] where price is excl. 17% VAT ILS)
and updates parts_catalog correctly per pricing policy:
  importer_price_ils = price (excl. VAT)
  max_price_ils      = price * 1.17 (incl. VAT)
  base_price         = price * 1.45 (our 45% margin)
"""
import asyncio, json, os, sys, time
import asyncpg

DB = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")
VAT = 0.18

async def run():
    src = sys.argv[1] if len(sys.argv) > 1 else "/tmp/Kia_oem.json"
    parts = json.load(open(src))
    print(f"Loaded {len(parts):,} Kia parts from {src}")

    conn = await asyncpg.connect(DB)
    t0 = time.monotonic()
    updated = not_found = inserted = 0
    spec = json.dumps({"importer": "Kia Israel (Delek Motors)", "source": "kia-israel.co.il",
                       "vat_rate": VAT, "vat_included": False}, ensure_ascii=False)

    for p in parts:
        sku  = str(p.get("sku", "")).strip()
        name = str(p.get("name", "")).strip()
        price = float(p.get("price", 0) or 0)
        if not sku or price <= 0:
            continue

        cost    = round(price, 2)
        retail  = round(price * (1 + VAT), 2)
        selling = round(price * 1.45, 2)

        # Try to update existing part by OEM number
        res = await conn.execute("""
            UPDATE parts_catalog SET
                importer_price_ils = $1,
                max_price_ils      = $2,
                base_price         = $3,
                specifications     = COALESCE(specifications,'{}')::jsonb || $4::jsonb,
                updated_at         = NOW()
            WHERE oem_number = $5 AND manufacturer = 'Kia' AND is_active = true
        """, cost, retail, selling, spec, sku)
        n = int(res.split()[-1])
        if n > 0:
            updated += n
        else:
            # Try inserting as new part
            try:
                await conn.execute("""
                    INSERT INTO parts_catalog(
                        id, sku, oem_number, name, name_he, manufacturer, category,
                        base_price, importer_price_ils, max_price_ils, min_price_ils,
                        part_type, part_condition, aftermarket_tier, is_active,
                        needs_oem_lookup, master_enriched, specifications,
                        created_at, updated_at
                    ) VALUES(
                        gen_random_uuid(), $1, $1, $2, $2, 'Kia', 'accessories',
                        $3, $4, $5, $5,
                        'Original', 'new', NULL, true,
                        true, false, $6::jsonb,
                        NOW(), NOW()
                    )
                    ON CONFLICT (sku) DO UPDATE SET
                        importer_price_ils = EXCLUDED.importer_price_ils,
                        max_price_ils      = EXCLUDED.max_price_ils,
                        base_price         = EXCLUDED.base_price,
                        specifications     = EXCLUDED.specifications,
                        updated_at         = NOW()
                """, sku, name, selling, cost, retail, spec)
                inserted += 1
            except Exception:
                not_found += 1

    elapsed = time.monotonic() - t0
    print(f"Done in {elapsed:.0f}s: updated={updated:,} inserted={inserted:,} not_found={not_found:,}")

    r = await conn.fetchrow(
        "SELECT COUNT(*) t, COUNT(*) FILTER (WHERE importer_price_ils>0) p "
        "FROM parts_catalog WHERE manufacturer='Kia' AND is_active=true"
    )
    print(f"Kia coverage: {r['p']:,}/{r['t']:,} ({100*r['p']//(r['t'] or 1)}%)")
    await conn.close()

asyncio.run(run())
