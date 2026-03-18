#!/usr/bin/env python3
"""
generate_embeddings.py — Populate parts_catalog.embedding via nomic-embed-text on Ollama.

Usage:
    python generate_embeddings.py            # embed all pending parts
    python generate_embeddings.py --dry-run  # count candidates only

A part is "pending" if:  embedding IS NULL AND is_active = TRUE

Text input per part:
    "{name} | {name_he} | {category} | {manufacturer} | {part_type} | {description[:200]}"

Model: nomic-embed-text (768-dim).
Ensure it is pulled on the Ollama VPS before running:
    ollama pull nomic-embed-text

API: POST {OLLAMA_URL}/api/embed
     {"model": "nomic-embed-text", "input": "<text>"}
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import datetime

import httpx
from dotenv import load_dotenv
from sqlalchemy import text

load_dotenv()

sys.path.insert(0, os.path.dirname(__file__))
from BACKEND_DATABASE_MODELS import async_session_factory

OLLAMA_URL     = os.getenv("OLLAMA_URL", "").rstrip("/")
EMBED_MODEL    = "nomic-embed-text"
BATCH_SIZE     = 50   # rows fetched per DB round-trip
CONCURRENT     = 10   # max parallel Ollama calls within a batch
OLLAMA_TIMEOUT = 10.0 # seconds per embedding call


def build_text(row) -> str:
    """Concatenate the most searchable fields into a single embedding input string."""
    parts = [
        row.name         or "",
        row.name_he      or "",
        row.category     or "",
        row.manufacturer or "",
        row.part_type    or "",
        (row.description or "")[:200],
    ]
    return " | ".join(p for p in parts if p)


async def embed_text(client: httpx.AsyncClient, text_input: str) -> list[float] | None:
    """Call Ollama /api/embed and return the 768-dim vector, or None on failure."""
    try:
        resp = await client.post(
            f"{OLLAMA_URL}/api/embed",
            json={"model": EMBED_MODEL, "input": text_input},
            timeout=OLLAMA_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        # Ollama returns {"embeddings": [[...floats...]]}
        embeddings = data.get("embeddings") or data.get("embedding")
        if embeddings and isinstance(embeddings[0], list):
            return embeddings[0]
        if embeddings and isinstance(embeddings[0], float):
            return embeddings
        return None
    except Exception as exc:
        print(f"[EmbedGen] Ollama error: {exc}")
        return None


async def count_pending(db) -> int:
    row = await db.execute(
        text("""
            SELECT COUNT(*)
            FROM parts_catalog
            WHERE embedding IS NULL
              AND is_active = TRUE
        """)
    )
    return row.scalar()


async def write_audit_row(db, total_processed: int) -> None:
    vtag = f"embedgen-{uuid.uuid4().hex[:16]}"
    await db.execute(
        text("""
            INSERT INTO catalog_versions
                (id, version_tag, description, parts_added, parts_updated,
                 source, status, created_at)
            VALUES
                (gen_random_uuid(), :vtag, :desc, 0, :updated,
                 'embedding_generator', 'completed', NOW())
            ON CONFLICT (version_tag) DO NOTHING
        """),
        {
            "vtag":    vtag,
            "desc":    f"Text embeddings generated: {total_processed} parts",
            "updated": total_processed,
        },
    )
    await db.commit()


async def run(dry_run: bool = False) -> None:
    if not OLLAMA_URL:
        print("[EmbedGen] ERROR: OLLAMA_URL is not set in .env")
        sys.exit(1)

    t0 = datetime.utcnow()

    async with async_session_factory() as db:
        total_pending = await count_pending(db)

    print(
        f"[EmbedGen] {total_pending} parts have embedding=NULL  "
        f"(model: {EMBED_MODEL}, OLLAMA_URL: {OLLAMA_URL})"
    )

    if dry_run:
        print("[EmbedGen] --dry-run: nothing processed.")
        return

    if total_pending == 0:
        print("[EmbedGen] Nothing to do.")
        return

    batch_num       = 0
    total_processed = 0
    total_errors    = 0

    async with httpx.AsyncClient() as client:
        # Semaphore caps concurrent Ollama calls to CONCURRENT within each batch
        sem = asyncio.Semaphore(CONCURRENT)

        async def embed_limited(text_input: str) -> list[float] | None:
            async with sem:
                return await embed_text(client, text_input)

        while True:
            async with async_session_factory() as db:
                rows = (await db.execute(
                    text("""
                        SELECT id, sku, name, name_he, category,
                               manufacturer, part_type, description
                        FROM parts_catalog
                        WHERE embedding IS NULL
                          AND is_active = TRUE
                        ORDER BY created_at ASC
                        LIMIT :lim
                    """),
                    {"lim": BATCH_SIZE},
                )).fetchall()

                if not rows:
                    break

                batch_num += 1

                # Fire all BATCH_SIZE embed calls concurrently, capped to CONCURRENT in-flight
                vectors = await asyncio.gather(
                    *[embed_limited(build_text(row)) for row in rows]
                )

                batch_processed = 0
                batch_errors    = 0

                for row, vec in zip(rows, vectors):
                    if vec is None:
                        # Leave embedding=NULL so a retry run can pick this row up again
                        batch_errors += 1
                        continue
                    await db.execute(
                        text("""
                            UPDATE parts_catalog
                            SET embedding = CAST(:vec AS vector)
                            WHERE id = :id
                        """),
                        {"vec": str(vec), "id": str(row.id)},
                    )
                    batch_processed += 1

                await db.commit()

            total_processed += batch_processed
            total_errors    += batch_errors
            elapsed = (datetime.utcnow() - t0).seconds
            print(
                f"[EmbedGen] batch {batch_num} — "
                f"processed {total_processed} / {total_pending}  "
                f"(errors: {total_errors}, {elapsed}s elapsed)"
            )

            if batch_processed == 0:
                print("[EmbedGen] Batch returned 0 embedded — stopping to avoid infinite loop.")
                break

    # Final audit row
    async with async_session_factory() as db:
        await write_audit_row(db, total_processed)

    elapsed = (datetime.utcnow() - t0).seconds
    print(
        f"[EmbedGen] Complete — {total_processed} parts embedded, "
        f"{total_errors} errors, {elapsed}s total."
    )


if __name__ == "__main__":
    asyncio.run(run(dry_run="--dry-run" in sys.argv))
