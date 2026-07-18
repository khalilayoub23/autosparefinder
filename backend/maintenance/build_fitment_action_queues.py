from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List


BASE_DIR = Path(__file__).parent.parent / "data"
NEXT_PASS_FILE = BASE_DIR / "full_car_database.next_pass.json"
WORKER_QUEUE_FILE = BASE_DIR / "full_car_database.worker_queue.json"
EXTERNAL_QUEUE_FILE = BASE_DIR / "full_car_database.external_queue.json"


def _load_next_pass() -> Dict[str, Any]:
    return json.loads(NEXT_PASS_FILE.read_text(encoding="utf-8"))


def _rank_worker_candidates(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    ranked = []
    for row in rows:
        total_parts = int(row.get("total_parts") or 0)
        parts_with_compat = int(row.get("parts_with_compat") or 0)
        pending_rows = int(row.get("pending_rows") or 0)
        coverage_ratio = (parts_with_compat / total_parts) if total_parts else 0.0
        ranked.append({
            **row,
            "coverage_ratio": round(coverage_ratio, 4),
            "priority": "high" if coverage_ratio >= 0.2 else "medium",
            "recommended_action": "run workbook rebuild against current parts_catalog fitment and inspect unresolved OEM/SKU matching",
        })
    ranked.sort(key=lambda row: (-row["coverage_ratio"], row["pending_rows"], row["manufacturer"]))
    return ranked


def _rank_external_candidates(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    ranked = []
    for row in rows:
        pending_rows = int(row.get("pending_rows") or 0)
        total_parts = int(row.get("total_parts") or 0)
        if pending_rows <= 100:
            priority = "high"
        elif total_parts <= 1500:
            priority = "high"
        elif pending_rows <= 3000:
            priority = "medium"
        else:
            priority = "low"

        ranked.append({
            **row,
            "priority": priority,
            "recommended_action": "collect external vehicle fitment source before rerunning workbook enrichment",
        })

    priority_order = {"high": 0, "medium": 1, "low": 2}
    ranked.sort(key=lambda row: (priority_order[row["priority"]], row["pending_rows"], row["manufacturer"]))
    return ranked


def build_fitment_action_queues() -> Dict[str, str]:
    next_pass = _load_next_pass()
    worker_queue = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_plan": str(NEXT_PASS_FILE),
        "workbook": next_pass.get("workbook"),
        "queue_type": "worker",
        "manufacturers": _rank_worker_candidates(next_pass.get("worker_candidates", [])),
        "execution_notes": [
            "these manufacturers already have some compatible_vehicles in parts_catalog",
            "runtime worker pass should focus on OEM/SKU exact matches first, then normalized OEM token matches",
        ],
    }
    WORKER_QUEUE_FILE.write_text(json.dumps(worker_queue, ensure_ascii=False, indent=2), encoding="utf-8")

    external_queue = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_plan": str(NEXT_PASS_FILE),
        "workbook": next_pass.get("workbook"),
        "queue_type": "external_fitment",
        "manufacturers": _rank_external_candidates(next_pass.get("external_source_required", [])),
        "execution_notes": [
            "these manufacturers currently have zero compatible_vehicles in parts_catalog",
            "scraper work here must target fitment, not only additional parts, otherwise workbook pending counts will not fall",
        ],
    }
    EXTERNAL_QUEUE_FILE.write_text(json.dumps(external_queue, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "worker_queue": str(WORKER_QUEUE_FILE),
        "external_queue": str(EXTERNAL_QUEUE_FILE),
    }


if __name__ == "__main__":
    print(json.dumps(build_fitment_action_queues(), ensure_ascii=False, indent=2))