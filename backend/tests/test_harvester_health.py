"""#78 — harvester health: idle on a drained queue is not a stall; genuine hangs are still detected.

Three layers:
  * decision rule (harvester_health.evaluate) — pure, every state boundary;
  * parallelism parsing (harvester _parallel_sessions);
  * END TO END, each scenario in its own process (tests/harvester_sim.py): the REAL harvester main loop / workers /
    status writing + the REAL healthcheck pass (harvester_health.health_pass), with a fake clock, fake page fetches
    (no network, no supplier traffic) and — for db_* — the REAL queue functions against a harvest_queue table in
    the SANDBOX schema h78_test (dropped afterwards; never public, so the sandbox's own harvester cannot see it).
Simulated, not production-observed.
"""
import json
import os
import subprocess
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import harvester_health as hh  # noqa: E402

NOW = 1_800_000_000.0
PID = 4242
BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _st(**kw):
    base = {"pid": PID, "state": "working", "updated_at": NOW, "last_progress_at": NOW, "rest_until": None}
    base.update(kw)
    return base


# ── decision rule ─────────────────────────────────────────────────────────────────────────────────
def test_E1_idle_rest_far_beyond_the_old_15_minute_window_is_idle_not_stalled():
    st = _st(state="idle", rest_until=NOW + 1800, updated_at=NOW - 30)
    r = hh.evaluate(st, pid=PID, now=NOW, log_age_s=2400)           # log silent for 40 min
    assert r["verdict"] == "idle" and r["reason"] == "idle"
    assert hh.evaluate(None, pid=PID, now=NOW, log_age_s=2400)["verdict"] == "stalled"   # the OLD rule's answer


def test_E2_rest_overrun_and_stale_heartbeat_are_stalls():
    assert hh.evaluate(_st(state="idle", rest_until=NOW - 400, updated_at=NOW - 10), pid=PID, now=NOW,
                       log_age_s=0)["reason"] == "rest_overrun"
    assert hh.evaluate(_st(state="idle", rest_until=NOW + 600, updated_at=NOW - 400), pid=PID, now=NOW,
                       log_age_s=0)["reason"] == "idle_heartbeat_stale"
    assert hh.evaluate(_st(state="idle", rest_until=None), pid=PID, now=NOW, log_age_s=0)["verdict"] == "stalled"


def test_E3_working_is_judged_by_progress_never_by_the_heartbeat():
    assert hh.evaluate(_st(last_progress_at=NOW - 899), pid=PID, now=NOW, log_age_s=5000)["verdict"] == "ok"
    r = hh.evaluate(_st(last_progress_at=NOW - 901, updated_at=NOW), pid=PID, now=NOW, log_age_s=0)
    assert r["verdict"] == "stalled" and r["reason"] == "no_progress"   # a fresh heartbeat cannot mask it


def test_E4_status_of_another_process_or_unknown_state_falls_back_to_the_old_rule():
    other = _st(pid=1, state="idle", rest_until=NOW + 3000)
    assert hh.evaluate(other, pid=PID, now=NOW, log_age_s=1000)["verdict"] == "stalled"
    assert hh.evaluate(other, pid=PID, now=NOW, log_age_s=100)["verdict"] == "ok"
    assert "unknown_state" in hh.evaluate(_st(state="weird"), pid=PID, now=NOW, log_age_s=10)["reason"]
    assert hh.evaluate(None, pid=None, now=NOW, log_age_s=None)["verdict"] == "missing"


def test_E5_log_line_is_structured_and_keeps_the_old_status_field():
    line = hh.log_line(PID, hh.evaluate(_st(state="idle", rest_until=NOW + 60), pid=PID, now=NOW, log_age_s=99), 99)
    assert line.startswith("[car_parts_ie_healthcheck] alive=True pid=4242 verdict=idle reason=idle")
    assert line.endswith("status=ok") and "http" not in line


# ── parallelism ───────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("raw,expect", [("5", 5), (" 2 ", 2), ("1", 1), ("8", 8), ("9", 8), ("0", 1), ("-3", 1),
                                        ("abc", 1), ("", 1), (None, 1)])
def test_P_parallel_sessions_parsing_and_bounds(raw, expect):
    src = open(os.path.join(BACKEND, "harvesters", "car_parts_ie_flaresolverr_harvester.py")).read()
    ns = {}
    start = src.index("def _parallel_sessions(")
    end = src.index("PARALLEL_SESSIONS, _PARALLEL_NOTE")
    exec(src[start:end], ns)                     # the real function, without importing the module (global logging)
    value, note = ns["_parallel_sessions"](raw)
    assert value == expect and (note == "" if raw in ("5", " 2 ", "1", "8") else note)


