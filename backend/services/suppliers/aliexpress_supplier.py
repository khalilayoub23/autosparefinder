import hashlib
import hmac
import logging
import os
import time
from typing import Any, Optional

import httpx

from services.suppliers.base_supplier import BaseSupplier, PartResult

logger = logging.getLogger(__name__)

ALIEXPRESS_API_URL = "https://api-sg.aliexpress.com/sync"

OE_BRANDS = {
    "bosch", "denso", "valeo", "ngk", "gates", "skf", "fag",
    "luk", "sachs", "monroe", "brembo", "ate", "hella", "mahle",
    "mann", "febi", "meyle", "trw", "delphi", "continental",
    "kayaba", "gabriel", "moog", "corteco", "elring", "victor reinz",
}

OEM_KEYWORDS = {"genuine", "original", "oem", "factory", "מקורי", "מקור"}


def classify_part_origin(title: str) -> str:
    t = _safe_text(title).lower()
    if any(k in t for k in OEM_KEYWORDS):
        return "original"
    if any(b in t for b in OE_BRANDS):
        return "oe_equivalent"
    return "aftermarket"



def _sign(params: dict[str, Any], app_secret: str) -> str:
    """IOP signature: sorted key+value concatenation, HMAC-SHA256."""
    sorted_str = "".join(f"{k}{v}" for k, v in sorted(params.items()))
    return hmac.new(
        app_secret.encode("utf-8"),
        sorted_str.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest().upper()


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


class AliExpressSupplier(BaseSupplier):
    name = "aliexpress"

    def __init__(self) -> None:
        self._app_key = os.getenv("ALIEXPRESS_APP_KEY", "")
        self._app_secret = os.getenv("ALIEXPRESS_APP_SECRET", "")
        self._access_token = os.getenv("ALIEXPRESS_ACCESS_TOKEN", "")

    def _credentials_ok(self) -> bool:
        return bool(self._app_key and self._app_secret and self._access_token)

    def _build_request(self, method: str, extra: dict[str, Any]) -> dict[str, Any]:
        params: dict[str, Any] = {
            "method": method,
            "app_key": self._app_key,
            "access_token": self._access_token,
            "timestamp": str(int(time.time() * 1000)),
            "sign_method": "sha256",
            "format": "json",
            "v": "2.0",
            **extra,
        }
        params["sign"] = _sign(params, self._app_secret)
        return params

    @staticmethod
    def _parse_rating(raw: Any) -> Optional[float]:
        txt = _safe_text(raw)
        if not txt:
            return None
        try:
            return float(txt.replace("%", ""))
        except Exception:
            return None

    async def search(self, query: str, limit: int = 10) -> list[PartResult]:
        if not self._credentials_ok():
            logger.error("AliExpress credentials missing")
            return []

        params = self._build_request(
            "aliexpress.affiliate.product.query",
            {
                "keywords": query,
                "category_ids": "44",  # Auto Parts
                "page_no": "1",
                "page_size": str(min(limit, 50)),
                "ship_to_country": "IL",
                "sort": "SALE_PRICE_ASC",
                "fields": ",".join(
                    [
                        "product_id",
                        "product_title",
                        "sale_price",
                        "original_price",
                        "product_main_image_url",
                        "product_detail_url",
                        "shop_id",
                        "evaluate_rate",
                        "lastest_volume",
                        "product_video_url",
                    ]
                ),
            },
        )

        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(ALIEXPRESS_API_URL, data=params)
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            logger.error("AliExpress search failed for '%s': %s", query, exc)
            return []

        body = (
            data.get("aliexpress_affiliate_product_query_response", {})
            .get("resp_result", {})
        )
        if body.get("resp_code") != 200:
            logger.warning("AliExpress API error: %s", body.get("resp_msg"))
            return []

        items = body.get("result", {}).get("products", {}).get("product", []) or []
        results: list[PartResult] = []

        for item in items:
            try:
                price = float(item.get("sale_price") or item.get("original_price") or 0)
                if price <= 0:
                    continue

                main_image = _safe_text(item.get("product_main_image_url"))
                title = str(item.get("product_title") or "")
                origin = classify_part_origin(title)
                results.append(
                    PartResult(
                        supplier=self.name,
                        item_id=str(item.get("product_id") or ""),
                        title=title,
                        price=price,
                        currency="USD",
                        shipping_cost=0.0,
                        total_cost=price,
                        condition="New",
                        seller=str(item.get("shop_id") or ""),
                        seller_rating=self._parse_rating(item.get("evaluate_rate")),
                        item_url=str(item.get("product_detail_url") or ""),
                        image_url=main_image or None,
                        location="CN",
                        estimated_delivery_days=20,
                        ships_to_israel=True,
                        image_urls=[main_image] if main_image else None,
                        tech_specs={"part_origin": origin},
                        warranty_text=None,
                        warranty_months=None,
                    )
                )
            except Exception as exc:
                logger.error("AliExpress map item error: %s", exc)

        logger.info("AliExpress query '%s' returned %d results", query, len(results))
        return results

    async def search_by_oem(self, oem_number: str, limit: int = 10) -> list[PartResult]:
        return await self.search(oem_number, limit)

    async def get_part_details(self, item_id: str) -> Optional[PartResult]:
        if not self._credentials_ok():
            logger.error("AliExpress credentials missing")
            return None

        params = self._build_request(
            "aliexpress.affiliate.productdetail.get",
            {
                "product_ids": item_id,
                "ship_to_country": "IL",
                "fields": ",".join(
                    [
                        "product_id",
                        "product_title",
                        "sale_price",
                        "original_price",
                        "product_main_image_url",
                        "product_detail_url",
                        "shop_id",
                        "evaluate_rate",
                        "image_urls",
                        "product_description",
                    ]
                ),
            },
        )

        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(ALIEXPRESS_API_URL, data=params)
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            logger.error("AliExpress get_part_details failed for '%s': %s", item_id, exc)
            return None

        body = (
            data.get("aliexpress_affiliate_productdetail_get_response", {})
            .get("resp_result", {})
        )
        if body.get("resp_code") != 200:
            return None

        products = body.get("result", {}).get("products", {}).get("product", []) or []
        if not products:
            return None

        item = products[0]
        try:
            price = float(item.get("sale_price") or item.get("original_price") or 0)
            if price <= 0:
                return None

            image_urls: list[str] = []
            main_image = _safe_text(item.get("product_main_image_url"))
            if main_image:
                image_urls.append(main_image)

            extra_imgs = item.get("image_urls", {}).get("string", []) or []
            image_urls.extend([str(u) for u in extra_imgs if _safe_text(u)])

            # Deduplicate while preserving order.
            image_urls = list(dict.fromkeys(image_urls))

            title = str(item.get("product_title") or "")
            origin = classify_part_origin(title)
            return PartResult(
                supplier=self.name,
                item_id=str(item.get("product_id") or item_id),
                title=title,
                price=price,
                currency="USD",
                shipping_cost=0.0,
                total_cost=price,
                condition="New",
                seller=str(item.get("shop_id") or ""),
                seller_rating=self._parse_rating(item.get("evaluate_rate")),
                item_url=str(item.get("product_detail_url") or ""),
                image_url=image_urls[0] if image_urls else None,
                location="CN",
                estimated_delivery_days=20,
                ships_to_israel=True,
                image_urls=image_urls or None,
                tech_specs={"part_origin": origin},
                warranty_text=None,
                warranty_months=None,
            )
        except Exception as exc:
            logger.error("AliExpress map details error: %s", exc)
            return None
