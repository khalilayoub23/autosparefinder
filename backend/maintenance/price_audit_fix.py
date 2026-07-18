#!/usr/bin/env python3
"""
DEPRECATED — DO NOT RUN. Policy changed 2026-06-12.

Old policy (THIS SCRIPT): base = max × 1.45 always. importer_price_ils = cost.
New policy (db_update_agent.py normalize_base_price):
  1. importer_price_ils > 0 (KGM/SsangYong only): base = max × 1.45
  2. importer=0, online_price_ils > 0 (eBay/intl): base = online × 1.45
  3. importer=0, online=0, max > 0 (IL official ref): base = max (NO markup)

Running this script will INFLATE IL-official-importer prices by 45%.
Use normalize_base_price in db_update_agent.py instead.

Price Audit & Fix — DEPRECATED — see above.

Old (wrong) pricing policy:
  - base_price = max_price_ils × 1.45 (always)
  - max_price_ils = importer_price_ils × 1.18 (domestic IL parts)
  - max_price_ils = online_price_ils (international/eBay parts, no IL VAT)
  - importer_price_ils = supplier cost excl. VAT (domestic)

Fixes applied in order:
  Step 1: Fix max_price_ils for domestic IL parts where VAT was not applied
           (max ≈ importer, ratio ~1.0 — should be ~1.18)
  Step 2: Fix max_price_ils for domestic IL parts where ratio is suspicious (>1.21)
           — normalize to importer × 1.18
  Step 3: Run normalize_base_price logic — set base_price = max_price_ils × 1.45
           for all priceable parts (max, importer, or online)
  Step 4: Report — count remaining pricing gaps

Domestic IL brands (officially operate in Israel with 18% VAT):
  All brands with importer_price_ils > 0 are treated as domestic unless
  they also have online_price_ils set and importer_price_ils is NULL/0.
"""

import sys
sys.exit("DEPRECATED: price_audit_fix.py uses old pricing policy. Use normalize_base_price in db_update_agent.py instead.")

import asyncio
import os
import sys
import time

import asyncpg

MARGIN = 1.45
VAT_IL = 0.18

# Known brands that definitely use international sources (no IL VAT)
# These should NOT have max_price updated to importer × 1.18
INTERNATIONAL_BRANDS = {
    "Rover", "Saab", "Daewoo", "Maserati",  # eBay imports
}


