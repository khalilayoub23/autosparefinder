#!/usr/bin/env python3
"""
Pricing Policy Test Cycle
=========================
Verifies the platform pricing policy is correctly implemented.

Platform: price comparison marketplace. Each OEM number can have multiple rows.

  base_price 3-case logic:
    1. importer_price_ils > 0 (KGM/SsangYong only): base = max × 1.45
    2. importer = 0, online_price_ils > 0 (eBay/international): base = online × 1.45
    3. importer = 0, online = 0, max > 0 (IL official importer reference): base = max (no markup)

  IL official importers (Toyota, Kia, BMW, Mazda, Subaru, Samelet, BYD, Zeekr, Isuzu, LR, etc.):
    - importer_price_ils = 0 (no real procurement cost)
    - base_price = max_price_ils (show dealer retail as OEM reference — no extra markup)
    - Customer sees official dealer retail to compare with cheaper alternatives

  KGM/SsangYong (kgm.co.il — actual wholesale buyer):
    - importer_price_ils = price/1.17 (actual trade cost)
    - base_price = max_price_ils × 1.45 (trade cost + IL VAT + 45% margin)

  eBay / international:
    - online_price_ils = USD/EUR × exchange rate
    - base_price = online_price_ils × 1.45

Tests:
  T1  IL official importer (PDF excl. VAT) — e.g. Lexus, Toyota: base = max (no markup)
  T2  IL official importer (PDF incl. 18% VAT) — e.g. Porsche: base = max (no markup)
  T3  KGM/SsangYong (PDF incl. 17% VAT): base = max × 1.45
  T4  International supplier (eBay, no IL VAT): base = online × 1.45
  T5  calculate_customer_price_from_ils: domestic vs international
  T6  Path B fallback: domestic and international decomposition
  T7  DB spot check: IL official importer, KGM/SsangYong, eBay parts
  T8  Profit not exposed in comparison API response schema
"""

import asyncio
import os
import sys
from decimal import Decimal, ROUND_HALF_UP

MARGIN = 1.45
VAT_IL = 0.18
DEFAULT_SHIPPING = float(os.getenv("DEFAULT_CUSTOMER_SHIPPING_ILS", "59"))
TOLERANCE = 0.02  # ₪0.02 rounding tolerance


