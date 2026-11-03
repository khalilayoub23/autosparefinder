#!/usr/bin/env python3
"""
Script: price_audit_fix.py
Purpose: Audit and converge `parts_catalog.base_price` with the central price policy (price_policy.py) - and nothing else.
         REWRITTEN 2026-11-01 (#119). The previous version (policy of 2026-06-12, `sys.exit` at import, 230 dead lines)
         wrote `importer * 1.18 * 1.45` (a GROSS figure) and `max_price_ils * 1.45` over every part regardless of its
         offers, i.e. it would have overwritten offer-owned values with an unrelated estimate and inflated
         IL-importer prices by VAT x margin. It no longer contains a price formula at all.
Process:
  default (read-only)  : dry-run of db_update_agent.apply_base_price_rule over the next --sample parts after the saved
                         cursor of each owner task: how many base_price values WOULD change, by owner
                         (offer = fix_base_prices, estimate = normalize_base_price) and why a part is left alone.
  --apply              : runs fix_base_prices then normalize_base_price through run_task(), i.e. through the maintenance
                         safety controller (kill switch, write caps, locks, price verification). Honours OOM_BLOCKED_TASKS:
                         while the tasks are blocked this reports `skipped`, it never writes by another path.
  --reset-latch SCOPE  : not here - use maintenance/maint_guard_cli.py.
Data Imported/Modified: reads parts_catalog/supplier_parts/suppliers; with --apply base_price only (via the two tasks).
Data Sources: database only.
Missing Data Delegation: price ownership and the formula live in price_policy.py / db_update_agent.apply_base_price_rule.
Last Updated: 2026-11-01
"""
import argparse
import asyncio
import json
import sys
from collections import Counter
from typing import Any, Dict, Optional

from sqlalchemy import text

TASKS = ("fix_base_prices", "normalize_base_price")


async def dry_run(db, *, after_id: Optional[str] = None, sample: int = 2000) -> Dict[str, Any]:
    """Read-only plan for ONE keyset batch: what apply_base_price_rule would write. Never writes."""
    import db_update_agent as dua
    from currency_rate import get_usd_to_ils_rate
    rate = await get_usd_to_ils_rate(db)
    after = after_id or dua._ZERO_UUID
    await db.execute(text("SET LOCAL statement_timeout = '60s'"))
    rows = (await db.execute(text(dua._base_price_inputs_sql(False)),
                             {"rate": rate, "after": after, "batch": int(sample)})).fetchall()
    would_change: Counter = Counter()
    unchanged = 0
    reasons: Counter = Counter()
    for r in rows:
        target, source = dua.base_price_target(r, rate)
        if target is None:
            reasons[source] += 1
        elif r.base_price is not None and round(float(r.base_price), 2) == target:
            unchanged += 1
        else:
            would_change[source] += 1
    return {"after": after, "scanned": len(rows), "would_change": dict(would_change), "unchanged": unchanged,
            "left_alone": dict(reasons), "last_id": rows[-1].id if rows else None}


async def apply_tasks(db) -> Dict[str, Any]:
    """Run the two owner tasks through run_task() (guard, locks, caps, verification). Returns their reports."""
    import db_update_agent as dua
    out = {}
    for name in TASKS:
        out[name] = await dua.run_task(name, db)
    return out


async def main(argv) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("Process:")[0])
    ap.add_argument("--apply", action="store_true", help="run fix_base_prices + normalize_base_price (guarded)")
    ap.add_argument("--sample", type=int, default=2000)
    args = ap.parse_args(argv)
    from BACKEND_DATABASE_MODELS import async_session_factory
    import db_update_agent as dua
    async with async_session_factory() as db:
        if args.apply:
            res = await apply_tasks(db)
        else:
            await db.execute(text("SET default_transaction_read_only = on"))
            res = {}
            for name in TASKS:
                cursor = await dua._get_task_cursor(db, name)
                res[name] = await dry_run(db, after_id=cursor, sample=args.sample)
        print(json.dumps(res, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
