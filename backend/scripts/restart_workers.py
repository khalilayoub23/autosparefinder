#!/usr/bin/env python3
"""
Script: scripts/restart_workers.py
Purpose: The single implementation of "which importer subprocesses does a backend
         restart manage, how are they stopped, and how are they resumed"
         (FIXES_TRACKER #60, 2026-10-03). Called by pre_restart.sh (capture, stop),
         container_start.sh and post_restart.sh (resume). Runs INSIDE the backend container.

Root causes it removes:
  1. STOP SELF-MATCH. pre_restart.sh ran `pgrep -f <name>` inside a `bash -c "…"` whose own
     command line contained every target name, so the first pgrep matched that shell and
     the loop SIGTERM'd itself: nothing after the first name was ever signalled.
     Here a process is a target only if its real argv (from /proc/<pid>/cmdline) is a
     Python interpreter running a managed script — a shell, grep, pgrep or this helper can
     never match.
  2. RESUME WITHOUT ARGUMENTS. The state kept only the script path as `cmd`; container_start.sh
     launched `python3 <cmd>` and post_restart.sh started from `cmd` too, so an importer
     came back with no --brand/--file and died with a usage error — reported as
     "Resumed" / "✅ complete". Both hooks also launched the same worker (duplicate), the
     state was never cleared (stale relaunch on every later start), and capture kept one
     process per script (concurrent imports of different vehicles collapsed to one).
     Here the exact argv is stored and replayed, resume is refused — loudly — when the
     arguments or an input file are missing, an identical running process is not
     duplicated, and the state is consumed under a lock before anything is launched.

Commands:
  capture   write the managed, resumable importers (exact argv) to the state file; print JSON
  stop      SIGTERM managed + stop-only processes, wait for them to exit, report each one
  resume    relaunch the captured importers with their exact argv; exit 3 if any could not be
  list      print what `capture`/`stop` would act on (read-only)

Process: see the commands above. Compatible with existing progress state: importers such as
  car_parts_ie_import_generic resume from "<--file>.checkpoint.json" when relaunched with
  the same arguments, so nothing is added to or removed from the recorded argv.
Data Imported/Modified: /app/state/worker_state.json (RESTART_STATE_FILE) only.
Data Sources: /proc.
Missing Data Delegation: n/a.
Last Updated: 2026-10-03
"""
from __future__ import annotations

import fcntl
import json
import os
import shlex
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

STATE_FILE = os.getenv("RESTART_STATE_FILE", "/app/state/worker_state.json")
LOG_DIR = os.getenv("RESTART_LOG_DIR", "/app/state/logs")
PROC_ROOT = os.getenv("RESTART_PROC_ROOT", "/proc")
STOP_WAIT_S = float(os.getenv("RESTART_STOP_WAIT_S", "10"))
# A capture is only meaningful for the restart it was taken for. If pre_restart.sh ran but the
# restart did not follow, the state must not be replayed at some much later container start.
STATE_MAX_AGE_S = float(os.getenv("RESTART_STATE_MAX_AGE_S", "1800"))

# Captured for resume AND stopped gracefully. Matched against the SCRIPT BASENAME only.
RESUMABLE = ("freesbe_importer", "category_backfill", "car_parts_ie_import", "ebay_brand_importer",
             "kgm_ssangyong", "saab_parts", "gm_playwright", "kick_run_all", "run_todo")
# Stopped gracefully but never resumed here: brand-discovery children are owned by
# night_pipeline.Controller, which marks an interrupted job FAILED and does not re-run it.
STOP_ONLY = ("oempartsonline_importer", "oem_parts_online_scraper")
# Flags whose value is an input file that must still exist for a resume to make sense
# (the backend container's /tmp does not survive a recreate).
INPUT_FILE_FLAGS = ("--file", "--input", "--json", "--path")


def _kind(script_base: str) -> Optional[str]:
    if script_base == os.path.basename(__file__):
        return None
    if any(f in script_base for f in RESUMABLE):
        return "resumable"
    if any(f in script_base for f in STOP_ONLY):
        return "stop_only"
    return None


