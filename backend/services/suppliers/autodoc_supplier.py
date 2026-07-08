"""
Autodoc supplier — real-time price lookup via autodoc.eu public API.
autodoc.co.il exists (Israeli site). Ships to Israel on request.
API endpoint: https://www.autodoc.eu/api/v1/
"""
import logging
import os
from typing import Optional

import httpx

from services.suppliers.base_supplier import BaseSupplier, PartResult

logger = logging.getLogger(__name__)

AUTODOC_API = "https://www.autodoc.eu/api/v1"
AUTODOC_IL_URL = "https://www.autodoc.co.il"
USD_TO_ILS = float(os.getenv("USD_TO_ILS", "3.72"))
EUR_TO_ILS = float(os.getenv("EUR_TO_ILS", "3.9"))


class AutodocSupplier(BaseSupplier):
    name = "autodoc"

    async def search(self, query: str, limit: int = 10) -> list[PartResult]:
        return await self.search_by_oem(query, limit)

    async def search_by_oem(self, oem_number: str, limit: int = 10) -> list[PartResult]:
        if not oem_number or len(oem_number) < 4:
            return []
        try:
            async with httpx.AsyncClient(timeout=8.0, headers={
                "User-Agent": "Mozilla/5.0",
                "Accept-Language": "en-US,en;q=0.9",
            }) as client:
                r = await client.get(
                    f"{AUTODOC_API}/parts/search",
                    params={"query": oem_number, "lang": "en", "country": "il", "limit": limit},
                )
                if r.status_code != 200:
                    return []
                data = r.json()
        except Exception as exc:
            logger.debug("Autodoc search failed for '%s': %s", oem_number, exc)
            return []

        results = []
        items = data if isinstance(data, list) else data.get("items", data.get("products", []))
        for item in items[:limit]:
            try:
                price_raw = item.get("price") or item.get("priceWithVat") or item.get("salePrice", 0)
                price_eur = float(price_raw) if price_raw else 0.0
                if price_eur <= 0:
                    continue
                price_ils = round(price_eur * EUR_TO_ILS, 2)
                part_number = str(item.get("articleNumber") or item.get("sku") or oem_number)
                brand = str(item.get("brand", {}).get("name") if isinstance(item.get("brand"), dict) else item.get("brand", ""))
                title = str(item.get("name") or item.get("title") or f"{brand} {part_number}").strip()
                item_url = item.get("url") or f"{AUTODOC_IL_URL}/search/{part_number}"
                results.append(PartResult(
                    supplier=self.name,
                    item_id=part_number,
                    title=title,
                    price=price_ils,
                    currency="ILS",
                    shipping_cost=0.0,
                    total_cost=price_ils,
                    condition="new",
                    seller="Autodoc",
                    seller_rating=4.5,
                    item_url=item_url,
                    image_url=item.get("image") or item.get("imageUrl"),
                    location="EU",
                    estimated_delivery_days=7,
                    ships_to_israel=True,
                    warranty_months=12,
                ))
            except Exception:
                continue
        return results

    async def get_part_details(self, item_id: str) -> Optional[PartResult]:
        results = await self.search_by_oem(item_id, limit=1)
        return results[0] if results else None
