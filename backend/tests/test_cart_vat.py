"""
Tests for conditional VAT on cart items.

Business rule: IL supplier → 18% VAT on sell price; foreign supplier → 0% VAT.
`is_local_supplier` is the single authoritative function.
"""
import pytest
from BACKEND_AI_AGENTS import is_local_supplier


# ── Unit tests for is_local_supplier ────────────────────────────────────────

def test_il_country_codes():
    assert is_local_supplier(supplier_country="IL") is True
    assert is_local_supplier(supplier_country="il") is True
    assert is_local_supplier(supplier_country="Israel") is True
    assert is_local_supplier(supplier_country="israel") is True
    assert is_local_supplier(supplier_country="ישראל") is True


def test_foreign_country_codes():
    assert is_local_supplier(supplier_country="IE") is False   # car-parts.ie
    assert is_local_supplier(supplier_country="GB") is False   # SNG Barratt
    assert is_local_supplier(supplier_country="US") is False   # eBay/RockAuto
    assert is_local_supplier(supplier_country="DE") is False   # Autodoc
    assert is_local_supplier(supplier_country="CN") is False   # AliExpress


def test_country_takes_priority_over_name():
    # If country is set, name is irrelevant
    assert is_local_supplier(supplier_name="autoparts pro il", supplier_country="US") is False
    assert is_local_supplier(supplier_name="RockAuto", supplier_country="IL") is True


def test_name_fallback_when_no_country():
    assert is_local_supplier(supplier_name="autoparts pro il") is True
    assert is_local_supplier(supplier_name="AliExpress") is False
    assert is_local_supplier(supplier_name="car-parts.ie") is False


def test_none_inputs_are_foreign():
    # Unknown supplier → no VAT (fail-closed — but per the docstring fallback to local rate;
    # when called from _customer_unit_price with None/None it uses _VAT_RATE locally.
    # Here the function itself returns False for None/None — it's the caller's job to
    # apply the default when the supplier is completely unknown.)
    assert is_local_supplier() is False
    assert is_local_supplier(supplier_name=None, supplier_country=None) is False


# ── Cart VAT formula tests ────────────────────────────────────────────────────

_VAT = 0.18
_MARGIN = 1.45


def _sell(cost):
    return round(cost * _MARGIN, 2)


def _vat_for_item(price, qty, is_il):
    """Mirrors the Cart.jsx per-item logic."""
    line = price * qty
    return line * _VAT if is_il else 0.0


def test_il_only_cart():
    """All items from IL supplier → full 18% VAT on each line."""
    items = [
        {"price": _sell(100), "qty": 2, "is_il": True},
        {"price": _sell(50),  "qty": 1, "is_il": True},
    ]
    vat = sum(_vat_for_item(i["price"], i["qty"], i["is_il"]) for i in items)
    subtotal = sum(i["price"] * i["qty"] for i in items)
    assert vat == pytest.approx(subtotal * _VAT, abs=0.01)
    assert vat > 0


def test_foreign_only_cart():
    """All items from foreign suppliers → zero VAT."""
    items = [
        {"price": _sell(80), "qty": 3, "is_il": False},
        {"price": _sell(60), "qty": 2, "is_il": False},
    ]
    vat = sum(_vat_for_item(i["price"], i["qty"], i["is_il"]) for i in items)
    assert vat == 0.0


def test_mixed_cart():
    """IL items incur VAT; foreign items do not; mixed total is strictly between 0 and all-IL."""
    il_price = _sell(100)
    foreign_price = _sell(80)

    items = [
        {"price": il_price,      "qty": 1, "is_il": True},
        {"price": foreign_price, "qty": 1, "is_il": False},
    ]
    vat = sum(_vat_for_item(i["price"], i["qty"], i["is_il"]) for i in items)

    all_il_vat = (il_price + foreign_price) * _VAT
    assert 0 < vat < all_il_vat
    assert vat == pytest.approx(il_price * _VAT, abs=0.01)


def test_empty_cart():
    """Empty cart → zero VAT and zero subtotal."""
    items = []
    vat = sum(_vat_for_item(i["price"], i["qty"], i["is_il"]) for i in items)
    subtotal = sum(i["price"] * i["qty"] for i in items)
    assert vat == 0.0
    assert subtotal == 0.0
