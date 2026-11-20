"""Listing Identity Guard (2026-11-19): pure decision rules. No database, no network."""
import pytest

from services import ebay_listing_identity as g
from services.ebay_listing_identity import Live, Stored, check_candidate, check_refresh, legacy_item_id

URL = "https://www.ebay.com/itm/123456789012?hash=abc"
ST = Stored(item_id="123456789012", oem_number="A2465200123", brand="Mercedes-Benz", seller="shopA")


def live(**kw):
    base = dict(ok=True, status="ok", item_id="123456789012", title="Mercedes A2465200123 brake pad", seller="shopA", condition="New",
                price=100.0, currency="USD", shipping=12.0, ships_to_israel=True)
    base.update(kw)
    return Live(**base)


@pytest.mark.parametrize("url,expected", [
    (URL, "123456789012"), ("https://www.ebay.com/itm/Brake-Pad/123456789012?x=1", "123456789012"),
    ("v1|123456789012|0", "123456789012"), ("123456789012", "123456789012"), (None, None), ("https://example.com/p/1", None)])
def test_legacy_item_id(url, expected):
    assert legacy_item_id(url) == expected


# ---- refresh: same listing --------------------------------------------------------------------------------------
def test_same_listing_price_increase_and_decrease_are_both_exact_matches():
    assert check_refresh(ST, live(price=999.0)).code == g.EXACT_MATCH      # a big increase is legitimate, never "suspicious"
    assert check_refresh(ST, live(price=1.0)).code == g.EXACT_MATCH
    assert check_refresh(ST, live()).ok


def test_seller_mismatch_blocks_the_write():
    v = check_refresh(ST, live(seller="someone-else"))
    assert v.code == g.SELLER_MISMATCH and not v.ok


def test_seller_unknown_is_not_a_mismatch_for_the_same_item_id():
    assert check_refresh(Stored("123456789012", "A2465200123", "Mercedes-Benz", None), live(seller="x")).ok


def test_different_listing_never_updates_the_offer():
    v = check_refresh(ST, live(item_id="999999999999"))
    assert v.code == g.DIFFERENT_LISTING and not v.ok


def test_condition_change_is_a_variant_mismatch():
    for cond in ("Used", "For parts or not working", "Seller refurbished", ""):
        assert check_refresh(ST, live(condition=cond)).code == g.VARIANT_MISMATCH


def test_typed_variant_offer_is_never_refreshed():
    v = check_refresh(Stored("123456789012", "A2465200123", part_type="aftermarket"), live())
    assert v.code == g.VARIANT_MISMATCH and not v.ok


def test_missing_listing_marks_unavailable_but_is_not_a_write():
    v = check_refresh(ST, Live(ok=False, status="ended"))
    assert v.code == g.MISSING_LISTING and v.makes_unavailable and not v.ok


def test_api_error_is_ambiguous_and_changes_nothing():
    v = check_refresh(ST, Live(ok=False, status="error"))
    assert v.code == g.AMBIGUOUS_IDENTITY and not v.ok and not v.makes_unavailable and v.retry_days == 1


def test_no_israel_shipping_makes_unavailable():
    v = check_refresh(ST, live(ships_to_israel=False))
    assert v.code == g.NO_ISRAEL_SHIPPING and v.makes_unavailable


def test_no_stored_item_id_is_reported_not_guessed():
    assert check_refresh(Stored(None, "A2465200123"), live()).code == g.NO_STORED_LISTING


@pytest.mark.parametrize("cur", ["EUR", "GBP", "", "usd1"])
def test_foreign_currency_is_never_treated_as_usd(cur):
    assert check_refresh(ST, live(currency=cur)).code == g.CURRENCY_AMBIGUOUS


def test_ils_currency_is_accepted():
    assert check_refresh(ST, live(currency="ILS")).ok


@pytest.mark.parametrize("price", [None, 0.0, -5.0])
def test_non_positive_price_is_ambiguous(price):
    assert check_refresh(ST, live(price=price)).code == g.AMBIGUOUS_IDENTITY


