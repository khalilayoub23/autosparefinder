"""The weekly duplicate gate must not fail on groups the offer policy holds open (2026-11-14: 'duplicate OEM groups
remaining 1' failed weekly_maintenance two weeks running on a group merge_master_parts may not merge)."""
from pathlib import Path

SRC = (Path(__file__).resolve().parent.parent / "maintenance" / "pipeline_parity_check.py").read_text()


def test_gate_separates_real_duplicates_from_policy_held_groups():
    assert "DUPLICATE_GROUPS_SQL" in SRC
    assert "COUNT(*) FILTER (WHERE NOT all_held) AS dupes" in SRC and "FILTER (WHERE all_held) AS held" in SRC
    # a member is held only through an offer whose supplier appears on ANOTHER member of the group
    assert "HAVING COUNT(DISTINCT id) > 1" in SRC and "bool_and(held)" in SRC


def test_only_real_duplicates_fail_the_gate():
    assert 'record("duplicate OEM groups remaining", dupes == 0, dupes' in SRC
    assert 'record("duplicate groups held by offer policy", True, held' in SRC
