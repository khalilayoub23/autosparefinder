"""#151 / #130: GM Israel (Cadillac, GMC, Buick) price lists have NO established VAT basis -> nothing is derived; VAT/margin exactly once when a basis is set."""
import importlib
from pathlib import Path

import pytest

import price_policy as pp

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def cad():
    return importlib.import_module("cadillac_israel_import")


@pytest.fixture()
def gmc():
    return importlib.import_module("gmc_buick_umi_import")


def test_unknown_basis_derives_nothing(cad, gmc):
    assert cad.GM_PRICE_BASIS is None and gmc.GM_PRICE_BASIS is None          # the shipped default
    assert cad.price_columns(46.54) == (None, None, None)
    assert gmc._gm_reference(46.54) is None and gmc._gm_importer_cost(46.54) is None


def test_inclusive_removes_vat_once_and_adds_margin_once(cad, monkeypatch):
    monkeypatch.setattr(cad, "GM_PRICE_BASIS", "inclusive")
    cost, ref, base = cad.price_columns(118.00)
    assert (cost, ref, base) == (100.00, 118.00, 145.00)                         # 118 / 1.18 ; reference = the quoted price ; 100 x 1.45
    assert ref / cost == pytest.approx(1 + pp.VAT_RATE)                          # VAT once, not twice
    assert base / cost == pytest.approx(pp.MARGIN)                               # margin once


def test_exclusive_adds_vat_once_and_margin_once(cad, monkeypatch):
    monkeypatch.setattr(cad, "GM_PRICE_BASIS", "exclusive")
    cost, ref, base = cad.price_columns(100.00)
    assert (cost, ref, base) == (100.00, 118.00, 145.00)


def test_gmc_helpers_follow_the_same_basis(gmc, monkeypatch):
    monkeypatch.setattr(gmc, "GM_PRICE_BASIS", "inclusive")
    assert (gmc._gm_reference(118.0), gmc._gm_importer_cost(118.0)) == (118.0, 100.0)
    monkeypatch.setattr(gmc, "GM_PRICE_BASIS", "exclusive")
    assert (gmc._gm_reference(100.0), gmc._gm_importer_cost(100.0)) == (118.0, 100.0)


def test_repeating_the_derivation_is_idempotent(cad, monkeypatch):
    monkeypatch.setattr(cad, "GM_PRICE_BASIS", "inclusive")
    first = cad.price_columns(46.54)
    assert cad.price_columns(46.54) == first                                     # a pure function of the quoted price, never of stored values


def test_customer_estimate_from_cleared_row_is_none_not_a_guess():
    assert pp.catalog_estimate(None, None, None) is None
    assert pp.catalog_estimate_price(None, None, None) is None


def test_foreign_cost_never_becomes_the_israeli_reference():
    est = pp.catalog_estimate(None, None, 119.40)
    assert est == (119.40, "foreign")                                            # online cost stays foreign (0% IL VAT) and is not max_price_ils
    assert pp.customer_fields(est[0], 0, supplier_country=est[1])["customer_vat_ils"] == 0


def test_importers_store_provenance_and_not_a_vat_claim():
    for rel in ("importers/cadillac_israel_import.py", "importers/gmc_buick_umi_import.py"):
        code = (ROOT / rel).read_text()
        assert "VAT basis of the source list is unverified" in code and "Israeli retail price (incl. VAT)" not in code, rel
        assert "'price_basis'" in code and "source_price_ils" in code
    repair = (ROOT / "maintenance" / "repair_vat_paths_151.py").read_text()
    assert "GM_UNVERIFIED" in repair and "price_basis" in repair
