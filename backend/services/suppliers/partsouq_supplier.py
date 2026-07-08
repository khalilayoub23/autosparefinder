"""
PartSouq — OEM parts, ships to Israel, Middle East focused.
Good for: genuine OEM parts for Asian/European cars.
"""
import logging
from typing import Optional
import httpx
from services.suppliers.base_supplier import BaseSupplier, PartResult

logger = logging.getLogger(__name__)
BASE = "https://partsouq.com"


class PartSouqSupplier(BaseSupplier):
    name = "partsouq"

    async def search(self, query: str, limit: int = 10) -> list[PartResult]:
        return await self.search_by_oem(query, limit)

    async def search_by_oem(self, oem_number: str, limit: int = 10) -> list[PartResult]:
        if not oem_number or len(oem_number) < 4:
            return []
        try:
            async with httpx.AsyncClient(timeout=8.0, headers={"User-Agent": "Mozilla/5.0"}) as c:
                r = await c.get(f"{BASE}/en/search", params={"q": oem_number, "lang": "en"})
                if r.status_code != 200:
                    return []
                # PartSouq returns HTML — create affiliate link result
                search_url = str(r.url)
        except Exception as exc:
            logger.debug("PartSouq search error: %s", exc)
            search_url = f"{BASE}/en/search?q={oem_number}"

        # Return as price-reference result (customer clicks through to PartSouq)
        return [PartResult(
            supplier=self.name,
            item_id=oem_number,
            title=f"OEM Part {oem_number}",
            price=0.0,
            currency="USD",
            shipping_cost=0.0,
            total_cost=0.0,
            condition="new",
            seller="PartSouq",
            seller_rating=4.2,
            item_url=search_url,
            image_url=None,
            location="AE",
            estimated_delivery_days=10,
            ships_to_israel=True,
            tech_specs={"type": "OEM", "affiliate": True},
        )]

    async def get_part_details(self, item_id: str) -> Optional[PartResult]:
        results = await self.search_by_oem(item_id, 1)
        return results[0] if results else None
