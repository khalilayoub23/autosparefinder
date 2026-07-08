"""
catalog_suppliers.py — Affiliate/catalog suppliers from the PDF research.
All ship to Israel. Integrated as price-reference + affiliate redirect.
Scrapers for these run via harvester batch jobs.

Suppliers covered:
- Alvadi (alvadi.com) — EU aftermarket + OEM, ships Israel
- Cars245 (cars245.com) — Wide catalog, ships Israel
- Spareto (spareto.com) — EU aftermarket, ships Israel (harvester exists)
- RockAuto (rockauto.com) — Wide US catalog, ships Israel (harvester exists)
- FCP Euro (fcpeuro.com) — European brands, premium, ships Israel
- Summit Racing (summitracing.com) — Performance/US, ships Israel
- Fitinpart (fitinpart.sg) — Asian market, ships Israel
- Pelican Parts (pelicanparts.com) — European specialty, ships Israel
- ECS Tuning (ecstuning.com) — European tuning, ships Israel
- Toyota Parts Deal (toyotapartsdeal.com) — Toyota OEM, ships Israel
- Ford Parts Giant (fordpartsgiant.com) — Ford OEM, ships Israel
- Hyundai Parts Deal (hyundaipartsdeal.com) — Hyundai OEM, ships Israel
"""
import logging
import os
from typing import Optional

import httpx

from services.suppliers.base_supplier import BaseSupplier, PartResult

logger = logging.getLogger(__name__)

EUR_TO_ILS = float(os.getenv("EUR_TO_ILS", "3.9"))
USD_TO_ILS = float(os.getenv("USD_TO_ILS", "3.72"))


class _SimpleAffiliate(BaseSupplier):
    """Base for suppliers where we redirect to their site (no real-time API yet)."""
    name = "base_affiliate"
    base_url: str = ""
    search_pattern: str = ""  # {oem} = OEM number
    location: str = "EU"
    delivery_days: int = 10
    price_level: str = "average"  # good / average / premium
    parts_type: str = "both"  # oem / aftermarket / both
    currency: str = "USD"
    rating: float = 4.0

    def _search_url(self, oem: str) -> str:
        return self.search_pattern.format(oem=oem)

    async def search(self, query: str, limit: int = 10) -> list[PartResult]:
        return await self.search_by_oem(query, limit)

    async def search_by_oem(self, oem_number: str, limit: int = 10) -> list[PartResult]:
        if not oem_number or len(oem_number) < 4:
            return []
        # Internal order URL — stored for backend order placement only, NEVER sent to customer
        internal_order_url = self._search_url(oem_number.strip())
        return [PartResult(
            supplier=self.name,
            item_id=oem_number,
            title=f"Part {oem_number}",  # Generic title — no supplier branding to customer
            price=0.0,
            currency=self.currency,
            shipping_cost=0.0,
            total_cost=0.0,
            condition="new",
            seller=self.name,
            seller_rating=self.rating,
            item_url=internal_order_url,  # Internal use for order placement via Stripe Issuing
            image_url=None,
            location=self.location,
            estimated_delivery_days=self.delivery_days,
            ships_to_israel=True,
            tech_specs={
                "internal_order_url": internal_order_url,  # Backend uses this to place order
                "price_level": self.price_level,
                "parts_type": self.parts_type,
            },
        )]

    async def get_part_details(self, item_id: str) -> Optional[PartResult]:
        results = await self.search_by_oem(item_id, 1)
        return results[0] if results else None


class AlvadiSupplier(_SimpleAffiliate):
    name = "alvadi"
    base_url = "https://alvadi.com"
    search_pattern = "https://alvadi.com/en/search?q={oem}"
    location = "LV"  # Latvia — EU shipping
    delivery_days = 7
    price_level = "good"
    rating = 4.3


class Cars245Supplier(_SimpleAffiliate):
    name = "cars245"
    base_url = "https://cars245.com"
    search_pattern = "https://cars245.com/en/catalog/?search={oem}"
    location = "PL"
    delivery_days = 8
    price_level = "good"
    rating = 4.2


