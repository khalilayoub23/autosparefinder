"""AliExpressSupplier.get_part_details() regression suite (no network).

Root-fix verification (2026-09-29): the previous parser read `aeop_ae_*` / `image_u_r_ls` /
top-level `store_id`/`product_id` paths that never matched any real `aliexpress.ds.product.get`
response, so `get_part_details()` returned `None` on every call since AliExpress went live.

`REAL_PRODUCT_GET_RESPONSE` below is the EXACT `response` body of a real, successful, production
`aliexpress.ds.product.get` call — captured 2026-09-29T03:04:08Z via a one-shot, owner-authorized
diagnostic instrumentation during the normal scheduled AliExpress sync (see FIXES_TRACKER.md
2026-09-29), then removed. It contains no credentials, tokens, or secrets — it is the response
body only, never the signed request. Product: a real Mazda air filter listing, OEM SH01-13-3A0A,
product_id 1005010669652047, rsp_code 200 "Call succeeds".
"""
import copy

import httpx
import pytest
from unittest.mock import AsyncMock

from services.suppliers.aliexpress_supplier import AliExpressSupplier

REAL_PRODUCT_GET_RESPONSE = {
    "aliexpress_ds_product_get_response": {
        "result": {
            "ae_item_sku_info_dtos": {
                "ae_item_sku_info_d_t_o": [
                    {
                        "sku_attr": "", "offer_sale_price": "14.24", "sku_id": "12000053138885208",
                        "price_include_tax": False, "currency_code": "USD", "sku_price": "29.05",
                        "offer_bulk_sale_price": "14.24", "sku_available_stock": 9, "id": "",
                    }
                ]
            },
            "ae_multimedia_info_dto": {
                "image_urls": (
                    "https://ae01.alicdn.com/kf/Se50234bb0ee045b6a20855992df4e72bw.jpg;"
                    "https://ae01.alicdn.com/kf/S726597e2e4a14a74abdc744a890a6c97K.jpg;"
                    "https://ae01.alicdn.com/kf/S31f29ec5eefb4ca6a9162d9e91c67aabA.jpg;"
                    "https://ae01.alicdn.com/kf/Sba23504b2ab64d3aa122e5b9ad73352dr.jpg;"
                    "https://ae01.alicdn.com/kf/S08eebefc899848b2b0e06bbc0a4a25f7V.jpg;"
                    "https://ae01.alicdn.com/kf/Sce65eafa896f4687aa6e18915ed42a53u.jpg"
                )
            },
            "package_info_dto": {
                "package_width": 22, "package_height": 7, "package_length": 26,
                "gross_weight": "0.469", "package_type": False, "product_unit": 100000015,
            },
            "logistics_info_dto": {"delivery_time": 7, "ship_to_country": "IL"},
            "product_id_converter_result": {
                "main_product_id": 1005010669652047, "sub_product_id": '{"US":3256810483337295}',
            },
            "ae_item_base_info_dto": {
                "subject": "OEM: SH01-13-3A0A Car Engine Air Filter for Mazda 3 6 CX5 2012 2013 2014 2015 2016",
                "evaluation_count": "10", "sales_count": "35", "product_status_type": "onSelling",
                "avg_evaluation_rating": "4.9", "separated_listing": False, "currency_code": "CNY",
                "sl_product": False, "category_id": 200004063, "product_id": 1005010669652047,
                "detail": "<p>Air Filter</p><p>SH01-13-3A0A</p>",
                "mobile_detail": '{"version":"2.0.0","moduleList":[]}',
            },
            "has_whole_sale": False,
            "ae_item_properties": {
                "ae_item_property": [
                    {"attr_name_id": 2, "attr_value_id": 8693958699, "attr_name": "Brand Name", "attr_value": "NoEnName_Null"},
                    {"attr_name_id": 400000603, "attr_value_id": 23399591357, "attr_name": "High-concerned chemical", "attr_value": "none"},
                    {"attr_name_id": 400038608, "attr_value_id": 24384447834, "attr_name": "Automotive fit type", "attr_value": "Vehicle specific fit"},
                    {"attr_name_id": 219, "attr_value_id": 9441741844, "attr_name": "Origin", "attr_value": "Mainland China"},
                    {"attr_name_id": 200000195, "attr_value_id": -1, "attr_name": "Item Weight", "attr_value": "0.47"},
                    {"attr_name_id": 200009126, "attr_value_id": -1, "attr_name": "Manufacturer Part Number", "attr_value": "SH01-13-3A0A"},
                    {"attr_name_id": 252174973, "attr_value_id": -1, "attr_name": "OEM NO.", "attr_value": "SH01-13-3A0A"},
                    {"attr_name_id": 200009128, "attr_value_id": -1, "attr_name": "Other Part Number", "attr_value": "SH01-13-3A0A"},
                    {"attr_name_id": 200000192, "attr_value_id": -1, "attr_name": "Item Length", "attr_value": "26"},
                    {"attr_name_id": 200000193, "attr_value_id": -1, "attr_name": "Item Width", "attr_value": "23"},
                    {"attr_name_id": 200000194, "attr_value_id": -1, "attr_name": "Item Height", "attr_value": "6"},
                    {"attr_name_id": 200009125, "attr_value_id": -1, "attr_name": "Interchange Part Number", "attr_value": "SH01-13-3A0A"},
                    {"attr_name_id": -1, "attr_name": "Choice", "attr_value": "yes"},
                ]
            },
            "ae_store_info": {
                "store_id": 1105230412, "shipping_speed_rating": "4.8", "communication_rating": "4.7",
                "store_name": "Shop1105230412 Store", "store_country_code": "CN", "item_as_described_rating": "4.7",
            },
        },
        "rsp_code": 200,
        "rsp_msg": "Call succeeds",
        "request_id": "0bb4a9a517906510480632316",
        "_trace_id_": "2151fce817906510480625018e0dcd",
    }
}


