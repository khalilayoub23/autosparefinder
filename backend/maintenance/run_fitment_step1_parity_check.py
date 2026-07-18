from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path
from typing import Any, Dict, List

from sqlalchemy import text

from BACKEND_DATABASE_MODELS import async_session_factory
from routes.parts import search_parts
from routes.vehicles import get_compatible_parts

REPORT_PATH = Path("/app/data/fitment_step1_parity_report.json")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Step 1 parity checks between /vehicles/{id}/compatible-parts and /parts/search?vehicle_id=..."
    )
    parser.add_argument("--checks", type=int, default=20, help="Maximum parity checks to run")
    parser.add_argument("--vehicle-limit", type=int, default=5, help="How many vehicles to sample")
    parser.add_argument(
        "--queries",
        nargs="*",
        default=["", "brake", "oil filter", "spark plug"],
        help="Search queries to combine with sampled vehicles",
    )
    return parser.parse_args()


async def _load_vehicle_ids(limit: int) -> List[str]:
    async with async_session_factory() as db:
        rows = (
            await db.execute(
                text(
                    """
                    SELECT id::text
                    FROM public.vehicles
                    WHERE manufacturer IS NOT NULL AND btrim(manufacturer) <> ''
                      AND model IS NOT NULL AND btrim(model) <> ''
                      AND year IS NOT NULL
                                        ORDER BY created_at DESC NULLS LAST, id DESC
                    LIMIT :lim
                    """
                ),
                {"lim": limit},
            )
        ).fetchall()
    return [r[0] for r in rows]


def _extract_ids(payload: Dict[str, Any], path: str) -> List[str]:
    if path == "compatible":
        parts = payload.get("parts") or []
    else:
        parts = payload.get("all_parts") or payload.get("parts") or []
    out: List[str] = []
    for item in parts:
        pid = item.get("id") if isinstance(item, dict) else None
        if pid is not None:
            out.append(str(pid))
    return out


async def run_parity_check(checks: int, vehicle_limit: int, queries: List[str]) -> Dict[str, Any]:
    started = time.monotonic()
    vehicles = await _load_vehicle_ids(vehicle_limit)

    report: Dict[str, Any] = {
        "task": "fitment_step1_parity_check",
        "status": "ok",
        "checks_requested": checks,
        "vehicles_sampled": len(vehicles),
        "queries": queries,
        "results": [],
        "mismatches": [],
    }

    if not vehicles:
        report["status"] = "skipped"
        report["reason"] = "no_vehicles_available"
        report["elapsed_s"] = round(time.monotonic() - started, 2)
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report

    combinations: List[Dict[str, str]] = []
    for vid in vehicles:
        for q in queries:
            combinations.append({"vehicle_id": vid, "q": q})
    combinations = combinations[:checks]

    async with async_session_factory() as db:
        for combo in combinations:
            vehicle_id = combo["vehicle_id"]
            q = combo["q"]

            row: Dict[str, Any] = {
                "vehicle_id": vehicle_id,
                "q": q,
                "compatible_status": 200,
                "search_status": 200,
            }

            try:
                compat_json = await get_compatible_parts(
                    vehicle_id=vehicle_id,
                    q=q,
                    category=None,
                    per_type=None,
                    sort_by="price_ils",
                    db=db,
                    request=None,
                    redis=None,
                )
                search_json = await search_parts(
                    query=q,
                    vehicle_id=vehicle_id,
                    category=None,
                    per_type=None,
                    sort_by="price_ils",
                    vehicle_manufacturer=None,
                    vehicle_model=None,
                    vehicle_submodel=None,
                    vehicle_year=None,
                    db=db,
                    request=None,
                    redis=None,
                )
            except Exception as exc:
                row["parity"] = "error"
                row["compatible_error"] = str(exc)
                row["search_error"] = str(exc)
                report["results"].append(row)
                report["mismatches"].append(row)
                continue

            compat_ids = _extract_ids(compat_json, "compatible")
            search_ids = _extract_ids(search_json, "search")

            row["compatible_count"] = len(compat_ids)
            row["search_count"] = len(search_ids)
            row["same_order"] = compat_ids == search_ids
            row["same_set"] = set(compat_ids) == set(search_ids)
            row["parity"] = "ok" if row["same_order"] else "mismatch"

            if row["parity"] != "ok":
                row["compatible_ids_preview"] = compat_ids[:20]
                row["search_ids_preview"] = search_ids[:20]
                report["mismatches"].append(row)

            report["results"].append(row)

    report["checks_executed"] = len(report["results"])
    report["mismatch_count"] = len(report["mismatches"])
    report["status"] = "ok" if report["mismatch_count"] == 0 else "warning"
    report["elapsed_s"] = round(time.monotonic() - started, 2)

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


async def _amain() -> None:
    args = _parse_args()
    result = await run_parity_check(args.checks, args.vehicle_limit, args.queries)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(_amain())
