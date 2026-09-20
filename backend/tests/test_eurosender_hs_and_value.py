"""HS code policy (no unconditional universal fallback) and declared-value
computation (customer price only, supplier cost structurally excluded)."""
import pytest

from services.shipping.eurosender_declared_value import (
    DeclaredValueError,
    declared_value_eur,
)
from services.shipping.eurosender_hs_codes import HsVerdict, classify_hs


# ---------------------------------------------------------------------------
# HS codes
# ---------------------------------------------------------------------------

def test_brakes_maps_to_specific_code():
    d = classify_hs("brakes")
    assert d.verdict == HsVerdict.MAPPED
    assert d.hs_code == "870830"


def test_gearbox_maps_to_specific_code():
    assert classify_hs("gearbox").hs_code == "870840"


def test_suspension_maps_to_specific_code():
    assert classify_hs("suspension").hs_code == "870880"


def test_wheels_maps_to_specific_code():
    assert classify_hs("wheels-bearings").hs_code == "870870"


def test_lighting_maps_to_specific_code_not_8708():
    d = classify_hs("lighting")
    assert d.hs_code == "851220"
    assert not d.hs_code.startswith("8708")


def test_filters_maps_to_specific_code():
    assert classify_hs("filters").hs_code == "842123"


def test_unmapped_category_is_manual_review_never_870899():
    d = classify_hs("some-unmapped-category")
    assert d.verdict == HsVerdict.MANUAL_REVIEW
    assert d.hs_code is None


def test_no_category_anywhere_in_the_map_uses_bare_870899_as_unconditional_default():
    """Verifies the owner's explicit prohibition: 870899 must never appear as
    an unconditional universal fallback value in the mapping table itself."""
    from services.shipping.eurosender_hs_codes import _HS_MAP
    assert "870899" not in _HS_MAP.values()


def test_overrides_env_takes_precedence(monkeypatch):
    monkeypatch.setenv("HS_CODE_OVERRIDES", '{"exhaust": "870892"}')
    d = classify_hs("exhaust")
    assert d.verdict == HsVerdict.MAPPED
    assert d.hs_code == "870892"


def test_malformed_overrides_json_falls_back_safely(monkeypatch):
    monkeypatch.setenv("HS_CODE_OVERRIDES", "{not valid json")
    d = classify_hs("brakes")
    assert d.hs_code == "870830"  # built-in map still works


# ---------------------------------------------------------------------------
# Declared value
# ---------------------------------------------------------------------------

def test_declared_value_uses_customer_item_prices():
    # 1000 ILS / 4.0 rate = 250 EUR
    value = declared_value_eur([1000.0], eur_to_ils=4.0)
    assert value == 250


def test_declared_value_sums_multiple_items():
    value = declared_value_eur([100.0, 200.0, 300.0], eur_to_ils=4.0)
    assert value == 150  # (100+200+300)/4 = 150


def test_declared_value_never_less_than_1_eur():
    value = declared_value_eur([1.0], eur_to_ils=4.0)
    assert value >= 1


def test_declared_value_rejects_empty_items():
    with pytest.raises(DeclaredValueError):
        declared_value_eur([])


def test_declared_value_rejects_zero_total():
    with pytest.raises(DeclaredValueError):
        declared_value_eur([0.0, 0.0])


def test_declared_value_rejects_invalid_rate():
    with pytest.raises(DeclaredValueError):
        declared_value_eur([100.0], eur_to_ils=0)


def test_declared_value_function_signature_cannot_accept_supplier_cost():
    """Structural proof (not just convention): declared_value_eur() has
    exactly two parameters — a list of customer-facing prices and an FX rate.
    There is no parameter through which a supplier cost, importer_price_ils,
    or shipping cost figure could be passed in."""
    import inspect
    sig = inspect.signature(declared_value_eur)
    param_names = set(sig.parameters.keys())
    assert param_names == {"item_total_prices_ils", "eur_to_ils"}
    for forbidden in ("supplier_cost", "importer_price", "cost_ils", "shipping_cost"):
        assert forbidden not in param_names


def test_declared_value_reads_env_when_rate_not_given(monkeypatch):
    monkeypatch.setenv("EUR_TO_ILS", "5.0")
    value = declared_value_eur([500.0])
    assert value == 100
