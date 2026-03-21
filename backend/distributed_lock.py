"""
distributed_lock.py — Redis-backed distributed lock for preventing concurrent
worker runs across multiple processes / containers.

Fails open: if Redis is unavailable the lock is always granted so the worker
can still run (avoids a Redis outage taking down all background jobs).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class DistributedLock:
    redis: Any
    key: str
    acquired: bool

    def __bool__(self) -> bool:
        return self.acquired

    async def release(self) -> None:
        if not self.acquired or self.redis is None:
            return
        try:
            await self.redis.delete(self.key)
        except Exception as exc:
            logger.warning("acquire_lock: failed to release %s: %s", self.key, exc)

    async def __aenter__(self) -> bool:
        return self.acquired

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.release()


async def acquire_lock(
    redis: Any,
    lock_name: str,
    *,
    ttl_seconds: int = 3600,
    namespace: str = "autospare:lock:",
) -> DistributedLock:
    """
    Acquire a Redis SET NX EX lock and return a lock handle.

    The returned lock handle is truthy only when acquired.
    Fails open: if Redis is unavailable, returns acquired=True so workers
    still run rather than silently doing nothing.

    Usage::

        lock = await acquire_lock(redis, "my_job", ttl_seconds=3600)
        if not lock:
            return {"status": "skipped"}
        try:
            # ... do work ...
        finally:
            await lock.release()
    """
    key = f"{namespace}{lock_name}"
    acquired = False

    if redis is None:
        logger.warning(
            "acquire_lock: Redis unavailable — proceeding without lock (%s)", key
        )
        return DistributedLock(redis=None, key=key, acquired=True)

    acquired = bool(await redis.set(key, "1", ex=ttl_seconds, nx=True))
    return DistributedLock(redis=redis, key=key, acquired=acquired)
