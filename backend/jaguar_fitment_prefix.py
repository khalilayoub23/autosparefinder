#!/usr/bin/env python3
"""
Jaguar OEM-prefix fitment mapping.
Maps 3-char Jaguar OEM number prefix → Jaguar model name.
Only covers parts with real manufacturer OEM numbers (not Car-Parts.ie internal codes).
"""
import asyncio, os, sys, time, asyncpg

DB_URL = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")

# (3-char prefix, model_name, year_from, year_to)
JAG_PREFIX_MAP = {
    # XF family
    "C2D": [("XF 2006-2016", 2006, 2016)],
    "C2S": [("XF 2016 >",    2016, None)],
    "GX7": [("XF 2016 >",    2016, None)],
    # XJ family
    "C2Z": [("XJ",           2009, None)],
    "C2P": [("XJ Series X350", 2003, 2009)],
    "C2C": [("XJ Series X308", 1997, 2003)],
    # Compact family
    "J9C": [("XE",           2015, None)],
    "T4N": [("E-Pace",       2018, None)],
    # Sport / performance
    "T4A": [("F-Type",       2013, None)],
    "T4K": [("F-Pace",       2017, None)],
    "T4J": [("F-Pace",       2016, None)],
    # SUV family
    "T2H": [("F-Pace",       2016, None)],
    "T2R": [("I-Pace",       2018, None)],
    # Classic/heritage
    "BEC": [("XK8 / XKR - To 2002", 1996, 2002)],
    "XR8": [("XK",           2006, 2014)],
    # S-Type
    "C2Y": [("S-Type",       2002, 2008)],
    "J43": [("S-Type",       1999, 2008)],
    # X-Type
    "C2A": [("X-Type",       2001, 2009)],
    "X40": [("X-Type",       2001, 2009)],
    # Generic / multi-model — skip
    "JLM": None,
    "JDE": None,
    "ASS": None,
}


async def run():
    if not DB_URL:
        print("ERROR: DATABASE_URL not set"); sys.exit(1)
    conn = await asyncpg.connect(DB_URL)
    t0 = time.monotonic()

    mfr = await conn.fetchrow("SELECT id FROM car_brands WHERE LOWER(name)='jaguar' LIMIT 1")
    if not mfr:
        print("ERROR: Jaguar not in car_brands"); return
    mfr_id = str(mfr["id"])

    parts = await conn.fetch("""
        SELECT id, oem_number FROM parts_catalog
        WHERE manufacturer = 'Jaguar'
          AND is_active = true
          AND oem_number IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM part_vehicle_fitment WHERE part_id = parts_catalog.id)
    """)
    print(f"Jaguar parts without fitment (with OEM number): {len(parts):,}")

    inserted = skipped = errors = 0
    for part in parts:
        pid = str(part["id"])
        oem = str(part["oem_number"] or "")[:3].upper()

        if oem not in JAG_PREFIX_MAP:
            skipped += 1
            continue
        entries = JAG_PREFIX_MAP[oem]
        if entries is None:
            skipped += 1
            continue

        for model, year_from, year_to in entries:
            try:
                await conn.execute("""
                    INSERT INTO part_vehicle_fitment (
                        id, part_id, manufacturer, manufacturer_id,
                        model, year_from, year_to, notes, created_at, updated_at
                    ) VALUES (
                        gen_random_uuid(), $1::uuid, 'Jaguar', $2::uuid,
                        $3, $4, $5, 'OEM prefix fitment mapping', NOW(), NOW()
                    ) ON CONFLICT (part_id, manufacturer, model, year_from) DO NOTHING
                """, pid, mfr_id, model, year_from, year_to)
                inserted += 1
            except Exception as e:
                errors += 1
                if errors <= 3: print(f"  error [{oem}]: {e}")

    after = await conn.fetchval("""
        SELECT COUNT(DISTINCT pc.id) FROM parts_catalog pc
        JOIN part_vehicle_fitment pvf ON pvf.part_id=pc.id WHERE pc.manufacturer='Jaguar'
    """)
    print(f"\n=== JAGUAR OEM-PREFIX FITMENT DONE ({time.monotonic()-t0:.1f}s) ===")
    print(f"  Fitment rows inserted: {inserted:,}")
    print(f"  Skipped (unknown prefix): {skipped:,}")
    print(f"  Jaguar parts with fitment now: {after:,}")
    await conn.close()


if __name__ == "__main__":
    asyncio.run(run())
