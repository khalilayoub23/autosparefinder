from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from run_targeted_external_fitment_pass import (
    _load_candidate_parts,
    _parse_brand_list,
    _probe_source_access,
    _provider_urls_from_env,
)
from external_fitment_providers import (
    provider_configuration_gaps,
    provider_enablement_snapshot,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Step 5 blocker playbook probe matrix.")
    parser.add_argument("--brand-limit", type=int, default=2, help="How many brands to probe")
    parser.add_argument("--parts-per-brand", type=int, default=3, help="How many parts per brand to sample")
    parser.add_argument("--brands", default="", help="Comma-separated forced brands")
    parser.add_argument("--output", default="", help="Optional output JSON path")
    return parser.parse_args()


def _summarize_provider_attempts(probes: List[Dict[str, Any]]) -> Dict[str, Dict[str, int]]:
    out: Dict[str, Dict[str, int]] = {}
    for probe in probes:
        for attempt in probe.get("provider_attempts", []) or []:
            provider = str(attempt.get("provider") or "unknown")
            status = str(attempt.get("status_code"))
            out.setdefault(provider, {})
            out[provider][status] = out[provider].get(status, 0) + 1
    return out


async def _run_playbook(brand_limit: int, parts_per_brand: int, brands_raw: str) -> Dict[str, Any]:
    forced_brands = _parse_brand_list(brands_raw)
    candidates = await _load_candidate_parts(
        brand_limit=brand_limit,
        parts_per_brand=parts_per_brand,
        forced_brands=forced_brands,
    )

    report: Dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "brand_limit": brand_limit,
        "parts_per_brand": parts_per_brand,
        "forced_brands": forced_brands,
        "provider_urls": _provider_urls_from_env(),
        "provider_enablement": provider_enablement_snapshot(),
        "provider_configuration_gaps": provider_configuration_gaps(),
        "attempted_parts": len(candidates),
        "brands_sampled": sorted({item["manufacturer"] for item in candidates}),
        "probes": [],
    }

    if not candidates:
        report["status"] = "skipped"
        report["reason"] = "no_external_candidates"
        return report

    seen_brands = set()
    for item in candidates:
        brand = item["manufacturer"]
        if brand in seen_brands:
            continue
        seen_brands.add(brand)
        report["probes"].append(await _probe_source_access(brand, item["part_number"]))

    status_values = []
    json_usable_attempts = 0
    non_json_200_attempts = 0
    fitment_skipped_attempts = 0
    for probe in report["probes"]:
        for attempt in probe.get("provider_attempts", []) or []:
            if not bool(attempt.get("supports_fitment", True)):
                continue
            if str(attempt.get("payload_kind") or "") == "skipped":
                fitment_skipped_attempts += 1
                continue
            if attempt.get("status_code") is not None:
                status_values.append(int(attempt["status_code"]))
            if int(attempt.get("status_code") or 0) == 200:
                if bool(attempt.get("fitment_usable", False)):
                    json_usable_attempts += 1
                else:
                    non_json_200_attempts += 1

    report["provider_status_totals"] = _summarize_provider_attempts(report["probes"])
    report["all_status_codes"] = sorted(set(status_values))
    report["json_usable_attempts"] = json_usable_attempts
    report["non_json_200_attempts"] = non_json_200_attempts
    report["fitment_skipped_attempts"] = fitment_skipped_attempts

    if status_values and set(status_values) == {403}:
        report["status"] = "blocked"
        report["blocked_reason"] = "external_provider_access_forbidden"
        report["recommended_next_actions"] = [
            "Confirm provider-side allowlist and anti-bot policy for current egress IP.",
            "Switch EXTERNAL_FITMENT_PROVIDER_URLS to approved mirror/provider endpoints.",
            "Run reduced targeted pass again to validate non-403 source access.",
        ]
    elif not status_values and fitment_skipped_attempts > 0:
        report["status"] = "blocked"
        report["blocked_reason"] = "external_provider_configuration_incomplete"
        report["recommended_next_actions"] = [
            "Fill provider_configuration_gaps (token/template settings).",
            "Re-run blocker playbook and confirm fitment-capable providers execute HTTP calls.",
        ]
    elif json_usable_attempts > 0:
        report["status"] = "reachable"
        report["recommended_next_actions"] = [
            "Run reduced targeted pass with the reachable provider URLs.",
            "Verify part_vehicle_fitment_rows_added > 0 before widening scope.",
        ]
    elif non_json_200_attempts > 0:
        report["status"] = "degraded"
        report["blocked_reason"] = "external_provider_non_api_response"
        report["recommended_next_actions"] = [
            "Replace endpoint(s) returning HTML/non-JSON payloads with true API endpoints.",
            "Re-run blocker playbook to confirm JSON API accessibility.",
        ]
    else:
        report["status"] = "degraded"
        report["recommended_next_actions"] = [
            "Inspect provider_status_totals for non-403 failures (4xx/5xx mix).",
            "Adjust EXTERNAL_FITMENT_PROVIDER_URLS and rerun blocker playbook.",
        ]

    return report


def main() -> None:
    args = _parse_args()
    report = asyncio.run(_run_playbook(args.brand_limit, args.parts_per_brand, args.brands))
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()