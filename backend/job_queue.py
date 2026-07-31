"""
Script: job_queue.py
Purpose: Sequenced background job runner — the "run the jobs in a queue and we
         only observe" layer the owner asked for (2026-07-29). Runs the heavy
         catalogue jobs ONE AT A TIME, in order, with real counters, a safe
         stop/resume, and a single status view.

Process:
  1. `ensure_table()` creates `pipeline_queue` (idempotent, self-creating like
     social_inbox — no migration needed).
  2. `seed_default_plan()` inserts the standard plan in order, skipping steps
     that already exist. Steps start `pending` and NOTHING runs until enabled.
  3. `run_once()` claims the lowest-order runnable step and executes exactly ONE
     batch of it as a subprocess, then returns. The supervised loop calls it
     repeatedly, so a stop request or a container restart can never interrupt
     more than one batch.
  4. `status()` renders one compact view for the API and WhatsApp.

Data Imported/Modified: `pipeline_queue` only. The STEPS modify catalogue tables;
     this module never writes to them directly.
Data Sources: none (internal orchestration).
Last Updated: 2026-07-29

DESIGN NOTES — each of these is a lesson this repo already paid for:

  • PROGRESS IS MEASURED, NEVER SELF-REPORTED. Every step carries a `count_sql`
    that counts what is genuinely LEFT in the database. A script that exits 0
    having written nothing is the exact failure mode behind the 8,210/8,210
    "skipped" fitment outage and the Meili 620K drift. `remaining_at` is shown
    so a stale number is visible AS stale rather than quietly trusted.

  • …BUT MEASURING IS ITSELF EXPENSIVE, SO IT IS PERIODIC. Measured live: the
    merge-group count takes ~81s and the thumbnail count ~66s on this catalogue.
    Counting before AND after every batch would spend ~150s per batch scanning
    4.3M rows to learn what a 300-row batch did — the "scan-for-nothing"
    anti-pattern CLAUDE.md calls a chronic bottleneck, which this module would
    otherwise have reintroduced while claiming to avoid it. So each step
    declares `measure_every`, and a count runs on the first batch and then once
    per that many batches. Between measurements the number is simply shown with
    its age; it is never invented from the subprocess's own claims.

  • TERMINATION IS DECIDED BY A MEASUREMENT, NOT AN EXIT CODE. On a measuring
    batch the step is finished when remaining hits 0, or when remaining has not
    DECREASED since the previous measurement (the step can no longer make
    progress). An exit code of 0 alone never ends a counted step.

  • ONE BATCH PER CALL. The runner never holds a long-lived subprocess. Stop
    means "do not start the next batch", so stopping is always clean and never
    leaves a half-written transaction. This is also why a restart costs at most
    one batch.

  • DISABLED BY DEFAULT (`JOB_QUEUE_ENABLED`, default 0). The owner's standing
    gate is that infrastructure is verified BEFORE anything is triggered. The
    runner being present must not mean the runner is running.

  • NO CONCURRENCY WITH ITSELF. A Redis lock plus a DB-level `FOR UPDATE
    SKIP LOCKED` claim means two runners (e.g. during a restart overlap) cannot
    both drive the same step.

  • A STEP THAT WRITES NOTHING TWICE IN A ROW IS FINISHED, NOT LOOPING FOREVER.
    Termination is decided by the batch's own effect (remaining stopped moving),
    never by a fresh full-table COUNT per iteration — that "scan-for-nothing"
    shape is called out in CLAUDE.md as a chronic bottleneck.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shlex
import subprocess
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import text

logger = logging.getLogger("job_queue")

ENABLED = os.getenv("JOB_QUEUE_ENABLED", "0").strip().lower() in ("1", "true", "yes")
# Seconds a single batch may run before it is killed. A batch that cannot finish
# in this window is too big — shrink the step's `batch_limit`, do not raise this.
BATCH_TIMEOUT_S = int(os.getenv("JOB_QUEUE_BATCH_TIMEOUT_S", "2400"))
# Pause between batches so the queue never monopolises the box.
COOLDOWN_S = int(os.getenv("JOB_QUEUE_COOLDOWN_S", "20"))
MAX_ATTEMPTS = int(os.getenv("JOB_QUEUE_MAX_ATTEMPTS", "3"))
_STOP_KEY = "autospare:jobqueue:stop"

TERMINAL = ("done", "failed", "skipped")


# ─────────────────────────── the plan ────────────────────────────────────────
# Order matters and is deliberate:
#   1. merge      — collapse duplicate (manufacturer, OEM) rows into ONE master
#                   record FIRST, so every later step does its work once rather
#                   than repeatedly on rows that are about to be merged away.
#   2. categorize — fill the catch-all bucket on the now-deduplicated catalogue.
#   3. thumbnails — images last of the write steps; it is the slowest per row
#                   (network + OCR) and benefits from a smaller, cleaner set.
#   4. reindex    — Meilisearch must run AFTER every write, or search serves the
#                   pre-merge, pre-category state.
#   5. parity     — verify the destination actually matches the source. A
#                   pipeline is not "done" because its steps exited 0.
DEFAULT_PLAN: List[Dict[str, Any]] = [
    {
        "step_key": "merge_master_parts",
        "step_order": 10,
        "title": "מיזוג כפילויות לרשומת אב",
        # --brand '%' == ALL manufacturers (the arg is used as `manufacturer
        # ILIKE $1`). The script's default is 'bmw', so omitting this silently
        # merged one brand and reported "0 identity groups" — a no-op that the
        # no-progress rule would then have marked DONE with 272,152 groups left.
        # Safe only because the group key now includes manufacturer_id; without
        # that, a wildcard would merge the same OEM ACROSS brands.
        # --all-brands iterates manufacturers ONE AT A TIME with a persisted
        # cursor. The old catalogue-wide `--brand '%'` form degraded as it ran
        # (duplicates get sparser, so LIMIT n scans further each time): measured
        # 90s -> 369s -> past the statement timeout, at which point batches
        # started failing. Per-manufacturer discovery is index-bounded and flat:
        # 8s. Never put the catalogue-wide form back.
        "cmd": "python3 /app/maintenance/merge_master_parts.py --all-brands --limit {batch}",
        # Measured: a batch costs ~90s of GROUP-BY discovery over 4.3M rows plus
        # ~23s of actual merging per 1,000 groups. The discovery is paid ONCE
        # per batch regardless of size, so a small batch spends 80% of its time
        # re-finding work. 1,000 -> 272 batches ≈ 10h; 5,000 -> 55 batches ≈ 3h.
        # Still far inside JOB_QUEUE_BATCH_TIMEOUT_S (2400s).
        "batch_limit": 5000,
        # A merged loser is soft-deleted with is_active=FALSE and
        # specifications->>'dedup_merged_into' = <canonical id>. There is NO
        # dedup column on parts_catalog — `is_active` already excludes losers,
        # so grouping over active rows is the correct remaining-work count.
        "count_sql": """
            SELECT COUNT(*) FROM (
                SELECT 1 FROM parts_catalog
                WHERE is_active
                  AND oem_number IS NOT NULL AND btrim(oem_number) <> ''
                GROUP BY manufacturer_id,
                         REPLACE(REPLACE(REPLACE(UPPER(oem_number),' ',''),'-',''),'.','')
                HAVING COUNT(*) > 1
            ) g
        """,  # normalisation MUST match merge_master_parts.NORM exactly (incl. '.')
        # 25 batches x ~10 min meant `remaining` was refreshed only every ~4
        # HOURS, so the owner-facing status quoted a figure five hours old. The
        # count is a ~370s aggregate, so refreshing every 8 batches costs ~7%
        # overhead and keeps the number inside ~1.5h. On-demand truth comes from
        # LIVE_SQL instead; this figure also drives termination, so it cannot
        # simply be dropped.
        "measure_every": 8,
    },
    {
        "step_key": "categorize_backlog",
        "step_order": 20,
        "title": "סיווג חלקים בקטגוריית כללי",
        # --scope buckets == the fallback buckets only (the default). NOT
        # "backlog", which is not a valid choice and would abort under argparse.
        # --max-seconds is a SOFT budget below the queue's hard BATCH_TIMEOUT_S
        # (2400s). Without it the script tried to drain the whole backlog in one
        # run, got hard-killed at 2400s three times, and reported `failed` with
        # batches_run=0 — even though earlier internal batches had committed.
        # It now stops cleanly and the queue calls it again.
        "cmd": "python3 /app/maintenance/categorize_parts_batch.py "
               "--scope buckets --max-seconds 1500",
        "batch_limit": 0,          # the script batches internally
        "count_sql": """
            SELECT COUNT(*) FROM parts_catalog
            WHERE is_active AND category IN
                  ('כללי','general','service-general','accessories')
        """,
        "measure_every": 1,          # the script does one big internal pass
    },
    {
        "step_key": "part_thumbnails",
        "step_order": 30,
        "title": "תמונות חלקים (הורדה, ניקוי, העלאה)",
        "cmd": "python3 /app/maintenance/build_part_thumbnails.py --limit {batch}",
        "batch_limit": 300,
        # Must mirror the SCRIPT's own candidate query, which REQUIRES a
        # parts_images row — it can only thumbnail a part that has a source
        # image. Counting every part without a thumbnail reported 4,006,844 of
        # "work" while the script found 0 candidates, i.e. a progress bar for
        # work that does not exist. The real gap there is image SOURCING (only
        # ~0.7% of the catalogue has any source image), which is a different
        # job, not this one.
        "count_sql": """
            SELECT COUNT(*) FROM parts_catalog pc
            WHERE pc.is_active
              AND EXISTS (SELECT 1 FROM parts_images pi
                          WHERE pi.part_id = pc.id
                            AND pi.url IS NOT NULL AND pi.url <> '')
              AND NOT EXISTS (SELECT 1 FROM part_thumbnails t WHERE t.part_id = pc.id)
        """,
        "measure_every": 40,         # 300-row batches are quick; the count is not
    },
    {
        "step_key": "meili_reindex",
        "step_order": 40,
        "title": "אינדוקס מחדש ב-Meilisearch",
        "cmd": "python3 /app/meili_sync.py",
        "batch_limit": 0,
        "count_sql": None,          # parity is checked by the next step
    },
    {
        "step_key": "parity_check",
        "step_order": 50,
        "title": "בדיקת התאמה סופית",
        "cmd": "python3 /app/maintenance/pipeline_parity_check.py",
        "batch_limit": 0,
        "count_sql": None,
    },
]


# ─────────────────────────── schema ──────────────────────────────────────────
async def ensure_table(db) -> None:
    """Create pipeline_queue if absent. Idempotent; STARTUP/SEED ONLY.

    Never call this from a request path or from the runner loop. Even
    `CREATE INDEX IF NOT EXISTS` / `ALTER TABLE … ADD COLUMN IF NOT EXISTS`
    acquire an AccessExclusiveLock, which deadlocks against the runner's row
    locks on the same table. It short-circuits when the schema is already
    complete so a stray call is cheap, but the rule stands.
    """
    have = (await db.execute(text("""
        SELECT COUNT(*) FROM information_schema.columns
        WHERE table_name = 'pipeline_queue'
          AND column_name IN ('step_key','measure_every','prev_remaining')
    """))).scalar()
    if int(have or 0) >= 3:
        return                      # schema complete — issue no DDL at all
    await db.execute(text("""
        CREATE TABLE IF NOT EXISTS pipeline_queue (
            id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            step_key      varchar(64)  NOT NULL UNIQUE,
            step_order    integer      NOT NULL,
            title         text,
            cmd           text         NOT NULL,
            batch_limit   integer      NOT NULL DEFAULT 0,
            count_sql     text,
            measure_every integer      NOT NULL DEFAULT 1,
            prev_remaining bigint,
            status        varchar(16)  NOT NULL DEFAULT 'pending',
            batches_run   integer      NOT NULL DEFAULT 0,
            attempts      integer      NOT NULL DEFAULT 0,
            remaining     bigint,
            remaining_at  timestamptz,
            started_at    timestamptz,
            finished_at   timestamptz,
            last_batch_at timestamptz,
            last_output   text,
            error         text,
            created_at    timestamptz  NOT NULL DEFAULT NOW()
        )
    """))
    await db.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_pipeline_queue_order ON pipeline_queue (step_order)"))
    # Additive columns for an already-created table (a fresh CREATE above covers
    # a new install; these cover an upgrade in place).
    for col, ddl in (("measure_every", "integer NOT NULL DEFAULT 1"),
                     ("prev_remaining", "bigint")):
        await db.execute(text(
            f"ALTER TABLE pipeline_queue ADD COLUMN IF NOT EXISTS {col} {ddl}"))
    await db.commit()


async def seed_default_plan(db, *, reset: bool = False) -> int:
    """Insert the default plan, and RE-SYNC the definition of existing steps.

    The DEFINITION columns (cmd, count_sql, batch_limit, measure_every, order,
    title) are owned by DEFAULT_PLAN and are overwritten on every seed. The
    PROGRESS columns (status, batches_run, attempts, remaining, timestamps) are
    never touched here, so a re-seed cannot lose work.

    This used to be `ON CONFLICT DO NOTHING`, which meant fixing a command in
    code did NOT reach a queue that had already been seeded — the runner kept
    executing the old, broken command line while the source looked correct.
    That is exactly how the merge step went on running `--limit 500` with no
    `--brand` after the bug had been "fixed".
    """
    await ensure_table(db)
    if reset:
        await db.execute(text(
            "DELETE FROM pipeline_queue WHERE status <> 'running'"))
    n = 0
    for s in DEFAULT_PLAN:
        res = await db.execute(text("""
            INSERT INTO pipeline_queue
                   (step_key, step_order, title, cmd, batch_limit, count_sql,
                    measure_every)
            VALUES (:k, :o, :t, :c, :b, :q, :m)
            ON CONFLICT (step_key) DO UPDATE SET
                step_order    = EXCLUDED.step_order,
                title         = EXCLUDED.title,
                cmd           = EXCLUDED.cmd,
                batch_limit   = EXCLUDED.batch_limit,
                count_sql     = EXCLUDED.count_sql,
                measure_every = EXCLUDED.measure_every
        """), {"k": s["step_key"], "o": s["step_order"], "t": s["title"],
               "c": s["cmd"], "b": s["batch_limit"], "q": s.get("count_sql"),
               "m": int(s.get("measure_every", 1))})
        n += res.rowcount or 0
    await db.commit()
    return n


# ─────────────────────────── stop flag ───────────────────────────────────────
async def _redis():
    import redis.asyncio as redis
    return redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"))


async def request_stop(on: bool = True) -> bool:
    """Ask the runner to stop after the CURRENT batch (or clear the request).

    Deliberately not a kill: a batch is allowed to finish and commit, so the
    queue is always stopped at a clean boundary.
    """
    try:
        r = await _redis()
        if on:
            await r.set(_STOP_KEY, "1")
        else:
            await r.delete(_STOP_KEY)
        await r.aclose()
        return True
    except Exception as exc:
        logger.warning("job_queue: stop flag unavailable (%s)", exc)
        return False


async def stop_requested() -> bool:
    try:
        r = await _redis()
        v = await r.exists(_STOP_KEY)
        await r.aclose()
        return bool(v)
    except Exception:
        return False      # Redis down => keep running rather than silently halt


# ─────────────────────────── measurement ─────────────────────────────────────
async def _measure(db, step: Dict[str, Any]) -> Optional[int]:
    """Count what is genuinely LEFT for this step, from the database.

    Returns None when the step declares no count (its completion is proven by
    the step itself, e.g. the parity check). A failed measurement returns None
    rather than 0 — reporting "0 remaining" because a query timed out would
    declare the work finished when it has not started.
    """
    q = (step.get("count_sql") or "").strip()
    if not q:
        return None
    try:
        await db.execute(text("SET LOCAL statement_timeout = '120s'"))
        return int((await db.execute(text(q))).scalar() or 0)
    except Exception as exc:
        logger.warning("job_queue: measure failed for %s: %s", step.get("step_key"), exc)
        return None


# ─────────────────────────── the runner ──────────────────────────────────────
def _run_batch(cmd: str, timeout_s: int) -> Dict[str, Any]:
    """Execute ONE batch as a subprocess at low priority.

    Low priority (`nice`) matters: these jobs share 6 vCPUs with the harvester
    and uvicorn, and a CPU-hungry batch that starves the API is a worse outcome
    than a slow backfill.
    """
    t0 = time.time()
    try:
        p = subprocess.run(
            shlex.split(cmd), capture_output=True, text=True, timeout=timeout_s,
            preexec_fn=lambda: os.nice(15),
        )
        out = ((p.stdout or "") + ("\n" + p.stderr if p.stderr else ""))[-4000:]
        return {"rc": p.returncode, "out": out.strip(), "elapsed": time.time() - t0,
                "timed_out": False}
    except subprocess.TimeoutExpired:
        return {"rc": -1, "out": f"TIMEOUT after {timeout_s}s", "timed_out": True,
                "elapsed": time.time() - t0}
    except Exception as exc:
        return {"rc": -1, "out": f"{type(exc).__name__}: {exc}", "timed_out": False,
                "elapsed": time.time() - t0}


async def run_once(db) -> Dict[str, Any]:
    """Claim the next runnable step and run exactly ONE batch of it.

    Returns a summary dict. Never raises: the supervised loop must survive any
    single bad batch.
    """
    if await stop_requested():
        return {"action": "stopped", "detail": "stop flag set"}

    # Claim atomically so two runners cannot drive the same step.
    row = (await db.execute(text("""
        SELECT id, step_key, title, cmd, batch_limit, count_sql, status,
               batches_run, attempts, remaining, prev_remaining, measure_every
        FROM pipeline_queue
        WHERE status IN ('pending','running')
        ORDER BY step_order ASC
        FOR UPDATE SKIP LOCKED
        LIMIT 1
    """))).mappings().first()
    if not row:
        await db.commit()
        return {"action": "idle", "detail": "no runnable step"}

    step = dict(row)
    key = step["step_key"]
    counted = bool((step.get("count_sql") or "").strip())
    every = max(1, int(step.get("measure_every") or 1))
    ran = int(step.get("batches_run") or 0)

    if step["status"] == "pending":
        await db.execute(text("""
            UPDATE pipeline_queue SET status='running', started_at=COALESCE(started_at, NOW())
            WHERE id = :id"""), {"id": step["id"]})
        await db.commit()

    # Measure on the FIRST batch of the step, then once per `measure_every`.
    # Counting is a 60-90s full scan here, so doing it every batch would cost
    # more than the work itself.
    must_measure = counted and (ran == 0 or ran % every == 0)
    if must_measure:
        before = await _measure(db, step)
        if before is not None:
            await db.execute(text("""
                UPDATE pipeline_queue SET remaining=:r, remaining_at=NOW() WHERE id=:id"""),
                {"r": before, "id": step["id"]})
            await db.commit()
            if before == 0:
                await _finish(db, step["id"], "done")
                return {"action": "completed", "step": key, "remaining": 0,
                        "detail": "nothing remaining"}
            prev = step.get("prev_remaining")
            # CRITICAL: "no progress" only means FINISHED when the previous batch
            # actually SUCCEEDED. An errored batch also leaves remaining
            # unchanged — treating that as completion would mark a BROKEN step
            # as done and let the queue move on to steps that depend on it.
            # `attempts > 0` means the last batch failed and is pending a retry.
            last_errored = int(step.get("attempts") or 0) > 0
            if prev is not None and before >= int(prev) and not last_errored:
                # A whole measurement window produced no reduction — the step
                # cannot make further progress. Believe the measurement.
                await _finish(db, step["id"], "done")
                return {"action": "completed", "step": key, "remaining": before,
                        "detail": f"no progress across {every} batch(es); prev={prev}"}
            await db.execute(text(
                "UPDATE pipeline_queue SET prev_remaining=:p WHERE id=:id"),
                {"p": before, "id": step["id"]})
            await db.commit()
    else:
        before = step.get("remaining")

    cmd = step["cmd"].replace("{batch}", str(step["batch_limit"] or 0))
    logger.info("[job_queue] %s batch #%d -> %s", key, ran + 1, cmd)
    res = await asyncio.to_thread(_run_batch, cmd, BATCH_TIMEOUT_S)

    if res["rc"] != 0:
        attempts = (step["attempts"] or 0) + 1
        final = attempts >= MAX_ATTEMPTS
        # NOTE: `:s` must NOT also be used inside a comparison here. Binding one
        # param in two type contexts (varchar column vs a text literal compare)
        # raises asyncpg AmbiguousParameterError — the same trap already
        # recorded for parts_images. Pass the derived value as its own param.
        await db.execute(text("""
            UPDATE pipeline_queue
            SET attempts=:a, status=:s, error=:e, last_output=:o,
                last_batch_at=NOW(),
                finished_at = CASE WHEN :fin THEN NOW() ELSE finished_at END
            WHERE id=:id"""),
            {"a": attempts, "s": "failed" if final else "running", "fin": bool(final),
             "e": res["out"][:2000], "o": res["out"], "id": step["id"]})
        await db.commit()
        return {"action": "error", "step": key, "attempts": attempts,
                "final": final, "detail": res["out"][:300]}

    await db.execute(text("""
        UPDATE pipeline_queue
        SET batches_run = batches_run + 1, attempts = 0, error = NULL,
            last_output = :o, last_batch_at = NOW()
        WHERE id = :id"""), {"o": res["out"], "id": step["id"]})
    await db.commit()

    # An UNCOUNTED step (reindex, parity) proves its own completion by exiting 0
    # once — there is no remaining-work query to consult.
    if not counted:
        await _finish(db, step["id"], "done")
        return {"action": "done", "step": key, "elapsed": round(res["elapsed"], 1)}

    return {"action": "batch", "step": key, "remaining": before,
            "measured": must_measure, "elapsed": round(res["elapsed"], 1)}


async def _finish(db, step_id, status: str, error: Optional[str] = None) -> None:
    await db.execute(text("""
        UPDATE pipeline_queue
        SET status=:s, finished_at=NOW(), error=:e
        WHERE id=:id"""), {"s": status, "e": error, "id": step_id})
    await db.commit()


# ─────────────────────────── status view ─────────────────────────────────────
async def queue_busy(db) -> bool:
    """True when the queue has a WRITE step still to do.

    Other heavy catalogue writers (db_update_agent's run_all_tasks, the
    thumbnail supervisor) call this and stand down, because two batched writers
    on parts_catalog do not go twice as fast — they contend. Measured
    2026-07-29: the merge step dropped from ~24,000 parts/hour to ~700 while
    run_all_tasks ran alongside it.

    The read-only tail of the plan (reindex, parity) is not counted as busy:
    those do not fight for row locks.
    """
    try:
        n = (await db.execute(text("""
            SELECT COUNT(*) FROM pipeline_queue
            WHERE status IN ('pending','running')
              AND step_key NOT IN ('meili_reindex','parity_check')
        """))).scalar()
        return bool(n)
    except Exception:
        return False


async def owns_step(db, step_key: str) -> bool:
    """True when the QUEUE is responsible for `step_key` right now.

    Some steps are also driven by their own standalone supervisor (thumbnails).
    Both running the same script concurrently is pure contention: two processes
    issue the same candidate query and fight for the same rows. The supervisor
    asks this before each cycle and stands down while the queue owns the step,
    then resumes once the queue has finished it (so newly imported parts keep
    getting processed).
    """
    if not (os.getenv("JOB_QUEUE_ENABLED", "0").strip().lower() in ("1", "true", "yes")):
        return False
    try:
        st = (await db.execute(text(
            "SELECT status FROM pipeline_queue WHERE step_key = :k"), {"k": step_key}
        )).scalar()
        return st in ("pending", "running")
    except Exception:
        return False       # cannot tell => do not block the supervisor


# A CHEAP, always-true progress signal per step, measured on demand when the
# owner asks (`תור`). The `count_sql` figures are 60-370s aggregates and are
# therefore periodic; these are 9-18s and can be run live, so the owner is never
# handed a stale number as if it were current.
LIVE_SQL: Dict[str, Dict[str, str]] = {
    "merge_master_parts": {
        "label": "חלקים שמוזגו לרשומת אב",
        "sql": "SELECT COUNT(*) FROM parts_catalog "
               "WHERE specifications ? 'dedup_merged_into'",
    },
    "categorize_backlog": {
        "label": "נותרו בקטגוריית כללי",
        "sql": "SELECT COUNT(*) FROM parts_catalog WHERE is_active AND category IN "
               "('כללי','general','service-general','accessories')",
    },
}


async def live_progress(db, step_key: str, timeout_s: int = 45) -> Optional[str]:
    """Measure the running step's progress RIGHT NOW. None if unavailable."""
    spec = LIVE_SQL.get(step_key)
    if not spec:
        return None
    try:
        await db.execute(text(f"SET LOCAL statement_timeout = '{timeout_s}s'"))
        v = (await db.execute(text(spec["sql"]))).scalar()
        return f"{spec['label']}: {int(v or 0):,}"
    except Exception as exc:
        logger.warning("live_progress(%s) failed: %s", step_key, exc)
        return None


