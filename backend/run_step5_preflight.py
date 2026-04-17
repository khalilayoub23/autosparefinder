from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from external_fitment_providers import (
    build_external_provider_attempts,
    provider_configuration_gaps,
    provider_enablement_snapshot,
    provider_endpoint_summary,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Step 5 provider readiness preflight.")
    parser.add_argument("--brand", default="Renault", help="Sample brand for attempt simulation")
    parser.add_argument("--part-number", default="1233014L00@", help="Sample part number for attempt simulation")
    parser.add_argument("--output", default="", help="Optional output JSON path")
    return parser.parse_args()


def _build_report(brand: str, part_number: str) -> Dict[str, Any]:
    attempts = build_external_provider_attempts(part_number=part_number, brand=brand)
    fitment_attempts: List[Dict[str, Any]] = [a for a in attempts if bool(a.get("supports_fitment", True))]
    skipped_fitment_attempts: List[Dict[str, Any]] = [
        a for a in fitment_attempts if str(a.get("skip_reason") or "").strip() or not str(a.get("url") or "").strip()
    ]
    executable_fitment_attempts = len(fitment_attempts) - len(skipped_fitment_attempts)

    report: Dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sample_brand": brand,
        "sample_part_number": part_number,
        "provider_urls": provider_endpoint_summary(),
        "provider_enablement": provider_enablement_snapshot(),
        "provider_configuration_gaps": provider_configuration_gaps(),
        "fitment_attempts_total": len(fitment_attempts),
        "fitment_attempts_executable": executable_fitment_attempts,
        "fitment_attempts_skipped": len(skipped_fitment_attempts),
        "fitment_skipped_reasons": sorted(
            {
                str(a.get("skip_reason") or "").strip()
                for a in skipped_fitment_attempts
                if str(a.get("skip_reason") or "").strip()
            }
        ),
    }

    if executable_fitment_attempts <= 0:
        report["status"] = "blocked"
        report["blocked_reason"] = "external_provider_configuration_incomplete"
        report["recommended_next_actions"] = [
            "Set required tokens/templates from provider_configuration_gaps.",
            "Re-run preflight until fitment_attempts_executable > 0.",
            "Then run blocker playbook and reduced pass.",
        ]
    else:
        report["status"] = "ready"
        report["recommended_next_actions"] = [
            "Run blocker playbook and reduced pass.",
            "Confirm json_usable_probe_attempts and inserted fitment rows.",
        ]

    return report


def main() -> None:
    args = _parse_args()
    report = _build_report(brand=args.brand, part_number=args.part_number)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
