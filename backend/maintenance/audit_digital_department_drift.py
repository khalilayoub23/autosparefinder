#!/usr/bin/env python3
"""
Script:  maintenance/audit_digital_department_drift.py
Purpose: Detect divergence between the source department skills
         (.claude/skills/dept-*/SKILL.md) and the runtime copies
         (backend/digital_department/departments/dept-*.md).

         The runtime copies are what the LLM actually sees — if the originals
         are updated but the copies are not re-synced, the deployed runtime
         silently uses stale brand/positioning/content guidelines.

         Exit codes:
           0 — all runtime files match their source originals
           1 — drift detected OR a file is missing on either side

         Separate sync command (never runs automatically from this script):
           python3 maintenance/audit_digital_department_drift.py --sync
           Copies changed source files over runtime files (never the reverse).
           Requires --confirm to actually write; dry-run by default.

Process:
  1. Walk the loader allowlist (digital_department/loader.py _ALLOWLIST).
  2. For each allowlisted department, resolve:
       source = .claude/skills/dept-{name}/SKILL.md
       runtime = digital_department/departments/dept-{name}.md
  3. Compute SHA-256 of both files.
  4. Report: MATCH / DRIFT / MISSING-SOURCE / MISSING-RUNTIME.
  5. Exit 1 if any drift or missing file.

Data Imported/Modified: none (read-only unless --sync --confirm supplied)
Last Updated: 2026-08-09
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths (relative to repo root — two levels above this script)
# ---------------------------------------------------------------------------
_SCRIPT = Path(__file__).resolve()
_BACKEND = _SCRIPT.parent.parent          # backend/
_REPO    = _BACKEND.parent               # repo root
_SKILLS  = _REPO / ".claude" / "skills"  # .claude/skills/
_RUNTIME = _BACKEND / "digital_department" / "departments"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _resolve_pairs() -> list[tuple[str, Path, Path]]:
    """Return [(name, source_path, runtime_path)] for every allowlisted dept."""
    # Import the allowlist directly rather than hard-coding it here, so
    # this script stays in sync with loader.py automatically.
    sys.path.insert(0, str(_BACKEND))
    from digital_department.loader import _ALLOWLIST  # type: ignore[import]

    pairs = []
    for name, filename in sorted(_ALLOWLIST.items()):
        # source: .claude/skills/dept-{name}/SKILL.md
        # (name may contain underscores; dir uses hyphens)
        dir_name = f"dept-{name.replace('_', '-')}"
        source = _SKILLS / dir_name / "SKILL.md"
        runtime = _RUNTIME / filename
        pairs.append((name, source, runtime))
    return pairs


def audit(verbose: bool = True) -> dict:
    """Run the drift audit. Returns a result dict suitable for testing."""
    pairs = _resolve_pairs()

    matches = []
    drifted = []
    missing_source = []
    missing_runtime = []

    for name, source, runtime in pairs:
        src_exists = source.exists()
        rnt_exists = runtime.exists()

        if not src_exists and not rnt_exists:
            # Both missing — unusual, skip with a warning
            if verbose:
                print(f"  WARN   [{name}] both source and runtime missing")
            continue

        if not src_exists:
            missing_source.append(name)
            if verbose:
                print(f"  MISS-S [{name}] source not found: {source}")
            continue

        if not rnt_exists:
            missing_runtime.append(name)
            if verbose:
                print(f"  MISS-R [{name}] runtime not found: {runtime}")
            continue

        h_src = _sha256(source)
        h_rnt = _sha256(runtime)
        if h_src == h_rnt:
            matches.append(name)
            if verbose:
                print(f"  MATCH  [{name}] {h_src[:12]}…")
        else:
            drifted.append(name)
            if verbose:
                print(f"  DRIFT  [{name}]")
                print(f"         source  {h_src[:12]}… ({source})")
                print(f"         runtime {h_rnt[:12]}… ({runtime})")

    return {
        "matches": matches,
        "drifted": drifted,
        "missing_source": missing_source,
        "missing_runtime": missing_runtime,
    }


def sync(confirm: bool = False) -> int:
    """Copy changed/missing source files over runtime files.

    Never touches .claude/skills/ — copy direction is source → runtime only.
    Returns the number of files that would be (or were) synced.
    """
    pairs = _resolve_pairs()
    synced = 0

    for name, source, runtime in pairs:
        if not source.exists():
            print(f"  SKIP   [{name}] source missing — cannot sync")
            continue

        needs_sync = (
            not runtime.exists()
            or _sha256(source) != _sha256(runtime)
        )
        if not needs_sync:
            continue

        if confirm:
            runtime.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, runtime)
            print(f"  SYNCED [{name}] {source.name} → {runtime}")
        else:
            print(f"  DRY    [{name}] would copy {source.name} → {runtime}")
        synced += 1

    return synced


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit (or sync) Digital Department source/runtime drift."
    )
    parser.add_argument(
        "--sync", action="store_true",
        help="Sync source files to runtime (dry-run unless --confirm also given).",
    )
    parser.add_argument(
        "--confirm", action="store_true",
        help="With --sync: actually write files. Without: no effect.",
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="Suppress per-file output; only print the summary.",
    )
    args = parser.parse_args()

    if args.sync:
        n = sync(confirm=args.confirm)
        if args.confirm:
            print(f"\nSync complete: {n} file(s) updated.")
        else:
            print(f"\nDry-run: {n} file(s) would be updated. Pass --confirm to apply.")
        return 0

    print("=== Digital Department Drift Audit ===")
    print(f"  Source  : {_SKILLS}")
    print(f"  Runtime : {_RUNTIME}")
    print()

    result = audit(verbose=not args.quiet)

    print()
    print(f"MATCH          : {len(result['matches'])}")
    print(f"DRIFT          : {len(result['drifted'])}")
    print(f"MISSING-SOURCE : {len(result['missing_source'])}")
    print(f"MISSING-RUNTIME: {len(result['missing_runtime'])}")

    problems = result["drifted"] + result["missing_source"] + result["missing_runtime"]
    if problems:
        print(f"\n[FAIL] {len(problems)} problem(s) detected:")
        for p in problems:
            print(f"  - {p}")
        print("\nTo sync: python3 maintenance/audit_digital_department_drift.py --sync --confirm")
        return 1

    print("\n[PASS] All runtime files match their source originals.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
