from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class PartResult:
    supplier: str
    item_id: str
    title: str
    price: float
    currency: str
    shipping_cost: float
    total_cost: float
    condition: str
    seller: str
    seller_rating: Optional[float]
    item_url: str
    image_url: Optional[str]
    location: str
    estimated_delivery_days: Optional[int]


@dataclass
class OrderResult:
    success: bool
    order_id: Optional[str]
    supplier: str
    total_charged: float
    tracking_number: Optional[str]
    error_message: Optional[str]


class BaseSupplier(ABC):
    name: str = "base"

    @abstractmethod
    async def search(self, query: str, limit: int = 10) -> list[PartResult]:
        """Search for parts by query string."""
        pass

    @abstractmethod
    async def search_by_oem(self, oem_number: str, limit: int = 10) -> list[PartResult]:
        """Search for parts by OEM number."""
        pass

    @abstractmethod
    async def get_part_details(self, item_id: str) -> Optional[PartResult]:
        """Get full details for a specific part."""
        pass

    async def place_order(self, item_id: str, card_details: dict) -> OrderResult:
        """Place an order; each supplier can override when checkout is added."""
        return OrderResult(
            success=False,
            order_id=None,
            supplier=self.name,
            total_charged=0.0,
            tracking_number=None,
            error_message="Order placement not yet implemented for this supplier",
        )
