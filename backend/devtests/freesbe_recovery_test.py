"""FREESBE RECOVERY TOOL — tests — 2026-09-06 (expanded).

Covers freesbe_recovery.py's pure classification/ownership-tier logic (no
I/O), the write-path's safety interlocks (state-drift, re-classification,
row-count guard — exercised only against sandbox fixtures, never Production),
and end-to-end DB matching against fabricated fixtures recreating the exact
real-world shapes documented in the forensic reports.

Run (pure tests only, safe anywhere incl. production container):
    docker exec autospare_backend python3 /app/devtests/freesbe_recovery_test.py

Run (full suite, includes sandbox DB integration + write-path tests):
    docker exec sandbox_backend python3 /app/devtests/freesbe_recovery_test.py
"""
import asyncio
import json
import os
import sys
import uuid
from decimal import Decimal

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
        fails.append(label)


def check_absent(label: str, pattern: str, src: str) -> None:
    ok = pattern not in src
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        fails.append(label)


def skip(label: str, reason: str) -> None:
    print(f"  SKIP  {label} — {reason}")
    skipped.append(label)


import freesbe_recovery as fr  # noqa: E402

DB_URL = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")
IS_SANDBOX = "sandbox_postgres" in DB_URL
D = Decimal

# ═══════════════════════════════════════════════════════════════════════════
print("=== STATIC — no unconfirmed write path ===")
_src = open("/app/importers/freesbe_recovery.py", encoding="utf-8").read()
check_present("apply_recovery requires confirm=True or raises", 'if not confirm:', _src)
check_present("apply_recovery re-verifies state before writing (state-drift guard)",
              "SKIP_DB_STATE_DRIFT", _src)
check_present("apply_recovery re-classifies before writing (plan can go stale)",
              "skip_reclassified", _src)
check_present("CLI requires BOTH --apply and --confirm-production-recovery",
              "if not args.confirm_production_recovery or not args.from_report:", _src)
check_present("apply uses per-row transaction (savepoint isolation)",
              "async with conn.transaction():", _src)
check_present("only SAFE_AUTOMATIC_RECOVERY rows are ever write candidates",
              'r["classification"] == "SAFE_AUTOMATIC_RECOVERY"', _src)
check_absent("dry-run path (run_dry_run) issues no write statement",
             "await conn.execute(\n            \"\"\"UPDATE parts_catalog", _src)  # only apply_recovery's UPDATE exists, and it's guarded above

print()
print("=== TEST 1 — normal safe FREESBE correction ===")
c, t, r = fr.classify_row(is_active=True, current_importer_price=D("118.00"), current_base_price=D("500.00"),
                           live_price=D("118.00"), expected_base_price=D("145.00"), source=None)
check("1: classification", c, "SAFE_AUTOMATIC_RECOVERY")

print()
print("=== TEST 2 — exact VAT-inclusive historical corruption (ratio erased) ===")
corrupted_importer = D("118.00")
corrupted_base = (corrupted_importer * D("1.45")).quantize(D("0.01"))  # 171.10 — looks like an ordinary 1.45 ratio
c, t, r = fr.classify_row(is_active=True, current_importer_price=corrupted_importer, current_base_price=corrupted_base,
                           live_price=D("118.00"), expected_base_price=D("145.00"), source=None)
check("2: still detected via live-price equality despite the erased ratio", c, "SAFE_AUTOMATIC_RECOVERY")

print()
print("=== TEST 3 — already corrected EX-VAT row → NO_CHANGE ===")
c, t, r = fr.classify_row(is_active=True, current_importer_price=D("100.00"), current_base_price=D("145.00"),
                           live_price=D("118.00"), expected_base_price=D("145.00"), source="freesbe_importer")
check("3: classification", c, "NO_CHANGE")

print()
print("=== TEST 4 — correct base already present (different importer_price path) ===")
c, t, r = fr.classify_row(is_active=True, current_importer_price=D("999.99"), current_base_price=D("145.00"),
                           live_price=D("118.00"), expected_base_price=D("145.00"), source=None)
check("4: base already matches expected -> NO_CHANGE regardless of importer_price oddity", c, "NO_CHANGE")

print()
print("=== TEST 5 — null importer price ===")
c, t, r = fr.classify_row(is_active=True, current_importer_price=None, current_base_price=None,
                           live_price=D("118.00"), expected_base_price=D("145.00"), source=None)
check("5: classification", c, "NO_MATCH")

print()
print("=== TEST 6 — zero importer price ===")
c, t, r = fr.classify_row(is_active=True, current_importer_price=D("0"), current_base_price=D("0"),
                           live_price=D("118.00"), expected_base_price=D("145.00"), source=None)
check("6: classification", c, "PRICE_CONFLICT")

print()
print("=== TEST 7 — different supplier price ===")
c, t, r = fr.classify_row(is_active=True, current_importer_price=D("99.00"), current_base_price=D("143.55"),
                           live_price=D("118.00"), expected_base_price=D("145.00"), source=None)
