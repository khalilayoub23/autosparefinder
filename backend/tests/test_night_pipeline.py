"""
Script: tests/test_night_pipeline.py
Purpose: Behavioural proof that night_pipeline.Controller OWNS heavy-job sequencing
         (FIXES_TRACKER #57): order, completion gating, failure handling, duplicate
         prevention, monthly-once, DST, restart safety, resource guard, trigger isolation.
Method: deterministic fakes only — a mutable clock, MemoryStore (same semantics as
         PgStore: one row per (job, period), conditional claim), fake jobs that can block,
         fail, or run far longer than expected. No DB, Redis, network or live process.
Last Updated: 2026-10-03
"""
from __future__ import annotations

import ast
import asyncio
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)
import night_pipeline as np  # noqa: E402

IL = ZoneInfo("Asia/Jerusalem")


def il(y, mo, d, h, mi=0) -> datetime:
    return datetime(y, mo, d, h, mi, tzinfo=IL).astimezone(timezone.utc)


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()
        asyncio.set_event_loop(asyncio.new_event_loop())


class Clock:
    def __init__(self, t: datetime):
        self.t = t

    def __call__(self) -> datetime:
        return self.t

    def advance(self, **kw):
        self.t += timedelta(**kw)


class Rig:
    """A controller over fake jobs. `events` records ("start"|"end", job, clock time)."""

    ORDER = ["auto_backup", "ebay_fitment_backfill", "sync_prices", "rex_night",
             "brand_discovery", "category_discovery", "weekly_maintenance"]

    def __init__(self, now: datetime, store=None, durations=None):
        self.clock = Clock(now)
        self.store = store or np.MemoryStore()
        self.events = []
        self.fail = set()            # jobs whose run() raises
        self.bad_post = set()        # jobs whose post-condition fails
        self.gates = {}              # job -> asyncio.Event the run blocks on
        self.durations = durations or {}
        self.avail = 4000.0
        self.headroom = 3000.0
        self.load = 0.5
        self.locks = set()           # externally held lock names (manual runs, DB agent, AliExpress sync…)
        self.marker_log = []
        guard = np.ResourceGuard(host_avail_mb=lambda: self.avail, cgroup_headroom_mb=lambda: self.headroom,
                                 load_per_cpu=lambda: self.load)

        def mk(key):
            async def _run():
                self.events.append(("start", key, self.clock()))
                if key in self.gates:
                    await self.gates[key].wait()
                self.clock.advance(minutes=self.durations.get(key, 5))
                if key in self.fail:
                    self.events.append(("end", key, self.clock()))
                    raise RuntimeError(f"{key} blew up")
                self.events.append(("end", key, self.clock()))
                return {"status": "completed"}

            async def _verify(res, started):
                return (key not in self.bad_post), ("ok" if key not in self.bad_post else "nothing was written")
            return _run, _verify

        specs = {
            "auto_backup": dict(period="daily"),
            "ebay_fitment_backfill": dict(period="daily", conflict_locks=("sync_prices",)),
            "sync_prices": dict(period="daily", conflict_locks=("sync_prices",)),
            "rex_night": dict(period="daily", conflict_locks=("scraper_cycle",)),
            "brand_discovery": dict(period="monthly", earliest=(3, 15), monthday=1, catchup_days=6,
                                    depends_on=("sync_prices",), mem_heavy=True,
                                    conflict_locks=("brand_discovery", "category_discovery", "db_update_agent")),
            "category_discovery": dict(period="daily", mem_heavy=True,
                                       conflict_locks=("category_discovery", "brand_discovery", "db_update_agent")),
            "weekly_maintenance": dict(period="weekly", earliest=(5, 0), weekday=5,
                                       conflict_locks=("sync_prices", "brand_discovery", "category_discovery")),
        }
        jobs = []
        for key in self.ORDER:
            r, v = mk(key)
            jobs.append(np.JobSpec(key=key, run=r, verify=v, **specs[key]))

        async def conflicts(job):
            for lk in job.conflict_locks:
                if lk in self.locks:
                    return f"lock {lk} is held"
            return None

        async def marker(k):
            self.marker_log.append(k)
        self.ctl = np.Controller(jobs=jobs, store=self.store, guard=guard, clock=self.clock,
                                 conflicts=conflicts, marker=marker, process_start=now)

    async def drain(self, max_ticks=60):
        """Tick until the controller has nothing to start. Waits advance the clock 1 min."""
        out = []
        for _ in range(max_ticks):
            res = await self.ctl.tick()
            out.append(res)
            if res["action"] == "idle":
                break
            if res["action"] == "wait":
                self.clock.advance(minutes=1)
        return out

    def started(self):
        return [j for e, j, _ in self.events if e == "start"]

    def state(self, job, period_key):
        return self.store.rows[(job, period_key)]["state"]

    def t(self, kind, job):
        return next(ts for e, j, ts in self.events if e == kind and j == job)


# ── 1-4. sequencing: each job starts only after the previous one ENDED ─────────

def test_full_night_runs_in_registry_order_one_at_a_time():
    rig = Rig(il(2026, 11, 1, 1, 0), durations={"sync_prices": 72, "rex_night": 3})   # Sunday, day 1
    run(rig.drain(max_ticks=400))
    assert rig.started() == ["auto_backup", "ebay_fitment_backfill", "sync_prices", "rex_night",
                             "brand_discovery", "category_discovery"]          # no weekly: not Saturday
    for prev, nxt in zip(rig.started(), rig.started()[1:]):
        assert rig.t("end", prev) <= rig.t("start", nxt), f"{nxt} started before {prev} ended"
    # never two jobs open at the same time
    depth = 0
    for e, _, _ in rig.events:
        depth += 1 if e == "start" else -1
        assert depth <= 1


