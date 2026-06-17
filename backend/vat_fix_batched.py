#!/usr/bin/env python3
"""
Batched VAT fix: correct importer_price_ils + base_price for parts with vat_rate=0.17.
Processes in chunks of 5000 rows to avoid 44-minute table scans and deadlocks.
"""
import asyncio, asyncpg, os, time

DB = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")
BATCH = 50000


async def main():
    conn = await asyncpg.connect(DB)
    t0 = time.monotonic()
    total_fixed = 0
    batch_num = 0
    errors = 0

    print(f"[vat_fix_batched] Starting batched VAT fix (batch={BATCH})", flush=True)

    while True:
        batch_num += 1
        for attempt in range(5):
            try:
                r = await conn.execute("""
                    WITH to_fix AS (
                        SELECT id FROM parts_catalog
                        WHERE specifications->>'vat_rate' = '0.17'
                          AND max_price_ils > 0
                          AND importer_price_ils > 0
                          AND is_active
                        LIMIT $1
                    )
                    UPDATE parts_catalog SET
                        importer_price_ils = round((max_price_ils / 1.18)::numeric, 2),
                        base_price         = round((max_price_ils / 1.18 * 1.45)::numeric, 2),
                        specifications     = specifications || '{"vat_rate": 0.18}'::jsonb,
                        updated_at         = NOW()
                    WHERE id IN (SELECT id FROM to_fix)
                """, BATCH)
                n = int(r.split()[-1])
                total_fixed += n
                elapsed = time.monotonic() - t0
                print(f"  batch {batch_num}: fixed {n:,} rows | total={total_fixed:,} [{elapsed:.0f}s]", flush=True)
                break
            except asyncpg.DeadlockDetectedError:
                wait = 10 * (attempt + 1)
                print(f"  batch {batch_num} deadlock (attempt {attempt+1}), sleep {wait}s", flush=True)
                await asyncio.sleep(wait)
                errors += 1
            except Exception as e:
                print(f"  batch {batch_num} error: {e}", flush=True)
                errors += 1
                await asyncio.sleep(5)
                break
        else:
            print(f"  batch {batch_num} failed after 5 attempts, stopping", flush=True)
            break

        if n < BATCH:
            print(f"[vat_fix_batched] Done — last batch had {n} rows (< {BATCH}), no more rows", flush=True)
            break

        await asyncio.sleep(0.2)

    elapsed = time.monotonic() - t0
    # Verify remaining
    remaining = await conn.fetchval("""
        SELECT COUNT(*) FROM parts_catalog
        WHERE specifications->>'vat_rate' = '0.17'
          AND max_price_ils > 0 AND importer_price_ils > 0 AND is_active
    """)
    print(f"\n[vat_fix_batched] COMPLETE: fixed={total_fixed:,} errors={errors} remaining={remaining:,} ({elapsed:.0f}s)", flush=True)
    await conn.close()


asyncio.run(main())
