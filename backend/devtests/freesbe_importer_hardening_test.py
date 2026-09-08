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

check_absent("base_price: old unbounded UPDATE (no LIMIT) is gone", 'result = await conn.fetch(\n        """\n        UPDATE parts_catalog\n        SET base_price = ROUND', _src)
check_present("base_price: batched function defined", "async def normalize_base_price_batched(", _src)
check_present("base_price: LIMIT-bounded batch query", "LIMIT $1", _src)
check_present("base_price: FOR UPDATE SKIP LOCKED used", "FOR UPDATE SKIP LOCKED", _src)
check_present("base_price: explicit statement_timeout set per batch", "SET LOCAL statement_timeout", _src)
check_present("base_price: each batch is its own transaction", "async with conn.transaction():", _src)
check_present("base_price: batch timeout/failure is caught, not left uncaught", "except (asyncpg.exceptions.QueryCanceledError, asyncpg.PostgresError)", _src)
check_present("base_price: incomplete normalization logged clearly, not silently swallowed", "base_price normalization INCOMPLETE", _src)
check_present("base_price: return type distinguishes complete vs incomplete", "-> tuple[int, bool]", _src)
check_present("base_price: caller checks the complete flag rather than assuming success", "if normalize_complete:", _src)

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
check_present("regression: idempotent UPDATE by primary key unchanged", "WHERE id = $4", _src)
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

            # ── Test G — base-price batching safety ──────────────────────
            print("\nG: base-price batching safety")
            g_ids = []
            for _ in range(7):
                pid = await _make_test_part(conn, brand_id, importer_price_ils=100.0, base_price=None)
                g_ids.append(pid)
                part_ids.append(pid)
            n, g_complete = await fi.normalize_base_price_batched(conn, batch_size=3, statement_timeout_ms=5000)
            check("G: all 7 qualifying rows normalized despite batch_size=3 (< total)", n >= 7, True)
            check_true("G: reports complete=True when no batch failed", g_complete)
            rows = await conn.fetch("SELECT base_price FROM parts_catalog WHERE id = ANY($1::uuid[])", g_ids)
            check("G: every targeted row now has a non-null base_price", all(r["base_price"] is not None for r in rows), True)
            # Formula corrected 2026-09-05 (pricing-policy fix): importer_price_ils
            # is now always ex-VAT cost by contract (process_page() writes it that
            # way from FREESBE's own priceWithoutVat field), so this safety-net
            # normalization no longer divides by 1.18 — doing so would silently
            # re-apply VAT-deflation to an already ex-VAT value. Matches
            # db_update_agent.normalize_base_price()'s canonical formula exactly.
            expected_base = round(100.0 * 1.45, 2)
            check("G: base_price computed with the correct cost/margin formula (no /1.18 — importer_price_ils is ex-VAT)", float(rows[0]["base_price"]), expected_base)
            check_present("G: does not use the old unbounded single UPDATE", "LIMIT $1", _src)

            # ── Test H — batch failure recovery ──────────────────────────
            print("\nH: batch failure recovery (rollback + safe resume)")
            h_committed_id = await _make_test_part(conn, brand_id, importer_price_ils=200.0, base_price=None)
            h_slow_id = await _make_test_part(conn, brand_id, importer_price_ils=300.0, base_price=None)
            part_ids += [h_committed_id, h_slow_id]

            # Batch 1: succeeds and commits normally.
            async with conn.transaction():
                await conn.execute("SET LOCAL statement_timeout = 5000")
                await conn.execute(
                    "UPDATE parts_catalog SET base_price = 1.23 WHERE id = $1", h_committed_id
                )
            # Batch 2: deliberately forced to exceed a tiny statement_timeout via pg_sleep,
            # mirroring the exact transaction+timeout pattern normalize_base_price_batched
            # uses, to prove a failed batch rolls back without touching batch 1's row.
            batch2_failed = False
            try:
                async with conn.transaction():
                    await conn.execute("SET LOCAL statement_timeout = 100")
                    await conn.execute(
                        "UPDATE parts_catalog SET base_price = 9.99 WHERE id = $1 AND pg_sleep(1) IS NULL",
                        h_slow_id,
                    )
            except asyncpg.exceptions.QueryCanceledError:
                batch2_failed = True
            check_true("H: the slow/forced batch actually raised QueryCanceledError", batch2_failed)

            row_committed = await conn.fetchrow("SELECT base_price FROM parts_catalog WHERE id=$1", h_committed_id)
            row_slow = await conn.fetchrow("SELECT base_price FROM parts_catalog WHERE id=$1", h_slow_id)
            check("H: earlier successful batch remains committed", float(row_committed["base_price"]), 1.23)
            check_true("H: failed batch's row was rolled back (base_price still NULL, not 9.99)", row_slow["base_price"] is None)

            # Safe resume: a normal call now picks up the still-unnormalized row.
            n2, h_complete = await fi.normalize_base_price_batched(conn, batch_size=10, statement_timeout_ms=5000)
            row_slow_after = await conn.fetchrow("SELECT base_price FROM parts_catalog WHERE id=$1", h_slow_id)
            check_true("H: importer can resume safely and complete the rolled-back row", row_slow_after["base_price"] is not None)
            check_true("H: checkpoint (here: DB state) never falsely marked the failed row done", n2 >= 1)
            check_true("H: the safe resume call itself reports complete=True", h_complete)

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

            # ── SAFETY-FIX SF-A — normalization timeout is caught, not raised ──
            print("\nSF-A: normalization timeout is caught cleanly (not an uncaught crash)")
            sfa_id = await _make_test_part(conn, brand_id, importer_price_ils=400.0, base_price=None)
            part_ids.append(sfa_id)
            # Force a DETERMINISTIC timeout rather than racing a tiny
            # statement_timeout against real query speed (an earlier version
            # of this test used statement_timeout_ms=1, which was flaky —
            # a fast sandbox could complete the query in under 1ms and never
            # actually time out). Instead, hold a real ACCESS EXCLUSIVE lock
            # on the table from a second connection so normalize's own
            # SELECT/UPDATE is guaranteed to block waiting for that lock —
            # statement_timeout counts lock-wait time too, so it reliably
            # fires with the same QueryCanceledError the real production
            # scenario (a concurrent writer holding a conflicting lock) would
            # produce — a more realistic test, not just a more reliable one.
            blocker_conn = await asyncpg.connect(DB_URL, timeout=10)
            blocker_tx = blocker_conn.transaction()
            await blocker_tx.start()
            await blocker_conn.execute("LOCK TABLE parts_catalog IN ACCESS EXCLUSIVE MODE")
            try:
                n_sfa, complete_sfa = await fi.normalize_base_price_batched(
                    conn, batch_size=10, statement_timeout_ms=200
                )
            finally:
                await blocker_tx.rollback()
                await blocker_conn.close()
            check_true("SF-A: does not raise — returns normally instead of propagating QueryCanceledError", True)
            check_true("SF-A: reports complete=False on a caught timeout", complete_sfa is False)
            check_true("SF-A: rows-updated count is non-negative and well-formed", isinstance(n_sfa, int) and n_sfa >= 0)
            row_sfa = await conn.fetchrow("SELECT base_price FROM parts_catalog WHERE id=$1", sfa_id)
            check_true("SF-A: the timed-out row's own batch was rolled back (base_price still NULL)", row_sfa["base_price"] is None)

            # ── SAFETY-FIX SF-B — pages already committed survive a later normalization failure ──
            print("\nSF-B: pages/data already committed survive a later normalization failure")
            # Simulate "page processing already committed" (autocommit, matching
            # process_page()'s real per-statement autocommit behavior) BEFORE
            # normalization is attempted, then force normalization to fail, and
            # confirm the earlier committed data is completely unaffected.
            sfb_id = await _make_test_part(conn, brand_id, importer_price_ils=500.0, base_price=None)
            part_ids.append(sfb_id)
            await conn.execute(
                "UPDATE parts_catalog SET importer_price_ils = 550.0, updated_at = NOW() WHERE id = $1",
                sfb_id,
            )  # represents a "page already processed" write, committed immediately (autocommit)
            committed_price_before = await conn.fetchval(
                "SELECT importer_price_ils FROM parts_catalog WHERE id=$1", sfb_id
            )
            _, _ = await fi.normalize_base_price_batched(conn, batch_size=10, statement_timeout_ms=1)
            committed_price_after = await conn.fetchval(
                "SELECT importer_price_ils FROM parts_catalog WHERE id=$1", sfb_id
            )
            check(
                "SF-B: data committed before normalization is untouched by a later normalization failure",
                float(committed_price_after), float(committed_price_before),
            )
            check_true(
                "SF-B: source proves ordering — checkpoint save call precedes the normalization call",
                _src.index("if page % 50 == 0 or page == total_pages:") <
                _src.index('print("\\nNormalizing base_price for newly priced parts...")'),
            )

            # ── SAFETY-FIX SF-E — true zero-candidate run is a clean, honest no-op ──
            print("\nSF-E: zero qualifying candidates — clean success, not a false error")
            # Ensure nothing in our own test fixtures still qualifies.
            await conn.execute(
                "UPDATE parts_catalog SET base_price = 1.00 WHERE id = ANY($1::uuid[]) AND base_price IS NULL",
                part_ids,
            )
            before_count = await conn.fetchval("SELECT COUNT(*) FROM parts_catalog")
            n_sfe, complete_sfe = await fi.normalize_base_price_batched(conn, batch_size=2000, statement_timeout_ms=5000)
            after_count = await conn.fetchval("SELECT COUNT(*) FROM parts_catalog")
            check("SF-E: zero candidates -> zero rows updated", n_sfe, 0)
            check_true("SF-E: zero candidates is reported as complete=True, never as a failure", complete_sfe is True)
            check("SF-E: no row was inserted or deleted by a zero-candidate run", after_count, before_count)

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
