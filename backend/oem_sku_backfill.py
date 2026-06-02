"""
oem_sku_backfill.py — Promote SKU → oem_number for parts where oem_number IS NULL
but the SKU already encodes the OEM part number (common for Renault, Kia, BMW, etc.).

Also extracts OEM numbers embedded in part names via manufacturer-specific regex patterns.

Usage:
    python oem_sku_backfill.py [--dry-run] [--manufacturer BRAND]

Returns standard job report JSON.
"""
import argparse
import asyncio
import json
import logging
import re
import time
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

# ---------------------------------------------------------------------------
# Per-manufacturer OEM validation patterns
# Format: (regex that must match the SKU to be promoted as OEM, strip_prefix)
# strip_prefix: prefix to remove from SKU before setting as oem_number (or None)
# ---------------------------------------------------------------------------
MANUFACTURER_OEM_RULES: list[tuple[str, re.Pattern, Optional[str]]] = [
    # Renault: RE + digits/letters, 10-12 chars total
    ("Renault",    re.compile(r"^RE[0-9]{7,10}[A-Z0-9]?$"),       None),
    # Kia: alphanumeric 8-15, no dashes, not starting with KIA- or internal codes
    ("Kia",        re.compile(r"^[0-9A-Z]{7,14}$"),               None),
    # Kia with KIA- prefix — strip it
    ("Kia",        re.compile(r"^KIA-([0-9A-Z]{7,14})$"),         "KIA-"),
    # Hyundai: same pattern as Kia (shared platform parts)
    ("Hyundai",    re.compile(r"^[0-9A-Z]{7,14}$"),               None),
    # BMW: 11 digits or alphanumeric like 34116775023
    ("BMW",        re.compile(r"^[0-9]{10,11}$"),                 None),
    ("BMW",        re.compile(r"^[0-9]{2}[0-9A-Z]{2,4}[0-9]{5,7}$"), None),
    # Mercedes: A + 10-13 alphanumeric
    ("Mercedes-Benz", re.compile(r"^A[0-9]{9,13}$"),             None),
    ("Mercedes-Benz", re.compile(r"^[0-9]{9,13}$"),              None),
    # Toyota: 5-char dash 4-5-char (e.g. 48530-0D090) or pure alphanumeric 8-12
    ("Toyota",     re.compile(r"^[0-9A-Z]{4,6}-[0-9A-Z]{4,6}$"), None),
    ("Toyota",     re.compile(r"^[0-9A-Z]{8,12}$"),              None),
    # Ford: 2-letter + 2-letter/digit + 6+ digits + optional letter
    ("Ford",       re.compile(r"^[A-Z0-9]{2}[A-Z0-9]{2}[0-9]{6,8}[A-Z]?$"), None),
    # Nissan: alphanumeric 8-12, e.g. 40006JG00A
    ("Nissan",     re.compile(r"^[0-9A-Z]{8,12}$"),              None),
    # Chevrolet/GM: pure digits 7-11
    ("Chevrolet",  re.compile(r"^[0-9]{7,11}$"),                 None),
    ("GM",         re.compile(r"^[0-9]{7,11}$"),                 None),
    # Mitsubishi: alphanumeric 7-10 e.g. 4605A128
    ("Mitsubishi", re.compile(r"^[0-9A-Z]{6,12}$"),              None),
    # Peugeot/Citroën: digits 7-13
    ("Peugeot",    re.compile(r"^[0-9]{7,13}[A-Z]?$"),           None),
    ("Citroën",    re.compile(r"^[0-9]{7,13}[A-Z]?$"),           None),
    # Volkswagen/Audi/Skoda/Seat: 3-digit.3-digit.3-digit.1-letter format or compact
    ("Volkswagen", re.compile(r"^[0-9A-Z]{8,14}$"),              None),
    ("Audi",       re.compile(r"^[0-9A-Z]{8,14}$"),              None),
    # Fiat: digits 7-10
    ("Fiat",       re.compile(r"^[0-9]{7,10}$"),                 None),
    # Subaru: digits + alpha 8-12
    ("Subaru",     re.compile(r"^[0-9A-Z]{8,12}$"),              None),
    # Mazda: BP/GJ style, 8-12 alphanumeric
    ("Mazda",      re.compile(r"^[A-Z]{1,3}[0-9A-Z]{5,10}$"),   None),
    # Honda: 8-digit standard
    ("Honda",      re.compile(r"^[0-9]{8,10}$"),                 None),
]

