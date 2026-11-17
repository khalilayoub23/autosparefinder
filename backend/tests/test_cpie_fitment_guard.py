"""2026-11-17: page-wide "popular parts" blocks (BYD Atto 3 & 9 small EV/new-model listings) must not create fitment,
and the listing-page slug must not decide a product's category."""
import cpie_fitment_guard as g

# real items from /tmp/byd__byd_atto-3_cpie.json (23 items, 3 listing pages)
BLOCK = [("Spark plug (RIDEX, 686S0003)", "brake-pads"), ("Glow plug (RIDEX, 243G0006)", "brake-pads"),
         ("Oil filter (RIDEX, 7O0005)", "brake-pads"), ("Sump plug washer (TOPRAN, 108 647)", "brake-pads"),
         ("Number plate bulb (BOSCH, 1 987 302 810)", "oil-filter"), ("Brake Fluid (ATE, 03.9901-5801.2)", "brake-discs")]


def test_small_payload_out_of_category_items_get_no_fitment():
    for name, slug in BLOCK:
        assert g.fitment_allowed(23, name, slug) is False, name


def test_small_payload_in_category_item_is_not_blocked():
    assert g.fitment_allowed(23, "Brake pad set (BOSCH, 0 986 494 000)", "brake-pads") is True


def test_full_size_payload_is_unchanged():
    for name, slug in BLOCK:
        assert g.fitment_allowed(400, name, slug) is True


def test_unknown_name_in_small_payload_fails_closed():
    assert g.fitment_allowed(23, "Zzzq 123", "brake-pads") is False


def test_category_comes_from_the_name_not_the_page_slug():
    assert g.category_for("Glow plug (RIDEX, 243G0006)", "brake-pads") != "brakes"
    assert g.category_for("Oil filter (RIDEX, 7O0005)", "brake-pads") == "filters"
    assert g.category_for("Brake pad set (BOSCH, 0 986 494 000)", "brake-pads") == "brakes"


def test_slug_is_only_a_fallback_for_an_uninformative_name():
    assert g.category_for("Zzzq 123", "brake-pads") == "brakes"
