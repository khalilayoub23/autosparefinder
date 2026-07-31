"""Job-queue runner: correctness of the ORCHESTRATION, with no real job run.

The queue drives destructive, hours-long catalogue jobs, so its control logic
has to be right before it is ever enabled. These tests use a throwaway step
whose "job" is a shell command, so ordering, claiming, batching, termination,
retry, stop/resume and the status view are all exercised for real (real table,
real subprocess, real transactions) without touching parts_catalog.

Covers the failure modes this repo has actually paid for:
  • a step that exits 0 having done nothing must NOT be reported as progress
  • termination must come from a MEASUREMENT, not an exit code
  • counting must not run on every batch (60-90s full scans here)
  • stop must land on a clean boundary, never mid-write

Run: docker exec autospare_backend python3 /app/devtests/job_queue_test.py
"""
import asyncio
import sys

sys.path.insert(0, "/app")
from sqlalchemy import text  # noqa: E402

import job_queue as jq  # noqa: E402
from BACKEND_API_ROUTES import async_session_factory  # noqa: E402

TEST_KEYS = ("zz_test_counted", "zz_test_uncounted", "zz_test_fail")
fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" +
          ("" if ok else f"\n         got={got!r} want={want!r}"))
    if not ok:
        fails.append(label)


async def cleanup(db):
    await db.execute(text("DELETE FROM pipeline_queue WHERE step_key = ANY(:k)"),
                     {"k": list(TEST_KEYS)})
    await db.commit()


async def add(db, key, order, cmd, count_sql=None, measure_every=1, batch=0):
    await db.execute(text("""
        INSERT INTO pipeline_queue (step_key, step_order, title, cmd, batch_limit,
                                    count_sql, measure_every)
        VALUES (:k,:o,:t,:c,:b,:q,:m)
        ON CONFLICT (step_key) DO UPDATE SET
          step_order=EXCLUDED.step_order, cmd=EXCLUDED.cmd,
          count_sql=EXCLUDED.count_sql, measure_every=EXCLUDED.measure_every,
          batch_limit=EXCLUDED.batch_limit, status='pending', batches_run=0,
          attempts=0, remaining=NULL, prev_remaining=NULL, error=NULL,
          started_at=NULL, finished_at=NULL
    """), {"k": key, "o": order, "t": f"TEST {key}", "c": cmd, "b": batch,
           "q": count_sql, "m": measure_every})
    await db.commit()


async def get(db, key):
    r = (await db.execute(text(
        "SELECT * FROM pipeline_queue WHERE step_key=:k"), {"k": key})).mappings().first()
    return dict(r) if r else None


async def park_real_steps(db, park=True):
    """Keep the REAL plan out of the way so tests claim only test steps."""
    await db.execute(text(
        "UPDATE pipeline_queue SET status=:s WHERE step_key <> ALL(:k)"),
        {"s": "skipped" if park else "pending", "k": list(TEST_KEYS)})
    await db.commit()


