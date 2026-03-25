"""All Pydantic request/response schemas shared across route modules."""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, EmailStr, Field


class SuperAdminSettingCreateBody(BaseModel):
    key: str
    value: Optional[str] = None
    value_type: str = "string"
    description: Optional[str] = None
    is_public: bool = False


class SuperAdminSettingUpdateBody(BaseModel):
    value: Optional[str] = None
    value_type: Optional[str] = None
    description: Optional[str] = None
    is_public: Optional[bool] = None


class SuperAdminUserRoleUpdateBody(BaseModel):
    is_admin: bool
    is_super_admin: bool
    role: Optional[str] = None


class UpdatePhoneRequest(BaseModel):
    new_phone: str = Field(..., max_length=20)
    verification_code: str = Field(..., max_length=10)


class PartsSearchRequest(BaseModel):
    query: str = Field(..., max_length=200)
    vehicle_id: Optional[str] = None
    category: Optional[str] = None
    limit: int = 20


class OrderItemCreate(BaseModel):
    part_id: Optional[str] = None
    supplier_part_id: str
    quantity: int = 1


class OrderCreate(BaseModel):
    items: List[OrderItemCreate]
    shipping_address: Dict[str, str]


class OrderCancelRequest(BaseModel):
    reason: str = Field(..., max_length=500)


class ReturnRequest(BaseModel):
    order_id: str
    reason: str = Field(..., max_length=500)
    description: Optional[str] = Field(None, max_length=1000)


class MultiCheckoutRequest(BaseModel):
    order_ids: List[str]


class NewsletterSubscribeRequest(BaseModel):
    email: EmailStr
    preferences: Optional[List[str]] = ["promotions"]


class CouponValidateRequest(BaseModel):
    code: str = Field(..., max_length=50)


class SupplierCreate(BaseModel):
    name: str
    country: str
    website: Optional[str] = None
    api_endpoint: Optional[str] = None
    api_key: Optional[str] = None
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None
    priority: int = 0
    reliability_score: float = 5.0
    supports_express: bool = False
    express_carrier: Optional[str] = None
    express_base_cost_usd: Optional[float] = None


class SupplierUpdateBody(BaseModel):
    name: Optional[str] = None
    country: Optional[str] = None
    website: Optional[str] = None
    api_endpoint: Optional[str] = None
    api_key: Optional[str] = None
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None
    priority: Optional[int] = None
    reliability_score: Optional[float] = None
    is_active: Optional[bool] = None
    supports_express: Optional[bool] = None
    express_carrier: Optional[str] = None
    express_base_cost_usd: Optional[float] = None


class CreateSocialPostRequest(BaseModel):
    content: str = Field(..., max_length=5000)
    platforms: List[str]
    schedule_time: Optional[datetime] = None


class UpdateSocialPostRequest(BaseModel):
    content: Optional[str] = Field(None, max_length=5000)
    platforms: Optional[List[str]] = None
    schedule_time: Optional[datetime] = None


class UserUpdateBody(BaseModel):
    full_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    role: Optional[str] = None
    is_verified: Optional[bool] = None
    is_active: Optional[bool] = None
    is_admin: Optional[bool] = None


class UserCreateBody(BaseModel):
    full_name: str
    email: str
    phone: str
    password: str
    role: str = "customer"
    is_admin: bool = False
    is_verified: bool = True


class ResolveApprovalBody(BaseModel):
    decision: Literal["approved", "rejected"]
    note: Optional[str] = None


class CartAddRequest(BaseModel):
    part_id: str
    quantity: int = 1


class WishlistAddRequest(BaseModel):
    part_id: str
