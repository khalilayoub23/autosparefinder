#!/usr/bin/env python3
import asyncio, asyncpg, os, sys

DB = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")

async def main():
    conn = await asyncpg.connect(DB)
    brands = ['BMW','Nissan','Renault','Chevrolet','Xpeng','Jaecoo',
              'Toyota','Kia','Hyundai','Volvo','Jaguar','Porsche','Lexus']
    for brand in brands:
        r = await conn.fetchrow(
            "SELECT COUNT(*) AS t, "
            "COUNT(CASE WHEN importer_price_ils > 0 THEN 1 END) AS p "
            "FROM parts_catalog WHERE manufacturer = $1 AND is_active = true", brand)
        if r['t'] > 0:
            pct = 100 * (r['p'] or 0) // (r['t'] or 1)
            print(f"{brand:<15} {r['p']:>8,} / {r['t']:>8,}  ({pct}%)")
    total = await conn.fetchrow(
        "SELECT COUNT(*) AS t, "
        "COUNT(CASE WHEN importer_price_ils > 0 THEN 1 END) AS p "
        "FROM parts_catalog WHERE is_active = true")
    pct = 100 * (total['p'] or 0) // (total['t'] or 1)
    print(f"{'TOTAL':<15} {total['p']:>8,} / {total['t']:>8,}  ({pct}%)")
    await conn.close()

asyncio.run(main())
