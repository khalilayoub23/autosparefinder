"""wait_guard: hard deadline, terminal states, independent fallback (2026-11-03)."""
import json
import os

from maintenance import wait_guard as wg


class Clock:
    def __init__(self): self.t = 1000.0
    def now(self): return self.t
    def sleep(self, s): self.t += s


def run(c, **kw):
    kw.setdefault("poll_s", 5); kw.setdefault("emit", lambda m: None)
    return wg.wait_for(now=c.now, sleep=c.sleep, **kw)


def test_hard_timeout_never_success():
    c = Clock()
    r = run(c, pid=1, deadline_s=60, alive=lambda p: True)
    assert r.state == wg.TIMED_OUT and r.waited_s >= 60


def test_normal_completion_via_fallback_when_process_exits():
    c = Clock(); calls = {"n": 0}
    def alive(p): calls["n"] += 1; return calls["n"] < 3
    fb = lambda: {"state": wg.COMPLETED, "exit_code": 0, "completed_at": 1234.0, "detail": "ok"}
    r = run(c, pid=5, deadline_s=600, alive=alive, fallback=fb)
    assert (r.state, r.exit_code, r.completed_at) == (wg.COMPLETED, 0, 1234.0)


def test_failed_exit_code_preserved():
    c = Clock()
    r = run(c, pid=5, deadline_s=60, alive=lambda p: False,
            fallback=lambda: {"state": wg.FAILED, "exit_code": 1, "completed_at": 5.0})
    assert r.state == wg.FAILED and r.exit_code == 1


def test_marker_alone_is_not_completion(tmp_path):
    out = tmp_path / "w.out"; out.write_text("DONE\n")
    c = Clock()
    r = run(c, pid=5, deadline_s=60, alive=lambda p: False, output_path=str(out), marker="DONE")
    assert r.state == wg.UNKNOWN and "not trusted" in r.reason


def test_watcher_stale_but_process_alive_keeps_waiting_then_times_out(tmp_path):
    out = tmp_path / "w.out"; out.write_text("x")
    os.utime(out, (1, 1))  # ancient
    c = Clock(); c.t = 5000.0
    r = run(c, pid=5, deadline_s=30, stale_s=10, alive=lambda p: True, output_path=str(out))
    assert r.state == wg.TIMED_OUT and "watcher stale" in r.events


def test_watcher_output_missing_with_dead_process_uses_fallback(tmp_path):
    c = Clock()
    r = run(c, pid=5, deadline_s=60, alive=lambda p: False, output_path=str(tmp_path / "nope"),
            fallback=lambda: {"state": wg.COMPLETED, "exit_code": 0, "completed_at": 1.0})
    assert r.state == wg.COMPLETED


def test_fallback_failure_is_unknown_not_success():
    c = Clock()
    def boom(): raise OSError("meili down")
    r = run(c, pid=5, deadline_s=600, alive=lambda p: False, fallback=boom)
    assert r.state == wg.UNKNOWN


def test_purge_fallback_reads_run_record(tmp_path):
    log = tmp_path / "r.jsonl"
    log.write_text(json.dumps({"finished_at": 2000.0, "exit_code": 0, "status": "completed", "duration_s": 9}) + "\n")
    assert wg.meili_purge_fallback(str(log), 1500.0)()["state"] == wg.COMPLETED
    assert wg.meili_purge_fallback(str(log), 2500.0)()["state"] == "RUNNING"
    log.write_text(json.dumps({"finished_at": 2000.0, "exit_code": 1, "status": "partial"}) + "\n")
    assert wg.meili_purge_fallback(str(log), 1500.0)()["state"] == wg.FAILED


def test_purge_fallback_queue_busy_is_running(tmp_path):
    fb = wg.meili_purge_fallback(str(tmp_path / "none"), 0, lambda p: {"results": [{"uid": 1}]})
    assert fb()["state"] == "RUNNING"


def test_event_trail_is_bounded_and_deduplicated():
    c = Clock()
    r = run(c, pid=None, deadline_s=3000, poll_s=5, alive=lambda p: False,
            fallback=lambda: {"state": "RUNNING"})
    assert r.state == wg.TIMED_OUT and r.events == ["fallback: still running"]
