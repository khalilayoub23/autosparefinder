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
import json
import os
import re
import sys
from datetime import datetime, timedelta

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
CHECKPOINT_FILE = "/app/state/meili_sync_checkpoint.json"

# Safe default: incremental upsert (no index deletion). Use --rebuild only for
# schema changes that require a full reindex. Setting MEILI_REBUILD=1 in env
# forces rebuild; checkpoint still prevents accidental re-deletion if it exists.
REBUILD_DEFAULT = os.getenv("MEILI_REBUILD", "0").strip().lower() not in {
    "0",
    "false",
    "no",
}


def _load_checkpoint() -> dict | None:
    try:
        with open(CHECKPOINT_FILE) as f:
            data = json.load(f)
        if data.get("index_ready") and isinstance(data.get("offset"), int):
            return data
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        pass
    return None


def _save_checkpoint(offset: int, total: int, manufacturer_filter: str | None = None,
                     last_id: str | None = None, cutoff: str | None = None,
                     saved_at: str | None = None) -> None:
    os.makedirs(os.path.dirname(CHECKPOINT_FILE), exist_ok=True)
    with open(CHECKPOINT_FILE, "w") as f:
        json.dump({
            "index_ready": True,
            "offset": offset,
            "total": total,
            "last_id": last_id,
            "cutoff": cutoff,
            "manufacturer": manufacturer_filter,
            "updated_at": saved_at or datetime.utcnow().isoformat(),
        }, f)


def _clear_checkpoint() -> None:
    try:
        os.remove(CHECKPOINT_FILE)
    except FileNotFoundError:
        pass

INDEX_SETTINGS = MeilisearchSettings(
    searchable_attributes=["name", "name_he", "sku", "oem_number", "manufacturer", "category"],
    filterable_attributes=[
        "id",
        "manufacturer",
        "category",
        "part_type",
        "part_condition",
        "is_active",
        "is_safety_critical",
        "oem_number",
        "sku",
        "min_price_ils",
        "importer_price_ils",
        "has_il_price",
    ],
    sortable_attributes=["min_price_ils", "base_price", "importer_price_ils"],
    ranking_rules=["words", "typo", "proximity", "attribute", "sort", "exactness"],
    typo_tolerance=TypoTolerance(
        enabled=True,
        min_word_size_for_typos=MinWordSizeForTypos(one_typo=4, two_typos=8),
    ),
)

# Keyset pagination (id > last_id) instead of OFFSET — OFFSET N forces a full
# re-sort of all N+batch rows per query, degrading to ~100s/batch at high
# offsets (measured 2026-07-02: a full pass would have taken ~23h). Keyset is
# O(batch) per query via the PK index regardless of position.
ZERO_UUID = "00000000-0000-0000-0000-000000000000"

SELECT_SQL_ALL = """
    SELECT id::text, sku, name, name_he, manufacturer, category,
           part_type, part_condition, oem_number, is_active, is_safety_critical,
           min_price_ils::float, base_price::float,
           importer_price_ils::float,
           (importer_price_ils IS NOT NULL AND importer_price_ils > 0) AS has_il_price
    FROM parts_catalog
    WHERE is_active = TRUE AND id > $1::uuid
    ORDER BY id
    LIMIT $2
"""

SELECT_SQL_INCREMENTAL = """
    SELECT id::text, sku, name, name_he, manufacturer, category,
           part_type, part_condition, oem_number, is_active, is_safety_critical,
           min_price_ils::float, base_price::float,
           importer_price_ils::float,
           (importer_price_ils IS NOT NULL AND importer_price_ils > 0) AS has_il_price
    FROM parts_catalog
    WHERE is_active = TRUE AND id > $1::uuid AND updated_at > $3
    ORDER BY id
    LIMIT $2
"""