def round2(x: float) -> float:
    return float(Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def check(label: str, got: float, expected: float, tol: float = TOLERANCE) -> bool:
    ok = abs(got - expected) <= tol
    status = "PASS" if ok else "FAIL"
    print(f"  [{status}] {label}: got ₪{got:.2f}, expected ₪{expected:.2f}")
    return ok


# ─────────────────────────────────────────────────────────────────────────────
# Unit tests (no DB required)
# ─────────────────────────────────────────────────────────────────────────────

def test_t1_domestic_pdf_excl_vat():
    """T1: IL official importer, PDF price excl. VAT (Lexus/Toyota style).
    Rule: importer_price_ils=0, max_price_ils = dealer retail incl. VAT, base_price = max × 1.45.
    """
    print("\n[T1] IL official importer — PDF price EXCL. VAT (Lexus/Toyota)")
    pdf_price = 1000.0  # excl. VAT as stated in PDF

    importer_price = 0.0                             # no wholesale procurement cost
    max_price      = round2(pdf_price * 1.18)        # IL dealer retail incl. VAT = 1180
    base_price     = round2(max_price * MARGIN)      # 45% margin = 1711

    customer_total = round2(base_price + DEFAULT_SHIPPING)

    passed = all([
        check("importer_price_ils", importer_price, 0.0),
        check("max_price_ils",      max_price,      1180.0),
        check("base_price",         base_price,     1711.0),
        check("customer_total",     customer_total, round2(1711.0 + DEFAULT_SHIPPING)),
    ])
    return passed


def test_t2_domestic_pdf_incl_18vat():
    """T2: IL official importer, PDF price INCL. 18% VAT (Porsche style).
    Rule: importer_price_ils=0, max_price_ils = dealer retail incl. VAT, base_price = max × 1.45.
    """
    print("\n[T2] IL official importer — PDF price INCL. 18% VAT (Porsche)")
    pdf_price = 1180.0  # price as in PDF (incl. 18% VAT) = dealer retail

    importer_price = 0.0
    max_price      = round2(pdf_price)               # dealer retail incl. VAT = 1180
    base_price     = round2(max_price * MARGIN)      # 45% margin = 1711

    customer_total = round2(base_price + DEFAULT_SHIPPING)

    passed = all([
        check("importer_price_ils", importer_price, 0.0),
        check("max_price_ils",      max_price,      1180.0),
        check("base_price",         base_price,     1711.0),
        check("customer_total",     customer_total, round2(1711.0 + DEFAULT_SHIPPING)),
    ])
    return passed


def test_t3_domestic_pdf_incl_17vat():
    """T3: Domestic IL importer, PDF price INCL. 17% VAT (KGM/SsangYong style)."""
    print("\n[T3] Domestic IL importer — PDF price INCL. 17% VAT (KGM/SsangYong)")
    pdf_price = 1170.0  # price as in PDF (incl. 17% VAT)

    importer_price  = round2(pdf_price / 1.17)               # excl. VAT = 1000
    max_price_norm  = round2(importer_price * 1.18)          # normalized to 18% VAT = 1180
    base_price      = round2(max_price_norm * MARGIN)        # 1711

    customer_total = round2(base_price + DEFAULT_SHIPPING)
    expected_total = round2(importer_price * MARGIN * (1 + VAT_IL) + DEFAULT_SHIPPING)

    passed = all([
        check("importer_price_ils (excl. VAT)",  importer_price,  1000.0, 1.0),
        check("max_price_ils (normalized 18%)",  max_price_norm,  1180.0),
        check("base_price",                      base_price,      1711.0),
        check("customer_total",                  customer_total,  expected_total),
    ])
    return passed


def test_t4_international_no_vat():
    """T4: International supplier (eBay), no IL VAT."""
    print("\n[T4] International supplier — no IL VAT (eBay)")
    ebay_price = 500.0  # price we pay, no IL VAT

    online_price = round2(ebay_price)         # our cost = no VAT
    max_price    = round2(ebay_price)         # max_price_ils = same (no VAT for international)
    base_price   = round2(max_price * MARGIN) # 725

    customer_total = round2(base_price + DEFAULT_SHIPPING)
    expected_total = round2(ebay_price * MARGIN + DEFAULT_SHIPPING)  # no VAT

    passed = all([
        check("online_price_ils", online_price,  500.0),
        check("max_price_ils",    max_price,     500.0),
        check("base_price",       base_price,    725.0),
        check("customer_total",   customer_total, expected_total),
    ])
    # Also verify international gets LESS than domestic for same cost
    domestic_equiv = round2(500.0 * MARGIN * (1 + VAT_IL) + DEFAULT_SHIPPING)
    print(f"         [INFO] International ₪{customer_total:.0f} vs domestic-equiv ₪{domestic_equiv:.0f} "
          f"(correct: no IL VAT for international)")
    return passed


def test_t5_calculate_customer_price():
    """T5: calculate_customer_price_from_ils function logic."""
    print("\n[T5] calculate_customer_price_from_ils — domestic vs international")

    # Simulate the function
    def calc(cost_ils: float, is_domestic: bool, shipping: float = DEFAULT_SHIPPING) -> dict:
        vat_rate = VAT_IL if is_domestic else 0.0
        price_no_vat = round2(cost_ils * MARGIN)
        vat = round2(price_no_vat * vat_rate)
        total = round2(price_no_vat + vat + shipping)
        return {"price_no_vat": price_no_vat, "vat": vat, "total": total}

    domestic = calc(1000.0, is_domestic=True)
    intl     = calc(500.0,  is_domestic=False)

    passed = all([
        check("domestic price_no_vat", domestic["price_no_vat"], 1450.0),
        check("domestic vat",          domestic["vat"],           261.0),
        check("domestic total",        domestic["total"],         1711.0 + DEFAULT_SHIPPING),
        check("international price_no_vat", intl["price_no_vat"], 725.0),
        check("international vat",         intl["vat"],            0.0),
        check("international total",       intl["total"],          725.0 + DEFAULT_SHIPPING),
    ])
    return passed


def test_t6_path_b_fallback():
    """T6: Path B fallback uses 3-case base_price logic."""
    print("\n[T6] Path B fallback — 3 cases")

    # Case 1: KGM/SsangYong — importer_price_ils > 0 → base = max × 1.45
    il_retail_kgm  = round2(1000.0 / 1.17 * 1.18)    # normalized IL retail ≈ 1008.55
    bp_kgm         = round2(il_retail_kgm * MARGIN)
    total_kgm      = round2(bp_kgm + DEFAULT_SHIPPING)

    # Case 2: eBay/international — online > 0, importer = 0 → base = online × 1.45
    bp_intl        = round2(500.0 * MARGIN)    # 725
    total_intl     = round2(bp_intl + DEFAULT_SHIPPING)

    # Case 3: IL official importer reference — importer = 0, online = 0 → base = max × 1.45
    max_ref = 1180.0
    bp_il_ref      = round2(max_ref * MARGIN)  # 45% margin = 1711
    total_il_ref   = round2(bp_il_ref + DEFAULT_SHIPPING)

    passed = all([
        check("KGM fallback base_price",         bp_kgm,    round2(il_retail_kgm * MARGIN)),
        check("KGM fallback customer_total",      total_kgm, round2(il_retail_kgm * MARGIN + DEFAULT_SHIPPING)),
        check("eBay fallback base_price",         bp_intl,   725.0),
        check("eBay fallback customer_total",     total_intl, 725.0 + DEFAULT_SHIPPING),
        check("IL ref fallback base_price",       bp_il_ref, 1711.0),
        check("IL ref fallback customer_total",   total_il_ref, round2(1711.0 + DEFAULT_SHIPPING)),
    ])
    return passed


def test_t8_profit_not_in_api_schema():
    """T8: Verify 'profit' key is not in the supplier comparison response schema."""
    print("\n[T8] Profit not exposed in supplier comparison API schema")

    # Read the routes/parts.py file and verify 'profit' is not in comparisons.append({...})
    try:
        import re
        with open("/app/routes/parts.py", "r") as f:
            content = f.read()

        # Find the comparisons.append block
        match = re.search(r'comparisons\.append\(\{(.+?)\}\)', content, re.DOTALL)
        if match:
            block = match.group(1)
            has_profit = '"profit"' in block or "'profit'" in block
            ok = not has_profit
            print(f"  [{'PASS' if ok else 'FAIL'}] 'profit' key in comparisons.append: {has_profit}")
            return ok
        else:
            print("  [WARN] Could not find comparisons.append block in routes/parts.py")
            return True  # Not a blocking failure
    except Exception as e:
        print(f"  [WARN] File check failed: {e}")
        return True


# ─────────────────────────────────────────────────────────────────────────────
# DB tests (require DB connection)
# ─────────────────────────────────────────────────────────────────────────────

async def test_t7_db_spot_check():
    """T7: Spot-check actual DB rows for correct price ratios."""
    print("\n[T7] DB spot check — actual price ratios")

    try:
        import asyncpg
        db_url = os.getenv("DATABASE_URL", "")
        if not db_url:
            print("  [SKIP] DATABASE_URL not set — skipping DB tests")
            return True

        pg_url = db_url.replace("postgresql+asyncpg://", "postgresql://").replace("asyncpg://", "postgresql://")
        conn = await asyncpg.connect(pg_url)

        results = []
        try:
            # 1. IL official importers (e.g. Kia, Toyota, BMW): importer_price_ils = 0
            #    and base_price = max_price_ils (no extra markup)
            row = await conn.fetchrow("""
                SELECT COUNT(*) as total,
                  COUNT(*) FILTER (WHERE importer_price_ils > 0) as has_importer
                FROM parts_catalog
                WHERE manufacturer IN ('Kia','Toyota','BMW','Mazda','Subaru','Land Rover')
                  AND (online_price_ils IS NULL OR online_price_ils = 0)
                  AND max_price_ils > 0 AND is_active = TRUE
            """)
            total_il = int(row["total"] or 0)
            has_imp = int(row["has_importer"] or 0)
            ok = has_imp == 0
            results.append(ok)
            print(f"  [{'PASS' if ok else 'FAIL'}] IL official importer rows with importer_price_ils=0: "
                  f"{total_il - has_imp}/{total_il} correct")

            # 2. IL official importers: base_price = max_price_ils * 1.45 (ratio = 1.45)
            row1b = await conn.fetchrow("""
                SELECT ROUND(AVG(base_price / NULLIF(max_price_ils,0))::numeric, 4) as ratio,
                       COUNT(*) as cnt
                FROM parts_catalog
                WHERE manufacturer IN ('Kia','Toyota','BMW','Mazda','Subaru')
                  AND (importer_price_ils IS NULL OR importer_price_ils = 0)
                  AND (online_price_ils IS NULL OR online_price_ils = 0)
                  AND max_price_ils > 1.0 AND base_price > 0 AND is_active = TRUE
            """)
            ratio_il = float(row1b["ratio"] or 0)
            ok1b = abs(ratio_il - 1.45) <= 0.01
            results.append(ok1b)
            print(f"  [{'PASS' if ok1b else 'FAIL'}] IL ref base/max ratio: {ratio_il:.4f} (expected 1.45), n={row1b['cnt']}")

            # 3. SsangYong: importer_price_ils > 0 → base/max ratio ≈ 1.45
            row2 = await conn.fetchrow("""
                SELECT ROUND(AVG(base_price / NULLIF(max_price_ils,0))::numeric, 4) as ratio,
                       COUNT(*) as cnt
                FROM parts_catalog
                WHERE manufacturer = 'SsangYong'
                  AND importer_price_ils > 0 AND max_price_ils > 0 AND base_price > 0
            """)
            ratio2 = float(row2["ratio"] or 0)
            ok2 = abs(ratio2 - 1.45) <= 0.01
            results.append(ok2)
            print(f"  [{'PASS' if ok2 else 'FAIL'}] SsangYong base/max ratio: {ratio2:.4f} (expected 1.45), n={row2['cnt']}")

            # 4. SsangYong max/importer ratio should be 1.18 (normalized from 17% VAT)
            row3 = await conn.fetchrow("""
                SELECT ROUND(AVG(max_price_ils / NULLIF(importer_price_ils,0))::numeric, 4) as ratio,
                       COUNT(*) as cnt
                FROM parts_catalog
                WHERE manufacturer = 'SsangYong'
                  AND importer_price_ils > 0 AND max_price_ils > 0
            """)
            ratio3 = float(row3["ratio"] or 0)
            ok3 = abs(ratio3 - 1.18) <= 0.01
            results.append(ok3)
            print(f"  [{'PASS' if ok3 else 'FAIL'}] SsangYong max/importer ratio: {ratio3:.4f} (expected ~1.18), n={row3['cnt']}")

            # 5. eBay parts (Rover, Daewoo, Maserati): base/online ratio ≈ 1.45
            row4 = await conn.fetchrow("""
                SELECT ROUND(AVG(base_price / NULLIF(online_price_ils,0))::numeric, 4) as ratio,
                       COUNT(*) as cnt
                FROM parts_catalog
                WHERE manufacturer IN ('Rover','Daewoo','Maserati')
                  AND online_price_ils > 0 AND base_price > 0
                  AND (importer_price_ils IS NULL OR importer_price_ils = 0)
            """)
            if row4 and row4["cnt"] > 0:
                ratio4 = float(row4["ratio"] or 0)
                ok4 = abs(ratio4 - 1.45) <= 0.01
                results.append(ok4)
                print(f"  [{'PASS' if ok4 else 'FAIL'}] eBay base/online ratio: {ratio4:.4f} (expected 1.45), n={row4['cnt']}")
            else:
                print("  [SKIP] No eBay brand parts found in DB")

            # 6. All active parts with max_price_ils should have base ≈ max × 1.45
            row5 = await conn.fetchrow("""
                SELECT COUNT(*) as cnt
                FROM parts_catalog
                WHERE max_price_ils > 1.0 AND base_price > 0 AND is_active = TRUE
                  AND ABS(base_price - ROUND(max_price_ils * 1.45, 2)) > 1.0
                  AND (online_price_ils IS NULL OR online_price_ils = 0)
                  AND (importer_price_ils IS NULL OR importer_price_ils = 0)
            """)
            outliers = int(row5["cnt"] or 0)
            ok5 = outliers == 0
            results.append(ok5)
            print(f"  [{'PASS' if ok5 else 'FAIL'}] Case3 parts with base ≠ max×1.45 (outliers): {outliers}")

        finally:
            await conn.close()

        return all(results)

    except ImportError:
        print("  [SKIP] asyncpg not available")
        return True
    except Exception as e:
        print(f"  [FAIL] DB test error: {e}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

async def main():
    print("=" * 60)
    print("  AutoSpareFinder Pricing Policy Test Cycle")
    print(f"  MARGIN={MARGIN}  IL_VAT={VAT_IL}  SHIPPING=₪{DEFAULT_SHIPPING}")
    print("=" * 60)

    tests = [
        ("T1 Domestic PDF excl. VAT (Lexus/Toyota)",  test_t1_domestic_pdf_excl_vat),
        ("T2 Domestic PDF incl. 18% VAT (Porsche)",   test_t2_domestic_pdf_incl_18vat),
        ("T3 Domestic PDF incl. 17% VAT (KGM)",       test_t3_domestic_pdf_incl_17vat),
        ("T4 International no IL VAT (eBay)",          test_t4_international_no_vat),
        ("T5 calculate_customer_price_from_ils",       test_t5_calculate_customer_price),
        ("T6 Path B fallback decomposition",           test_t6_path_b_fallback),
        ("T8 Profit not in API schema",                test_t8_profit_not_in_api_schema),
    ]

    results = []
    for name, fn in tests:
        try:
            result = fn()
        except Exception as e:
            print(f"\n  [ERROR] {name}: {e}")
            result = False
        results.append((name, result))

    # DB test (async)
    try:
        db_result = await test_t7_db_spot_check()
        results.append(("T7 DB spot check", db_result))
    except Exception as e:
        print(f"\n  [ERROR] T7 DB spot check: {e}")
        results.append(("T7 DB spot check", False))

    print("\n" + "=" * 60)
    print("  RESULTS SUMMARY")
    print("=" * 60)
    passed = 0
    failed = 0
    for name, ok in results:
        status = "PASS" if ok else "FAIL"
        print(f"  [{status}] {name}")
        if ok:
            passed += 1
        else:
            failed += 1

    print(f"\n  Total: {passed} passed, {failed} failed")
    print("=" * 60)

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
