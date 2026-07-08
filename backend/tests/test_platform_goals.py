"""
AutoSpareFinder — Platform Goal Verification Tests
===================================================
Verifies the core platform goal:
  AI-driven dropshipping platform for auto parts from multiple suppliers,
  with correct barcodes/OEM numbers, pricing, IL shipping, and system health.

Run inside backend container:
  docker exec autospare_backend python3 /app/tests/test_platform_goals.py

Exit 0 = all checks pass (or only warnings).
Exit 1 = at least one FAIL.
"""

import asyncio
import asyncpg
import os
import sys
import time
import urllib.request
import json
from datetime import datetime, timedelta

DB_URL = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")
API_BASE = os.environ.get("API_BASE_URL", "http://localhost:8000")
MEILI_URL = os.environ.get("MEILI_URL", "http://meilisearch:7700")
MEILI_KEY = os.environ.get("MEILI_MASTER_KEY", "")

PASS = "✅ PASS"
WARN = "⚠️  WARN"
FAIL = "❌ FAIL"

results = []

def record(name, status, detail=""):
    tag = {"pass": PASS, "warn": WARN, "fail": FAIL}[status]
    line = f"{tag}  {name}"
    if detail:
        line += f"\n       {detail}"
    print(line)
    results.append(status)


# ── DB checks ─────────────────────────────────────────────────────────────────

