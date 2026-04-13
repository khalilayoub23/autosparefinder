from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from sqlalchemy import text

from BACKEND_DATABASE_MODELS import async_session_factory
from catalog_scraper import _get, _sync_vehicle_fitment
from db_update_agent import ensure_part_vehicle_fitment_table, run_task
from run_fitment_enrichment_pass import PLAN_JSON_FILE, run_fitment_enrichment_pass_async


REPORT_FILE = Path(__file__).parent / "data" / "full_car_database.external_pass_report.json"


def _derive_part_number(sku: str, oem_number: str) -> str:
    if oem_number and str(oem_number).strip():
        return str(oem_number).strip()
    sku_value = str(sku or "").strip()
    if "-" in sku_value:
        return sku_value.split("-", 1)[1].strip()
    return sku_value


async def _load_candidate_parts(brand_limit: int = 5, parts_per_brand: int = 5) -> List[Dict[str, str]]:
    brands: List[str] = []
    if PLAN_JSON_FILE.exists():
        plan = json.loads(PLAN_JSON_FILE.read_text(encoding="utf-8"))
        brands = [
            str(item.get("manufacturer") or "").strip()
            for item in plan.get("recommended_next_batch", {}).get("external_source", [])
            if str(item.get("manufacturer") or "").strip()
        ]
    brands = brands[:brand_limit]

    candidates: List[Dict[str, str]] = []
    async with async_session_factory() as db:
        for brand in brands:
            rows = (await db.execute(text("""
                SELECT id, manufacturer, sku, oem_number
                FROM parts_catalog
                WHERE is_active = TRUE
                  AND manufacturer = :brand
                  AND (
                        compatible_vehicles IS NULL
                     OR jsonb_typeof(compatible_vehicles) <> 'array'
                     OR jsonb_array_length(compatible_vehicles) = 0
                  )
                ORDER BY sku
                LIMIT :limit
            """), {"brand": brand, "limit": parts_per_brand})).fetchall()
            for part_id, manufacturer, sku, oem_number in rows:
                part_number = _derive_part_number(str(sku or ""), str(oem_number or ""))
                if not part_number:
                    continue
                candidates.append({
                    "part_id": str(part_id),
                    "manufacturer": str(manufacturer or brand),
                    "sku": str(sku or ""),
                    "oem_number": str(oem_number or ""),
                    "part_number": part_number,
                })
    return candidates


async def _probe_source_access(brand: str, part_number: str) -> Dict[str, Any]:
    url = (
        "https://www.autodoc.eu/api/v1/part/applicability"
        f"?partNumber={part_number}&brand={brand}&lang=en&perPage=5"
    )
    response = await _get(url, headers={"Accept": "application/json"}, timeout=15, use_proxy=False)
    return {
        "manufacturer": brand,
        "part_number": part_number,
        "status_code": None if response is None else response.status_code,
    }


async def _count_fitment_rows(db) -> int:
    result = await db.execute(text("SELECT COUNT(*) FROM part_vehicle_fitment"))
    return int(result.scalar() or 0)


async def _run_external_fitment_pass() -> Dict[str, Any]:
    candidates = await _load_candidate_parts()
    report: Dict[str, Any] = {
        "attempted_parts": len(candidates),
        "candidate_brands": sorted({item["manufacturer"] for item in candidates}),
        "source_probes": [],
        "sync_attempts": [],
    }

    if not candidates:
        report["status"] = "skipped"
        report["reason"] = "no_external_candidates"
        report["post_pass_plan"] = await run_fitment_enrichment_pass_async()
        return report

    probed_brands = set()
    async with async_session_factory() as db:
        await ensure_part_vehicle_fitment_table(db)
        fitment_before = await _count_fitment_rows(db)

        for item in candidates:
            brand = item["manufacturer"]
            if brand not in probed_brands:
                report["source_probes"].append(await _probe_source_access(brand, item["part_number"]))
                probed_brands.add(brand)

            before = await db.execute(text("SELECT COUNT(*) FROM part_vehicle_fitment WHERE part_id = CAST(:part_id AS uuid)"), {"part_id": item["part_id"]})
            before_count = int(before.scalar() or 0)
            await _sync_vehicle_fitment(db, item["part_id"], item["part_number"], brand)
            after = await db.execute(text("SELECT COUNT(*) FROM part_vehicle_fitment WHERE part_id = CAST(:part_id AS uuid)"), {"part_id": item["part_id"]})
            after_count = int(after.scalar() or 0)
            report["sync_attempts"].append({
                "manufacturer": brand,
                "sku": item["sku"],
                "part_number": item["part_number"],
                "fitment_rows_added": max(0, after_count - before_count),
            })

        fitment_after = await _count_fitment_rows(db)
        report["part_vehicle_fitment_rows_added"] = max(0, fitment_after - fitment_before)
        report["merge_result"] = await run_task("merge_catalog_fitment_from_part_vehicle_fitment", db)

    statuses = {probe.get("status_code") for probe in report["source_probes"]}
    if report.get("part_vehicle_fitment_rows_added", 0) > 0:
        report["status"] = "ok"
    elif statuses == {403}:
        report["status"] = "blocked"
        report["reason"] = "autodoc_access_forbidden"
    else:
        report["status"] = "no_change"

    report["post_pass_plan"] = await run_fitment_enrichment_pass_async()
    return report


def run_targeted_external_fitment_pass() -> Dict[str, Any]:
    report: Dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "ok",
    }
    try:
        report.update(asyncio.run(_run_external_fitment_pass()))
    except Exception as exc:
        report["status"] = "error"
        report["error"] = str(exc)

    REPORT_FILE.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    print(json.dumps(run_targeted_external_fitment_pass(), ensure_ascii=False, indent=2))