"""Scenario driver for the #78 harvester-health E2E tests — runs in its OWN process (the harvester configures global
logging at import; isolation keeps that out of the pytest process).

Usage: python3 tests/harvester_sim.py <scenario> <out.json>

What is REAL: the harvester module (main loop, worker, harvest_model, _rest, status writing — harvesters/
car_parts_ie_flaresolverr_harvester.py) and the healthcheck decision (harvester_health.health_pass — the exact pass
the supervisor runs). With scenario "db_*" the queue functions are the real claim_next_model / complete_model /
reclaim_stale_in_progress against a harvest_queue table in the SANDBOX schema h78_test (search_path), never public.
What is FAKE: the clock (only the harvester module's `time` is replaced), page fetches and the relay (no network,
no supplier traffic), FlareSolverr clearance. Every check records (fake seconds since start, verdict, reason).
"""
import asyncio
import json
import os
import sys
import tempfile
import threading
import time as _real_time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class FakeTime:
    """Replaces the harvester module's `time`. sleep() advances the clock and gives the driver a hook."""

    def __init__(self):
        self.now = 1_800_000_000.0
        self.lock = threading.Lock()
        self.on_main_sleep = None

    def time(self):
        with self.lock:
            return self.now

    def advance(self, s):
        with self.lock:
            self.now += s

    def sleep(self, s):
        self.advance(s)
        if threading.current_thread() is threading.main_thread() and self.on_main_sleep:
            self.on_main_sleep(s)
        _real_time.sleep(0.001)


class Stop(Exception):
    pass


