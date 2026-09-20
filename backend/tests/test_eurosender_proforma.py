"""Proforma builders — contract verified 2026-09-11 against the official
Eurosender OpenAPI spec after a live Sandbox 400 on real order 325493-26
("Extra attributes are not allowed: unitValue, countryOfOrigin"). These
tests prove the corrected field names/types and the required-data guards."""
import pytest

from services.shipping.eurosender_proforma import (
    REASON_COMMERCIAL,
    ProformaBuildError,
    build_proforma_contact,
    build_proforma_items,
)

VALID_ITEM_INPUT = {
    "category": "brakes", "quantity": 2, "content": "brake pads",
    "total_price_ils": 200.0, "weight_kg": 1.5, "country_of_origin": "IE",
}
HS_MAP = {"brakes": "870830"}


def test_build_proforma_items_uses_verified_field_names():
    items = build_proforma_items([VALID_ITEM_INPUT], HS_MAP, eur_to_ils=4.0)
    assert len(items) == 1
    item = items[0]
    assert set(item.keys()) == {"description", "country", "quantity", "weight", "value", "hsCode"}
    assert "unitValue" not in item
    assert "countryOfOrigin" not in item


def test_build_proforma_items_country_field_holds_origin():
    items = build_proforma_items([VALID_ITEM_INPUT], HS_MAP, eur_to_ils=4.0)
    assert items[0]["country"] == "IE"


def test_build_proforma_items_weight_present_and_correct():
    items = build_proforma_items([VALID_ITEM_INPUT], HS_MAP, eur_to_ils=4.0)
    assert items[0]["weight"] == 1.5


def test_build_proforma_items_value_is_integer():
    items = build_proforma_items([VALID_ITEM_INPUT], HS_MAP, eur_to_ils=4.0)
    assert isinstance(items[0]["value"], int)
    assert items[0]["value"] == round(200.0 / 4.0 / 2)  # per-unit, integer


def test_build_proforma_items_missing_hs_code_raises():
    with pytest.raises(ProformaBuildError):
        build_proforma_items([VALID_ITEM_INPUT], {}, eur_to_ils=4.0)


def test_build_proforma_items_missing_country_of_origin_raises():
    item = dict(VALID_ITEM_INPUT)
    item["country_of_origin"] = None
    with pytest.raises(ProformaBuildError):
        build_proforma_items([item], HS_MAP, eur_to_ils=4.0)


def test_build_proforma_items_missing_weight_raises():
    item = dict(VALID_ITEM_INPUT)
    item["weight_kg"] = None
    with pytest.raises(ProformaBuildError):
        build_proforma_items([item], HS_MAP, eur_to_ils=4.0)


def test_build_proforma_items_zero_weight_raises():
    item = dict(VALID_ITEM_INPUT)
    item["weight_kg"] = 0
    with pytest.raises(ProformaBuildError):
        build_proforma_items([item], HS_MAP, eur_to_ils=4.0)


def test_build_proforma_items_zero_value_raises():
    item = dict(VALID_ITEM_INPUT)
    item["total_price_ils"] = 0
    with pytest.raises(ProformaBuildError):
        build_proforma_items([item], HS_MAP, eur_to_ils=4.0)


def test_reason_commercial_constant_matches_verified_enum():
    """Live-verified 2026-09-11 against real order 325493-26: the numeric
    key '2' was rejected ("not a valid choice"); the string label
    'commercial' was accepted."""
    assert REASON_COMMERCIAL == "commercial"


# ---------------------------------------------------------------------------
# build_proforma_contact — reuses existing pickup/delivery data, never invents
# ---------------------------------------------------------------------------

def test_build_proforma_contact_maps_known_fields():
    contact = {"name": "John Doe", "email": "j@example.com", "phone": "+353111"}
    address = {"street": "1 Main St", "zip": "D01F5P2", "city": "Dublin", "country": "IE"}
    result = build_proforma_contact(contact, address)
    assert result["contactPerson"] == "John Doe"
    assert result["email"] == "j@example.com"
    assert result["phone"] == "+353111"
    assert result["street"] == "1 Main St"
    assert result["zip"] == "D01F5P2"
    assert result["city"] == "Dublin"
    assert result["country"] == "IE"


def test_build_proforma_contact_never_invents_vat_or_eori():
    contact = {"name": "A", "email": "a@b.com", "phone": "1"}
    address = {"street": "S", "zip": "Z", "city": "C", "country": "IE"}
    result = build_proforma_contact(contact, address)
    assert result["vat"] is None
    assert result["eoriNumber"] is None
    assert result["companyName"] is None


def test_build_proforma_contact_has_all_required_schema_keys():
    """Verified schema: contactPerson, companyName, phone, email, street,
    zip, city, country, vat, eoriNumber — all keys must be present even
    when the value is null."""
    contact = {"name": "A", "email": "a@b.com", "phone": "1"}
    address = {"street": "S", "zip": "Z", "city": "C", "country": "IE"}
    result = build_proforma_contact(contact, address)
    expected_keys = {"contactPerson", "companyName", "phone", "email", "street", "zip", "city", "country", "vat", "eoriNumber"}
    assert set(result.keys()) == expected_keys