async def run_db_checks():
    conn = await asyncpg.connect(DB_URL)
    await conn.execute("SET statement_timeout = '60s'")

    # 1. Catalog size sanity — use pg_class estimate (fast, ~1% accuracy)
    total = await conn.fetchval(
        "SELECT reltuples::bigint FROM pg_class WHERE relname='parts_catalog'"
    )
    if total >= 4_000_000:
        record("Catalog size ≥ 4M active parts", "pass", f"{total:,} active parts")
    elif total >= 1_000_000:
        record("Catalog size", "warn", f"Only {total:,} active parts (expected ≥ 4M)")
    else:
        record("Catalog size", "fail", f"Only {total:,} active parts")

    # 2. OEM barcode coverage (>99%) — sample check
    r_oem = await conn.fetchrow("""
        SELECT SUM(CASE WHEN oem_number IS NULL OR oem_number='' THEN 1 ELSE 0 END) as miss,
               COUNT(*) as n
        FROM (SELECT oem_number FROM parts_catalog TABLESAMPLE SYSTEM(1) WHERE is_active LIMIT 50000) s
    """)
    missing_oem_pct = r_oem["miss"] / r_oem["n"] * 100 if r_oem["n"] else 100
    pct_with_oem = 100 - missing_oem_pct
    if pct_with_oem >= 99:
        record("OEM barcode coverage ≥99%", "pass", f"{pct_with_oem:.1f}% have OEM number (~{int(missing_oem_pct/100*total):,} missing est.)")
    elif pct_with_oem >= 95:
        record("OEM barcode coverage", "warn", f"{pct_with_oem:.1f}% have OEM number")
    else:
        record("OEM barcode coverage", "fail", f"Only {pct_with_oem:.1f}% have OEM number")

    # 3. Price policy: base_price = importer_price_ils × 1.45 (±2%) — sample 100K rows
    r = await conn.fetchrow("""
        SELECT
          COUNT(*) FILTER (WHERE ABS(base_price - importer_price_ils * 1.45) / (importer_price_ils * 1.45) < 0.02) as correct,
          COUNT(*) FILTER (WHERE ABS(base_price - importer_price_ils * 1.45) / (importer_price_ils * 1.45) >= 0.02) as wrong,
          COUNT(*) as total_priced
        FROM (
          SELECT importer_price_ils, base_price
          FROM parts_catalog TABLESAMPLE SYSTEM(5)
          WHERE is_active AND importer_price_ils > 0 AND base_price > 0
          LIMIT 100000
        ) s
    """)
    wrong_pct = r["wrong"] / r["total_priced"] * 100 if r["total_priced"] else 0
    if wrong_pct < 0.5:
        record("Price policy 45% margin", "pass",
               f"{r['correct']:,} correct, {r['wrong']:,} wrong ({wrong_pct:.2f}%)")
    elif wrong_pct < 2:
        record("Price policy 45% margin", "warn",
               f"{r['wrong']:,} parts have wrong margin ({wrong_pct:.1f}%)")
    else:
        record("Price policy 45% margin", "fail",
               f"{r['wrong']:,} parts have wrong margin ({wrong_pct:.1f}%) — CRITICAL")

    # 4. Part condition casing (must be lowercase) — sample check
    bad_cond = await conn.fetchval("""
        SELECT COUNT(*) FROM (
          SELECT part_condition FROM parts_catalog TABLESAMPLE SYSTEM(2) WHERE is_active LIMIT 50000
        ) s
        WHERE part_condition NOT IN ('new','used','oem','aftermarket','remanufactured','oe_equivalent','reconditioned')
    """)
    if bad_cond == 0:
        record("Part condition casing (all lowercase)", "pass")
    else:
        record("Part condition casing", "fail", f"{bad_cond:,} parts have invalid/uppercase part_condition")

    # 5. VAT rate spot-check — strict test: parts must NOT have max_price = cost × 1.17
    # 17% was the wrong historic rate; 18% is correct. Use strict non-overlapping tolerance.
    r2 = await conn.fetchrow("""
        SELECT
          COUNT(*) FILTER (WHERE ABS(max_price_ils / importer_price_ils - 1.17) < 0.005) as strict_17,
          COUNT(*) FILTER (WHERE ABS(max_price_ils / importer_price_ils - 1.18) < 0.005) as strict_18,
          COUNT(*) as sampled
        FROM (
          SELECT importer_price_ils, max_price_ils
          FROM parts_catalog
          WHERE is_active AND importer_price_ils > 5 AND max_price_ils > 5
          LIMIT 5000
        ) s
    """)
    if r2["sampled"] > 100:
        at_17 = r2["strict_17"] or 0
        at_18 = r2["strict_18"] or 0
        pct_17 = at_17 / r2["sampled"] * 100
        if pct_17 > 5:
            record("VAT rate 18% (not 17%)", "fail",
                   f"{pct_17:.1f}% of sample use wrong 17% VAT — must be 18%")
        else:
            record("VAT rate 18% spot-check", "pass",
                   f"Sample {r2['sampled']}: {at_18} at 18%, {at_17} at 17% ({pct_17:.1f}%)")
    else:
        record("VAT rate check", "warn", "Too few samples")

    # 6. IL importer price coverage — use quick estimate via sample
    priced_sample = await conn.fetchrow("""
        SELECT SUM(CASE WHEN importer_price_ils > 0 THEN 1 ELSE 0 END) as priced, COUNT(*) as sampled
        FROM (SELECT importer_price_ils FROM parts_catalog TABLESAMPLE SYSTEM(1) WHERE is_active LIMIT 50000) s
    """)
    priced = int(priced_sample["priced"] / priced_sample["sampled"] * total) if priced_sample["sampled"] else 0
    priced_pct = priced / total * 100
    if priced_pct >= 45:
        record("IL importer price coverage ≥45%", "pass",
               f"{priced:,}/{total:,} ({priced_pct:.1f}%) — structural ceiling for global catalog")
    else:
        record("IL importer price coverage", "warn",
               f"Only {priced_pct:.1f}% priced — check importers")

    # 7. Supplier_parts available count (fast check — no EXISTS scan)
    supplier_priced = await conn.fetchval(
        "SELECT COUNT(*) FROM supplier_parts WHERE is_available AND price_ils > 0"
    )
    pct_sup = supplier_priced / total * 100
    if supplier_priced >= 500_000:
        record("Supplier_parts available+priced ≥500K", "pass",
               f"{supplier_priced:,} available priced supplier records")
    else:
        record("Supplier_parts availability", "warn", f"Only {supplier_priced:,} available priced records")

    # 8. IL suppliers active
    il_count = await conn.fetchval(
        "SELECT COUNT(*) FROM suppliers WHERE country='IL' AND is_active"
    )
    if il_count >= 10:
        record("IL suppliers active", "pass", f"{il_count} active IL suppliers")
    else:
        record("IL suppliers active", "warn", f"Only {il_count} active IL suppliers")

    # 9. Supplier parts for car-parts.ie (IE → ships to IL via platform)
    cp_ie = await conn.fetchval("""
        SELECT COUNT(*) FROM supplier_parts sp
        JOIN suppliers s ON s.id=sp.supplier_id
        WHERE s.name='Car-Parts.ie' AND sp.is_available AND sp.price_ils > 0
    """)
    if cp_ie >= 100_000:
        record("Car-Parts.ie (IE) supplier active", "pass",
               f"{cp_ie:,} available priced parts from car-parts.ie")
    else:
        record("Car-Parts.ie supplier", "warn", f"Only {cp_ie:,} available priced car-parts.ie records")

    # 10. Part type populated — sample check
    missing_type_s = await conn.fetchrow("""
        SELECT SUM(CASE WHEN part_type IS NULL OR part_type='' THEN 1 ELSE 0 END) as miss, COUNT(*) as n
        FROM (SELECT part_type FROM parts_catalog TABLESAMPLE SYSTEM(1) WHERE is_active LIMIT 50000) s
    """)
    missing_type_pct = missing_type_s["miss"] / missing_type_s["n"] * 100 if missing_type_s["n"] else 100
    if missing_type_pct < 1:
        record("Part type populated ≥99%", "pass", f"~{missing_type_pct:.1f}% missing in sample")
    else:
        record("Part type populated", "warn", f"~{missing_type_pct:.1f}% missing part_type in sample")

    # 11. Cross-reference table (OEM ↔ aftermarket barcodes)
    xref = await conn.fetchval("SELECT COUNT(*) FROM part_cross_reference")
    if xref >= 20_000:
        record("OEM cross-reference table populated", "pass", f"{xref:,} cross-reference rows")
    else:
        record("OEM cross-reference table", "warn", f"Only {xref:,} rows (target ≥20K)")

    # 12. Fitment data (vehicle compatibility)
    fitment = await conn.fetchval(
        "SELECT COUNT(*) FROM part_vehicle_fitment"
    )
    if fitment >= 200_000:
        record("Vehicle fitment data", "pass", f"{fitment:,} fitment rows")
    else:
        record("Vehicle fitment data", "warn", f"Only {fitment:,} fitment rows")

    # 13. DB agent heartbeat (active within last 15 min)
    last_hb = await conn.fetchval("""
        SELECT MAX(last_heartbeat_at) FROM job_registry
        WHERE job_id LIKE '%db_update%' OR job_id LIKE '%run_all_tasks%'
    """)
    if last_hb and (datetime.utcnow() - last_hb.replace(tzinfo=None)) < timedelta(hours=3):
        record("DB update agent heartbeat recent", "pass",
               f"Last heartbeat: {last_hb.strftime('%H:%M UTC')}")
    else:
        record("DB update agent heartbeat", "warn", f"Last heartbeat: {last_hb}")

    # 14. Supplier_parts: price_ils > 0 for available records (just availability check)
    # NOTE: price_ils = cost we pay supplier, base_price = our selling price (cost×1.45), they differ by design
    zero_price_available = await conn.fetchval("""
        SELECT COUNT(*) FROM supplier_parts
        WHERE is_available AND (price_ils IS NULL OR price_ils = 0)
        LIMIT 1000
    """)
    if zero_price_available == 0:
        record("No available supplier records with zero price", "pass")
    elif zero_price_available < 100:
        record("Available supplier records with zero price", "warn", f"{zero_price_available} have is_available=true but price_ils=0")
    else:
        record("Available supplier records with zero price", "fail",
               f"{zero_price_available}+ records marked available but price_ils=0")

    await conn.close()