@pytest.fixture
def sup(monkeypatch):
    for k, v in (("ALIEXPRESS_APP_KEY", "546482"), ("ALIEXPRESS_APP_SECRET", "S" * 32), ("ENCRYPTION_KEY", "k" * 64)):
        monkeypatch.setenv(k, v)
    s = AliExpressSupplier()
    monkeypatch.setattr(s, "_ensure_token", AsyncMock(return_value=True))
    return s


class _R:
    status_code = 200
    def __init__(self, d): self._d = d
    def json(self): return self._d
    def raise_for_status(self): pass


def _post(monkeypatch, payload):
    async def post(self, url, data=None, **kw):
        return _R(payload)
    monkeypatch.setattr(httpx.AsyncClient, "post", post)


# ---- Test A: real response parses to a valid PartResult, not None -----------------------------
async def test_real_response_produces_a_valid_part_result(sup, monkeypatch):
    _post(monkeypatch, REAL_PRODUCT_GET_RESPONSE)
    out = await sup.get_part_details("1005010669652047")
    assert out is not None       # root cause: this was ALWAYS None before the fix


# ---- Test B: price -----------------------------------------------------------------------------
async def test_real_response_price_matches_offer_sale_price(sup, monkeypatch):
    _post(monkeypatch, REAL_PRODUCT_GET_RESPONSE)
    out = await sup.get_part_details("1005010669652047")
    assert out.price == 14.24 and out.currency == "USD" and out.total_cost == 14.24
    assert out.shipping_cost == 0.0                    # shipping cost stays NOT invented (unchanged policy)


# ---- Test C: images ------------------------------------------------------------------------------
async def test_real_response_extracts_all_six_gallery_images(sup, monkeypatch):
    _post(monkeypatch, REAL_PRODUCT_GET_RESPONSE)
    out = await sup.get_part_details("1005010669652047")
    assert out.image_url == "https://ae01.alicdn.com/kf/Se50234bb0ee045b6a20855992df4e72bw.jpg"
    assert out.image_urls is not None and len(out.image_urls) == 6
    assert out.image_urls[-1] == "https://ae01.alicdn.com/kf/Sce65eafa896f4687aa6e18915ed42a53u.jpg"


