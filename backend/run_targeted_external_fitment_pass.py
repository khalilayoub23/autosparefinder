from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import text

from BACKEND_DATABASE_MODELS import async_session_factory
from catalog_scraper import _get, _sync_vehicle_fitment
from db_update_agent import ensure_part_vehicle_fitment_table, run_task
from external_fitment_providers import (
    build_external_provider_attempts,
    classify_external_payload,
    provider_configuration_gaps,
    provider_enablement_snapshot,
    provider_endpoint_summary,
)
from run_fitment_enrichment_pass import PLAN_JSON_FILE, run_fitment_enrichment_pass_async


REPORT_FILE = Path(__file__).parent / "data" / "full_car_database.external_pass_report.json"
DEFAULT_EXTERNAL_PROVIDER_URLS: List[str] = []


def _provider_urls_from_env() -> List[str]:
    return provider_endpoint_summary()


def _derive_part_number(sku: str, oem_number: str) -> str:
    if oem_number and str(oem_number).strip():
        return str(oem_number).strip()
    sku_value = str(sku or "").strip()
    if "-" in sku_value:
        return sku_value.split("-", 1)[1].strip()
    return sku_value


def _parse_brand_list(raw_value: str) -> List[str]:
    out: List[str] = []
    for part in (raw_value or "").split(","):
        value = part.strip()
        if value:
            out.append(value)
    return out


def _parse_bool(value: Optional[str], default: bool) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() not in {"0", "false", "no", "off"}


def _build_provider_attempts(part_number: str, brand: str) -> List[Dict[str, Any]]:
    return build_external_provider_attempts(part_number=part_number, brand=brand)


async def _load_candidate_parts(
    brand_limit: int = 5,
    parts_per_brand: int = 5,
    forced_brands: Optional[List[str]] = None,
) -> List[Dict[str, str]]:
    brands: List[str] = []
    if forced_brands:
        brands = [str(b).strip() for b in forced_brands if str(b).strip()]
    elif PLAN_JSON_FILE.exists():
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
    provider_attempts: List[Dict[str, Any]] = []
    selected_provider: Optional[str] = None
    selected_status: Optional[int] = None

    for attempt in _build_provider_attempts(part_number=part_number, brand=brand):
        skip_reason = str(attempt.get("skip_reason") or "").strip()
        source_kind = str(attempt.get("source_kind") or "autodoc_like")
        supports_fitment = bool(attempt.get("supports_fitment", True))
        if skip_reason or not str(attempt.get("url") or "").strip():
            provider_attempts.append(
                {
                    "provider": attempt.get("provider"),
                    "source_kind": source_kind,
                    "supports_fitment": supports_fitment,
                    "use_proxy": bool(attempt.get("use_proxy", True)),
                    "status_code": None,
                    "payload_kind": "skipped",
                    "fitment_usable": False,
                    "items_count": 0,
                    "content_type": "",
                    "skip_reason": skip_reason or "empty_url",
                }
            )
            continue

        attempt_headers = {"Accept": "application/json"}
        for h_key, h_value in (attempt.get("headers") or {}).items():
            if h_key and h_value is not None:
                attempt_headers[str(h_key)] = str(h_value)

        response = await _get(
            str(attempt.get("url") or ""),
            headers=attempt_headers,
            timeout=15,
            use_proxy=bool(attempt.get("use_proxy", True)),
        )
        status = None if response is None else int(response.status_code)
        payload_meta = classify_external_payload(
            response,
            source_kind=source_kind,
            default_brand=brand,
            supports_fitment=supports_fitment,
        )
        provider_attempts.append(
            {
                "provider": attempt.get("provider"),
                "source_kind": source_kind,
                "supports_fitment": supports_fitment,
                "use_proxy": bool(attempt.get("use_proxy", True)),
                "status_code": status,
                "payload_kind": payload_meta["payload_kind"],
                "fitment_usable": bool(payload_meta.get("fitment_usable", False)),
                "items_count": int(payload_meta["items_count"]),
                "content_type": payload_meta["content_type"],
            }
        )
        if (
            status == 200
            and bool(payload_meta.get("fitment_usable", False))
            and selected_provider is None
        ):
            selected_provider = str(attempt.get("provider") or "") or None
            selected_status = status
            break

    if selected_status is None:
        for probe in provider_attempts:
            if probe.get("status_code") is not None:
                selected_status = int(probe["status_code"])
                break

    return {
        "manufacturer": brand,
        "part_number": part_number,
        "status_code": selected_status,
        "selected_provider": selected_provider,
        "provider_attempts": provider_attempts,
    }


