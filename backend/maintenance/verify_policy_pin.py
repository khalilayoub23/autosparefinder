"""
Script: verify_policy_pin.py
Purpose: prove, from inside the running backend container, that the offer-deduplication policy is enforced by hash:
         file hash == a configured pin == runtime hash, and that a wrong pin / missing file / missing pin fail closed
         (loader refusal and gate_or_exit exit code 3; a missing file is covered by tests/test_offer_dedup_policy_pin.py). READ-ONLY: touches no database.
Process:  1. hash the policy file found by the loader (and its sibling .sha256 when present);
          2. report OFFER_POLICY_EXPECTED_SHA256 / OFFER_POLICY_REQUIRE_PIN as configured in THIS process;
          3. require_policy() in this process must succeed and report pinned=True;
          4. subprocess checks with overridden environment: wrong pin -> refused; no pin + require flag -> refused;
             gate_or_exit(real run) -> exit code 3 in each refusal case;
          5. exit 0 only if every check passes (so a deploy script can gate on it).
Data Imported/Modified: none.
Data Sources: docs/policies/OFFER_DEDUPLICATION_POLICY.md (+ .sha256 sibling), environment.
Missing Data Delegation: a missing sibling is reported, not failed (it is a review artifact; the pin is the enforcement).
Last Updated: 2026-11-07
Usage (production): docker exec autospare_backend python3 /app/maintenance/verify_policy_pin.py
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import offer_policy as op  # noqa: E402

PROBE = (
    "import sys, offer_policy as op\n"
    "try:\n"
    "    op.require_policy()\n"
    "except op.PolicyUnavailable as e:\n"
    "    print('REFUSED:', str(e)[:140])\n"
    "else:\n"
    "    print('LOADED'); sys.exit(0)\n"
    "try:\n"
    "    op.gate_or_exit('verify_policy_pin', dry_run=False)\n"
    "except SystemExit as e:\n"
    "    print('GATE_EXIT', e.code); sys.exit(10 + int(e.code or 0))\n"
)


def _probe(env_over: dict) -> tuple[bool, str]:
    env = {**os.environ, **env_over}
    r = subprocess.run([sys.executable, "-c", PROBE], env=env, capture_output=True, text=True, cwd=str(Path(__file__).resolve().parent.parent))
    out = (r.stdout + r.stderr).strip().replace("\n", " | ")
    return ("REFUSED" in r.stdout and r.returncode == 13), out[:240]  # 13 = 10 + gate exit code 3


def main() -> int:
    checks: list[tuple[str, bool, str]] = []
    try:
        info = op.require_policy()
    except op.PolicyUnavailable as exc:
        print(json.dumps({"ok": False, "runtime": "REFUSED", "reason": str(exc)}, indent=1))
        return 1
    raw = Path(info.path).read_bytes()
    file_sha = hashlib.sha256(raw).hexdigest()
    pins = op.configured_pins()
    sib = Path(info.path + ".sha256")
    sib_sha = sib.read_text().split()[0].lower() if sib.exists() else None
    checks.append(("runtime hash == file hash", info.sha256 == file_sha, info.sha256[:16]))
    checks.append(("a configured pin lists the file hash", file_sha in pins, f"{len(pins)} pin(s)"))
    checks.append(("loader reports pinned=True", info.pinned is True, str(info.pinned)))
    checks.append(("OFFER_POLICY_REQUIRE_PIN is set", os.getenv("OFFER_POLICY_REQUIRE_PIN", "") in ("1", "true", "yes", "on"), os.getenv("OFFER_POLICY_REQUIRE_PIN", "")))
    checks.append(("sibling .sha256 matches (if present)", sib_sha in (None, file_sha), str(sib_sha)[:16]))
    ok, out = _probe({"OFFER_POLICY_EXPECTED_SHA256": "0" * 64})
    checks.append(("wrong pin -> refused, gate exit 3", ok, out))
    ok, out = _probe({"OFFER_POLICY_EXPECTED_SHA256": "", "OFFER_POLICY_REQUIRE_PIN": "1"})
    checks.append(("no pin + require flag -> refused, gate exit 3", ok, out))
    report = {"policy_version": info.version, "policy_sha256": info.sha256, "path": info.path, "pins": [p[:16] for p in pins],
              "checks": [{"check": n, "ok": o, "detail": d} for n, o, d in checks]}
    report["ok"] = all(o for _, o, _ in checks)
    print(json.dumps(report, indent=1))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
