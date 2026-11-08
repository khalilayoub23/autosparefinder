"""#129: supplier VAT basis is resolved from the registry by supplier NAME (one enforcement point), importers never write
the margin price into supplier_parts.price_ils, and the double-margin repair only matches evidenced rows."""
import pathlib
import re

import pytest

import price_policy as pp

ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _reset_registry():
    pp.load_vat_bases(None)
    yield
    pp.load_vat_bases(None)


def _reg():
    pp.load_vat_bases({"Inc Co": "inclusive", "Exc Co": "exclusive", "Unk Co": "unknown", "Null Co": None})


def test_hundred_shekel_table_via_registry():
    _reg()
    inc = pp.unit_price(100, "Inc Co", "IL")
    exc = pp.unit_price(100, "Exc Co", "IL")
    assert abs(inc.total - 145.0) <= 0.02          # VAT stripped then added once (+-1 agora rounding)
    assert exc.total == 171.1                      # 100 x1.45 x1.18
    assert pp.unit_price(100, "Unk Co", "IL") is None
    assert pp.unit_price(100, "Null Co", "IL") is None      # unclassified == unknown, never silent exclusive
    assert pp.unit_price(100, "Not In Registry", "IL") is None
    assert pp.unit_price(100, "Foreign", "US").total == 145.0   # foreign: untouched by the registry


def test_unloaded_registry_keeps_legacy_and_no_name_is_exclusive():
    assert pp.unit_price(100, "Inc Co", "IL").total == 171.1    # registry never loaded -> legacy
    _reg()
    assert pp.unit_price(100, None, "IL").total == 171.1        # catalog estimate (importer cost) is ex-VAT


def test_explicit_basis_beats_registry():
    _reg()
    assert pp.unit_price(100, "Exc Co", "IL", vat_basis="inclusive").total == pp.unit_price(100, "Inc Co", "IL").total


def test_registry_lookup_is_case_and_space_insensitive():
    _reg()
    assert pp.effective_vat_basis(" inc co ") == "inclusive"


def test_idempotent_no_double_vat():
    _reg()
    once = pp.cost_ex_vat(118, "Inc Co", "IL")
    assert once == 100.0
    assert pp.cost_ex_vat(once, "Exc Co", "IL") == once


@pytest.mark.parametrize("rel", ["importers/samelet_import_v2.py", "importers/import_champion_motors.py"])
def test_importers_never_write_base_price_into_supplier_parts(rel):
    src = (ROOT / rel).read_text()
    assert not re.search(r"float\((part|row)\[.base_price.\] or 0\)", src), "margin price must not reach supplier_parts"
    assert "importer_price_ils" in src


def test_repair_rules_are_evidence_gated():
    src = (ROOT / "maintenance/repair_double_margin_129.py").read_text()
    assert "1.45 * pc.importer_price_ils" in src and "BETWEEN 1.40 AND 1.49" in src
    assert "ANY($2::uuid[])" in src                      # rule B limited to two suppliers
    assert "supplier_price_repairs_129" in src and "--rollback" in src
