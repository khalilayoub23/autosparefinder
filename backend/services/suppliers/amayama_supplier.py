"""
Amayama — OEM parts from Japanese/Korean manufacturers, ships to Israel.
Specializes in genuine Toyota, Honda, Nissan, Hyundai, Kia OEM parts.
"""
import logging
from typing import Optional
import httpx
from services.suppliers.base_supplier import BaseSupplier, PartResult

logger = logging.getLogger(__name__)
BASE = "https://amayama.com"


class AmayamaSupplier(BaseSupplier):
    name = "amayama"

    async def search(self, query: str, limit: int = 10) -> list[PartResult]:
        return await self.search_by_oem(query, limit)

    async def search_by_oem(self, oem_number: str, limit: int = 10) -> list[PartResult]:
        if not oem_number or len(oem_number) < 5:
            return []
        clean = oem_number.replace("-", "").replace(" ", "").upper()
        search_url = f"{BASE}/en/parts/{clean}"
        try:
            async with httpx.AsyncClient(timeout=8.0, headers={"User-Agent": "Mozilla/5.0"}) as c:
                r = await c.get(f"{BASE}/en/autocomplete", params={"term": clean})
                if r.status_code == 200:
                    data = r.json()
                    if data and isinstance(data, list):
                        item = data[0]
                        price_jpy = float(item.get("price", 0) or 0)
                        price_usd = round(price_jpy / 145.0, 2) if price_jpy else 0.0
                        price_ils = round(price_usd * 3.72, 2) if price_usd else 0.0
                        return [PartResult(
                            supplier=self.name,
                            item_id=clean,
                            title=str(item.get("label") or f"OEM {clean}"),
                            price=price_ils if price_ils > 0 else 0.0,
                            currency="ILS",
                            shipping_cost=0.0,
                            total_cost=price_ils if price_ils > 0 else 0.0,
                            condition="new",
                            seller="Amayama",
                            seller_rating=4.6,
                            item_url=item.get("url") or search_url,
                            image_url=None,
                            location="JP",
                            estimated_delivery_days=14,
                            ships_to_israel=True,
                            tech_specs={"type": "OEM", "origin": "Japan"},
                            warranty_months=12,
                        )]
        except Exception as exc:
            logger.debug("Amayama search error: %s", exc)

        return [PartResult(
            supplier=self.name,
            item_id=clean,
            title=f"OEM Part {clean}",
            price=0.0,
            currency="ILS",
            shipping_cost=0.0,
            total_cost=0.0,
            condition="new",
            seller="Amayama",
            seller_rating=4.6,
            item_url=search_url,
            image_url=None,
            location="JP",
            estimated_delivery_days=14,
            ships_to_israel=True,
            tech_specs={"type": "OEM", "affiliate": True},
        )]

    async def get_part_details(self, item_id: str) -> Optional[PartResult]:
        results = await self.search_by_oem(item_id, 1)
        return results[0] if results else None