def test_P_compose_default_documentation_and_code_agree():
    root = os.path.dirname(BACKEND)
    assert os.path.exists(os.path.join(root, "docker-compose.yml")), "repo layout expected: <root>/{backend,docker-compose.yml}"
    compose = open(os.path.join(root, "docker-compose.yml")).read()
    assert "HARVESTER_PARALLEL_SESSIONS: ${HARVESTER_PARALLEL_SESSIONS:-5}" in compose
    claude = open(os.path.join(root, "CLAUDE.md")).read()
    assert "PARALLEL_SESSIONS = 2" not in claude                     # the stale statement is gone
    assert "HARVESTER_PARALLEL_SESSIONS" in claude and "= 5" in claude


# ── end to end (subprocess per scenario) ──────────────────────────────────────────────────────────
def _sim(scenario, env_extra=None):
    out = tempfile.mktemp(prefix=f"h78-{scenario}-", suffix=".json")
    env = dict(os.environ, PYTHONPATH=BACKEND, **(env_extra or {}))
    p = subprocess.run([sys.executable, os.path.join(BACKEND, "tests", "harvester_sim.py"), scenario, out],
                       env=env, capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, (p.returncode, p.stdout[-2000:], p.stderr[-2000:])
    res = json.load(open(out))
    os.unlink(out)
    for c in res["checks"]:
        print(f"EVIDENCE-H78 {scenario} t+{c['t']}s {c['tag']}: verdict={c['verdict']} reason={c['reason']} "
              f"(old rule: {c['legacy_verdict']})")
    print(f"EVIDENCE-H78 {scenario} kills={res['kills']} claimed={res['claimed']}")
    return res


def _by(res, tag):
    return [c for c in res["checks"] if c["tag"] == tag][0]


def test_S1_empty_queue_for_an_hour_no_false_stall_but_a_wedged_rest_is_caught():
    r = _sim("idle_long")
    for tag in ("rest+20min", "rest+40min", "rest+59min"):
        assert _by(r, tag)["verdict"] == "idle" and _by(r, tag)["legacy_verdict"] == "stalled"   # old rule killed here
    assert _by(r, "rest_overrun")["verdict"] == "stalled" and _by(r, "rest_overrun")["reason"] == "rest_overrun"
    assert len(r["kills"]) == 1 and "rest_overrun" in r["kills"][0]["label"]


def test_S2_new_work_after_an_idle_period_is_picked_up():
    r = _sim("idle_then_work")
    assert _by(r, "idle+25min")["verdict"] == "idle"
    assert r["claimed"] == ["brand/new-model-1", "brand/new-model-2"]
    assert _by(r, "after_new_work")["verdict"] in ("idle", "ok") and not r["kills"]


def test_S3_legitimately_slow_requests_are_never_killed():
    r = _sim("slow_work")
    mids = [c for c in r["checks"] if c["tag"] == "mid_request"]
    assert mids and all(c["verdict"] == "ok" for c in mids) and not r["kills"]
    assert any(v[2] for v in r["relayed"])                                       # the model completed


def test_S4_a_genuinely_hung_worker_is_detected_within_the_bound():
    r = _sim("hung_worker")
    assert _by(r, "hang+5min")["verdict"] == "ok" and _by(r, "hang+13min")["verdict"] == "ok"
    assert _by(r, "hang+17min")["verdict"] == "stalled" and _by(r, "hang+17min")["reason"] == "no_progress"
    assert len(r["kills"]) == 1


def test_S5_one_hung_worker_while_another_is_productive_then_detected_when_progress_stops():
    r = _sim("partial_hang")
    assert _by(r, "other_worker_busy_or_done")["verdict"] == "ok"
    assert len([v for v in r["claimed"] if "good-" in v]) == 6                    # the healthy worker kept working
    assert _by(r, "hang+17min")["verdict"] == "stalled" and len(r["kills"]) == 1


def test_S6_transient_failures_are_progress_not_stalls():
    r = _sim("flaky_work")
    assert all(c["verdict"] == "ok" for c in r["checks"]) and not r["kills"]
    assert any(v[2] for v in r["relayed"])


def test_S7_a_stale_status_file_from_a_dead_process_cannot_vouch_for_the_live_one():
    r = _sim("stale_status_other_pid")
    c = _by(r, "status_from_dead_process")
    assert c["verdict"] == "stalled" and "status_of_other_pid" in c["reason"]


# ── real queue functions, isolated schema ────────────────────────────────────────────────────────
def _schema_url():
    from BACKEND_DATABASE_MODELS import engine
    if engine.url.host != "sandbox_postgres":
        pytest.skip(f"isolated DB only (catalog host {engine.url.host!r})")
    base = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    assert "?" not in base
    return base, base + "?options=-csearch_path%3Dh78_test"


def _psql(base, sql, args=None, fetch=False):
    import psycopg2
    c = psycopg2.connect(base)
    try:
        with c, c.cursor() as cur:
            cur.execute(sql, args)
            return cur.fetchall() if fetch else None
    finally:
        c.close()


DDL = """CREATE SCHEMA IF NOT EXISTS h78_test;
CREATE TABLE h78_test.harvest_queue (
  id uuid DEFAULT gen_random_uuid() PRIMARY KEY, brand_en varchar(120) NOT NULL, model_name varchar(200) NOT NULL,
  model_slug varchar(240), il_vehicle_count int DEFAULT 0 NOT NULL, priority_rank int, source varchar(40)
  DEFAULT 'il_market' NOT NULL, status varchar(24) DEFAULT 'pending' NOT NULL, parts_found int DEFAULT 0 NOT NULL,
  attempts int DEFAULT 0 NOT NULL, last_harvested_at timestamptz, created_at timestamptz DEFAULT now() NOT NULL,
  updated_at timestamptz DEFAULT now() NOT NULL, UNIQUE (brand_en, model_slug));"""


@pytest.fixture
def queue_schema():
    base, url = _schema_url()
    _psql(base, "DROP SCHEMA IF EXISTS h78_test CASCADE")
    _psql(base, DDL)          # production DDL (pg_dump -s, read-only), in a private schema
    try:
        yield base, url
    finally:
        _psql(base, "DROP SCHEMA IF EXISTS h78_test CASCADE")


def test_D1_concurrent_workers_on_the_real_queue_claim_every_model_exactly_once(queue_schema):
    base, url = queue_schema
    for i in range(12):
        _psql(base, "INSERT INTO h78_test.harvest_queue (brand_en, model_name, model_slug, priority_rank) "
                    "VALUES ('brand', %s, %s, %s)", (f"m{i}", f"brand/m{i:02d}", i))   # production form: brand/model
    r = _sim("db_concurrent", {"DATABASE_URL": url})
    rows = _psql(base, "SELECT model_slug, status, attempts FROM h78_test.harvest_queue ORDER BY 1", fetch=True)
    assert sorted(r["claimed"]) == sorted(f"brand/m{i:02d}" for i in range(12))      # each once, no duplicates
    assert all(st == "done" and att == 1 for _, st, att in rows), rows
    assert r["status"]["parallel"] == 5


def test_D2_recovery_after_a_kill_reclaims_stuck_models_without_duplicates(queue_schema):
    base, url = queue_schema
    for i in range(4):
        _psql(base, "INSERT INTO h78_test.harvest_queue (brand_en, model_name, model_slug, priority_rank, status, "
                    "attempts, updated_at) VALUES ('brand', %s, %s, %s, %s, %s, now() - interval '2 hours')",
              (f"r{i}", f"brand/r{i}", i, "in_progress" if i < 2 else "pending", 1 if i < 2 else 0))
    r = _sim("db_recovery", {"DATABASE_URL": url})
    rows = dict((s, (st, att)) for s, st, att in _psql(
        base, "SELECT model_slug, status, attempts FROM h78_test.harvest_queue", fetch=True))
    assert sorted(r["claimed"]) == ["brand/r0", "brand/r1", "brand/r2", "brand/r3"]
    assert rows["brand/r0"] == ("done", 2) and rows["brand/r1"] == ("done", 2)   # killed mid-model: retried once
    assert rows["brand/r2"] == ("done", 1) and rows["brand/r3"] == ("done", 1)


# ── #78 edge cases: malformed / partial / skewed status, toggle (#93) ────────────────────────────────
@pytest.mark.parametrize("content", ["{not json", '{"pid": 4242, "state": "idle", "rest_until"', "", "null", "[1, 2]"])
def test_E6_malformed_or_partial_status_file_falls_back_to_the_old_rule(tmp_path, content):
    import asyncio as _a
    status, log = tmp_path / "s.json", tmp_path / "h.log"
    status.write_text(content)
    log.write_text("x")
    os.utime(log, (NOW - 100, NOW - 100))
    kills = []

    async def kill(pid, label):
        kills.append(label)
    r = _a.run(hh.health_pass(PID, log_path=str(log), status_path=str(status), now=NOW, kill=kill))
    assert r["verdict"] == "ok" and r["reason"].startswith("legacy_log_age") and not kills      # quiet 100 s: ok
    os.utime(log, (NOW - 1000, NOW - 1000))
    r = _a.run(hh.health_pass(PID, log_path=str(log), status_path=str(status), now=NOW, kill=kill))
    assert r["verdict"] == "stalled" and len(kills) == 1                                         # still protected


def test_E7_missing_status_and_missing_log_never_crash():
    import asyncio as _a

    async def kill(pid, label):
        raise AssertionError("must not kill")
    r = _a.run(hh.health_pass(PID, log_path="/nonexistent/h.log", status_path="/nonexistent/s.json", now=NOW, kill=kill))
    assert r["verdict"] == "ok" and r["reason"] == "legacy_log_age(no_status)"


def test_E8_clock_stepped_back_cannot_hide_a_hang_beyond_the_skew_bound():
    # progress "in the future" by 10 min (the clock stepped back): not trusted → stalled, not "fresh" forever
    r = hh.evaluate(_st(last_progress_at=NOW + 600), pid=PID, now=NOW, log_age_s=0)
    assert r["verdict"] == "stalled" and r["progress_age_s"] is None
    # small skew (≤ 300 s) is tolerated as fresh
    assert hh.evaluate(_st(last_progress_at=NOW + 200), pid=PID, now=NOW, log_age_s=0)["verdict"] == "ok"
    # a rest whose heartbeat is from the "future" is not trusted either
    assert hh.evaluate(_st(state="idle", rest_until=NOW + 600, updated_at=NOW + 900), pid=PID, now=NOW,
                       log_age_s=0)["reason"] == "idle_heartbeat_stale"


@pytest.mark.parametrize("env,on,src", [
    ({}, True, "default"), ({"CPIE_HARVEST_ENABLED": "0"}, False, "CPIE_HARVEST_ENABLED=0"),
    ({"HARVESTER_ENABLED": "false"}, False, "HARVESTER_ENABLED=false"),
    ({"CPIE_HARVEST_ENABLED": "1", "HARVESTER_ENABLED": "0"}, True, "CPIE_HARVEST_ENABLED=1"),   # specific wins
    ({"CPIE_HARVEST_ENABLED": " ", "HARVESTER_ENABLED": "off"}, False, "HARVESTER_ENABLED=off"),  # blank = unset
    ({"CPIE_HARVEST_ENABLED": "maybe"}, True, "unrecognised"),     # a typo never silently stops production
    ({"CPIE_HARVEST_ENABLED": "NO"}, False, "CPIE_HARVEST_ENABLED=NO"),
])
def test_T1_harvest_toggle_semantics(env, on, src):
    enabled, source = hh.harvest_enabled(env)
    assert enabled is on and src in source


@pytest.mark.parametrize("env,expect_spawn", [({"CPIE_HARVEST_ENABLED": "0"}, False), ({"HARVESTER_ENABLED": "0"}, False),
                                               ({}, True), ({"CPIE_HARVEST_ENABLED": "1"}, True)])
def test_T2_the_real_supervisor_loop_never_launches_a_disabled_harvester(monkeypatch, env, expect_spawn):
    """The ONLY launch path (BACKEND_API_ROUTES._car_parts_ie_harvester_loop), driven with a fake asyncio.sleep and
    a recording create_subprocess_exec — nothing is actually spawned."""
    import asyncio as _a
    import types
    import BACKEND_API_ROUTES as api
    for k in ("CPIE_HARVEST_ENABLED", "HARVESTER_ENABLED"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    spawned, sleeps = [], []

    class _Proc:
        async def wait(self):
            return 0

    async def fake_exec(*args, **kw):
        spawned.append(args[1])
        return _Proc()

    async def fake_sleep(s):
        sleeps.append(s)
        if len(sleeps) >= 4:
            raise _a.CancelledError()
    fake = types.SimpleNamespace(**{n: getattr(_a, n) for n in dir(_a) if not n.startswith("__")})
    fake.sleep, fake.create_subprocess_exec = fake_sleep, fake_exec
    monkeypatch.setattr(api, "asyncio", fake)
    with pytest.raises(_a.CancelledError):
        _a.run(api._car_parts_ie_harvester_loop())
    assert bool(spawned) is expect_spawn
    if expect_spawn:
        assert spawned[0].endswith("harvesters/car_parts_ie_flaresolverr_harvester.py")
    else:
        assert sleeps[1:] == [300, 300, 300]                     # idles, re-checking, never launches


@pytest.mark.parametrize("workers", [[1, 2], "busy", 7, {"w0": None}, {"w0": [1]}, {"w0": "x"}])
def test_E9_malformed_workers_field_never_crashes_the_verdict(workers):
    # A working status whose `workers` map is corrupt must still be judged on its own progress, not raise
    # (an exception here would fail every healthcheck pass). Found with E6's non-object case, 2026-10-22.
    st = {"pid": PID, "state": "working", "updated_at": NOW - 5, "last_progress_at": NOW - 30, "workers": workers}
    r = hh.evaluate(st, pid=PID, now=NOW, log_age_s=10.0)
    assert (r["verdict"], r["reason"]) == ("ok", "working")
    st["last_progress_at"] = NOW - hh.PROGRESS_STALL_S - 1
    assert hh.evaluate(st, pid=PID, now=NOW, log_age_s=10.0)["verdict"] == "stalled"
