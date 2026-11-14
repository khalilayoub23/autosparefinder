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


def test_cluster_logic_counts_only_real_duplicates():
    import importlib.util
    spec = importlib.util.spec_from_file_location("ppc_mod", Path(__file__).resolve().parent.parent / "maintenance" / "pipeline_parity_check.py")
    src = SRC.split("def split_distinct_groups")[1].split("async def main")[0]
    ns: dict = {}
    exec("def split_distinct_groups" + src, ns)
    f = ns["split_distinct_groups"]
    k2a = {"g": "1", "name": "K2 T104 Brake Fluid", "oem_number": "Article №: T104", "suppliers": ["s1"]}
    k2b = dict(k2a)
    tow = {"g": "1", "name": "STEINHOF T-104 Towbar", "oem_number": "Article №: T-104", "suppliers": ["s1"]}
    assert f([k2a, k2b, tow]) == (0, 1)                       # twins held by the same supplier + a different article
    k2c = dict(k2a, suppliers=["s2"])
    assert f([k2a, k2c, tow]) == (1, 0)                       # twins from different suppliers: mergeable -> real
    assert f([dict(k2a, suppliers=[]), dict(k2a, suppliers=[])]) == (1, 0)
    assert f([k2a, tow]) == (0, 1)
