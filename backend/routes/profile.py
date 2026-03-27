"""Profile — all /api/v1/profile* endpoints extracted from BACKEND_API_ROUTES.py."""

from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Request
from sqlalchemy import select, and_, func, update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from BACKEND_DATABASE_MODELS import get_pii_db, User, UserProfile, Order
from BACKEND_AUTH_SECURITY import (
    get_current_user, update_phone_number, get_redis, check_rate_limit
)
from routes.schemas import UpdatePhoneRequest
from routes.utils import _scan_bytes_for_virus

router = APIRouter()

@router.get("/api/v1/profile")
async def get_profile(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_pii_db)
):
    result = await db.execute(select(UserProfile).where(UserProfile.user_id == current_user.id))
    profile = result.scalar_one_or_none()
    return {
        "user": {
            "id": str(current_user.id),
            "email": current_user.email,
            "phone": current_user.phone,
            "full_name": current_user.full_name,
            "is_verified": current_user.is_verified
        },
        "profile": {
            "address": profile.address_line1 if profile else None,
            "apartment": profile.address_line2 if profile else None,
            "city": profile.city if profile else None,
            "postal_code": profile.postal_code if profile else None,
            "preferred_language": profile.preferred_language if profile else "he",
            "avatar_url": profile.avatar_url if profile else None
        } if profile else None,
    }

@router.put("/api/v1/profile")
async def update_profile(
    address_line1: Optional[str] = None,
    address_line2: Optional[str] = None,
    city: Optional[str] = None,
    postal_code: Optional[str] = None,
    full_name: Optional[str] = None,
    phone: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_pii_db)
):
    result = await db.execute(select(UserProfile).where(UserProfile.user_id == current_user.id))
    profile = result.scalar_one_or_none()
    if not profile:
        profile = UserProfile(user_id=current_user.id)
        db.add(profile)
    if address_line1 is not None:
        profile.address_line1 = address_line1
    if address_line2 is not None:
        profile.address_line2 = address_line2
    if city is not None:
        profile.city = city
    if postal_code is not None:
        profile.postal_code = postal_code
    if full_name is not None:
        current_user.full_name = full_name
    if phone is not None and phone.strip() != (current_user.phone or ''):
        existing = await db.execute(select(User).where(User.phone == phone.strip(), User.id != current_user.id))
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="מספר הטלפון כבר רשום לחשבון אחר")
        await db.execute(sa_update(User).where(User.id == current_user.id).values(phone=phone.strip()))
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    return {"message": "Profile updated"}

@router.post("/api/v1/profile/avatar")
async def upload_avatar(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_pii_db),
    request: Request = None,
    redis=Depends(get_redis)
):
    if redis and request:
        ip = request.client.host if request.client else "unknown"
        allowed = await check_rate_limit(redis, f"rate:upload_avatar:{ip}", 10, 60)
        if not allowed:
            raise HTTPException(status_code=429, detail="יותר מדי בקשות — נסה שוב בעוד דקה")
    content = await file.read()
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Avatar too large (max 5 MB)")
    allowed_mimes = {"image/jpeg", "image/png", "image/webp"}
    mime = (file.content_type or "").split(";")[0].strip().lower()
    if mime not in allowed_mimes:
        raise HTTPException(status_code=415, detail="Unsupported image type")
    scan_status, virus_name = _scan_bytes_for_virus(content)
    if scan_status == "infected":
        raise HTTPException(status_code=400, detail=f"File rejected: malware detected ({virus_name})")
    return {"avatar_url": "https://cdn.autospare.com/avatars/coming-soon.jpg"}

@router.delete("/api/v1/profile/avatar")
async def delete_avatar(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_pii_db)
):
    return {"message": "Avatar deleted"}

@router.post("/api/v1/profile/update-phone")
async def update_phone(
    data: UpdatePhoneRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_pii_db)
):
    await update_phone_number(current_user, data.new_phone, data.verification_code, db)
    return {"message": "Phone number updated"}

@router.get("/api/v1/profile/marketing-preferences")
async def get_marketing_preferences(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_pii_db)
):
    result = await db.execute(select(UserProfile).where(UserProfile.user_id == current_user.id))
    profile = result.scalar_one_or_none()
    return {
        "marketing_consent": profile.marketing_consent if profile else False,
        "newsletter_subscribed": profile.newsletter_subscribed if profile else False,
        "preferences": profile.marketing_preferences if profile else {}
    }

@router.put("/api/v1/profile/marketing-preferences")
async def update_marketing_preferences(
    marketing_consent: Optional[bool] = None,
    newsletter_subscribed: Optional[bool] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_pii_db)
):
    result = await db.execute(select(UserProfile).where(UserProfile.user_id == current_user.id))
    profile = result.scalar_one_or_none()
    if not profile:
        profile = UserProfile(user_id=current_user.id)
        db.add(profile)
    if marketing_consent is not None:
        profile.marketing_consent = marketing_consent
    if newsletter_subscribed is not None:
        profile.newsletter_subscribed = newsletter_subscribed
    await db.commit()
    return {"message": "Preferences updated"}

@router.get("/api/v1/profile/order-history")
async def get_order_history_summary(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_pii_db)
):
    result = await db.execute(
        select(func.count(Order.id).label("total"), func.sum(Order.total_amount).label("spent"))
        .where(Order.user_id == current_user.id)
    )
    stats = result.first()
    return {
        "total_orders": stats.total or 0,
        "total_spent": float(stats.spent or 0)
    }
