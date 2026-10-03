"""
Script: tests/test_overnight_schedule.py
Purpose: Regression guard for the overnight scheduling window and the harvester audit
         (FIXES_TRACKER #52 + #53, 2026-10-03).
Covers:
  1. local_schedule — Israel wall-clock slots are DST-correct (2026-10-25 fall-back,
     2027-03-26 spring-forward): no duplicate day, no skipped day, exact wall time.
  2. Each scheduled loop is registered exactly once in startup(); moved loops have no
     restart-anchored sleep(86400)/sleep(interval) left.
  3. The overnight slots and the daytime group scan cannot overlap, using runtimes
     measured in job_registry / logs (sync_prices max 203 min, group scan ~3.1 h).
  4. _INPROC_JOB_LOCKS is the ONLY job→lock map and names exactly the locks the
     in-process jobs acquire (it had drifted: "price_sync" vs real "sync_prices").
  5. _reconcile_orphaned_jobs frees every in-process lock even when 0 rows are still
     'running' (pre_restart.sh had already superseded them → brand_discovery leaked 24 h).
  6. cpie_full_seed retries in 1 h on ANY failure (rc != 0), not only on fast failures.
No DB, Redis, network or live server is touched.
Last Updated: 2026-10-03
"""
from __future__ import annotations

import ast
import asyncio
import os
import re
import sys
import types
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)
import local_schedule as ls  # noqa: E402

IL = ZoneInfo("Asia/Jerusalem")


