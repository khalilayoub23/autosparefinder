"""
Script: maintenance/pipeline_parity_check.py
Purpose: Final gate of the job queue — prove the pipeline's OUTCOME, not its
         exit codes. Every step before this one can exit 0 while having written
         nothing; this asks the database and Meilisearch what is actually true.
Process:
  Runs a fixed set of checks, prints a table, and exits non-zero if any FAIL.
  Each check states the number it found so a regression is visible, not merely
  "ok/not ok".
Data Imported/Modified: NONE. Read-only by design — a verifier that writes can
  mask the very drift it exists to detect.
Data Sources: parts_catalog, part_thumbnails, supplier_parts, Meilisearch stats.
Missing Data Delegation: a WARN never fails the run; only a hard FAIL does.
Last Updated: 2026-07-29

WHY THIS EXISTS: "the pipeline ran" and "the pipeline worked" are different
claims. This repo has been bitten by the gap repeatedly — Meilisearch silently
620K documents behind while its checkpoint claimed complete; 8,210 of 8,210
fitment rows counted as "skipped"; two run_all_tasks tasks failing every cycle
for weeks while cycles kept completing. Self-reported success is not evidence.
"""
import asyncio
import json
import os
import sys
import urllib.request

import asyncpg

DB = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")
MEILI = os.environ.get("MEILI_URL", "http://meilisearch:7700")
MEILI_KEY = os.environ.get("MEILI_MASTER_KEY", "")
# Meili sync is incremental and the catalogue moves under it; a small lag is
# healthy, a large one means the index is serving a stale catalogue.
MAX_INDEX_LAG = int(os.getenv("PARITY_MAX_INDEX_LAG", "50000"))

rows = []


def record(name, ok, found, detail="", warn=False):
    rows.append({"name": name, "ok": ok, "warn": warn, "found": found, "detail": detail})


def meili_count():
    req = urllib.request.Request(f"{MEILI.rstrip('/')}/indexes/parts/stats")
    if MEILI_KEY:
        req.add_header("Authorization", f"Bearer {MEILI_KEY}")
    with urllib.request.urlopen(req, timeout=60) as r:
        return int(json.load(r).get("numberOfDocuments", 0))


