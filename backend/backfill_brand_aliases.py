#!/usr/bin/env python3
"""
backfill_brand_aliases.py — Backfill brand_aliases from car_brands.aliases[].

For every car_brand whose aliases array is non-empty, inserts:
  • each entry in the aliases[] array
  • the canonical English name (car_brands.name)  — if not already present
  • the Hebrew name (car_brands.name_he)           — if not already present
All inserts: ON CONFLICT (brand_id, alias) DO NOTHING.

Usage:
    python backfill_brand_aliases.py            # run backfill
    python backfill_brand_aliases.py --dry-run  # count candidates only
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import datetime

from dotenv import load_dotenv
from sqlalchemy import text

load_dotenv()

sys.path.insert(0, os.path.dirname(__file__))
from BACKEND_DATABASE_MODELS import async_session_factory

PRINT_EVERY = 50   # print progress line every N brands


async def fetch_brands(db):
    result = await db.execute(
        text("""
            SELECT id, name, name_he, aliases
            FROM car_brands
            WHERE aliases IS NOT NULL
              AND aliases != '{}'
            ORDER BY name
        """)
    )
    return result.fetchall()


def build_alias_set(row) -> list[str]:
    """Return deduplicated, stripped aliases for one brand row."""
    seen: set[str] = set()
    candidates = list(row.aliases or [])
    # add canonical English name and Hebrew name
    if row.name:
        candidates.append(row.name)
    if row.name_he:
        candidates.append(row.name_he)
    result = []
    for raw in candidates:
        a = raw.strip()
        if a and a not in seen:
            seen.add(a)
            result.append(a)
    return result


async def write_audit_row(db, total_inserted: int) -> None:
    vtag = f"brand-aliases-backfill-{uuid.uuid4().hex[:12]}"
    await db.execute(
        text("""
            INSERT INTO catalog_versions
                (id, version_tag, description, parts_added, parts_updated,
                 source, status, created_at)
            VALUES
                (gen_random_uuid(), :vtag, :desc, :added, 0,
                 'brand_aliases_backfill', 'completed', NOW())
            ON CONFLICT (version_tag) DO NOTHING
        """),
        {
            "vtag":  vtag,
            "desc":  f"brand_aliases backfill: {total_inserted} aliases inserted",
            "added": total_inserted,
        },
    )
    await db.commit()


async def run(dry_run: bool = False) -> None:
    t0 = datetime.utcnow()

    async with async_session_factory() as db:
        brands = await fetch_brands(db)

    total_brands = len(brands)
    # Count total candidate aliases across all brands
    total_candidates = sum(len(build_alias_set(r)) for r in brands)

    print(f"[Backfill] {total_brands} brands with non-empty aliases array.")
    print(f"[Backfill] {total_candidates} total alias candidates.")

    if dry_run:
        print("[Backfill] --dry-run: nothing written.")
        return

    if total_brands == 0:
        print("[Backfill] Nothing to do.")
        return

    total_inserted = 0
    brands_processed = 0

    async with async_session_factory() as db:
        for row in brands:
            aliases = build_alias_set(row)
            for alias in aliases:
                res = await db.execute(
                    text("""
                        INSERT INTO brand_aliases (brand_id, alias, normalized, source)
                        VALUES (:brand_id, :alias, :normalized, 'import')
                        ON CONFLICT (brand_id, alias) DO NOTHING
                    """),
                    {
                        "brand_id":   str(row.id),
                        "alias":      alias,
                        "normalized": alias.lower(),
                    },
                )
                total_inserted += res.rowcount

            brands_processed += 1
            if brands_processed % PRINT_EVERY == 0:
                elapsed = (datetime.utcnow() - t0).seconds
                print(
                    f"[Backfill]  {brands_processed}/{total_brands} brands — "
                    f"{total_inserted} aliases inserted  ({elapsed}s)"
                )

        await db.commit()

    # Final audit row
    async with async_session_factory() as db:
        await write_audit_row(db, total_inserted)

    elapsed = (datetime.utcnow() - t0).seconds
    print(
        f"[Backfill] Complete — {brands_processed} brands processed, "
        f"{total_inserted} aliases inserted, {elapsed}s total."
    )


if __name__ == "__main__":
    asyncio.run(run(dry_run="--dry-run" in sys.argv))