def _run_coro(coro):
    """Run a coroutine WITHOUT leaving the thread with no current event loop.
    _run_coro() sets the current loop to None on exit (Python 3.11), which breaks any
    later test in the same session that calls asyncio.get_event_loop()
    (e.g. tests/test_flaresolverr_health.py)."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()
        asyncio.set_event_loop(asyncio.new_event_loop())
ROUTES = os.path.join(BACKEND, "BACKEND_API_ROUTES.py")
SRC = open(ROUTES, encoding="utf-8").read()
TREE = ast.parse(SRC)


def _func(name: str) -> ast.AsyncFunctionDef:
    for node in ast.walk(TREE):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{name} not found")


def _func_src(name: str) -> str:
    return ast.get_source_segment(SRC, _func(name))


# ── 1. DST-correct Israel wall-clock scheduling ─────────────────────────────────

@pytest.mark.parametrize("hour,minute,runtime_min", [(1, 0, 5), (1, 15, 70), (1, 15, 203), (10, 0, 190)])
def test_daily_slot_exact_wall_time_no_dup_no_skip_across_dst(hour, minute, runtime_min):
    t = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
    end = datetime(2027, 4, 30, tzinfo=timezone.utc)
    seen: dict[date, int] = {}
    while t < end:
        nxt = ls.next_run_utc(hour, minute, None, t)
        loc = nxt.astimezone(IL)
        assert (loc.hour, loc.minute) == (hour, minute), loc
        seen[loc.date()] = seen.get(loc.date(), 0) + 1
        t = nxt + timedelta(minutes=runtime_min)
    days = sorted(seen)
    assert all(c == 1 for c in seen.values())
    assert all((b - a).days == 1 for a, b in zip(days, days[1:]))
    assert date(2026, 10, 25) in seen and date(2027, 3, 26) in seen


def test_dst_offsets_are_real_israel_offsets():
    # 01:15 IDT (UTC+3) before the fall-back, 01:15 IST (UTC+2) after it.
    a = ls.next_run_utc(1, 15, None, datetime(2026, 10, 24, 12, tzinfo=timezone.utc))
    b = ls.next_run_utc(1, 15, None, datetime(2026, 10, 25, 12, tzinfo=timezone.utc))
    assert a == datetime(2026, 10, 24, 22, 15, tzinfo=timezone.utc)
    assert b == datetime(2026, 10, 25, 23, 15, tzinfo=timezone.utc)


def test_weekly_saturday_slot():
    t = datetime(2026, 10, 3, 12, tzinfo=timezone.utc)
    for _ in range(60):
        n = ls.next_run_utc(5, 0, 5, t)
        loc = n.astimezone(IL)
        assert (loc.weekday(), loc.hour, loc.minute) == (5, 5, 0)
        assert n - t <= timedelta(days=7, hours=1)
        t = n + timedelta(minutes=20)


def test_seconds_until_never_zero_or_negative():
    now = datetime(2026, 10, 3, 22, 0, tzinfo=timezone.utc)  # exactly 01:00 IDT
    assert ls.seconds_until(1, 0, None, now) == pytest.approx(24 * 3600)


# ── 2. Single registration; no restart-anchored sleeps in scheduled loops ───────

REMOVED_CLOCK_LOOPS = ("price_sync_loop", "backup_loop", "ebay_fitment_backfill_loop",
                       "brand_discovery_monthly", "weekly_maintenance")


def test_night_pipeline_is_the_single_heavy_job_registration():
    startup = _func_src("startup")
    assert len(re.findall(r'_supervised_task\("night_pipeline",\s*_night_pipeline\.controller_loop\(\)\)', startup)) == 1
    for name in REMOVED_CLOCK_LOOPS:                       # #57: no independent clock loop remains
        assert f'_supervised_task("{name}"' not in startup, name
    for fn in ("_price_sync_loop", "_backup_loop", "_ebay_fitment_backfill_loop",
               "_brand_discovery_monthly_loop", "_weekly_maintenance_loop"):
        assert fn not in SRC, fn
    assert startup.count("start_scraper_task()") == 1
    assert startup.count("start_db_agent(") == 1
    names = re.findall(r'_supervised_task\("([^"]+)"', startup)
    assert len(names) == len(set(names)), "duplicate supervised task name"


def test_group_scan_uses_local_schedule_only():
    body = _func_src("_group_scan_loop")
    assert "seconds_until(" in body
    assert "sleep(86400)" not in body and "sleep(interval)" not in body
    assert "START_DELAY" not in re.sub(r"#.*", "", body)
    assert "running_job()" in body          # stands down for a late-running pipeline job


def test_auto_backup_no_longer_schedules_itself_and_dumps_pii():
    src = open(os.path.join(BACKEND, "auto_backup.py"), encoding="utf-8").read()
    assert "async def _backup_loop" not in src and "asyncio.sleep(" not in src
    assert 'os.environ.get("DATABASE_PII_URL")' in src      # was PII_DATABASE_URL (never set → PII skipped)


def test_heavy_single_run_functions_do_not_schedule_themselves():
    for fn in ("run_ebay_fitment_once", "run_sync_prices_once", "run_weekly_maintenance_once"):
        body = _code(_func_src(fn))
        assert "seconds_until(" not in body and "while True:\n        " not in body.replace("            while True:", "")
        assert "return " in body


# ── 3. Overnight window vs daytime group scan (measured runtimes) ──────────────

def _window(h, m, minutes, day=date(2026, 10, 10)):
    s = datetime(day.year, day.month, day.day, h, m, tzinfo=IL)
    return s, s + timedelta(minutes=minutes)


def _scan_default():
    h = int(re.search(r'getenv\("SOCIAL_GROUP_SCAN_HOUR_IL",\s*"(\d+)"\)', SRC).group(1))
    m = int(re.search(r'getenv\("SOCIAL_GROUP_SCAN_MINUTE_IL",\s*"(\d+)"\)', SRC).group(1))
    return h, m


def test_group_scan_daytime_window_disjoint_from_night_window():
    import night_pipeline as np
    scan = _window(*_scan_default(), 190)   # 39 batches × ~4.8 min measured 2026-10-03
    close = _window(*np.WINDOW_CLOSE, 0)[0]
    assert scan[0] >= close, "group scan must start after the night window has closed"
    assert 9 <= scan[0].hour and scan[1].hour < 21   # owner notify window 09-21


# ── 4. One job→lock map, matching the real acquire_lock names ──────────────────

def _inproc_locks() -> dict:
    for node in TREE.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "_INPROC_JOB_LOCKS" for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError("_INPROC_JOB_LOCKS missing")


def test_lock_map_matches_acquire_lock_names():
    acquired = set()
    for f in ("catalog_scraper.py", "db_update_agent.py", "BACKEND_AI_AGENTS.py"):
        acquired |= set(re.findall(r'acquire_lock\([^,]+,\s*"([a-z_]+)"', open(os.path.join(BACKEND, f), encoding="utf-8").read()))
    assert acquired == set(_inproc_locks().values())
    assert _inproc_locks()["sync_prices"] == "sync_prices"


def test_no_second_hand_written_lock_map():
    code = re.sub(r"#[^\n]*", "", SRC)   # the explanatory comment may name the old key
    assert not re.search(r"_JOB_LOCK_MAP\s*=\s*\{", code)
    assert '"price_sync"' not in code


# ── 5. Reconciler frees every in-process lock even with 0 'running' rows ──────

class _FakeRedis:
    def __init__(self, keys):
        self.keys = set(keys)
        self.closed = False

    async def delete(self, key):
        had = key in self.keys
        self.keys.discard(key)
        return int(had)

    async def aclose(self):
        self.closed = True


class _FakeResult:
    def fetchall(self):
        return []          # pre_restart.sh already superseded every row


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, *a, **k):
        return _FakeResult()

    async def commit(self):
        pass


def test_reconciler_frees_leaked_locks_when_no_rows_running(capsys):
    fn = _func("_reconcile_orphaned_jobs")
    leaked = {"autospare:lock:brand_discovery", "autospare:lock:sync_prices",
              "autospare:lock:unrelated_external_job"}
    redis = _FakeRedis(leaked)
    auth = types.ModuleType("BACKEND_AUTH_SECURITY")

    async def _get_redis():
        return redis
    auth.get_redis = _get_redis
    ns = {"_BACKEND_START_UTC": datetime.now(timezone.utc), "_INPROC_JOB_LOCKS": _inproc_locks(),
          "async_session_factory": _FakeSession, "text": lambda s: s}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), ROUTES, "exec"), ns)
    sys.modules["BACKEND_AUTH_SECURITY"], saved = auth, sys.modules.get("BACKEND_AUTH_SECURITY")
    try:
        _run_coro(ns["_reconcile_orphaned_jobs"]())
    finally:
        if saved is not None:
            sys.modules["BACKEND_AUTH_SECURITY"] = saved
        else:
            sys.modules.pop("BACKEND_AUTH_SECURITY", None)
    assert redis.keys == {"autospare:lock:unrelated_external_job"}, redis.keys
    assert not redis.closed, "must not close the shared get_redis() singleton"
    assert "freed stale in-process job locks" in capsys.readouterr().out


# ── 6. cpie_full_seed retry decision uses the exit code ───────────────────────

def test_full_seed_retries_hourly_on_any_failure():
    body = _func_src("_car_parts_ie_full_seed_loop")
    assert "between if rc == 0 else 3600" in body
    assert "started > 120" not in body


# ── 7. #54 trigger consolidation: REX night cycle is the single automatic owner ──

CS_PATH = os.path.join(BACKEND, "catalog_scraper.py")
CS_SRC = open(CS_PATH, encoding="utf-8").read()
CS_TREE = ast.parse(CS_SRC)
DA_PATH = os.path.join(BACKEND, "db_update_agent.py")
DA_SRC = open(DA_PATH, encoding="utf-8").read()
DA_TREE = ast.parse(DA_SRC)


def _src_of(tree, src, name):
    for node in ast.walk(tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == name:
            return ast.get_source_segment(src, node), node
    raise AssertionError(name)


def _code(s: str) -> str:
    return re.sub(r"#[^\n]*", "", s)


def test_rex_loop_starts_no_heavy_job():
    loop = _code(_src_of(CS_TREE, CS_SRC, "scraper_background_loop")[0])
    for heavy in ("run_scraper_cycle", "resolve_inactive_parts_with_oem_lookup", "_run_category_discovery",
                  "run_transport_pipeline_if_due", "run_brand_discovery", "run_monthly_brand_discovery",
                  "run_rex_night_cycle", "sync_ebay_prices", "is_noon_run", "is_midnight_run"):
        assert heavy not in loop, heavy
    assert "refresh_and_persist_ils_exchange_rate" in loop and "run_jaguar_fitment_lookup_todos" in loop


def test_rex_no_longer_calls_ebay_price_sync():
    loop = _code(_src_of(CS_TREE, CS_SRC, "scraper_background_loop")[0])
    assert "sync_ebay_prices" not in loop


def test_each_price_sync_has_exactly_one_automatic_caller():
    callers = {"sync_ebay_prices": [], "sync_aliexpress_prices": []}
    for root, _, files in os.walk(BACKEND):
        if any(p in root for p in ("tests", "devtests", "__pycache__", "legacy")):
            continue
        for f in files:
            if f.endswith(".py"):
                txt = _code(open(os.path.join(root, f), encoding="utf-8", errors="ignore").read())
                for fn in callers:
                    if re.search(rf"await {fn}\(", txt):
                        callers[fn].append(f)
    assert callers == {"sync_ebay_prices": ["BACKEND_AI_AGENTS.py"], "sync_aliexpress_prices": ["BACKEND_AI_AGENTS.py"]}


def _ordered_tasks():
    body, _ = _src_of(DA_TREE, DA_SRC, "run_all_tasks")
    m = re.search(r"ordered_tasks = \[(.*?)\]", body, re.S)
    return re.findall(r'^\s*"([a-z_]+)"', _code(m.group(1)), re.M)


def test_db_agent_cannot_launch_brand_discovery_by_cycle_or_todo():
    # #55: not in the automatic order, not in TASK_REGISTRY (so an agent todo cannot
    # inject it — run_all_tasks only accepts todo task names that are in TASK_REGISTRY),
    # and db_update_agent contains no call to run_brand_discovery at all.
    for t in ("trigger_scraper_for_registry_gaps", "trigger_scraper_for_misses"):
        assert t not in _ordered_tasks()
        assert not re.search(rf'"{t}"\s*:', _code(DA_SRC)), t
    assert "run_brand_discovery" not in _code(DA_SRC)
    assert "name in TASK_REGISTRY" in DA_SRC            # the todo-injection gate still exists
    for sel in ("select_registry_gap_brands", "select_search_miss_brands"):
        body = _code(_src_of(DA_TREE, DA_SRC, sel)[0])
        assert "create_task" not in body and "UPDATE search_misses" not in body


def test_category_discovery_uses_slug_and_job_registry():
    body = _code(_src_of(CS_TREE, CS_SRC, "_run_category_discovery")[0])
    assert "category_slug = categorize_on_ingest(category_he)" in body
    assert '"c": category_slug' in body and "category=category_slug," in body
    assert '"c": category_he' not in body and "category=category_he," not in body
    assert 'job_registry_start(_jdb, "category_discovery"' in body
    assert "job_registry_finish(" in body and "_cd_hb_task.cancel()" in body


# ── 8. #55 brand discovery: exactly ONE automatic trigger, monthly, 01:00 IL on the 1st ──

def test_monthly_slot_is_first_of_month_0315_israel_across_dst():
    t = datetime(2026, 10, 3, 12, tzinfo=timezone.utc)
    seen = []
    for _ in range(40):                                   # > 3 years
        n = ls.next_run_utc(3, 15, None, t, 1)
        loc = n.astimezone(IL)
        assert (loc.day, loc.hour, loc.minute) == (1, 3, 15), loc
        seen.append((loc.year, loc.month))
        t = n + timedelta(minutes=300)                    # stand-down wait + a long run must not re-fire
    assert len(seen) == len(set(seen)), "more than one automatic run in a month"
    assert seen[0] == (2026, 11)
    for (y1, m1), (y2, m2) in zip(seen, seen[1:]):
        assert (y2 * 12 + m2) - (y1 * 12 + m1) == 1, "a month was skipped"
    # real Israel offsets: Nov 1 is IST (UTC+2), Jul 1 is IDT (UTC+3)
    assert ls.next_run_utc(3, 15, None, datetime(2026, 10, 3, tzinfo=timezone.utc), 1) == datetime(2026, 11, 1, 1, 15, tzinfo=timezone.utc)
    assert ls.next_run_utc(3, 15, None, datetime(2027, 6, 15, tzinfo=timezone.utc), 1) == datetime(2027, 7, 1, 0, 15, tzinfo=timezone.utc)


def test_monthday_must_exist_in_every_month():
    with pytest.raises(ValueError):
        ls.next_run_utc(1, 0, None, None, 31)


def test_brand_discovery_is_a_monthly_controller_job_with_constraints():
    import night_pipeline as np
    jobs = {j.key: j for j in np.build_production_controller().jobs}
    bd = jobs["brand_discovery"]
    assert (bd.period, bd.monthday, bd.earliest) == ("monthly", 1, (3, 15))
    assert "sync_prices" in bd.depends_on and bd.mem_heavy
    for lk in ("brand_discovery", "category_discovery", "db_update_agent"):
        assert lk in bd.conflict_locks
    # 03:15 is an earliest-start constraint inside the controller, not a second scheduler
    assert "_brand_discovery_monthly_loop" not in SRC and "BRAND_DISCOVERY_HOUR_IL" not in SRC


def _call_sites(func: str) -> dict:
    """{relative file: [enclosing function, …]} for every call to `func` in backend code."""
    found: dict = {}
    for root, dirs, files in os.walk(BACKEND):
        dirs[:] = [d for d in dirs if d not in ("tests", "devtests", "__pycache__", "legacy", "node_modules", "state")]
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
                        inner = [x for x in ast.walk(fn) if isinstance(x, (ast.AsyncFunctionDef, ast.FunctionDef)) and x is not fn
                                 and any(n is node for n in ast.walk(x))]
                        if not inner:
                            found.setdefault(os.path.relpath(path, BACKEND), []).append(fn.name)
    return {k: sorted(v) for k, v in found.items()}


def test_every_brand_discovery_call_site_is_whitelisted():
    """The complete launch inventory. A new caller (e.g. the old DB-agent trigger coming
    back) fails this test and must be justified here."""
    bulk = os.path.join("harvesters", "bulk_harvest.py")
    assert _call_sites("run_brand_discovery") == {
        # AUTOMATIC — the monthly orchestrator only (pools A, B, C):
        "catalog_scraper.py": ["_test"] + ["run_monthly_brand_discovery"] * 3,   # _test = `python catalog_scraper.py` CLI harness (manual)
        # MANUAL:
        "routes/admin.py": ["_run", "_run"],     # POST /api/v1/admin/scraper/discover[/{brand}]
        bulk: ["main"],                          # python3 /app/harvesters/bulk_harvest.py
    }
    # the monthly orchestrator itself has exactly one caller: the night-pipeline job
    assert _call_sites("run_monthly_brand_discovery") == {"night_pipeline.py": ["run_brand_job"]}
    # the manual paths are not wired to any loop or subprocess launcher
    assert _call_sites("run_oempartsonline_all_brands") == {bulk: ["main"]}
    launcher = re.compile(r'(python3?\s+[^\n"\']*bulk_harvest|"harvesters",\s*"bulk_harvest|import\s+bulk_harvest|from\s+bulk_harvest)')
    for src in (SRC, CS_SRC, DA_SRC):
        assert not launcher.search(src)
    assert "_test()" in CS_SRC[CS_SRC.index('if __name__ == "__main__":'):]


def test_manual_admin_triggers_still_exist():
    admin = open(os.path.join(BACKEND, "routes", "admin.py"), encoding="utf-8").read()
    assert '@router.post("/api/v1/admin/scraper/discover"' in admin
    assert '@router.post("/api/v1/admin/scraper/discover/{brand}"' in admin
    seg = admin[admin.index('"/api/v1/admin/scraper/discover"'):]
    assert "get_current_admin_user" in seg[:600]


def _run_monthly(statuses, miss_brands=("Kia",)):
    """Execute the real run_monthly_brand_discovery with fakes; returns (report, calls, marked)."""
    _, node = _src_of(CS_TREE, CS_SRC, "run_monthly_brand_discovery")
    calls, marked = [], []
    it = iter(statuses)

    async def fake_rbd(**kw):
        calls.append(kw)
        return {"status": next(it)}

    class _S:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False
    da = types.ModuleType("db_update_agent")

    async def _gap(db):
        return {"brands": ["Aion", "BAIC"], "target": 500000, "per_run": 20}

    async def _miss(db):
        return {"brands": list(miss_brands), "ids": ["1", "2"] if miss_brands else []}

    async def _mark(db, ids):
        marked.extend(ids)
    da.select_registry_gap_brands, da.select_search_miss_brands, da.mark_search_misses_triggered = _gap, _miss, _mark

    async def _nosleep(_):
        return None
    ns = {"run_brand_discovery": fake_rbd, "scraper_session_factory": _S, "DISCOVERY_ENABLED": True,
          "asyncio": types.SimpleNamespace(sleep=_nosleep), "Dict": dict, "Any": object,
          "_last_discovery_report": None}
    exec(compile(ast.Module(body=[node], type_ignores=[]), CS_PATH, "exec"), ns)
    saved = sys.modules.get("db_update_agent")
    sys.modules["db_update_agent"] = da
    try:
        rep = _run_coro(ns["run_monthly_brand_discovery"]())
    finally:
        if saved is None:
            sys.modules.pop("db_update_agent", None)
        else:
            sys.modules["db_update_agent"] = saved
    return rep, calls, marked


def test_monthly_run_sweeps_three_pools_and_marks_misses_after_running():
    rep, calls, marked = _run_monthly(["completed", "completed", "completed"])
    assert calls == [{}, {"brands": ["Aion", "BAIC"], "target": 500000, "per_run": 20}, {"brands": ["Kia"]}]
    assert marked == ["1", "2"] and rep["status"] == "completed"


def test_monthly_run_skips_entirely_when_lock_is_held_by_a_manual_run():
    rep, calls, marked = _run_monthly(["skipped"])
    assert len(calls) == 1 and marked == [] and rep["status"] == "skipped"


def test_search_miss_rows_stay_untriggered_if_their_run_was_skipped():
    rep, calls, marked = _run_monthly(["nothing_to_do", "completed", "skipped"])
    assert len(calls) == 3 and marked == []


def test_brand_discovery_lock_prevents_concurrent_runs():
    import distributed_lock as dl

    class _R:
        def __init__(self):
            self.kv = {}

        async def set(self, k, v, nx=False, ex=None, **kw):
            if nx and k in self.kv:
                return None
            self.kv[k] = v
            return True

        async def get(self, k):
            return self.kv.get(k)

        async def delete(self, k):
            return int(self.kv.pop(k, None) is not None)

        async def eval(self, script, numkeys, key, token):
            if self.kv.get(key) == token:
                del self.kv[key]
                return 1
            return 0

    async def scenario():
        r = _R()
        first = await dl.acquire_lock(r, "brand_discovery", ttl_seconds=86400)
        second = await dl.acquire_lock(r, "brand_discovery", ttl_seconds=86400)
        return bool(first), bool(second), list(r.kv)
    first, second, keys = _run_coro(scenario())
    assert first is True and second is False and keys == ["autospare:lock:brand_discovery"]
    body = _code(_src_of(CS_TREE, CS_SRC, "run_brand_discovery")[0])
    assert 'acquire_lock(await get_redis(), "brand_discovery"' in body
    assert "brand_discovery already running on another worker" in body


# ── 9. group-scan batch watchdog (host-OOM hang, 2026-10-03) ───────────────────

def test_group_scan_batch_has_non_blocking_watchdog():
    body = _code(_func_src("_group_scan_loop"))
    i = body.index("scan_groups(_batch)")
    seg = body[i - 200:i + 600]
    assert "asyncio.create_task(_BatchScanAgent().scan_groups(_batch))" in seg
    assert "await asyncio.wait(" in seg and "SOCIAL_GROUP_SCAN_BATCH_TIMEOUT_S" in seg
    assert "_btask.cancel()" in seg and "raise TimeoutError" in seg
    assert "wait_for(" not in seg       # wait_for would await the (hanging) cancellation
    assert "await _BatchScanAgent().scan_groups(_batch)" not in body


def test_watchdog_pattern_survives_hanging_cancellation():
    async def hung_batch():
        try:
            await asyncio.sleep(3600)          # renderer gone, nothing ever returns
        finally:
            await asyncio.sleep(3600)          # cleanup against the dead browser hangs too

    async def one_batch(timeout):
        t = asyncio.create_task(hung_batch())
        done, _ = await asyncio.wait({t}, timeout=timeout)
        if not done:
            t.cancel()
            raise TimeoutError("batch exceeded")
        return t.result()

    async def main():
        start = asyncio.get_running_loop().time()
        with pytest.raises(TimeoutError):
            await one_batch(0.2)
        return asyncio.get_running_loop().time() - start
    assert _run_coro(main()) < 1.0