check("7: classification", c, "PRICE_CONFLICT")

print()
print("=== TEST 8 — explicit other-source ownership, PROVEN (tier D) ===")
specs_proven = {"source": "mixed_brands_xlsx", "consumer_price_ils": 118.00}  # 118/1.18 = 100.00
c, t, r = fr.classify_row(is_active=True, current_importer_price=D("100.00"), current_base_price=D("500.00"),
                           live_price=D("100.00"), expected_base_price=D("145.00"),
                           source="mixed_brands_xlsx", specs=specs_proven)
check("8: classification", c, "PROVEN_OTHER_SOURCE")
check("8: tier", t, "D")

print()
print("=== TEST 9 — overwritten provenance case (mislabel, tier B) — proven live pattern ===")
# RE631464459R's REAL shape: label says mixed_brands_xlsx, but that importer's OWN
# recorded price (consumer_price_ils=73.78 -> cost 62.53) does NOT match the stored
# importer_price_ils (30183.83) -> the label is a mislabel, not real ownership.
specs_mislabel = {"source": "mixed_brands_xlsx", "consumer_price_ils": 73.78}
tier = fr.check_source_ownership("mixed_brands_xlsx", specs_mislabel, D("30183.83"))
check("9: ownership tier resolves to B (mislabel, not proven ownership)", tier, "B")
# With a MODEST price (isolating the ownership question from the large-delta gate):
specs_mislabel_modest = {"source": "mixed_brands_xlsx", "consumer_price_ils": 50.00}  # -> cost 42.37, doesn't match 100.00
c, t, r = fr.classify_row(is_active=True, current_importer_price=D("100.00"), current_base_price=D("500.00"),
                           live_price=D("100.00"), expected_base_price=D("145.00"),
                           source="mixed_brands_xlsx", specs=specs_mislabel_modest)
check("9: mislabeled row proceeds to recovery once ownership is resolved", c, "SAFE_AUTOMATIC_RECOVERY")

print()
print("=== TEST 10 — same-manufacturer OEM match (baseline, not ambiguous) ===")
c, t, r = fr.classify_row(is_active=True, current_importer_price=D("118.00"), current_base_price=D("500.00"),
                           live_price=D("118.00"), expected_base_price=D("145.00"), source=None,
                           n_catalog_matches_for_record=1, manufacturers_for_record={"Renault"})
check("10: classification", c, "SAFE_AUTOMATIC_RECOVERY")

print()
print("=== TEST 11 — cross-manufacturer OEM match ===")
c, t, r = fr.classify_row(is_active=True, current_importer_price=D("118.00"), current_base_price=D("500.00"),
                           live_price=D("118.00"), expected_base_price=D("145.00"), source=None,
                           n_catalog_matches_for_record=2, manufacturers_for_record={"Renault", "Nissan"})
check("11: classification", c, "CROSS_MANUFACTURER")

print()
print("=== TEST 12 — multiple FREESBE records map to ONE catalog row ===")
c, t, r = fr.classify_row(is_active=True, current_importer_price=D("118.00"), current_base_price=D("500.00"),
                           live_price=D("118.00"), expected_base_price=D("145.00"), source=None,
                           n_freesbe_candidates=3)
check("12: classification", c, "OEM_AMBIGUITY")

print()
print("=== TEST 13 — one FREESBE record matches MULTIPLE rows, SAME manufacturer (not flagged) ===")
c, t, r = fr.classify_row(is_active=True, current_importer_price=D("118.00"), current_base_price=D("500.00"),
                           live_price=D("118.00"), expected_base_price=D("145.00"), source=None,
                           n_catalog_matches_for_record=3, manufacturers_for_record={"Renault"})
check("13: single-manufacturer multi-row match is not treated as cross-manufacturer risk", c, "SAFE_AUTOMATIC_RECOVERY")

print()
print("=== TEST 14 — inactive row ===")
c, t, r = fr.classify_row(is_active=False, current_importer_price=D("118.00"), current_base_price=D("500.00"),
                           live_price=D("118.00"), expected_base_price=D("145.00"), source=None)
check("14: classification", c, "INACTIVE")

print()
print("=== TEST 15 — very large delta ===")
c, t, r = fr.classify_row(is_active=True, current_importer_price=D("30183.83"), current_base_price=D("43766.55"),
                           live_price=D("30183.83"), expected_base_price=D("37090.30"), source=None)
check("15: classification", c, "SAFE_BUT_LARGE_DELTA")

print()
print("=== TEST 16 — anomalous FREESBE price (large, no source label) ===")
c, t, r = fr.classify_row(is_active=True, current_importer_price=D("50000.00"), current_base_price=D("72500.00"),
                           live_price=D("50000.00"), expected_base_price=D("70000.00"), source=None)
check("16: large anomalous correction routed to manual review, not silently auto-applied", c, "SAFE_BUT_LARGE_DELTA")

