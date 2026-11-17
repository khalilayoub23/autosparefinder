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


def test_same_listing_url_alone_never_proves_identity_when_holder_brand_unresolved():
    assert ci.offer_key("1", "FAE", None, bare_exists=True, same_url=True) is None                      # same page alone: fail closed
    assert ci.offer_key("1", "FAE", None, bare_exists=True, same_url=True, same_part=False) is None
    assert ci.offer_key("1", "FAE", None, bare_exists=True, same_url=True, same_part=True) == "1"       # the holder's own part row
    assert ci.offer_key("1", "FAE", "MAPCO", bare_exists=True, same_url=False) == "FAE~1"
    # same listing page but the holder brand is RESOLVED and different: never overwrite the holder (59008Set regression)
    assert ci.offer_key("1", "FAE", "MAPCO", bare_exists=True, same_url=True) == "FAE~1"
    assert ci.offer_key("1", "MAPCO", "MAPCO", bare_exists=True, same_url=True) == "1"


# ---- 2026-11-17 bare-key identity: one offer per make-specific part row, never another part's / brand's ----
import asyncio


def _choose(holders, art, brand, make, pid, url=None):
    async def f(k):
        return holders.get(k)
    return asyncio.run(ci.choose_offer_key(art, brand, make, pid, f, url))


def test_same_brand_other_make_part_gets_its_own_scoped_offer():
    holders = {"7O0005": {"name": "RIDEX 7O0005 oil filter", "supplier_url": "u", "part_id": "P-OPEL"}}
    k = _choose(holders, "7O0005", "RIDEX", "BYD", "P-BYD")
    assert k == "BYD@7O0005" and ci.article_of(k) == (None, "7O0005")   # orders still use the bare article


def test_own_part_and_first_writer_keep_bare_key_and_rerun_is_idempotent():
    assert _choose({}, "1", "RIDEX", "OPEL", "P1") == "1"
    h = {"1": {"name": "RIDEX 1 filter", "supplier_url": "u", "part_id": "P1"}}
    assert _choose(h, "1", "RIDEX", "OPEL", "P1") == "1"
    h["BYD@1"] = {"name": "RIDEX 1 filter", "supplier_url": "u", "part_id": "P2"}
    assert _choose(h, "1", "RIDEX", "BYD", "P2") == "BYD@1"        # re-harvest lands on the same scoped row


def test_scoped_key_held_by_a_third_part_fails_closed():
    h = {"1": {"name": "RIDEX 1 f", "supplier_url": "u", "part_id": "PA"}, "BYD@1": {"name": "RIDEX 1 f", "supplier_url": "u", "part_id": "PX"}}
    assert _choose(h, "1", "RIDEX", "BYD", "PB") is None


def test_keyed_holder_of_other_or_unresolved_brand_is_never_written():
    h = {"1": {"name": "MAPCO 1 f", "supplier_url": "u", "part_id": "PA"},
         "FAE~1": {"name": "Coil spring 1", "supplier_url": "u", "part_id": "PB"}}      # holder brand unresolved
    assert _choose(h, "1", "FAE", "BMW", "PC") is None
    assert _choose({}, "1", None, "BMW", "PC") is None


def test_article_of_strips_scope_then_brand():
    assert ci.article_of("BYD@FAE~62505") == ("FAE", "62505")
