#!/usr/bin/env python3
"""
Script:  maintenance/backfill_warranty_months.py
Purpose: Populate supplier_parts.warranty_months from the warranty data that
         already exists in parts_catalog.specifications, and normalise the
         free-text warranty values into one numeric field.

Why (owner question 2026-07-28: "warranty should be for all parts, why is it not
implemented and wired?"):
    It IS implemented and wired — `supplier_parts.warranty_months` is a real
    column and the search/compare API already returns it (routes/parts.py).
    Measured live: 3,698,242 of 4,158,026 supplier_parts rows (88.9%) carry it.
    Warranty sits on the SUPPLIER OFFER, not the part, which is correct — two
    suppliers can warrant the same part differently.

    What is missing is population, and it is NOT evenly spread:
        459,784 rows have no warranty_months, and 297,491 of them come from ONE
        supplier — "Official Manufacturer Sites" (REX's scraper), which never
        captured the field at all.

WHAT THIS SCRIPT WILL AND WILL NOT DO
    It fills ONLY from data that actually exists:
      • specifications.warranty_months  (numeric already)  → ~36,086 rows
      • specifications.warranty / warranty_text (free text) → parsed to months
    Roughly 423,644 rows have NO warranty information anywhere. Those are LEFT
    NULL. Inventing a plausible default (e.g. the brand's vehicle warranty from
    car_brands.warranty_years) would be fabricated data on a commercial promise
    the customer can hold us to — the platform rule is measure or leave it out.
    Closing that gap needs the REX scraper to start capturing warranty at source.

FREE-TEXT PARSING — driven by the real distinct values found in the catalog:
    'אחריות לשנתיים כולל עבודה'            → 24
    'ל 6 חודשים  או 10000 ק\'מ הקודם מבינהם' →  6
    'חריות לשנה ללא הגבלת ק\'\'מ'            → 12   (note the typo'd חריות)
    '12 months / 100,000 km'               → 12
    '24חודשים' / '12חודשים'                 → 24 / 12   (no space)
    'ל 18 חודשים ללא הגבלת ק\'מ'            → 18
    'תיאור מק"ט'                            → None (not a warranty at all)

Data Modified: supplier_parts.warranty_months, parts_catalog.specifications
Usage:  python3 /app/maintenance/backfill_warranty_months.py [--dry-run]
Last Updated:  2026-07-28
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import time

import asyncpg

DB = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")

_YEARS_HE = {"שנה": 12, "שנתיים": 24, "שלוש שנים": 36, "שלש שנים": 36,
             "ארבע שנים": 48, "חמש שנים": 60}


def parse_months(text: str | None) -> int | None:
    """Free-text warranty → whole months. Returns None when it isn't a warranty.

    Deliberately conservative: anything not clearly a duration yields None
    rather than a guess, because this value is a commercial promise.
    """
    if not text:
        return None
    t = str(text).strip().lower()
    if not t or "תיאור" in t:
        return None

    # '24חודשים' (no space) and 'ל 6 חודשים' and '12 months'
    m = re.search(r"(\d{1,3})\s*(?:חודש|חודשים|months?|mo\b)", t)
    if m:
        n = int(m.group(1))
        return n if 1 <= n <= 120 else None
    m = re.search(r"(\d{1,2})\s*(?:שנים|שנה|years?|yr)\b", t)
    if m:
        n = int(m.group(1)) * 12
        return n if 1 <= n <= 120 else None
    # Hebrew word-numbers: 'אחריות לשנתיים', 'חריות לשנה' (typo present in data)
    for word, months in sorted(_YEARS_HE.items(), key=lambda kv: -len(kv[0])):
        if word in t:
            return months
    return None


async def fill_platform_default(conn, dry_run: bool) -> None:
    """Apply the platform-default warranty where no supplier figure exists.

    Owner directive 2026-07-28: "update the warranty to all parts." We are the
    seller, so where a supplier states nothing the PLATFORM's own warranty
    applies — that is a business policy, not invented supplier data, and it is
    recorded as `warranty_source='platform_default'` so the two can never be
    confused on a customer-facing surface.

    Driven PER SUPPLIER, not by scanning for NULLs. `warranty_months IS NULL`
    has no index, so scanning 4.1M rows to find the ~423k is the documented
    "scan-for-nothing" anti-pattern; `supplier_id` is indexed and the gap is
    concentrated in a handful of suppliers.
    """
    import warranty_policy as wp

    sups = await conn.fetch("""
        SELECT s.id, s.name, COUNT(*) AS n
        FROM supplier_parts sp JOIN suppliers s ON s.id = sp.supplier_id
        WHERE sp.warranty_months IS NULL
        GROUP BY 1, 2 ORDER BY 3 DESC
    """)
    total = sum(r["n"] for r in sups)
    print(f"[default] {total:,} rows with no supplier warranty, "
          f"across {len(sups)} suppliers → {wp.DEFAULT_MONTHS} months "
          f"({wp.SOURCE_PLATFORM})")
    for r in sups[:8]:
        print(f"     {r['name'][:34]:36} {r['n']:>9,}")
    if dry_run:
        print("[default] DRY RUN — nothing written")
        return

    for r in sups:
        done = 0
        while True:
            rows = await conn.fetch(
                """
                WITH b AS (
                    SELECT id FROM supplier_parts
                    WHERE supplier_id = $1 AND warranty_months IS NULL
                    LIMIT 5000
                    FOR UPDATE SKIP LOCKED
                )
                UPDATE supplier_parts sp
                   SET warranty_months = $2, warranty_source = $3, updated_at = NOW()
                  FROM b WHERE sp.id = b.id
                RETURNING 1
                """,
                r["id"], wp.DEFAULT_MONTHS, wp.SOURCE_PLATFORM)
            if not rows:
                break
            done += len(rows)
        print(f"[default] {r['name'][:34]:36} filled {done:,}", flush=True)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--fill-default", action="store_true",
                    help="after the source-derived pass, apply the platform "
                         "default to rows that still have no warranty")
    a = ap.parse_args()

    conn = await asyncpg.connect(DB, statement_cache_size=0)
    await conn.execute("SET statement_timeout = '300s'")
    t0 = time.monotonic()

    rows = await conn.fetch("""
        SELECT sp.id,
               p.specifications->>'warranty_months' AS wm,
               p.specifications->>'warranty'        AS wt,
               p.specifications->>'warranty_text'   AS wt2
        FROM supplier_parts sp
        JOIN parts_catalog p ON p.id = sp.part_id
        WHERE sp.warranty_months IS NULL
          AND (p.specifications ? 'warranty_months'
               OR p.specifications ? 'warranty'
               OR p.specifications ? 'warranty_text')
    """)
    print(f"[warranty] {len(rows):,} supplier rows have a candidate source", flush=True)

    updates, unparsed = [], {}
    for r in rows:
        months = None
        if r["wm"]:
            try:
                months = int(float(r["wm"]))
            except (TypeError, ValueError):
                months = None
        if months is None:
            months = parse_months(r["wt"]) or parse_months(r["wt2"])
        if months and 1 <= months <= 120:
            updates.append((r["id"], months))
        else:
            src = r["wm"] or r["wt"] or r["wt2"]
            if src:
                unparsed[str(src)[:60]] = unparsed.get(str(src)[:60], 0) + 1

    print(f"[warranty] resolvable to months : {len(updates):,}")
    print(f"[warranty] unparseable sources  : {sum(unparsed.values()):,}")
    for v, k in sorted(unparsed.items(), key=lambda kv: -kv[1])[:8]:
        print(f"     {k:7,}  {v!r}")

    if a.dry_run:
        print("\n[warranty] DRY RUN — nothing written")
        if a.fill_default:
            print()
            await fill_platform_default(conn, True)
        await conn.close()
        return

    done = 0
    for i in range(0, len(updates), 2000):
        chunk = updates[i:i + 2000]
        await conn.executemany(
            "UPDATE supplier_parts SET warranty_months=$2, updated_at=NOW() WHERE id=$1",
            chunk)
        done += len(chunk)
        print(f"[warranty] {done:,}/{len(updates):,}", flush=True)

    if a.fill_default:
        print()
        await fill_platform_default(conn, a.dry_run)

    print(f"[warranty] DONE in {time.monotonic()-t0:.0f}s")
    await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