@pytest.mark.parametrize("prev,nxt", [("auto_backup", "ebay_fitment_backfill"),
                                      ("ebay_fitment_backfill", "sync_prices"),
                                      ("sync_prices", "rex_night"),
                                      ("rex_night", "brand_discovery")])
def test_next_job_waits_for_real_completion_of_previous(prev, nxt):
    async def scenario():
        rig = Rig(il(2026, 11, 1, 3, 30))          # past every earliest-start
        rig.gates[prev] = asyncio.Event()
        task = asyncio.create_task(rig.drain(max_ticks=400))
        for _ in range(50):
            await asyncio.sleep(0)
        assert rig.started()[-1] == prev, rig.started()
        # the previous job is RUNNING: a second controller tick must not start anything
        res = await rig.ctl.tick()
        assert res == {"action": "wait", "reason": f"{prev} is RUNNING"}
        assert nxt not in rig.started()
        rig.gates[prev].set()
        await task
        assert rig.t("end", prev) <= rig.t("start", nxt)
    run(scenario())


# ── 5 + 14. a slow job delays the next one — the clock never starts it ─────────

def test_delayed_sync_prices_makes_brand_discovery_wait_past_0315():
    rig = Rig(il(2026, 11, 1, 1, 0), durations={"sync_prices": 203})     # historical max
    run(rig.drain(max_ticks=600))
    assert rig.t("end", "sync_prices").astimezone(IL).strftime("%H:%M") >= "04:30"
    assert rig.t("start", "brand_discovery") >= rig.t("end", "rex_night") >= rig.t("end", "sync_prices")
    assert rig.t("start", "brand_discovery").astimezone(IL).strftime("%H:%M") > "03:15"


def test_fast_night_still_holds_brand_discovery_until_its_earliest_start():
    rig = Rig(il(2026, 11, 1, 1, 0), durations={"sync_prices": 20})
    res = run(rig.drain(max_ticks=400))
    assert rig.t("start", "brand_discovery") >= il(2026, 11, 1, 3, 15)
    waits = [r for r in res if r["action"] == "wait" and r.get("job") == "brand_discovery"]
    assert waits and "earliest start 03:15" in waits[0]["reason"]
    # strict order: category discovery did not jump the queue while brand discovery waited
    assert rig.t("start", "category_discovery") >= rig.t("end", "brand_discovery")


def test_job_running_far_beyond_its_historical_max_starts_nothing_else_and_no_burst_after():
    async def scenario():
        rig = Rig(il(2026, 11, 2, 1, 0))
        rig.gates["sync_prices"] = asyncio.Event()
        task = asyncio.create_task(rig.drain(max_ticks=400))
        for _ in range(50):
            await asyncio.sleep(0)
        assert rig.started()[-1] == "sync_prices"
        for hours in range(1, 9):                       # 8 h — 2.4× the 203-min maximum, past 07:00
            rig.clock.t = il(2026, 11, 2, 1, 10) + timedelta(hours=hours)
            assert (await rig.ctl.tick())["action"] == "wait"
            assert rig.started()[-1] == "sync_prices"
        rig.gates["sync_prices"].set()
        await task
        # the window closed while it ran: the rest is BLOCKED, not launched as a daytime burst
        assert rig.started() == ["auto_backup", "ebay_fitment_backfill", "sync_prices"]
        assert rig.state("sync_prices", "D:2026-11-02") == np.COMPLETED
        for j in ("rex_night", "category_discovery"):
            row = rig.store.rows[(j, "D:2026-11-02")]
            assert row["state"] == np.BLOCKED and "window closed" in row["reason"]
    run(scenario())


# ── 6. failures are never COMPLETED; dependents are blocked, unrelated jobs go on ─

def test_exception_and_failed_postcondition_are_FAILED_never_completed():
    rig = Rig(il(2026, 11, 10, 1, 0))
    rig.fail.add("auto_backup")
    rig.bad_post.add("ebay_fitment_backfill")
    run(rig.drain(max_ticks=200))
    b = rig.store.rows[("auto_backup", "D:2026-11-10")]
    f = rig.store.rows[("ebay_fitment_backfill", "D:2026-11-10")]
    assert b["state"] == np.FAILED and "RuntimeError" in b["reason"]
    assert f["state"] == np.FAILED and "post-condition failed: nothing was written" in f["reason"]
    # unrelated later jobs still ran, in order, one at a time
    assert rig.state("sync_prices", "D:2026-11-10") == np.COMPLETED
    assert rig.started() == ["auto_backup", "ebay_fitment_backfill", "sync_prices", "rex_night", "category_discovery"]


def test_dependent_job_is_blocked_when_its_dependency_fails():
    rig = Rig(il(2026, 11, 7, 1, 0))                    # day 7 = last catch-up night
    rig.fail.add("sync_prices")
    run(rig.drain(max_ticks=400))
    assert rig.state("sync_prices", "D:2026-11-07") == np.FAILED
    row = rig.store.rows[("brand_discovery", "M:2026-11")]
    assert row["state"] == np.BLOCKED and "dependency sync_prices is FAILED" in row["reason"]
    assert "brand_discovery" not in rig.started()
    assert rig.state("category_discovery", "D:2026-11-07") == np.COMPLETED


# ── 7 + 10. duplicates and restarts cannot produce a second execution ─────────