SELECT_SQL_MANUFACTURER = """
    SELECT id::text, sku, name, name_he, manufacturer, category,
           part_type, part_condition, oem_number, is_active, is_safety_critical,
           min_price_ils::float, base_price::float,
           importer_price_ils::float,
           (importer_price_ils IS NOT NULL AND importer_price_ils > 0) AS has_il_price
    FROM parts_catalog
    WHERE LOWER(manufacturer) = LOWER($3)
      AND is_active = TRUE AND id > $1::uuid
    ORDER BY id
    LIMIT $2
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

    # Check for a resumable checkpoint (only for full-catalog runs, not manufacturer-scoped)
    checkpoint = None
    resume_offset = 0
    resume_last_id = ZERO_UUID
    incremental_cutoff = None  # datetime → sync only rows updated since then
    run_start = datetime.utcnow()
    if not manufacturer_filter:
        checkpoint = _load_checkpoint()
        if checkpoint:
            resume_offset = checkpoint["offset"]
            resume_last_id = checkpoint.get("last_id") or ZERO_UUID
            if resume_last_id != ZERO_UUID and checkpoint.get("cutoff"):
                # Interrupted incremental run — resume it with the same cutoff.
                incremental_cutoff = datetime.fromisoformat(checkpoint["cutoff"])
                print(f"[meili_sync] Resuming interrupted incremental run (cutoff={incremental_cutoff}).", flush=True)
            elif resume_last_id == ZERO_UUID and 0 < resume_offset < checkpoint.get("total", total):
                # Old-format (pre keyset) mid-pass checkpoint can't be resumed
                # positionally — restart the pass (upserts are idempotent).
                print("[meili_sync] Old-format mid-pass checkpoint (no last_id) — restarting pass from 0.", flush=True)
                resume_offset = 0
            elif resume_offset >= checkpoint.get("total", 0) and resume_last_id == ZERO_UUID:
                # Completed pass → incremental mode. Parts get random UUIDs, so
                # id-position tells us nothing about what's new — filter by
                # updated_at instead (1h overlap margin for clock skew /
                # in-flight writes at checkpoint save time).
                try:
                    saved_at = datetime.fromisoformat(checkpoint["updated_at"])
                    incremental_cutoff = saved_at - timedelta(hours=1)
                    total = await conn.fetchval(
                        "SELECT COUNT(*) FROM parts_catalog WHERE is_active=TRUE AND updated_at > $1",
                        incremental_cutoff,
                    )
                    resume_offset = 0
                    print(f"[meili_sync] Incremental mode: {total} rows updated since {incremental_cutoff}.", flush=True)
                except (KeyError, ValueError):
                    print("[meili_sync] Completed checkpoint missing updated_at — full pass.", flush=True)
            print(
                f"[meili_sync] Checkpoint found: resuming from {resume_offset}/{total} "
                f"last_id={resume_last_id[:13]}… (saved {checkpoint.get('updated_at', '?')})",
                flush=True,
            )

    async with AsyncClient(MEILI_URL, MEILI_MASTER_KEY) as client:
        index = client.index(INDEX_NAME)

        if checkpoint:
            # Resume mode: continue uploading from checkpoint offset.
            # Always update settings even on resume so new filterable/sortable
            # attributes take effect without requiring a full rebuild.
            # On resume: submit settings update but don't block on it.
            # Meilisearch processes settings + document uploads in parallel.
            # New filterable attrs (part_condition, importer_price_ils) go live
            # once Meilisearch finishes reindexing in the background.
            print("[meili_sync] Resuming upload — settings update submitted (non-blocking).", flush=True)
            try:
                await index.update_settings(INDEX_SETTINGS)
            except Exception as _se:
                print(f"[meili_sync] Settings task queued: {_se}", flush=True)
        elif rebuild_index and not manufacturer_filter:
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

        if not checkpoint:
            settings_task = await index.update_settings(INDEX_SETTINGS)
            try:
                await _wait_task(client, settings_task, timeout_ms=1_800_000)  # 30 min — large indexes take time
                print("[meili_sync] Index settings applied.", flush=True)
            except Exception as e:
                # Settings task timed out but keeps processing in Meilisearch background — continue sync
                print(f"[meili_sync] Settings task timeout (non-fatal, Meilisearch continues in background): {e}", flush=True)

        # Write initial checkpoint before first batch (marks index as ready)
        if not manufacturer_filter and not checkpoint:
            _save_checkpoint(0, total)

        last_id = resume_last_id
        total_sent = resume_offset  # already uploaded before checkpoint
        t0 = datetime.utcnow()
        last_batch_task = None

        while True:
            if manufacturer_filter:
                rows = await conn.fetch(
                    SELECT_SQL_MANUFACTURER,
                    last_id,
                    BATCH_SIZE,
                    manufacturer_filter,
                )
            elif incremental_cutoff is not None:
                rows = await conn.fetch(SELECT_SQL_INCREMENTAL, last_id, BATCH_SIZE, incremental_cutoff)
            else:
                rows = await conn.fetch(SELECT_SQL_ALL, last_id, BATCH_SIZE)

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
            last_id = rows[-1]["id"]
            elapsed = (datetime.utcnow() - t0).seconds
            print(f"[meili_sync] uploaded {total_sent}/{total} ({elapsed}s)", flush=True)

            # Save checkpoint after every batch so a restart can resume from here
            if not manufacturer_filter:
                _save_checkpoint(
                    total_sent, total, last_id=last_id,
                    cutoff=incremental_cutoff.isoformat() if incremental_cutoff else None,
                )

        # Wait for the final batch to finish indexing before exiting.
        if last_batch_task is not None:
            print("[meili_sync] Waiting for Meilisearch to finish indexing...", flush=True)
            await _wait_task(client, last_batch_task, timeout_ms=900_000)

    await conn.close()

    # Mark checkpoint as fully complete — do NOT clear it.
    # Clearing the checkpoint causes the next run to see no checkpoint and
    # trigger a full rebuild (deleting 3.45M indexed documents). Instead,
    # save offset=total so the next run loads the checkpoint, skips upload
    # (no rows at that offset), and exits safely without touching the index.
    if not manufacturer_filter:
        # Completed checkpoint: last_id=None + offset==total → next run enters
        # incremental mode. updated_at = this run's START time so rows changed
        # while the pass was running are re-checked next time.
        _save_checkpoint(total, total, saved_at=run_start.isoformat())

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
    # Single-instance guard: two concurrent syncs clobber each other's
    # checkpoint (root-caused 2026-07-02 — supervised 2h loop spawned a run
    # while a manual catch-up was mid-pass; offsets overwrote each other).
    import fcntl
    _lock_f = open("/tmp/meili_sync.lock", "w")
    try:
        fcntl.flock(_lock_f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("[meili_sync] another instance is already running — exiting", flush=True)
        sys.exit(0)

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
