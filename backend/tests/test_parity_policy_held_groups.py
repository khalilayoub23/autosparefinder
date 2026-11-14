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


def _gate():
    import importlib.util
    spec = importlib.util.spec_from_file_location("ppc_mod", Path(__file__).resolve().parent.parent / "maintenance" / "pipeline_parity_check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _m(name, num, suppliers, pt="aftermarket"):
    return {"g": "1", "id": name + str(suppliers), "name": name, "oem_number": num, "suppliers": suppliers, "part_type": pt,
            "manufacturer_id": "m1", "manufacturer": "Toyota"}


def test_cluster_logic_counts_only_real_duplicates():
    f = _gate().split_distinct_groups
    k2a = _m("K2 T104 Brake Fluid", "Article №: T104", ["s1"])
    k2b = dict(k2a)
    tow = _m("STEINHOF T-104 Towbar", "Article №: T-104", ["s1"])
    assert f([k2a, k2b, tow]) == (0, 1)                       # twins held by the same supplier + a different article
    k2c = dict(k2a, suppliers=["s2"])
    assert f([k2a, k2c, tow]) == (1, 0)                       # twins from different suppliers: mergeable -> real
    assert f([dict(k2a, suppliers=[]), dict(k2a, suppliers=[])]) == (1, 0)
    assert f([k2a, tow]) == (0, 1)


def test_gate_judges_per_article_and_reports_uncertain_groups_with_evidence():
    g = _gate().classify_groups
    wiper = _m("SWF 119272 SKODA Scala Hatchback windshield wipers", "119272", ["s1"])
    pipe = _m("TOPRAN 119 272 Rocker cover breather pipe", "119 272", ["s2"])
    r = g([wiper, pipe])
    assert (r["real"], r["different"], r["uncertain"]) == (0, 1, 0)          # whitespace collision: seen, not a duplicate
    veh_a = _m("Opel Corsa D brake hose rear", "32352", ["s1"])
    veh_b = _m("BMW Z8 E52 Tailgate strut 32352", "32352", ["s2"])
    r = g([veh_a, veh_b])
    assert (r["real"], r["different"], r["uncertain"]) == (0, 0, 1)          # unbranded / vehicle-first: fail closed
    ev = r["evidence"][0]
    assert ev["verdict"] == "uncertain" and len(ev["rows"]) == 2 and ev["rows"][0]["vs_other_clusters"] == ["brand_unknown"]
    oem_a = _m("מפריד שמן", "06Q103495H", [], pt="oem")
    oem_b = _m("Vapor Canister Purge Valve Assembly", "06Q-103-495-H", [], pt="oem")
    assert g([oem_a, oem_b])["real"] == 1                                    # true duplicate IS detected
    assert g([oem_a, oem_b]) == g([oem_a, oem_b])                            # idempotent
    mixed = g([oem_a, oem_b, dict(pipe, g="1")])
    assert mixed["real"] == 1                                                # mixed group: the true duplicate still counts


def test_gate_normalisation_matches_the_merge():
    assert "replace(replace(replace(oem_number,' ',''),'-',''),'.','')" in SRC
