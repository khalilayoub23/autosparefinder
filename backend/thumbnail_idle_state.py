"""
Script: thumbnail_idle_state.py
Purpose: Remember, across backend restarts, that the thumbnail backlog was drained and how far
         the supervisor (BACKEND_API_ROUTES._thumbnail_import_loop) had backed off.
Why: proving "0 candidates" costs a 150-245 s join over ~1.4 M parts_images rows. The loop backs
     off 300 s -> 3600 s between empty checks, but that back-off lived in memory, so every restart
     reset it to 300 s (2026-10-10: three empty scans within 20 minutes of two restarts).
Process: save(backoff_s) when a batch finds 0 candidates; clear() when a batch finds work;
         resume() -> (seconds_to_wait_before_first_batch, backoff_to_continue_with). Unknown,
         broken or implausible state -> (floor, 300): the check is never skipped for longer than
         the back-off the loop had already earned (max 3600 s).
Data Imported/Modified: /app/state/thumbnail_import_idle.json only.
Data Sources: none.
Missing Data Delegation: n/a.
Last Updated: 2026-10-10
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional, Tuple

STATE = Path(os.getenv("THUMBNAIL_IDLE_STATE", "/app/state/thumbnail_import_idle.json"))
MIN_BACKOFF, MAX_BACKOFF = 300, 3600


def save(backoff_s: float, now: Optional[float] = None, path: Optional[Path] = None) -> None:
    p = path or STATE
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps({"drained_at": now or time.time(), "backoff_s": float(backoff_s)}), encoding="utf-8")
        os.replace(tmp, p)
    except OSError:
        pass


def clear(path: Optional[Path] = None) -> None:
    try:
        (path or STATE).unlink()
    except OSError:
        pass


def resume(floor_s: float = 120, now: Optional[float] = None, path: Optional[Path] = None) -> Tuple[float, float]:
    try:
        data = json.loads((path or STATE).read_text(encoding="utf-8"))
        drained_at, backoff = float(data["drained_at"]), float(data["backoff_s"])
    except (OSError, ValueError, KeyError, TypeError):
        return float(floor_s), float(MIN_BACKOFF)
    now = now or time.time()
    backoff = max(MIN_BACKOFF, min(MAX_BACKOFF, backoff))
    age = now - drained_at
    if age < 0:
        return float(floor_s), float(MIN_BACKOFF)
    # After the remaining wait the loop is exactly where it would have been without the restart:
    # it has slept `backoff`, so the next empty check backs off twice as long (capped).
    return max(float(floor_s), backoff - age), float(min(backoff * 2, MAX_BACKOFF))
