"""
meili_sync.py — Bulk-loads all parts_catalog rows into Meilisearch 'parts' index.
Usage: python meili_sync.py [--dry-run]
"""
from __future__ import annotations
import asyncio
import os
import sys
from datetime import datetime

import asyncpg
from dotenv import load_dotenv
from meilisearch_python_sdk import AsyncClient
from meilisearch_python_sdk.models.settings import (
    MeilisearchSettings,
    MinWordSizeForTypos,
    TypoTolerance,
)

load_dotenv()

MEILI_URL = os.getenv("MEILI_URL", "http://localhost:7700")
MEILI_MASTER_KEY = os.getenv("MEILI_MASTER_KEY", "")
_raw_meili_db = os.getenv("DATABASE_URL", "")
if not _raw_meili_db:
    raise RuntimeError("DATABASE_URL environment variable is required")
DB_URL = (
    _raw_meili_db
    .replace("postgresql+asyncpg://", "postgresql://")
    .replace("+asyncpg", "")
)
INDEX_NAME = "parts"
BATCH_SIZE = 5_000

INDEX_SETTINGS = MeilisearchSettings(
    searchable_attributes=["name", "name_he", "sku", "oem_number", "manufacturer", "category"],
    filterable_attributes=[
        "id", "manufacturer", "category", "part_type",
        "is_active", "is_safety_critical", "oem_number", "sku", "min_price_ils",
    ],
    sortable_attributes=["min_price_ils", "base_price"],
    ranking_rules=["words", "typo", "proximity", "attribute", "sort", "exactness"],
    typo_tolerance=TypoTolerance(
        enabled=True,
        min_word_size_for_typos=MinWordSizeForTypos(one_typo=4, two_typos=8),
    ),
)

SELECT_SQL = """
    SELECT id::text, sku, name, name_he, manufacturer, category,
           part_type, oem_number, is_active, is_safety_critical,
           min_price_ils::float, base_price::float
    FROM parts_catalog ORDER BY id OFFSET $1 LIMIT $2
"""


async def run(dry_run: bool = False) -> None:
    conn = await asyncpg.connect(DB_URL)
    total = await conn.fetchval("SELECT COUNT(*) FROM parts_catalog")
    print(f"[meili_sync] {total} rows in parts_catalog  (MEILI_URL={MEILI_URL})")
    if dry_run:
        print("[meili_sync] --dry-run: no data uploaded.")
        await conn.close()
        return
    async with AsyncClient(MEILI_URL, MEILI_MASTER_KEY) as client:
        index = client.index(INDEX_NAME)
        try:
            await client.create_index(INDEX_NAME, primary_key="id")
        except Exception:
            pass  # index already exists — safe to continue
        await index.update_settings(INDEX_SETTINGS)
        print("[meili_sync] Index settings applied.")
        offset, total_sent, t0 = 0, 0, datetime.utcnow()
        while True:
            rows = await conn.fetch(SELECT_SQL, offset, BATCH_SIZE)
            if not rows:
                break
            docs = [dict(r) for r in rows]
            for d in docs:
                d["is_active"] = bool(d["is_active"])
                d["is_safety_critical"] = bool(d["is_safety_critical"])
            await index.add_documents(docs)
            total_sent += len(docs)
            offset += BATCH_SIZE
            elapsed = (datetime.utcnow() - t0).seconds
            print(f"[meili_sync]  uploaded {total_sent}/{total}  ({elapsed}s)")
    await conn.close()
    print(f"[meili_sync] Done — {total_sent} documents indexed.")


if __name__ == "__main__":
    asyncio.run(run(dry_run="--dry-run" in sys.argv))