def test_shipping_unknown_is_allowed_and_kept_separate_from_price():
    v = check_refresh(ST, live(shipping=None))
    assert v.ok                                       # the caller then keeps the STORED shipping (COALESCE)
    assert g.normalize_total(100.0, None) == (100.0, 0.0)
    assert g.normalize_total(100.0, 12.5) == (100.0, 12.5)


def test_negative_shipping_is_ambiguous():
    assert check_refresh(ST, live(shipping=-1.0)).code == g.SHIPPING_AMBIGUOUS


# ---- candidates (search results) ---------------------------------------------------------------------------------
OEM, BRAND = "A2465200123", "Mercedes-Benz"


def test_good_candidate_is_a_verified_new_listing():
    assert check_candidate(OEM, BRAND, live()).code == g.VERIFIED_NEW_LISTING


def test_title_spacing_and_dashes_do_not_hide_the_part_number():
    assert check_candidate(OEM, BRAND, live(title="Genuine Mercedes A 246 520 01 23 pad")).ok
    assert check_candidate(OEM, BRAND, live(title="pad", mpn="A-246-520-0123")).ok


def test_candidate_without_the_part_number_is_a_product_mismatch():
    assert check_candidate(OEM, BRAND, live(title="Mercedes brake pad set front")).code == g.PRODUCT_MISMATCH


def test_candidate_naming_another_make_is_a_product_mismatch():
    assert check_candidate(OEM, BRAND, live(title="BMW A2465200123 compatible pad")).code == g.PRODUCT_MISMATCH


def test_ordinary_words_are_not_makes():
    assert check_candidate(OEM, BRAND, live(title="A2465200123 seat belt buckle minimum")).ok


def test_candidate_condition_must_be_new():
    assert check_candidate(OEM, BRAND, live(condition="Used")).code == g.VARIANT_MISMATCH


def test_candidate_currency_ambiguity():
    assert check_candidate(OEM, BRAND, live(currency="EUR")).code == g.CURRENCY_AMBIGUOUS


def test_candidate_not_shipping_to_israel():
    assert check_candidate(OEM, BRAND, live(ships_to_israel=False)).code == g.NO_ISRAEL_SHIPPING


def test_a_different_seller_never_replaces_a_stored_listing_that_is_not_verified_missing():
    v = check_candidate(OEM, BRAND, live(item_id="999999999999", seller="other"), existing_item_id="123456789012")
    assert v.code == g.DIFFERENT_LISTING and not v.ok


def test_replacement_is_allowed_only_after_the_stored_listing_is_verified_missing():
    v = check_candidate(OEM, BRAND, live(item_id="999999999999", seller="other"), existing_item_id="123456789012",
                        existing_verified_missing=True)
    assert v.ok


def test_short_part_numbers_cannot_match_by_accident():
    assert check_candidate("A12", BRAND, live(title="Mercedes A12 pad")).code == g.PRODUCT_MISMATCH


def test_only_two_verdicts_may_write():
    assert {c for c in (g.EXACT_MATCH, g.VERIFIED_NEW_LISTING)} == {c for c in vars(g).values() if isinstance(c, str)
            and c in (g.EXACT_MATCH, g.VERIFIED_NEW_LISTING)}
    for code in (g.SELLER_MISMATCH, g.PRODUCT_MISMATCH, g.VARIANT_MISMATCH, g.MISSING_LISTING, g.NO_ISRAEL_SHIPPING,
                 g.DIFFERENT_LISTING, g.AMBIGUOUS_IDENTITY, g.CURRENCY_AMBIGUOUS, g.SHIPPING_AMBIGUOUS, g.NO_STORED_LISTING):
        assert not g.Verdict(code, "x", 1).ok


def test_unsupported_listing_is_ambiguous_with_a_long_retry():
    v = check_refresh(ST, Live(ok=False, status="unsupported"))
    assert v.code == g.AMBIGUOUS_IDENTITY and not v.ok and v.retry_days == 14


def _item(**kw):
    base = {"itemId": "v1|123456789012|0", "legacyItemId": "123456789012", "title": "x", "condition": "New",
            "price": {"value": "10", "currency": "USD"}, "seller": {"username": "s"}}
    base.update(kw)
    return base