# Internal / synthetic SKU prefixes — skip these entirely (NOT OEM numbers)
INTERNAL_SKU_PREFIXES = (
    "RENA-", "SUZU-", "SUZU_", "TOY-", "BMW-", "FORD-",
    "LR-", "JLR-", "SANDBOX-", "QA-", "TEST-",
)

# Strippable manufacturer prefixes that hide a real OEM number after the dash
# Format: (prefix_to_strip, manufacturer_name)
STRIPPABLE_OEM_PREFIXES: list[tuple[str, str]] = [
    ("KIA-",  "Kia"),
]

# For names: regex patterns to extract OEM from embedded text
# Format: (manufacturer_prefix_or_None, compiled_pattern, group_index)
NAME_EXTRACTION_PATTERNS: list[tuple[Optional[str], re.Pattern, int]] = [
    # "OEM: XXXXXXX" or "OEM# XXXXXXX"
    (None, re.compile(r"OEM[:\s#]+([A-Z0-9]{6,16})", re.IGNORECASE), 1),
    # "Part# XXXXXXX" or "Part No XXXXXXX"
    (None, re.compile(r"(?:Part\s*[#:No\.]+)\s*([A-Z0-9]{6,16})", re.IGNORECASE), 1),
    # "Ref[:\s]+XXXXXXX"
    (None, re.compile(r"Ref[:\s]+([A-Z0-9]{6,16})", re.IGNORECASE), 1),
    # Renault number: 7-10 digits sometimes starting with 77/82/86/28/17
    ("Renault", re.compile(r"\b((?:77|82|86|28|17|82|80|53|21|16|25)\d{8,10})\b"), 1),
    # BMW 11-digit
    ("BMW", re.compile(r"\b([0-9]{11})\b"), 1),
    # Kia/Hyundai 0K-style
    ("Kia",     re.compile(r"\b(0K[A-Z0-9]{6,12})\b"), 1),
    ("Hyundai", re.compile(r"\b(0K[A-Z0-9]{6,12})\b"), 1),
]


def _is_internal_sku(sku: str) -> bool:
    """Return True if the SKU is a synthetic internal reference, not an OEM number."""
    if not sku:
        return True
    sku_upper = sku.upper()
    for prefix in INTERNAL_SKU_PREFIXES:
        if sku_upper.startswith(prefix):
            return True
    # Multiple comma-separated values → internal aggregated key
    if "," in sku:
        return True
    # SKU with spaces → likely descriptive, not OEM
    if " " in sku.strip():
        return True
    return False


def _oem_from_sku(manufacturer: str, sku: str) -> Optional[str]:
    """Return a validated OEM number from the SKU, or None if not valid."""
    # Check strippable prefixes first (e.g. KIA-0K2A144410F → 0K2A144410F)
    for prefix, mfr_name in STRIPPABLE_OEM_PREFIXES:
        if mfr_name.lower() == manufacturer.lower() and sku.upper().startswith(prefix.upper()):
            candidate = sku[len(prefix):]
            result = _oem_from_sku(manufacturer, candidate)
            if result:
                return result
    if _is_internal_sku(sku):
        return None
    for mfr, pattern, strip_prefix in MANUFACTURER_OEM_RULES:
        if mfr.lower() != manufacturer.lower():
            continue
        m = pattern.match(sku)
        if m:
            if strip_prefix and sku.startswith(strip_prefix):
                return sku[len(strip_prefix):]
            # If pattern has a capture group, use group(1)
            try:
                return m.group(1)
            except IndexError:
                return sku
    return None


