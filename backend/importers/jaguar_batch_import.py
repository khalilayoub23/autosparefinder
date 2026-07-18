#!/usr/bin/env python3
"""
Fast batch Jaguar NDJSON import using executemany (streaming, low memory).
Processes 250K SNG Barratt parts via temp table bulk UPDATE then batch INSERT.

Usage:
  python3 jaguar_batch_import.py [/path/to/jaguar_parts_raw.ndjson]
"""
import asyncio, json, os, sys, time
import asyncpg

DB = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")
GBP_ILS = 4.8
VAT = 0.18
BATCH = 500
SRC = sys.argv[1] if len(sys.argv) > 1 else "/opt/autosparefinder/jaguar_parts_raw.ndjson"


def prepare_part(p: dict):
    oem = str(p.get("base_part_number") or "").strip()
    part_num = str(p.get("part_number") or "").strip()
    title = str(p.get("title") or oem).strip()
    price_gbp = float(p.get("price_gbp") or 0)
    if not oem or price_gbp <= 0:
        return None

    cost    = round(price_gbp * GBP_ILS, 2)
    retail  = round(cost * (1 + VAT), 2)
    selling = round(cost * 1.45, 2)
    sku     = f"JAG-{oem[:58]}"
    spec    = json.dumps({
        "source": "sng_barratt", "price_gbp": price_gbp,
        "gbp_ils_rate": GBP_ILS, "vat_rate": VAT
    })
    return (oem, part_num, title, selling, cost, retail, sku, spec)


async def run():
    print(f"Loading {SRC}")
    conn = await asyncpg.connect(DB)
    t0 = time.monotonic()

    # Step 1: Create temp table and load all OEM→price pairs
    await conn.execute("""
        CREATE TEMP TABLE _jag_prices (
            oem TEXT, part_num TEXT, title TEXT,
            selling NUMERIC, cost NUMERIC, retail NUMERIC,
            sku TEXT, spec TEXT
        )
    """)

    total_in = valid = 0
    batch = []
    with open(SRC) as f:
        for line in f:
            total_in += 1
            line = line.strip()
            if not line:
                continue
            try:
                p = json.loads(line)
            except Exception:
                continue
            rec = prepare_part(p)
            if rec:
                batch.append(rec)
                valid += 1
            if len(batch) >= BATCH:
                await conn.executemany(
                    "INSERT INTO _jag_prices VALUES ($1,$2,$3,$4,$5,$6,$7,$8)", batch
                )
                batch = []
    if batch:
        await conn.executemany(
            "INSERT INTO _jag_prices VALUES ($1,$2,$3,$4,$5,$6,$7,$8)", batch
        )

    print(f"Loaded {total_in:,} NDJSON lines, {valid:,} valid into temp table ({time.monotonic()-t0:.1f}s)")

    # Step 2: Bulk UPDATE existing by oem_number (base_part_number match)
    r1 = await conn.execute("""
        UPDATE parts_catalog pc SET
            importer_price_ils = CASE WHEN pc.importer_price_ils IS NULL OR pc.importer_price_ils=0 THEN j.cost ELSE pc.importer_price_ils END,
            max_price_ils      = CASE WHEN pc.max_price_ils IS NULL OR pc.max_price_ils=0 THEN j.retail ELSE pc.max_price_ils END,
            base_price         = CASE WHEN pc.base_price IS NULL OR pc.base_price=0 THEN j.selling ELSE pc.base_price END,
            specifications     = COALESCE(pc.specifications,'{}')::jsonb || j.spec::jsonb,
            updated_at         = NOW()
        FROM _jag_prices j
        WHERE pc.oem_number = j.oem AND pc.manufacturer = 'Jaguar' AND pc.is_active = true
    """)
    upd1 = int(r1.split()[-1])
    print(f"Updated by oem_number: {upd1:,}")

    # Step 3: Bulk UPDATE by part_number (P_ prefixed full OEM)
    r2 = await conn.execute("""
        UPDATE parts_catalog pc SET
            importer_price_ils = CASE WHEN pc.importer_price_ils IS NULL OR pc.importer_price_ils=0 THEN j.cost ELSE pc.importer_price_ils END,
            max_price_ils      = CASE WHEN pc.max_price_ils IS NULL OR pc.max_price_ils=0 THEN j.retail ELSE pc.max_price_ils END,
            base_price         = CASE WHEN pc.base_price IS NULL OR pc.base_price=0 THEN j.selling ELSE pc.base_price END,
            specifications     = COALESCE(pc.specifications,'{}')::jsonb || j.spec::jsonb,
            updated_at         = NOW()
        FROM _jag_prices j
        WHERE pc.oem_number = j.part_num AND pc.manufacturer = 'Jaguar' AND pc.is_active = true
          AND (pc.importer_price_ils IS NULL OR pc.importer_price_ils = 0)
    """)
    upd2 = int(r2.split()[-1])
    print(f"Updated by part_number: {upd2:,}")

    # Step 4: Batch INSERT unmatched parts (those not in DB by sku)
    ins_q = """
        INSERT INTO parts_catalog(
            id, sku, oem_number, name, name_he, manufacturer, category,
            base_price, importer_price_ils, max_price_ils, min_price_ils,
            part_type, part_condition, is_active, needs_oem_lookup, master_enriched,
            specifications, created_at, updated_at
        )
        SELECT gen_random_uuid(), j.sku, j.oem, j.title, j.title, 'Jaguar', 'accessories',
               j.selling, j.cost, j.retail, j.cost,
               'OE_Equivalent', 'new', true, true, false,
               j.spec::jsonb, NOW(), NOW()
        FROM _jag_prices j
        WHERE NOT EXISTS (SELECT 1 FROM parts_catalog WHERE sku = j.sku)
        ON CONFLICT (sku) DO NOTHING
    """
    r3 = await conn.execute(ins_q)
    ins = int(r3.split()[-1])
    print(f"Inserted new: {ins:,}")

    await conn.execute("DROP TABLE _jag_prices")

    r = await conn.fetchrow(
        "SELECT COUNT(*) t, COUNT(*) FILTER (WHERE importer_price_ils>0) p "
        "FROM parts_catalog WHERE manufacturer='Jaguar' AND is_active=true"
    )
    elapsed = time.monotonic() - t0
    print(f"\nDone in {elapsed:.0f}s | Jaguar coverage: {r['p']:,}/{r['t']:,} ({100*r['p']//(r['t'] or 1)}%)")
    await conn.close()


asyncio.run(run())