def main(scenario, out_path):
    import harvester_health as hh
    import harvesters.car_parts_ie_flaresolverr_harvester as hv
    clock = FakeTime()
    t0 = clock.now
    tmp = tempfile.mkdtemp(prefix="h78sim-")
    hv.time = clock
    hv.STATUS_FILE = hv.Path(tmp) / "harvester_status.json"
    hv.STATE_FILE = hv.Path(tmp) / "state.json"
    hv._STATUS.update(pid=os.getpid(), since=clock.now, updated_at=clock.now, last_progress_at=clock.now)
    log_path = os.path.join(tmp, "harvester.log")
    open(log_path, "w").close()
    os.utime(log_path, (t0, t0))                      # the log is as quiet as in production: never written again
    hv.save_state = lambda st: None
    hv.load_state = lambda: {"cycles": 0, "total_parts": 0, "runs": [], "idle_cycles": 4}
    hv.ensure_clearance = lambda force=False: True
    hv._CLEARANCE["cookie"] = "fake"
    hv.get_additional_slugs = lambda html, b, m: []
    hv.BASE_SLUGS = ["slug-a", "slug-b", "slug-c"]
    hv.fs_cleanup_stale_sessions = lambda: 0
    relayed, checks, kills = [], [], []
    hv.post_relay = lambda vehicle, parts, done=False: relayed.append((vehicle, len(parts), done)) or True

    # ── queue: fake (in-memory) or real (sandbox schema h78_test) ──
    claimed = []
    if scenario.startswith("db_"):
        real_claim = hv.claim_next_model

        def claim():
            c = real_claim()
            if c:
                claimed.append(c[1])
            return c
        hv.claim_next_model = claim
    else:
        q = {"pending": [], "lock": threading.Lock()}

        def claim():
            with q["lock"]:
                if not q["pending"]:
                    return None
                v = q["pending"].pop(0)
                claimed.append(v)
                return (f"id-{v}", v)
        hv.claim_next_model = claim
        hv.complete_model = lambda qid, n: None
        hv._queue_pending_count = lambda: len(q["pending"])
        hv._queue_progress = lambda: {"done": 0, "total": 0, "brands_done": 0, "brands_total": 0}
        hv.reclaim_stale_in_progress = lambda minutes=30: 0
        hv.requeue_completed_for_refresh = lambda days=14: 0

    async def _kill(pid, label):
        kills.append({"t": clock.now - t0, "label": label})

    def check(tag):
        r = asyncio.run(hh.health_pass(os.getpid(), log_path=log_path, status_path=str(hv.STATUS_FILE),
                                       now=clock.now, kill=_kill))
        checks.append({"tag": tag, "t": round(clock.now - t0), "verdict": r["verdict"], "reason": r["reason"],
                       "line": r["line"]})
        legacy = hh.evaluate(None, pid=os.getpid(), now=clock.now, log_age_s=clock.now - t0)
        checks[-1]["legacy_verdict"] = legacy["verdict"]        # what the OLD log-age-only rule would have said

    def finish(code=0):
        with open(out_path, "w") as fh:
            json.dump({"checks": checks, "kills": kills, "claimed": claimed, "relayed": relayed,
                       "status": json.loads(open(hv.STATUS_FILE).read()) if hv.STATUS_FILE.exists() else None},
                      fh, ensure_ascii=False)
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)        # the per-scenario status/log directory
        os._exit(code)

    pages = {"mode": "fast", "release": threading.Event(), "hang_vehicle": None, "blocked": threading.Event()}

    def fs_get(url, session_id="", timeout_ms=20000):
        name = threading.current_thread().name
        if pages["mode"] == "hang" or (pages["hang_vehicle"] and pages["hang_vehicle"] in url):
            pages["blocked"].set()
            pages["release"].wait(600)                    # blocked: no progress, no time passing for this worker
            return ""
        if pages["mode"] == "slow":
            clock.advance(600)                            # each request takes 10 fake minutes, then completes
            check("mid_request")                          # the watchdog looks while the request is still running
        elif pages["mode"] == "flaky" and url.endswith("slug-b"):
            return ""                                     # transient failure: empty page, the model continues
        else:
            clock.advance(5)
        return "<html></html>"
    hv.fs_get = fs_get
    hv.parse_parts = lambda html, path: [{"sku": f"{path}-1"}] if html else []

    # ── scenarios ───────────────────────────────────────────────────────────
    if scenario == "idle_long":
        # drained queue, idle x5+ → a 3600 s rest; check at 20, 40 and 59 minutes, then past the end + grace
        seen = {"n": 0}

        def on_sleep(s):
            el = clock.now - t0
            for mark, tag in ((1200, "rest+20min"), (2400, "rest+40min"), (3540, "rest+59min")):
                if el >= mark and tag not in [c["tag"] for c in checks]:
                    check(tag)
            if el >= 3600:
                raise Stop()
        clock.on_main_sleep = on_sleep
        try:
            hv.main()
        except Stop:
            pass
        st = json.loads(open(hv.STATUS_FILE).read())
        # the harvester never wakes (simulated wedge in the rest): far past rest_until + grace
        clock.now = float(st["rest_until"]) + 400
        check("rest_overrun")
        finish()

    if scenario == "idle_then_work":
        q["pending"] = []
        state = {"phase": "idle"}

        def on_sleep(s):
            el = clock.now - t0
            if state["phase"] == "idle" and el >= 1500:
                check("idle+25min")
                q["pending"].extend(["brand/new-model-1", "brand/new-model-2"])   # new work arrives
                state["phase"] = "queued"
            if state["phase"] == "queued" and claimed:
                state["phase"] = "worked"
            if state["phase"] == "worked" and len(claimed) >= 2 and s >= 60:
                check("after_new_work")
                raise Stop()
            if el > 4 * 3600:
                raise Stop()
        clock.on_main_sleep = on_sleep
        try:
            hv.main()
        except Stop:
            pass
        finish()

    if scenario in ("slow_work", "flaky_work"):
        pages["mode"] = "slow" if scenario == "slow_work" else "flaky"
        q["pending"] = ["brand/slow-model"]
        hv.PARALLEL_SESSIONS = 1
        orig_note = hv._note_progress

        def note(vehicle=None, done=False):
            orig_note(vehicle, done)
            if vehicle and not done:
                check(f"page_done@{round(clock.now - t0)}")
        hv._note_progress = note

        def on_sleep(s):
            if claimed and s >= 60:
                raise Stop()
        clock.on_main_sleep = on_sleep
        try:
            hv.main()
        except Stop:
            pass
        finish()

    if scenario in ("hung_worker", "partial_hang"):
        if scenario == "hung_worker":
            pages["mode"] = "hang"
            q["pending"] = ["brand/hung-model"]
            hv.PARALLEL_SESSIONS = 1
        else:
            pages["hang_vehicle"] = "hung-model"
            q["pending"] = ["brand/hung-model"] + [f"brand/good-{i}" for i in range(6)]
            hv.PARALLEL_SESSIONS = 2

        def driver():
            assert pages["blocked"].wait(30), "the worker never reached the blocking request"
            _real_time.sleep(0.2)                     # the harvester's status is written; the worker is blocked
            if scenario == "partial_hang":
                _real_time.sleep(1.0)                 # the healthy worker drains its models meanwhile
                check("other_worker_busy_or_done")
            base = clock.now
            for off, tag in ((300, "hang+5min"), (800, "hang+13min"), (1000, "hang+17min")):
                clock.now = base + off
                check(tag)
            pages["release"].set()
            _real_time.sleep(0.2)
            finish()
        threading.Thread(target=driver, daemon=True).start()

        def on_sleep(s):
            pass
        clock.on_main_sleep = on_sleep
        hv.main()

    if scenario == "stale_status_other_pid":
        hv._STATUS.update(pid=999999, state="idle", rest_until=clock.now + 3600)
        hv._status_write(force=True)
        clock.advance(1200)
        check("status_from_dead_process")
        finish()

    if scenario in ("db_concurrent", "db_recovery"):
        hv.PARALLEL_SESSIONS = 5

        def on_sleep(s):
            if s >= 60 and claimed:
                raise Stop()
        clock.on_main_sleep = on_sleep
        try:
            hv.main()
        except Stop:
            pass
        finish()

    raise SystemExit(f"unknown scenario {scenario}")


if __name__ == "__main__":
    try:
        main(sys.argv[1], sys.argv[2])
    except SystemExit:
        raise
    except BaseException as exc:                       # surface the failure to the test
        import traceback
        traceback.print_exc()
        os._exit(3)
