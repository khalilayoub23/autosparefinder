#!/usr/bin/env python3
"""
backfill_parts_master.py — One-time backfill of existing parts through enrich_pending_parts().

Usage:
    python backfill_parts_master.py            # run backfill
    python backfill_parts_master.py --dry-run  # count candidates only
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import datetime

from dotenv import load_dotenv
from sqlalchemy import text

load_dotenv()

# Import shared session factory + enrich function
sys.path.insert(0, os.path.dirname(__file__))
from BACKEND_DATABASE_MODELS import async_session_factory
from ai_catalog_builder import enrich_pending_parts

BATCH_SIZE = 100


async def count_pending(db) -> int:
    row = await db.execute(
        text("""
            SELECT COUNT(*)
            FROM parts_catalog
            WHERE needs_oem_lookup = FALSE
              AND master_enriched  = FALSE
              AND is_active        = TRUE
        """)
    )
    return row.scalar()


async def write_audit_row(db, total_processed: int) -> None:
    vtag = f"backfill-{uuid.uuid4().hex[:16]}"
    await db.execute(
        text("""
            INSERT INTO catalog_versions
                (id, version_tag, description, parts_added, parts_updated,
                 source, status, created_at)
            VALUES
                (gen_random_uuid(), :vtag, :desc, 0, :updated,
                 'backfill', 'completed', NOW())
            ON CONFLICT (version_tag) DO NOTHING
        """),
        {
            "vtag":    vtag,
            "desc":    f"Backfill parts_master: {total_processed} parts enriched",
            "updated": total_processed,
        },
    )
    await db.commit()


async def run(dry_run: bool = False) -> None:
    t0 = datetime.utcnow()

    async with async_session_factory() as db:
        total_pending = await count_pending(db)

    print(
        f"[Backfill] {total_pending} parts have "
        f"master_enriched=FALSE AND needs_oem_lookup=FALSE"
    )

    if dry_run:
        print("[Backfill] --dry-run: nothing processed.")
        return

    if total_pending == 0:
        print("[Backfill] Nothing to do.")
        return

    batch_num = 0
    total_processed = 0
    total_errors = 0

    while True:
        async with async_session_factory() as db:
            remaining = await count_pending(db)
            if remaining == 0:
                break

            batch_num += 1
            result = await enrich_pending_parts(db, limit=BATCH_SIZE)

        processed  = result.get("processed",  0)
        errors     = result.get("errors",      0)
        total_processed += processed
        total_errors    += errors

        elapsed = (datetime.utcnow() - t0).seconds
        print(
            f"[Backfill] batch {batch_num} — "
            f"processed {total_processed} / {total_pending}  "
            f"(errors: {total_errors}, {elapsed}s elapsed)"
        )

        if processed == 0:
            # Guard against a stalled batch (e.g. all Ollama failures)
            print("[Backfill] Batch returned 0 processed — stopping to avoid infinite loop.")
            break

    # Final audit row
    async with async_session_factory() as db:
        await write_audit_row(db, total_processed)

    elapsed = (datetime.utcnow() - t0).seconds
    print(
        f"[Backfill] Complete — {total_processed} parts enriched, "
        f"{total_errors} errors, {elapsed}s total."
    )


if __name__ == "__main__":
    asyncio.run(run(dry_run="--dry-run" in sys.argv))
