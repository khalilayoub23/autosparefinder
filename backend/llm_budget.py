"""
Script: llm_budget.py
Purpose: ONE durable, cross-process daily ceiling on LLM calls.
Process: every LLM caller asks `try_spend(job)` before calling the provider and
         is refused once the shared day counter is exhausted.
Data Imported/Modified: Redis keys `autospare:llm:calls:<YYYY-MM-DD>` (a counter
         expiring after 48h) and `:by:<job>` for per-caller attribution.
Data Sources: none.
Missing Data Delegation: if Redis is unreachable the budget FAILS OPEN with a
         warning — a categorization backfill must not be blocked by a cache
         outage — but the failure is logged so it cannot be silent.
Last Updated: 2026-08-06

WHY THIS EXISTS (measured 2026-08-06):
  `db_cleanup_agent` held its daily ceiling in a MODULE GLOBAL:

      _llm_calls_today: int = 0

  Two ways that is not a ceiling:
    1. It resets to 0 on every container restart. We restarted twice in one day,
       so the "150/day" cap could be spent three times over.
    2. It is per-PROCESS. `maintenance/llm_categorize_catchall.py` runs as its
       own process and shared no counter with it at all — it spent 400+ calls
       against the same provider key while the cleanup agent believed the day's
       budget was untouched.
  The quota then depleted, exactly as it had before, and the protection that was
  supposed to prevent it had never covered the second caller.

  RULE: a budget that lives in memory is not a budget. A shared resource needs a
  shared counter, in a store that outlives the process.
"""
from __future__ import annotations

import datetime as _dt
import logging
import os

logger = logging.getLogger("llm_budget")

# One ceiling for EVERY caller combined — that is the whole point. Per-job caps
# would re-create the problem: N jobs each under their own limit can still
# exhaust one provider key.
DAILY_MAX = int(os.getenv("LLM_DAILY_MAX_CALLS", "1200"))
_PREFIX = "autospare:llm:calls"
_TTL_S = 172800          # 48h: outlives the day it counts, self-cleaning


def _day() -> str:
    return _dt.datetime.utcnow().strftime("%Y-%m-%d")


async def _redis():
    try:
        import redis.asyncio as _r
        return _r.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"))
    except Exception as exc:
        logger.warning("llm_budget: redis unavailable (%s) — failing OPEN", exc)
        return None


async def try_spend(job: str, n: int = 1) -> tuple[bool, str]:
    """Reserve `n` calls for `job`. -> (allowed, reason).

    Increments FIRST and refunds on refusal, so two processes racing at the
    boundary cannot both be told yes.
    """
    r = await _redis()
    if r is None:
        return True, "budget unavailable (redis down) — allowed"
    key = f"{_PREFIX}:{_day()}"
    try:
        used = await r.incrby(key, n)
        await r.expire(key, _TTL_S)
        if used > DAILY_MAX:
            await r.decrby(key, n)          # refund: we are not making the call
            return False, f"daily LLM budget exhausted ({DAILY_MAX}/{DAILY_MAX})"
        await r.hincrby(f"{key}:by", job, n)
        await r.expire(f"{key}:by", _TTL_S)
        return True, f"{used}/{DAILY_MAX}"
    except Exception as exc:
        logger.warning("llm_budget: spend failed (%s) — failing OPEN", exc)
        return True, "budget error — allowed"
    finally:
        try:
            await r.aclose()
        except Exception:
            pass


async def status() -> dict:
    """What has been spent today, and by whom — so depletion is attributable."""
    r = await _redis()
    if r is None:
        return {"available": False}
    key = f"{_PREFIX}:{_day()}"
    try:
        used = int(await r.get(key) or 0)
        by = await r.hgetall(f"{key}:by")
        return {
            "available": True,
            "day": _day(),
            "used": used,
            "max": DAILY_MAX,
            "remaining": max(0, DAILY_MAX - used),
            "by_job": {
                (k.decode() if isinstance(k, bytes) else k):
                int(v.decode() if isinstance(v, bytes) else v)
                for k, v in (by or {}).items()
            },
        }
    except Exception as exc:
        return {"available": False, "error": f"{type(exc).__name__}: {exc}"}
    finally:
        try:
            await r.aclose()
        except Exception:
            pass