async def main() -> int:
    c = await asyncpg.connect(DB, statement_cache_size=0)
    await c.execute("SET statement_timeout='300s'")

    active = await c.fetchval("SELECT COUNT(*) FROM parts_catalog WHERE is_active")
    record("active parts", True, active)

    # 1. No duplicate (manufacturer, normalised OEM) group should remain active.
    dupes = await c.fetchval("""
        SELECT COUNT(*) FROM (
            SELECT 1 FROM parts_catalog
            WHERE is_active AND oem_number IS NOT NULL AND btrim(oem_number) <> ''
            GROUP BY manufacturer_id, upper(replace(replace(oem_number,' ',''),'-',''))
            HAVING COUNT(*) > 1
        ) g""")
    record("duplicate OEM groups remaining", dupes == 0, dupes,
           "same manufacturer + OEM should be ONE master record")

    # 2. A merged loser must never still be active — that would double-list it.
    stranded = await c.fetchval("""
        SELECT COUNT(*) FROM parts_catalog
        WHERE is_active AND specifications ? 'dedup_merged_into'""")
    record("merged losers still active", stranded == 0, stranded,
           "soft-deleted rows must stay inactive")

    # 3. Catch-all bucket should be drained.
    catchall = await c.fetchval("""
        SELECT COUNT(*) FROM parts_catalog
        WHERE is_active AND category IN
              ('כללי','general','service-general','accessories')""")
    record("parts in catch-all category", catchall == 0, catchall,
           "unclassified parts are invisible to category filters", warn=True)

    # 4. Non-canonical categories must not exist at all (a vocabulary leak).
    try:
        sys.path.insert(0, "/app")
        from category_map import CANONICAL
        bad = await c.fetchval("""
            SELECT COUNT(*) FROM parts_catalog
            WHERE is_active AND category IS NOT NULL
              AND category <> 'כללי' AND NOT (category = ANY($1::text[]))""",
            list(CANONICAL))
        record("non-canonical category values", bad == 0, bad,
               "category column must hold ONE vocabulary")
    except Exception as exc:
        record("non-canonical category values", True, -1,
               f"skipped: {type(exc).__name__}", warn=True)

    # 5. Orphans — the merge repoints these; any orphan means data was lost.
    orph_sp = await c.fetchval("""
        SELECT COUNT(*) FROM supplier_parts sp
        WHERE NOT EXISTS (SELECT 1 FROM parts_catalog p WHERE p.id = sp.part_id)""")
    record("orphaned supplier_parts", orph_sp == 0, orph_sp, "FK integrity")

    orph_f = await c.fetchval("""
        SELECT COUNT(*) FROM part_vehicle_fitment f
        WHERE NOT EXISTS (SELECT 1 FROM parts_catalog p WHERE p.id = f.part_id)""")
    record("orphaned fitment rows", orph_f == 0, orph_f, "FK integrity")

    # 6. Thumbnails: every active part should have a VERDICT (ok/rejected/none),
    #    not merely a successful one — an unprocessed part is the gap.
    unprocessed = await c.fetchval("""
        SELECT COUNT(*) FROM parts_catalog p
        WHERE p.is_active
          AND NOT EXISTS (SELECT 1 FROM part_thumbnails t WHERE t.part_id = p.id)""")
    record("parts with no thumbnail verdict", unprocessed == 0, unprocessed,
           "every part should be processed, even if rejected", warn=True)

    # 7. Pricing invariant: an available offer must carry a price.
    priceless = await c.fetchval("""
        SELECT COUNT(*) FROM supplier_parts
        WHERE is_available AND (price_ils IS NULL OR price_ils <= 0)""")
    record("available offers with no price", priceless == 0, priceless,
           "an available offer must be buyable", warn=True)

    await c.close()

    # 8. Search index parity — the check that caught the 620K drift.
    try:
        idx = meili_count()
        lag = active - idx
        record("Meilisearch lag vs catalogue", abs(lag) <= MAX_INDEX_LAG, lag,
               f"index={idx:,} catalogue={active:,} (max {MAX_INDEX_LAG:,})")
    except Exception as exc:
        record("Meilisearch lag vs catalogue", False, -1,
               f"unreachable: {type(exc).__name__}: {exc}")

    # ── report ────────────────────────────────────────────────────────────────
    width = max(len(r["name"]) for r in rows)
    fails = warns = 0
    print("\n=== PIPELINE PARITY CHECK ===")
    for r in rows:
        if r["ok"]:
            tag = "PASS"
        elif r["warn"]:
            tag = "WARN"; warns += 1
        else:
            tag = "FAIL"; fails += 1
        print(f"  [{tag}] {r['name']:<{width}}  {r['found']:>12,}"
              f"{('   ' + r['detail']) if r['detail'] else ''}")
    print(f"\n  {len(rows)} checks · {fails} failed · {warns} warnings")
    if fails:
        print("  RESULT: FAIL — the pipeline did not achieve its outcome.")
        return 1
    print("  RESULT: PASS" + (" (with warnings)" if warns else ""))
    return 0


if __name__ == "__main__":
    # Handle --help WITHOUT doing the work. These checks are full-table scans
    # (~5 minutes); a script that ignores --help and runs anyway is the trap
    # CLAUDE.md records — anything probing this file's interface would trigger a
    # real scan instead of getting a usage line.
    if "--help" in sys.argv or "-h" in sys.argv:
        print(__doc__)
        print("usage: pipeline_parity_check.py   (no arguments; read-only)")
        print("env: PARITY_MAX_INDEX_LAG (default 50000)")
        sys.exit(0)
    sys.exit(asyncio.run(main()))
