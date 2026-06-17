"""
ebay_fitment_backfill.py — Enrich parts_catalog with OEM cross-references and fitment
data extracted from eBay item specifics.

For each active part that has an eBay supplier_parts entry:
1. Calls eBay get_part_details(supplier_sku) to fetch itemSpecifics
2. Extracts "OE/OEM Reference Number", "Compatible Vehicles", Make/Model/Year
3. Writes cross-reference OEM numbers back to parts_catalog (if missing)
4. Creates part_vehicle_fitment rows for each compatible vehicle

Rate-limited by the eBay supplier's built-in semaphore + circuit breaker.
Designed to run as a background batch job; safe to interrupt and restart.

Usage:
    python ebay_fitment_backfill.py [--dry-run] [--limit N] [--offset N]
"""
import argparse
import asyncio
import json
import logging
import re
import time
import uuid
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

import os

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://autospare:autospare@autospare_postgres_catalog:5432/autospare",
)

# eBay item specifics keys that carry OEM / cross-reference numbers
OEM_SPEC_KEYS = {
    "oe part number",
    "reference oe/oem number",
    "oem part number",
    "manufacturer part number",
    "mpn",
    "part number",
    "cross reference",
    "interchange part number",
    "replaces part number",
    "superseded part number",
    "oe number",
    "oem number",
}

# Keys that carry fitment data
MAKE_KEYS = {"make", "vehicle make", "car make", "brand (vehicle)", "compatible vehicle make"}
MODEL_KEYS = {"model", "vehicle model", "car model", "compatible vehicle model"}
YEAR_KEYS = {"year", "vehicle year", "year range", "compatible year range", "years"}
ENGINE_KEYS = {"engine", "engine size", "engine type", "engine code"}
TRANSMISSION_KEYS = {"transmission", "transmission type"}

# eBay "Compatible Vehicles" — often a multi-value field with "Make Year Model" strings
COMPAT_KEYS = {"compatible vehicles", "fits", "application", "fitment", "vehicle"}


def _norm_key(k: str) -> str:
    return k.lower().strip()


def _extract_years(raw: str) -> tuple[Optional[int], Optional[int]]:
    """Extract year_from, year_to from a string like '2010-2018' or '2015'."""
    raw = raw.strip()
    m = re.search(r"(19|20)(\d{2})\s*[-–to]+\s*(19|20)(\d{2})", raw)
    if m:
        return int(m.group(1) + m.group(2)), int(m.group(3) + m.group(4))
    m = re.search(r"\b(19|20)(\d{2})\b", raw)
    if m:
        yr = int(m.group(1) + m.group(2))
        return yr, yr
    return None, None


def _parse_compat_vehicle_string(raw: str) -> list[dict]:
    """
    Parse a 'Compatible Vehicles' string like:
      "2010-2018 BMW 3 Series, 2015-2020 Audi A4"
    Returns list of {manufacturer, model, year_from, year_to}.
    """
    results = []
    # Split on semicolons or newlines
    entries = re.split(r"[;\n|]+", raw)
    for entry in entries:
        entry = entry.strip()
        if not entry:
            continue
        year_from, year_to = _extract_years(entry)
        # Remove the year portion to get make/model
        clean = re.sub(r"\b(19|20)\d{2}\b\s*[-–to]*\s*(19|20)?\d{0,2}", "", entry).strip()
        clean = re.sub(r"\s+", " ", clean).strip().strip(",").strip()
        if not clean:
            continue
        parts = clean.split(None, 1)
        make = parts[0] if parts else None
        model = parts[1] if len(parts) > 1 else None
        if make:
            results.append({
                "manufacturer": make,
                "model": model or "",
                "year_from": year_from,
                "year_to": year_to,
            })
    return results


