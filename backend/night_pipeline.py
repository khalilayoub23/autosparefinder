"""
Script: night_pipeline.py
Purpose: The ONE authoritative automatic controller for heavy night jobs
         (FIXES_TRACKER #57, 2026-10-03):

             controller → one heavy job → VERIFIED completion → next eligible job

Root cause it removes: heavy jobs were started by independent clock-time loops
  (auto_backup 01:00, eBay fitment 01:00, sync_prices 01:15, REX 00:00 UTC, brand
  discovery 03:15, weekly maintenance Sat 05:00). Clock offsets only prevent overlap
  while every runtime stays near its median — sync_prices ranges 23..203 min — and all
  8 uvicorn cgroup-OOM kills of the week before happened with two heavy in-process jobs
  running together. The defect was the absence of central orchestration, not the times.

Process (Controller.tick, called by controller_loop every POLL seconds):
  1. Night D = the local (Israel) date whose window [WINDOW_OPEN, WINDOW_CLOSE) contains
     "now" (or the most recent one). One row per (job, period) is created QUEUED —
     daily "D:YYYY-MM-DD", weekly "W:YYYY-Www" (only on the job's weekday), monthly
     "M:YYYY-MM" (day `monthday` .. `monthday+catchup_days`). The row is the idempotency
     key: a period can be claimed QUEUED→RUNNING exactly once, ever.
  2. If ANY pipeline job is RUNNING, nothing else starts — however long it has run.
  3. Jobs are taken strictly in registry order. The first QUEUED job starts only when:
     its earliest-start time has passed (a CONSTRAINT, not a trigger), every dependency
     is COMPLETED for this night, no conflicting lock is held (manual runs, DB agent for
     memory-heavy jobs, the backlog job_queue, an external pg_dump), and the resource
     guard (host MemAvailable, cgroup headroom, load) passes. Otherwise it WAITS and the
     reason is recorded on the row.
  4. The job is awaited to real completion, then its post-condition is verified:
     COMPLETED only if run() returned AND verify() passed; any exception or failed
     post-condition is FAILED. Dependents of a FAILED/BLOCKED job become BLOCKED;
     unrelated later jobs still run, one at a time.
  5. When the window closes, jobs still QUEUED are BLOCKED ("window closed") — no daytime
     catch-up burst. A monthly job with catch-up nights left stays QUEUED for the next
     night instead (still at most one execution per month).
  6. On controller start, RUNNING rows left by a dead process become FAILED
     ("interrupted by backend restart") — never re-run, so a restart cannot duplicate.
  The controller never cancels, kills or restarts anything; it only waits.

Data Imported/Modified: table `night_pipeline_runs` (self-created at controller start),
     Redis key autospare:night_pipeline:running (TTL marker other loops stand down on).
     The JOBS modify their own tables; this module never writes catalogue data.
Data Sources: none (internal orchestration).
Missing Data Delegation: n/a.
Last Updated: 2026-10-03
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from local_schedule import LOCAL_TZ

logger = logging.getLogger("night_pipeline")

QUEUED, RUNNING, COMPLETED, FAILED, BLOCKED = "QUEUED", "RUNNING", "COMPLETED", "FAILED", "BLOCKED"
TERMINAL = (COMPLETED, FAILED, BLOCKED)
RUNNING_MARKER_KEY = "autospare:night_pipeline:running"


def _hm(env: str, default: str) -> Tuple[int, int]:
    h, m = (os.getenv(env, default) or default).split(":")
    return int(h), int(m)


WINDOW_OPEN = _hm("NIGHT_PIPELINE_OPEN_IL", "01:00")
WINDOW_CLOSE = _hm("NIGHT_PIPELINE_CLOSE_IL", "07:00")
POLL_S = int(os.getenv("NIGHT_PIPELINE_POLL_S", "60"))
COOLDOWN_S = int(os.getenv("NIGHT_PIPELINE_COOLDOWN_S", "60"))


def enabled() -> bool:
    """Production only by default: the sandbox backend bind-mounts the same code and must
    not run the heavy pipeline (its backup loop was writing sandbox dumps into the
    production backups directory)."""
    default = "1" if os.getenv("ENVIRONMENT", "").strip().lower() == "production" else "0"
    return os.getenv("NIGHT_PIPELINE_ENABLED", default).strip().lower() in ("1", "true", "yes")


@dataclass
class JobSpec:
    key: str
    period: str                                   # "daily" | "weekly" | "monthly"
    run: Callable[[], Awaitable[Any]]
    verify: Callable[[Any, datetime], Awaitable[Tuple[bool, str]]]
    earliest: Tuple[int, int] = WINDOW_OPEN       # local wall time on the night's date
    weekday: Optional[int] = None                 # weekly: 0=Mon … 6=Sun
    monthday: Optional[int] = None                # monthly: first eligible day
    catchup_days: int = 0                         # monthly: extra eligible nights
    depends_on: Tuple[str, ...] = ()
    conflict_locks: Tuple[str, ...] = ()          # Redis lock names that must be free
    mem_heavy: bool = False

    def eligible(self, d: date) -> bool:
        if self.period == "daily":
            return True
        if self.period == "weekly":
            return d.weekday() == self.weekday
        return self.monthday <= d.day <= self.monthday + self.catchup_days

    def last_eligible_night(self, d: date) -> bool:
        return self.period != "monthly" or d.day >= self.monthday + self.catchup_days

    def period_key(self, d: date) -> str:
        if self.period == "daily":
            return f"D:{d.isoformat()}"
        if self.period == "weekly":
            y, w, _ = d.isocalendar()
            return f"W:{y}-W{w:02d}"
        return f"M:{d.year}-{d.month:02d}"


# ── state stores ────────────────────────────────────────────────────────────────

class MemoryStore:
    """In-memory store with the same semantics as PgStore (tests, and a safe fallback)."""

    def __init__(self) -> None:
        self.rows: Dict[Tuple[str, str], Dict[str, Any]] = {}

    async def ensure_schema(self) -> None:
        return None

    async def ensure(self, job_key: str, period_key: str, now: datetime) -> None:
        self.rows.setdefault((job_key, period_key), {
            "job_key": job_key, "period_key": period_key, "state": QUEUED, "reason": None,
            "created_at": now, "started_at": None, "finished_at": None, "detail": None})

    async def get(self, job_key: str, period_key: str) -> Optional[Dict[str, Any]]:
        r = self.rows.get((job_key, period_key))
        return dict(r) if r else None

    async def claim(self, job_key: str, period_key: str, now: datetime) -> bool:
        r = self.rows.get((job_key, period_key))
        if not r or r["state"] != QUEUED:
            return False
        r.update(state=RUNNING, started_at=now, reason=None)
        return True

    async def finish(self, job_key: str, period_key: str, state: str, reason: Optional[str],
                     detail: Any, now: datetime) -> None:
        r = self.rows[(job_key, period_key)]
        r.update(state=state, reason=reason, detail=detail, finished_at=now)

    async def note(self, job_key: str, period_key: str, reason: str) -> None:
        r = self.rows.get((job_key, period_key))
        if r and r["state"] == QUEUED:
            r["reason"] = reason

    async def running(self) -> List[Dict[str, Any]]:
        return [dict(r) for r in self.rows.values() if r["state"] == RUNNING]

    async def queued(self) -> List[Dict[str, Any]]:
        return [dict(r) for r in self.rows.values() if r["state"] == QUEUED]

    async def fail_interrupted(self, process_start: datetime, now: datetime) -> List[str]:
        out = []
        for r in self.rows.values():
            if r["state"] == RUNNING and r["started_at"] and r["started_at"] < process_start:
                r.update(state=FAILED, reason="interrupted by backend restart", finished_at=now)
                out.append(r["job_key"])
        return out


class PgStore:
    """night_pipeline_runs in the catalog DB. One row per (job, period) — the PRIMARY KEY
    is the idempotency guarantee; claim() is a single conditional UPDATE."""

    def __init__(self, session_factory) -> None:
        self._sf = session_factory

    async def _exec(self, sql: str, params: Optional[dict] = None, fetch: bool = False):
        from sqlalchemy import text
        async with self._sf() as db:
            res = await db.execute(text(sql), params or {})
            rows = [dict(r._mapping) for r in res.fetchall()] if fetch else None
            await db.commit()
            return rows if fetch else res.rowcount

    async def ensure_schema(self) -> None:
        await self._exec("""
            CREATE TABLE IF NOT EXISTS night_pipeline_runs (
                job_key     text        NOT NULL,
                period_key  text        NOT NULL,
                state       text        NOT NULL DEFAULT 'QUEUED',
                reason      text,
                detail      jsonb,
                created_at  timestamptz NOT NULL DEFAULT now(),
                started_at  timestamptz,
                finished_at timestamptz,
                PRIMARY KEY (job_key, period_key)
            )""")

    async def ensure(self, job_key: str, period_key: str, now: datetime) -> None:
        await self._exec("INSERT INTO night_pipeline_runs (job_key, period_key, created_at) "
                         "VALUES (:j, :p, :n) ON CONFLICT (job_key, period_key) DO NOTHING",
                         {"j": job_key, "p": period_key, "n": now})

    async def get(self, job_key: str, period_key: str) -> Optional[Dict[str, Any]]:
        rows = await self._exec("SELECT * FROM night_pipeline_runs WHERE job_key=:j AND period_key=:p",
                                {"j": job_key, "p": period_key}, fetch=True)
        return rows[0] if rows else None

    async def claim(self, job_key: str, period_key: str, now: datetime) -> bool:
        n = await self._exec("UPDATE night_pipeline_runs SET state='RUNNING', started_at=:n, reason=NULL "
                             "WHERE job_key=:j AND period_key=:p AND state='QUEUED'",
                             {"j": job_key, "p": period_key, "n": now})
        return n == 1

    async def finish(self, job_key: str, period_key: str, state: str, reason: Optional[str],
                     detail: Any, now: datetime) -> None:
        await self._exec("UPDATE night_pipeline_runs SET state=:s, reason=:r, detail=CAST(:d AS jsonb), "
                         "finished_at=:n WHERE job_key=:j AND period_key=:p",
                         {"s": state, "r": reason, "n": now, "j": job_key, "p": period_key,
                          "d": json.dumps(detail, default=str)[:20000] if detail is not None else None})

    async def note(self, job_key: str, period_key: str, reason: str) -> None:
        await self._exec("UPDATE night_pipeline_runs SET reason=:r WHERE job_key=:j AND period_key=:p "
                         "AND state='QUEUED' AND reason IS DISTINCT FROM :r",
                         {"r": reason[:500], "j": job_key, "p": period_key})

    async def running(self) -> List[Dict[str, Any]]:
        return await self._exec("SELECT * FROM night_pipeline_runs WHERE state='RUNNING'", fetch=True)

    async def queued(self) -> List[Dict[str, Any]]:
        return await self._exec("SELECT * FROM night_pipeline_runs WHERE state='QUEUED'", fetch=True)

    async def fail_interrupted(self, process_start: datetime, now: datetime) -> List[str]:
        rows = await self._exec(
            "UPDATE night_pipeline_runs SET state='FAILED', reason='interrupted by backend restart', "
            "finished_at=:n WHERE state='RUNNING' AND started_at < :ps RETURNING job_key",
            {"n": now, "ps": process_start}, fetch=True)
        return [r["job_key"] for r in rows]


# ── resource guard ──────────────────────────────────────────────────────────────

def _read_host_avail_mb() -> Optional[float]:
    try:
        with open("/proc/meminfo") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) / 1024
    except Exception:
        return None
    return None


def _read_cgroup_headroom_mb() -> Optional[float]:
    """memory.max − anonymous memory of this container (page cache is reclaimable)."""
    try:
        mx = open("/sys/fs/cgroup/memory.max").read().strip()
        if mx == "max":
            return None
        anon = 0
        with open("/sys/fs/cgroup/memory.stat") as fh:
            for line in fh:
                if line.startswith("anon "):
                    anon = int(line.split()[1])
                    break
        return (int(mx) - anon) / 1048576
    except Exception:
        return None


def _read_load_per_cpu() -> Optional[float]:
    try:
        return os.getloadavg()[1] / max(1, os.cpu_count() or 1)
    except Exception:
        return None


@dataclass
class ResourceGuard:
    """Refuses to START a heavy job under memory/CPU pressure. It never frees resources by
    stopping anything — the controller simply re-checks on the next poll.
    Defaults from measured evidence (#56): the host OOM-killed at ~1.5 GB available when
    ~1 GB of extra Chrome appeared; uvicorn was cgroup-OOM-killed at its 4 GB limit."""
    min_host_avail_mb: float = float(os.getenv("NIGHT_MIN_HOST_AVAIL_MB", "1000"))
    min_host_avail_heavy_mb: float = float(os.getenv("NIGHT_MIN_HOST_AVAIL_HEAVY_MB", "1500"))
    min_cgroup_headroom_mb: float = float(os.getenv("NIGHT_MIN_CGROUP_HEADROOM_MB", "500"))
    min_cgroup_headroom_heavy_mb: float = float(os.getenv("NIGHT_MIN_CGROUP_HEADROOM_HEAVY_MB", "1200"))
    max_load_per_cpu: float = float(os.getenv("NIGHT_MAX_LOAD_PER_CPU", "1.5"))
    host_avail_mb: Callable[[], Optional[float]] = _read_host_avail_mb
    cgroup_headroom_mb: Callable[[], Optional[float]] = _read_cgroup_headroom_mb
    load_per_cpu: Callable[[], Optional[float]] = _read_load_per_cpu

    def check(self, job: JobSpec) -> Tuple[bool, str]:
        need = self.min_host_avail_heavy_mb if job.mem_heavy else self.min_host_avail_mb
        avail = self.host_avail_mb()
        if avail is not None and avail < need:
            return False, f"host memory available {avail:.0f} MB < {need:.0f} MB"
        need_cg = self.min_cgroup_headroom_heavy_mb if job.mem_heavy else self.min_cgroup_headroom_mb
        head = self.cgroup_headroom_mb()
        if head is not None and head < need_cg:
            return False, f"container memory headroom {head:.0f} MB < {need_cg:.0f} MB"
        load = self.load_per_cpu()
        if load is not None and load > self.max_load_per_cpu:
            return False, f"load per CPU {load:.2f} > {self.max_load_per_cpu:.2f}"
        return True, ""


# ── controller ─────────────────────────────────────────────────────────────────

async def _no_conflict(_job: JobSpec) -> Optional[str]:
    return None


async def _no_marker(_job_key: Optional[str]) -> None:
    return None


@dataclass
class Controller:
    jobs: List[JobSpec]
    store: Any
    guard: ResourceGuard = field(default_factory=ResourceGuard)
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
    conflicts: Callable[[JobSpec], Awaitable[Optional[str]]] = _no_conflict
    marker: Callable[[Optional[str]], Awaitable[None]] = _no_marker
    process_start: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # — time helpers (all comparisons in UTC; wall times built in LOCAL_TZ → DST-safe) —
    @staticmethod
    def _at(d: date, hm: Tuple[int, int]) -> datetime:
        return datetime.combine(d, time(hm[0], hm[1]), tzinfo=LOCAL_TZ).astimezone(timezone.utc)

    def night_of(self, now: datetime) -> Tuple[date, bool]:
        """(night date D, window_is_open). D is the most recent date whose window has opened."""
        d = now.astimezone(LOCAL_TZ).date()
        if now < self._at(d, WINDOW_OPEN):
            d -= timedelta(days=1)
        return d, self._at(d, WINDOW_OPEN) <= now < self._at(d, WINDOW_CLOSE)

    def next_open(self, now: datetime) -> datetime:
        d = now.astimezone(LOCAL_TZ).date()
        for _ in range(3):
            o = self._at(d, WINDOW_OPEN)
            if o > now:
                return o
            d += timedelta(days=1)
        return now + timedelta(hours=1)

    async def recover(self) -> List[str]:
        """Call once at controller start: RUNNING rows of a dead process → FAILED."""
        return await self.store.fail_interrupted(self.process_start, self.clock())

    async def _block_or_carry(self, job: JobSpec, d: date, reason: str) -> str:
        pk = job.period_key(d)
        if not job.last_eligible_night(d):
            await self.store.note(job.key, pk, f"carried to next night: {reason}")
            return "carried"
        await self.store.finish(job.key, pk, BLOCKED, reason, None, self.clock())
        logger.warning("[night_pipeline] %s %s BLOCKED — %s", job.key, pk, reason)
        return "blocked"

    async def tick(self) -> Dict[str, Any]:
        now = self.clock()
        d, window_open = self.night_of(now)

        running = await self.store.running()
        if running:                      # rule 2 — never start beside a running heavy job
            return {"action": "wait", "reason": f"{running[0]['job_key']} is RUNNING"}

        if window_open:
            for job in self.jobs:
                if job.eligible(d):
                    await self.store.ensure(job.key, job.period_key(d), now)

        carried: set = set()
        for job in self.jobs:
            if not job.eligible(d):
                continue
            pk = job.period_key(d)
            row = await self.store.get(job.key, pk)
            if not row or row["state"] != QUEUED or job.key in carried:
                continue

            if not window_open:          # rule 5 — no catch-up outside the window
                if await self._block_or_carry(job, d, "night window closed before it could start") == "carried":
                    carried.add(job.key)
                continue

            # dependencies: COMPLETED for THIS night, or it does not run
            dep_block = None
            dep_wait = None
            for dep_key in job.depends_on:
                dep = next(j for j in self.jobs if j.key == dep_key)
                drow = await self.store.get(dep_key, dep.period_key(d)) if dep.eligible(d) else None
                st = drow["state"] if drow else None
                if st == COMPLETED:
                    continue
                if st in (FAILED, BLOCKED) or st is None:
                    dep_block = f"dependency {dep_key} is {st or 'not scheduled tonight'}"
                else:
                    dep_wait = f"waiting for dependency {dep_key} ({st})"
            if dep_block:
                if await self._block_or_carry(job, d, dep_block) == "carried":
                    carried.add(job.key)
                continue
            if dep_wait:                 # cannot happen in strict order, kept as a hard guard
                await self.store.note(job.key, pk, dep_wait)
                return {"action": "wait", "job": job.key, "reason": dep_wait}

            earliest = self._at(d, job.earliest)
            if now < earliest:           # earliest-start is a constraint, not a trigger
                reason = f"earliest start {job.earliest[0]:02d}:{job.earliest[1]:02d} local not reached"
                await self.store.note(job.key, pk, reason)
                return {"action": "wait", "job": job.key, "reason": reason}

            conflict = await self.conflicts(job)
            if conflict:
                await self.store.note(job.key, pk, f"delayed: {conflict}")
                return {"action": "wait", "job": job.key, "reason": conflict}

            ok, why = self.guard.check(job)
            if not ok:
                await self.store.note(job.key, pk, f"delayed: {why}")
                return {"action": "wait", "job": job.key, "reason": why}

            if not await self.store.claim(job.key, pk, now):     # lost a race → someone else has it
                return {"action": "wait", "job": job.key, "reason": "already claimed"}
            return await self._execute(job, pk, now)

        return {"action": "idle", "reason": "window closed" if not window_open else "night complete"}

    async def _execute(self, job: JobSpec, pk: str, started: datetime) -> Dict[str, Any]:
        logger.info("[night_pipeline] START %s %s", job.key, pk)
        await self.marker(job.key)

        async def _beat() -> None:                      # keep the stand-down marker alive
            while True:
                await asyncio.sleep(POLL_S)
                await self.marker(job.key)
        beat = asyncio.create_task(_beat())
        state, reason, result = FAILED, None, None
        try:
            result = await job.run()                    # awaited to REAL completion
            ok, why = await job.verify(result, started)
            if ok:
                state = COMPLETED
            else:
                reason = f"post-condition failed: {why}"
        except asyncio.CancelledError:
            beat.cancel()
            reason = "controller task cancelled while the job was running"
            await self.store.finish(job.key, pk, FAILED, reason, None, self.clock())
            await self.marker(None)
            raise
        except Exception as exc:
            reason = f"{type(exc).__name__}: {str(exc)[:400]}"
        beat.cancel()
        await self.store.finish(job.key, pk, state, reason, result if isinstance(result, (dict, list)) else None,
                                self.clock())
        await self.marker(None)
        (logger.info if state == COMPLETED else logger.error)(
            "[night_pipeline] %s %s %s%s", state, job.key, pk, f" — {reason}" if reason else "")
        return {"action": "ran", "job": job.key, "state": state, "reason": reason}


# ── production wiring ───────────────────────────────────────────────────────────

async def _redis():
    from BACKEND_AUTH_SECURITY import get_redis
    return await get_redis()


async def running_job() -> Optional[str]:
    """Key of the pipeline job running right now (None if none) — other loops stand down on it."""
    try:
        r = await _redis()
        return (await r.get(RUNNING_MARKER_KEY)) if r else None
    except Exception:
        return None


MEM_HEAVY_JOBS = ("brand_discovery", "category_discovery")


def build_production_controller() -> Controller:
    from BACKEND_DATABASE_MODELS import async_session_factory
    from sqlalchemy import text

    async def _registry_ok(job_name: str, started: datetime) -> Tuple[bool, str]:
        async with async_session_factory() as db:
            row = (await db.execute(text(
                "SELECT status FROM job_registry WHERE job_name = :n AND started_at >= :s "
                "ORDER BY started_at DESC LIMIT 1"), {"n": job_name, "s": started})).first()
        if not row:
            return False, f"no job_registry row for {job_name} since the job started"
        return (row[0] == "completed"), f"job_registry {job_name} status={row[0]}"

    # 1 — backup
    async def run_backup_job():
        from auto_backup import run_backup
        return await run_backup()

    async def verify_backup(res, _started):
        # run_backup reports success as "ok:<path>:<tag>"; anything else ("error", "skipped",
        # "dry_run") is not a backup. The dump file must exist and be non-empty.
        bad, missing = {}, []
        for label, v in (res or {}).items():
            parts = str(v).split(":")
            if parts[0] != "ok":
                bad[label] = v
            elif len(parts) < 2 or not os.path.isfile(parts[1]) or os.path.getsize(parts[1]) == 0:
                missing.append(label)
        ok = bool(res) and not bad and not missing
        return ok, ("all databases dumped" if ok else f"not ok: {bad or ''} missing/empty file: {missing or ''}")

    # 2 — eBay fitment
    async def run_fitment_job():
        from BACKEND_API_ROUTES import run_ebay_fitment_once
        return await run_ebay_fitment_once()

    async def verify_fitment(res, _started):
        st = (res or {}).get("status")
        return st in ("ok", "partial"), f"status={st} scanned={(res or {}).get('scanned')} api_errors={(res or {}).get('api_errors')}"

    # 3 — sync_prices
    async def run_sync_job():
        from BACKEND_API_ROUTES import run_sync_prices_once
        return await run_sync_prices_once()

    async def verify_sync(res, started):
        if (res or {}).get("status") != "completed":
            return False, f"status={(res or {}).get('status')} {(res or {}).get('reason') or (res or {}).get('error') or ''}"
        return await _registry_ok("sync_prices", started)

    # 4 — REX night cycle
    async def run_rex_job():
        from catalog_scraper import run_rex_night_cycle
        return await run_rex_night_cycle()

    async def verify_rex(res, started):
        st = ((res or {}).get("scraper_cycle") or {}).get("status")
        if st == "disabled":                       # SCRAPE_ENABLED=false — nothing to verify
            return True, "scraper cycle disabled by configuration"
        if st in ("error", "skipped", None):
            return False, f"scraper cycle status={st}"
        return await _registry_ok("run_scraper_cycle", started)

    # 5 — monthly brand discovery
    async def run_brand_job():
        from catalog_scraper import run_monthly_brand_discovery
        return await run_monthly_brand_discovery()

    async def verify_brand(res, started):
        if (res or {}).get("status") != "completed":
            return False, f"status={(res or {}).get('status')} {(res or {}).get('reason') or ''}"
        return await _registry_ok("run_brand_discovery", started)

    # 6 — category discovery
    async def run_category_job():
        from catalog_scraper import _run_category_discovery
        return await _run_category_discovery()

    async def verify_category(res, started):
        if (res or {}).get("status") != "completed":
            return False, f"status={(res or {}).get('status')} {(res or {}).get('reason') or ''}"
        return await _registry_ok("category_discovery", started)

    # 7 — weekly maintenance
    async def run_weekly_job():
        from BACKEND_API_ROUTES import run_weekly_maintenance_once
        return await run_weekly_maintenance_once()

    async def verify_weekly(res, _started):
        return (res or {}).get("status") == "completed", f"status={(res or {}).get('status')} problems={(res or {}).get('problems')}"

    jobs = [
        JobSpec("auto_backup", "daily", run_backup_job, verify_backup),
        JobSpec("ebay_fitment_backfill", "daily", run_fitment_job, verify_fitment, conflict_locks=("sync_prices",)),
        JobSpec("sync_prices", "daily", run_sync_job, verify_sync, conflict_locks=("sync_prices",)),
        JobSpec("rex_night", "daily", run_rex_job, verify_rex, conflict_locks=("scraper_cycle",)),
        JobSpec("brand_discovery", "monthly", run_brand_job, verify_brand,
                earliest=_hm("BRAND_DISCOVERY_EARLIEST_IL", "03:15"),
                monthday=int(os.getenv("BRAND_DISCOVERY_MONTHDAY", "1")),
                catchup_days=int(os.getenv("BRAND_DISCOVERY_CATCHUP_DAYS", "6")),
                depends_on=("sync_prices",),
                conflict_locks=("brand_discovery", "category_discovery", "db_update_agent"), mem_heavy=True),
        JobSpec("category_discovery", "daily", run_category_job, verify_category,
                conflict_locks=("category_discovery", "brand_discovery", "db_update_agent"), mem_heavy=True),
        JobSpec("weekly_maintenance", "weekly", run_weekly_job, verify_weekly,
                earliest=_hm("WEEKLY_MAINT_EARLIEST_IL", "05:00"),
                weekday=int(os.getenv("WEEKLY_MAINT_WEEKDAY_IL", "5")),
                conflict_locks=("sync_prices", "brand_discovery", "category_discovery")),
    ]

    async def conflicts(job: JobSpec) -> Optional[str]:
        try:
            r = await _redis()
            for lk in job.conflict_locks:
                if r and await r.exists(f"autospare:lock:{lk}"):
                    return f"lock {lk} is held (a manual or agent run is in flight)"
        except Exception as exc:
            return f"lock check failed: {exc}"          # prefer waiting over an unchecked start
        try:
            import job_queue as _jq
            async with async_session_factory() as db:
                if await _jq.queue_busy(db):
                    return "backlog job queue is running a step"
                ext = (await db.execute(text(
                    "SELECT count(*) FROM pg_stat_activity WHERE application_name = 'pg_dump'"))).scalar()
                if ext:
                    return "an external pg_dump is running (backup container)"
        except Exception as exc:
            return f"db conflict check failed: {exc}"
        return None

    async def marker(job_key: Optional[str]) -> None:
        try:
            r = await _redis()
            if not r:
                return
            if job_key:
                await r.set(RUNNING_MARKER_KEY, job_key, ex=max(300, POLL_S * 5))
            else:
                await r.delete(RUNNING_MARKER_KEY)
        except Exception as exc:
            logger.warning("[night_pipeline] marker update failed: %s", exc)

    return Controller(jobs=jobs, store=PgStore(async_session_factory), conflicts=conflicts, marker=marker)


async def controller_loop() -> None:
    """Supervised task — the single automatic entry point for every heavy night job."""
    if not enabled():
        logger.info("[night_pipeline] disabled (ENVIRONMENT is not production / NIGHT_PIPELINE_ENABLED=0)")
        return
    ctl = build_production_controller()
    await ctl.store.ensure_schema()
    interrupted = await ctl.recover()
    await ctl.marker(None)                       # a marker left by a dead process is stale by definition
    if interrupted:
        logger.error("[night_pipeline] jobs interrupted by the restart marked FAILED: %s", interrupted)
    logger.info("[night_pipeline] controller started — window %02d:%02d-%02d:%02d %s, jobs: %s",
                *WINDOW_OPEN, *WINDOW_CLOSE, LOCAL_TZ, [j.key for j in ctl.jobs])
    last_reason = None
    while True:
        try:
            res = await ctl.tick()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("[night_pipeline] tick error: %s", exc)
            res = {"action": "wait", "reason": f"tick error: {exc}"}
        if res["action"] == "ran":
            last_reason = None
            await asyncio.sleep(COOLDOWN_S)
        elif res["action"] == "wait":
            if res.get("reason") != last_reason:
                last_reason = res.get("reason")
                logger.info("[night_pipeline] waiting — %s: %s", res.get("job", "-"), last_reason)
            await asyncio.sleep(POLL_S)
        else:
            last_reason = None
            now = ctl.clock()
            await asyncio.sleep(max(POLL_S, min(600.0, (ctl.next_open(now) - now).total_seconds())))


async def status(limit: int = 30) -> List[Dict[str, Any]]:
    """Recent pipeline rows, newest first (for an admin/status view)."""
    from BACKEND_DATABASE_MODELS import async_session_factory
    return await PgStore(async_session_factory)._exec(
        "SELECT job_key, period_key, state, reason, started_at, finished_at FROM night_pipeline_runs "
        "ORDER BY created_at DESC, job_key LIMIT :l", {"l": limit}, fetch=True)
