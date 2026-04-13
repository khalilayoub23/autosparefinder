from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from BACKEND_DATABASE_MODELS import async_session_factory
from db_update_agent import run_task
from run_fitment_enrichment_pass import run_fitment_enrichment_pass_async


REPORT_FILE = Path(__file__).parent / "data" / "full_car_database.worker_pass_report.json"


async def _run_worker_tasks() -> Dict[str, Any]:
    async with async_session_factory() as db:
        tasks = [
            "sync_models_from_catalog",
            "sync_models_from_catalog_file",
            "backfill_catalog_fitment_from_xls",
            "merge_catalog_fitment_from_part_vehicle_fitment",
        ]
        results = []
        for task_name in tasks:
            results.append(await run_task(task_name, db))
        return {
            "tasks": results,
        }


async def _run_worker_fitment_pass() -> Dict[str, Any]:
    return {
        "worker_tasks": await _run_worker_tasks(),
        "post_pass_plan": await run_fitment_enrichment_pass_async(),
    }


def run_targeted_worker_fitment_pass() -> Dict[str, Any]:
    report: Dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "ok",
    }
    try:
        report.update(asyncio.run(_run_worker_fitment_pass()))
    except Exception as exc:
        report["status"] = "error"
        report["error"] = str(exc)

    REPORT_FILE.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    print(json.dumps(run_targeted_worker_fitment_pass(), ensure_ascii=False, indent=2))