def test_two_controllers_on_one_store_execute_each_job_once():
    async def scenario():
        store = np.MemoryStore()
        a = Rig(il(2026, 11, 10, 1, 0), store=store)
        b = Rig(il(2026, 11, 10, 1, 0), store=store)
        a.gates["auto_backup"] = asyncio.Event()
        ta = asyncio.create_task(a.drain(max_ticks=200))
        for _ in range(20):
            await asyncio.sleep(0)
        assert (await b.ctl.tick())["action"] == "wait"       # duplicate trigger sees RUNNING
        a.gates["auto_backup"].set()
        await ta
        await b.drain(max_ticks=200)
        return a.started() + b.started()
    started = run(scenario())
    assert sorted(started) == sorted(set(started)), started


def test_claim_is_single_use_per_period():
    async def scenario():
        s = np.MemoryStore()
        now = il(2026, 11, 3, 1, 0)
        await s.ensure("sync_prices", "D:2026-11-10", now)
        await s.ensure("sync_prices", "D:2026-11-10", now)          # re-seeding is a no-op
        first = await s.claim("sync_prices", "D:2026-11-10", now)
        second = await s.claim("sync_prices", "D:2026-11-10", now)
        await s.finish("sync_prices", "D:2026-11-10", np.COMPLETED, None, None, now)
        third = await s.claim("sync_prices", "D:2026-11-10", now)
        return first, second, third, len(s.rows)
    assert run(scenario()) == (True, False, False, 1)


def test_restart_marks_interrupted_job_failed_and_reruns_nothing():
    async def scenario():
        store = np.MemoryStore()
        old = Rig(il(2026, 11, 10, 1, 0), store=store)
        old.gates["sync_prices"] = asyncio.Event()
        t = asyncio.create_task(old.drain(max_ticks=200))
        for _ in range(50):
            await asyncio.sleep(0)
        t.cancel()                                           # the process dies mid-sync
        try:
            await t
        except asyncio.CancelledError:
            pass
        store.rows[("sync_prices", "D:2026-11-10")]["state"] = np.RUNNING   # as a SIGKILL would leave it
        new = Rig(il(2026, 11, 10, 2, 0), store=store)        # new process, same night
        new.ctl.process_start = il(2026, 11, 10, 2, 0)
        interrupted = await new.ctl.recover()
        await new.drain(max_ticks=300)
        return old, new, interrupted
    old, new, interrupted = run(scenario())
    assert interrupted == ["sync_prices"]
    row = new.store.rows[("sync_prices", "D:2026-11-10")]
    assert row["state"] == np.FAILED and row["reason"] == "interrupted by backend restart"
    assert not set(old.started()) & set(new.started()), "a job ran twice across the restart"
    assert new.started() == ["rex_night", "category_discovery"]


# ── 8. monthly brand discovery: exactly once per month, deterministic catch-up ─

def _night(rig, d: date):
    rig.clock.t = il(d.year, d.month, d.day, 1, 0)
    return rig.drain(max_ticks=500)


def test_brand_discovery_runs_exactly_once_per_month_over_a_year():
    rig = Rig(il(2026, 10, 30, 1, 0))
    d = date(2026, 10, 30)
    async def year():
        nonlocal d
        while d < date(2027, 11, 3):
            await _night(rig, d)
            d += timedelta(days=1)
    run(year())
    months = [ts.astimezone(IL).strftime("%Y-%m") for e, j, ts in rig.events if e == "start" and j == "brand_discovery"]
    assert months == sorted(set(months)) and len(months) == 13          # 2026-11 … 2027-11
    days = {ts.astimezone(IL).day for e, j, ts in rig.events if e == "start" and j == "brand_discovery"}
    assert days == {1}
    for e, j, ts in rig.events:
        if e == "start" and j == "brand_discovery":
            assert ts.astimezone(IL).strftime("%H:%M") >= "03:15"


def test_missed_day_one_catches_up_once_and_only_once():
    rig = Rig(il(2026, 11, 1, 1, 0))
    rig.fail.add("sync_prices")                              # day 1: dependency fails
    run(_night(rig, date(2026, 11, 1)))
    row = rig.store.rows[("brand_discovery", "M:2026-11")]
    assert row["state"] == np.QUEUED and "carried to next night" in row["reason"]
    rig.fail.clear()
    run(_night(rig, date(2026, 11, 2)))                      # day 2: runs
    run(_night(rig, date(2026, 11, 3)))                      # day 3: must NOT run again
    starts = [ts.astimezone(IL).day for e, j, ts in rig.events if e == "start" and j == "brand_discovery"]
    assert starts == [2]


def test_backend_down_for_the_whole_window_runs_nothing_and_creates_no_backlog():
    rig = Rig(il(2026, 11, 10, 9, 30))                        # process comes up after the 07:00 close
    res = run(rig.drain())
    assert res[-1]["action"] == "idle" and rig.started() == [] and rig.store.rows == {}
    run(_night(rig, date(2026, 11, 11)))                     # next night: ONE normal night, no doubles
    assert rig.started().count("sync_prices") == 1 and rig.started().count("auto_backup") == 1


def test_late_start_inside_the_window_runs_in_order_then_blocks_the_rest_at_close():
    rig = Rig(il(2026, 11, 10, 6, 40), durations={"auto_backup": 10, "ebay_fitment_backfill": 15})
    run(rig.drain(max_ticks=200))
    assert rig.started() == ["auto_backup", "ebay_fitment_backfill"]
    assert rig.state("sync_prices", "D:2026-11-10") == np.BLOCKED


def test_weekly_job_only_on_saturday_and_not_before_0500():
    rig = Rig(il(2026, 11, 7, 1, 0))                         # Saturday
    run(rig.drain(max_ticks=500))
    assert rig.t("start", "weekly_maintenance") >= il(2026, 11, 7, 5, 0)
    assert rig.started()[-1] == "weekly_maintenance"
    rig2 = Rig(il(2026, 11, 8, 1, 0))                        # Sunday
    run(rig2.drain(max_ticks=500))
    assert "weekly_maintenance" not in rig2.started()


