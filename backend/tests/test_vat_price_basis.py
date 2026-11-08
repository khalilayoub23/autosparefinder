"""VAT price basis (2026-11-05): inclusive / exclusive / unknown follow different, correct paths; VAT exactly once."""
import price_policy as pp


def test_il_exclusive_adds_vat_once():
    u = pp.unit_price(100, supplier_country="IL", vat_basis="exclusive")
    assert (u.net, u.vat, u.total) == (145.00, 26.10, 171.10)


def test_il_inclusive_strips_vat_then_adds_once():
    assert pp.cost_ex_vat(100, supplier_country="IL", vat_basis="inclusive") == 84.75
    u = pp.unit_price(100, supplier_country="IL", vat_basis="inclusive")
    assert abs(u.total - 145.00) <= 0.02          # 100 x 1.45: VAT not stacked on VAT
    assert u.total < pp.unit_price(100, supplier_country="IL", vat_basis="exclusive").total


def test_il_unknown_is_blocked_not_assumed():
    assert pp.unit_price(100, supplier_country="IL", vat_basis="unknown") is None
    assert pp.customer_fields(100, 0, supplier_country="IL", vat_basis="unknown")["customer_total_ils"] is None
    assert pp.vat_basis_blocks(supplier_country="IL", vat_basis="garbage")


def test_not_supplied_keeps_legacy_exclusive():
    assert pp.unit_price(100, supplier_country="IL").total == 171.10


def test_foreign_never_gets_israeli_vat_whatever_basis():
    for b in ("inclusive", "exclusive", "unknown", None):
        u = pp.unit_price(100, supplier_country="US", vat_basis=b)
        assert (u.vat, u.total) == (0.0, 145.00)


def test_repeated_recalculation_is_stable():
    a = pp.unit_price(100, supplier_country="IL", vat_basis="inclusive")
    b = pp.unit_price(pp.cost_ex_vat(100, supplier_country="IL", vat_basis="inclusive"), supplier_country="IL",
                      vat_basis="exclusive")
    assert abs(a.total - b.total) < 0.01          # stripping once then adding once == the inclusive path


def test_sql_twin_mentions_local_and_unknown():
    assert "vat_price_basis" in pp.vat_basis_blocks_sql("s")
