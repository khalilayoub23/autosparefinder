"""FREESBE PRICING POLICY — regression tests — 2026-09-05.

Forensic audit finding: process_page() (both the INSERT branch for new parts
and the UPDATE branch for existing matched parts) stored the FREESBE API's
VAT-INCLUSIVE `price` directly into `importer_price_ils`, which the platform's
authoritative pricing contract defines as the EX-VAT supplier cost (see
CLAUDE.md "Import formula", BACKEND_AI_AGENTS.get_supplier_vat_rate, and every
other IL importer — colmobil_import_v2.py, kia_israel_harvester.py, etc.). The
UPDATE branch additionally never wrote `base_price` or `max_price_ils` at all,
and matches by OEM number across EVERY manufacturer in the catalog (not just
Renault/Nissan/Chery/Xpeng/JAC) — so its blast radius is the whole catalog,
not just freesbe-created rows (173,852 rows touched in the 2026-09-05 run vs.
2,674 inserted).

This is exactly the gap the pre-existing freesbe_importer_hardening_test.py
never covered — it asserts process_page()'s SIGNATURE is unchanged (regression
check, line ~113) but never exercises its pricing arithmetic. This file fills
that gap by calling parse_part()/process_page() directly against a live
sandbox DB and checking the actual written values.

Run (static checks only, safe anywhere incl. production container):
    docker exec autospare_backend python3 /app/devtests/freesbe_pricing_policy_test.py

Run (full suite, includes sandbox DB integration tests):
    docker exec sandbox_backend python3 /app/devtests/freesbe_pricing_policy_test.py
"""
import asyncio
import os
import sys
import uuid

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/importers")

fails: list[str] = []
skipped: list[str] = []


def check(label: str, got, want) -> None:
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"         got : {got!r}")
        print(f"         want: {want!r}")
        fails.append(label)


def check_true(label: str, cond: bool) -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {label}")
    if not cond:
        fails.append(label)


def check_present(label: str, pattern: str, src: str) -> None:
    ok = pattern in src
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"         pattern not found: {pattern!r}")
        fails.append(label)


def check_absent(label: str, pattern: str, src: str) -> None:
    ok = pattern not in src
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"         pattern unexpectedly present (should be gone): {pattern!r}")
        fails.append(label)


def skip(label: str, reason: str) -> None:
    print(f"  SKIP  {label} — {reason}")
    skipped.append(label)


import freesbe_importer as fi  # noqa: E402 — module import, __main__ guard prevents execution

DB_URL = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")
IS_SANDBOX = "sandbox_postgres" in DB_URL

# ═══════════════════════════════════════════════════════════════════════════
print("=== POLICY ARITHMETIC — the authoritative pricing formulas themselves ===")

# Israeli supplier: cost ex-VAT -> selling base (×1.45) -> VAT-inclusive
# reference/customer price (×1.18 on top of the selling base). This is the
# formula the FREESBE fix must feed, not a freesbe-specific invention.
_cost = 100.0
_selling_base = round(_cost * 1.45, 2)
_il_customer_ref = round(_selling_base * 1.18, 2)
check("Israeli policy: selling base = cost × 1.45", _selling_base, 145.0)
check("Israeli policy: VAT-inclusive customer/reference price = selling_base × 1.18", _il_customer_ref, 171.10)

# Foreign supplier: no VAT stage at all.
_foreign_customer_ref = _selling_base
check("Foreign policy: customer/reference price = selling base (no VAT applied)", _foreign_customer_ref, 145.0)

# The two must differ by exactly the 18% VAT factor — proves the distinction
# is real and not accidentally collapsed to one formula.
check_true("Israeli vs foreign customer price differ by exactly 18%",
           round(_il_customer_ref / _foreign_customer_ref, 4) == 1.18)

print()
print("=== STATIC — parse_part() extracts the authoritative ex-VAT cost ===")

# FREESBE is an Israeli supplier: API price is VAT-inclusive; priceWithoutVat
# is the API's own authoritative ex-VAT cost (verified live 2026-09-05 present
# on 20/20 sampled items across the full page range, ratio ~1.18).
_p = fi.parse_part({"partId": "RE-TESTOEM1", "price": "118.00", "priceWithoutVat": "100.00",
                     "description": "Test part", "isOriginal": True, "isAvailable": True})
check("parse_part: price_ils (VAT-inclusive) preserved for max_price_ils", _p["price_ils"], 118.00)
check("parse_part: price_ex_vat comes directly from priceWithoutVat, not derived from price/1.18",
      _p["price_ex_vat"], 100.00)

