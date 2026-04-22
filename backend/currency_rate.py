"""Shared USD/ILS exchange-rate helpers.

Canonical key: ``ils_per_usd``
Legacy key (kept in sync for compatibility): ``currency_exchange_rate_usd_to_ils``
"""

from __future__ import annotations

import os
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from BACKEND_DATABASE_MODELS import USD_TO_ILS


FX_CANONICAL_KEY = "ils_per_usd"
FX_LEGACY_KEY = "currency_exchange_rate_usd_to_ils"
FX_KEYS = (FX_CANONICAL_KEY, FX_LEGACY_KEY)

DEFAULT_USD_TO_ILS = float(os.getenv("USD_TO_ILS", str(USD_TO_ILS)))


def _normalize_rate(value: object, fallback: float = DEFAULT_USD_TO_ILS) -> float:
    try:
        rate = float(value)
        if 2.0 <= rate <= 10.0:
            return rate
    except Exception:
        pass
    return float(fallback)


async def get_usd_to_ils_rate(db: AsyncSession, fallback: float = DEFAULT_USD_TO_ILS) -> float:
    """Return USD->ILS rate from system settings, preferring canonical key."""
    try:
        rows = (await db.execute(
            text(
                """
                SELECT key, value
                FROM system_settings
                WHERE key IN (:k1, :k2)
                ORDER BY CASE
                    WHEN key = :preferred THEN 0
                    WHEN key = :legacy THEN 1
                    ELSE 2
                END
                """
            ),
            {
                "k1": FX_CANONICAL_KEY,
                "k2": FX_LEGACY_KEY,
                "preferred": FX_CANONICAL_KEY,
                "legacy": FX_LEGACY_KEY,
            },
        )).fetchall()
        for row in rows:
            rate = _normalize_rate(row[1], fallback=float(fallback))
            if rate > 0:
                return rate
    except Exception:
        pass
    return _normalize_rate(fallback, fallback=DEFAULT_USD_TO_ILS)


async def upsert_usd_to_ils_rate(
    db: AsyncSession,
    rate: float,
) -> float:
    """Persist USD->ILS rate to canonical + legacy setting keys."""
    normalized_rate = _normalize_rate(rate)
    str_value = f"{normalized_rate:.6f}"

    payloads = (
        (
            FX_CANONICAL_KEY,
            "USD to ILS exchange rate (canonical runtime key)",
        ),
        (
            FX_LEGACY_KEY,
            "USD to ILS exchange rate (legacy compatibility key)",
        ),
    )

    for key, description in payloads:
        await db.execute(
            text(
                """
                INSERT INTO system_settings (id, key, value, value_type, description, is_public, updated_at)
                VALUES (:id, :key, :value, 'float', :description, TRUE, NOW())
                ON CONFLICT (key)
                DO UPDATE SET
                    value = EXCLUDED.value,
                    value_type = EXCLUDED.value_type,
                    description = EXCLUDED.description,
                    is_public = EXCLUDED.is_public,
                    updated_at = NOW()
                """
            ),
            {
                "id": str(uuid.uuid4()),
                "key": key,
                "value": str_value,
                "description": description,
            },
        )

    return normalized_rate
