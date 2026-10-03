"""
Script: local_schedule.py
Purpose: Single source of truth for "run at a fixed ISRAEL wall-clock time" scheduling
         of the backend's daily/weekly supervised loops (overnight processing window).
Process:
  1. next_run_utc(hour, minute, weekday, monthday) walks forward day by day in the app's local
     timezone (APP_LOCAL_TIMEZONE, default Asia/Jerusalem — the same setting
     BACKEND_API_ROUTES.APP_LOCAL_TZ uses), builds the wall-clock candidate, converts
     it to UTC and returns the first one strictly in the future.
  2. seconds_until(...) is the asyncio.sleep() argument for a loop.
Why (2026-10-03): daily/weekly loops used `sleep(24h)` after each run, anchored to the
  container start, so they drifted (sync_prices start time slid ~1h/day: 22:44 → 06:47
  UTC over 8 days) and re-anchored on every restart. Fixed wall-clock times stop both.
DST: all comparisons/subtractions happen in UTC. Subtracting two aware datetimes that
  share one ZoneInfo is naive wall-clock arithmetic in Python (off by 1h across a DST
  change) — never do that here. On the Israeli fall-back night 01:00-02:00 occurs twice;
  fold=0 (the first occurrence) is used, and because "next" is computed in UTC a job
  that just ran cannot fire again in the repeated hour.
Data Imported/Modified: none (pure time math).
Data Sources: none.
Missing Data Delegation: n/a.
Last Updated: 2026-10-03
"""
from __future__ import annotations

import os
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

LOCAL_TZ = ZoneInfo(os.getenv("APP_LOCAL_TIMEZONE", "Asia/Jerusalem"))


def next_run_utc(hour: int, minute: int = 0, weekday: int | None = None,
                 now: datetime | None = None, monthday: int | None = None) -> datetime:
    """Next occurrence of local `hour:minute` as an aware UTC datetime, strictly after now.
    Optional filters on the LOCAL calendar date: `weekday` (0=Monday … 6=Sunday) and/or
    `monthday` (1-28: day of the month, e.g. 1 = first day of each month)."""
    if monthday is not None and not 1 <= monthday <= 28:
        raise ValueError("monthday must be 1..28 so that every month has the slot")
    now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    day = now_utc.astimezone(LOCAL_TZ).date()
    for _ in range(9 if monthday is None else 400):
        if (weekday is None or day.weekday() == weekday) and (monthday is None or day.day == monthday):
            cand = datetime.combine(day, time(hour, minute), tzinfo=LOCAL_TZ).astimezone(timezone.utc)
            if cand > now_utc:
                return cand
        day += timedelta(days=1)
    raise ValueError(f"no local slot found for {hour:02d}:{minute:02d} weekday={weekday} monthday={monthday}")


def seconds_until(hour: int, minute: int = 0, weekday: int | None = None,
                  now: datetime | None = None, monthday: int | None = None) -> float:
    """Seconds from now until next_run_utc(...). Floor of 1s so a loop never spins."""
    now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return max(1.0, (next_run_utc(hour, minute, weekday, now_utc, monthday) - now_utc).total_seconds())


def describe(hour: int, minute: int = 0, weekday: int | None = None,
             monthday: int | None = None) -> str:
    """Human-readable next run, for log lines: '2026-10-04 01:00 IDT (2026-10-03T22:00Z)'."""
    nxt = next_run_utc(hour, minute, weekday, None, monthday)
    return f"{nxt.astimezone(LOCAL_TZ):%Y-%m-%d %H:%M %Z} ({nxt:%Y-%m-%dT%H:%MZ})"
