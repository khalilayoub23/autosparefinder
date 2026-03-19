"""
generate_image_embeddings.py
────────────────────────────
One-shot backfill script: for every parts_images row where embedding_generated
is FALSE, download the image, embed it via Ollama CLIP, and write the resulting
512-dim vector into parts_catalog.image_embedding.

Usage
-----
    cd /workspaces/autosparefinder/backend
    python generate_image_embeddings.py
    python generate_image_embeddings.py --batch 25
    python generate_image_embeddings.py --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import logging
import os
import sys

import httpx
from sqlalchemy import text

# Allow running from the backend/ dir or from project root
sys.path.insert(0, os.path.dirname(__file__))

from BACKEND_DATABASE_MODELS import async_session_factory  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

OLLAMA_URL: str = os.getenv("OLLAMA_URL", "http://localhost:11434")
CLIP_MODEL: str = os.getenv("CLIP_MODEL", "clip")


# ──────────────────────────────────────────────────────────────────────────────


async def _embed_image(client: httpx.AsyncClient, image_url: str) -> list[float] | None:
    """Download *image_url* and return a 512-dim CLIP embedding, or None on error."""
    r = await client.get(image_url, timeout=15.0, follow_redirects=True)
    r.raise_for_status()
    b64 = base64.b64encode(r.content).decode()

    er = await client.post(
        f"{OLLAMA_URL}/api/embed",
        json={"model": CLIP_MODEL, "input": b64},
        timeout=30.0,
    )
    er.raise_for_status()
    data = er.json()

    emb = data.get("embeddings") or data.get("embedding")
    if not emb:
        return None
    # Ollama may return [[...]] or [...]
    return emb[0] if isinstance(emb[0], list) else emb


# ──────────────────────────────────────────────────────────────────────────────


async def run(batch_size: int, dry_run: bool) -> None:
    logger.info("OLLAMA_URL=%s  CLIP_MODEL=%s  batch=%d  dry_run=%s",
                OLLAMA_URL, CLIP_MODEL, batch_size, dry_run)

    total_ok = 0
    total_err = 0

    async with async_session_factory() as db, httpx.AsyncClient() as client:
        while True:
            rows = (await db.execute(
                text("""
                    SELECT id, part_id, url
                    FROM parts_images
                    WHERE embedding_generated = FALSE
                      AND url IS NOT NULL
                    ORDER BY is_primary DESC, created_at
                    LIMIT :lim
                """),
                {"lim": batch_size},
            )).fetchall()

            if not rows:
                break

            logger.info("Processing %d image(s)…", len(rows))

            for row in rows:
                try:
                    vec = await _embed_image(client, row.url)
                    if vec is None:
                        logger.warning("No embedding returned for %s", row.url[:80])
                        total_err += 1
                        continue

                    if not dry_run:
                        await db.execute(
                            text("UPDATE parts_catalog "
                                 "SET image_embedding = CAST(:v AS vector) "
                                 "WHERE id = :id"),
                            {"v": str(vec), "id": str(row.part_id)},
                        )
                        await db.execute(
                            text("UPDATE parts_images "
                                 "SET embedding_generated = TRUE "
                                 "WHERE id = :id"),
                            {"id": str(row.id)},
                        )
                    else:
                        logger.info("[dry-run] Would embed %s for part %s", row.url[:80], row.part_id)

                    total_ok += 1
                except Exception as exc:
                    logger.warning("Error on %s: %s", row.url[:80], exc)
                    total_err += 1

            if not dry_run:
                await db.commit()

            # If we fetched fewer rows than the batch limit we're done.
            if len(rows) < batch_size:
                break

    logger.info("Done — embedded: %d  errors: %d", total_ok, total_err)


# ──────────────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill CLIP image embeddings")
    parser.add_argument("--batch", type=int, default=50,
                        help="Rows to process per DB round-trip (default: 50)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Fetch & embed but do NOT write to DB")
    args = parser.parse_args()
    asyncio.run(run(args.batch, args.dry_run))


if __name__ == "__main__":
    main()
