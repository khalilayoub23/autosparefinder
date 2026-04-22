import base64
import logging
import os
import time
from typing import Optional

import httpx

from services.suppliers.base_supplier import BaseSupplier, PartResult

logger = logging.getLogger(__name__)


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

                shipping_options = item.get("shippingOptions") or []
                shipping_cost = 0.0
                if shipping_options:
                    first_option = shipping_options[0] or {}
                    if not bool(first_option.get("freeShipping")):
                        ship_obj = first_option.get("shippingCost") or {}
                        shipping_cost = float(ship_obj.get("value") or 0)

                seller_obj = item.get("seller") or {}
                seller_rating = seller_obj.get("feedbackPercentage")
                rating_value = float(seller_rating) if seller_rating is not None else None

                image_url = None
                image_obj = item.get("image") or {}
                if image_obj.get("imageUrl"):
                    image_url = str(image_obj.get("imageUrl"))

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

            shipping_options = item.get("shippingOptions") or []
            shipping_cost = 0.0
            if shipping_options:
                first_option = shipping_options[0] or {}
                if not bool(first_option.get("freeShipping")):
                    ship_obj = first_option.get("shippingCost") or {}
                    shipping_cost = float(ship_obj.get("value") or 0)

            seller_obj = item.get("seller") or {}
            seller_rating = seller_obj.get("feedbackPercentage")
            rating_value = float(seller_rating) if seller_rating is not None else None

            image_url = None
            image_obj = item.get("image") or {}
            if image_obj.get("imageUrl"):
                image_url = str(image_obj.get("imageUrl"))

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
            )
        except Exception as exc:
            logger.error("Failed to map eBay part details for '%s': %s", item_id, exc)
            return None
