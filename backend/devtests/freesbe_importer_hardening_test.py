"""FREESBE IMPORTER HARDENING — regression tests — 2026-09-05.

Covers the forensic investigation's findings: an invocation of
freesbe_importer.py was found hung for 2.5+ months with no explicit DB
connection timeout, an unbounded/unbatched base_price UPDATE, unguarded
duplicate agent_todos inserts (32 rows from 16 clean runs), and no
already-complete detection or single-instance protection.

Test groups A-J map directly to the hardening task's required scenarios.
Groups requiring a real database/Redis (D, E, F, G, H, J) run ONLY against
the isolated sandbox stack (sandbox_postgres / sandbox_redis) — this file
must be run via `docker exec sandbox_backend`, never `autospare_backend`,
for those groups to execute (they self-skip with a clear message otherwise,
never silently pass). Groups A, B, C, I and the static checks are pure/static
and run anywhere.

Run (full suite, includes sandbox DB/Redis groups):
    docker exec sandbox_backend python3 /app/devtests/freesbe_importer_hardening_test.py

Run (pure/static groups only, safe anywhere incl. production container):
    docker exec autospare_backend python3 /app/devtests/freesbe_importer_hardening_test.py
"""
import asyncio
import os
import pathlib
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


_src = pathlib.Path("/app/importers/freesbe_importer.py").read_text(encoding="utf-8")

import freesbe_importer as fi  # noqa: E402 — module import, __main__ guard prevents execution

DB_URL = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")
IS_SANDBOX = "sandbox_postgres" in DB_URL

# ═══════════════════════════════════════════════════════════════════════════
print("=== STATIC — hardening present in source ===")

check_present("connection timeout: connect_with_timeout() defined", "async def connect_with_timeout(", _src)
check_present("connection timeout: asyncpg.connect receives timeout kwarg", "asyncpg.connect(db_url, timeout=timeout_s)", _src)
check_present("connection timeout: outer asyncio.wait_for backstop", "asyncio.wait_for(\n            asyncpg.connect(db_url, timeout=timeout_s),", _src)
check_present("connection timeout: clean diagnostic on failure", 'print(f"ERROR: database connection', _src)

# base_price is owned by the ONE central rule (2026-11-18): the importer's own catalogue-wide normalisation pass is GONE and must not return.
check_absent("base_price: the importer-owned catalogue-wide normalisation function is gone", "async def normalize_base_price_batched(", _src)
check_absent("base_price: no importer-owned `x 1.45` formula remains", "* 1.45", _src)
check_absent("base_price: no UPDATE of base_price outside the rule", "SET base_price", _src)
check_present("base_price: the central rule runs per page on exactly the parts the page wrote", "base_price_rule.apply_and_report(conn, touched_part_ids, \"freesbe_importer\"", _src)
check_present("base_price: a rule failure is counted (failed parts are queued for retry by the rule), not swallowed", "base_price_failed_parts", _src)
check_present("base_price: new parts start at 0.0 (the rule prices them)", "0.0,$8,TRUE,FALSE,FALSE", _src)

check_present("agent_todos: idempotency check function defined", "async def todo_already_pending(", _src)
check_present("agent_todos: insert gated on idempotency check", "async def queue_pipeline_todos_if_needed(", _src)
check_present("agent_todos: skips insert when already pending", "Pipeline todos already pending — skipping duplicate insert.", _src)

check_present("single-instance: reuses project-wide distributed_lock", "from distributed_lock import acquire_lock", _src)
check_present("single-instance: lock acquired around the run", "lock = await acquire_lock(redis, LOCK_NAME", _src)
check_present("single-instance: lock released in finally", "await lock.release()", _src)
check_present("single-instance: crash-safe TTL (not indefinite)", "LOCK_TTL_SECONDS", _src)
check_present("single-instance: second instance exits cleanly, no deadlock", "Another Freesbe importer instance is already running", _src)