def _extract_fitment_from_specs(specs: dict) -> tuple[list[str], list[dict]]:
    """
    Returns:
        oem_refs: list of OEM cross-reference numbers
        fitment_rows: list of fitment dicts {manufacturer, model, year_from, year_to, engine_type, transmission}
    """
    oem_refs: list[str] = []
    fitment_rows: list[dict] = []

    # Normalize all keys for lookup
    normalized = {_norm_key(k): v for k, v in specs.items()}

    # --- Extract OEM cross-references ---
    for key in OEM_SPEC_KEYS:
        val = normalized.get(key, "")
        if not val:
            continue
        # May be comma-separated list
        for part in re.split(r"[,;/\n]+", str(val)):
            ref = part.strip().upper().replace(" ", "")
            if ref and len(ref) >= 5:
                oem_refs.append(ref)

    # --- Try structured make/model/year ---
    make = next((normalized[k] for k in MAKE_KEYS if k in normalized), None)
    model = next((normalized[k] for k in MODEL_KEYS if k in normalized), None)
    year_raw = next((normalized[k] for k in YEAR_KEYS if k in normalized), None)
    engine = next((normalized[k] for k in ENGINE_KEYS if k in normalized), None)
    transmission = next((normalized[k] for k in TRANSMISSION_KEYS if k in normalized), None)

    if make:
        year_from, year_to = _extract_years(str(year_raw or ""))
        fitment_rows.append({
            "manufacturer": str(make).strip(),
            "model": str(model or "").strip(),
            "year_from": year_from,
            "year_to": year_to,
            "engine_type": str(engine or "").strip() or None,
            "transmission": str(transmission or "").strip() or None,
        })

    # --- Try "Compatible Vehicles" multi-string ---
    for key in COMPAT_KEYS:
        val = normalized.get(key, "")
        if not val:
            continue
        parsed = _parse_compat_vehicle_string(str(val))
        for row in parsed:
            row["engine_type"] = str(engine or "").strip() or None
            row["transmission"] = str(transmission or "").strip() or None
            fitment_rows.append(row)
        if parsed:
            break  # found a compatible vehicles field, stop

    # Dedupe fitment rows
    seen: set[tuple] = set()
    deduped: list[dict] = []
    for row in fitment_rows:
        key = (row["manufacturer"], row["model"], row.get("year_from"), row.get("year_to"))
        if key not in seen and row["manufacturer"]:
            seen.add(key)
            deduped.append(row)

    return list(dict.fromkeys(oem_refs)), deduped  # dedupe OEM refs too


async def _write_fitment(
    db: AsyncSession,
    part_id: str,
    fitment_rows: list[dict],
) -> int:
    """Insert fitment rows, skip duplicates. Returns count inserted."""
    inserted = 0
    for row in fitment_rows:
        mfr = row.get("manufacturer", "").strip()
        model = row.get("model", "").strip()
        if not mfr:
            continue
        # Check for existing
        existing = await db.execute(
            text(
                "SELECT id FROM part_vehicle_fitment "
                "WHERE part_id = CAST(:part_id AS uuid) "
                "AND manufacturer = :mfr AND model = :model "
                "AND COALESCE(year_from, 0) = :year_from AND COALESCE(year_to, 0) = :year_to"
            ),
            {
                "part_id": part_id,
                "mfr": mfr,
                "model": model,
                "year_from": row.get("year_from") or 0,
                "year_to": row.get("year_to") or 0,
            },
        )
        if existing.fetchone():
            continue
        await db.execute(
            text(
                "INSERT INTO part_vehicle_fitment "
                "(id, part_id, manufacturer, model, year_from, year_to, engine_type, transmission, notes, created_at, updated_at) "
                "VALUES (gen_random_uuid(), CAST(:part_id AS uuid), :mfr, :model, :year_from, :year_to, "
                ":engine_type, :transmission, :notes, NOW(), NOW())"
            ),
            {
                "part_id": part_id,
                "mfr": mfr,
                "model": model,
                "year_from": row.get("year_from") or 0,
                "year_to": row.get("year_to") or 0,
                "engine_type": row.get("engine_type"),
                "transmission": row.get("transmission"),
                "notes": "source:ebay_item_specifics",
            },
        )
        inserted += 1
    return inserted


