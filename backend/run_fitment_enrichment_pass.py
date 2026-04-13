from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from sqlalchemy import text

from BACKEND_DATABASE_MODELS import async_session_factory
from build_full_car_database import (
    OUTPUT_XLSX_FILE,
    PENDING_JSON_FILE,
    _load_catalog_fitment_lookup,
    build_full_car_database,
)


PLAN_JSON_FILE = Path(__file__).parent / "data" / "full_car_database.next_pass.json"


async def _load_catalog_coverage() -> Dict[str, Dict[str, int]]:
    async with async_session_factory() as db:
        rows = (await db.execute(text("""
            SELECT manufacturer,
                   COUNT(*) AS total_parts,
                   COUNT(*) FILTER (
                       WHERE compatible_vehicles IS NOT NULL
                         AND jsonb_typeof(compatible_vehicles) = 'array'
                         AND jsonb_array_length(compatible_vehicles) > 0
                   ) AS parts_with_compat
            FROM parts_catalog
            WHERE is_active = TRUE
              AND manufacturer IS NOT NULL
              AND TRIM(manufacturer) <> ''
            GROUP BY manufacturer
            ORDER BY manufacturer
        """))).fetchall()
    return {
        str(manufacturer): {
            "total_parts": int(total_parts or 0),
            "parts_with_compat": int(parts_with_compat or 0),
        }
        for manufacturer, total_parts, parts_with_compat in rows
        if manufacturer
    }


async def _load_pass_state() -> tuple[
    Dict[str, Dict[str, Dict[str, list[Dict[str, Any]]]]],
    Dict[str, Dict[str, int]],
]:
    fitment_lookup = await _load_catalog_fitment_lookup()
    coverage = await _load_catalog_coverage()
    return fitment_lookup, coverage


def _load_pending_summary() -> Dict[str, Any]:
    return json.loads(PENDING_JSON_FILE.read_text(encoding="utf-8"))


async def run_fitment_enrichment_pass_async() -> Dict[str, Any]:
    fitment_lookup, coverage = await _load_pass_state()
    workbook_path = build_full_car_database(fitment_lookup=fitment_lookup)
    pending_summary = _load_pending_summary()

    worker_candidates = []
    external_source_required = []

    for manufacturer, details in sorted(pending_summary.get("manufacturers", {}).items()):
        pending_rows = int(details.get("pending_rows") or 0)
        coverage_row = coverage.get(manufacturer, {"total_parts": 0, "parts_with_compat": 0})
        entry = {
            "manufacturer": manufacturer,
            "pending_rows": pending_rows,
            "total_parts": coverage_row["total_parts"],
            "parts_with_compat": coverage_row["parts_with_compat"],
            "sample_catalog_numbers": details.get("sample_catalog_numbers", []),
        }
        if coverage_row["parts_with_compat"] > 0:
            worker_candidates.append(entry)
        else:
            external_source_required.append(entry)

    worker_candidates.sort(key=lambda row: (row["pending_rows"], row["manufacturer"]))
    external_source_required.sort(key=lambda row: (row["pending_rows"], row["manufacturer"]))

    plan = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "workbook": str(workbook_path or OUTPUT_XLSX_FILE),
        "pending_summary": str(PENDING_JSON_FILE),
        "totals": {
            "pending_rows": int(pending_summary.get("pending_rows") or 0),
            "worker_candidate_manufacturers": len(worker_candidates),
            "external_source_manufacturers": len(external_source_required),
        },
        "worker_candidates": worker_candidates,
        "external_source_required": external_source_required,
        "recommended_next_batch": {
            "worker": worker_candidates[:5],
            "external_source": external_source_required[:5],
        },
        "notes": [
            "worker_candidates already have some compatible_vehicles in parts_catalog and are the only brands the internal worker can enrich without a new external fitment source",
            "external_source_required brands currently have zero compatible_vehicles in parts_catalog, so the workbook cannot be populated further without scraper support that returns fitment or a new source file",
        ],
    }

    PLAN_JSON_FILE.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    return plan


def run_fitment_enrichment_pass() -> Dict[str, Any]:
    return asyncio.run(run_fitment_enrichment_pass_async())


if __name__ == "__main__":
    report = run_fitment_enrichment_pass()
    print(json.dumps(report, ensure_ascii=False, indent=2))