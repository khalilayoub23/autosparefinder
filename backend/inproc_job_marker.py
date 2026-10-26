"""
Script: inproc_job_marker.py
Purpose: Durable "running / finished" marker for a long job that runs INSIDE the uvicorn process
         (no subprocess, no job_registry row), so the restart preflight can see it.
         First user: the daily Facebook group scan (~3 h). On 2026-10-10 a backend recreation at
         07:06 UTC cut that day's scan (started 07:00 UTC) - the pre-restart gate looked only at
         job_registry rows and subprocesses - and the loop has no catch-up, so the day was lost.
Process: one JSON file per job in /app/state/inproc_jobs/<name>.json (worker_state volume):
         mark_started() writes {"started_at", "pid"}; mark_finished() adds "finished_at".
         status() -> {"running": bool, "started_at", "finished_at", "age_s"}; a start with no
         finish older than `stale_after_s` is reported as not running (the process died).
Data Imported/Modified: /app/state/inproc_jobs/*.json only.
Data Sources: none.
Missing Data Delegation: n/a.
Last Updated: 2026-10-10
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

DIR = Path(os.getenv("INPROC_JOB_MARKER_DIR", "/app/state/inproc_jobs"))


def _path(name: str) -> Path:
    safe = "".join(ch for ch in name if ch.isalnum() or ch in "_-")
    return DIR / f"{safe}.json"


def _write(name: str, data: Dict[str, Any]) -> None:
    DIR.mkdir(parents=True, exist_ok=True)
    tmp = _path(name).with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, _path(name))


def mark_started(name: str, now: Optional[float] = None) -> None:
    _write(name, {"started_at": now or time.time(), "finished_at": None, "pid": os.getpid()})


def mark_finished(name: str, now: Optional[float] = None) -> None:
    data = _read(name) or {}
    data["finished_at"] = now or time.time()
    _write(name, data)


def _read(name: str) -> Optional[Dict[str, Any]]:
    try:
        data = json.loads(_path(name).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def status(name: str, stale_after_s: float = 6 * 3600, now: Optional[float] = None) -> Dict[str, Any]:
    data = _read(name)
    now = now or time.time()
    if not data or not isinstance(data.get("started_at"), (int, float)):
        return {"known": False, "running": False, "started_at": None, "finished_at": None, "age_s": None}
    age = now - float(data["started_at"])
    running = data.get("finished_at") is None and 0 <= age <= stale_after_s
    return {"known": True, "running": running, "started_at": data["started_at"],
            "finished_at": data.get("finished_at"), "age_s": round(age, 1)}