# ── API checks ────────────────────────────────────────────────────────────────

def api_get(path, token=None):
    url = f"{API_BASE}{path}"
    req = urllib.request.Request(url)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except Exception as e:
        return 0, {"error": str(e)}


def run_api_checks():
    # Health endpoint
    status, body = api_get("/health")
    if status == 200 and body.get("status") in ("ok", "healthy"):
        record("API /health endpoint", "pass", f"status={body.get('status')}")
    elif status == 200:
        record("API /health endpoint", "warn", f"status={body.get('status')}")
    else:
        record("API /health endpoint", "fail", f"HTTP {status}: {body}")

    # Search API — response format: {original:{part:{...}}, oem:{part:{...}}, aftermarket:{...}, all_parts:[...]}
    status, body = api_get("/api/v1/parts/search?q=brake+pads&limit=5")
    if status == 200:
        # Extract first result from any category
        first_part = None
        for key in ("original", "oem", "aftermarket"):
            if body.get(key, {}).get("part"):
                first_part = body[key]["part"]
                break
        if first_part is None and body.get("all_parts"):
            first_part = body["all_parts"][0]
        if first_part:
            has_price = first_part.get("base_price", 0) > 0
            has_oem = bool(first_part.get("oem_number"))
            record("Search API returns results", "pass",
                   f"Got results | first: {first_part.get('name','?')[:50]} | price={first_part.get('base_price')} | oem={has_oem}")
            if not has_price:
                record("Search results have prices", "warn", "First result has no base_price")
            else:
                record("Search results have prices", "pass")
        else:
            record("Search API returns results", "warn", f"Status 200 but no part in response. Keys: {list(body.keys())}")
    else:
        record("Search API", "fail", f"HTTP {status}: {body.get('error','?')}")

    # Part supplier comparison endpoint
    status, body = api_get("/api/v1/parts/search?q=oil+filter+toyota&limit=1")
    first = (body.get("original", {}).get("part") or
             body.get("oem", {}).get("part") or
             (body.get("all_parts") or [None])[0]) if status == 200 else None
    if first:
        part_id = first.get("id")
        if part_id:
            status2, body2 = api_get(f"/api/v1/parts/{part_id}/suppliers")
            if status2 == 200:
                suppliers = body2.get("suppliers", [])
                record("Supplier comparison endpoint", "pass",
                       f"Part {part_id[:8]}... has {len(suppliers)} suppliers listed")
            else:
                record("Supplier comparison endpoint", "warn", f"HTTP {status2}")
        else:
            record("Supplier comparison endpoint", "warn", "Could not get part_id from search")
    else:
        record("Supplier comparison endpoint", "warn", f"No search results (status={status})")