async def _count_fitment_rows(db) -> int:
    result = await db.execute(text("SELECT COUNT(*) FROM part_vehicle_fitment"))
    return int(result.scalar() or 0)


async def _run_external_fitment_pass(
    brand_limit: int = 5,
    parts_per_brand: int = 5,
    include_post_plan: bool = True,
    forced_brands: Optional[List[str]] = None,
) -> Dict[str, Any]:
    candidates = await _load_candidate_parts(
        brand_limit=brand_limit,
        parts_per_brand=parts_per_brand,
        forced_brands=forced_brands,
    )
    report: Dict[str, Any] = {
        "attempted_parts": len(candidates),
        "candidate_brands": sorted({item["manufacturer"] for item in candidates}),
        "provider_urls": _provider_urls_from_env(),
        "provider_enablement": provider_enablement_snapshot(),
        "provider_configuration_gaps": provider_configuration_gaps(),
        "source_probes": [],
        "sync_attempts": [],
    }

    if not candidates:
        report["status"] = "skipped"
        report["reason"] = "no_external_candidates"
        if include_post_plan:
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
            sync_meta = await _sync_vehicle_fitment(db, item["part_id"], item["part_number"], brand)
            after = await db.execute(text("SELECT COUNT(*) FROM part_vehicle_fitment WHERE part_id = CAST(:part_id AS uuid)"), {"part_id": item["part_id"]})
            after_count = int(after.scalar() or 0)
            report["sync_attempts"].append({
                "manufacturer": brand,
                "sku": item["sku"],
                "part_number": item["part_number"],
                "fitment_rows_added": max(0, after_count - before_count),
                "selected_provider": (sync_meta or {}).get("selected_provider") if isinstance(sync_meta, dict) else None,
                "provider_attempts": (sync_meta or {}).get("provider_attempts", []) if isinstance(sync_meta, dict) else [],
            })

        fitment_after = await _count_fitment_rows(db)
        report["part_vehicle_fitment_rows_added"] = max(0, fitment_after - fitment_before)
        if report["part_vehicle_fitment_rows_added"] > 0:
            report["merge_result"] = await run_task("merge_catalog_fitment_from_part_vehicle_fitment", db)
        else:
            report["merge_result"] = {
                "task": "merge_catalog_fitment_from_part_vehicle_fitment",
                "status": "skipped",
                "reason": "no_new_part_vehicle_fitment_rows",
            }

    statuses = {
        int(status)
        for probe in report["source_probes"]
        for status in [
            *[
                a.get("status_code")
                for a in probe.get("provider_attempts", [])
                if isinstance(a, dict) and bool(a.get("supports_fitment", True))
            ],
        ]
        if status is not None
    }
    provider_status_totals: Dict[str, Dict[str, int]] = {}
    json_usable_probe_attempts = 0
    non_json_200_probe_attempts = 0
    fitment_skipped_probe_attempts = 0
    for probe in report["source_probes"]:
        for attempt in probe.get("provider_attempts", []) or []:
            provider_name = str(attempt.get("provider") or "unknown")
            status_key = str(attempt.get("status_code"))
            provider_status_totals.setdefault(provider_name, {})
            provider_status_totals[provider_name][status_key] = provider_status_totals[provider_name].get(status_key, 0) + 1
            if not bool(attempt.get("supports_fitment", True)):
                continue
            if str(attempt.get("payload_kind") or "") == "skipped":
                fitment_skipped_probe_attempts += 1
                continue
            if int(attempt.get("status_code") or 0) == 200:
                if bool(attempt.get("fitment_usable", False)):
                    json_usable_probe_attempts += 1
                else:
                    non_json_200_probe_attempts += 1
    report["provider_status_totals"] = provider_status_totals
    report["json_usable_probe_attempts"] = json_usable_probe_attempts
    report["non_json_200_probe_attempts"] = non_json_200_probe_attempts
    report["fitment_skipped_probe_attempts"] = fitment_skipped_probe_attempts

    if report.get("part_vehicle_fitment_rows_added", 0) > 0:
        report["status"] = "ok"
    elif statuses and statuses == {403}:
        report["status"] = "blocked"
        report["reason"] = "external_provider_access_forbidden"
    elif not statuses and fitment_skipped_probe_attempts > 0:
        report["status"] = "blocked"
        report["reason"] = "external_provider_configuration_incomplete"
    elif json_usable_probe_attempts == 0 and non_json_200_probe_attempts > 0:
        report["status"] = "blocked"
        report["reason"] = "external_provider_non_api_response"
    else:
        report["status"] = "no_change"

    if include_post_plan:
        report["post_pass_plan"] = await run_fitment_enrichment_pass_async()
    return report