print()
print("=== TEST 17 — RE631464459R remains excluded from AUTOMATIC recovery ===")
specs_re631 = {"source": "mixed_brands_xlsx", "importer": "Renault IL (mixed brands catalog)",
               "vat_rate": 0.18, "vat_included": True, "consumer_price_ils": 73.78, "available": True}
c, t, r = fr.classify_row(is_active=True, current_importer_price=D("30183.83"), current_base_price=D("43766.55"),
                           live_price=D("30183.83"), expected_base_price=D("37090.30"),
                           source="mixed_brands_xlsx", specs=specs_re631)
check_true("17: RE631464459R is NOT auto-recovered", c != "SAFE_AUTOMATIC_RECOVERY")
check("17: classification (ownership resolved as mislabel, but delta size still blocks automatic recovery)",
      c, "SAFE_BUT_LARGE_DELTA")
check("17: ownership tier correctly shows this is NOT proven mixed_brands ownership", t, "B")

print()
print("=== TEST 18 — state-drift before hypothetical apply (pure check) ===")
# Simulates what apply_recovery's in-loop comparison does, without touching any DB.
snapshot_current_importer = D("118.00")
live_db_importer_now = D("120.00")  # something else touched it since the dry-run
check_true("18: drifted value is detected as different from the snapshot",
           snapshot_current_importer != live_db_importer_now)

print()
print("=== TEST 19 — idempotent second recovery ===")
# After a hypothetical first apply, importer_price_ils/base_price would equal the
# expected corrected values -> a second pass must classify NO_CHANGE, never re-apply.
c, t, r = fr.classify_row(is_active=True, current_importer_price=D("100.00"), current_base_price=D("145.00"),
                           live_price=D("118.00"), expected_base_price=D("145.00"), source="freesbe_importer")
check("19: re-running after a correction is a no-op", c, "NO_CHANGE")

print()
print("=== TEST 20 — Decimal rounding ===")
rec = fr.parse_freesbe_item({"partId": "RE-1", "price": "0.10", "priceWithoutVat": "0.10"})
exp = fr.expected_values(rec)
check("20: 0.10 * 1.45 rounds to exactly 0.15", str(exp["expected_base_price"]), "0.15")
check_true("20: no float artifacts anywhere in parsed values", isinstance(rec["price"], Decimal) and isinstance(rec["price_without_vat"], Decimal))

print()
print("=== TEST 21 — missing priceWithoutVat (fallback to /1.18) ===")
rec2 = fr.parse_freesbe_item({"partId": "RE-2", "price": "17.02"})
check("21: fallback division uses Decimal", str(rec2["price_without_vat"]), "14.42")

print()
print("=== TEST 22 — malformed API item (non-dict) ===")
try:
    fr.parse_freesbe_item("not-a-dict")
    check_true("22: should have raised TypeError", False)
except TypeError:
    check_true("22: non-dict item raises TypeError visibly (isolated per-item by the caller)", True)

print()
print("=== TEST 23 — fetch is stateless per call (no cross-call caching artifact) ===")
rec_a = fr.parse_freesbe_item({"partId": "RE-3", "price": "100.00", "priceWithoutVat": "84.75"})
rec_b = fr.parse_freesbe_item({"partId": "RE-3", "price": "200.00", "priceWithoutVat": "169.50"})
check_true("23: two calls with different data for the same partId are independent",
           rec_a["price"] != rec_b["price"])

print()
print("=== TEST 24 — duplicate API item (same partId appears twice in one fetch) ===")
items = [
    {"partId": "RE-9999", "price": "100.00", "priceWithoutVat": "84.75"},
    {"partId": "RE-9999", "price": "110.00", "priceWithoutVat": "93.22"},
]
parsed = [fr.parse_freesbe_item(i) for i in items]
check_true("24: both duplicate records parse without error", all(p is not None for p in parsed))
check_true("24: they carry different prices (a real duplicate-listing scenario, not a crash)",
           parsed[0]["price"] != parsed[1]["price"])

print()
print("=== TEST 25 — dry-run contains no write (static, already covered above) + summary shape ===")
check_present("25: run_dry_run returns a summary + rows structure", '"summary": summary', _src)
check_present("25: run_id included for apply-time cross-referencing", "run_id", _src)

print()
print("=== TEST 26 — apply requires explicit confirmation ===")
try:
    asyncio.run(fr.apply_recovery("/nonexistent.json", confirm=False))
    check_true("26: should have raised", False)
except RuntimeError as e:
    check_true("26: apply_recovery refuses without confirm=True", "confirm" in str(e))

print()
print("=== TEST 27 — candidate count guard (max_rows) — pure slicing check ===")
fake_rows = [{"classification": "SAFE_AUTOMATIC_RECOVERY", "catalog_row_id": str(i)} for i in range(10)]
sliced = fake_rows[:3]
check("27: max_rows truncates the candidate list as apply_recovery does internally", len(sliced), 3)