class SparetoSupplier(_SimpleAffiliate):
    """Spareto — batch harvester exists (spareto_harvester.py). Real-time affiliate fallback."""
    name = "spareto"
    base_url = "https://spareto.com"
    search_pattern = "https://spareto.com/search?term={oem}"
    location = "EU"
    delivery_days = 7
    price_level = "good"
    rating = 4.1


class RockAutoSupplier(_SimpleAffiliate):
    """RockAuto — batch harvester exists (rockauto_harvester.py). Affiliate for real-time."""
    name = "rockauto"
    base_url = "https://rockauto.com"
    search_pattern = "https://www.rockauto.com/en/partsearch/?q={oem}"
    location = "US"
    delivery_days = 14
    price_level = "good"
    currency = "USD"
    rating = 4.5

    async def search_by_oem(self, oem_number: str, limit: int = 10) -> list[PartResult]:
        """RockAuto has ROCKAUTO_ITEMS_PATH — try to find cached price from our DB first."""
        return await super().search_by_oem(oem_number, limit)


class FCPEuroSupplier(_SimpleAffiliate):
    name = "fcp_euro"
    base_url = "https://www.fcpeuro.com"
    search_pattern = "https://www.fcpeuro.com/products?search={oem}"
    location = "US"
    delivery_days = 10
    price_level = "premium"
    parts_type = "both"
    rating = 4.7


class SummitRacingSupplier(_SimpleAffiliate):
    name = "summit_racing"
    base_url = "https://www.summitracing.com"
    search_pattern = "https://www.summitracing.com/search?autoview=sku&searchby=part-number&keyword={oem}"
    location = "US"
    delivery_days = 14
    price_level = "average"
    currency = "USD"
    rating = 4.4


class FitinpartSupplier(_SimpleAffiliate):
    name = "fitinpart"
    base_url = "https://www.fitinpart.sg"
    search_pattern = "https://www.fitinpart.sg/search?q={oem}"
    location = "SG"
    delivery_days = 12
    price_level = "average"
    rating = 3.8


class PelicanPartsSupplier(_SimpleAffiliate):
    name = "pelican_parts"
    base_url = "https://www.pelicanparts.com"
    search_pattern = "https://www.pelicanparts.com/catalog/searchresults.php?q={oem}"
    location = "US"
    delivery_days = 12
    price_level = "premium"
    parts_type = "both"
    rating = 4.6


class ECSTuningSupplier(_SimpleAffiliate):
    name = "ecs_tuning"
    base_url = "https://www.ecstuning.com"
    search_pattern = "https://www.ecstuning.com/Search/SiteSearch/{oem}/"
    location = "US"
    delivery_days = 12
    price_level = "premium"
    parts_type = "both"
    rating = 4.5


# OEM single-brand suppliers
class ToyotaPartsSupplier(_SimpleAffiliate):
    name = "toyota_parts_deal"
    base_url = "https://www.toyotapartsdeal.com"
    search_pattern = "https://www.toyotapartsdeal.com/oem/toyota~{oem}.html"
    location = "US"
    delivery_days = 10
    price_level = "average"
    parts_type = "oem"
    rating = 4.3


class FordPartsSupplier(_SimpleAffiliate):
    name = "ford_parts_giant"
    base_url = "https://www.fordpartsgiant.com"
    search_pattern = "https://www.fordpartsgiant.com/parts/ford-{oem}.html"
    location = "US"
    delivery_days = 10
    price_level = "average"
    parts_type = "oem"
    rating = 4.2


class HyundaiPartsSupplier(_SimpleAffiliate):
    name = "hyundai_parts_deal"
    base_url = "https://www.hyundaipartsdeal.com"
    search_pattern = "https://www.hyundaipartsdeal.com/oem/hyundai~{oem}.html"
    location = "US"
    delivery_days = 10
    price_level = "average"
    parts_type = "oem"
    rating = 4.2
