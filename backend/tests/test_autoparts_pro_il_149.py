"""#149: no fabricated offer price at import, and a local supplier with an unknown VAT basis is never listed to a customer."""
from pathlib import Path

import price_policy as pp

ROOT = Path(__file__).resolve().parent.parent


def test_excel_importer_never_invents_a_price():
    src = (ROOT / "importers" / "import_from_excel.py").read_text()
    assert "or 100.0" not in src and "base_price_db or" not in src
    assert 'stats["no_source_price"]' in src


def test_compare_endpoint_skips_blocked_supplier():
    src = (ROOT / "routes" / "parts.py").read_text()
    assert "if _pp.vat_basis_blocks(r[1], r[2]):\n            continue" in src


def test_unknown_basis_local_supplier_is_blocked_but_inclusive_exclusive_foreign_are_not():
    assert pp.vat_basis_blocks("X", "Israel", "unknown") is True
    assert pp.vat_basis_blocks("X", "Israel", "inclusive") is False
    assert pp.vat_basis_blocks("X", "Israel", "exclusive") is False
    assert pp.vat_basis_blocks("X", "Ireland", "unknown") is False


def test_seed_does_not_invent_a_website():
    assert "autopartspro.co.il" not in (ROOT / "maintenance" / "seed_data.py").read_text()
