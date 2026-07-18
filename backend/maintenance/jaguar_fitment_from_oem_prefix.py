"""
jaguar_fitment_from_oem_prefix.py — Infer Jaguar model fitment from OEM part number prefix.

For the ~95K SNG Barratt Jaguar parts that have no fitment data (SNG didn't publish
vehicle applications for them), we derive a prefix→[models] map from the 155K parts
that DO have fitment, then apply it to fill in the gaps.

Strategy:
- Prefixes that map predominantly (≥70%) to ONE model → assign that model only.
- Prefixes that span MULTIPLE models (cross-series parts like JLM, EAC, C2C) →
  assign ALL models that have ≥5 known parts with that prefix. These are genuinely
  universal Jaguar parts that fit across the range.

Usage:
    python jaguar_fitment_from_oem_prefix.py [--dry-run] [--min-confidence 70] [--min-model-count 5]
"""
import argparse
import asyncio
import logging
import os
import time
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://autospare:autospare@autospare_postgres_catalog:5432/autospare",
)

JAGUAR_BRAND_ID_QUERY = "SELECT id FROM car_brands WHERE LOWER(name) = 'jaguar' LIMIT 1"


async def build_prefix_model_map(db: AsyncSession, min_confidence: float, min_model_count: int) -> dict[str, list[dict]]:
    """
    Returns prefix → list of {model, year_from, year_to} dicts.

    - If one model dominates (≥ min_confidence %), only that model is returned.
    - Otherwise ALL models with ≥ min_model_count known parts for that prefix are
      returned (cross-series universal parts like JLM, EAC, C2C).
    """
    result = await db.execute(text("""
        SELECT
            UPPER(SUBSTRING(pc.oem_number, 1, 3)) AS prefix,
            pvf.model,
            pvf.year_from,
            pvf.year_to,
            COUNT(*) AS cnt,
            SUM(COUNT(*)) OVER (PARTITION BY UPPER(SUBSTRING(pc.oem_number, 1, 3))) AS prefix_total
        FROM parts_catalog pc
        JOIN part_vehicle_fitment pvf ON pvf.part_id = pc.id
        WHERE pc.manufacturer = 'Jaguar'
          AND pc.oem_number IS NOT NULL
          AND pvf.notes = 'SNG Barratt catalogue'
          AND pvf.manufacturer = 'Jaguar'
        GROUP BY prefix, pvf.model, pvf.year_from, pvf.year_to
    """))
    rows = result.fetchall()

    # Group by prefix
    from collections import defaultdict
    prefix_data: dict[str, list] = defaultdict(list)
    prefix_totals: dict[str, int] = {}
    for row in rows:
        prefix_data[row.prefix].append(row)
        prefix_totals[row.prefix] = row.prefix_total

    prefix_map: dict[str, list[dict]] = {}
    for prefix, entries in prefix_data.items():
        total = prefix_totals[prefix]
        # Check if any single model dominates
        dominant = max(entries, key=lambda r: r.cnt)
        if (dominant.cnt / total * 100) >= min_confidence:
            # Single dominant model
            prefix_map[prefix] = [{
                "model": dominant.model,
                "year_from": dominant.year_from,
                "year_to": dominant.year_to,
            }]
        else:
            # Multi-model prefix — assign all models with ≥ min_model_count parts
            prefix_map[prefix] = [
                {"model": r.model, "year_from": r.year_from, "year_to": r.year_to}
                for r in entries if r.cnt >= min_model_count
            ]

    usable = sum(1 for v in prefix_map.values() if v)
    multi = sum(1 for v in prefix_map.values() if len(v) > 1)
    logger.info(
        "Built prefix→model map: %d usable prefixes (%d single-model, %d multi-model)",
        usable, usable - multi, multi,
    )
    return prefix_map