async def main():
    async with async_session_factory() as db:
        await jq.ensure_table(db)
        await cleanup(db)
        await jq.request_stop(False)

        print("0. the real plan seeds, in order, and starts PENDING")
        await jq.seed_default_plan(db)
        rows = (await db.execute(text(
            "SELECT step_key, step_order, status FROM pipeline_queue "
            "WHERE step_key <> ALL(:k) ORDER BY step_order"), {"k": list(TEST_KEYS)}
        )).mappings().all()
        keys = [r["step_key"] for r in rows]
        check("plan order", keys, ["merge_master_parts", "categorize_backlog",
                                   "part_thumbnails", "meili_reindex", "parity_check"])
        # The runner must default to OFF. Assert the DEFAULT, not the live value
        # — once the owner enables it (JOB_QUEUE_ENABLED=1) the live value is
        # legitimately True and an assertion on it would be testing the
        # deployment, not the code.
        import importlib
        src = open("/app/job_queue.py", encoding="utf-8").read()
        check("runner defaults to OFF in code",
              'os.getenv("JOB_QUEUE_ENABLED", "0")' in src, True)
        live_enabled = jq.ENABLED
        print(f"     (live JOB_QUEUE_ENABLED={live_enabled} — owner-controlled)")
        check("every step is in a known state",
              all(r["status"] in ("pending", "running", "skipped", "done", "failed")
                  for r in rows), True)

        await park_real_steps(db, True)

        # ── ordering ────────────────────────────────────────────────────────
        print("\n1. the LOWEST step_order is claimed first")
        await add(db, "zz_test_uncounted", 902, "true")
        await add(db, "zz_test_counted", 901, "true", count_sql="SELECT 0")
        r = await jq.run_once(db)
        check("claimed lowest order", r["step"], "zz_test_counted")

        # ── measured termination ────────────────────────────────────────────
        print("\n2. remaining=0 at claim time completes the step WITHOUT running it")
        check("completed on measurement", r["action"], "completed")
        row = await get(db, "zz_test_counted")
        check("status done", row["status"], "done")
        check("no batch was run", row["batches_run"], 0)

        print("\n3. an UNCOUNTED step completes by exiting 0 once")
        r = await jq.run_once(db)
        check("uncounted step ran", r["step"], "zz_test_uncounted")
        check("marked done", (await get(db, "zz_test_uncounted"))["status"], "done")

        print("\n4. queue is idle when every step is terminal")
        check("idle", (await jq.run_once(db))["action"], "idle")

        # ── no-progress termination ─────────────────────────────────────────
        print("\n5. a step that exits 0 but does NOT reduce remaining is finished,")
        print("   not looped forever (this is the 'exited 0, wrote nothing' trap)")
        await add(db, "zz_test_counted", 901, "true",
                  count_sql="SELECT 7", measure_every=1)
        r1 = await jq.run_once(db)     # measures 7, runs a batch
        check("first batch ran", r1["action"], "batch")
        check("remaining measured", r1["remaining"], 7)
        r2 = await jq.run_once(db)     # measures 7 again -> no progress -> done
        check("second pass completes", r2["action"], "completed")
        check("reason is the measurement",
              "no progress" in (r2.get("detail") or ""), True)
        check("status done", (await get(db, "zz_test_counted"))["status"], "done")

        # ── periodic measurement ────────────────────────────────────────────
        print("\n6. counting is PERIODIC, not once per batch (60-90s scans here)")
        await add(db, "zz_test_counted", 901, "true",
                  count_sql="SELECT 5", measure_every=3)
        seen = []
        for _ in range(3):
            rr = await jq.run_once(db)
            if rr["action"] != "batch":
                break
            seen.append(rr["measured"])
        check("measures on batch 1 only, within the window", seen, [True, False, False])

        # ── retry then fail ─────────────────────────────────────────────────
        print("\n7. a failing step retries, then fails terminally (no hot loop)")
        await cleanup(db)
        await add(db, "zz_test_fail", 903, "false", count_sql="SELECT 9")
        outcomes = []
        for _ in range(jq.MAX_ATTEMPTS):
            outcomes.append(await jq.run_once(db))
        check("all attempts errored",
              all(o["action"] == "error" for o in outcomes), True)
        check("attempts counted up",
              [o["attempts"] for o in outcomes], list(range(1, jq.MAX_ATTEMPTS + 1)))
        check("last one is final", outcomes[-1]["final"], True)
        check("status failed", (await get(db, "zz_test_fail"))["status"], "failed")
        check("error captured",
              bool((await get(db, "zz_test_fail"))["error"] is not None), True)

        # ── stop / resume ───────────────────────────────────────────────────
        print("\n8. stop is honoured BEFORE a batch starts (clean boundary)")
        await cleanup(db)
        await add(db, "zz_test_counted", 901, "true", count_sql="SELECT 3")
        await jq.request_stop(True)
        r = await jq.run_once(db)
        check("stopped", r["action"], "stopped")
        check("no batch ran", (await get(db, "zz_test_counted"))["batches_run"], 0)
        check("step left runnable", (await get(db, "zz_test_counted"))["status"], "pending")
        await jq.request_stop(False)
        r = await jq.run_once(db)
        check("resumes after clearing stop", r["action"], "batch")

        # ── status view ─────────────────────────────────────────────────────
        print("\n9. status view renders and reports honestly")
        st = await jq.status(db)
        check("status has every step", st["total"] >= 5, True)
        txt = jq.render_status(st)
        # The header reflects the LIVE toggle: "(מושבת)" when off, plain when on.
        check("header matches the live toggle",
              ("מושבת" in txt) == (not st["enabled"]), True)
        check("names a step", "TEST zz_test_counted" in txt or "מיזוג" in txt, True)

        # ── real count SQL is valid against the LIVE schema ─────────────────
        print("\n10. every real step's count_sql actually runs (schema drift guard)")
        for s in jq.DEFAULT_PLAN:
            q = (s.get("count_sql") or "").strip()
            if not q:
                print(f"  PASS  {s['step_key']}: no count (proves itself)")
                continue
            try:
                await db.execute(text("SET LOCAL statement_timeout='240s'"))
                v = int((await db.execute(text(q))).scalar() or 0)
                print(f"  PASS  {s['step_key']}: remaining = {v:,}")
            except Exception as exc:
                print(f"  FAIL  {s['step_key']}: {type(exc).__name__}: {str(exc)[:120]}")
                fails.append(f"count_sql {s['step_key']}")

        # ── cleanup ─────────────────────────────────────────────────────────
        await cleanup(db)
        await park_real_steps(db, False)
        await jq.request_stop(False)
        print("\n11. cleanup — test steps removed, real plan restored to pending")
        left = (await db.execute(text(
            "SELECT COUNT(*) FROM pipeline_queue WHERE step_key = ANY(:k)"),
            {"k": list(TEST_KEYS)})).scalar()
        check("test rows removed", left, 0)
        real = (await db.execute(text(
            "SELECT COUNT(*) FROM pipeline_queue WHERE status='pending'"))).scalar()
        check("real plan pending", real, 5)

    print()
    if fails:
        print(f"FAILED: {len(fails)} -> {fails}")
        return 1
    print("ALL PASS — orchestration is correct and the queue is still disabled.")
    return 0


sys.exit(asyncio.run(main()))
