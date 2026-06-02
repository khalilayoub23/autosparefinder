"""
meili_sync.py - Bulk-load parts_catalog rows into Meilisearch index 'parts'.

Default behavior is a deterministic full rebuild to prevent stale documents from
surviving after DB-side deletions/merges. Use --no-rebuild to keep incremental
upsert behavior when needed.

Use --manufacturer to scope refresh to one manufacturer without deleting the
entire index. In scoped mode, existing docs for that manufacturer are deleted
by filter and then re-uploaded from DB.

Usage:
  python meili_sync.py [--dry-run] [--rebuild|--no-rebuild] [--manufacturer ORA]
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
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
    _raw_meili_db.replace("postgresql+asyncpg://", "postgresql://").replace("+asyncpg", "")
)

INDEX_NAME = "parts"
BATCH_SIZE = 5000

# Root-fix default: rebuild index unless explicitly disabled.
REBUILD_DEFAULT = os.getenv("MEILI_REBUILD", "1").strip().lower() not in {
    "0",
    "false",
    "no",
}

INDEX_SETTINGS = MeilisearchSettings(
    searchable_attributes=["name", "name_he", "sku", "oem_number", "manufacturer", "category"],
    filterable_attributes=[
        "id",
        "manufacturer",
        "category",
        "part_type",
        "is_active",
        "is_safety_critical",
        "oem_number",
        "sku",
        "min_price_ils",
    ],
    sortable_attributes=["min_price_ils", "base_price"],
    ranking_rules=["words", "typo", "proximity", "attribute", "sort", "exactness"],
    typo_tolerance=TypoTolerance(
        enabled=True,
        min_word_size_for_typos=MinWordSizeForTypos(one_typo=4, two_typos=8),
    ),
)

SELECT_SQL_ALL = """
    SELECT id::text, sku, name, name_he, manufacturer, category,
           part_type, oem_number, is_active, is_safety_critical,
           min_price_ils::float, base_price::float
    FROM parts_catalog
    WHERE is_active = TRUE
    ORDER BY id
    OFFSET $1 LIMIT $2
"""

SELECT_SQL_MANUFACTURER = """
    SELECT id::text, sku, name, name_he, manufacturer, category,
           part_type, oem_number, is_active, is_safety_critical,
           min_price_ils::float, base_price::float
    FROM parts_catalog
    WHERE LOWER(manufacturer) = LOWER($3)
      AND is_active = TRUE
    ORDER BY id
    OFFSET $1 LIMIT $2
