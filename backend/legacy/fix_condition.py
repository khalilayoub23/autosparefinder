#!/usr/bin/env python3
"""Fix part_condition New→new in 10K batches with aggressive deadlock retry."""
import asyncio, asyncpg, os, time

DB = os.environ.get("DATABASE_URL","").replace("postgresql+asyncpg://","postgresql://")
BATCH = 10000  # smaller batches = fewer row locks = fewer deadlocks

async def main():
    conn = await asyncpg.connect(DB)
    total = await conn.fetchval("SELECT COUNT(*) FROM parts_catalog WHERE part_condition='New'")
    print(f"[cond_fix] {total:,} rows to fix (10K batches)", flush=True)
    fixed = 0
    batch = 0
    while True:
        for attempt in range(8):
            try:
                r = await conn.execute("""
                    WITH b AS (SELECT id FROM parts_catalog WHERE part_condition='New' LIMIT $1)
                    UPDATE parts_catalog SET part_condition='new', updated_at=NOW()
                    FROM b WHERE parts_catalog.id=b.id
                """, BATCH)
                n = int(r.split()[-1])
                break
            except asyncpg.DeadlockDetectedError:
                wait = 15 * (attempt + 1)
                print(f"  deadlock attempt {attempt+1}, sleep {wait}s", flush=True)
                await asyncio.sleep(wait)
        else:
            print("[cond_fix] too many deadlocks, sleeping 120s then continuing", flush=True)
            await asyncio.sleep(120)
            continue
        if n == 0:
            break
        fixed += n
        batch += 1
        if batch % 10 == 0:
            print(f"  batch {batch}: {n:,} fixed (total={fixed:,})", flush=True)
        await asyncio.sleep(1.0)  # polite gap to reduce lock contention
    rem = await conn.fetchval("SELECT COUNT(*) FROM parts_catalog WHERE part_condition='New'")
    print(f"[cond_fix] DONE: {fixed:,} fixed, {rem:,} remaining", flush=True)
    await conn.close()

asyncio.run(main())
