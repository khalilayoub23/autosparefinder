"""
Script: search_warmup.py
Purpose: Runs the startup search warm-up cases (BACKEND_API_ROUTES._warm_search_paths) and
         reports a classified result, so a failed or slow case is visible as such instead of
         being hidden behind "primed N search shapes".
Process:
  - one fresh DB session per case (a cancelled query must not poison the next case);
  - each case is bounded by asyncio.wait_for (its own timeout_s or the default);
  - outcome per case: ok | timeout | error (exception type logged, never raised): the warm-up is
    optional and must never fail startup or core API health;
  - the search is called with redis=None, and routes/parts.py starts its live external supplier
    lookup only when a Redis client exists to cache the result - so the warm-up makes no request
    to external suppliers (before 2026-10-09 every start sent ~20 of them, results discarded).
Data Imported/Modified: none directly (the search fills its own in-process / Redis caches).
Data Sources: parts_catalog via search_parts.
Missing Data Delegation: n/a.
Last Updated: 2026-10-09
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Dict, Iterable

logger = logging.getLogger(__name__)


async def run_warmup_cases(cases: Iterable[Dict[str, Any]], search_fn: Callable[..., Awaitable[Any]],
                           session_factory: Callable[[], Any], default_timeout_s: float) -> Dict[str, Any]:
    result: Dict[str, Any] = {"total": 0, "ok": 0, "timeout": 0, "error": 0, "failures": []}
    for case in cases:
        result["total"] += 1
        timeout = float(case.get("timeout_s", default_timeout_s))
        label = case.get("query") or f"<fitment {case.get('vehicle_manufacturer')} {case.get('vehicle_model')}>"
        try:
            async with session_factory() as db:
                await asyncio.wait_for(
                    search_fn(
                        query=case.get("query", ""),
                        vehicle_id=case.get("vehicle_id"),
                        category=case.get("category"),
                        per_type=4,
                        sort_by="price_ils",
                        vehicle_manufacturer=case.get("vehicle_manufacturer"),
                        vehicle_model=case.get("vehicle_model"),
                        vehicle_submodel=case.get("vehicle_submodel"),
                        vehicle_year=case.get("vehicle_year"),
                        enable_cross_refs=case.get("enable_cross_refs"),
                        db=db,
                        request=None,
                        redis=None,
                    ),
                    timeout=timeout,
                )
            result["ok"] += 1
        except asyncio.TimeoutError:
            result["timeout"] += 1
            result["failures"].append({"case": label, "kind": "timeout"})
            logger.warning("[SearchWarmup] timeout after %.0fs for %s (non-fatal)", timeout, label)
        except Exception as exc:
            result["error"] += 1
            result["failures"].append({"case": label, "kind": "error", "type": type(exc).__name__})
            logger.warning("[SearchWarmup] error for %s: %s: %s (non-fatal)", label, type(exc).__name__, exc)
    return result


def summary_line(result: Dict[str, Any]) -> str:
    return (f"[SearchWarmup] primed {result['ok']}/{result['total']} search shapes "
            f"(timeouts={result['timeout']}, errors={result['error']})")
