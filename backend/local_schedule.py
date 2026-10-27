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
  3. delay_after_restart(...) is for INTERVAL loops (run_all_tasks every 3 h, meili_sync every
     2 h). Those slept "N seconds after container start", so every restart re-anchored them and
     ran a full cycle at once, however recently the last one finished (2026-10-10: two restarts
     28 minutes apart ran run_all_tasks at 05:20, 06:40 and 07:08 UTC and meili_sync twice).
     It returns how long the first cycle after a start must still wait so the interval is
     measured from the LAST COMPLETED run, never less than the loop's own settle time.
Data Imported/Modified: none (pure time math).
Data Sources: none.
Missing Data Delegation: n/a.
Last Updated: 2026-10-10
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


def delay_after_restart(last_completed: datetime | None, interval_s: float, floor_s: float,
                        now: datetime | None = None) -> float:
    """Seconds an interval loop must wait before its FIRST cycle after a process start.
    `last_completed` = when the previous cycle finished (aware datetime, or None if unknown).
    Never less than `floor_s` (the loop's settle time), never more than `interval_s`.
    Unknown, naive or future timestamps fall back to `floor_s`: a cycle is never skipped."""
    floor_s = max(0.0, float(floor_s))
    if last_completed is None or last_completed.tzinfo is None:
        return floor_s
    now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    age = (now_utc - last_completed.astimezone(timezone.utc)).total_seconds()
    if age < 0:
        return floor_s
    return max(floor_s, min(float(interval_s), float(interval_s) - age))


# ── fixed daily slots (2026-10-27: run_all_tasks 3×/day at owner-chosen local times) ──────────

def parse_times(spec: str) -> list[tuple[int, int]]:
    """'21:30,00:15,07:15' -> [(0, 15), (7, 15), (21, 30)] (sorted, de-duplicated).
    Invalid entries raise ValueError — a typo must not silently drop a run."""
    out = set()
    for part in (spec or "").split(","):
        part = part.strip()
        if not part:
            continue
        h, m = part.split(":")
        h, m = int(h), int(m)
        if not (0 <= h <= 23 and 0 <= m <= 59):
            raise ValueError(f"invalid local time {part!r}")
        out.add((h, m))
    if not out:
        raise ValueError("no run times configured")
    return sorted(out)


def next_slot_utc(times: list[tuple[int, int]], now: datetime | None = None) -> datetime:
    """The earliest of the local daily slots strictly after now (aware UTC)."""
    return min(next_run_utc(h, m, None, now) for h, m in times)


def last_slot_utc(times: list[tuple[int, int]], now: datetime | None = None) -> datetime:
    """The most recent local daily slot at or before now (aware UTC). DST-correct: each slot is
    resolved on its own local calendar date."""
    now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    today = now_utc.astimezone(LOCAL_TZ).date()
    best = None
    for back in range(0, 3):
        day = today - timedelta(days=back)
        for h, m in times:
            cand = datetime.combine(day, time(h, m), tzinfo=LOCAL_TZ).astimezone(timezone.utc)
            if cand <= now_utc and (best is None or cand > best):
                best = cand
        if best is not None:
            return best
    raise ValueError("no past slot found")


def slot_missed(times: list[tuple[int, int]], last_started: datetime | None, grace_s: float,
                now: datetime | None = None) -> datetime | None:
    """The scheduled slot that should have started a run but did not, or None.
    A slot counts as missed only once `grace_s` has passed since it, and only if no run started
    at or after it. `last_started` must be aware (None = never ran)."""
    now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    slot = last_slot_utc(times, now_utc)
    if (now_utc - slot).total_seconds() < grace_s:
        return None
    if last_started is not None and last_started.astimezone(timezone.utc) >= slot:
        return None
    return slot