def test_item_without_shipping_options_but_worldwide_ships_to_israel_with_unknown_cost():
    from services.suppliers.ebay_supplier import EbaySupplier
    live = EbaySupplier.live_from_item(_item(shipToLocations={"regionIncluded": [{"regionId": "WORLDWIDE", "regionType": "WORLDWIDE"}]}))
    assert live.ships_to_israel is True and live.shipping is None
    assert check_refresh(Stored("123456789012", "X"), live).code == g.EXACT_MATCH


def test_item_without_shipping_options_and_without_israel_does_not_ship():
    from services.suppliers.ebay_supplier import EbaySupplier
    us = EbaySupplier.live_from_item(_item(shipToLocations={"regionIncluded": [{"regionId": "US"}]}))
    assert us.ships_to_israel is False
    excl = EbaySupplier.live_from_item(_item(shipToLocations={"regionIncluded": [{"regionId": "WORLDWIDE"}], "regionExcluded": [{"regionId": "IL"}]}))
    assert excl.ships_to_israel is False
    assert check_refresh(Stored("123456789012", "X"), us).code == g.NO_ISRAEL_SHIPPING


def test_item_with_israel_listed_explicitly_ships():
    from services.suppliers.ebay_supplier import EbaySupplier
    assert EbaySupplier.live_from_item(_item(shipToLocations={"regionIncluded": [{"regionId": "IL"}]})).ships_to_israel is True


def test_world_region_only_is_ambiguous_not_a_no_ship_verdict():
    from services.suppliers.ebay_supplier import EbaySupplier
    asia = EbaySupplier.live_from_item(_item(shipToLocations={"regionIncluded": [{"regionId": "ASIA", "regionType": "WORLD_REGION"}]}))
    assert asia.ships_to_israel is False and asia.ship_region_unclear is True
    v = check_refresh(Stored("123456789012", "X"), asia)
    assert v.code == g.AMBIGUOUS_IDENTITY and not v.makes_unavailable and v.retry_days == 14


def test_worldwide_with_israel_excluded_is_a_real_no_ship():
    from services.suppliers.ebay_supplier import EbaySupplier
    live = EbaySupplier.live_from_item(_item(shipToLocations={"regionIncluded": [{"regionId": "WORLDWIDE"}], "regionExcluded": [{"regionId": "IL"}]}))
    assert live.ships_to_israel is False and live.ship_region_unclear is False
    assert check_refresh(Stored("123456789012", "X"), live).code == g.NO_ISRAEL_SHIPPING


def test_no_shipping_information_at_all_is_ambiguous_never_a_no_ship_verdict():
    """Absence of shippingOptions AND shipToLocations proves nothing (2026-11-20 rule)."""
    from services.suppliers.ebay_supplier import EbaySupplier
    live = EbaySupplier.live_from_item(_item())
    assert live.ships_to_israel is False and live.ship_region_unclear is True
    v = check_refresh(Stored("123456789012", "X"), live)
    assert v.code == g.AMBIGUOUS_IDENTITY and not v.makes_unavailable


def test_any_world_region_without_israel_exclusion_is_ambiguous():
    from services.suppliers.ebay_supplier import EbaySupplier
    for rid in ("EUROPE", "ASIA", "MIDDLE_EAST", "AFRICA"):
        live = EbaySupplier.live_from_item(_item(shipToLocations={"regionIncluded": [{"regionId": rid}]}))
        assert live.ship_region_unclear is True and not check_refresh(Stored("123456789012", "X"), live).makes_unavailable


def test_country_list_without_israel_and_explicit_exclusion_remain_real_no_ship():
    from services.suppliers.ebay_supplier import EbaySupplier
    us = EbaySupplier.live_from_item(_item(shipToLocations={"regionIncluded": [{"regionId": "US"}, {"regionId": "CA"}]}))
    assert us.ship_region_unclear is False and check_refresh(Stored("123456789012", "X"), us).code == g.NO_ISRAEL_SHIPPING
    ex = EbaySupplier.live_from_item(_item(shipToLocations={"regionIncluded": [{"regionId": "EUROPE"}], "regionExcluded": [{"regionId": "IL"}]}))
    assert ex.ship_region_unclear is False and check_refresh(Stored("123456789012", "X"), ex).code == g.NO_ISRAEL_SHIPPING
