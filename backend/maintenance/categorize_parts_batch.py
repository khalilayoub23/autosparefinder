#!/usr/bin/env python3
"""
Script:  maintenance/categorize_parts_batch.py
Purpose: Re-categorize every part sitting in a historical fallback bucket, or
         holding a value that is not canonical at all, using the SINGLE source
         of truth (category_map).

Process:
  1. Claim a batch of unlocked rows (FOR UPDATE SKIP LOCKED) so the pass never
     blocks — and is never blocked by — the concurrent harvester / db_update_agent.
  2. Classify with category_map.categorize(), passing name + name_he + the
     car-parts.ie source URL + the existing category label.
  3. Bulk-UPDATE per resulting category; anything unmatched → 'כללי'.

Scope (DERIVED from category_map, so it cannot drift from the rule file):
  • category_map.BAD_FALLBACK_BUCKETS — 'כללי', 'general', 'service-general',
    'accessories', 'tools-equipment'. These were used as importer defaults, so
    they are full of parts that were never really classified.
  • ANY value not in category_map.CANONICAL — e.g. 'Brakes', 'General Parts',
    'Auto Parts', 'Other Parts', 'Engine', written by importers that used to
    carry their own private category maps.

Data Modified: parts_catalog.category, parts_catalog.updated_at
Data Sources:  none (pure re-classification of existing rows)
Last Updated:  2026-07-27 — scope + rules unified onto category_map.

Note: RULES live in category_map.py. Do NOT add a keyword here.
"""
import argparse
import asyncio
import os
import time

import asyncpg

from category_map import BAD_FALLBACK_BUCKETS, CANONICAL, CATCH_ALL, categorize

DB = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")
BATCH = int(os.getenv("CATEGORIZE_BATCH", "5000"))

_BUCKETS_SQL = ", ".join("'" + b.replace("'", "''") + "'" for b in BAD_FALLBACK_BUCKETS)
_CANONICAL_SQL = ", ".join("'" + c.replace("'", "''") + "'" for c in sorted(CANONICAL))

# A row is in scope if it is in a fallback bucket OR its value is not canonical.
SCOPE_SQL = (
    f"(category IN ({_BUCKETS_SQL}) "
    f" OR category IS NULL "
    f" OR TRIM(COALESCE(category, '')) = '' "
    f" OR category NOT IN ({_CANONICAL_SQL}))"
)