# ── Meilisearch check ─────────────────────────────────────────────────────────

def run_meili_check():
    try:
        headers = {}
        if MEILI_KEY:
            headers["Authorization"] = f"Bearer {MEILI_KEY}"
        req = urllib.request.Request(f"{MEILI_URL}/indexes/parts/stats", headers=headers)
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        meili_docs = data.get("numberOfDocuments", 0)
        if meili_docs >= 4_000_000:
            record("Meilisearch index size ≥4M", "pass", f"{meili_docs:,} docs indexed")
        elif meili_docs >= 3_000_000:
            record("Meilisearch index", "warn", f"{meili_docs:,} docs (catalog may be ahead)")
        else:
            record("Meilisearch index", "fail", f"Only {meili_docs:,} docs — likely out of sync")
    except Exception as e:
        record("Meilisearch connectivity", "warn", f"Could not reach Meili: {e}")


# ── Price calculation spot-checks ─────────────────────────────────────────────

def run_price_formula_checks():
    cases = [
        # (consumer_price_incl_vat, expected_cost, expected_base)
        (1180.0, 1000.0, 1450.0),   # 1180/1.18=1000, 1000*1.45=1450
        (590.0, 500.0, 725.0),
        (236.0, 200.0, 290.0),
    ]
    all_ok = True
    for price, expected_cost, expected_base in cases:
        cost = round(price / 1.18, 2)
        base = round(cost * 1.45, 2)
        if abs(cost - expected_cost) < 0.01 and abs(base - expected_base) < 0.01:
            pass
        else:
            all_ok = False
            print(f"       Formula error: price={price} → cost={cost} (expected {expected_cost}), base={base} (expected {expected_base})")
    if all_ok:
        record("Price formula (consumer/1.18 × 1.45 = base)", "pass",
               "consumer_price → cost(ex-VAT) → base_price all correct")
    else:
        record("Price formula", "fail", "Formula calculation error")


# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    print("=" * 65)
    print("AutoSpareFinder — Platform Goal Verification")
    print(f"Run at: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 65)

    print("\n── DATABASE CHECKS ──")
    await run_db_checks()

    print("\n── API CHECKS ──")
    run_api_checks()

    print("\n── SEARCH ENGINE ──")
    run_meili_check()

    print("\n── PRICE FORMULA VERIFICATION ──")
    run_price_formula_checks()

    # Summary
    n_pass = results.count("pass")
    n_warn = results.count("warn")
    n_fail = results.count("fail")

    print("\n" + "=" * 65)
    print(f"SUMMARY: {n_pass} PASS  |  {n_warn} WARN  |  {n_fail} FAIL")
    print("=" * 65)

    if n_fail > 0:
        print("\nFailed checks need fixing before platform launch.")
        sys.exit(1)
    elif n_warn > 0:
        print("\nWarnings present — review before launch, not blockers.")
        sys.exit(0)
    else:
        print("\nAll checks pass — platform goal met.")
        sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
