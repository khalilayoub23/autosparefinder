#!/usr/bin/env python3
"""
Script: base_price_snapshot.py
Purpose: Pre-change export and (separately approved) restore of parts_catalog.base_price - the undo artifact for the A3
         backfill. NOT part of the code rollback: the code rollback restores files, this restores DATA.
Process:
  export  --out DIR   : one REPEATABLE READ, READ ONLY transaction streams (id, base_price) ordered by id to
                        DIR/base_price_snapshot.csv; writes DIR/manifest.json {rows, nonnull, sha256, taken_at, max_updated_at}.
  verify  --dir DIR   : recomputes sha256 + row count of the CSV against the manifest (no database access).
  plan    --dir DIR   : read-only: how many live rows differ from the snapshot (changed / missing / new since).
  restore --dir DIR   : DRY RUN unless --apply AND the environment variable MAINT_RESTORE_CONFIRM equals the manifest sha256.
                        Updates only base_price (and updated_at) of rows that differ, in batches of --batch ids, one short
                        transaction each, SKIP LOCKED. Rows created after the snapshot are never touched.
Data Imported/Modified: reads parts_catalog; restore --apply writes base_price/updated_at only.
Data Sources: DATABASE_URL.
Missing Data Delegation: none.
Last Updated: 2026-11-01
"""
import argparse
import asyncio
import csv
import hashlib
import json
import os
import sys
import uuid
from decimal import Decimal
from datetime import datetime, timezone
from pathlib import Path

import asyncpg

CSV_NAME, MANIFEST = "base_price_snapshot.csv", "manifest.json"


def _dsn() -> str:
    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


async def export_snapshot(conn, out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    csv_path = out / CSV_NAME
    rows = nonnull = 0
    # one consistent snapshot: REPEATABLE READ, READ ONLY (when the caller already holds a transaction, join it)
    txn = conn.transaction() if conn.is_in_transaction() else conn.transaction(isolation="repeatable_read", readonly=True)
    async with txn:
        taken = await conn.fetchval("SELECT now()")
        max_upd = await conn.fetchval("SELECT max(updated_at) FROM parts_catalog")
        with open(csv_path, "w", newline="") as fh:
            w = csv.writer(fh)
            async for r in conn.cursor("SELECT id::text, base_price FROM parts_catalog ORDER BY id", prefetch=20000):
                w.writerow([r[0], "" if r[1] is None else str(r[1])])
                rows += 1
                nonnull += r[1] is not None
    manifest = {"rows": rows, "nonnull": nonnull, "sha256": file_sha256(csv_path),
                "taken_at": taken.isoformat(), "max_updated_at": max_upd.isoformat() if max_upd else None,
                "created_by": "base_price_snapshot.py export", "file": CSV_NAME}
    (out / MANIFEST).write_text(json.dumps(manifest, indent=1))
    return manifest


def verify_snapshot(directory: Path) -> dict:
    m = json.loads((directory / MANIFEST).read_text())
    csv_path = directory / m["file"]
    sha = file_sha256(csv_path)
    with open(csv_path, newline="") as fh:
        n = sum(1 for _ in csv.reader(fh))
    return {"ok": sha == m["sha256"] and n == m["rows"], "sha256_match": sha == m["sha256"],
            "rows_file": n, "rows_manifest": m["rows"], "sha256": sha}


def _load(directory: Path):
    with open(directory / CSV_NAME, newline="") as fh:
        for rid, base in csv.reader(fh):
            yield rid, (None if base == "" else base)


async def _stage(conn, directory: Path) -> int:
    await conn.execute("CREATE TEMP TABLE IF NOT EXISTS _bp_snap (id uuid PRIMARY KEY, base_price numeric(10,2))")
    await conn.execute("TRUNCATE _bp_snap")
    batch, total = [], 0
    for rid, base in _load(directory):
        batch.append((uuid.UUID(rid), None if base is None else Decimal(base)))
        if len(batch) >= 20000:
            await conn.copy_records_to_table("_bp_snap", records=batch)
            total += len(batch)
            batch = []
    if batch:
        await conn.copy_records_to_table("_bp_snap", records=batch)
        total += len(batch)
    return total


async def plan(conn, directory: Path) -> dict:
    v = verify_snapshot(directory)
    if not v["ok"]:
        raise RuntimeError(f"snapshot failed verification: {v}")
    staged = await _stage(conn, directory)
    differ = await conn.fetchval("""SELECT count(*) FROM _bp_snap s JOIN parts_catalog p ON p.id = s.id
                                    WHERE p.base_price IS DISTINCT FROM s.base_price""")
    missing = await conn.fetchval("SELECT count(*) FROM _bp_snap s WHERE NOT EXISTS (SELECT 1 FROM parts_catalog p WHERE p.id = s.id)")
    return {"snapshot_rows": staged, "differ": differ, "deleted_since": missing}


async def restore(conn, directory: Path, *, apply: bool, batch: int = 5000, confirm: str = "") -> dict:
    p = await plan(conn, directory)
    manifest = json.loads((directory / MANIFEST).read_text())
    if not apply:
        return {**p, "applied": 0, "dry_run": True}
    if confirm != manifest["sha256"]:
        raise PermissionError("restore --apply needs MAINT_RESTORE_CONFIRM=<manifest sha256>")
    applied = 0
    while True:
        async with conn.transaction():
            await conn.execute("SET LOCAL lock_timeout = '5s'")
            n = await conn.fetchval("""
                WITH todo AS (
                    SELECT p.id, s.base_price AS v FROM _bp_snap s JOIN parts_catalog p ON p.id = s.id
                    WHERE p.base_price IS DISTINCT FROM s.base_price ORDER BY p.id LIMIT $1 FOR UPDATE OF p SKIP LOCKED),
                upd AS (UPDATE parts_catalog p SET base_price = todo.v, updated_at = NOW() FROM todo
                        WHERE p.id = todo.id RETURNING 1)
                SELECT count(*) FROM upd""", batch)
        applied += n
        if n == 0:
            break
    return {**p, "applied": applied, "dry_run": False}


async def _main(argv) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("Process:")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export"); e.add_argument("--out", required=True)
    for name in ("verify", "plan", "restore"):
        s = sub.add_parser(name); s.add_argument("--dir", required=True)
        if name == "restore":
            s.add_argument("--apply", action="store_true"); s.add_argument("--batch", type=int, default=5000)
    a = ap.parse_args(argv)
    if a.cmd == "verify":
        r = verify_snapshot(Path(a.dir)); print(json.dumps(r, indent=1)); return 0 if r["ok"] else 1
    conn = await asyncpg.connect(_dsn())
    try:
        if a.cmd == "export":
            print(json.dumps(await export_snapshot(conn, Path(a.out)), indent=1))
        elif a.cmd == "plan":
            print(json.dumps(await plan(conn, Path(a.dir)), indent=1))
        else:
            print(json.dumps(await restore(conn, Path(a.dir), apply=a.apply, batch=a.batch,
                                           confirm=os.getenv("MAINT_RESTORE_CONFIRM", "")), indent=1))
    finally:
        await conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main(sys.argv[1:])))
