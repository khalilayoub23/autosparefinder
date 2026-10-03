"""
Pre-deployment verification: simulate the FIXED aggregation logic against live DB data
and compare against direct independent DB counts.

This runs the same SQL + same fixed loop as the new routes/parts.py code,
so the output represents what family_counts WILL be after restart.
"""
import asyncio, asyncpg, os

DB = os.environ.get('DATABASE_URL','').replace('postgresql+asyncpg://','postgresql://')

VERIFY_SLUGS = [
    'body-exterior',       # large (>900K)
    'electrical',          # large (~400K)
    'interior-comfort',    # medium (~350K)
    'suspension-steering', # medium (~295K)
    'brakes',              # medium (~170K)
    'cooling',             # smaller (~142K)
    'engine',              # was broken (0 via classifier bug) — must be ~322K after fix
    'filters',             # smaller (~71K)
    'gearbox',             # smaller (~64K)
    'wipers-washers',      # should be 0 or very small
]

async def main():
    from part_type_taxonomy import (
        PART_TYPE_FAMILY_BY_ID, classify_part_type_family, iter_part_type_families
    )
    from routes.parts import CANONICAL_FILTER_CATEGORIES, _normalize_filter_category

    conn = await asyncpg.connect(DB, command_timeout=45)

    # ── Simulate fixed aggregation ───────────────────────────────────────────
    agg_rows = await conn.fetch("""
        SELECT category, part_type, COUNT(*) AS cnt
        FROM parts_catalog
        WHERE is_active = TRUE
        GROUP BY category, part_type
    """)

    family_counts = {f.id: 0 for f in iter_part_type_families()}
    fallback_counts = {c: 0 for c in CANONICAL_FILTER_CATEGORIES}

    for row in agg_rows:
        raw_category, raw_part_type, cnt = row['category'], row['part_type'], row['cnt']
        family = PART_TYPE_FAMILY_BY_ID.get(raw_category) or classify_part_type_family(
            raw_category, raw_part_type, None, None, None
        )
        if family:
            family_counts[family.id] = family_counts.get(family.id, 0) + cnt
        else:
            canonical = _normalize_filter_category(raw_category)
            if canonical:
                fallback_counts[canonical] = fallback_counts.get(canonical, 0) + cnt

    # ── Independent direct DB counts ────────────────────────────────────────
    placeholders = ','.join(f'${i+1}' for i in range(len(VERIFY_SLUGS)))
    db_rows = await conn.fetch(
        f"""SELECT category, COUNT(*) AS cnt
            FROM parts_catalog
            WHERE is_active = TRUE
              AND category = ANY(ARRAY[{placeholders}]::text[])
            GROUP BY category""",
        *VERIFY_SLUGS
    )
    await conn.close()

    db_direct = {r['category']: r['cnt'] for r in db_rows}

    # ── Report ───────────────────────────────────────────────────────────────
    print(f"{'CATEGORY':<28} {'Simulated API':>16} {'DB direct':>12}  MATCH")
    print('-' * 72)
    all_pass = True
    for slug in VERIFY_SLUGS:
        sim = family_counts.get(slug, 0)
        db  = db_direct.get(slug, 0)
        # Allow up to 200 rows of live write churn between the two queries
        if abs(sim - db) <= 200:
            match = 'PASS'
        else:
            match = f'FAIL (delta {sim - db:+,})'
            all_pass = False
        print(f'{slug:<28} {sim:>16,} {db:>12,}  {match}')

    print()
    # Confirm engine is no longer 0
    if family_counts.get('engine', 0) == 0:
        print('FAIL — engine family count is still 0 after simulated fix')
        all_pass = False
    if family_counts.get('fluids', 0) > 400_000:
        print(f"FAIL — fluids count suspiciously large ({family_counts['fluids']:,}), "
              "suggests engine parts are still leaking into fluids")
        all_pass = False

    print()
    if all_pass:
        print('STATUS: PRE-DEPLOYMENT PASS')
    else:
        print('STATUS: FAIL — see rows above')

asyncio.run(main())
