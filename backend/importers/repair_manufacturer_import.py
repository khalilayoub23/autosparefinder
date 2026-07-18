"""
repair_manufacturer_import.py

Root-fix utility for manufacturer-scoped import reconciliation:
- Reconciles source catalog keys vs DB keys.
- Inserts missing manufacturer keys (safe, deterministic placeholders).
- Fixes inactive rows, missing category, and bad names.
- Reports connectivity (rows without supplier_parts link).

Usage:
  python repair_manufacturer_import.py ORA --apply
  python repair_manufacturer_import.py ORA --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

import asyncpg
import pandas as pd
from dotenv import load_dotenv

from categories import guess_category_by_text

load_dotenv()

_raw_url = os.getenv("DATABASE_URL", "")
if not _raw_url:
    raise RuntimeError("DATABASE_URL environment variable is required")
DB_URL = _raw_url.replace("postgresql+asyncpg://", "postgresql://").replace("+asyncpg", "")


def _candidate_db_urls(url: str) -> list[str]:
    out = [url]
    parsed = urlparse(url)
    host = parsed.hostname
    if not host:
        return out
    if host in {"127.0.0.1", "localhost"}:
        return out

    netloc_local = parsed.netloc.replace(host, "127.0.0.1")
    netloc_loopback = parsed.netloc.replace(host, "localhost")
    out.append(urlunparse(parsed._replace(netloc=netloc_local)))
    out.append(urlunparse(parsed._replace(netloc=netloc_loopback)))

    # Common docker-compose service aliases that may resolve only in-container.
    for alias in ("db", "postgres", "postgres_catalog", "autospare_postgres_catalog"):
        netloc_alias = parsed.netloc.replace(host, alias)
        out.append(urlunparse(parsed._replace(netloc=netloc_alias)))

    # Deduplicate while preserving order.
    dedup = []
    seen = set()
    for item in out:
        if item in seen:
            continue
        seen.add(item)
        dedup.append(item)
    return dedup


async def connect_db() -> asyncpg.Connection:
    last_error: Exception | None = None
    for candidate in _candidate_db_urls(DB_URL):
        try:
            return await asyncpg.connect(candidate)
        except Exception as exc:
            last_error = exc
            continue

    if last_error is not None:
        raise last_error
    raise RuntimeError("Unable to establish database connection")

DEFAULT_SOURCES = [
    ("merged", Path("/opt/autosparefinder/merged_all_final-1.xlsx"), "manufacturer-sheet"),
    ("normalized", Path("/opt/autosparefinder/backend/data/parts_database.normalized.xlsx"), "parts_catalog_import"),
    ("full", Path("/opt/autosparefinder/backend/data/full car database.xlsx"), "manufacturer-sheet"),
    ("parts", Path("/opt/autosparefinder/backend/data/parts_database.xlsx"), "manufacturer-sheet"),
]

SOURCE_PRIORITY = {"merged": 0, "normalized": 1, "full": 2, "parts": 3}


@dataclass
class SourceRow:
    key: str
    name: str | None
    category: str | None
    base_price: float | None
    source_name: str
    source_file: str


def clean(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    if not s or s.lower() in {"nan", "none"}:
        return None
    return s


def normalize_key(value: Any) -> str | None:
    s = clean(value)
    if not s:
        return None
    s = s.upper()
    s = re.sub(r"\s+", "", s)
    s = re.sub(r"^ORA-", "", s)
    if re.fullmatch(r"[0-9]+\.0+", s):
        s = s.split(".", 1)[0]
    return s or None


def normalize_name(value: Any) -> str | None:
    s = clean(value)
    if not s:
        return None
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def parse_price(value: Any) -> float | None:
    s = clean(value)
    if not s:
        return None
    s = s.replace("₪", "").replace(",", "")
    try:
        num = float(s)
        if num < 0:
            return None
        return round(num, 2)
    except (TypeError, ValueError):
        return None


def pick_column(df: pd.DataFrame, exact_names: list[str], contains_tokens: list[str]) -> str | None:
    lowered = {str(c).strip().lower(): c for c in df.columns}
    for name in exact_names:
        col = lowered.get(name.lower())
        if col is not None:
            return col

    for c in df.columns:
        ctext = str(c).strip().lower()
        if any(token.lower() in ctext for token in contains_tokens):
            return c

    return None


def resolve_sheet(xls: pd.ExcelFile, manufacturer: str, mode: str) -> str | None:
    if mode == "parts_catalog_import":
        return "parts_catalog_import" if "parts_catalog_import" in xls.sheet_names else None

    target_candidates = [manufacturer, manufacturer.upper(), manufacturer.title()]
    by_lower = {name.lower(): name for name in xls.sheet_names}
    for candidate in target_candidates:
        hit = by_lower.get(candidate.lower())
        if hit:
            return hit
    return None


def load_source_rows(manufacturer: str) -> tuple[dict[str, SourceRow], dict[str, int]]:
    merged: dict[str, SourceRow] = {}
    stats: dict[str, int] = {}

    for source_name, source_path, source_mode in DEFAULT_SOURCES:
        if not source_path.exists():
            stats[f"{source_name}_rows"] = 0
            stats[f"{source_name}_keys"] = 0
            continue

        xls = pd.ExcelFile(source_path)
        sheet_name = resolve_sheet(xls, manufacturer, source_mode)
        if not sheet_name:
            stats[f"{source_name}_rows"] = 0
            stats[f"{source_name}_keys"] = 0
            continue

        df = pd.read_excel(source_path, sheet_name=sheet_name)

        mfr_col = pick_column(
            df,
            exact_names=["manufacturer", "brand", "make", "יצרן", "מותג"],
            contains_tokens=["manufact", "brand", "יצרן", "מותג"],
        )
        if sheet_name == "parts_catalog_import" and mfr_col is not None:
            df = df[df[mfr_col].astype(str).str.strip().str.lower() == manufacturer.strip().lower()]

        key_col = pick_column(
            df,
            exact_names=["oem_number", "catalog_num", "מספר קטלוגי", "מק\"ט", "מקט"],
            contains_tokens=["oem", "catalog", "קטלוג", "מק"],
        )
        if key_col is None:
            stats[f"{source_name}_rows"] = int(len(df))
            stats[f"{source_name}_keys"] = 0
            continue

        name_col = pick_column(
            df,
            exact_names=["name", "description", "תיאור החלק"],
            contains_tokens=["name", "description", "תיאור"],
        )
        category_col = pick_column(
            df,
            exact_names=["category", "קטגוריה"],
            contains_tokens=["category", "קטגור"],
        )
        price_col = pick_column(
            df,
            exact_names=["base_price", "price", "מחיר"],
            contains_tokens=["base_price", "price", "מחיר"],
        )

        seen = set()
        valid_rows = 0
        for _, row in df.iterrows():
            key = normalize_key(row.get(key_col))
            if not key:
                continue

            valid_rows += 1
            seen.add(key)

            candidate = SourceRow(
                key=key,
                name=normalize_name(row.get(name_col)) if name_col else None,
                category=clean(row.get(category_col)) if category_col else None,
                base_price=parse_price(row.get(price_col)) if price_col else None,
                source_name=source_name,
                source_file=str(source_path),
            )

            existing = merged.get(key)
            if existing is None:
                merged[key] = candidate
                continue

            if SOURCE_PRIORITY[source_name] < SOURCE_PRIORITY[existing.source_name]:
                merged[key] = candidate

        stats[f"{source_name}_rows"] = valid_rows
        stats[f"{source_name}_keys"] = len(seen)

    return merged, stats


def parse_affected(result: str) -> int:
    # asyncpg execute returns strings like: "UPDATE 42"
    chunks = (result or "").split()
    if chunks and chunks[-1].isdigit():
        return int(chunks[-1])
    return 0


async def fetch_db_keys(conn: asyncpg.Connection, manufacturer: str) -> dict[str, dict[str, Any]]:
    rows = await conn.fetch(
        """
        SELECT id, sku, oem_number, name, category, part_type, description, is_active
        FROM parts_catalog
        WHERE LOWER(manufacturer) = LOWER($1)
        """,
        manufacturer,
    )

    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = normalize_key(row["oem_number"])
        if not key:
            continue
        out[key] = dict(row)
    return out


async def insert_missing_keys(
    conn: asyncpg.Connection,
    manufacturer: str,
    source_rows: dict[str, SourceRow],
    missing_keys: list[str],
    dry_run: bool,
) -> tuple[int, list[str]]:
    if not missing_keys:
        return 0, []

    existing_skus_rows = await conn.fetch(
        "SELECT sku FROM parts_catalog WHERE LOWER(manufacturer)=LOWER($1)",
        manufacturer,
    )
    existing_skus = {str(r["sku"]).upper() for r in existing_skus_rows if r["sku"]}

    to_insert = []
    inserted_keys = []
    prefix = re.sub(r"[^A-Z0-9]", "", manufacturer.upper())[:4] or "PART"

    for key in missing_keys:
        src = source_rows[key]
        base_sku = f"{prefix}-{key}"
        sku = base_sku
        suffix = 1
        while sku.upper() in existing_skus:
            suffix += 1
            sku = f"{prefix}-SRC-{key}-{suffix}"

        existing_skus.add(sku.upper())

        name = src.name or f"{manufacturer.upper()} PART {key}"
        category = src.category or guess_category_by_text(f"{name} {manufacturer}") or "general"
        base_price = src.base_price if src.base_price is not None else 0.0

        to_insert.append(
            (
                uuid.uuid4(),
                sku[:100],
                name[:255],
                category[:100],
                manufacturer,
                "OEM",
                name[:500],
                json.dumps(
                    {
                        "source": "repair_manufacturer_import",
                        "source_file": src.source_file,
                        "reconciled_at": datetime.utcnow().isoformat(),
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    [
                        {
                            "manufacturer": manufacturer.upper(),
                            "model": "All Models",
                            "year_from": 2020,
                            "year_to": datetime.utcnow().year,
                            "source": "fallback_full_coverage",
                        }
                    ],
                    ensure_ascii=False,
                ),
                key,
                float(base_price),
            )
        )
        inserted_keys.append(key)

    if dry_run:
        return len(to_insert), inserted_keys

    await conn.executemany(
        """
        INSERT INTO parts_catalog
            (id, sku, name, category, manufacturer, part_type,
             description, specifications, compatible_vehicles, oem_number,
             base_price, part_condition, is_safety_critical, needs_oem_lookup,
             master_enriched, is_active, created_at, updated_at)
        VALUES
            ($1, $2, $3, $4, $5, $6,
             $7, $8::jsonb, $9::jsonb, $10,
             $11, 'new', false, false,
             false, true, NOW(), NOW())
        ON CONFLICT (sku) DO NOTHING
        """,
        to_insert,
    )

    return len(to_insert), inserted_keys


async def apply_quality_fixes(conn: asyncpg.Connection, manufacturer: str, dry_run: bool) -> dict[str, int]:
    metrics = {
        "activated": 0,
        "needs_oem_cleared": 0,
        "names_fixed": 0,
        "categories_fixed": 0,
    }

    if not dry_run:
        activated = await conn.execute(
            """
            UPDATE parts_catalog
            SET is_active = TRUE,
                updated_at = NOW()
            WHERE LOWER(manufacturer)=LOWER($1)
              AND is_active = FALSE
            """,
            manufacturer,
        )
        metrics["activated"] = parse_affected(activated)

        needs_cleared = await conn.execute(
            """
            UPDATE parts_catalog
            SET needs_oem_lookup = FALSE,
                updated_at = NOW()
            WHERE LOWER(manufacturer)=LOWER($1)
              AND needs_oem_lookup = TRUE
              AND oem_number IS NOT NULL
              AND BTRIM(oem_number) <> ''
            """,
            manufacturer,
        )
        metrics["needs_oem_cleared"] = parse_affected(needs_cleared)

        names_fixed = await conn.execute(
            """
            UPDATE parts_catalog
            SET name = CONCAT(
                    UPPER($1),
                    ' PART ',
                    COALESCE(NULLIF(BTRIM(oem_number), ''), NULLIF(BTRIM(sku), ''), SUBSTRING(id::text, 1, 8))
                ),
                updated_at = NOW()
            WHERE LOWER(manufacturer)=LOWER($1)
              AND (name IS NULL OR BTRIM(name)='' OR BTRIM(name)='ללא שם' OR BTRIM(name)='(ללא שם)')
            """,
            manufacturer,
        )
        metrics["names_fixed"] = parse_affected(names_fixed)

    rows = await conn.fetch(
        """
        SELECT id, name, part_type, description
        FROM parts_catalog
        WHERE LOWER(manufacturer)=LOWER($1)
          AND (category IS NULL OR BTRIM(category)='')
        """,
        manufacturer,
    )

    updates = []
    for row in rows:
        blob = " ".join(
            [
                clean(row["name"]) or "",
                clean(row["part_type"]) or "",
                clean(row["description"]) or "",
            ]
        )
        guessed = guess_category_by_text(blob) or "general"
        updates.append((guessed[:100], row["id"]))

    metrics["categories_fixed"] = len(updates)
    if updates and not dry_run:
        await conn.executemany(
            "UPDATE parts_catalog SET category=$1, updated_at=NOW() WHERE id=$2",
            updates,
        )

    return metrics


async def connectivity_stats(conn: asyncpg.Connection, manufacturer: str) -> dict[str, int]:
    row = await conn.fetchrow(
        """
        WITH x AS (
            SELECT pc.id,
                   COALESCE(BOOL_OR(s.id IS NOT NULL), FALSE) AS has_supplier
            FROM parts_catalog pc
            LEFT JOIN supplier_parts sp ON sp.part_id = pc.id
            LEFT JOIN suppliers s ON s.id = sp.supplier_id
            WHERE LOWER(pc.manufacturer)=LOWER($1)
            GROUP BY pc.id
        )
        SELECT
            COUNT(*)::int AS total,
            COUNT(*) FILTER (WHERE has_supplier)::int AS with_supplier,
            COUNT(*) FILTER (WHERE NOT has_supplier)::int AS no_supplier
        FROM x
        """,
        manufacturer,
    )
    return dict(row)


async def run(manufacturer: str, apply_changes: bool, dry_run: bool) -> None:
    manufacturer = manufacturer.strip()
    source_rows, source_stats = load_source_rows(manufacturer)
    source_keys = set(source_rows.keys())

    conn = await connect_db()
    db_map_before = await fetch_db_keys(conn, manufacturer)
    db_keys_before = set(db_map_before.keys())

    missing_keys = sorted(source_keys - db_keys_before)
    extra_keys = sorted(db_keys_before - source_keys)

    should_apply = apply_changes and not dry_run
    inserted_count, inserted_keys = await insert_missing_keys(
        conn,
        manufacturer,
        source_rows,
        missing_keys,
        dry_run=not should_apply,
    )

    quality = await apply_quality_fixes(conn, manufacturer, dry_run=not should_apply)

    db_map_after = await fetch_db_keys(conn, manufacturer)
    db_keys_after = set(db_map_after.keys())

    dup_row = await conn.fetchrow(
        """
        SELECT COUNT(*)::int AS dup_norm_oem_groups
        FROM (
            SELECT UPPER(REGEXP_REPLACE(REGEXP_REPLACE(BTRIM(oem_number), '^ORA-', ''), '\\s+', '', 'g')) k,
                   COUNT(*) c
            FROM parts_catalog
            WHERE LOWER(manufacturer)=LOWER($1)
              AND oem_number IS NOT NULL
              AND BTRIM(oem_number) <> ''
            GROUP BY 1
            HAVING COUNT(*) > 1
        ) t
        """,
        manufacturer,
    )

    conn_stats = await connectivity_stats(conn, manufacturer)
    await conn.close()

    summary = {
        "manufacturer": manufacturer,
        "apply": bool(should_apply),
        "source": {
            "distinct_keys": len(source_keys),
            "stats": source_stats,
        },
        "db_before": {
            "distinct_keys": len(db_keys_before),
            "missing_from_db": len(missing_keys),
            "extra_in_db": len(extra_keys),
        },
        "changes": {
            "inserted_missing_keys": inserted_count,
            "inserted_key_sample": inserted_keys[:30],
            "quality": quality,
        },
        "db_after": {
            "distinct_keys": len(db_keys_after),
            "missing_from_db": len(source_keys - db_keys_after),
            "extra_in_db": len(db_keys_after - source_keys),
            "dup_norm_oem_groups": int(dup_row["dup_norm_oem_groups"]),
        },
        "connectivity": conn_stats,
    }

    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Repair manufacturer import quality and source parity")
    parser.add_argument("manufacturer", type=str, help="Manufacturer name, e.g. ORA")
    parser.add_argument("--apply", action="store_true", help="Apply changes to DB")
    parser.add_argument("--dry-run", action="store_true", help="Compute and report only")
    args = parser.parse_args()

    asyncio.run(run(args.manufacturer, apply_changes=args.apply, dry_run=args.dry_run))