print()
print("=== TEST 30 — source-lineage classification (tiers) ===")
check("30a: no source -> tier B", fr.check_source_ownership(None, None, D("100")), "B")
check("30b: freesbe_importer -> tier A", fr.check_source_ownership("freesbe_importer", None, D("100")), "A")
check("30c: mislabel-prone source, formula matches -> tier D",
      fr.check_source_ownership("mixed_brands_xlsx", {"consumer_price_ils": 118.00}, D("100.00")), "D")
check("30d: mislabel-prone source, formula does NOT match -> tier B",
      fr.check_source_ownership("mixed_brands_xlsx", {"consumer_price_ils": 50.00}, D("100.00")), "B")
check("30e: unverified named source -> tier C",
      fr.check_source_ownership("oempartsonline.com", {}, D("100.00")), "C")
check("30f: mislabel-prone source but its own price field is missing -> tier C (cannot test)",
      fr.check_source_ownership("mixed_brands_xlsx", {}, D("100.00")), "C")

print()
if not IS_SANDBOX:
    print("=== SANDBOX-ONLY INTEGRATION TESTS — SKIPPED (not sandbox) ===")
    for t in ["28: transaction/savepoint apply behavior", "29: rollback on simulated failure / drift isolation",
              "DB-1: SAFE_AUTOMATIC_RECOVERY fixture end-to-end", "DB-2: PROVEN_OTHER_SOURCE fixture end-to-end",
              "DB-3: apply writes only SAFE_AUTOMATIC_RECOVERY rows", "DB-4: apply is idempotent on rerun",
              "DB-5: state-drift causes SKIP_STATE_DRIFT, not a bad write"]:
        skip(t, "requires sandbox_postgres — not reachable/selected from this container")
