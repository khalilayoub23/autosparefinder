#!/usr/bin/env python3
"""Fix part_condition New→new in 50K batches with deadlock retry."""
import asyncio, asyncpg, os, time

DB = os.environ.get("DATABASE_URL","").replace("postgresql+asyncpg://","postgresql://")

async def main():
    conn = await asyncpg.connect(DB)
    total = await conn.fetchval("SELECT COUNT(*) FROM parts_catalog WHERE part_condition='New'")
    print(f"[cond_fix] {total:,} rows to fix", flush=True)
    fixed = 0
    batch = 0
    while True:
        for attempt in range(5):
            try:
                r = await conn.execute("""
                    WITH b AS (SELECT id FROM parts_catalog WHERE part_condition='New' LIMIT 50000)
                    UPDATE parts_catalog SET part_condition='new', updated_at=NOW()
                    FROM b WHERE parts_catalog.id=b.id
                """)
                n = int(r.split()[-1])
                break
            except asyncpg.DeadlockDetectedError:
                wait = 10 * (attempt + 1)
                print(f"  deadlock attempt {attempt+1}, sleep {wait}s", flush=True)
                await asyncio.sleep(wait)
        else:
            print("[cond_fix] failed after 5 attempts, stopping", flush=True)
            break
        if n == 0:
            break
        fixed += n
        batch += 1
        if batch % 5 == 0 or batch <= 3:
            print(f"  batch {batch}: {n:,} fixed (total={fixed:,})", flush=True)
        await asyncio.sleep(0.3)
    rem = await conn.fetchval("SELECT COUNT(*) FROM parts_catalog WHERE part_condition='New'")
    print(f"[cond_fix] DONE: {fixed:,} fixed, {rem:,} remaining", flush=True)
    await conn.close()

asyncio.run(main())
