#!/usr/bin/env python3
"""
Script:  maintenance/wait_guard.py
Purpose: Wait for a monitored process with a HARD deadline and an independent fallback, so a
         dead/stale watcher can never leave a wait hanging or report success by omission.
Process:
  1. Every `poll_s`, check the monitored PID directly (/proc) and the watcher output file.
  2. A marker in the watcher output is accepted only as a hint: completion is decided by the
     monitored process state, or (when the PID is unknown/gone) by the fallback check.
  3. If the watcher output goes stale (> stale_s) or the watcher exited, the fallback runs
     (for the Meili purge: read-only task + run-log queries).
  4. Terminal state: COMPLETED | FAILED | TIMED_OUT | UNKNOWN. Never waits past `deadline_s`.
  5. The monitored process is NEVER signalled, killed or restarted by this tool.
Data Imported/Modified: none (read-only; the CLI prints a report).
Data Sources: /proc, the watcher output file, optional JSONL run log, Meilisearch GET /tasks.
Missing Data Delegation: no evidence -> UNKNOWN, never COMPLETED.
Last Updated: 2026-11-03
"""
from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

COMPLETED, FAILED, TIMED_OUT, UNKNOWN = "COMPLETED", "FAILED", "TIMED_OUT", "UNKNOWN"


@dataclass
class WaitResult:
    state: str
    reason: str
    exit_code: int | None = None
    completed_at: float | None = None
    waited_s: float = 0.0
    events: list = field(default_factory=list)


def pid_alive(pid: int) -> bool:
    """True only for a live, non-zombie process (checked in /proc, not by trusting a watcher)."""
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"
    except (OSError, IndexError):
        return False


def wait_for(*, pid: int | None, deadline_s: float, poll_s: float = 5.0, stale_s: float = 120.0,
             status_every_s: float = 60.0, output_path: str | None = None, marker: str | None = None,
             fallback: Callable[[], dict] | None = None, now=time.time, sleep=time.sleep,
             alive=pid_alive, emit: Callable[[str], None] = lambda m: print(m, flush=True)) -> WaitResult:
    """Block until a terminal state. `fallback()` returns {"state": COMPLETED|FAILED|RUNNING,
    "exit_code": int|None, "completed_at": float|None, "detail": str} or raises."""
    t0 = now()
    ev: list = []
    last_status = t0
    marker_seen = False
    fb_fail = 0

    def note(msg):  # bounded, de-duplicated event trail (a poll loop must not grow it without limit)
        if (not ev or ev[-1] != msg) and len(ev) < 50:
            ev.append(msg)

    def done(state, reason, rc=None, at=None):
        r = WaitResult(state, reason, rc, at, round(now() - t0, 1), ev)
        emit(f"[wait_guard] {time.strftime('%H:%M:%S')} {state}: {reason} (waited {r.waited_s}s)")
        return r

    while True:
        t = now()
        if t - t0 >= deadline_s:
            return done(TIMED_OUT, f"hard deadline {deadline_s:.0f}s reached; monitored process left untouched")
        out_age = None
        if output_path:
            try:
                st = Path(output_path).stat()
                out_age = t - st.st_mtime
                if marker and not marker_seen and marker in Path(output_path).read_text(errors="replace"):
                    marker_seen = True
                    note("marker seen (hint only)")
            except OSError:
                note("watcher output missing")
        proc_alive = alive(pid) if pid else None
        watcher_stale = out_age is not None and out_age > stale_s
        if watcher_stale:
            note("watcher stale")
        if proc_alive is True and not watcher_stale and not (output_path and out_age is None):
            pass  # healthy: process alive, watcher fresh
        elif proc_alive is not True:
            # process gone (or unknown): decide by independent fallback, never by marker alone
            if fallback is None:
                return done(UNKNOWN, "monitored process not running and no fallback configured"
                            + (" (marker seen but not trusted)" if marker_seen else ""))
            try:
                fb = fallback()
                fb_fail = 0
                if fb.get("state") in (COMPLETED, FAILED):
                    return done(fb["state"], fb.get("detail", "fallback verdict"),
                                fb.get("exit_code"), fb.get("completed_at"))
                note("fallback: still running")
            except Exception as exc:  # noqa: BLE001
                fb_fail += 1
                note(f"fallback error: {type(exc).__name__}")
                if fb_fail >= 3:
                    return done(UNKNOWN, f"watcher/process gone and fallback failed {fb_fail}x: {exc}")
        if t - last_status >= status_every_s:
            last_status = t
            emit(f"[wait_guard] {time.strftime('%H:%M:%S')} waiting {t - t0:.0f}s/{deadline_s:.0f}s "
                 f"process_alive={proc_alive} output_age={None if out_age is None else round(out_age)}s")
        sleep(poll_s)


def meili_purge_fallback(runlog: str, since: float, meili_get: Callable[[str], dict] | None = None) -> Callable[[], dict]:
    """Independent verdict for the purge: the JSONL run record written by purge_run_log, plus
    read-only Meilisearch queue state (GET /tasks?statuses=enqueued,processing) as a busy check."""
    def check() -> dict:
        rec = None
        try:
            for line in Path(runlog).read_text().splitlines():
                r = json.loads(line)
                if r.get("finished_at", 0) >= since:
                    rec = r
        except OSError:
            rec = None
        if rec:
            ok = rec.get("exit_code") == 0 and rec.get("status") == "completed"
            return {"state": COMPLETED if ok else FAILED, "exit_code": rec.get("exit_code"),
                    "completed_at": rec.get("finished_at"),
                    "detail": f"run record status={rec.get('status')} duration={rec.get('duration_s')}s"}
        if meili_get:
            q = meili_get("/tasks?types=documentDeletion&statuses=enqueued,processing&limit=1")
            if q.get("results"):
                return {"state": "RUNNING", "detail": "deletion tasks still queued"}
        return {"state": "RUNNING", "detail": "no run record yet"}
    return check


def _meili_getter():
    import urllib.request
    url = os.environ.get("MEILI_URL", "http://meilisearch:7700").rstrip("/")
    key = os.environ.get("MEILI_MASTER_KEY", "")

    def get(path: str) -> dict:
        req = urllib.request.Request(url + path, headers={"Authorization": f"Bearer {key}"})
        with urllib.request.urlopen(req, timeout=20) as r:  # GET only: creates no task
            return json.loads(r.read())
    return get


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Hard-deadline wait for the Meili purge (read-only).")
    ap.add_argument("--pid", type=int)
    ap.add_argument("--deadline", type=float, default=3600)
    ap.add_argument("--stale", type=float, default=120)
    ap.add_argument("--output")
    ap.add_argument("--marker")
    ap.add_argument("--runlog", default="/app/state/logs/meili_purge_runs.jsonl")
    ap.add_argument("--since", type=float, default=time.time())
    a = ap.parse_args()
    res = wait_for(pid=a.pid, deadline_s=a.deadline, stale_s=a.stale, output_path=a.output, marker=a.marker,
                   fallback=meili_purge_fallback(a.runlog, a.since, _meili_getter()))
    print(json.dumps(res.__dict__, default=str))
    sys.exit({COMPLETED: 0, FAILED: 1, TIMED_OUT: 2, UNKNOWN: 3}[res.state])