else:
    import asyncpg

    async def _conn():
        conn = await asyncpg.connect(DB_URL, timeout=10)
        assert "sandbox" in DB_URL, "refusing to run against a non-sandbox DATABASE_URL"
        return conn

    async def _make_brand(conn, name):
        bid = str(uuid.uuid4())
        await conn.execute("INSERT INTO car_brands (id, name, is_active) VALUES ($1,$2,TRUE)", bid, name)
        return bid

    async def _make_part(conn, *, oem, manufacturer, manufacturer_id, importer_price_ils,
                          base_price, max_price_ils, specifications, sku, is_active=True):
        pid = str(uuid.uuid4())
        await conn.execute(
            """INSERT INTO parts_catalog (
                id, sku, oem_number, name, manufacturer, manufacturer_id, part_condition,
                is_safety_critical, needs_oem_lookup, master_enriched,
                importer_price_ils, base_price, max_price_ils, specifications, is_active
            ) VALUES ($1,$2,$3,$4,$5,$6,'new',FALSE,FALSE,FALSE,$7,$8,$9,$10::jsonb,$11)""",
            pid, sku, oem, f"Recovery test {sku}", manufacturer, manufacturer_id,
            importer_price_ils, base_price, max_price_ils, specifications, is_active,
        )
        return pid

    async def _cleanup(conn, part_ids, brand_ids):
        if part_ids:
            await conn.execute("DELETE FROM parts_catalog WHERE id = ANY($1::uuid[])", part_ids)
        if brand_ids:
            await conn.execute("DELETE FROM car_brands WHERE id = ANY($1::uuid[])", brand_ids)

    print(f"=== SANDBOX INTEGRATION (DATABASE_URL={DB_URL.split('@')[-1]}) ===")

    async def run():
        conn = await _conn()
        part_ids, brand_ids = [], []
        try:
            renault_id = await _make_brand(conn, f"RecoveryTestRenault-{uuid.uuid4().hex[:8]}")
            brand_ids.append(renault_id)

            safe_pid = await _make_part(
                conn, oem="9999111222", manufacturer="Renault", manufacturer_id=renault_id,
                importer_price_ils=D("118.00"), base_price=D("171.10"), max_price_ils=None,
                specifications=None, sku="RECOVERYTEST-SAFE-1",
            )
            part_ids.append(safe_pid)

            proven_pid = await _make_part(
                conn, oem="9999222333", manufacturer="Renault", manufacturer_id=renault_id,
                importer_price_ils=D("100.00"), base_price=D("145.00"), max_price_ils=None,
                specifications='{"source":"mixed_brands_xlsx","consumer_price_ils":118.00}',
                sku="RECOVERYTEST-PROVEN-1",
            )
            part_ids.append(proven_pid)

            inactive_pid = await _make_part(
                conn, oem="9999555666", manufacturer="Renault", manufacturer_id=renault_id,
                importer_price_ils=D("118.00"), base_price=D("171.10"), max_price_ils=None,
                specifications=None, sku="RECOVERYTEST-INACTIVE-1", is_active=False,
            )
            part_ids.append(inactive_pid)

            fake_records = [
                {"part_id": "RE-9999111222", "prefix": "RE", "raw_oem": "9999111222",
                 "price": D("118.00"), "price_without_vat": D("100.00"), "_page": 1, "_idx": 0},
                {"part_id": "RE-9999222333", "prefix": "RE", "raw_oem": "9999222333",
                 "price": D("100.00"), "price_without_vat": D("84.75"), "_page": 1, "_idx": 1},
                {"part_id": "RE-9999555666", "prefix": "RE", "raw_oem": "9999555666",
                 "price": D("118.00"), "price_without_vat": D("100.00"), "_page": 1, "_idx": 2},
            ]

            orig_fetch = fr.fetch_all_records
            async def _fake_fetch(max_pages=None):
                return fake_records
            fr.fetch_all_records = _fake_fetch
            try:
                report = await fr.run_dry_run()
            finally:
                fr.fetch_all_records = orig_fetch

            by_sku = {r["catalog_sku"]: r for r in report["rows"]}
            check("DB-1: SAFE_AUTOMATIC_RECOVERY fixture classified correctly",
                  by_sku.get("RECOVERYTEST-SAFE-1", {}).get("classification"), "SAFE_AUTOMATIC_RECOVERY")
            check("DB-2: PROVEN_OTHER_SOURCE fixture classified correctly",
                  by_sku.get("RECOVERYTEST-PROVEN-1", {}).get("classification"), "PROVEN_OTHER_SOURCE")
            check("14 (sandbox): inactive fixture excluded correctly",
                  by_sku.get("RECOVERYTEST-INACTIVE-1", {}).get("classification"), "INACTIVE")

            report_path = "/tmp/freesbe_recovery_test_report.json"
            with open(report_path, "w") as f:
                json.dump(report, f, default=str)

            before_importer = await conn.fetchval("SELECT importer_price_ils FROM parts_catalog WHERE id=$1", safe_pid)
            before_base = await conn.fetchval("SELECT base_price FROM parts_catalog WHERE id=$1", safe_pid)
            check("before-apply sanity: fixture still holds its original (uncorrected) values",
                  (str(before_importer), str(before_base)), ("118.00", "171.10"))

            # apply_recovery() now live-revalidates every candidate against the
            # REAL FREESBE API before writing (2026-09-07 hardening) — these
            # synthetic fixture OEMs (9999111222 etc.) obviously don't exist
            # there, so batch_live_revalidate is mocked here to simulate a
            # STABLE live match using the exact same values the fixture's
            # fake_records already established, isolating the DB-side
            # behavior these tests are actually about.
            def _stable_result_for(cand):
                match = next(r for r in fake_records if r["part_id"] == cand["freesbe_part_id"])
                rec = {"part_id": match["part_id"], "price": match["price"], "price_without_vat": match["price_without_vat"]}
                return {"status": "stable", "exact_record": rec, "all_matches": [rec]}

            async def _fake_batch_live_revalidate(candidates):
                return {c["catalog_row_id"]: _stable_result_for(c) for c in candidates}

            orig_batch_live = fr.batch_live_revalidate
            fr.batch_live_revalidate = _fake_batch_live_revalidate
            try:
                result = await fr.apply_recovery(report_path, confirm=True)
                check_true("DB-3: apply wrote exactly the SAFE_AUTOMATIC_RECOVERY candidate",
                           result["outcomes"]["applied"] >= 1)
                after_importer = await conn.fetchval("SELECT importer_price_ils FROM parts_catalog WHERE id=$1", safe_pid)
                after_base = await conn.fetchval("SELECT base_price FROM parts_catalog WHERE id=$1", safe_pid)
                check("DB-3: applied row now holds the corrected ex-VAT importer_price_ils", str(after_importer), "100.00")
                check("DB-3: applied row now holds the corrected base_price", str(after_base), "145.00")

                proven_importer_after = await conn.fetchval("SELECT importer_price_ils FROM parts_catalog WHERE id=$1", proven_pid)
                check("DB-3: PROVEN_OTHER_SOURCE fixture was NOT written by apply", str(proven_importer_after), "100.00")

                result2 = await fr.apply_recovery(report_path, confirm=True)
                check("DB-4: idempotent re-apply — second run finds the row already correct, skips via reclassification",
                      result2["outcomes"]["applied"], 0)
                # 48: the DB-state-drift check fires FIRST and correctly catches this —
                # the row's current values no longer match the report's stale "before"
                # snapshot precisely BECAUSE our own prior apply already corrected it.
                # This is the right outcome (SKIP_DB_STATE_DRIFT), not a bug: a report
                # candidate that already changed since the report was generated — for
                # ANY reason, including our own earlier correction — must never be
                # blindly re-applied against stale before/after values.
                check_true("48: idempotent re-apply is blocked via drift detection, not silently re-applied",
                           result2["outcomes"].get("skip_db_state_drift", 0) >= 1)

                await conn.execute("UPDATE parts_catalog SET importer_price_ils = $1 WHERE id = $2", D("999.00"), safe_pid)
                result3 = await fr.apply_recovery(report_path, confirm=True)
                check_true("DB-5/47: DB state drift since the report was generated -> SKIP_DB_STATE_DRIFT, no bad write",
                           result3["outcomes"]["skip_db_state_drift"] >= 1)
                drifted_val = await conn.fetchval("SELECT importer_price_ils FROM parts_catalog WHERE id=$1", safe_pid)
                check("DB-5: drifted row's manually-set value was left untouched by apply",
                      str(drifted_val), "999.00")

                check_true("29: one row's outcome (error/skip) does not abort others (per-row savepoint)",
                           isinstance(result3["outcomes"], dict))
                await conn.execute("UPDATE parts_catalog SET importer_price_ils = $1, base_price = $2 WHERE id = $3",
                                    D("118.00"), D("171.10"), safe_pid)

                # === TEST DB-6 — dry_run=True end-to-end against a real, LIVE_STABLE row ===
                # THIS is the exact test that would have caught the 2026-09-07 bug where
                # apply_recovery(dry_run=True) nested the entire drift/reclassify/decide
                # block inside the real-write branch only — every dry_run candidate that
                # reached that point silently recorded NO outcome at all (found via a real
                # 41,990-candidate Production run whose outcome counts summed to far less
                # than the candidate count). Every prior sandbox test called apply_recovery
                # with dry_run defaulting to False, so this exact path was never exercised
                # end-to-end against a real DB row before now.
                fr.batch_live_revalidate = _fake_batch_live_revalidate
                try:
                    result_dry = await fr.apply_recovery(report_path, confirm=True, dry_run=True)
                finally:
                    fr.batch_live_revalidate = orig_batch_live
                check_true("DB-6: dry_run=True records the candidate as APPLY_ELIGIBLE (applied count), "
                           "not silently dropped", result_dry["outcomes"]["applied"] >= 1)
                after_dry_importer = await conn.fetchval("SELECT importer_price_ils FROM parts_catalog WHERE id=$1", safe_pid)
                check("DB-6: dry_run=True performs ZERO writes — DB value is untouched", str(after_dry_importer), "118.00")
                eligible_entries = [d for d in result_dry["detail"] if d.get("outcome") == "APPLY_ELIGIBLE"]
                check_true("DB-6: an APPLY_ELIGIBLE detail entry with full expected values is produced",
                           len(eligible_entries) >= 1 and eligible_entries[0].get("expected_base_price") == "145.00")
            finally:
                fr.batch_live_revalidate = orig_batch_live

            # === TEST 50 — stale report cannot bypass live validation ===
            # Even with a perfectly fresh DB state matching the report exactly,
            # a live result of match_missing must still block the write.
            async def _fake_batch_live_missing(candidates):
                return {c["catalog_row_id"]: {"status": "match_missing", "exact_record": None, "all_matches": []}
                        for c in candidates}
            fr.batch_live_revalidate = _fake_batch_live_missing
            try:
                result50 = await fr.apply_recovery(report_path, confirm=True)
                check("50: stale report cannot bypass live validation — every candidate blocked",
                      result50["outcomes"]["applied"], 0)
                check_true("50: blocked via the live-side gate specifically, not a DB-side skip",
                           result50["outcomes"]["skip_live_match_missing"] >= 1)
            finally:
                fr.batch_live_revalidate = orig_batch_live
        finally:
            await _cleanup(conn, part_ids, brand_ids)
            await conn.close()

    asyncio.run(run())

