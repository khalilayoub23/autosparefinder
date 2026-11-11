"""chev_provenance_1384: provenance only, never a price (2026-11-11 closure, Issue 1)."""
import pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "maintenance"))
import chev_provenance_1384 as cp


def test_chosen_price_is_the_file_price_that_reproduces_the_stored_cost():
    pv = cp.provenance([14.1, 71.0], 60.17, "r1")                 # 71.0 / 1.18 = 60.17
    assert pv["source_price_ils"] == 71.0 and pv["source_prices_in_file"] == [14.1, 71.0]
    assert pv["candidate_cost_if_inclusive"] == 60.17 and pv["candidate_cost_if_exclusive"] == 71.0


def test_basis_stays_unverified_and_evidence_is_attached():
    pv = cp.provenance([118.0], 100.0, "r1")
    assert pv["price_basis"] == "unverified" and len(pv["price_basis_evidence"]) >= 3


def test_unreproducible_rows_get_nothing():
    assert cp.provenance([500.0], 100.0, "r1") is None


def test_script_writes_only_specifications():
    src = open(cp.__file__, encoding="utf8").read()
    body = src.split('"""', 2)[2]
    assert "SET specifications" in body
    for col in ("importer_price_ils =", "max_price_ils =", "min_price_ils =", "base_price ="):
        assert col not in body
