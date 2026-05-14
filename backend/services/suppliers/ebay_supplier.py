import base64
import logging
import os
import re
import time
from typing import Any, Optional

import httpx

from services.suppliers.base_supplier import BaseSupplier, PartResult

logger = logging.getLogger(__name__)


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for val in values:
        item = _safe_text(val)
        if not item or item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _parse_warranty_months(raw_text: Optional[str]) -> Optional[int]:
    text = _safe_text(raw_text).lower()
    if not text:
        return None

    month_match = re.search(r"(\d{1,3})\s*(month|months|mon|mo|חודש|חודשים)", text)
    if month_match:
        return int(month_match.group(1))

    year_match = re.search(r"(\d{1,2})\s*(year|years|yr|yrs|שנה|שנים)", text)
    if year_match:
        return int(year_match.group(1)) * 12

    day_match = re.search(r"(\d{1,4})\s*(day|days|d|יום|ימים)", text)
    if day_match:
        days = int(day_match.group(1))
        return max(1, (days + 29) // 30)

    if "lifetime" in text or "לכל החיים" in text:
        return 120

    return None


def _extract_image_urls(item: dict) -> list[str]:
    urls: list[str] = []

    image_obj = item.get("image") or {}
    if isinstance(image_obj, dict) and image_obj.get("imageUrl"):
        urls.append(_safe_text(image_obj.get("imageUrl")))

    for key in ("additionalImages", "additionalImageUrls", "thumbnailImages"):
        raw_list = item.get(key) or []
        if not isinstance(raw_list, list):
            continue
        for entry in raw_list:
            if isinstance(entry, dict):
                candidate = entry.get("imageUrl") or entry.get("url")
            else:
                candidate = entry
            if candidate:
                urls.append(_safe_text(candidate))

    return _dedupe_preserve_order(urls)


def _extract_specs_and_warranty(item: dict) -> tuple[dict[str, str], Optional[str], Optional[int]]:
    specs: dict[str, str] = {}

    def _set_spec(name: Any, value: Any) -> None:
        key = _safe_text(name)
        if not key:
            return
        if isinstance(value, list):
            val = ", ".join([_safe_text(v) for v in value if _safe_text(v)])
        else:
            val = _safe_text(value)
        if not val:
            return
        specs[key] = val

    localized_aspects = item.get("localizedAspects") or []
    if isinstance(localized_aspects, list):
        for aspect in localized_aspects:
            if not isinstance(aspect, dict):
                continue
            _set_spec(aspect.get("name"), aspect.get("value"))

    aspects = item.get("aspects") or {}
    if isinstance(aspects, dict):
        for key, value in aspects.items():
            _set_spec(key, value)

    item_specifics = item.get("itemSpecifics") or []
    if isinstance(item_specifics, list):
        for spec in item_specifics:
            if not isinstance(spec, dict):
                continue
            _set_spec(spec.get("name"), spec.get("value"))
    elif isinstance(item_specifics, dict):
        for key, value in item_specifics.items():
            _set_spec(key, value)

    return_terms = item.get("returnTerms") or {}
    if isinstance(return_terms, dict):
        if return_terms.get("returnsAccepted") is not None:
            _set_spec("Returns Accepted", return_terms.get("returnsAccepted"))
        period = return_terms.get("returnPeriod") or {}
        if isinstance(period, dict):
            period_value = _safe_text(period.get("value"))
            period_unit = _safe_text(period.get("unit"))
            if period_value:
                _set_spec("Return Period", f"{period_value} {period_unit}".strip())

    warranty_text: Optional[str] = None
    for key, value in specs.items():
        key_lower = key.lower()
        if "warranty" in key_lower or "guarantee" in key_lower or "אחריות" in key_lower:
            warranty_text = _safe_text(value)
            break

    if not warranty_text:
        for direct_key in ("warranty", "manufacturerWarranty", "warrantyInfo"):
            candidate = item.get(direct_key)
            if _safe_text(candidate):
                warranty_text = _safe_text(candidate)
                break

    warranty_months = _parse_warranty_months(warranty_text)
    return specs, warranty_text, warranty_months


def _shipping_from_options_for_israel(item: dict) -> tuple[bool, float]:
    shipping_options = item.get("shippingOptions") or []
    if not isinstance(shipping_options, list):
        return False, 0.0

    for option in shipping_options:
        if not isinstance(option, dict):
            continue

        option_hint = " ".join(
            [
                _safe_text(option.get("optionType")),
                _safe_text(option.get("type")),
                _safe_text(option.get("shippingServiceCode")),
            ]
        ).lower()

        if "pickup" in option_hint:
            continue

        if bool(option.get("freeShipping")):
            return True, 0.0

        ship_obj = option.get("shippingCost") or {}
        try:
            shipping_cost = float(ship_obj.get("value") or 0)
        except Exception:
            shipping_cost = 0.0
        return True, max(shipping_cost, 0.0)

    return False, 0.0


class EbaySupplier(BaseSupplier):
    name = "ebay"

    def __init__(self) -> None:
        self._token: Optional[str] = None
        self._token_expires_at: float = 0.0

        self._app_id = os.getenv("EBAY_APP_ID", "")
        self._cert_id = os.getenv("EBAY_CERT_ID", "")
        self._environment = os.getenv("EBAY_ENVIRONMENT", "PRODUCTION").upper()
        self._base_url = "https://api.sandbox.ebay.com" if self._environment == "SANDBOX" else "https://api.ebay.com"

    async def _get_token(self) -> str:
        now = time.time()
        if self._token and now < self._token_expires_at:
            return self._token

        if not self._app_id or not self._cert_id:
            logger.error("eBay credentials are missing (EBAY_APP_ID / EBAY_CERT_ID)")
            return ""

        auth_raw = f"{self._app_id}:{self._cert_id}".encode("utf-8")
        auth_b64 = base64.b64encode(auth_raw).decode("utf-8")

        token_url = f"{self._base_url}/identity/v1/oauth2/token"
        headers = {
            "Authorization": f"Basic {auth_b64}",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        data = {
            "grant_type": "client_credentials",
            "scope": "https://api.ebay.com/oauth/api_scope",
        }

        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.post(token_url, headers=headers, data=data)
                response.raise_for_status()
                payload = response.json()
        except Exception as exc:
            logger.error("Failed to fetch eBay OAuth token: %s", exc)
            return ""

        token = str(payload.get("access_token") or "")
        expires_in = int(payload.get("expires_in") or 0)
        if not token or expires_in <= 0:
            logger.error("Invalid eBay OAuth token response")
            return ""

        # Keep a small buffer so calls never use an expired token.
        self._token = token
        self._token_expires_at = time.time() + max(0, expires_in - 60)
        return self._token

    async def search(self, query: str, limit: int = 10) -> list[PartResult]:
        token = await self._get_token()
        if not token:
            logger.error("eBay search aborted: missing access token")
            return []

        url = f"{self._base_url}/buy/browse/v1/item_summary/search"
        params = {
            "q": query,
            "category_ids": "6030",
            "limit": str(limit),
            "filter": "conditionIds:{1000}",
        }
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "X-EBAY-C-ENDUSERCTX": "contextualLocation=country=IL",
        }

        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.get(url, headers=headers, params=params)
                response.raise_for_status()
                payload = response.json()
        except Exception as exc:
            logger.error("eBay search failed for query '%s': %s", query, exc)
            return []

        items = payload.get("itemSummaries") or []
        results: list[PartResult] = []

        for item in items:
            try:
                price_obj = item.get("price") or {}
                price = float(price_obj.get("value") or 0)
                currency = str(price_obj.get("currency") or "USD")
                ships_to_israel, shipping_cost = _shipping_from_options_for_israel(item)
                if not ships_to_israel:
                    continue

                seller_obj = item.get("seller") or {}
                seller_rating = seller_obj.get("feedbackPercentage")
                rating_value = float(seller_rating) if seller_rating is not None else None

                image_urls = _extract_image_urls(item)
                image_url = image_urls[0] if image_urls else None
                tech_specs, warranty_text, warranty_months = _extract_specs_and_warranty(item)

                results.append(
                    PartResult(
                        supplier=self.name,
                        item_id=str(item.get("itemId") or ""),
                        title=str(item.get("title") or ""),
                        price=price,
                        currency=currency,
                        shipping_cost=shipping_cost,
                        total_cost=price + shipping_cost,
                        condition=str(item.get("condition") or "unknown"),
                        seller=str(seller_obj.get("username") or ""),
                        seller_rating=rating_value,
                        item_url=str(item.get("itemWebUrl") or ""),
                        image_url=image_url,
                        location=str(item.get("itemLocation") or ""),
                        estimated_delivery_days=None,
                        image_urls=image_urls or None,
                        tech_specs=tech_specs or None,
                        warranty_text=warranty_text,
                        warranty_months=warranty_months,

                        ships_to_israel=ships_to_israel,
                    )
                )
            except Exception as exc:
                logger.error("Failed to map eBay search item: %s", exc)

        logger.info("eBay query '%s' returned %s results", query, len(results))
        return results

    async def search_by_oem(self, oem_number: str, limit: int = 10) -> list[PartResult]:
        return await self.search(oem_number, limit)

    async def get_part_details(self, item_id: str) -> Optional[PartResult]:
        token = await self._get_token()
        if not token:
            logger.error("eBay part details aborted: missing access token")
            return None

        url = f"{self._base_url}/buy/browse/v1/item/{item_id}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "X-EBAY-C-ENDUSERCTX": "contextualLocation=country=IL",
        }

        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.get(url, headers=headers)
                response.raise_for_status()
                item = response.json()
        except Exception as exc:
            logger.error("eBay get_part_details failed for '%s': %s", item_id, exc)
            return None

        try:
            price_obj = item.get("price") or {}
            price = float(price_obj.get("value") or 0)
            currency = str(price_obj.get("currency") or "USD")
            ships_to_israel, shipping_cost = _shipping_from_options_for_israel(item)
            if not ships_to_israel:
                return None

            seller_obj = item.get("seller") or {}
            seller_rating = seller_obj.get("feedbackPercentage")
            rating_value = float(seller_rating) if seller_rating is not None else None

            image_urls = _extract_image_urls(item)
            image_url = image_urls[0] if image_urls else None
            tech_specs, warranty_text, warranty_months = _extract_specs_and_warranty(item)

            return PartResult(
                supplier=self.name,
                item_id=str(item.get("itemId") or item_id),
                title=str(item.get("title") or ""),
                price=price,
                currency=currency,
                shipping_cost=shipping_cost,
                total_cost=price + shipping_cost,
                condition=str(item.get("condition") or "unknown"),
                seller=str(seller_obj.get("username") or ""),
                seller_rating=rating_value,
                item_url=str(item.get("itemWebUrl") or ""),
                image_url=image_url,
                location=str(item.get("itemLocation") or ""),
                estimated_delivery_days=None,
                image_urls=image_urls or None,
                tech_specs=tech_specs or None,
                warranty_text=warranty_text,
                warranty_months=warranty_months,

                ships_to_israel=ships_to_israel,
            )
        except Exception as exc:
            logger.error("Failed to map eBay part details for '%s': %s", item_id, exc)
            return None