# ── 9. DST ─────────────────────────────────────────────────────────────────────

def test_window_and_earliest_start_follow_israel_wall_clock_across_dst():
    ctl = Rig(il(2026, 10, 24, 12, 0)).ctl
    # summer (IDT, UTC+3) vs winter (IST, UTC+2): same wall clock, different UTC
    assert ctl._at(date(2026, 10, 24), (1, 0)) == datetime(2026, 10, 23, 22, 0, tzinfo=timezone.utc)
    assert ctl._at(date(2026, 10, 26), (1, 0)) == datetime(2026, 10, 25, 23, 0, tzinfo=timezone.utc)
    assert ctl._at(date(2026, 11, 1), (3, 15)) == datetime(2026, 11, 1, 1, 15, tzinfo=timezone.utc)
    assert ctl._at(date(2027, 7, 1), (3, 15)) == datetime(2027, 7, 1, 0, 15, tzinfo=timezone.utc)
    # fall-back night (01:00-02:00 occurs twice): exactly one night row set, no double run
    rig = Rig(il(2026, 10, 25, 1, 0))
    async def two_passes():
        await rig.drain(max_ticks=300)
        rig.clock.t = datetime(2026, 10, 24, 23, 30, tzinfo=timezone.utc)     # 01:30 IST — the repeated hour
        await rig.drain(max_ticks=300)
    run(two_passes())
    assert rig.started().count("auto_backup") == 1 and rig.started().count("sync_prices") == 1
    # spring-forward night
    rig3 = Rig(il(2027, 3, 26, 1, 0))
    run(rig3.drain(max_ticks=300))
    assert rig3.started().count("sync_prices") == 1
    assert ctl.night_of(il(2026, 11, 3, 0, 59)) == (date(2026, 11, 2), False)
    assert ctl.night_of(il(2026, 11, 3, 1, 0)) == (date(2026, 11, 3), True)
    assert ctl.night_of(il(2026, 11, 3, 7, 0)) == (date(2026, 11, 3), False)


# ── 11. resource guard ─────────────────────────────────────────────────────────

def test_low_host_memory_delays_the_next_job_and_records_why():
    rig = Rig(il(2026, 11, 10, 1, 0))
    rig.avail = 600.0
    res = run(rig.ctl.tick())
    assert res["action"] == "wait" and "host memory available 600 MB < 1000 MB" in res["reason"]
    assert rig.started() == []
    assert "delayed: host memory available" in rig.store.rows[("auto_backup", "D:2026-11-10")]["reason"]
    rig.avail = 4000.0                                         # pressure gone → it runs on the re-check
    assert run(rig.ctl.tick())["action"] == "ran"


def test_memory_heavy_jobs_need_more_headroom_than_light_ones():
    rig = Rig(il(2026, 11, 10, 1, 0))
    rig.avail, rig.headroom = 1200.0, 800.0                    # fine for light jobs, not for discovery
    res = run(rig.drain(max_ticks=40))
    assert rig.started() == ["auto_backup", "ebay_fitment_backfill", "sync_prices", "rex_night"]
    assert any("host memory available 1200 MB < 1500 MB" in (r.get("reason") or "") for r in res)
    assert rig.state("category_discovery", "D:2026-11-10") == np.QUEUED


def test_cpu_pressure_and_cgroup_headroom_delay_too():
    rig = Rig(il(2026, 11, 10, 1, 0))
    rig.load = 2.4
    assert "load per CPU 2.40 > 1.50" in run(rig.ctl.tick())["reason"]
    rig.load, rig.headroom = 0.5, 100.0
    assert "container memory headroom 100 MB < 500 MB" in run(rig.ctl.tick())["reason"]
    assert rig.started() == []


def test_resource_pressure_never_kills_or_cancels_a_running_job():
    async def scenario():
        rig = Rig(il(2026, 11, 10, 1, 0))
        rig.gates["auto_backup"] = asyncio.Event()
        t = asyncio.create_task(rig.drain(max_ticks=200))
        for _ in range(20):
            await asyncio.sleep(0)
        rig.avail = 50.0                                       # severe pressure while a job runs
        for _ in range(5):
            assert (await rig.ctl.tick())["action"] == "wait"
        assert not t.done()
        rig.avail = 4000.0
        rig.gates["auto_backup"].set()
        await t
        return rig
    rig = run(scenario())
    assert rig.state("auto_backup", "D:2026-11-10") == np.COMPLETED


# ── 12. protected / external workloads are only ever waited for ───────────────

def test_external_lock_holder_makes_the_controller_wait_not_interfere():
    rig = Rig(il(2026, 11, 10, 1, 0))
    rig.locks.add("sync_prices")                # a manual eBay/AliExpress sync is in flight
    res = run(rig.drain(max_ticks=30))
    assert rig.started() == ["auto_backup"]     # fitment and sync both wait for that lock
    assert any("lock sync_prices is held" in (r.get("reason") or "") for r in res)
    assert rig.locks == {"sync_prices"}         # the controller never touched the foreign lock
    rig.locks.clear()
    run(rig.drain(max_ticks=300))
    assert rig.started()[:3] == ["auto_backup", "ebay_fitment_backfill", "sync_prices"]


