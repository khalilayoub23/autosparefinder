"""
Script: maint_guard_cli.py
Purpose: Operate the #119 maintenance safety controller (maintenance_guard.py) WITHOUT a restart.
Process:
  status                      - active latches, per-task consecutive-error counters, last run counters, recent events.
  trip <all|price|TASK> <why> - set a latching kill switch (every guarded task checks it before each batch).
  reset <all|price|TASK> [note] - the ONLY way to clear a latching stop; zeroes the task's error counter; logged.
  Run: docker exec -i autospare_backend python3 /app/maintenance/maint_guard_cli.py status
Data Imported/Modified: system_settings keys `maint_guard__*` (trip/reset only; status is read-only).
Data Sources: system_settings.
Missing Data Delegation: none.
Last Updated: 2026-11-01
"""
import asyncio
import json
import sys

from sqlalchemy import text

import maintenance_guard as mg
from BACKEND_DATABASE_MODELS import async_session_factory


async def _main(argv) -> int:
    if not argv or argv[0] not in {"status", "trip", "reset"}:
        print(__doc__)
        return 2
    cmd = argv[0]
    async with async_session_factory() as db:
        if cmd == "status":
            await db.execute(text("SET default_transaction_read_only = on"))
            out = {"latches": await mg.active_latches(db), "tasks": {}}
            for task in sorted(mg.GUARDED_TASKS):
                rows = await mg._get_many(db, [f"{mg.PREFIX}errs__{task}", f"{mg.PREFIX}run__{task}",
                                               f"cursor__{task}"])
                out["tasks"][task] = {"consecutive_errors": rows.get(f"{mg.PREFIX}errs__{task}"),
                                      "last_run": json.loads(rows[f"{mg.PREFIX}run__{task}"])
                                      if rows.get(f"{mg.PREFIX}run__{task}") else None,
                                      "cursor": rows.get(f"cursor__{task}"), "cap": mg.cap_for(task)}
            out["recent_events"] = await mg.recent_events(db, 10)
            print(json.dumps(out, indent=2, default=str, ensure_ascii=False))
            return 0
        if len(argv) < 2:
            print(__doc__)
            return 2
        scope = argv[1]
        if cmd == "trip":
            created = await mg.trip(db, scope, " ".join(argv[2:]) or "manual")
            print("tripped" if created else "already active")
        else:
            removed = await mg.reset(db, scope, note=" ".join(argv[2:]))
            print("reset" if removed else "no active latch (error counter cleared)")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main(sys.argv[1:])))
