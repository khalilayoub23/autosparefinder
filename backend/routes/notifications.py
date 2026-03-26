"""Notifications — all /api/v1/notifications* endpoints extracted from BACKEND_API_ROUTES.py."""

from fastapi import APIRouter, Depends, HTTPException
from sse_starlette.sse import EventSourceResponse
from sqlalchemy import select, and_, func
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List
import asyncio
from datetime import datetime

from BACKEND_DATABASE_MODELS import get_pii_db, Notification, User
from BACKEND_AUTH_SECURITY import get_current_user, get_current_verified_user, get_redis

router = APIRouter()

# seconds
_SSE_HEARTBEAT_INTERVAL = 30  # seconds

@router.get("/api/v1/notifications/stream")
async def notifications_stream(
    current_user: User = Depends(get_current_verified_user),
    redis=Depends(get_redis),
):
    """SSE stream: subscribe to user:{user_id}:notifications Redis Pub/Sub channel."""
    user_id = str(current_user.id)

    async def event_generator():
        if not redis:
            yield {"event": "connected", "data": ""}
            return

        channel = f"user:{user_id}:notifications"
        pubsub = redis.pubsub()
        await pubsub.subscribe(channel)
        try:
            yield {"event": "connected", "data": ""}
            last_heartbeat = asyncio.get_running_loop().time()
            while True:
                now = asyncio.get_running_loop().time()
                if now - last_heartbeat >= _SSE_HEARTBEAT_INTERVAL:
                    yield {"event": "heartbeat", "data": ""}
                    last_heartbeat = now
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=0.1
                )
                if message and message["type"] == "message":
                    yield {"event": "notification", "data": message["data"]}
                else:
                    await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            pass
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.close()

    return EventSourceResponse(event_generator())


@router.get("/api/v1/notifications")
async def get_notifications(
    current_user: User = Depends(get_current_user),
    limit: int = 50,
    db: AsyncSession = Depends(get_pii_db)
):
    result = await db.execute(
        select(Notification)
        .where(Notification.user_id == current_user.id)
        .order_by(Notification.created_at.desc())
        .limit(limit)
    )
    notifs = result.scalars().all()
    return {
        "notifications": [
            {
                "id": str(n.id),
                "type": n.type,
                "title": n.title,
                "message": n.message,
                "read_at": n.read_at,
                "created_at": n.created_at,
            }
            for n in notifs
        ]
    }


@router.get("/api/v1/notifications/unread-count")
async def get_unread_count(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_pii_db)
):
    result = await db.execute(
        select(func.count(Notification.id)).where(
            and_(Notification.user_id == current_user.id, Notification.read_at.is_(None))
        )
    )
    return {"unread_count": result.scalar() or 0}


@router.put("/api/v1/notifications/{notification_id}/read")
async def mark_notification_read(
    notification_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_pii_db),
):
    result = await db.execute(
        select(Notification).where(
            and_(Notification.id == notification_id, Notification.user_id == current_user.id)
        )
    )
    n = result.scalar_one_or_none()
    if not n:
        raise HTTPException(status_code=404, detail="Notification not found")
    n.read_at = datetime.utcnow()
    await db.commit()
    return {"message": "Marked as read"}


@router.put("/api/v1/notifications/read-all")
async def mark_all_read(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_pii_db),
):
    result = await db.execute(
        select(Notification).where(
            and_(Notification.user_id == current_user.id, Notification.read_at.is_(None))
        )
    )
    notifs = result.scalars().all()
    for n in notifs:
        n.read_at = datetime.utcnow()
    await db.commit()
    return {"message": f"Marked {len(notifs)} notifications as read"}


@router.delete("/api/v1/notifications/{notification_id}")
async def delete_notification(
    notification_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_pii_db),
):
    result = await db.execute(
        select(Notification).where(
            and_(Notification.id == notification_id, Notification.user_id == current_user.id)
        )
    )
    n = result.scalar_one_or_none()
    if not n:
        raise HTTPException(status_code=404, detail="Notification not found")
    await db.delete(n)
    await db.commit()
    return {"message": "Notification deleted"}