async def run_audit_fix(conn: asyncpg.Connection) -> dict:
    results = {}
    t0 = time.monotonic()

    print("\n" + "=" * 60)
    print("  Price Audit & Fix — 3.2M Parts Catalog")
    print("=" * 60)

    # ─────────────────────────────────────────────────────────────
    # PRE-FIX COUNTS
    # ─────────────────────────────────────────────────────────────
    print("\n[PRE-FIX AUDIT]")

    pre = await conn.fetchrow("""
        SELECT
            COUNT(*) as total_active,
            COUNT(*) FILTER (WHERE base_price > 0) as has_base_price,
            COUNT(*) FILTER (WHERE max_price_ils > 0) as has_max_price,
            COUNT(*) FILTER (WHERE importer_price_ils > 0) as has_importer_price,
            COUNT(*) FILTER (WHERE online_price_ils > 0) as has_online_price,
            COUNT(*) FILTER (
                WHERE importer_price_ils > 1 AND max_price_ils > 0
                  AND max_price_ils / importer_price_ils BETWEEN 0.95 AND 1.05
            ) as issue_a_vat_missing,
            COUNT(*) FILTER (
                WHERE importer_price_ils > 1 AND max_price_ils > 0
                  AND max_price_ils / importer_price_ils > 1.21
            ) as issue_b_over_vat
        FROM parts_catalog
        WHERE is_active = TRUE
    """)

    print(f"  Total active parts:    {pre['total_active']:>10,}")
    print(f"  Has base_price:        {pre['has_base_price']:>10,}")
    print(f"  Has max_price_ils:     {pre['has_max_price']:>10,}")
    print(f"  Has importer_price:    {pre['has_importer_price']:>10,}")
    print(f"  Has online_price:      {pre['has_online_price']:>10,}")
    print(f"  [ISSUE A] VAT missing: {pre['issue_a_vat_missing']:>10,}  (max ≈ importer, ratio ~1.0)")
    print(f"  [ISSUE B] Over-VAT:    {pre['issue_b_over_vat']:>10,}  (ratio > 1.21)")
    results["pre"] = dict(pre)

    # ─────────────────────────────────────────────────────────────
    # STEP 1: Fix Issue A — max_price_ils missing VAT
    # Parts where max_price_ils ≈ importer_price_ils (ratio 0.95-1.05)
    # These are domestic IL parts where VAT was not added to max_price.
    # Fix: max_price_ils = importer_price_ils × 1.18
    # ─────────────────────────────────────────────────────────────
    print("\n[STEP 1] Fixing max_price_ils for domestic parts missing IL VAT...")
    r1 = await conn.execute("""
        UPDATE parts_catalog
        SET
            max_price_ils = ROUND(importer_price_ils * 1.18, 2),
            base_price    = ROUND(importer_price_ils * 1.18 * 1.45, 2),
            updated_at    = NOW()
        WHERE is_active = TRUE
          AND importer_price_ils > 1
          AND max_price_ils > 0
          AND max_price_ils / importer_price_ils BETWEEN 0.95 AND 1.05
          AND manufacturer NOT IN ('Rover', 'Saab', 'Daewoo', 'Maserati')
    """)
    step1_count = int(r1.split()[-1])
    print(f"  Fixed: {step1_count:,} parts (max_price_ils now = importer × 1.18)")
    results["step1_fixed"] = step1_count

    # ─────────────────────────────────────────────────────────────
    # STEP 2: Fix Issue B — max_price_ils over-stated (ratio > 1.21)
    # These parts have max_price_ils much higher than importer × 1.18.
    # Could be: double VAT, data from different source, old import bug.
    # Fix: normalize to importer_price_ils × 1.18 for all domestic brands.
    # ─────────────────────────────────────────────────────────────
    print("\n[STEP 2] Fixing over-stated max_price_ils (ratio > 1.21)...")
    r2 = await conn.execute("""
        UPDATE parts_catalog
        SET
            max_price_ils = ROUND(importer_price_ils * 1.18, 2),
            base_price    = ROUND(importer_price_ils * 1.18 * 1.45, 2),
            updated_at    = NOW()
        WHERE is_active = TRUE
          AND importer_price_ils > 1
          AND max_price_ils > 0
          AND max_price_ils / importer_price_ils > 1.21
          AND max_price_ils / importer_price_ils < 10.0
          AND manufacturer NOT IN ('Rover', 'Saab', 'Daewoo', 'Maserati')
    """)
    step2_count = int(r2.split()[-1])
    print(f"  Fixed: {step2_count:,} parts (max_price_ils normalized to importer × 1.18)")
    results["step2_fixed"] = step2_count

    # ─────────────────────────────────────────────────────────────
    # STEP 3: normalize_base_price — cover all remaining parts
    # 3a. max_price_ils > 0 → base = max × 1.45
    # 3b. Only importer_price_ils > 0 → base = importer × 1.18 × 1.45
    # 3c. Only online_price_ils > 0 → base = online × 1.45
    # ─────────────────────────────────────────────────────────────
    print("\n[STEP 3] Running normalize_base_price for all priceable parts...")

    r3a = await conn.execute("""
        UPDATE parts_catalog
        SET base_price = ROUND(max_price_ils * 1.45, 2),
            updated_at = NOW()
        WHERE is_active = TRUE
          AND max_price_ils > 0
          AND (ABS(base_price - max_price_ils * 1.45) > 0.50
               OR base_price IS NULL OR base_price = 0)
    """)
    r3a_count = int(r3a.split()[-1])
    print(f"  3a (max → base): {r3a_count:,} parts updated")

    r3b = await conn.execute("""
        UPDATE parts_catalog
        SET max_price_ils = ROUND(importer_price_ils * 1.18, 2),
            base_price    = ROUND(importer_price_ils * 1.18 * 1.45, 2),
            updated_at    = NOW()
        WHERE is_active = TRUE
          AND (max_price_ils IS NULL OR max_price_ils = 0)
          AND importer_price_ils > 0
          AND manufacturer NOT IN ('Rover', 'Saab', 'Daewoo', 'Maserati')
    """)
    r3b_count = int(r3b.split()[-1])
    print(f"  3b (importer → max → base): {r3b_count:,} parts updated")

    r3c = await conn.execute("""
        UPDATE parts_catalog
        SET max_price_ils = ROUND(online_price_ils, 2),
            base_price    = ROUND(online_price_ils * 1.45, 2),
            updated_at    = NOW()
        WHERE is_active = TRUE
          AND (max_price_ils IS NULL OR max_price_ils = 0)
          AND (importer_price_ils IS NULL OR importer_price_ils = 0)
          AND online_price_ils > 0
    """)
    r3c_count = int(r3c.split()[-1])
    print(f"  3c (online → max → base): {r3c_count:,} parts updated")

    results.update({"step3a": r3a_count, "step3b": r3b_count, "step3c": r3c_count})

    # ─────────────────────────────────────────────────────────────
    # POST-FIX VERIFICATION
    # ─────────────────────────────────────────────────────────────
    print("\n[POST-FIX VERIFICATION]")

    post = await conn.fetchrow("""
        SELECT
            COUNT(*) as total_active,
            COUNT(*) FILTER (WHERE base_price > 0) as has_base_price,
            COUNT(*) FILTER (WHERE max_price_ils > 0) as has_max_price,
            COUNT(*) FILTER (
                WHERE importer_price_ils > 1 AND max_price_ils > 0
                  AND max_price_ils / importer_price_ils BETWEEN 0.95 AND 1.05
            ) as remaining_a_vat_missing,
            COUNT(*) FILTER (
                WHERE importer_price_ils > 1 AND max_price_ils > 0
                  AND max_price_ils / importer_price_ils > 1.21
                  AND max_price_ils / importer_price_ils < 10.0
            ) as remaining_b_over_vat,
            COUNT(*) FILTER (
                WHERE max_price_ils > 1 AND base_price > 0
                  AND ABS(base_price - max_price_ils * 1.45) > 1.0
            ) as remaining_c_wrong_margin,
            COUNT(*) FILTER (
                WHERE (base_price IS NULL OR base_price = 0)
                  AND (max_price_ils IS NULL OR max_price_ils = 0)
                  AND (importer_price_ils IS NULL OR importer_price_ils = 0)
                  AND (online_price_ils IS NULL OR online_price_ils = 0)
            ) as no_pricing_data
        FROM parts_catalog
        WHERE is_active = TRUE
    """)

    print(f"  Total active parts:         {post['total_active']:>10,}")
    print(f"  Has base_price:             {post['has_base_price']:>10,}  ({100*post['has_base_price']/post['total_active']:.1f}%)")
    print(f"  [A] VAT still missing:      {post['remaining_a_vat_missing']:>10,}  (target: 0)")
    print(f"  [B] Over-VAT remaining:     {post['remaining_b_over_vat']:>10,}  (target: 0)")
    print(f"  [C] Wrong margin remaining: {post['remaining_c_wrong_margin']:>10,}  (target: 0)")
    print(f"  [E] No pricing data at all: {post['no_pricing_data']:>10,}  (needs external enrichment)")
    results["post"] = dict(post)

    # Final ratios for key brands
    print("\n[RATIO VERIFICATION]")
    ratios = await conn.fetch("""
        SELECT manufacturer,
               COUNT(*) as n,
               ROUND(AVG(max_price_ils / NULLIF(importer_price_ils,0))::numeric, 4) as avg_max_importer,
               ROUND(AVG(base_price / NULLIF(max_price_ils,0))::numeric, 4) as avg_base_max
        FROM parts_catalog
        WHERE manufacturer IN ('Lexus','Nissan','Toyota','Kia','SsangYong','Rover','Maserati')
          AND importer_price_ils > 1 AND max_price_ils > 0 AND base_price > 0
          AND is_active = TRUE
        GROUP BY manufacturer
        ORDER BY manufacturer
    """)
    print(f"  {'Brand':<15} {'N':>7} {'max/importer':>13} {'base/max':>10}")
    print(f"  {'-'*50}")
    for r in ratios:
        ok_max = "✓" if r['avg_max_importer'] and abs(float(r['avg_max_importer']) - 1.18) < 0.01 else "!"
        ok_base = "✓" if r['avg_base_max'] and abs(float(r['avg_base_max']) - 1.45) < 0.01 else "!"
        print(f"  {r['manufacturer']:<15} {r['n']:>7,} {ok_max} {float(r['avg_max_importer'] or 0):.4f}       {ok_base} {float(r['avg_base_max'] or 0):.4f}")

    elapsed = time.monotonic() - t0
    print(f"\n[DONE] Elapsed: {elapsed:.1f}s")
    print("=" * 60)
    return results


async def main():
    db_url = os.getenv("DATABASE_URL", "")
    if not db_url:
        print("ERROR: DATABASE_URL not set")
        sys.exit(1)

    pg_url = db_url.replace("postgresql+asyncpg://", "postgresql://").replace("asyncpg://", "postgresql://")
    conn = await asyncpg.connect(pg_url)
    try:
        results = await run_audit_fix(conn)
        # Summarize pass/fail
        post = results.get("post", {})
        issues = (
            post.get("remaining_a_vat_missing", 0) +
            post.get("remaining_b_over_vat", 0) +
            post.get("remaining_c_wrong_margin", 0)
        )
        if issues == 0:
            print("\n  ALL PRICING ISSUES RESOLVED ✓")
            sys.exit(0)
        else:
            print(f"\n  {issues} issues remain — manual review needed")
            sys.exit(1)
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