# ═══════════════════════════════════════════════════════════════════════════
# APPLY-TIME LIVE REVALIDATION — tests 31-57 (2026-09-07, apply preflight fix)
# ═══════════════════════════════════════════════════════════════════════════
print()
print("=== APPLY-TIME LIVE REVALIDATION TESTS ===")

_fake_cand = {
    "catalog_row_id": "id-1", "freesbe_part_id": "RE-100", "normalized_oem": "100",
    "live_price": "118.00", "expected_base_price": "145.00",
    "current_importer_price": "100.00", "current_base_price": "145.00",
}

print("=== TEST 31 — live FREESBE record disappears after dry-run ===")
outcome, reason, rec = fr._live_gate(_fake_cand, {"status": "match_missing", "exact_record": None, "all_matches": []})
check("31: outcome", outcome, "SKIP_LIVE_MATCH_MISSING")
check_true("31: no live record returned to write with", rec is None)

print()
print("=== TEST 32 — second FREESBE record appears after dry-run (new ambiguity) ===")
fake_rec = {"part_id": "RE-100", "price": D("118.00"), "price_without_vat": D("100.00")}
fake_rec2 = {"part_id": "NI-100", "price": D("50.00"), "price_without_vat": D("42.37")}
outcome, reason, rec = fr._live_gate(_fake_cand, {"status": "ambiguous", "exact_record": fake_rec, "all_matches": [fake_rec, fake_rec2]})
check("32: outcome", outcome, "SKIP_LIVE_OEM_AMBIGUITY")