# Fallback path: API omits priceWithoutVat -> derive via /1.18 rather than crash or store None.
_p_fallback = fi.parse_part({"partId": "RE-TESTOEM2", "price": "118.00", "description": "No ex-vat field"})
check("parse_part: falls back to price/1.18 when priceWithoutVat is missing",
      _p_fallback["price_ex_vat"], round(118.00 / 1.18, 2))

# Malformed / zero priceWithoutVat -> falls back safely, never stores a bad or zero cost.
_p_malformed = fi.parse_part({"partId": "RE-TESTOEM3", "price": "118.00", "priceWithoutVat": "not-a-number"})
check("parse_part: malformed priceWithoutVat falls back to price/1.18, does not crash",
      _p_malformed["price_ex_vat"], round(118.00 / 1.18, 2))
_p_zero = fi.parse_part({"partId": "RE-TESTOEM4", "price": "118.00", "priceWithoutVat": "0"})
check("parse_part: priceWithoutVat=0 falls back to price/1.18 rather than storing a zero cost",
      _p_zero["price_ex_vat"], round(118.00 / 1.18, 2))

print()
print("=== STATIC — source-level regression checks ===")
_src = open("/app/importers/freesbe_importer.py", encoding="utf-8").read()

check_present("INSERT branch: importer_price_ils comes from the ex-VAT cost, not the VAT-inclusive price",
              'cost_ex_vat = p["price_ex_vat"]', _src)
check_present("INSERT branch: max_price_ils is now populated in the column list",
              "importer_price_ils, base_price, max_price_ils, is_active", _src)
check_present("INSERT branch: ON CONFLICT also refreshes base_price under the same guard",
              "base_price = CASE WHEN EXCLUDED.importer_price_ils > 0 THEN EXCLUDED.base_price", _src)
check_present("UPDATE branch: base_price is now computed and written alongside importer_price_ils",
              "new_base_price = round(new_cost_ex_vat * 1.45, 2)", _src)
check_present("UPDATE branch: max_price_ils is now written from the VAT-inclusive reference price",
              "SET importer_price_ils = $1, base_price = $2, max_price_ils = $3", _src)
check_absent("normalize_base_price_batched no longer divides an already-ex-VAT cost by 1.18",
             "importer_price_ils / 1.18", _src)
check_present("normalize_base_price_batched matches db_update_agent's canonical formula (importer_price_ils × 1.45)",
              "base_price = ROUND(importer_price_ils * 1.45, 2)", _src)
check_present("regression: process_page still matches on normalized OEM number (unchanged matching logic)",
              "regexp_replace(upper(COALESCE(oem_number, '')), '[^A-Z0-9]', '', 'g')", _src)
check_present("regression: idempotent upsert (ON CONFLICT sku) unchanged", "ON CONFLICT (sku) DO UPDATE SET", _src)

print()
if not IS_SANDBOX:
    print("=== INTEGRATION — SKIPPED (this run's DATABASE_URL is not the sandbox) ===")
    print("    Re-run via: docker exec sandbox_backend python3 /app/devtests/freesbe_pricing_policy_test.py")
    for _t in ["1: INSERT branch pricing", "2: UPDATE branch pricing (cross-manufacturer)",
               "3: UPDATE branch skip-guard unit consistency", "4: normalize_base_price_batched contract match",
               "5: RE-8200665342-style stale row cannot recur"]:
        skip(_t, "requires sandbox_postgres — not reachable/selected from this container")