def _script_of(argv: List[str]) -> Optional[str]:
    """The script a Python interpreter is running, or None if argv is not `python … script.py`."""
    if not argv or not os.path.basename(argv[0]).startswith("python"):
        return None
    i = 1
    while i < len(argv):
        a = argv[i]
        if a in ("-m", "-c"):
            return None                      # module / inline code: not a managed script
        if a.startswith("-"):
            i += 2 if a in ("-W", "-X") else 1
            continue
        return a
    return None


def scan(proc_root: str = None) -> List[Dict[str, Any]]:
    """Managed processes, from their real argv. Never matches a shell, grep, pgrep or itself."""
    proc_root = proc_root or PROC_ROOT
    out: List[Dict[str, Any]] = []
    me = os.getpid()
    for entry in sorted(os.listdir(proc_root), key=lambda s: (len(s), s)):
        if not entry.isdigit() or int(entry) == me:
            continue
        try:
            raw = open(os.path.join(proc_root, entry, "cmdline"), "rb").read()
        except OSError:
            continue
        argv = [a.decode("utf-8", "replace") for a in raw.split(b"\0") if a]
        script = _script_of(argv)
        if not script:
            continue
        kind = _kind(os.path.basename(script))
        if not kind:
            continue
        try:
            cwd = os.readlink(os.path.join(proc_root, entry, "cwd"))
        except OSError:
            cwd = "/app"
        out.append({"pid": int(entry), "argv": argv, "script": script, "kind": kind, "cwd": cwd,
                    "name": os.path.basename(script).replace(".py", "")})
    return out


