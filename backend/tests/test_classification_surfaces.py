"""Customer-facing classification is wired on every surface (2026-09-21): a class label must reach search, compare,
cart, wishlist, chat, WhatsApp checkout and the partner API, and the label must distinguish the three classes."""
import pathlib
import offer_classification as oc

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _src(rel):
    return (ROOT / rel).read_text()


def test_three_classes_have_distinct_customer_labels():
    assert oc.offer_label("oem", None) == "oem"
    assert oc.offer_label("oem", "aftermarket") == "aftermarket"        # the offer's own class wins over the part's
    assert oc.offer_label("oem", "oe_equivalent") == "oe_equivalent"
    assert len({oc.class_label_he(c) for c in ("oem", "oe_equivalent", "aftermarket")}) == 3


def test_every_customer_surface_emits_the_label_and_none_exposes_raw_cost():
    assert "offer_label(" in _src("routes/parts.py") and '"part_type"' in _src("routes/parts.py")
    cart = _src("routes/cart.py")
    assert cart.count("offerPartType") >= 2                                # cart lines + wishlist
    assert "class_label_he" in _src("routes/payments.py")                  # checkout description
    assert "part_type" in _src("routes/public_api.py") and "offer_label(" in _src("routes/public_api.py")
    chat = _src("BACKEND_AI_AGENTS.py")
    assert "class_label_he" in chat and "סוג המוצר" in chat


def test_raw_supplier_usd_cost_is_never_serialized_to_customers():
    src = _src("routes/parts.py")
    assert '"price_usd":' in src
    for line in src.splitlines():
        if line.strip().startswith('"price_usd":') and "None" not in line:
            raise AssertionError(f"raw price_usd exposed: {line.strip()}")


def test_search_offers_never_carry_the_raw_supplier_cost_as_price_ils():
    """Found by the bounded 200-run verification: search emitted `price_ils` = RAW supplier cost on every offer."""
    src = _src("routes/parts.py")
    assert "round(price_ils, 2) if price_ils else None" not in src
    assert "round(b_price_ils, 2) if b_price_ils else None" not in src
    assert src.count("_offer_price_fields(_customer_price_fields(") == 2
    import routes.parts as p
    out = p._offer_price_fields(p._customer_price_fields(20.0, 0.0, supplier_name="AliExpress", supplier_country="CN"))
    assert out["price_ils"] == out["customer_price_ils"] == 29.0 and out["price_ils"] != 20.0