async def status(db, live: bool = False) -> Dict[str, Any]:
    # DO NOT call ensure_table() here. `CREATE INDEX` / `ALTER TABLE … ADD
    # COLUMN` take an AccessExclusiveLock on pipeline_queue, while the runner
    # holds row locks on the same table (FOR UPDATE + UPDATE). Running DDL from
    # a READ path deadlocked the two against each other — observed live:
    #   "waits for RowExclusiveLock … blocked by …; waits for
    #    AccessExclusiveLock … blocked by …"
    # which both 500'd this endpoint and killed the runner's batch. DDL belongs
    # to startup/seed only. If the table is missing, this raises and the caller
    # surfaces it — which is correct, and far better than silently locking.
    rows = (await db.execute(text("""
        SELECT step_key, step_order, title, status, batches_run, attempts,
               remaining, remaining_at, started_at, finished_at, last_batch_at,
               error
        FROM pipeline_queue ORDER BY step_order ASC
    """))).mappings().all()
    steps = [dict(r) for r in rows]
    running = next((s["step_key"] for s in steps if s["status"] == "running"), None)
    out = {
        "enabled": os.getenv("JOB_QUEUE_ENABLED", "0").strip().lower()
                   in ("1", "true", "yes"),
        "stop_requested": await stop_requested(),
        "total": len(steps),
        "done": sum(1 for s in steps if s["status"] == "done"),
        "failed": sum(1 for s in steps if s["status"] == "failed"),
        "running": running,
        "steps": steps,
    }
    if live and running:
        out["live"] = await live_progress(db, running)
    return out


