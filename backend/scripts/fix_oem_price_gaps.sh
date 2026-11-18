#!/bin/bash
# Fix price gaps caused by duplicate OEM numbers (e.g., '51759-2B300' vs '517592B300').
# This does NOT delete duplicates — it only copies the COST (importer/max price) from the priced entry to the unpriced one; base_price is set by base_price_rule.
# Safe to run multiple times.
#
# Usage: docker exec autospare_backend bash /app/scripts/fix_oem_price_gaps.sh [brand]
#        brand defaults to all brands

BRAND="${1:-}"

docker exec autospare_backend python3 -c "
import asyncio, asyncpg, os
import base_price_rule

DB = os.environ.get('DATABASE_URL','').replace('postgresql+asyncpg://','postgresql://')
BRAND = '${BRAND}'

async def main():
    conn = await asyncpg.connect(DB)

    brand_filter = 'AND LOWER(manufacturer)=LOWER(\$1)' if BRAND else ''

    # Find pairs: same normalized OEM, one priced, one not
    # Normalized = strip dashes, spaces, dots
    sql = '''
        WITH normalized AS (
            SELECT id,
                   REPLACE(REPLACE(REPLACE(UPPER(oem_number), '-', ''), ' ', ''), '.', '') AS norm_oem,
                   LOWER(manufacturer) AS mfr,
                   importer_price_ils, max_price_ils
            FROM parts_catalog
            WHERE is_active AND oem_number IS NOT NULL
              AND LENGTH(oem_number) >= 5
    ''' + (f\"AND LOWER(manufacturer)=LOWER('{BRAND}')\" if BRAND else '') + '''
        ),
        pairs AS (
            SELECT a.id AS unpriced_id,
                   b.importer_price_ils AS src_cost,
                   b.max_price_ils AS src_max
            FROM normalized a
            JOIN normalized b ON a.norm_oem = b.norm_oem AND a.mfr = b.mfr AND a.id != b.id
            WHERE a.importer_price_ils = 0 AND b.importer_price_ils > 0
        )
        SELECT COUNT(*) FROM pairs
    '''

    cnt = await conn.fetchval(sql)
    print(f'Found {cnt:,} unpriced parts that have a priced counterpart with same normalized OEM')

    if cnt == 0:
        await conn.close()
        return

    # Apply prices
    rows = await conn.fetch('''
        WITH normalized AS (
            SELECT id,
                   REPLACE(REPLACE(REPLACE(UPPER(oem_number), '-', ''), ' ', ''), '.', '') AS norm_oem,
                   LOWER(manufacturer) AS mfr,
                   importer_price_ils, max_price_ils
            FROM parts_catalog
            WHERE is_active AND oem_number IS NOT NULL AND LENGTH(oem_number) >= 5
    ''' + (f\"AND LOWER(manufacturer)=LOWER('{BRAND}')\" if BRAND else '') + '''
        ),
        pairs AS (
            SELECT DISTINCT ON (a.id)
                   a.id AS unpriced_id,
                   b.importer_price_ils AS src_cost,
                   b.max_price_ils AS src_max
            FROM normalized a
            JOIN normalized b ON a.norm_oem = b.norm_oem AND a.mfr = b.mfr AND a.id != b.id
            WHERE a.importer_price_ils = 0 AND b.importer_price_ils > 0
            ORDER BY a.id, b.importer_price_ils DESC
        )
        UPDATE parts_catalog SET
            importer_price_ils = pairs.src_cost,
            max_price_ils      = CASE WHEN pairs.src_max > 0 THEN pairs.src_max ELSE parts_catalog.max_price_ils END,
            updated_at         = NOW()
        FROM pairs
        WHERE parts_catalog.id = pairs.unpriced_id
        RETURNING parts_catalog.id::text
    ''')
    print(f'Updated: {len(rows)} parts with prices from their priced counterparts')
    # base_price is the ONE rule's (a counterpart's base_price is never copied)
    await base_price_rule.apply_and_report(conn, [r[0] for r in rows], 'fix_oem_price_gaps')

    await conn.close()

asyncio.run(main())
"
