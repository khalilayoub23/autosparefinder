"""
Reviews — /api/v1/parts/{part_id}/reviews endpoints extracted from BACKEND_API_ROUTES.py.

Endpoints:
  GET  /api/v1/parts/{part_id}/reviews
  POST /api/v1/parts/{part_id}/reviews
"""
from fastapi import APIRouter, Depends, HTTPException, status
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel, Field, validator
import uuid

from BACKEND_DATABASE_MODELS import get_pii_db, User, Order
from BACKEND_AUTH_SECURITY import get_current_verified_user

router = APIRouter()


class ReviewCreateRequest(BaseModel):
    rating: int
    title:  Optional[str] = Field(None, max_length=255)
    body:   Optional[str] = Field(None, max_length=2000)

    @validator("rating")
    def validate_rating(cls, v):
        if not 1 <= v <= 5:
            raise ValueError("rating must be 1–5")
        return v


@router.get("/api/v1/parts/{part_id}/reviews")
async def get_part_reviews(
    part_id: str,
    db: AsyncSession = Depends(get_pii_db),
):
    from BACKEND_DATABASE_MODELS import PartReview

    try:
        part_uuid = uuid.UUID(part_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid part_id")

    res = await db.execute(
        select(PartReview, User)
        .join(User, PartReview.user_id == User.id)
        .where(PartReview.part_id == part_uuid)
        .order_by(PartReview.created_at.desc())
    )
    rows = res.all()

    reviews = []
    total_rating = 0
    for review, user in rows:
        total_rating += review.rating
        reviews.append({
            "id":                 str(review.id),
            "rating":             review.rating,
            "title":              review.title,
            "body":               review.body,
            "isVerifiedPurchase": review.is_verified_purchase,
            "createdAt":          review.created_at.isoformat(),
            "user": {
                "id":       str(user.id),
                "fullName": user.full_name,
            },
        })

    avg = round(total_rating / len(reviews), 1) if reviews else None
    return {"reviews": reviews, "count": len(reviews), "averageRating": avg}


@router.post("/api/v1/parts/{part_id}/reviews", status_code=status.HTTP_201_CREATED)
async def create_part_review(
    part_id: str,
    body: ReviewCreateRequest,
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_pii_db),
):
    from BACKEND_DATABASE_MODELS import PartReview, OrderItem as _OrderItem

    try:
        part_uuid = uuid.UUID(part_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid part_id")

    existing = await db.execute(
        select(PartReview).where(
            PartReview.user_id == current_user.id,
            PartReview.part_id == part_uuid,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="You have already reviewed this part")

    # Verified purchase: user must have a delivered order containing this part
    verified_res = await db.execute(
        select(Order)
        .join(_OrderItem, _OrderItem.order_id == Order.id)
        .where(
            Order.user_id == current_user.id,
            Order.status == "delivered",
            _OrderItem.part_id == part_uuid,
        )
        .limit(1)
    )
    verified_order = verified_res.scalar_one_or_none()

    review = PartReview(
        user_id=current_user.id,
        part_id=part_uuid,
        order_id=verified_order.id if verified_order else None,
        rating=body.rating,
        title=body.title,
        body=body.body,
        is_verified_purchase=verified_order is not None,
    )
    db.add(review)
    await db.commit()
    await db.refresh(review)

    return {
        "id":                 str(review.id),
        "partId":             str(review.part_id),
        "rating":             review.rating,
        "title":              review.title,
        "body":               review.body,
        "isVerifiedPurchase": review.is_verified_purchase,
        "createdAt":          review.created_at.isoformat(),
    }
