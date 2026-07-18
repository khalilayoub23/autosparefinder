#!/usr/bin/env python3
"""
Fix base_price for parts where base_price != round(importer_price_ils * 1.45, 2).
Policy: UNIFORM 45% margin. base_price = cost * 1.45. No exceptions.
"""
import asyncio, asyncpg, os, time

DB = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")
BATCH = 10000


async def main():
    conn = await asyncpg.connect(DB)
    t0 = time.monotonic()
    total_fixed = 0
    batch_num = 0

    print(f"[margin_fix] Starting batched margin fix (batch={BATCH})", flush=True)

    remaining = await conn.fetchval("""
        SELECT COUNT(*) FROM parts_catalog
        WHERE is_active = true AND importer_price_ils > 0 AND base_price > 0
          AND ABS(base_price - ROUND(importer_price_ils * 1.45, 2)) > 0.5
    """)
    print(f"[margin_fix] Total to fix: {remaining:,}", flush=True)

    while True:
        batch_num += 1
        for attempt in range(5):
            try:
                r = await conn.execute("""
                    WITH to_fix AS (
                        SELECT id FROM parts_catalog
                        WHERE is_active = true AND importer_price_ils > 0 AND base_price > 0
                          AND ABS(base_price - ROUND(importer_price_ils * 1.45, 2)) > 0.5
                        LIMIT $1
                    )
                    UPDATE parts_catalog SET
                        base_price = ROUND(importer_price_ils * 1.45, 2),
                        updated_at = NOW()
                    WHERE id IN (SELECT id FROM to_fix)
                """, BATCH)
                n = int(r.split()[-1])
                total_fixed += n
                elapsed = time.monotonic() - t0
                print(f"  batch {batch_num}: fixed {n:,} | total={total_fixed:,} [{elapsed:.0f}s]", flush=True)
                break
            except asyncpg.DeadlockDetectedError:
                wait = 15 * (attempt + 1)
                print(f"  batch {batch_num} deadlock (attempt {attempt+1}), retry in {wait}s", flush=True)
                await asyncio.sleep(wait)
            except Exception as e:
                print(f"  batch {batch_num} error: {e}", flush=True)
                await asyncio.sleep(5)
                break
        else:
            print(f"[margin_fix] batch {batch_num} failed after 5 attempts, stopping", flush=True)
            break

        if n < BATCH:
            break

        await asyncio.sleep(0.3)

    remaining = await conn.fetchval("""
        SELECT COUNT(*) FROM parts_catalog
        WHERE is_active = true AND importer_price_ils > 0 AND base_price > 0
          AND ABS(base_price - ROUND(importer_price_ils * 1.45, 2)) > 0.5
    """)
    elapsed = time.monotonic() - t0
    print(f"[margin_fix] DONE: fixed={total_fixed:,} remaining={remaining:,} ({elapsed:.0f}s)", flush=True)
    await conn.close()


asyncio.run(main())
