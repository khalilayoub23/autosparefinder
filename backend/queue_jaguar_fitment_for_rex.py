"""
queue_jaguar_fitment_for_rex.py
--------------------------------
Queues SNG Barratt Jaguar parts that still have no vehicle fitment
as batched agent_todos for REX to research via external providers
(autodoc, partsouq, etc.).

Usage:
    python3 queue_jaguar_fitment_for_rex.py [--dry-run] [--batch-size N] [--clear-old]

    --dry-run      Print what would be inserted, don't write to DB.
    --batch-size   Parts per todo batch (default 500).
    --clear-old    Delete previously queued 'not_started' jaguar_fitment todos first.
"""

import argparse
import asyncio
import json
import os
import uuid
from typing import Any, Dict, List

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://autospare:autospare@autospare_postgres_catalog:5432/autospare",
)

_engine = create_async_engine(DATABASE_URL, pool_size=3, max_overflow=1, echo=False)
_session_factory = async_sessionmaker(_engine, expire_on_commit=False)


async def _get_unmapped_parts(db) -> List[Dict[str, Any]]:
    """
    Returns SNG Barratt Jaguar parts that:
      - are active
      - have an OEM number
      - have NO rows in part_vehicle_fitment
    """
    rows = (await db.execute(text("""
        SELECT pc.id, pc.oem_number, pc.name
        FROM parts_catalog pc
        JOIN supplier_parts sp ON sp.part_id = pc.id
        JOIN suppliers s ON s.id = sp.supplier_id
        WHERE pc.is_active = TRUE
          AND pc.manufacturer = 'Jaguar'
          AND pc.oem_number IS NOT NULL
          AND btrim(pc.oem_number) <> ''
          AND s.name ILIKE '%sng%'
          AND NOT EXISTS (
              SELECT 1 FROM part_vehicle_fitment pvf
              WHERE pvf.part_id = pc.id
          )
        ORDER BY pc.id
    """))).mappings().all()
    return [dict(r) for r in rows]


async def _clear_old_todos(db) -> int:
    """Delete previously queued not_started jaguar fitment todos for REX."""
    result = await db.execute(text("""
        DELETE FROM agent_todos
        WHERE assigned_to_agent = 'rex'
          AND status = 'not_started'
          AND category = 'fitment'
          AND 'jaguar' = ANY(tags)
          AND artifacts->>'task_names' LIKE '%jaguar_fitment_lookup%'
    """))
    await db.commit()
    return result.rowcount


async def _insert_todos(db, batches: List[List[Dict[str, Any]]], dry_run: bool) -> int:
    """Insert one agent_todo per batch. Returns count inserted."""
    inserted = 0
    for i, batch in enumerate(batches):
        part_ids = [str(r["id"]) for r in batch]
        oem_numbers = [str(r["oem_number"]) for r in batch if r.get("oem_number")]

        title = f"Jaguar fitment lookup batch {i + 1}/{len(batches)} ({len(batch)} parts)"
        description = (
            f"External provider fitment lookup for {len(batch)} SNG Barratt Jaguar parts "
            f"whose OEM prefix had no known fitment reference in the catalog. "
            f"REX should call _sync_vehicle_fitment for each part_id + oem_number pair."
        )
        artifacts = {
            "task_names": ["jaguar_fitment_lookup"],
            "manufacturer": "Jaguar",
            "supplier": "SNG Barratt",
            "batch_index": i + 1,
            "batch_total": len(batches),
            "part_count": len(batch),
            "part_ids": part_ids,
            "oem_numbers": list(dict.fromkeys(oem_numbers)),  # dedupe, preserve order
        }

        if dry_run:
            print(
                f"  [DRY-RUN] Would insert: '{title}' | "
                f"parts={len(batch)} | first_oem={oem_numbers[0] if oem_numbers else 'N/A'}"
            )
            inserted += 1
            continue

        await db.execute(text("""
            INSERT INTO agent_todos
                (id, title, description, status, priority,
                 assigned_to_agent, category, tags, artifacts,
                 created_at, updated_at)
            VALUES
                (:id, :title, :description, 'not_started', 'high',
                 'rex', 'fitment',
                 ARRAY['jaguar', 'fitment', 'sng_barratt', 'external_lookup'],
                 CAST(:artifacts AS jsonb),
                 NOW(), NOW())
        """), {
            "id": str(uuid.uuid4()),
            "title": title,
            "description": description,
            "artifacts": json.dumps(artifacts),
        })
        inserted += 1

    if not dry_run:
        await db.commit()
    return inserted


async def run(dry_run: bool, batch_size: int, clear_old: bool) -> None:
    async with _session_factory() as db:
        # Optionally clear stale todos first
        if clear_old and not dry_run:
            deleted = await _clear_old_todos(db)
            print(f"[Queue] Cleared {deleted} old jaguar fitment todos (not_started).")
        elif clear_old and dry_run:
            print("[Queue] [DRY-RUN] Would clear old not_started jaguar fitment todos.")

        # Fetch unmapped parts
        parts = await _get_unmapped_parts(db)
        print(f"[Queue] Unmapped SNG Jaguar parts (with OEM, no fitment): {len(parts):,}")

        if not parts:
            print("[Queue] Nothing to queue — all Jaguar parts already have fitment.")
            return

        # Split into batches
        batches = [parts[i : i + batch_size] for i in range(0, len(parts), batch_size)]
        print(f"[Queue] Creating {len(batches)} todos (batch_size={batch_size})...")

        inserted = await _insert_todos(db, batches, dry_run=dry_run)

        if dry_run:
            print(f"\n[Queue] DRY-RUN complete — would insert {inserted} todos for {len(parts):,} parts.")
        else:
            print(f"\n[Queue] Done — inserted {inserted} todos covering {len(parts):,} parts.")
            print(f"[Queue] REX will pick them up on its next cycle (check catalog_scraper.py).")


def main():
    parser = argparse.ArgumentParser(description="Queue Jaguar fitment lookup todos for REX")
    parser.add_argument("--dry-run", action="store_true", help="Print only, don't write to DB")
    parser.add_argument("--batch-size", type=int, default=500, help="Parts per todo batch")
    parser.add_argument(
        "--clear-old", action="store_true",
        help="Delete old not_started jaguar fitment todos before inserting new ones"
    )
    args = parser.parse_args()
    asyncio.run(run(dry_run=args.dry_run, batch_size=args.batch_size, clear_old=args.clear_old))


if __name__ == "__main__":
    main()