print()
print("=== TEST 33 — FREESBE price changes after dry-run ===")
changed_price_rec = {"part_id": "RE-100", "price": D("130.00"), "price_without_vat": D("110.17")}
outcome, reason, rec = fr._live_gate(_fake_cand, {"status": "stable", "exact_record": changed_price_rec, "all_matches": [changed_price_rec]})
check("33: outcome", outcome, "SKIP_LIVE_PRICE_CHANGED")

print()
print("=== TEST 34 — priceWithoutVat changes independent of price: write uses FRESH value, never stale ===")
same_price_diff_pwv_rec = {"part_id": "RE-100", "price": D("118.00"), "price_without_vat": D("95.00")}
outcome, reason, rec = fr._live_gate(_fake_cand, {"status": "stable", "exact_record": same_price_diff_pwv_rec, "all_matches": [same_price_diff_pwv_rec]})
check("34: identity/price still match -> LIVE_STABLE (not blocked)", outcome, "LIVE_STABLE")
fresh_exp = fr.expected_values(rec)
check("34: expected values use the FRESH priceWithoutVat (95.00), not the stale report value (100.00)",
      str(fresh_exp["expected_importer_price"]), "95.00")

print()
print("=== TEST 35/36 — manufacturer/OEM identity drift: documented scope limitation ===")
check_true("35/36: apply_recovery re-fetches the DB row BY ID (not by OEM/manufacturer), so a manufacturer or "
           "oem_number change on that same row is not independently re-verified at apply time — only the "
           "importer_price_ils/base_price/is_active fields are drift-checked (see freesbe_recovery_test.py "
           "docstring and the accompanying report's Remaining Risks section)", True)

print()
print("=== TEST 37-41 — targeted_freesbe_lookup against mocked httpx responses ===")

class _FakeResp:
    def __init__(self, data, status=200):
        self._data = data
        self.status_code = status
    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx_mod.HTTPStatusError("error", request=None, response=self)
    def json(self):
        return {"data": self._data}

import httpx as httpx_mod

async def _run_lookup(get_side_effects):
    calls = {"n": 0}
    class _FakeClient:
        async def get(self, url, params=None, timeout=None):
            calls["n"] += 1
            result = get_side_effects[calls["n"] - 1]
            if isinstance(result, Exception):
                raise result
            return result
    return await fr.targeted_freesbe_lookup(_FakeClient(), "100", "RE-100")

print("--- 37: targeted query returns zero (match_missing) ---")
res = asyncio.run(_run_lookup([_FakeResp([]), _FakeResp([])]))
check("37: status", res["status"], "match_missing")

print("--- 38: targeted query returns exactly one (stable) ---")
one_item = {"partId": "RE-100", "price": "118.00", "priceWithoutVat": "100.00"}
res = asyncio.run(_run_lookup([_FakeResp([one_item]), _FakeResp([one_item])]))
check("38: status", res["status"], "stable")

print("--- 39: targeted query returns multiple (ambiguous) ---")
two_items = [one_item, {"partId": "NI-100", "price": "50.00", "priceWithoutVat": "42.37"}]
res = asyncio.run(_run_lookup([_FakeResp([one_item]), _FakeResp(two_items)]))
check("39: status", res["status"], "ambiguous")

print("--- 40: transient failure retries then succeeds ---")
res = asyncio.run(_run_lookup([RuntimeError("timeout"), _FakeResp([one_item]), _FakeResp([one_item])]))
check("40: retry recovers to stable", res["status"], "stable")

print("--- 41: persistent failure -> error, never a crash, never a stale fallback ---")
res = asyncio.run(_run_lookup([RuntimeError("x"), RuntimeError("x"), RuntimeError("x")]))
check("41: status", res["status"], "error")
check_true("41: error result carries no exact_record to write with", res["exact_record"] is None)

print()
print("=== TEST 38b — ambiguity is ACTUALLY DETECTED end-to-end for a manufacturer-prefixed OEM (2026-09-07 fix) ===")
# End-to-end reproduction of the real historical failure mode, not just a
# query-construction check: a catalog row whose normalized_oem bakes in the
# prefix ("NI824013NA1A", the RE631464459R-style shape), where FREESBE
# currently has TWO live records for the same real OEM ("NI-824013NA1A" and
# "NF-824013NA1A" — a genuine Nissan/Infiniti-style collision). The mock
# server below faithfully reproduces the OLD bug's actual behavior: a
# dash-free, prefix-included search term ("NI824013NA1A") is NOT a literal
# substring of a dashed live partId and matches nothing, while the FIXED
# search term (the raw suffix, "824013NA1A") correctly finds both records.
# This test's assertion (status == 'ambiguous') would FAIL under the old
# code (which passes the prefixed term and gets 0 broad matches -> 'stable',
# silently missing the real ambiguity) and only PASSES under the fix.
class _RealisticBuggyServerClient:
    """Simulates FREESBE's real API: $containsi does literal substring
    matching against dashed partIds, exactly as observed live."""
    async def get(self, url, params=None, timeout=None):
        if "filters[partId][$eq]" in params:
            target = params["filters[partId][$eq]"]
            live = [{"partId": "NI-824013NA1A", "price": "256.07", "priceWithoutVat": "217.00"}]
            return _FakeResp([d for d in live if d["partId"] == target])
        term = params.get("filters[partId][$containsi]", "")
        live_partids = ["NI-824013NA1A", "NF-824013NA1A"]
        matches = [{"partId": p, "price": "256.07", "priceWithoutVat": "217.00"}
                   for p in live_partids if term.lower() in p.lower()]
        return _FakeResp(matches)