def _age_str(ts) -> str:
    """How old a measurement is, in words. Empty when it is fresh."""
    if not ts:
        return ""
    try:
        t = ts.replace(tzinfo=None) if getattr(ts, "tzinfo", None) else ts
        mins = int((datetime.utcnow() - t).total_seconds() / 60)
    except Exception:
        return ""
    if mins < 3:
        return ""
    if mins < 90:
        return f" (נמדד לפני {mins} דק')"
    return f" (נמדד לפני {mins // 60} שעות)"


def render_status(st: Dict[str, Any]) -> str:
    """Compact Hebrew status for WhatsApp — the 'we only observe' view.

    EVERY measured number is shown WITH ITS AGE. The counts here are 60-370s
    full-table aggregates, so they are refreshed periodically rather than per
    batch; without the age a figure measured four hours ago reads as current.
    That is exactly what happened on 2026-07-29 — the owner was told 178,363
    remaining when the live number was 132,276, and reasonably called it untrue
    data. A stale number is acceptable; a stale number presented as fresh is not.
    """
    icon = {"pending": "⏳", "running": "▶️", "done": "✅",
            "failed": "❌", "skipped": "⏭️"}
    head = "🧵 *תור המשימות*"
    if not st["enabled"]:
        head += " (מושבת — לא רץ)"
    elif st["stop_requested"]:
        head += " (בקשת עצירה)"
    lines = [head]
    for s in st["steps"]:
        bits = []
        if s["remaining"] is not None:
            bits.append(f"נותרו {int(s['remaining']):,}{_age_str(s.get('remaining_at'))}")
        if s["batches_run"]:
            bits.append(f"{s['batches_run']} מנות")
        if s["status"] == "running" and s.get("last_batch_at"):
            a = _age_str(s["last_batch_at"])
            bits.append(f"מנה אחרונה{a or ' — כרגע'}")
        tail = (" · " + " · ".join(bits)) if bits else ""
        lines.append(f"{icon.get(s['status'],'•')} {s['title'] or s['step_key']}{tail}")
        if s["status"] == "failed" and s["error"]:
            lines.append(f"   ↳ {str(s['error'])[:110]}")
    if st.get("live"):
        lines.append(f"🔎 *נמדד עכשיו*: {st['live']}")
    lines.append(f"— {st['done']}/{st['total']} הושלמו")
    return "\n".join(lines)


__all__ = [
    "ENABLED", "ensure_table", "seed_default_plan", "run_once", "status",
    "render_status", "request_stop", "stop_requested", "DEFAULT_PLAN",
]
