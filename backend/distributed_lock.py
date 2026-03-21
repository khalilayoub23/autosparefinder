"""
distributed_lock.py — Redis-backed distributed lock for preventing concurrent
worker runs across multiple processes / containers.

Fails open: if Redis is unavailable the lock is always granted so the worker
can still run (avoids a Redis outage taking down all background jobs).
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

logger = logging.getLogger(__name__)


@asynccontextmanager
async def acquire_lock(
    lock_name: str,
    *,
    ttl: int = 3600,
    namespace: str = "autospare:lock:",
) -> AsyncIterator[bool]:
    """
    Async context manager that acquires a Redis SET NX EX lock.

    Yields:
        True  — lock acquired (proceed normally)
        False — lock already held by another worker (caller should skip)

    Fails open: if Redis is unavailable, always yields True so the worker
    still runs rather than silently doing nothing.

    Usage::

        async with acquire_lock("my_job", ttl=3600) as locked:
            if not locked:
                return {"status": "skipped"}
            # ... do work ...
    """
    from BACKEND_AUTH_SECURITY import get_redis

    redis = await get_redis()
    key = f"{namespace}{lock_name}"
    acquired = False

    if redis is None:
        logger.warning(
            "acquire_lock: Redis unavailable — proceeding without lock (%s)", key
        )
        try:
            yield True
        finally:
            return

    try:
        acquired = bool(await redis.set(key, "1", ex=ttl, nx=True))
        yield acquired
    finally:
        if acquired:
            try:
                await redis.delete(key)
            except Exception as exc:
                logger.warning(
                    "acquire_lock: failed to release %s: %s", key, exc
                )
