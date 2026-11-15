"""Workstream C regression: car-parts.ie identity keys are brand-aware, deterministic and fail closed."""
import itertools
import cpie_identity as ci


def test_same_number_two_brands_never_share_keys():
    # brand A arrives first and owns the bare keys
    assert ci.part_sku("BMW", "75659", "MAPCO", None, existing=False) == "BMW-75659"
    assert ci.offer_key("75659", "MAPCO", None, bare_exists=False) == "75659"
    # brand B, same article, same make: must NOT land on A's part row nor A's offer
    assert ci.part_sku("BMW", "75659", "FAE", "MAPCO", existing=True) == "BMW-FAE~75659"
    assert ci.offer_key("75659", "FAE", "MAPCO", bare_exists=True) == "FAE~75659"


def test_same_brand_stays_on_the_same_keys_repeated_import():
    for _ in range(3):
        assert ci.part_sku("BMW", "75659", "MAPCO", "MAPCO", existing=True) == "BMW-75659"
        assert ci.offer_key("75659", "MAPCO", "MAPCO", bare_exists=True) == "75659"


def test_order_independence_of_who_owns_the_bare_key():
    # whoever arrives first owns the bare key; the other is qualified; keys are never equal
    for first, second in itertools.permutations(["MAPCO", "FAE"]):
        k1 = ci.offer_key("1", first, None, False)
        k2 = ci.offer_key("1", second, first, True)
        assert k1 == "1" and k2 == f"{second}~1" and k1 != k2


def test_fail_closed():
    assert ci.part_sku("BMW", "1", None, None, existing=False) is None            # brand unresolved -> hold
    assert ci.part_sku("BMW", "1", "FAE", None, existing=True) is None           # row exists, owner unknown -> hold
    assert ci.offer_key("1", None, None, False) is None
    assert ci.offer_key("1", "FAE", None, bare_exists=True) is None              # bare key owner unknown -> skip offer


def test_brand_token_is_safe_and_reversible():
    k = ci.offer_key("62505", "MEAT & DORIA", "MAPCO", True)
    assert k == "MEAT-DORIA~62505"
    assert ci.article_of(k) == ("MEAT-DORIA", "62505")
    assert ci.article_of("62505") == (None, "62505")


def test_brand_resolution_uses_identity_vocabulary_not_vehicle_make():
    # vehicle-first title: the brand is the known brand before the number, never 'HONDA'
    assert ci.brand_of("MAPCO 62505 Honda CRX AF fuel filter", "62505") == "MAPCO"
    assert ci.brand_of("Honda CRX fuel filter", "62505") is None


def test_same_listing_url_keeps_the_bare_key_even_if_holder_brand_unresolved():
    assert ci.offer_key("1", "FAE", None, bare_exists=True, same_url=True) == "1"
    assert ci.offer_key("1", "FAE", "MAPCO", bare_exists=True, same_url=False) == "FAE~1"
