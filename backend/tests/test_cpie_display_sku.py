"""Brand-qualified Car-Parts.ie offer keys ('BRAND~article') are internal: purchase orders and customer payloads use the bare article."""
import supplier_ordering
from routes.utils import display_supplier_sku


def test_display_sku_strips_only_the_brand_qualifier():
    assert display_supplier_sku("AIC~59008Set") == "59008Set"
    assert display_supplier_sku("59008Set") == "59008Set"          # bare key unchanged
    assert display_supplier_sku("NGK~0017~X") == "0017~X"           # split once, like cpie_identity.article_of
    assert display_supplier_sku(None) is None and display_supplier_sku("") == ""
    assert display_supplier_sku("Article №: 12345") == "Article №: 12345"   # other legacy shapes are not touched here


def test_purchase_order_uses_the_bare_article():
    row = {"id": "abcdef1234567890", "terms_hash": "h" * 20, "terms": {"shipping": {"cost": 10, "currency": "ILS"}},
           "items": [{"supplier_part_id": "sp1", "quantity": 2, "unit_cost": 100, "currency": "ILS"},
                     {"supplier_part_id": "sp2", "quantity": 1, "unit_cost": 5, "currency": "ILS"}]}
    offers = {"sp1": {"supplier_sku": "AIC~59008Set"}, "sp2": {"supplier_sku": "12345"}}
    body = supplier_ordering.render_purchase_order(row, offers, {"full_name": "x", "street": "y", "city": "z", "country": "IL"})["body"]
    assert "Your SKU 59008Set x 2" in body and "Your SKU 12345 x 1" in body
    assert "~" not in body