def test_db_agent_lock_delays_only_memory_heavy_jobs():
    rig = Rig(il(2026, 11, 10, 1, 0))
    rig.locks.add("db_update_agent")
    run(rig.drain(max_ticks=40))
    assert rig.started() == ["auto_backup", "ebay_fitment_backfill", "sync_prices", "rex_night"]
    assert "lock db_update_agent is held" in rig.store.rows[("category_discovery", "D:2026-11-10")]["reason"]


def test_controller_source_cannot_kill_restart_or_cancel_other_workloads():
    src = re.sub(r"#[^\n]*", "", open(os.path.join(BACKEND, "night_pipeline.py"), encoding="utf-8").read())
    code = re.sub(r'"""[\s\S]*?"""', "", src)
    for forbidden in ("os.kill", ".terminate(", ".kill(", "SIGTERM", "SIGKILL", "subprocess", "docker",
                      "os.system", "pkill", "DELETE FROM", "TRUNCATE"):
        assert forbidden not in code, forbidden
    assert set(re.findall(r"(\w+)\.cancel\(\)", code)) == {"beat"}      # only its own marker heartbeat
    # the only Redis key it deletes is its own running-marker
    assert re.findall(r"r\.delete\(([^)]*)\)", code) == ["RUNNING_MARKER_KEY"]


def test_running_marker_is_set_during_a_job_and_cleared_after():
    rig = Rig(il(2026, 11, 10, 1, 0))
    run(rig.ctl.tick())
    assert rig.marker_log[0] == "auto_backup" and rig.marker_log[-1] is None


# ── 13. bypass audit: nothing but the controller starts a heavy job ───────────

def _call_sites(func: str) -> dict:
    found: dict = {}
    for root, dirs, files in os.walk(BACKEND):
        dirs[:] = [d for d in dirs if d not in ("tests", "devtests", "__pycache__", "legacy", "node_modules", "state", "archive")]
        for f in files:
            if not f.endswith(".py"):
                continue
            path = os.path.join(root, f)
            try:
                tree = ast.parse(open(path, encoding="utf-8", errors="ignore").read())
            except SyntaxError:
                continue
            for fn in ast.walk(tree):
                if not isinstance(fn, (ast.AsyncFunctionDef, ast.FunctionDef)):
                    continue
                for node in ast.walk(fn):
                    if isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", None)) == func:
                        nested = [x for x in ast.walk(fn) if isinstance(x, (ast.AsyncFunctionDef, ast.FunctionDef))
                                  and x is not fn and any(n is node for n in ast.walk(x))]
                        if not nested:
                            found.setdefault(os.path.relpath(path, BACKEND), []).append(fn.name)
    return {k: sorted(v) for k, v in found.items()}


@pytest.mark.parametrize("func,allowed", [
    ("run_ebay_fitment_once", {"night_pipeline.py": ["run_fitment_job"]}),
    ("run_sync_prices_once", {"night_pipeline.py": ["run_sync_job"]}),
    ("run_rex_night_cycle", {"night_pipeline.py": ["run_rex_job"]}),
    ("run_monthly_brand_discovery", {"night_pipeline.py": ["run_brand_job"]}),
    ("_run_category_discovery", {"night_pipeline.py": ["run_category_job"]}),
    ("run_weekly_maintenance_once", {"night_pipeline.py": ["run_weekly_job"]}),
    ("run_scraper_cycle", {"catalog_scraper.py": ["run_rex_night_cycle"], "routes/admin.py": ["_run"]}),   # + manual admin trigger
    ("resolve_inactive_parts_with_oem_lookup", {"catalog_scraper.py": ["run_rex_night_cycle"]}),
    ("run_transport_pipeline_if_due", {"catalog_scraper.py": ["run_rex_night_cycle"]}),
    ("controller_loop", {"BACKEND_API_ROUTES.py": ["startup"]}),
])
def test_heavy_job_has_exactly_one_automatic_caller(func, allowed):
    assert _call_sites(func) == allowed


def test_run_backup_is_called_only_by_the_controller_and_the_backup_tool_itself():
    sites = _call_sites("run_backup")
    assert sites.pop("night_pipeline.py") == ["run_backup_job"]
    assert set(sites) <= {"auto_backup.py", "routes/admin.py"}, sites       # manual restore/backup tooling only


def test_sync_prices_agent_method_is_reached_only_via_controller_or_admin():
    sites = _call_sites("sync_prices")
    assert sites == {"BACKEND_API_ROUTES.py": ["run_sync_prices_once"], "routes/admin.py": sites.get("routes/admin.py")}
    assert sites["routes/admin.py"], "the manual admin trigger should still exist"


def test_no_time_based_loop_or_todo_path_remains_for_heavy_jobs():
    routes = open(os.path.join(BACKEND, "BACKEND_API_ROUTES.py"), encoding="utf-8").read()
    startup = routes[routes.index("async def startup():"):routes.index("# ── Social Feedback Loop")]
    regs = re.findall(r'_supervised_task\("([^"]+)"', startup)
    assert regs.count("night_pipeline") == 1
    for gone in ("price_sync_loop", "backup_loop", "ebay_fitment_backfill_loop", "brand_discovery_monthly", "weekly_maintenance"):
        assert gone not in regs, gone
    da = re.sub(r"#[^\n]*", "", open(os.path.join(BACKEND, "db_update_agent.py"), encoding="utf-8").read())
    for heavy in ("run_brand_discovery", "run_scraper_cycle", "_run_category_discovery", "sync_prices(",
                  "run_backup", "run_weekly_maintenance_once", "run_ebay_fitment_once"):
        assert heavy not in da, heavy
    assert "running_job()" in da                 # and it stands down for memory-heavy pipeline jobs


