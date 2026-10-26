"""
Script: search_miss_resolver.py
Purpose: Decide whether a recorded customer search miss (search_misses) has really been resolved
         by the live supplier lookup. Used by BACKEND_API_ROUTES._scrape_search_misses_loop.
Process:
  - genuine_offers(): keeps only results with a real price (price > 0 and total_cost > 0).
    The "affiliate" suppliers (catalog_suppliers._SimpleAffiliate, PartSouq, Amayama's fallback)
    return a price-0 link placeholder for ANY query of 4+ characters - also when the site
    answered HTTP 403 - so "the list is not empty" proves nothing. Before 2026-10-10 the loop
    used exactly that test: every miss was marked triggered_scrape=TRUE and the customer was
    notified "we found new parts" (live: 43 of 43 rows triggered, 37 customers notified).
  - process_misses(): one lookup per miss, no retry; marks a miss resolved ONLY when at least
    one genuine offer exists. A lookup error or a placeholder-only answer leaves the row
    untouched. An unresolved miss is re-checked at most once per RECHECK_AFTER_S (24 h, kept in
    process memory) and the caller only selects misses seen in the last 30 days, so a miss that
    can never be resolved does not make the job query the suppliers every cycle forever.
Data Imported/Modified: none directly (the caller's mark_resolved writes search_misses.triggered_scrape).
Data Sources: services.supplier_aggregator.search_all_suppliers (passed in).
Missing Data Delegation: n/a.
Last Updated: 2026-10-10
"""
from __future__ import annotations

import time
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional

RECHECK_AFTER_S = 86400
_last_checked: Dict[str, float] = {}     # miss id -> time of the last unresolved lookup


def _num(result: Any, field: str) -> float:
    value = result.get(field) if isinstance(result, dict) else getattr(result, field, 0)
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def genuine_offers(results: Iterable[Any]) -> List[Any]:
    """Results a customer could actually buy: a positive price and a positive total."""
    return [r for r in (results or []) if _num(r, "price") > 0 and _num(r, "total_cost") > 0]


async def process_misses(misses: Iterable[Any], search_fn: Callable[..., Awaitable[List[Any]]],
                         mark_resolved: Callable[[Any], Awaitable[None]],
                         limit_per_supplier: int = 5, now: Optional[float] = None,
                         recheck_after_s: float = RECHECK_AFTER_S) -> Dict[str, Any]:
    out: Dict[str, Any] = {"checked": 0, "resolved": 0, "unresolved": 0, "errors": 0, "deferred": 0}
    now = time.time() if now is None else now
    for miss in misses:
        key = str(getattr(miss, "id", ""))
        if key and now - _last_checked.get(key, float("-inf")) < recheck_after_s:
            out["deferred"] += 1
            continue
        out["checked"] += 1
        if key:
            _last_checked[key] = now
        try:
            results = await search_fn(miss.query, limit_per_supplier=limit_per_supplier)
            offers = genuine_offers(results)
            if offers:
                await mark_resolved(miss)
                _last_checked.pop(key, None)
                out["resolved"] += 1
                print(f"[scrape_misses] {len(offers)} priced offer(s) for: {miss.query}")
            else:
                out["unresolved"] += 1
                print(f"[scrape_misses] No priced offer for: {miss.query} "
                      f"({len(results or [])} placeholder/unpriced result(s) ignored)")
        except Exception as miss_err:
            out["errors"] += 1
            print(f"[scrape_misses] Error for miss {getattr(miss, 'id', '?')}: {miss_err}")
    return out