"""


def _normalize_manufacturer_filter(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _escape_meili_filter_value(value: str) -> str:
    return re.sub(r'(["\\])', r"\\\1", value)


async def _wait_task(client: AsyncClient, task_info, timeout_ms: int = 900_000) -> None:
    task_uid = getattr(task_info, "task_uid", None)
    if task_uid is None:
        return
    await client.wait_for_task(
        task_uid,
        timeout_in_ms=timeout_ms,
        interval_in_ms=200,
        raise_for_status=True,
    )


async def run(
    *,
    dry_run: bool = False,
    rebuild_index: bool = REBUILD_DEFAULT,
    manufacturer_filter: str | None = None,
) -> None:
    manufacturer_filter = _normalize_manufacturer_filter(manufacturer_filter)
    conn = await asyncpg.connect(DB_URL)

    if manufacturer_filter:
        total = await conn.fetchval(
            "SELECT COUNT(*) FROM parts_catalog WHERE LOWER(manufacturer)=LOWER($1) AND is_active=TRUE",
            manufacturer_filter,
        )
    else:
        total = await conn.fetchval("SELECT COUNT(*) FROM parts_catalog WHERE is_active=TRUE")

    print(
        f"[meili_sync] {total} rows in parts_catalog "
        f"(MEILI_URL={MEILI_URL}, rebuild={rebuild_index}, manufacturer={manufacturer_filter or 'ALL'})",
        flush=True,
    )

    if dry_run:
        print("[meili_sync] --dry-run: no data uploaded.", flush=True)
        await conn.close()
        return

    async with AsyncClient(MEILI_URL, MEILI_MASTER_KEY) as client:
        index = client.index(INDEX_NAME)

        if rebuild_index and not manufacturer_filter:
            try:
                delete_task = await index.delete()
                await _wait_task(client, delete_task, timeout_ms=300_000)
                print("[meili_sync] Existing index deleted for clean rebuild.", flush=True)
            except Exception:
                print("[meili_sync] Existing index not found; creating new index.", flush=True)

            create_task = await client.create_index(INDEX_NAME, primary_key="id")
            await _wait_task(client, create_task, timeout_ms=120_000)
        else:
            try:
                create_task = await client.create_index(INDEX_NAME, primary_key="id")
                await _wait_task(client, create_task, timeout_ms=120_000)
                print("[meili_sync] Created missing index.", flush=True)
            except Exception:
                pass

        if manufacturer_filter:
            escaped = _escape_meili_filter_value(manufacturer_filter)
            # Count existing docs before deleting so we can report progress
            existing_count = await conn.fetchval(
                "SELECT COUNT(*) FROM parts_catalog WHERE LOWER(manufacturer)=LOWER($1) AND is_active=TRUE",
                manufacturer_filter,
            )
            print(f"[meili_sync] Deleting existing docs for '{manufacturer_filter}' (up to {existing_count} docs)...", flush=True)
            delete_filter_task = await index.delete_documents_by_filter(
                f'manufacturer = "{escaped}"'
            )
            await _wait_task(client, delete_filter_task, timeout_ms=300_000)
            print(
                "[meili_sync] Deleted existing documents for manufacturer "
                f"'{manufacturer_filter}'.",
                flush=True,
            )

        settings_task = await index.update_settings(INDEX_SETTINGS)
        await _wait_task(client, settings_task, timeout_ms=300_000)
        print("[meili_sync] Index settings applied.", flush=True)

        offset = 0
        total_sent = 0
        t0 = datetime.utcnow()
        last_batch_task = None

        while True:
            if manufacturer_filter:
                rows = await conn.fetch(
                    SELECT_SQL_MANUFACTURER,
                    offset,
                    BATCH_SIZE,
                    manufacturer_filter,
                )
            else:
                rows = await conn.fetch(SELECT_SQL_ALL, offset, BATCH_SIZE)

            if not rows:
                break

            docs = [dict(r) for r in rows]
            for d in docs:
                d["is_active"] = bool(d["is_active"])
                d["is_safety_critical"] = bool(d["is_safety_critical"])

            # Fire upload without waiting — Meilisearch queues tasks internally.
            # We wait only once after all batches are sent (see below).
            last_batch_task = await index.add_documents(docs)

            total_sent += len(docs)
            offset += BATCH_SIZE
            elapsed = (datetime.utcnow() - t0).seconds
            print(f"[meili_sync] uploaded {total_sent}/{total} ({elapsed}s)", flush=True)

        # Wait for the final batch to finish indexing before exiting.
        if last_batch_task is not None:
            print("[meili_sync] Waiting for Meilisearch to finish indexing...", flush=True)
            await _wait_task(client, last_batch_task, timeout_ms=900_000)

    await conn.close()
    print(
        "[meili_sync] Done - "
        f"{total_sent} documents indexed for {manufacturer_filter or 'ALL'}.",
        flush=True,
    )


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync parts_catalog to Meilisearch")
    parser.add_argument("--dry-run", action="store_true", help="Do not upload documents")
    parser.add_argument(
        "--manufacturer",
        type=str,
        default=None,
        help="Sync only one manufacturer (scoped refresh, no full index rebuild)",
    )
    parser.add_argument("--rebuild", action="store_true", help="Force full index rebuild")
    parser.add_argument(
        "--no-rebuild",
        action="store_true",
        help="Disable full rebuild and upsert docs",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args(sys.argv[1:])
    dry = args.dry_run
    rebuild = REBUILD_DEFAULT
    if args.rebuild:
        rebuild = True
    if args.no_rebuild:
        rebuild = False

    if args.manufacturer and rebuild:
        print(
            "[meili_sync] --manufacturer provided; using scoped refresh "
            "without full index rebuild.",
            flush=True,
        )
        rebuild = False

    asyncio.run(
        run(
            dry_run=dry,
            rebuild_index=rebuild,
            manufacturer_filter=args.manufacturer,
        )
    )