def test_production_registry_order_dependencies_and_environment_gate(monkeypatch):
    ctl = np.build_production_controller()
    assert [j.key for j in ctl.jobs] == Rig.ORDER
    jobs = {j.key: j for j in ctl.jobs}
    assert jobs["brand_discovery"].depends_on == ("sync_prices",)
    assert (jobs["weekly_maintenance"].period, jobs["weekly_maintenance"].weekday, jobs["weekly_maintenance"].earliest) == ("weekly", 5, (5, 0))
    assert jobs["category_discovery"].mem_heavy and not jobs["sync_prices"].mem_heavy
    monkeypatch.delenv("NIGHT_PIPELINE_ENABLED", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "production")
    assert np.enabled()
    monkeypatch.delenv("ENVIRONMENT")                 # the sandbox backend has no ENVIRONMENT
    assert not np.enabled()


def test_restart_resume_hook_cannot_relaunch_controller_owned_work():
    # container_start.sh / post_restart.sh relaunch whatever scripts/restart_workers.py captured.
    # Brand-discovery children must be stop-only, or a restart would run part of a heavy job
    # outside the controller.
    sys.path.insert(0, os.path.join(BACKEND, "scripts"))
    import restart_workers as rw
    for owned in ("oem_parts_online_scraper.py", "oempartsonline_importer.py"):
        assert rw._kind(owned) == "stop_only", owned               # still stopped gracefully
    assert rw._kind("catalog_scraper.py") is None                  # never captured, never resumed
    for heavy in ("night_pipeline.py", "auto_backup.py", "merge_master_parts.py", "ebay_fitment_backfill.py"):
        assert rw._kind(heavy) is None, heavy


# ── #58 final validation ───────────────────────────────────────────────────────
import types  # noqa: E402

CS_PATH = os.path.join(BACKEND, "catalog_scraper.py")
ROUTES_PATH = os.path.join(BACKEND, "BACKEND_API_ROUTES.py")


def _load_fn(path, name, ns):
    src = open(path, encoding="utf-8").read()
    node = next(n for n in ast.walk(ast.parse(src)) if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef)) and n.name == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), path, "exec"), ns)
    return ns[name]


class _FakeRedis:
    def __init__(self):
        self.kv = {}

    async def set(self, k, v, nx=False, ex=None, **kw):
        if nx and k in self.kv:
            return None
        self.kv[k] = v
        return True

    async def get(self, k):
        return self.kv.get(k)

    async def incr(self, k):
        self.kv[k] = int(self.kv.get(k) or 0) + 1
        return self.kv[k]

    async def delete(self, k):
        return int(self.kv.pop(k, None) is not None)

    async def exists(self, k):
        return int(k in self.kv)

    async def eval(self, script, numkeys, key, token):
        if self.kv.get(key) == token:
            del self.kv[key]
            return 1
        return 0


# — backup boundary: the external pg_dump is waited for, never fought —

def test_external_pg_dump_delays_the_next_job_until_it_is_gone():
    rig = Rig(il(2026, 11, 10, 5, 0))                 # 03:00 UTC = the backup containers' cron slot (IST)
    dump = {"on": True}

    async def conflicts(job):
        return "an external pg_dump is running (backup container)" if dump["on"] else None
    rig.ctl.conflicts = conflicts
    res = run(rig.drain(max_ticks=6))
    assert rig.started() == [] and all(r["action"] == "wait" for r in res)
    assert "external pg_dump" in rig.store.rows[("auto_backup", "D:2026-11-10")]["reason"]
    dump["on"] = False
    assert run(rig.ctl.tick())["action"] == "ran"


def test_production_conflict_check_covers_pg_dump_job_queue_and_locks():
    src = open(os.path.join(BACKEND, "night_pipeline.py"), encoding="utf-8").read()
    body = src[src.index("    async def conflicts(job: JobSpec)"):src.index("    async def marker(job_key")]
    assert "application_name = 'pg_dump'" in body and "queue_busy" in body and "autospare:lock:" in body
    assert body.count("return f\"") >= 2 and "prefer waiting" in body       # a failed check waits, it does not start


def test_backup_postcondition_requires_ok_status_and_a_real_file(tmp_path):
    job = next(j for j in np.build_production_controller().jobs if j.key == "auto_backup")
    good = tmp_path / "autospare_20261110_010000.sql"
    good.write_bytes(b"PGDMP" + b"x" * 100)
    empty = tmp_path / "autospare_pii_20261110_010000.sql"
    empty.write_bytes(b"")
    now = il(2026, 11, 10, 1, 0)
    assert run(job.verify({"autospare": f"ok:{good}:daily"}, now))[0] is True
    assert run(job.verify({"autospare": f"ok:{good}:daily", "autospare_pii": "error"}, now))[0] is False
    assert run(job.verify({"autospare": f"ok:{good}:daily", "autospare_pii": "skipped"}, now))[0] is False
    assert run(job.verify({"autospare": f"ok:{empty}:daily"}, now))[0] is False
    assert run(job.verify({"autospare": f"ok:{tmp_path / 'missing.sql'}:daily"}, now))[0] is False
    assert run(job.verify({}, now))[0] is False


# — restart-mid-job: backup leaves no fake "latest" dump; fitment repeats its batch —