# ---- Test D: delivery / destination -------------------------------------------------------------
async def test_real_response_maps_delivery_time_and_destination(sup, monkeypatch):
    _post(monkeypatch, REAL_PRODUCT_GET_RESPONSE)
    out = await sup.get_part_details("1005010669652047")
    assert out.estimated_delivery_days == 7             # logistics_info_dto.delivery_time (was hardcoded 20)
    assert out.ships_to_israel is True                  # logistics_info_dto.ship_to_country == "IL"


async def test_real_response_identity_and_tech_specs(sup, monkeypatch):
    _post(monkeypatch, REAL_PRODUCT_GET_RESPONSE)
    out = await sup.get_part_details("1005010669652047")
    assert out.item_id == "1005010669652047"
    assert "Mazda" in out.title and "SH01-13-3A0A" in out.title
    assert out.seller == "1105230412"                   # ae_store_info.store_id
    assert out.tech_specs["manufacturer_part_number"] == "SH01-13-3A0A"
    assert out.tech_specs["oem_no"] == "SH01-13-3A0A"
    assert out.tech_specs["interchange_part_number"] == "SH01-13-3A0A"
    assert out.tech_specs["item_weight_kg"] == "0.47"
    assert out.tech_specs["item_length_cm"] == "26" and out.tech_specs["item_width_cm"] == "23" and out.tech_specs["item_height_cm"] == "6"
    assert out.tech_specs["origin_country"] == "Mainland China"
    assert out.tech_specs["automotive_fit_type"] == "Vehicle specific fit"
    assert "part_origin" in out.tech_specs             # existing self-derived classification preserved
    # deliberately deferred (no destination field / would require new DB schema): not present
    assert "package_width" not in out.tech_specs and "detail" not in out.tech_specs


# ---- Test E: missing optional fields must not crash the parser ----------------------------------
async def test_missing_optional_structures_does_not_crash(sup, monkeypatch):
    minimal = copy.deepcopy(REAL_PRODUCT_GET_RESPONSE)
    result = minimal["aliexpress_ds_product_get_response"]["result"]
    del result["logistics_info_dto"]
    del result["ae_item_properties"]
    del result["ae_store_info"]
    del result["package_info_dto"]
    _post(monkeypatch, minimal)
    out = await sup.get_part_details("1005010669652047")
    assert out is not None
    assert out.estimated_delivery_days == 20            # existing fallback preserved when logistics is absent
    assert out.ships_to_israel is True                  # existing fallback preserved
    assert out.seller == ""                              # existing fallback preserved
    assert out.tech_specs == {"part_origin": out.tech_specs["part_origin"]}   # only the self-derived key survives


async def test_no_usable_price_returns_none(sup, monkeypatch):
    empty_price = copy.deepcopy(REAL_PRODUCT_GET_RESPONSE)
    empty_price["aliexpress_ds_product_get_response"]["result"]["ae_item_sku_info_dtos"] = {"ae_item_sku_info_d_t_o": []}
    empty_price["aliexpress_ds_product_get_response"]["result"]["ae_item_base_info_dto"].pop("sale_price", None)
    _post(monkeypatch, empty_price)
    assert await sup.get_part_details("1005010669652047") is None


# ---- Test F: existing error_response behavior is unchanged ---------------------------------------
async def test_error_response_still_returns_none(sup, monkeypatch):
    _post(monkeypatch, {"error_response": {"code": "IllegalAccessToken", "msg": "x"}})
    assert await sup.get_part_details("1005010669652047") is None


async def test_empty_result_still_returns_none(sup, monkeypatch):
    _post(monkeypatch, {"aliexpress_ds_product_get_response": {"result": {}}})
    assert await sup.get_part_details("1005010669652047") is None
