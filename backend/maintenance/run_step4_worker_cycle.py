from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from typing import Any, Dict

from BACKEND_DATABASE_MODELS import async_session_factory
from db_update_agent import run_task
from sqlalchemy.exc import DBAPIError


MAX_TASK_RETRIES = 3


def _is_deadlock_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return "deadlock detected" in msg


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one Step 4 worker cycle (tasks only, no post-plan).")
    parser.add_argument("--label", default="", help="Optional label to include in report")
    return parser.parse_args()


async def _run_cycle(label: str) -> Dict[str, Any]:
    tasks = [
        "sync_models_from_catalog",
        "sync_models_from_catalog_file",
        "backfill_catalog_fitment_from_xls",
        "backfill_bmw_fitment_from_name_he",
        "backfill_mini_fitment_from_name_he",
        "merge_catalog_fitment_from_part_vehicle_fitment",
    ]

    out: Dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "ok",
        "label": label,
        "tasks": [],
    }

    async with async_session_factory() as db:
        for task_name in tasks:
            attempt = 1
            while True:
                try:
                    task_result = await run_task(task_name, db)
                    task_result["attempt"] = attempt
                    out["tasks"].append(task_result)
                    break
                except DBAPIError as exc:
                    await db.rollback()
                    if not _is_deadlock_error(exc) or attempt >= MAX_TASK_RETRIES:
                        out["status"] = "failed"
                        out["failed_task"] = task_name
                        out["failed_attempt"] = attempt
                        out["error"] = str(exc)
                        raise

                    out["tasks"].append(
                        {
                            "task": task_name,
                            "status": "retrying",
                            "attempt": attempt,
                            "reason": "deadlock_detected",
                        }
                    )
                    attempt += 1
                    await asyncio.sleep(1.5 * attempt)

    return out


def main() -> None:
    args = _parse_args()
    report = asyncio.run(_run_cycle(args.label))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
