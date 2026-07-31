#!/usr/bin/env python3
"""
Script:  devtests/importer_rules_sync_test.py
Purpose: Prove docs/IMPORTER_RULES.md and maintenance/audit_importers.py agree.

Why: the whole point of the audit is that PROSE IS NOT ENFORCEMENT — 146 ERROR-level
violations existed of rules that had been documented in CLAUDE.md for months. The
failure mode this guards against is the mirror image: a rule that exists only in the
doc (nothing checks it) or only in the script (nobody knows why it fires).

Checks:
  1. every rule id emitted by the audit is NAMED in the doc;
  2. every rule id the doc claims is enforced actually exists in the script;
  3. CLAUDE.md points at both the doc and the script.

Usage:  python3 /app/devtests/importer_rules_sync_test.py
Last Updated:  2026-07-28
"""
from __future__ import annotations

import pathlib
import re
import subprocess
import sys

APP = pathlib.Path(__file__).resolve().parent.parent
REPO = APP.parent
DOC = REPO / "docs" / "IMPORTER_RULES.md"
AUDIT = APP / "maintenance" / "audit_importers.py"
CLAUDE = REPO / "CLAUDE.md"


def main() -> int:
    fails: list[str] = []

    audit_src = AUDIT.read_text(encoding="utf-8")
    doc_src = DOC.read_text(encoding="utf-8")
    claude_src = CLAUDE.read_text(encoding="utf-8")

    # 1. rule ids the script can actually emit
    emitted = set(re.findall(r'Finding\(path,\s*"([a-z0-9-]+)"', audit_src))
    print(f"rule ids implemented in the audit ({len(emitted)}):")
    for r in sorted(emitted):
        print(f"   {r}")

    # 2. every implemented rule must be named in the doc
    missing_in_doc = sorted(r for r in emitted if r not in doc_src)
    if missing_in_doc:
        fails.append(f"implemented but NOT documented: {missing_in_doc}")

    # 3. every rule the doc says is 'enforced by X' must exist in the script
    claimed = set(re.findall(r"enforced by `([a-z0-9-]+)`", doc_src))
    claimed |= set(re.findall(r"— enforced by `([a-z0-9-]+)`", doc_src))
    missing_in_code = sorted(r for r in claimed if r not in emitted)
    if missing_in_code:
        fails.append(f"documented as enforced but NOT implemented: {missing_in_code}")

    # 4. CLAUDE.md must point at both
    if "docs/IMPORTER_RULES.md" not in claude_src:
        fails.append("CLAUDE.md does not link docs/IMPORTER_RULES.md")
    if "audit_importers.py" not in claude_src:
        fails.append("CLAUDE.md does not name the enforcement script")

    # 5. the audit must actually run
    proc = subprocess.run([sys.executable, str(AUDIT)],
                          capture_output=True, text=True)
    if proc.returncode not in (0, 1):
        fails.append(f"audit crashed (exit {proc.returncode}): {proc.stderr[:200]}")

    print()
    print(f"documented-as-enforced ({len(claimed)}): {sorted(claimed)}")
    print()
    if fails:
        print("FAILURES:")
        for x in fails:
            print("   ✗", x)
        return 1
    print("✓ doc and audit are in sync; CLAUDE.md links both; audit runs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