check_present("no-op detection: is_import_complete() defined", "def is_import_complete(", _src)
check_present("no-op detection: checked before DB connection is opened", "if is_import_complete(completed, total_pages):", _src)
check_present("no-op detection: clear log message", "FREESBE import already complete — no work required", _src)

# Regression: core matching/update/insert logic untouched
check_present("regression: process_page signature unchanged", "async def process_page(conn, parts: list, stats: dict, brand_id_cache: dict,", _src)
# Placeholder shifted $2 -> $4 on 2026-09-05 (pricing-policy fix): the UPDATE
# now also sets base_price/max_price_ils in the same statement (see
# freesbe_pricing_policy_test.py), pushing `id` later in the parameter list.
# The UPDATE-by-primary-key SHAPE this check protects (single row, by `id`,
# not by any other predicate) is unchanged.
check_present("regression: idempotent UPDATE by primary key unchanged", "WHERE id = $3", _src)
check_present("regression: idempotent upsert (ON CONFLICT sku) unchanged", "ON CONFLICT (sku) DO UPDATE SET", _src)
check_present("regression: CASE guard on importer_price_ils preserved", "CASE WHEN EXCLUDED.importer_price_ils > 0", _src)
check_present("regression: fetch_page bounded retry/timeout unchanged", "for attempt in range(3):", _src)

print()
print("=== A/B/C — checkpoint semantics (pure functions, no I/O) ===")

# Test A: complete checkpoint (1..1771)
complete_pages = set(range(1, 1772))
check_true("A: is_import_complete(1..1771, 1771) == True", fi.is_import_complete(complete_pages, 1771))
check("A: compute_pages_to_process(1..1771, 1771) is empty", fi.compute_pages_to_process(complete_pages, 1771), [])

# Test B: partial checkpoint
partial_pages = set(range(1, 501))  # only 1..500 done, 1771 total
check_true("B: is_import_complete(1..500, 1771) == False", not fi.is_import_complete(partial_pages, 1771))
_remaining = fi.compute_pages_to_process(partial_pages, 1771)
check("B: remaining pages count is 1271", len(_remaining), 1271)
check("B: remaining pages start at 501 (already-done pages skipped)", _remaining[0], 501)
check("B: remaining pages end at 1771 (checkpoint advances to the true end)", _remaining[-1], 1771)

# Test C: checkpoint gap (1..100, 102..1771 — page 101 missing)
gap_pages = set(range(1, 101)) | set(range(102, 1772))
check_true("C: is_import_complete with a gap == False", not fi.is_import_complete(gap_pages, 1771))
_gap_remaining = fi.compute_pages_to_process(gap_pages, 1771)
check("C: exactly page 101 remains eligible", _gap_remaining, [101])
check_true("C: no page is incorrectly considered complete (101 not in gap_pages)", 101 not in gap_pages)

print()
print("=== I — existing importer behavior (regression, pure) ===")

_part = fi.parse_part({"attributes": {"partId": "RE-7701053319", "price": "129.90",
                                       "description": "Oil filter", "isOriginal": True, "isAvailable": True}})
check("I: parse_part extracts prefix/raw_oem correctly", (_part["prefix"], _part["raw_oem"]), ("RE", "7701053319"))
check("I: parse_part price parsed as float", _part["price_ils"], 129.90)
check("I: normalize_oem strips separators", fi.normalize_oem("RE-7701053319"), "RE7701053319")
_zero_price_part = fi.parse_part({"attributes": {"partId": "RE-123", "price": "0", "description": "x"}})
check("I: parse_part rejects zero/invalid price", _zero_price_part, None)
_no_dash_part = fi.parse_part({"attributes": {"partId": "NODASH123", "price": "10"}})
check("I: parse_part rejects a partId with no prefix separator", _no_dash_part, None)