async def run_backfill(
    dry_run: bool = False,
    limit: int = 500,
    offset: int = 0,
) -> dict:
    from services.suppliers.ebay_supplier import EbaySupplier

    engine = create_async_engine(DATABASE_URL, echo=False)
    Session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    ebay = EbaySupplier()
    t0 = time.monotonic()
    scanned = oem_updated = fitment_added = api_errors = skipped = 0

    async with Session() as db:
        # Fetch eBay supplier_parts: include supplier_url so we can extract real item IDs
        result = await db.execute(
            text(
                """
                SELECT DISTINCT ON (sp.part_id)
                    sp.part_id, sp.supplier_sku, sp.supplier_url,
                    pc.oem_number, pc.manufacturer
                FROM supplier_parts sp
                JOIN suppliers s ON sp.supplier_id = s.id
                JOIN parts_catalog pc ON sp.part_id = pc.id
                WHERE s.name ILIKE '%ebay%'
                  AND sp.supplier_url IS NOT NULL
                  AND sp.supplier_url LIKE '%ebay.com/itm/%'
                  AND pc.is_active = TRUE
                ORDER BY sp.part_id, sp.last_checked_at DESC NULLS LAST
                LIMIT :limit OFFSET :offset
                """
            ),
            {"limit": limit, "offset": offset},
        )
        rows = result.fetchall()
        scanned = len(rows)
        logger.info("Processing %d eBay supplier parts (offset=%d)", scanned, offset)

    # Regex to extract numeric eBay item ID from URL
    _EBAY_ITEM_ID_RE = re.compile(r"/itm/([0-9]+)")

    for row in rows:
        part_id = str(row.part_id)
        supplier_sku = row.supplier_sku
        supplier_url = row.supplier_url or ""
        existing_oem = row.oem_number
        manufacturer = row.manufacturer or ""

        # Extract real eBay item ID from URL (e.g. /itm/175466110596 → v1|175466110596|0)
        m = _EBAY_ITEM_ID_RE.search(supplier_url)
        if not m:
            skipped += 1
            continue
        ebay_item_id = f"v1|{m.group(1)}|0"

        # Call eBay for full item specifics
        try:
            detail = await ebay.get_part_details(ebay_item_id)
        except Exception as exc:
            logger.warning("eBay get_part_details(%s) error: %s", ebay_item_id, exc)
            api_errors += 1
            continue

        if not detail or not detail.tech_specs:
            skipped += 1
            continue

        specs = detail.tech_specs or {}
        oem_refs, fitment_rows = _extract_fitment_from_specs(specs)

        if not oem_refs and not fitment_rows:
            skipped += 1
            continue

        async with Session() as db:
            try:
                # Write OEM cross-references (only if part currently has no OEM)
                if oem_refs and not existing_oem:
                    primary_oem = oem_refs[0]
                    if not dry_run:
                        await db.execute(
                            text(
                                "UPDATE parts_catalog SET oem_number = :oem, updated_at = NOW() "
                                "WHERE id = CAST(:part_id AS uuid) AND oem_number IS NULL"
                            ),
                            {"part_id": part_id, "oem": primary_oem},
                        )
                    oem_updated += 1
                    logger.debug("OEM %s → part %s", primary_oem, part_id)

                # Write fitment rows
                if fitment_rows:
                    if not dry_run:
                        inserted = await _write_fitment(db, part_id, fitment_rows)
                        fitment_added += inserted
                    else:
                        fitment_added += len(fitment_rows)
                        logger.debug(
                            "[DRY RUN] Would add %d fitment rows for part %s: %s",
                            len(fitment_rows), part_id,
                            [(r["manufacturer"], r["model"]) for r in fitment_rows[:3]],
                        )

                if not dry_run:
                    await db.commit()

            except Exception as exc:
                if not dry_run:
                    await db.rollback()
                logger.error("DB write error for part %s: %s", part_id, exc)
                api_errors += 1

        # Small pause to stay within rate limits
        await asyncio.sleep(0.1)

    await engine.dispose()

    elapsed = round(time.monotonic() - t0, 2)
    report = {
        "task": "ebay_fitment_backfill",
        "status": "ok" if api_errors == 0 else "partial",
        "dry_run": dry_run,
        "scanned": scanned,
        "oem_updated": oem_updated,
        "fitment_added": fitment_added,
        "skipped": skipped,
        "api_errors": api_errors,
        "elapsed_s": elapsed,
    }
    logger.info("Result: %s", json.dumps(report))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=500, help="Parts to process per run")
    parser.add_argument("--offset", type=int, default=0, help="Pagination offset")
    args = parser.parse_args()

    result = asyncio.run(run_backfill(dry_run=args.dry_run, limit=args.limit, offset=args.offset))
    print(json.dumps(result, indent=2))