def _oem_from_name(manufacturer: str, name: str) -> Optional[str]:
    """Try to extract an OEM number from the part name field."""
    if not name:
        return None
    for mfr_filter, pattern, group in NAME_EXTRACTION_PATTERNS:
        if mfr_filter and mfr_filter.lower() != manufacturer.lower():
            continue
        m = pattern.search(name)
        if m:
            candidate = m.group(group).strip()
            # Basic sanity: 6–16 chars, alphanumeric only
            if 6 <= len(candidate) <= 16 and re.match(r"^[A-Z0-9]+$", candidate, re.IGNORECASE):
                return candidate
    return None


async def run_backfill(
    dry_run: bool = False,
    manufacturer_filter: Optional[str] = None,
    batch_size: int = 500,
) -> dict:
    engine = create_async_engine(DATABASE_URL, echo=False)
    Session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    t0 = time.monotonic()
    scanned = updated = skipped = errors = 0

    async with Session() as db:
        # Fetch all active parts with NULL oem_number
        where_clauses = ["oem_number IS NULL", "is_active = TRUE", "manufacturer IS NOT NULL"]
        params: dict = {}
        if manufacturer_filter:
            where_clauses.append("manufacturer = :mfr")
            params["mfr"] = manufacturer_filter

        result = await db.execute(
            text(
                f"SELECT id, manufacturer, sku, name FROM parts_catalog "
                f"WHERE {' AND '.join(where_clauses)} ORDER BY manufacturer, id"
            ),
            params,
        )
        rows = result.fetchall()
        scanned = len(rows)
        logger.info("Scanned %d parts with missing OEM number", scanned)

        # Process in batches
        batch_updates: list[dict] = []

        for row in rows:
            part_id, manufacturer, sku, name = row.id, row.manufacturer, row.sku, row.name
            oem: Optional[str] = None

            # Priority 1: validate SKU as OEM
            if sku:
                oem = _oem_from_sku(manufacturer, sku)

            # Priority 2: extract from name
            if not oem and name:
                oem = _oem_from_name(manufacturer, str(name))

            if not oem:
                skipped += 1
                continue

            batch_updates.append({"part_id": str(part_id), "oem": oem})

            if len(batch_updates) >= batch_size:
                if not dry_run:
                    try:
                        for update in batch_updates:
                            await db.execute(
                                text(
                                    "UPDATE parts_catalog SET oem_number = :oem, updated_at = NOW() "
                                    "WHERE id = CAST(:part_id AS uuid) AND oem_number IS NULL"
                                ),
                                update,
                            )
                        await db.commit()
                        updated += len(batch_updates)
                    except Exception as exc:
                        await db.rollback()
                        logger.error("Batch write error: %s", exc)
                        errors += len(batch_updates)
                else:
                    logger.info(
                        "[DRY RUN] Would update %d parts (sample: %s → %s)",
                        len(batch_updates),
                        batch_updates[0]["part_id"],
                        batch_updates[0]["oem"],
                    )
                    updated += len(batch_updates)
                batch_updates.clear()

        # Final partial batch
        if batch_updates:
            if not dry_run:
                try:
                    for update in batch_updates:
                        await db.execute(
                            text(
                                "UPDATE parts_catalog SET oem_number = :oem, updated_at = NOW() "
                                "WHERE id = CAST(:part_id AS uuid) AND oem_number IS NULL"
                            ),
                            update,
                        )
                    await db.commit()
                    updated += len(batch_updates)
                except Exception as exc:
                    await db.rollback()
                    logger.error("Final batch write error: %s", exc)
                    errors += len(batch_updates)
            else:
                updated += len(batch_updates)

    await engine.dispose()

    elapsed = round(time.monotonic() - t0, 2)
    report = {
        "task": "oem_sku_backfill",
        "status": "ok" if errors == 0 else "error",
        "dry_run": dry_run,
        "scanned": scanned,
        "updated": updated,
        "skipped": skipped,
        "errors": errors,
        "elapsed_s": elapsed,
    }
    logger.info("Result: %s", json.dumps(report))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    parser.add_argument("--manufacturer", default=None, help="Limit to one manufacturer")
    args = parser.parse_args()

    result = asyncio.run(run_backfill(dry_run=args.dry_run, manufacturer_filter=args.manufacturer))
    print(json.dumps(result, indent=2))