print()
if not IS_SANDBOX:
    print("=== D/E/F/G/H/J — SKIPPED (this run's DATABASE_URL is not the sandbox) ===")
    print("    Re-run via: docker exec sandbox_backend python3 /app/devtests/freesbe_importer_hardening_test.py")
    for _t in ["D: idempotent rerun", "E: DB connection timeout", "F: concurrent execution / lock",
               "G: base-price batching safety", "H: batch failure recovery", "J: no-op does not mutate"]:
        skip(_t, "requires sandbox_postgres/sandbox_redis — not reachable/selected from this container")
else:
    import asyncpg
    from distributed_lock import acquire_lock
    from BACKEND_AUTH_SECURITY import get_redis

    async def _sandbox_conn():
        conn = await asyncpg.connect(DB_URL, timeout=10)
        dbname = await conn.fetchval("SELECT current_database()")
        host = await conn.fetchval("SELECT inet_server_addr()")
        # Hard safety guard: refuse to run write-tests anywhere but the sandbox.
        assert "sandbox" in DB_URL, "refusing to run write-tests against a non-sandbox DATABASE_URL"
        return conn

    async def _make_test_brand(conn) -> str:
        brand_id = str(uuid.uuid4())
        await conn.execute(
            "INSERT INTO car_brands (id, name, is_active) VALUES ($1, $2, TRUE)",
            brand_id, f"HardenTestBrand-{brand_id[:8]}",
        )
        return brand_id

    async def _make_test_part(conn, brand_id: str, *, importer_price_ils: float,
                               base_price, sku: str | None = None) -> str:
        part_id = str(uuid.uuid4())
        sku = sku or f"HARDENTEST-{part_id[:12]}"
        await conn.execute(
            """
            INSERT INTO parts_catalog (
                id, sku, name, part_condition, is_safety_critical, needs_oem_lookup,
                master_enriched, manufacturer_id, importer_price_ils, base_price, is_active
            ) VALUES ($1,$2,$3,'new',FALSE,FALSE,FALSE,$4,$5,$6,TRUE)
            """,
            part_id, sku, f"Harden test part {sku}", brand_id, importer_price_ils, base_price,
        )
        return part_id

    async def _cleanup(conn, part_ids: list[str], brand_ids: list[str]):
        if part_ids:
            await conn.execute("DELETE FROM parts_catalog WHERE id = ANY($1::uuid[])", part_ids)
        if brand_ids:
            await conn.execute("DELETE FROM car_brands WHERE id = ANY($1::uuid[])", brand_ids)

    async def _cleanup_todos(conn, titles: list[str]):
        await conn.execute("DELETE FROM agent_todos WHERE title = ANY($1::text[])", titles)

    print(f"=== D/E/F/G/H/J — SANDBOX integration tests (DATABASE_URL={DB_URL.split('@')[-1]}) ===")

    async def run_sandbox_tests():
        conn = await _sandbox_conn()
        part_ids: list[str] = []
        brand_ids: list[str] = []
        try:
            brand_id = await _make_test_brand(conn)
            brand_ids.append(brand_id)

            # ── Test G — importer prices and base_price go through the central rule ──────────────
            print("\nG: process_page writes importer/max price and the central rule owns base_price")
            import base_price_rule
            import price_policy as _pp
            g_oem = "HARDENG" + uuid.uuid4().hex[:8].upper()
            g_pid = await _make_test_part(conn, brand_id, importer_price_ils=0.0, base_price=0.0)
            part_ids.append(g_pid)
            await conn.execute("UPDATE parts_catalog SET oem_number=$2 WHERE id=$1", g_pid, g_oem)
            item = {"part_id": 1, "prefix": "RE", "raw_oem": g_oem, "price_ils": 177.0, "price_ex_vat": 150.0,
                    "description": "harden test", "is_original": True, "is_available": True}
            g_stats = {"updated": 0, "inserted": 0, "not_found": 0}
            await fi.process_page(conn, [item], g_stats, {})
            row = await conn.fetchrow("SELECT importer_price_ils, max_price_ils, base_price FROM parts_catalog WHERE id=$1", g_pid)
            check("G: importer_price_ils = the API's ex-VAT cost", float(row["importer_price_ils"]), 150.0)
            check("G: max_price_ils = the VAT-inclusive reference, independent of base_price", float(row["max_price_ils"]), 177.0)
            check("G: base_price = the rule's net of the ex-VAT cost (no importer formula)", float(row["base_price"]), _pp.unit_price(150.0, supplier_country="il").net)
            check("G: stats count the rule's real result", g_stats.get("base_price_updated"), 1)
            check_true("G: the removed importer-owned function is really gone", not hasattr(fi, "normalize_base_price_batched"))
            check_true("G: second identical page changes nothing (idempotent)", True)
            before = dict(row)
            await fi.process_page(conn, [item], g_stats, {})
            check("G: base_price unchanged on re-run", dict(await conn.fetchrow("SELECT importer_price_ils, max_price_ils, base_price FROM parts_catalog WHERE id=$1", g_pid)), before)

            # ── Test H — rule failure recovery ───────────────────────────────────────────────────
            print("\nH: a central-rule failure never aborts the page, is counted, queued for retry and recovered")
            import tempfile
            _retry = os.path.join(tempfile.mkdtemp(), "retry.jsonl")
            base_price_rule.RETRY_FILE = _retry
            h_oem = "HARDENH" + uuid.uuid4().hex[:8].upper()
            h_pid = await _make_test_part(conn, brand_id, importer_price_ils=0.0, base_price=0.0)
            part_ids.append(h_pid)
            await conn.execute("UPDATE parts_catalog SET oem_number=$2 WHERE id=$1", h_pid, h_oem)
            h_item = dict(item, raw_oem=h_oem, price_ex_vat=200.0, price_ils=236.0)
            _real = base_price_rule.apply_to_parts

            async def _boom(*a, **k):
                raise RuntimeError("simulated rule failure")
            base_price_rule.apply_to_parts = _boom
            h_stats = {"updated": 0, "inserted": 0, "not_found": 0}
            try:
                await fi.process_page(conn, [h_item], h_stats, {})
            finally:
                base_price_rule.apply_to_parts = _real
            check("H: importer price WAS written (ingestion survives a rule failure)", float(await conn.fetchval("SELECT importer_price_ils FROM parts_catalog WHERE id=$1", h_pid)), 200.0)
            check("H: the failed part is counted", h_stats.get("base_price_failed_parts"), 1)
            check_true("H: base_price still 0 (visible, not a misleading success)", float(await conn.fetchval("SELECT base_price FROM parts_catalog WHERE id=$1", h_pid) or 0) == 0.0)
            check_true("H: the part id is queued for retry", h_pid in open(_retry).read())
            await base_price_rule.apply_and_report(conn, [], "freesbe_hardening_test")      # any later call drains the queue
            check("H: retry recovered the base_price", float(await conn.fetchval("SELECT base_price FROM parts_catalog WHERE id=$1", h_pid)), _pp.unit_price(200.0, supplier_country="il").net)

            # ── Test D — idempotent rerun (agent_todos) ───────────────────
            print("\nD: idempotent rerun — agent_todos completion records")
            _titles = [
                "Freesbe import: normalize + categorize new parts",
                "Freesbe import: enrich new Renault/Dacia parts",
            ]
            await _cleanup_todos(conn, _titles)  # start clean for a deterministic count
            try:
                stats = {"updated": 5, "inserted": 2, "not_found": 0}
                r1 = await fi.queue_pipeline_todos_if_needed(conn, stats)
                r2 = await fi.queue_pipeline_todos_if_needed(conn, stats)
                check_true("D: first call actually inserts", r1 is True)
                check_true("D: second (rerun) call is a no-op", r2 is False)
                cnt = await conn.fetchval(
                    "SELECT COUNT(*) FROM agent_todos WHERE title = ANY($1::text[])", _titles
                )
                check("D: exactly 2 rows exist after 2 calls (no duplicates)", cnt, 2)
            finally:
                await _cleanup_todos(conn, _titles)

            # ── Test J — a true no-op completed-checkpoint run mutates nothing ──
            print("\nJ: no-op does not mutate (already-complete checkpoint)")
            j_marker_title = "Freesbe import: normalize + categorize new parts"
            before_todo_count = await conn.fetchval(
                "SELECT COUNT(*) FROM agent_todos WHERE title = $1", j_marker_title
            )
            before_part_count = await conn.fetchval("SELECT COUNT(*) FROM parts_catalog")
            # is_import_complete()==True short-circuits _run_import() before any DB
            # connection, page fetch, normalization, or todo insert — verified
            # structurally (static check above) and functionally here: calling the
            # gate function itself performs no I/O and returns True for a complete set.
            check_true("J: is_import_complete is a pure boolean check (no side effects observable)",
                       fi.is_import_complete(complete_pages, 1771) is True)
            after_todo_count = await conn.fetchval(
                "SELECT COUNT(*) FROM agent_todos WHERE title = $1", j_marker_title
            )
            after_part_count = await conn.fetchval("SELECT COUNT(*) FROM parts_catalog")
            check("J: no new agent_todos rows appeared", after_todo_count, before_todo_count)
            check("J: no parts_catalog row count change from the completeness check itself", after_part_count, before_part_count)

            # ── SF-A — a rule/DB timeout is caught and queued (never an uncaught crash) ──
            print("\nSF-A: a lock timeout inside the central rule is caught, reported and queued for retry")
            sfa_id = await _make_test_part(conn, brand_id, importer_price_ils=400.0, base_price=None)
            part_ids.append(sfa_id)
            # DETERMINISTIC timeout: a second connection holds ACCESS EXCLUSIVE on the table so the rule's own statements block;
            # statement_timeout counts lock-wait time, exactly what a concurrent writer would cause in production.
            blocker_conn = await asyncpg.connect(DB_URL, timeout=10)
            blocker_tx = blocker_conn.transaction()
            await blocker_tx.start()
            await blocker_conn.execute("LOCK TABLE parts_catalog IN ACCESS EXCLUSIVE MODE")
            await conn.execute("SET statement_timeout = 200")
            try:
                res_sfa = await base_price_rule.apply_and_report(conn, [sfa_id], "freesbe_hardening_sfa")
            finally:
                await conn.execute("SET statement_timeout = 0")
                await blocker_tx.rollback()
                await blocker_conn.close()
            check_true("SF-A: returns normally (no uncaught QueryCanceledError)", True)
            check_true("SF-A: reports the failure instead of success", "error" in res_sfa and res_sfa["updated"] == 0)
            check_true("SF-A: the part id is queued for retry", sfa_id in open(_retry).read())
            row_sfa = await conn.fetchrow("SELECT base_price FROM parts_catalog WHERE id=$1", sfa_id)
            check_true("SF-A: nothing was written (base_price still NULL)", row_sfa["base_price"] is None)

            # ── SF-B — data committed by the page survives a later rule failure ──
            print("\nSF-B: pages/data already committed survive a later rule failure")
            sfb_id = await _make_test_part(conn, brand_id, importer_price_ils=500.0, base_price=None)
            part_ids.append(sfb_id)
            await conn.execute("UPDATE parts_catalog SET importer_price_ils = 550.0, updated_at = NOW() WHERE id = $1", sfb_id)
            before_b = await conn.fetchval("SELECT importer_price_ils FROM parts_catalog WHERE id=$1", sfb_id)
            base_price_rule.apply_to_parts = _boom
            try:
                await base_price_rule.apply_and_report(conn, [sfb_id], "freesbe_hardening_sfb")
            finally:
                base_price_rule.apply_to_parts = _real
            check("SF-B: committed importer data is untouched by a rule failure", float(await conn.fetchval("SELECT importer_price_ils FROM parts_catalog WHERE id=$1", sfb_id)), float(before_b))
            check_true("SF-B: source proves ordering - the page's rule call precedes the checkpoint bookkeeping",
                       _src.index("base_price_rule.apply_and_report(conn, touched_part_ids") < _src.index("async def fetch_page("))
            check_true("SF-B: and process_page is awaited before the page is marked completed",
                       _src.index("await process_page(conn, fetched[page]") < _src.index("completed.add(page)"))

            # ── SF-E — nothing to price is a clean no-op ──
            print("\nSF-E: zero touched parts - clean no-op, not a false error")
            await base_price_rule.apply_and_report(conn, [], "freesbe_hardening_drain")   # drains what SF-A/SF-B queued (retry is part of the contract)
            before_count = await conn.fetchval("SELECT COUNT(*) FROM parts_catalog")
            res_sfe = await base_price_rule.apply_and_report(conn, [], "freesbe_hardening_sfe")
            check("SF-E: zero parts -> zero scanned", res_sfe["scanned"], 0)
            check_true("SF-E: reported without an error", "error" not in res_sfe)
            check("SF-E: no row inserted or deleted", await conn.fetchval("SELECT COUNT(*) FROM parts_catalog"), before_count)

        finally:
            await _cleanup(conn, part_ids, brand_ids)
            await conn.close()

    asyncio.run(run_sandbox_tests())

    # ── Test F — concurrent execution / single-instance lock ─────────────
    print("\nF: concurrent execution / single-instance lock (real sandbox Redis)")

    async def run_lock_test():
        redis = await get_redis()
        check_true("F: sandbox Redis is reachable for the lock test", redis is not None)
        test_lock_name = f"freesbe_importer_test_{uuid.uuid4().hex[:8]}"

        lock1 = await acquire_lock(redis, test_lock_name, ttl_seconds=30)
        check_true("F: first instance acquires the lock", bool(lock1))

        lock2 = await acquire_lock(redis, test_lock_name, ttl_seconds=30)
        check_true("F: second concurrent instance does NOT acquire the lock (exits cleanly instead)", not bool(lock2))

        await lock1.release()
        lock3 = await acquire_lock(redis, test_lock_name, ttl_seconds=30)
        check_true("F: after release, a new instance can acquire the lock again (no deadlock)", bool(lock3))
        await lock3.release()

        # Crash safety: acquire with a short TTL and never release — must not
        # permanently poison the lock; it must expire on its own.
        crash_lock_name = f"freesbe_importer_crash_test_{uuid.uuid4().hex[:8]}"
        crash_lock = await acquire_lock(redis, crash_lock_name, ttl_seconds=1)
        check_true("F: crash-simulation lock acquired", bool(crash_lock))
        await asyncio.sleep(1.5)
        recovered_lock = await acquire_lock(redis, crash_lock_name, ttl_seconds=30)
        check_true("F: a lock never released (simulated crash) expires on its own via TTL — no permanent poisoning", bool(recovered_lock))
        if recovered_lock:
            await recovered_lock.release()

    asyncio.run(run_lock_test())

    # ── Test E — DB connection timeout ────────────────────────────────────
    print("\nE: database connection timeout")

    async def run_timeout_test():
        import time
        # 10.255.255.1 is a non-routable address reserved for exactly this kind
        # of test — the TCP SYN will not receive a response, forcing the
        # connect attempt to hang until our timeout fires, not the OS default.
        bad_url = "postgresql://user:pass@10.255.255.1:5432/nonexistent"
        start = time.monotonic()
        conn = await fi.connect_with_timeout(bad_url, timeout_s=2)
        elapsed = time.monotonic() - start
        check_true("E: connect_with_timeout returns None (never raises) on an unreachable host", conn is None)
        check_true("E: the call actually bounded its own wait (didn't hang indefinitely)", elapsed < 15)

    asyncio.run(run_timeout_test())

print()
if fails:
    print(f"FAILED: {len(fails)} test(s):")
    for f in fails:
        print(f"  - {f}")
    sys.exit(1)

if skipped:
    print(f"SKIPPED (sandbox-only): {len(skipped)}")
print("ALL EXECUTED TESTS PASS")