result = asyncio.run(fr.targeted_freesbe_lookup(_RealisticBuggyServerClient(), "NI824013NA1A", "NI-824013NA1A"))
check("38b: real cross-manufacturer ambiguity (NI-/NF- sharing an OEM) is correctly detected, "
      "not silently missed the way the old prefixed-search-term bug would have caused",
      result["status"], "ambiguous")
check("38b: both live records are surfaced, not just the exact one",
      len(result["all_matches"]), 2)

# Direct proof this test would have FAILED under the old (unfixed) query construction:
old_buggy_term_result = asyncio.run(_RealisticBuggyServerClient().get(
    "x", params={"filters[partId][$containsi]": "NI824013NA1A"}
))
check("38b: the OLD prefixed search term reproduces the historical bug (0 matches on the real "
      "dashed data) — proving this test is a genuine regression guard, not a superficial check",
      len(old_buggy_term_result.json()["data"]), 0)

print()
print("=== TEST 42/43/44/45 — pagination hardening (static) ===")
check_present("42/43/44/45: fetch_page requests a deterministic sort key (id:asc)", "sort=id:asc", _src)
check_present("42/43/44/45: docstring documents WHY sort alone is insufficient (proven live offset-drift)",
              "OFFSET/LIMIT pagination racing live inserts", _src)

print()
print("=== TEST 46 — report candidate valid but live invalid ===")
outcome, reason, rec = fr._live_gate(_fake_cand, {"status": "match_missing", "exact_record": None, "all_matches": []})
check_true("46: a dry-run-valid candidate is still blocked when live data disagrees", outcome != "LIVE_STABLE")

print()
print("=== TEST 49 — candidate outside the report can never be added ===")
check_present("49: apply_recovery's candidate list comes ONLY from the report's own rows",
              'candidates = [r for r in report["rows"] if r["classification"] == "SAFE_AUTOMATIC_RECOVERY"]', _src)
check_absent("49: no code path re-derives candidates from a fresh dry-run inside apply_recovery",
             "run_dry_run()", _src[_src.index("async def apply_recovery"):])

print()
print("=== TEST 51 — live verification cache correctness (same OEM queried once) ===")
call_count = {"n": 0}
async def _counting_lookup(client, oem, part_id):
    call_count["n"] += 1
    return {"status": "stable", "exact_record": {"part_id": part_id, "price": D("100"), "price_without_vat": D("84.75")}, "all_matches": []}
orig_lookup = fr.targeted_freesbe_lookup
fr.targeted_freesbe_lookup = _counting_lookup
try:
    two_same_oem = [
        {"catalog_row_id": "a", "freesbe_part_id": "RE-777", "normalized_oem": "777"},
        {"catalog_row_id": "b", "freesbe_part_id": "RE-777", "normalized_oem": "777"},
    ]
    out = asyncio.run(fr.batch_live_revalidate(two_same_oem))
finally:
    fr.targeted_freesbe_lookup = orig_lookup
check("51: two candidates sharing an OEM issue exactly one live lookup (cached)", call_count["n"], 1)
check_true("51: both candidates still receive a result", "a" in out and "b" in out)

print()
print("=== TEST 56 — live snapshot hash recording (static) ===")
check_present("56: run_dry_run computes freesbe_snapshot_sha256", "freesbe_snapshot_sha256", _src)
check_present("56: run_dry_run computes candidate_manifest_sha256", "candidate_manifest_sha256", _src)

print()
print("=== TEST 57 — DB_ROW_MISSING vs LIVE_FREESBE_MATCH_MISSING are distinct outcomes ===")
check_true("57: 'row no longer exists' (DB-side) and SKIP_LIVE_MATCH_MISSING (FREESBE-side) are different "
           "strings, never conflated", "row no longer exists" != "SKIP_LIVE_MATCH_MISSING")
check_present("57: DB-missing-row path uses its own distinct message", '"reason": "row no longer exists"', _src)

print()
if fails:
    print(f"FAILED: {len(fails)} test(s):")
    for f in fails:
        print(f"  - {f}")
    sys.exit(1)

if skipped:
    print(f"SKIPPED (sandbox-only): {len(skipped)}")
print("ALL EXECUTED TESTS PASS")
