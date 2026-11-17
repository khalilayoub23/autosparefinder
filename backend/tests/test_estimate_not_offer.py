"""2026-11-17: a catalog estimate is NOT a supplier offer - it is never listed in `suppliers`, has no supplier_part_id and
cannot be ordered. Source-level guard (the DB-backed cart path is exercised by the existing cart tests)."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _src(rel):
    return (ROOT / rel).read_text()


def test_suppliers_endpoint_never_fabricates_an_offer():
    s = _src("routes/parts.py")
    assert '"is_base_price_fallback": True' not in s
    assert '"supplier_name":          "AutoSpareFinder"' not in s
    assert '"orderable": False' in s and "catalog_estimate" in s


def test_chat_agent_catalog_estimate_is_separate_and_not_orderable():
    s = _src("BACKEND_AI_AGENTS.py")
    assert '"is_base_price_fallback": True' not in s
    assert '"orderable": False' in s
    assert re.search(r'"catalog_estimate":\s*catalog_estimate', s)


def test_cart_add_requires_a_real_available_offer():
    s = _src("routes/cart.py")
    assert "SupplierPart.is_available == True" in s
    assert 'raise HTTPException(status_code=404, detail="Part not available from any supplier")' in s


def test_order_lines_require_a_real_supplier_part():
    s = _src("routes/orders.py")
    assert "SupplierPart.id == item.supplier_part_id" in s


def test_orderable_offer_requires_an_active_supplier_in_every_order_path():
    import price_policy as pp
    assert pp.offer_is_orderable(True, True, "Car-Parts.ie") is True
    assert pp.offer_is_orderable(True, False, "ASAP Network") is False           # inactive supplier (1,336 estimate-only parts)
    assert pp.offer_is_orderable(False, True, "Car-Parts.ie") is False
    assert pp.offer_is_orderable(True, True, "Official Manufacturer Sites") is False
    for rel, needle in (("routes/payments.py", "Supplier.is_active == True"), ("routes/cart.py", "SupplierModel.is_active == True"),
                        ("routes/orders.py", "offer_is_orderable")):
        assert needle in _src(rel), rel
    assert "NON_ORDERABLE_SUPPLIER_NAMES" in pp.ORDERABLE_OFFER_SQL or "Official Manufacturer Sites" in pp.ORDERABLE_OFFER_SQL
