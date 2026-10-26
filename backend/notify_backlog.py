"""
Script: notify_backlog.py
Purpose: Honest owner notifications about a STANDING BACKLOG (items waiting for the owner): report what is
         NEW since the last announcement, never re-announce old items as new, and remind about an unchanged
         backlog only at a low cadence — with the real total and the age of the oldest item.
Why (FIXES_TRACKER #71): the keyword alert said "15 מילות סיווג חדשות" every day for 12+ days while the same
         15 tokens (consensus reached 2026-07-27 / 08-06) were listed and the real backlog was 2,524; the
         pending-posts reminder re-listed the same July posts daily. Nothing remembered what was announced.
Process: backlog_plan() is a pure decision; load_state()/save_state() keep, per backlog kind, the ids already
         announced (a Redis set) and the times of the last "new" announcement and the last reminder.
         First use (no state yet): the existing backlog counts as already announced (carried over), so a deploy
         can never announce thousands of old items as new.
Data Imported/Modified: Redis keys autospare:backlog:<kind>:{announced,announced_at,reminded_at} only.
Data Sources: the caller's current backlog ({id: created_at}).
Last Updated: 2026-10-20
"""
from __future__ import annotations

import os
import time
from datetime import datetime
from typing import Dict, Iterable, Optional

REMIND_EVERY_S = int(float(os.getenv("BACKLOG_REMINDER_DAYS", "7")) * 86400)
# At most one "new items" message per backlog per this interval (the callers run every few minutes; the
# previous keyword alert was once a day). New items in between are reported together next time.
NEW_MIN_INTERVAL_S = int(os.getenv("BACKLOG_NEW_MIN_INTERVAL_S", "86400"))


def _key(kind: str, part: str) -> str:
    return f"autospare:backlog:{kind}:{part}"


def backlog_plan(current: Dict[str, Optional[datetime]], announced: Optional[set], *,
                 last_reminder_ts: Optional[float], now_ts: float,
                 remind_every_s: int = REMIND_EVERY_S,
                 last_new_ts: Optional[float] = None, new_min_interval_s: int = NEW_MIN_INTERVAL_S) -> dict:
    """Decide what to tell the owner about a backlog. `announced` None = no state yet (first use).

    Returns {"send": "new"|"reminder"|None, "new": [ids, oldest first], "total": n,
             "oldest": datetime|None, "bootstrap": bool}.
    """
    ids = list(current)
    created = [c for c in current.values() if c is not None]
    oldest = min(created) if created else None
    bootstrap = announced is None
    seen = set(ids) if bootstrap else set(announced)
    new = sorted((i for i in ids if i not in seen),
                 key=lambda i: (current[i] is None, current[i] or datetime.min))
    plan = {"send": None, "new": new, "total": len(ids), "oldest": oldest, "bootstrap": bootstrap}
    if new:
        if last_new_ts is None or now_ts - last_new_ts >= new_min_interval_s:
            plan["send"] = "new"
        # else: hold — reported together in the next allowed "new" message, never dropped
    elif ids and (last_reminder_ts is None or now_ts - last_reminder_ts >= remind_every_s):
        plan["send"] = "reminder"
    return plan


async def load_state(r, kind: str) -> dict:
    """{"announced": set|None, "announced_at": float|None, "reminded_at": float|None}"""
    exists = await r.exists(_key(kind, "announced"))
    members = await r.smembers(_key(kind, "announced")) if exists else None
    ann = await r.get(_key(kind, "announced_at"))
    rem = await r.get(_key(kind, "reminded_at"))
    return {
        "announced": ({m.decode() if isinstance(m, bytes) else str(m) for m in members}
                      if members is not None else None),
        "announced_at": float(ann) if ann else None,
        "reminded_at": float(rem) if rem else None,
    }


async def save_state(r, kind: str, current_ids: Iterable[str], plan: dict, now_ts: Optional[float] = None) -> None:
    """Call ONLY after the message was confirmed sent (or queued). The announced set becomes the current
    backlog, so items that left the backlog are forgotten and a returning item counts as new again."""
    now_ts = time.time() if now_ts is None else now_ts
    ids = [str(i) for i in current_ids]
    pipe = r.pipeline()
    pipe.delete(_key(kind, "announced"))
    if ids:
        pipe.sadd(_key(kind, "announced"), *ids)
    else:
        pipe.sadd(_key(kind, "announced"), "__empty__")   # state exists even for an empty backlog
    if plan.get("send") == "new":
        pipe.set(_key(kind, "announced_at"), str(now_ts))
    # a "new" message also shows the whole backlog, so it counts as the reminder too
    if plan.get("send") in ("new", "reminder"):
        pipe.set(_key(kind, "reminded_at"), str(now_ts))
    await pipe.execute()


async def record_bootstrap(r, kind: str, current_ids: Iterable[str]) -> None:
    """Persist a first-use baseline without sending anything."""
    await save_state(r, kind, current_ids, {"send": None})