async def main() -> None:
    conn = await asyncpg.connect(DB, statement_cache_size=0)
    t0 = time.monotonic()

    # ── Termination guard (CLAUDE.md: bounded batched loops) ──────────────────
    # 'כללי' is BOTH a scope member (we want to retry old catch-all rows against
    # the new rules) AND the destination for anything that still doesn't match.
    # Without a cutoff, a row we just moved to 'כללי' stays in scope and gets
    # re-fetched forever — the loop never drains and burns CPU re-deciding rows
    # it already decided. Every write below sets updated_at = NOW(), so pinning
    # a run-start timestamp and only claiming rows older than it means each row
    # is considered exactly once per run.
    # LOCALTIMESTAMP, not NOW(): `parts_catalog.updated_at` is
    # `timestamp WITHOUT time zone`, and asyncpg refuses to bind a tz-AWARE value
    # against it ("can't subtract offset-naive and offset-aware datetimes").
    # Same trap already recorded in the CLAUDE.md mistake log for
    # normalize_part_types / normalize_categories / dedup.
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-seconds", type=int, default=0,
                    help="soft time budget: stop cleanly (exit 0) after N seconds "
                         "and leave the rest for the next run. 0 = run to completion. "
                         "Needed when a scheduler imposes a HARD timeout — this step "
                         "was killed 3x at 2400s having committed nothing visible, "
                         "because a full pass over the backlog cannot fit in one run.")
    ap.add_argument("--scope", choices=("buckets", "all"), default="buckets",
                    help="buckets = only the fallback/non-canonical buckets (default). "
                         "all = RE-LAND every active part through the current rules.")
    args = ap.parse_args()

    run_start = await conn.fetchval("SELECT LOCALTIMESTAMP")

    # ── Make the claim query INDEX-USABLE ────────────────────────────────────
    # `... OR category NOT IN (23 values)` forces a sequential scan of 4.3M rows
    # on every batch (measured ~50 rows/s). Instead, resolve the open-ended
    # "not canonical" half into a concrete value list ONCE, using a cheap
    # GROUP BY on the indexed `category` column, then claim with a single
    # `category = ANY($3)` that ix_parts_catalog_category can serve.
    distinct_cats = [
        r["category"] for r in await conn.fetch(
            "SELECT category FROM parts_catalog WHERE is_active "
            "AND category IS NOT NULL AND TRIM(category) <> '' GROUP BY category"
        )
    ]
    if args.scope == "all":
        # FULL RE-LAND. The 14 pre-merge importer maps wrote categories that the
        # unified rules disagree with on ~27% of already-categorized parts, and
        # spot-checking those disagreements shows the STORED value is usually the
        # wrong one ('Wiper blade' filed as gearbox, 'Impact Bar' as
        # air-conditioning-heating, a Hebrew door hinge as lighting). task6 in
        # db_cleanup_agent re-lands continuously but a full pass takes ~3 days at
        # 500 rows/cycle, so most of the catalog still carries the old landings.
        # This mode re-decides every active part in one bounded pass.
        in_scope = sorted({c for c in distinct_cats})
    else:
        in_scope = sorted(
            {c for c in distinct_cats if c not in CANONICAL} | set(BAD_FALLBACK_BUCKETS)
        )
    print(f"[catbatch] scope categories ({len(in_scope)}): {in_scope}", flush=True)

    # Plain `category = ANY(...)` — NOT wrapped in COALESCE/TRIM, which would be
    # a function on the column and would make ix_parts_catalog_category unusable.
    # Verified 2026-07-27: zero NULL and zero empty categories in the live table,
    # so no COALESCE is needed. (If NULLs ever appear, add a separate OR branch —
    # do not wrap the column.)
    claim_sql = "category = ANY($3) AND updated_at < $2"
    in_scope_arr = in_scope

    total = await conn.fetchval(
        "SELECT COUNT(*) FROM parts_catalog WHERE is_active "
        "AND category = ANY($2) AND updated_at < $1",
        run_start, in_scope_arr,
    )
    print(f"[catbatch] {total:,} parts in scope (cutoff {run_start:%Y-%m-%d %H:%M:%S})",
          flush=True)

    categorized = 0
    left_as_catch_all = 0
    batch_num = 0
    empty_streak = 0
    max_batches = int(total / BATCH * 1.1) + 200

    while batch_num < max_batches:
        # Soft time budget. A hard kill from the scheduler loses the run's
        # accounting and reports a failure even though earlier batches DID
        # commit; stopping voluntarily leaves a clean, resumable state and an
        # honest exit code. The next invocation picks up where this one stopped.
        if args.max_seconds and (time.monotonic() - t0) >= args.max_seconds:
            print(f"[catbatch] stopping early — soft time budget "
                  f"{args.max_seconds}s reached after {batch_num} batches", flush=True)
            break
        updates: dict[str, list] = {}
        unmatched_ids: list = []
        n = 0
        try:
            async with conn.transaction():
                await conn.execute("SET LOCAL lock_timeout = '4s'")
                rows = await conn.fetch(
                    f"SELECT id, name, name_he, category, "
                    f"       COALESCE(specifications->>'source_url', '') AS src_url, "
                    f"       LEFT(COALESCE(description,'') || ' ' || "
                    f"            COALESCE(specifications::text,''), 800) AS extra "
                    f"FROM parts_catalog "
                    f"WHERE is_active AND {claim_sql} "
                    f"LIMIT $1 FOR UPDATE SKIP LOCKED",
                    BATCH, run_start, in_scope_arr,
                )
                n = len(rows)
                for r in rows:
                    # existing_category lets a mappable LABEL ('Brakes',
                    # 'General Parts') resolve via VARIANT_MAP instead of being
                    # re-guessed from keywords. categorize() deliberately ignores
                    # it when it is one of the fallback buckets.
                    # In 'all' mode do NOT pass existing_category: the whole point
                    # is to re-decide from the part itself, and trusting the stored
                    # label would just confirm the old broken landing.
                    cat = categorize(
                        name=r["name"] or "",
                        name_he=r["name_he"] or "",
                        url=r["src_url"] or "",
                        existing_category=("" if args.scope == "all"
                                           else (r["category"] or "")),
                        # description + specifications, consulted ONLY when the
                        # name yields nothing. 87.3% of stuck parts carry specs
                        # and it rescues 15.4% of them — rows whose "name" is the
                        # vehicle or literally "OEM Part".
                        extra=r["extra"] or "",
                    )
                    if args.scope == "all" and cat == CATCH_ALL and \
                            (r["category"] or "") not in BAD_FALLBACK_BUCKETS:
                        # Never demote a real category to the catch-all just
                        # because the rules have no opinion on this name.
                        continue
                    if cat and cat != CATCH_ALL:
                        updates.setdefault(cat, []).append(r["id"])
                    else:
                        unmatched_ids.append(r["id"])

                for cat, ids in updates.items():
                    await conn.execute(
                        "UPDATE parts_catalog SET category=$1, updated_at=NOW() "
                        "WHERE id=ANY($2::uuid[])",
                        cat, ids,
                    )
                    categorized += len(ids)

                if unmatched_ids:
                    # Unmatched parts land in 'כללי' — the ONE catch-all.
                    # Never 'general'/'service-general'/'accessories'.
                    await conn.execute(
                        "UPDATE parts_catalog SET category=$1, updated_at=NOW() "
                        "WHERE id=ANY($2::uuid[])",
                        CATCH_ALL, unmatched_ids,
                    )
                    left_as_catch_all += len(unmatched_ids)

        except (asyncpg.exceptions.LockNotAvailableError,
                asyncpg.exceptions.QueryCanceledError,
                asyncpg.exceptions.DeadlockDetectedError):
            await asyncio.sleep(3)
            continue

        if n == 0:
            empty_streak += 1
            still = await conn.fetchval(
                "SELECT COUNT(*) FROM parts_catalog WHERE is_active "
                "AND category = ANY($2) AND updated_at < $1",
                run_start, in_scope_arr,
            )
            if still > 500 and empty_streak < 40:
                print(f"  (all remaining {still:,} locked — waiting 15s)", flush=True)
                await asyncio.sleep(15)
                continue
            break
        empty_streak = 0
        batch_num += 1

        if batch_num % 20 == 0 or batch_num <= 5:
            elapsed = time.monotonic() - t0
            rate = (categorized + left_as_catch_all) / max(elapsed, 1)
            print(
                f"  batch {batch_num}: categorized={categorized:,} "
                f"catch_all={left_as_catch_all:,} "
                f"[{elapsed:.0f}s, {rate:.0f} rows/s]",
                flush=True,
            )

    unclaimed = await conn.fetchval(
        "SELECT COUNT(*) FROM parts_catalog WHERE is_active "
        "AND category = ANY($2) AND updated_at < $1",
        run_start, in_scope_arr,
    )
    remaining = await conn.fetchval(
        f"SELECT COUNT(*) FROM parts_catalog WHERE is_active AND {SCOPE_SQL}"
    )
    elapsed = time.monotonic() - t0
    print(
        f"[catbatch] DONE: {categorized:,} categorized, "
        f"{left_as_catch_all:,} → {CATCH_ALL}, {unclaimed:,} unclaimed this run, "
        f"{remaining:,} in scope overall ({elapsed:.0f}s)",
        flush=True,
    )
    await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
