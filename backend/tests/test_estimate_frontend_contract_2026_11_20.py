"""Regression (2026-11-20): customer-facing estimate contract.
- routes/parts.py must not use the `:name::type` cast inside text() (collides with bind params and aborts the
  transaction - it silently poisoned every aftermarket search that reached the barcode lookup).
- an estimate part (no offer) must be unorderable server-side: add_cart_item requires an available, active-supplier offer.
- the search payload keeps `is_estimate` / `customer_estimate_ils`, which the frontend (utils/offerPrice.js) relies on.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _src(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def test_no_bind_param_double_colon_cast_in_parts_routes():
    src = _src("routes/parts.py")
    bad = re.findall(r"text\(\s*f?[\"']{1,3}[^\"']*?[^:\w]:[a-z_]+::[a-z]+", src, flags=re.S)
    assert not bad, bad[:3]
    assert ":pid::uuid" not in src


def test_cart_requires_available_offer_for_orderability():
    src = _src("routes/cart.py")
    body = src.split("async def add_cart_item", 1)[1].split("@router.delete", 1)[0]
    assert "SupplierPart.is_available == True" in body
    assert "SupplierModel.is_active == True" in body
    assert 'status_code=404, detail="Part not available from any supplier"' in body
    assert "_customer_unit_price" in body          # price is server-computed, never client-supplied


def test_search_exposes_estimate_flags_for_frontend():
    src = _src("routes/parts.py")
    assert 'part_dict["is_estimate"]' in src
    assert 'part_dict["customer_estimate_ils"]' in src
    # the estimate is never injected as a supplier offer
    assert '"orderable": False' in src


def test_estimate_policy_has_no_base_price_input():
    import price_policy as pp
    import inspect
    params = inspect.signature(pp.catalog_estimate).parameters
    assert "base_price" not in params
