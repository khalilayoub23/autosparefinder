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


def test_small_payload_never_creates_fitment_even_for_in_category_items():
    # consultant 2026-11-17: no vehicle-specific evidence exists in a page-wide block, in-category or not
    assert g.fitment_allowed(23, "Brake pad set (BOSCH, 0 986 494 000)", "brake-pads") is False


def test_full_size_payload_without_block_shape_is_unchanged():
    for name, slug in (("Brake pad set BOSCH 0 986 494 000", "brake-pads"), ("BMW X5 Spark plug NGK 1234", "brake-pads")):
        assert g.fitment_allowed(400, name, slug) is True


def test_small_payload_category_comes_from_the_name_not_the_page_slug():
    assert g.category_for("Glow plug (RIDEX, 243G0006)", "brake-pads", payload_size=23) != "brakes"
    assert g.category_for("Oil filter (RIDEX, 7O0005)", "brake-pads", payload_size=23) == "filters"
    assert g.category_for("Zzzq 123", "brake-pads", payload_size=23) == "brakes"      # slug only as fallback


def test_full_size_payload_keeps_the_established_category_rule():
    from category_map import categorize_on_ingest
    n = "Windshield wiper blade (BOSCH, 3 397 118 900)"
    assert g.category_for(n, "", "https://www.car-parts.ie/car-parts/x/y/wiper-blades", payload_size=400) == \
        categorize_on_ingest(name=n, url="https://www.car-parts.ie/car-parts/x/y/wiper-blades", extra="")


def test_block_item_in_a_full_size_payload_gets_no_fitment():
    # consultant 2026-11-17: block titles ride on timing-belt/clutch/alternator pages of full-size EV payloads
    assert g.fitment_allowed(400, "Spark plug (RIDEX, 686S0003)", "timing-belt") is False
    assert g.fitment_allowed(400, "Oil filter (RIDEX, 7O0005)", "clutch") is False


def test_in_category_or_vehicle_named_items_in_full_size_payloads_keep_fitment():
    assert g.fitment_allowed(400, "Oil filter (RIDEX, 7O0005)", "oil-filter") is True
    assert g.fitment_allowed(400, "BYD Yuan Plus Glow plug BERU GE133", "glow-plugs") is True   # vehicle-named: not block-shaped
    assert g.fitment_allowed(400, "Spark plug (RIDEX, 686S0003)", "") is True                    # no slug: unchanged behaviour


def test_slug_of():
    assert g.slug_of("https://www.car-parts.ie/car-parts/byd/seal/timing-belt?x=1") == "timing-belt"


# ---- C1 (2026-11-17): ICE-only products never fit a pure battery-electric vehicle ----
def test_ice_only_product_never_fits_a_bev():
    for nm in ("RIDEX 7O0002 Audi A4 B7 oil filter", "BOSCH 0 242 235 666 AUDI Q6 e-tron SUV spark plug",
               "LuK 415 0477 10 BYD Seal Saloon clutch flywheel", "RIDEX 1123T0002 AUDI Q6 e-tron SUV timing chain",
               "LIQUI MOLY 20647 MAXUS T90EV engine oil"):  # "Sump plug washer" removed 2026-11-18: weak evidence, not ICE-only
        assert g.fitment_allowed(500, nm, "x", vehicle_slug="byd/seal") is False, nm


def test_same_items_on_ice_hybrid_and_unlisted_vehicles_are_unaffected():
    for slug in ("audi/a4-b7", "byd/seal-u", "kia/niro-plus", "bmw/i3-i01", "toyota/corolla"):
        assert g.fitment_allowed(500, "RIDEX 7O0002 Audi A4 B7 oil filter", "x", vehicle_slug=slug) is True, slug


def test_bev_keeps_non_ice_fitment_and_transmission_oil():
    for nm in ("Brake pad set BOSCH 0 986 494 000", "Cabin air filter MANN CUK 2939", "Wiper blade BOSCH 3 397 007",
               "Gear oil MOTUL 8100 transmission fluid"):
        assert g.fitment_allowed(500, nm, "", vehicle_slug="tesla/model-3") is True, nm


def test_bev_list_has_no_hybrid_or_phev():
    import json, os
    d = json.load(open(os.path.join(os.path.dirname(g.__file__), "data", "bev_models.json")))
    slugs = set(d["vehicle_slugs"])
    for hybrid in ("byd/seal-u", "byd/tang", "byd/han", "kia/niro-plus", "bmw/i3-i01", "volvo/xc40-recharget5", "polestar/polestar-1-p"):
        assert hybrid not in slugs, hybrid
