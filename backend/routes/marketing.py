"""Marketing — all /api/v1/marketing* endpoints extracted from BACKEND_API_ROUTES.py."""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional, List
from pydantic import EmailStr
from BACKEND_DATABASE_MODELS import get_db, get_pii_db, User
from BACKEND_AUTH_SECURITY import get_current_user, get_redis, check_rate_limit
from routes.schemas import NewsletterSubscribeRequest, CouponValidateRequest

router = APIRouter()

@router.post("/api/v1/marketing/subscribe")
async def subscribe_newsletter(
    data: NewsletterSubscribeRequest,
    request: Request,
    db: AsyncSession = Depends(get_pii_db),
    redis=Depends(get_redis)
):
    ip = request.client.host if request.client else "unknown"
    if redis:
        allowed = await check_rate_limit(redis, f'rate:subscribe:{ip}', 3, 60)
        if not allowed:
            raise HTTPException(status_code=429, detail='יותר מדי בקשות — נסה שוב בעוד דקה')
    return {"message": "Subscribed successfully"}

@router.post("/api/v1/marketing/validate-coupon")
async def validate_coupon(
    data: CouponValidateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_pii_db)
):
    return {
        "valid": True,
        "code": data.code,
        "discount_type": "percentage",
        "discount_value": 10
    }

@router.get("/api/v1/marketing/coupons")
async def get_available_coupons(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_pii_db)
):
    return {"coupons": []}

@router.post("/api/v1/marketing/apply-coupon")
async def apply_coupon(
    order_id: str,
    coupon_code: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_pii_db)
):
    return {"discount": 0, "message": "Coupon system coming soon"}

@router.get("/api/v1/marketing/promotions")
async def get_active_promotions(
    db: AsyncSession = Depends(get_db)
):
    return {
        "promotions": [
            {
                "code": "WELCOME10",
                "description": "10% on first order",
                "discount_type": "percentage",
                "value": 10
            }
        ]
    }

@router.post("/api/v1/marketing/referral")
async def create_referral(
    email: EmailStr,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_pii_db)
):
    return {
        "message": "Referral sent",
        "referral_link": f"https://autospare.com?ref={str(current_user.id)[:8]}"
    }

@router.get("/api/v1/marketing/loyalty-points")
async def get_loyalty_points(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_pii_db)
):
    return {
        "points": 0,
        "tier": "bronze",
        "next_tier": "silver",
        "points_needed": 500
    }