else:
    import asyncpg

    async def _conn():
        conn = await asyncpg.connect(DB_URL, timeout=10)
        assert "sandbox" in DB_URL, "refusing to run write-tests against a non-sandbox DATABASE_URL"
        return conn

    async def _make_brand(conn, name: str) -> str:
        bid = str(uuid.uuid4())
        await conn.execute(
            "INSERT INTO car_brands (id, name, is_active) VALUES ($1, $2, TRUE)", bid, name
        )
        return bid

    async def _make_part(conn, *, oem: str, manufacturer: str, manufacturer_id: str,
                          importer_price_ils=None, base_price=None, max_price_ils=None,
                          sku: str) -> str:
        pid = str(uuid.uuid4())
        await conn.execute(
            """
            INSERT INTO parts_catalog (
                id, sku, oem_number, name, manufacturer, manufacturer_id, part_condition,
                is_safety_critical, needs_oem_lookup, master_enriched,
                importer_price_ils, base_price, max_price_ils, is_active
            ) VALUES ($1,$2,$3,$4,$5,$6,'new',FALSE,FALSE,FALSE,$7,$8,$9,TRUE)
            """,
            pid, sku, oem, f"Pricing test part {sku}", manufacturer, manufacturer_id,
            importer_price_ils, base_price, max_price_ils,
        )
        return pid

    async def _cleanup(conn, part_ids: list[str], brand_ids: list[str], skus: list[str]):
        if part_ids:
            await conn.execute("DELETE FROM parts_catalog WHERE id = ANY($1::uuid[])", part_ids)
        if skus:
            await conn.execute("DELETE FROM parts_catalog WHERE sku = ANY($1::text[])", skus)
        if brand_ids:
            await conn.execute("DELETE FROM car_brands WHERE id = ANY($1::uuid[])", brand_ids)

    print(f"=== INTEGRATION — process_page() pricing (sandbox: {DB_URL.split('@')[-1]}) ===")

    async def run():
        conn = await _conn()
        part_ids: list[str] = []
        brand_ids: list[str] = []
        insert_skus: list[str] = ["RE-9999000111"]
        try:
            renault_id = await _make_brand(conn, f"PricingTestRenault-{uuid.uuid4().hex[:8]}")
            brand_ids.append(renault_id)
            toyota_id = await _make_brand(conn, f"PricingTestToyota-{uuid.uuid4().hex[:8]}")
            brand_ids.append(toyota_id)

            # ── Test 1: INSERT branch — new unmatched RE- part ──────────────
            print("\n1: INSERT branch pricing (new Renault part, PHASE 11 spec: 118 VAT-in -> 100 cost -> 145 base)")
            stats1 = {"updated": 0, "inserted": 0, "not_found": 0}
            part1 = fi.parse_part({"partId": "RE-9999000111", "price": "118.00",
                                    "priceWithoutVat": "100.00", "description": "Insert test"})
            await fi.process_page(conn, [part1], stats1, {"Renault": renault_id})
            check("1: exactly one insert recorded", stats1["inserted"], 1)
            row1 = await conn.fetchrow(
                "SELECT id, importer_price_ils, base_price, max_price_ils FROM parts_catalog WHERE sku='RE-9999000111'"
            )
            check_true("1: inserted row exists", row1 is not None)
            if row1:
                part_ids.append(row1["id"])
                check("1: importer_price_ils = ex-VAT cost (100.00), NOT the VAT-inclusive 118.00",
                      float(row1["importer_price_ils"]), 100.00)
                check("1: base_price = cost × 1.45 = 145.00 (matches PHASE 11 spec exactly)",
                      float(row1["base_price"]), 145.00)
                check("1: max_price_ils = the original VAT-inclusive reference price (118.00)",
                      float(row1["max_price_ils"]), 118.00)
                check_true("1: base_price is NOT 171.10 (VAT must not be baked into base_price)",
                           float(row1["base_price"]) != 171.10)

            # ── Test 2: UPDATE branch — existing part, ANY manufacturer ─────
            print("\n2: UPDATE branch pricing (existing non-Renault part — proves the cross-manufacturer blast radius is now safe)")
            existing_pid = await _make_part(
                conn, oem="777888999", manufacturer="PricingTestToyota", manufacturer_id=toyota_id,
                importer_price_ils=50.0, base_price=72.5, sku="PRICETEST-EXISTING-1",
            )
            part_ids.append(existing_pid)
            stats2 = {"updated": 0, "inserted": 0, "not_found": 0}
            part2 = fi.parse_part({"partId": "XX-777888999", "price": "118.00",
                                    "priceWithoutVat": "100.00", "description": "Update test"})
            await fi.process_page(conn, [part2], stats2, {})
            check("2: exactly one update recorded", stats2["updated"], 1)
            row2 = await conn.fetchrow(
                "SELECT importer_price_ils, base_price, max_price_ils, manufacturer FROM parts_catalog WHERE id=$1",
                existing_pid,
            )
            check("2: UPDATE branch writes ex-VAT cost, not the raw VAT-inclusive price",
                  float(row2["importer_price_ils"]), 100.00)
            check("2: UPDATE branch now sets base_price too (previously left stale/untouched)",
                  float(row2["base_price"]), 145.00)
            check("2: UPDATE branch now sets max_price_ils too (previously never populated)",
                  float(row2["max_price_ils"]), 118.00)
            check("2: the matched row's manufacturer is untouched (freesbe never overwrites manufacturer)",
                  row2["manufacturer"], "PricingTestToyota")

            # ── Test 3: UPDATE branch skip-guard is now unit-consistent ─────
            print("\n3: UPDATE branch skip-guard (old_price vs new cost, both ex-VAT now)")
            skip_pid = await _make_part(
                conn, oem="SKIPGUARD1", manufacturer="PricingTestToyota", manufacturer_id=toyota_id,
                importer_price_ils=500.0, base_price=725.0, sku="PRICETEST-SKIPGUARD-1",
            )
            part_ids.append(skip_pid)
            stats3 = {"updated": 0, "inserted": 0, "not_found": 0}
            part3 = fi.parse_part({"partId": "XX-SKIPGUARD1", "price": "118.00",
                                    "priceWithoutVat": "100.00", "description": "Skip guard test"})
            await fi.process_page(conn, [part3], stats3, {})
            check("3: stale-guard skips when old ex-VAT cost (500) is already > new ex-VAT cost (100) × 2",
                  stats3["updated"], 0)
            row3 = await conn.fetchrow("SELECT importer_price_ils, base_price FROM parts_catalog WHERE id=$1", skip_pid)
            check("3: skipped row's importer_price_ils is untouched", float(row3["importer_price_ils"]), 500.0)
            check("3: skipped row's base_price is untouched", float(row3["base_price"]), 725.0)

            # ── Test 4: normalize_base_price_batched matches process_page's own contract ──
            print("\n4: normalize_base_price_batched formula now matches process_page's own formula")
            norm_pid = await _make_part(
                conn, oem="NORMTEST1", manufacturer="PricingTestToyota", manufacturer_id=toyota_id,
                importer_price_ils=100.0, base_price=None, sku="PRICETEST-NORM-1",
            )
            part_ids.append(norm_pid)
            n, complete = await fi.normalize_base_price_batched(conn, batch_size=10, statement_timeout_ms=5000)
            check_true("4: normalization completed without error", complete)
            row4 = await conn.fetchrow("SELECT base_price FROM parts_catalog WHERE id=$1", norm_pid)
            check("4: normalize_base_price_batched(100.0 ex-VAT) == 145.00, same as process_page's own math",
                  float(row4["base_price"]), 145.00)

            # ── Test 5: the exact RE-8200665342 stale-row shape cannot recur ──
            print("\n5: regression — the exact stale-row shape found in Production (RE-8200665342) cannot recur")
            # That row showed importer_price_ils=374.67 (raw VAT-inclusive, written by the
            # old UPDATE-branch bug with NO conversion) alongside a base_price=543.27 that
            # equals 374.67*1.45 — i.e. some other process later multiplied the WRONG
            # (VAT-inclusive) importer_price_ils by 1.45, an 18%-inflated base_price that
            # looked numerically ordinary. Prove the fixed process_page() cannot produce
            # this shape: feed it real-shaped API numbers (price=VAT-inclusive,
            # priceWithoutVat=ex-VAT) and confirm importer_price_ils lands on the ex-VAT
            # figure, with base_price computed from that SAME correct figure in one write.
            regress_pid = await _make_part(
                conn, oem="RE8200665342REGR", manufacturer="PricingTestToyota", manufacturer_id=toyota_id,
                importer_price_ils=None, base_price=None, sku="PRICETEST-REGRESS-1",
            )
            part_ids.append(regress_pid)
            stats5 = {"updated": 0, "inserted": 0, "not_found": 0}
            part5 = fi.parse_part({"partId": "XX-RE8200665342REGR", "price": "442.11",
                                    "priceWithoutVat": "374.67", "description": "Regression test"})
            await fi.process_page(conn, [part5], stats5, {})
            row5 = await conn.fetchrow("SELECT importer_price_ils, base_price FROM parts_catalog WHERE id=$1", regress_pid)
            check("5: importer_price_ils lands on the ex-VAT cost (374.67), never the VAT-inclusive 442.11",
                  float(row5["importer_price_ils"]), 374.67)
            check("5: base_price computed fresh from the correct ex-VAT cost in the same write (543.27... corrected)",
                  float(row5["base_price"]), round(374.67 * 1.45, 2))
        finally:
            await _cleanup(conn, part_ids, brand_ids, insert_skus)
            await conn.close()

    asyncio.run(run())

print()
if fails:
    print(f"FAILED: {len(fails)} test(s):")
    for f in fails:
        print(f"  - {f}")
    sys.exit(1)

if skipped:
    print(f"SKIPPED (sandbox-only): {len(skipped)}")
print("ALL EXECUTED TESTS PASS")
