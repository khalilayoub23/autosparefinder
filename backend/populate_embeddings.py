#!/usr/bin/env python3
"""
populate_embeddings.py — Root fix for pgvector text embedding data plane.

Purpose:
  Populate the `embedding` column (Vector(1536)) in parts_catalog using
  sentence-transformers embeddings of part names and descriptions.
  
  This enables semantic search and reranking via pgvector.

Idempotency:
  • Skips parts that already have embeddings.
  • Retries on transient HF API failures with backoff.
  • Logs progress and errors for audit.

Usage:
  python populate_embeddings.py [--batch-size=100] [--max-parts=10000]
"""

import asyncio
import logging
import sys
from argparse import ArgumentParser
from typing import Optional
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

sys.path.insert(0, "/app")
from BACKEND_DATABASE_MODELS import PartsCatalog, PartImage
from hf_client import hf_embed


async def get_db_session():
    db_url = "postgresql+asyncpg://autospare_catalog:autospare_catalog_dev@postgres_catalog:5432/autospare_catalog"
    engine = create_async_engine(
        db_url,
        echo=False,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
    )
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return async_session(), engine


async def fetch_parts_without_embeddings(
    session: AsyncSession, limit: int = 10000
) -> list[dict]:
    query = (
        select(
            PartsCatalog.id,
            PartsCatalog.sku,
            PartsCatalog.name,
            PartsCatalog.name_he,
            PartsCatalog.description,
        )
        .where(PartsCatalog.embedding.is_(None))
        .limit(limit)
    )
    result = await session.execute(query)
    rows = result.fetchall()
    parts = [
        {
            "id": row.id,
            "sku": row.sku,
            "name": row.name,
            "name_he": row.name_he,
            "description": row.description,
        }
        for row in rows
    ]
    return parts


def build_embedding_text(part: dict) -> str:
    text_parts = []
    if part.get("name"):
        text_parts.append(part["name"])
    if part.get("name_he"):
        text_parts.append(part["name_he"])
    if part.get("description"):
        text_parts.append(part["description"])
    return " ".join(text_parts)[:1024]


async def populate_embedding_for_part(
    session: AsyncSession, part_id: UUID, embedding: list[float]
) -> bool:
    try:
        await session.execute(
            text(
                """
                UPDATE parts_catalog SET embedding = :emb WHERE id = :id
                """
            ),
            {"emb": embedding, "id": str(part_id)},
        )
        await session.commit()
        return True
    except Exception as e:
        logger.error(f"Failed to store embedding for part {part_id}: {e}")
        await session.rollback()
        return False


async def process_batch(
    session: AsyncSession,
    parts: list[dict],
    retry_count: int = 3,
) -> tuple[int, int]:
    success_count = 0
    error_count = 0

    for i, part in enumerate(parts):
        part_id = part["id"]
        sku = part["sku"]
        text_to_embed = build_embedding_text(part)

        if not text_to_embed.strip():
            logger.warning(f"Part {sku} has no text for embedding; skipping")
            error_count += 1
            continue

        embedding = None
        for attempt in range(retry_count):
            try:
                embedding = await hf_embed(text_to_embed, timeout=30.0)
                if embedding and len(embedding) > 0:
                    break
                logger.warning(f"Part {sku} (attempt {attempt + 1}): empty embedding")
            except Exception as e:
                logger.warning(
                    f"Part {sku} (attempt {attempt + 1}): {e}"
                )
                if attempt < retry_count - 1:
                    await asyncio.sleep(2 ** attempt)

        if not embedding or len(embedding) == 0:
            logger.error(f"Failed to generate embedding for part {sku} after {retry_count} attempts")
            error_count += 1
            continue

        if await populate_embedding_for_part(session, part_id, embedding):
            success_count += 1
            logger.info(f"Embedded part {sku} ({i + 1}/{len(parts)})")
        else:
            error_count += 1

    return success_count, error_count


async def main():
    parser = ArgumentParser()
    parser.add_argument(
        "--batch-size", type=int, default=100, help="Batch size for processing"
    )
    parser.add_argument(
        "--max-parts", type=int, default=10000, help="Max parts to process"
    )
    args = parser.parse_args()

    session, engine = await get_db_session()

    try:
        logger.info(f"Fetching parts without embeddings (max {args.max_parts})...")
        parts = await fetch_parts_without_embeddings(session, limit=args.max_parts)
        logger.info(f"Found {len(parts)} parts without embeddings")

        if not parts:
            logger.info("No parts to process; exiting")
            return

        total_success = 0
        total_error = 0

        for i in range(0, len(parts), args.batch_size):
            batch = parts[i : i + args.batch_size]
            logger.info(f"Processing batch {i // args.batch_size + 1}/{(len(parts) + args.batch_size - 1) // args.batch_size}...")
            batch_success, batch_error = await process_batch(session, batch)
            total_success += batch_success
            total_error += batch_error

        logger.info(
            f"Embedding population complete: {total_success} success, {total_error} errors"
        )

    finally:
        await session.close()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
