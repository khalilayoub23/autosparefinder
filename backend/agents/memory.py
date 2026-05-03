"""
agents/memory.py — Shared memory store for all AutoSpareFinder agents.

Three layers:
  1. PostgreSQL  — persistent long-term memory (brand guide, decisions, metrics)
  2. Redis       — short-term tactical memory (last 24h events, trends)
  3. In-process  — ephemeral cache (current session only)

Usage:
    from agents.memory import AgentMemory
    mem = AgentMemory(db, agent_name="marketing_agent")
    await mem.set("last_post_performance", {"likes": 120, "views": 3400})
    data = await mem.get("last_post_performance")
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("agents.memory")

# ── In-process ephemeral cache ────────────────────────────────────────────────
_local_cache: dict[str, Any] = {}


class AgentMemory:
    def __init__(self, db: AsyncSession, agent_name: str):
        self.db = db
        self.agent_name = agent_name

    # ── PostgreSQL — persistent ───────────────────────────────────────────────

    async def set(
        self,
        key: str,
        value: Any,
        ttl_hours: Optional[int] = None,
    ) -> None:
        """Store a value in shared persistent memory."""
        expires_at = (
            datetime.utcnow() + timedelta(hours=ttl_hours)
            if ttl_hours
            else None
        )
        payload = json.dumps(value, ensure_ascii=False)
        await self.db.execute(
            text("""
                INSERT INTO agent_memory (key, value, updated_by, expires_at, updated_at)
                VALUES (:key, CAST(:value AS jsonb), :agent, :expires_at, NOW())
                ON CONFLICT (key) DO UPDATE
                SET value      = EXCLUDED.value,
                    updated_by = EXCLUDED.updated_by,
                    expires_at = EXCLUDED.expires_at,
                    updated_at = NOW()
            """),
            {
                "key": key,
                "value": payload,
                "agent": self.agent_name,
                "expires_at": expires_at,
            },
        )
        await self.db.commit()
        _local_cache[key] = value
        logger.debug("memory.set [%s] key=%s agent=%s", "pg", key, self.agent_name)

    async def get(self, key: str) -> Optional[Any]:
        """Retrieve a value. Returns None if not found or expired."""
        if key in _local_cache:
            return _local_cache[key]
        result = await self.db.execute(
            text("""
                SELECT value FROM agent_memory
                WHERE key = :key
                  AND (expires_at IS NULL OR expires_at > NOW())
            """),
            {"key": key},
        )
        row = result.fetchone()
        if row is None:
            return None
        value = row[0] if isinstance(row[0], (dict, list)) else json.loads(row[0])
        _local_cache[key] = value
        return value

    async def delete(self, key: str) -> None:
        await self.db.execute(
            text("DELETE FROM agent_memory WHERE key = :key"),
            {"key": key},
        )
        await self.db.commit()
        _local_cache.pop(key, None)

    # ── Redis — short-term ────────────────────────────────────────────────────

    async def set_redis(self, key: str, value: Any, ttl_seconds: int = 86400) -> None:
        """Store in Redis for fast short-term access (default 24h)."""
        try:
            from BACKEND_AUTH_SECURITY import get_redis
            r = await get_redis()
            await r.set(
                f"agent_mem:{key}",
                json.dumps(value, ensure_ascii=False),
                ex=ttl_seconds,
            )
        except Exception as exc:
            logger.warning("memory.set_redis failed: %s", exc)

    async def get_redis(self, key: str) -> Optional[Any]:
        try:
            from BACKEND_AUTH_SECURITY import get_redis
            r = await get_redis()
            val = await r.get(f"agent_mem:{key}")
            if val is None:
                return None
            return json.loads(val.decode() if isinstance(val, bytes) else val)
        except Exception as exc:
            logger.warning("memory.get_redis failed: %s", exc)
            return None

    # ── Convenience helpers ───────────────────────────────────────────────────

    async def append_event(self, key: str, event: dict, max_events: int = 50) -> None:
        """Append an event to a list stored in memory (e.g. post history)."""
        existing: list = await self.get(key) or []
        existing.append({**event, "ts": datetime.utcnow().isoformat()})
        if len(existing) > max_events:
            existing = existing[-max_events:]
        await self.set(key, existing)

    async def get_brand_guide(self) -> dict:
        """Return AutoSpareFinder brand guide — shared across all agents."""
        guide = await self.get("brand_guide")
        if guide:
            return guide
        # Default brand guide
        default = {
            "name": "AutoSpareFinder",
            "tagline": "חלקי חילוף לכל רכב — מהיר, אמין, זול",
            "tone": "מקצועי אבל נגיש, עברית ברורה",
            "colors": {"primary": "#0099e6", "dark": "#0d1117"},
            "target_audience": "בעלי רכב בישראל, 25-55",
            "usp": [
                "302,000 חלקים במלאי",
                "משלוח מהיר לכל הארץ",
                "מחירים תחרותיים",
                "תמיכה בעברית",
            ],
            "hashtags": [
                "#חלקירכב", "#autosparefinder", "#גראז׳",
                "#רכב", "#תיקוןרכב", "#חלקיחילוף",
            ],
        }
        await self.set("brand_guide", default)
        return default


# ── DB migration helper ───────────────────────────────────────────────────────

async def ensure_memory_table(db: AsyncSession) -> None:
    """Create agent_memory table if it doesn't exist."""
    await db.execute(text("""
        CREATE TABLE IF NOT EXISTS agent_memory (
            key         VARCHAR(255) PRIMARY KEY,
            value       JSONB        NOT NULL,
            updated_by  VARCHAR(100),
            expires_at  TIMESTAMP,
            updated_at  TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """))
    await db.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_agent_memory_updated_by "
        "ON agent_memory (updated_by)"
    ))
    await db.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_agent_memory_expires_at "
        "ON agent_memory (expires_at)"
    ))
    await db.commit()
    logger.info("agent_memory table ready")
