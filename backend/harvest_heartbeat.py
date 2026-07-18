"""Shared in-process heartbeat for browser-driven harvesters (Amayama, RockAuto).

The harvester hits the unpriced-OEM feed every round while it's alive — even when
Cloudflare is temporarily blocking it (0 new parts). So "is the harvester running?"
is answered by feed activity, NOT by whether new parts appeared. The feed endpoint
(routes/system.py) records a heartbeat here on every call; the Amayama monitor
(BACKEND_API_ROUTES.py) reads it and only alerts when there's been NO activity for a
while (tab closed / harvester stopped), never merely because output is idle.

Both live in the same uvicorn process, so a module-level dict is shared — no IPC.
Resets on restart (fine: a running harvester re-populates it within ~1 min).
"""
import time

_HEARTBEAT: dict = {}


def record(source: str) -> None:
    if source:
        _HEARTBEAT[str(source).lower()] = time.time()


def age_seconds(source: str):
    """Seconds since the source last hit the feed, or None if never seen."""
    t = _HEARTBEAT.get(str(source).lower())
    return None if t is None else time.time() - t