async def run_backfill(dry_run: bool = False, min_confidence: float = 70.0, min_model_count: int = 5, batch_size: int = 500) -> dict:
    engine = create_async_engine(DATABASE_URL, echo=False)
    Session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    t0 = time.monotonic()
    scanned = inserted = skipped_no_prefix = 0

    async with Session() as db:
        # Step 1: build the prefix map
        prefix_map = await build_prefix_model_map(db, min_confidence, min_model_count)

        # Step 2: get Jaguar brand ID
        brand_row = await db.execute(text(JAGUAR_BRAND_ID_QUERY))
        brand_id_row = brand_row.fetchone()
        jaguar_brand_id = str(brand_id_row[0]) if brand_id_row else None

        # Step 3: fetch all SNG Jaguar parts missing fitment
        result = await db.execute(text("""
            SELECT pc.id AS part_id, pc.oem_number
            FROM supplier_parts sp
            JOIN suppliers s ON sp.supplier_id = s.id
            JOIN parts_catalog pc ON sp.part_id = pc.id
            WHERE s.name ILIKE '%sng%'
              AND pc.is_active = TRUE
              AND pc.oem_number IS NOT NULL
              AND pc.manufacturer = 'Jaguar'
              AND NOT EXISTS (
                  SELECT 1 FROM part_vehicle_fitment pvf WHERE pvf.part_id = pc.id
              )
        """))
        parts = result.fetchall()
        scanned = len(parts)
        logger.info("Found %d SNG Jaguar parts without fitment", scanned)

    # Step 4: build all rows to insert
    all_rows: list[dict] = []
    dry_samples = 0
    for part in parts:
        part_id = str(part.part_id)
        oem = (part.oem_number or "").strip().upper()
        if not oem:
            skipped_no_prefix += 1
            continue

        prefix3 = oem[:3]
        models = prefix_map.get(prefix3)
        if not models:
            skipped_no_prefix += 1
            continue

        if dry_run:
            inserted += len(models)
            if dry_samples < 3:
                logger.info(
                    "[DRY RUN] %s → prefix=%s → %d models: %s",
                    oem, prefix3, len(models),
                    [m["model"] for m in models[:3]],
                )
                dry_samples += 1
            continue

        for m in models:
            all_rows.append({
                "part_id": part_id,
                "brand_id": jaguar_brand_id,
                "model": m["model"],
                "year_from": m["year_from"],
                "year_to": m["year_to"],
            })

    # Step 5: flush in batches
    if not dry_run:
        for i in range(0, len(all_rows), batch_size):
            chunk = all_rows[i:i + batch_size]
            async with Session() as db:
                async with db.begin():
                    for row in chunk:
                        await db.execute(text("""
                            INSERT INTO part_vehicle_fitment
                                (id, part_id, manufacturer, manufacturer_id,
                                 model, year_from, year_to, notes, created_at, updated_at)
                            VALUES
                                (gen_random_uuid(), CAST(:part_id AS uuid), 'Jaguar', CAST(:brand_id AS uuid),
                                 :model, :year_from, :year_to,
                                 'SNG Barratt catalogue (inferred from OEM prefix)',
                                 NOW(), NOW())
                            ON CONFLICT (part_id, manufacturer, model, year_from) DO NOTHING
                        """), row)
            inserted += len(chunk)
            logger.info("  flushed batch — inserted so far: %d / %d", inserted, len(all_rows))

    elapsed = round(time.monotonic() - t0, 2)
    report = {
        "task": "jaguar_fitment_from_oem_prefix",
        "status": "ok",
        "dry_run": dry_run,
        "min_confidence_pct": min_confidence,
        "min_model_count": min_model_count,
        "scanned": scanned,
        "inserted": inserted,
        "skipped_no_match": skipped_no_prefix,
        "elapsed_s": elapsed,
    }
    logger.info("Result: %s", report)
    import json
    print(json.dumps(report, indent=2))
    return report


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--min-confidence", type=float, default=70.0,
                    help="Minimum %% for single-model prefix (default 70)")
    ap.add_argument("--min-model-count", type=int, default=5,
                    help="Minimum known parts for a model in multi-model prefix (default 5)")
    args = ap.parse_args()
    asyncio.run(run_backfill(dry_run=args.dry_run, min_confidence=args.min_confidence, min_model_count=args.min_model_count))