def test_interrupted_backup_never_leaves_a_dump_under_its_final_name(tmp_path, monkeypatch):
    import auto_backup as ab
    monkeypatch.setattr(ab, "BACKUP_DIR", str(tmp_path))
    monkeypatch.setattr(ab, "DATABASE_URL", "postgresql://u:p@h/autospare")
    monkeypatch.setattr(ab, "PII_DATABASE_URL", "postgresql://u:p@h/autospare_pii")
    monkeypatch.setattr(ab, "_tag_backup", lambda p: "daily")
    monkeypatch.setattr(ab, "_prune_old_backups_smart", lambda label: None)
    stale = tmp_path / "autospare_20261109_010000.sql.partial"      # left by a killed run
    stale.write_bytes(b"truncated")
    seen = []

    def fake_dump(url, out_path):
        seen.append(out_path)
        open(out_path, "wb").write(b"PGDMP-data")
        return "pii" not in url                                     # the PII dump "fails"
    monkeypatch.setattr(ab, "_pg_dump", fake_dump)
    res = run(ab.run_backup())
    assert all(p.endswith(".sql.partial") for p in seen), "pg_dump must write to the temp name"
    assert not stale.exists(), "leftover of an interrupted run must be cleaned"
    files = sorted(f.name for f in tmp_path.iterdir())
    assert len([f for f in files if f.startswith("autospare_2") and f.endswith(".sql")]) == 1
    assert not [f for f in files if f.endswith(".partial")]
    assert not [f for f in files if f.startswith("autospare_pii_") and f.endswith(".sql")]
    assert res["autospare"].startswith("ok:") and res["autospare_pii"] == "error"


def _fitment(run_backfill, redis):
    auth = types.ModuleType("BACKEND_AUTH_SECURITY")

    async def _gr():
        return redis
    auth.get_redis = _gr
    mod = types.ModuleType("ebay_fitment_backfill")
    mod.run_backfill = run_backfill
    saved = {k: sys.modules.get(k) for k in ("BACKEND_AUTH_SECURITY", "ebay_fitment_backfill")}
    sys.modules["BACKEND_AUTH_SECURITY"], sys.modules["ebay_fitment_backfill"] = auth, mod
    try:
        fn = _load_fn(ROUTES_PATH, "run_ebay_fitment_once", {"print": lambda *a, **k: None})
        return run(fn())
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


def test_fitment_offset_advances_only_after_a_completed_batch():
    r = _FakeRedis()
    offsets = []

    async def ok(dry_run, limit, offset):
        offsets.append(offset)
        return {"status": "ok", "scanned": limit}

    async def boom(dry_run, limit, offset):
        offsets.append(offset)
        raise RuntimeError("backend restarted mid-batch")
    _fitment(ok, r)
    with pytest.raises(RuntimeError):
        _fitment(boom, r)                       # interrupted: counter must NOT move
    _fitment(ok, r)                             # the same batch is repeated
    assert offsets == [0, 500, 500] and r.kv["autospare:ebay_fitment:run"] == 2


# — category discovery: bounded even though its runtime was never measured —

def _category(monkeypatch, counts=None, env=None, avail=5000.0, manufacturers=("Aion", "Kia", "Toyota")):
    calls = {"sku_loads": [], "searches": [], "counts": []}
    counts = counts or {}

    class _Res:
        def __init__(self, rows=None, scalar=None):
            self._rows, self._scalar = rows or [], scalar

        def fetchall(self):
            return self._rows

        def scalar(self):
            return self._scalar

    class _S:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def execute(self, sql, params=None):
            sql = str(sql)
            if "DISTINCT manufacturer" in sql:
                return _Res(rows=[(m,) for m in sorted(manufacturers)])
            if "SELECT sku" in sql:
                calls["sku_loads"].append(params["m"])
                return _Res(rows=[("A-1",), ("B 2",)])
            if "COUNT(*)" in sql:
                calls["counts"].append((params["m"], params["c"]))
                return _Res(scalar=counts.get((params["m"], params["c"]), 5))
            return _Res()

        async def commit(self):
            pass

        async def rollback(self):
            pass

    async def _search(query, manufacturer, category_he, max_parts=20):
        calls["searches"].append((manufacturer, category_he, query))
        return []

    async def _noop(*a, **k):
        return "jid"
    for k, v in (env or {}).items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(np, "_read_host_avail_mb", lambda: avail)
    redis = _FakeRedis()
    auth = types.ModuleType("BACKEND_AUTH_SECURITY")

    async def _gr():
        return redis
    auth.get_redis = _gr
    saved = sys.modules.get("BACKEND_AUTH_SECURITY")
    sys.modules["BACKEND_AUTH_SECURITY"] = auth
    import uuid as _uuid
    from typing import Any, Dict, Optional
    ns = {"Any": Any, "Dict": Dict, "Optional": Optional, "asyncio": asyncio, "os": os, "uuid": _uuid,
          "datetime": datetime, "random": types.SimpleNamespace(uniform=lambda a, b: 0.0),
          "text": lambda s: s, "scraper_session_factory": _S, "CATEGORY_DISCOVERY_ENABLED": True,
          "CATEGORY_DISCOVERY_INTERVAL_H": 12, "CATEGORY_MIN_PARTS_PER_BRAND_CATEGORY": 3,
          "DISCOVERY_CATEGORIES": [("בלמים", ["brake pad", "brake disc"]), ("מצבר", ["car battery"])],
          "SCRAPE_REQUEST_DELAY": 0.0, "_OEM_DISCOVERY_SOURCES": set(),
          "_search_category_query_multisource": _search,
          "categorize_on_ingest": lambda he: {"בלמים": "brakes", "מצבר": "electrical"}[he],
          "db_upsert_part": _noop, "resolve_aftermarket_brand": _noop,
          "job_registry_start": _noop, "job_heartbeat": _noop, "job_registry_finish": _noop,
          "print": lambda *a, **k: None}
    try:
        fn = _load_fn(CS_PATH, "_run_category_discovery", ns)
        report = run(fn())
    finally:
        if saved is None:
            sys.modules.pop("BACKEND_AUTH_SECURITY", None)
        else:
            sys.modules["BACKEND_AUTH_SECURITY"] = saved
    return report, calls, redis


