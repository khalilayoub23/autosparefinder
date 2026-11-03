"""Regression guard for the container-path defect class (2026-11-02): an importer/harvester/scraper default that points
at the developer host (/opt/autosparefinder/...) does not exist inside the container, so the source was silently skipped.

Static guard (no DB): scans the real tree this file ships in."""
from pathlib import Path

import pytest

from path_audit_scan import scan_tree

ROOT = Path(__file__).resolve().parent.parent


def test_no_executable_default_points_at_a_host_only_path():
    broken = [h for h in scan_tree(ROOT) if h["class"] == "BROKEN_IN_CONTAINER"]
    assert not broken, "host-absolute path in executable code (use app_paths):\n" + "\n".join(
        f"  {h['file']}:{h['line']}  {h['path']}" for h in broken)


def test_host_only_allowlist_is_not_stale():
    """Every allow-listed host-only file must still contain a host path - otherwise the allowlist hides a future regression."""
    from path_audit_scan import HOST_ONLY_FILES
    present = {h["file"] for h in scan_tree(ROOT) if h["class"] == "HOST_ONLY"}
    stale = [f for f in HOST_ONLY_FILES if (ROOT / f).exists() and f not in present]
    assert not stale, f"allowlisted but no longer containing a host path: {stale}"