def run_targeted_external_fitment_pass(
    *,
    brand_limit: Optional[int] = None,
    parts_per_brand: Optional[int] = None,
    include_post_plan: Optional[bool] = None,
    forced_brands: Optional[List[str]] = None,
    output_file: Optional[Path] = None,
) -> Dict[str, Any]:
    report: Dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "ok",
    }
    try:
        resolved_brand_limit = int(brand_limit if brand_limit is not None else os.getenv("EXTERNAL_PASS_BRAND_LIMIT", "5"))
        resolved_parts_per_brand = int(parts_per_brand if parts_per_brand is not None else os.getenv("EXTERNAL_PASS_PARTS_PER_BRAND", "5"))
        if include_post_plan is None:
            resolved_include_post_plan = _parse_bool(os.getenv("EXTERNAL_PASS_INCLUDE_POST_PLAN", "1"), default=True)
        else:
            resolved_include_post_plan = bool(include_post_plan)
        resolved_forced_brands = forced_brands if forced_brands is not None else _parse_brand_list(os.getenv("EXTERNAL_PASS_BRANDS", ""))
        report.update(asyncio.run(
            _run_external_fitment_pass(
                brand_limit=resolved_brand_limit,
                parts_per_brand=resolved_parts_per_brand,
                include_post_plan=resolved_include_post_plan,
                forced_brands=resolved_forced_brands,
            )
        ))
    except Exception as exc:
        report["status"] = "error"
        report["error"] = str(exc)

    target_file = output_file or REPORT_FILE
    target_file.parent.mkdir(parents=True, exist_ok=True)
    target_file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run reduced external fitment pass and emit JSON report.")
    parser.add_argument("--brand-limit", type=int, default=None, help="How many brands to probe")
    parser.add_argument("--parts-per-brand", type=int, default=None, help="How many parts per brand")
    parser.add_argument("--include-post-plan", default=None, help="Whether to run post-pass plan (true/false)")
    parser.add_argument("--brands", default=None, help="Comma-separated forced brands")
    parser.add_argument("--output", default=None, help="Optional output report file path")
    args = parser.parse_args()

    include_post_plan_arg: Optional[bool] = None
    if args.include_post_plan is not None:
        include_post_plan_arg = _parse_bool(args.include_post_plan, default=True)
    forced_brands_arg: Optional[List[str]] = None
    if args.brands is not None:
        forced_brands_arg = _parse_brand_list(args.brands)
    output_arg = Path(args.output) if args.output else None

    print(
        json.dumps(
            run_targeted_external_fitment_pass(
                brand_limit=args.brand_limit,
                parts_per_brand=args.parts_per_brand,
                include_post_plan=include_post_plan_arg,
                forced_brands=forced_brands_arg,
                output_file=output_arg,
            ),
            ensure_ascii=False,
            indent=2,
        )
    )