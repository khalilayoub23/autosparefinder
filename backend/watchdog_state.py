"""
watchdog_state.py — shared event log between the stall watchdog and db_update_agent.

The watchdog (BACKEND_API_ROUTES.py) records every action it takes here.
db_update_agent reads and validates these records each cycle, alerts on anomalies.

Both modules run in the same uvicorn process so no IPC is needed — just a shared
module-level deque. Thread-safe because asyncio is single-threaded per event loop.
"""
from __future__ import annotations

import collections
from datetime import datetime, timezone
from typing import Literal

WatchdogAction = Literal["kill_orphan", "warn_live_long", "kill_stuck_importer"]


class WatchdogEvent:
    __slots__ = ("ts", "action", "pid", "dur_s", "details", "validated")

    def __init__(self, action: WatchdogAction, pid: int, dur_s: int, details: str = ""):
        self.ts: datetime = datetime.now(timezone.utc)
        self.action: WatchdogAction = action
        self.pid: int = pid
        self.dur_s: int = dur_s
        self.details: str = details
        self.validated: bool = False


# Rolling buffer — keeps the most recent 500 events. Old entries auto-evict.
_EVENTS: collections.deque[WatchdogEvent] = collections.deque(maxlen=500)


def record(action: WatchdogAction, pid: int, dur_s: int, details: str = "") -> None:
    """Called by the watchdog immediately after taking an action."""
    _EVENTS.append(WatchdogEvent(action, pid, dur_s, details))


def drain_unvalidated() -> list[WatchdogEvent]:
    """Returns all events not yet validated by db_update_agent. Does not remove them."""
    return [e for e in _EVENTS if not e.validated]


def stats() -> dict:
    """Summary counts for the heartbeat — all events regardless of validation."""
    kills = sum(1 for e in _EVENTS if e.action == "kill_orphan")
    importer_kills = sum(1 for e in _EVENTS if e.action == "kill_stuck_importer")
    warns = sum(1 for e in _EVENTS if e.action == "warn_live_long")
    return {
        "total_events": len(_EVENTS),
        "orphan_kills": kills,
        "importer_kills": importer_kills,
        "live_warnings": warns,
        "unvalidated": len(drain_unvalidated()),
    }