def _write_state(state: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(STATE_FILE) or ".", exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(state, fh, indent=2)
    os.replace(tmp, STATE_FILE)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def cmd_capture() -> int:
    workers, seen = [], set()
    for p in scan():
        if p["kind"] != "resumable":
            continue
        key = tuple(p["argv"])
        if key in seen:                      # the same command twice is one worker…
            continue
        seen.add(key)                        # …but different arguments are different workers
        workers.append({"name": p["name"], "argv": p["argv"], "cwd": p["cwd"],
                        "cmd": p["script"], "full_cmd": " ".join(shlex.quote(a) for a in p["argv"])})
    state = {"version": 2, "workers": workers, "timestamp": _now()}
    _write_state(state)
    print(json.dumps(state))
    return 0


def _alive(pid: int) -> bool:
    """True while the process is really running. A zombie (exited, not yet reaped by its
    parent — importers are children of uvicorn) has exited: kill(pid, 0) still succeeds on
    it, so its state is read from /proc/<pid>/stat."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        stat = open(f"/proc/{pid}/stat").read()
        return stat[stat.rindex(")") + 2] != "Z"
    except (OSError, ValueError, IndexError):
        return False


def cmd_stop() -> int:
    targets = scan()
    if not targets:
        print("[restart_workers] stop: no managed importer/scraper process is running")
        return 0
    for p in targets:
        try:
            os.kill(p["pid"], signal.SIGTERM)
            print(f"[restart_workers] stop: SIGTERM → {p['name']} pid={p['pid']} ({p['kind']})")
        except ProcessLookupError:
            print(f"[restart_workers] stop: {p['name']} pid={p['pid']} already exited")
    deadline = time.time() + STOP_WAIT_S
    while time.time() < deadline and any(_alive(p["pid"]) for p in targets):
        time.sleep(0.25)
    still = [p for p in targets if _alive(p["pid"])]
    for p in still:
        print(f"[restart_workers] stop: {p['name']} pid={p['pid']} still running after {STOP_WAIT_S:.0f}s "
              f"(finishing its current unit; not force-killed)")
    print(f"[restart_workers] stop: signalled={len(targets)} exited={len(targets) - len(still)} still_running={len(still)}")
    return 0


def _validate(w: Dict[str, Any]) -> Optional[str]:
    """Reason this worker must NOT be relaunched, or None if it is safe to."""
    argv = w.get("argv")
    if not argv and w.get("full_cmd"):
        try:
            argv = shlex.split(w["full_cmd"])
        except ValueError:
            return "recorded command line cannot be parsed"
        w["argv"] = argv
    if not argv:
        return "no arguments were recorded (only a script path) — refusing to start it with defaults"
    script = _script_of(argv)
    if not script:
        return "recorded command is not `python <script> …`"
    if _kind(os.path.basename(script)) != "resumable":
        return f"{os.path.basename(script)} is not a resumable managed importer"
    if not os.path.isfile(script):
        return f"script not found: {script}"
    for i, a in enumerate(argv):
        flag, _, inline = a.partition("=")
        if flag in INPUT_FILE_FLAGS:
            path = inline or (argv[i + 1] if i + 1 < len(argv) else "")
            if not path or not os.path.isfile(path):
                return f"input file is gone: {path or '(missing value)'}"
    return None


def cmd_resume() -> int:
    if not os.path.isfile(STATE_FILE):
        print("[restart_workers] resume: no state file — nothing to resume")
        return 0
    lock_fd = os.open(STATE_FILE + ".lock", os.O_CREAT | os.O_RDWR, 0o644)
    fcntl.flock(lock_fd, fcntl.LOCK_EX)          # the two hooks cannot both consume the state
    try:
        try:
            state = json.load(open(STATE_FILE))
        except Exception as exc:
            print(f"[restart_workers] resume: NOT RESUMED — state file unreadable: {exc}")
            return 3
        workers = state.get("workers") or []
        if not workers:
            print("[restart_workers] resume: nothing to resume"
                  + (f" (state already consumed at {state['consumed_at']})" if state.get("consumed_at") else ""))
            return 0
        stale_reason = None
        try:
            captured = datetime.strptime(state.get("timestamp") or "", "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - captured).total_seconds()
            if age > STATE_MAX_AGE_S:
                stale_reason = f"captured {age / 60:.0f} min ago (limit {STATE_MAX_AGE_S / 60:.0f} min) — not from this restart"
        except ValueError:
            stale_reason = "capture has no valid timestamp"
        # Consume BEFORE launching: a second hook, or a later container start, sees an empty list.
        _write_state({"version": 2, "workers": [], "consumed_at": _now(),
                      "captured_at": state.get("timestamp"), "pending_count": len(workers)})
        running = {tuple(p["argv"]) for p in scan()}
        os.makedirs(LOG_DIR, exist_ok=True)
        outcomes = []
        for w in workers:
            name = w.get("name") or "unknown"
            reason = stale_reason or _validate(w)
            if reason:
                print(f"[restart_workers] resume: NOT RESUMED {name} — {reason}")
                outcomes.append({"name": name, "outcome": "not_resumed", "reason": reason})
                continue
            argv = w["argv"]
            if tuple(argv) in running:
                print(f"[restart_workers] resume: SKIPPED {name} — an identical process is already running")
                outcomes.append({"name": name, "outcome": "already_running"})
                continue
            log = os.path.join(LOG_DIR, f"resumed_{name}_{int(time.time() * 1000)}.log")
            with open(log, "ab") as fh:
                proc = subprocess.Popen(argv, stdout=fh, stderr=subprocess.STDOUT, cwd=w.get("cwd") or "/app",
                                        env={**os.environ, "PYTHONUNBUFFERED": "1"}, start_new_session=True)
            running.add(tuple(argv))
            print(f"[restart_workers] resume: RESUMED {name} pid={proc.pid} argv={' '.join(shlex.quote(a) for a in argv)} log={log}")
            outcomes.append({"name": name, "outcome": "resumed", "pid": proc.pid, "argv": argv, "log": log})
            time.sleep(float(os.getenv("RESTART_RESUME_STAGGER_S", "2")))
        _write_state({"version": 2, "workers": [], "consumed_at": _now(),
                      "captured_at": state.get("timestamp"), "last_resume": outcomes})
        n_ok = sum(1 for o in outcomes if o["outcome"] == "resumed")
        n_bad = sum(1 for o in outcomes if o["outcome"] == "not_resumed")
        n_dup = sum(1 for o in outcomes if o["outcome"] == "already_running")
        print(f"[restart_workers] RESUME RESULT: resumed={n_ok} not_resumed={n_bad} already_running={n_dup}")
        return 3 if n_bad else 0
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def cmd_list() -> int:
    print(json.dumps([{k: p[k] for k in ("pid", "name", "kind", "argv")} for p in scan()], indent=2))
    return 0


def main(argv: List[str]) -> int:
    cmds = {"capture": cmd_capture, "stop": cmd_stop, "resume": cmd_resume, "list": cmd_list}
    if len(argv) != 2 or argv[1] not in cmds:
        print("usage: restart_workers.py capture|stop|resume|list", file=sys.stderr)
        return 2
    return cmds[argv[1]]()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