def test_category_discovery_skips_covered_pairs_without_loading_skus_or_searching(monkeypatch):
    report, calls, redis = _category(monkeypatch)                   # every pair already has 5 parts
    assert report["status"] == "completed" and "truncated" not in report
    assert calls["sku_loads"] == [] and calls["searches"] == []
    assert {c for _, c in calls["counts"]} == {"brakes", "electrical"}   # counts by SLUG, never Hebrew
    assert "autospare:lock:category_discovery" not in redis.kv          # lock released


def test_category_discovery_searches_only_short_pairs_and_loads_skus_once(monkeypatch):
    report, calls, _ = _category(monkeypatch, counts={("Kia", "brakes"): 0, ("Kia", "electrical"): 1})
    assert calls["sku_loads"] == ["Kia"]                             # lazily, once, only for the short manufacturer
    assert {m for m, _, _ in calls["searches"]} == {"Kia"} and len(calls["searches"]) == 3   # 2 + 1 search terms
    assert report["status"] == "completed"


def test_category_discovery_stops_cleanly_when_its_time_budget_is_spent(monkeypatch):
    report, calls, redis = _category(monkeypatch, counts={("Kia", "brakes"): 0},
                                     env={"CATEGORY_DISCOVERY_MAX_RUNTIME_S": "-1"})
    assert report["status"] == "completed" and "time budget" in report["truncated"]
    assert calls["searches"] == [] and calls["counts"] == []
    assert "autospare:lock:category_discovery" not in redis.kv


def test_category_discovery_stops_cleanly_under_memory_pressure(monkeypatch):
    report, calls, _ = _category(monkeypatch, counts={("Kia", "brakes"): 0}, avail=300.0)
    assert "host memory available 300 MB < 800 MB" in report["truncated"]
    assert calls["searches"] == [] and report["status"] == "completed"


def test_category_discovery_is_memory_heavy_and_guarded_at_start():
    job = next(j for j in np.build_production_controller().jobs if j.key == "category_discovery")
    assert job.mem_heavy and "db_update_agent" in job.conflict_locks
    g = np.ResourceGuard(host_avail_mb=lambda: 1400.0, cgroup_headroom_mb=lambda: 3000.0, load_per_cpu=lambda: 0.2)
    assert g.check(job) == (False, "host memory available 1400 MB < 1500 MB")


# — brand discovery catch-up: day 1 normal, days 2-7 once, never after day 7, no burst —

def test_brand_discovery_is_never_started_after_day_7():
    rig = Rig(il(2026, 11, 1, 1, 0))
    rig.fail.add("sync_prices")                       # dependency fails every night of the window
    async def week():
        for day in range(1, 8):
            await _night(rig, date(2026, 11, day))
    run(week())
    row = rig.store.rows[("brand_discovery", "M:2026-11")]
    assert row["state"] == np.BLOCKED and "dependency sync_prices is FAILED" in row["reason"]
    rig.fail.clear()
    async def rest_of_month():
        for day in range(8, 31):
            await _night(rig, date(2026, 11, day))
    run(rest_of_month())
    assert "brand_discovery" not in rig.started()
    assert [k for k in rig.store.rows if k[0] == "brand_discovery"] == [("brand_discovery", "M:2026-11")]


def test_first_start_on_day_8_does_not_queue_that_month():
    rig = Rig(il(2026, 11, 8, 1, 0))
    run(rig.drain(max_ticks=400))
    assert "brand_discovery" not in rig.started()
    assert not [k for k in rig.store.rows if k[0] == "brand_discovery"]


def test_months_of_downtime_produce_one_run_not_a_burst():
    rig = Rig(il(2026, 10, 1, 1, 0))
    run(_night(rig, date(2026, 10, 1)))               # October run
    # backend is down for November and December entirely; it returns on 2 January
    run(_night(rig, date(2027, 1, 2)))
    run(_night(rig, date(2027, 1, 3)))
    months = [ts.astimezone(IL).strftime("%Y-%m") for e, j, ts in rig.events if e == "start" and j == "brand_discovery"]
    assert months == ["2026-10", "2027-01"]           # no runs "owed" for the missed months
    assert sorted(k[1] for k in rig.store.rows if k[0] == "brand_discovery") == ["M:2026-10", "M:2027-01"]


def test_brand_discovery_interrupted_by_restart_is_not_rerun_in_the_catch_up_days():
    async def scenario():
        store = np.MemoryStore()
        old = Rig(il(2026, 11, 1, 1, 0), store=store)
        old.gates["brand_discovery"] = asyncio.Event()
        t = asyncio.create_task(old.drain(max_ticks=500))
        for _ in range(3000):
            await asyncio.sleep(0)
            if old.started() and old.started()[-1] == "brand_discovery":
                break
        assert old.started()[-1] == "brand_discovery"
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass
        store.rows[("brand_discovery", "M:2026-11")]["state"] = np.RUNNING     # as a hard kill leaves it
        new = Rig(il(2026, 11, 1, 5, 30), store=store)
        new.ctl.process_start = il(2026, 11, 1, 5, 30)
        assert await new.ctl.recover() == ["brand_discovery"]
        for day in (1, 2, 3, 7):
            await _night(new, date(2026, 11, day)) if day > 1 else await new.drain(max_ticks=300)
        return store, new
    store, new = run(scenario())
    row = store.rows[("brand_discovery", "M:2026-11")]
    assert row["state"] == np.FAILED and row["reason"] == "interrupted by backend restart"
    assert "brand_discovery" not in new.started()
