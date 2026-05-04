"""
DB Update Agent — backend/db_update_agent.py

Runs a set of autonomous cleaning / normalisation tasks against the catalogue DB.
Each task is idempotent: re-running it is always safe.

Tasks
-----
1.  clean_part_names          – strip trailing car-model suffixes from part names
2.  normalize_part_types      – unify to "Original" / "OEM" / "Aftermarket"
3.  normalize_categories      – map variants to 14 canonical Hebrew categories
4.  normalize_availability    – unify to "in_stock" / "out_of_stock" / "on_order"
5.  fix_base_prices           – ensure base_price = supplier min + 18 % VAT markup
6.  flag_fake_skus            – set needs_oem_lookup=True for auto-generated SKUs
7.  fill_car_brands           – seed il_importer / warranty_* for known makes
8.  sync_manufacturer_registries – keep car/truck brand registries clean + logos
9.  run_all_tasks             – orchestrator that runs core tasks and returns a report dict

On-demand tasks (NOT in run_all_tasks — must be triggered explicitly):
9.  populate_supplier_parts   – link every active part to every active supplier
                                (ON CONFLICT DO NOTHING; safe to re-run)
10. validate_migrations       – pre-flight safety scan of all Alembic migration
                                files for patterns that can cause downtime

Admin endpoints call run_all_tasks or individual tasks through get_db.
run_agent_background_loop()  – optional periodic loop (disabled by default).
"""
# DATA QUALITY PIPELINE OWNER: DB Update Agent — normalises and enriches parts_catalog

from __future__ import annotations

import asyncio
from collections import defaultdict
import httpx
import json
import logging
import os
import re
import time
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from currency_rate import get_usd_to_ils_rate
from resilience import job_registry_start, job_registry_finish
from manufacturer_normalization import PARTS_BRANDS, canonicalize_vehicle_model_for_manufacturer
from manufacturer_normalization import normalize_vehicle_model_name, normalize_vehicle_submodel_name
from manufacturer_normalization import normalize_manufacturer_name

logger = logging.getLogger("db_update_agent")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VAT = 0.18          # Israeli VAT rate
ILS_PER_USD = 3.72  # fallback – overridden at runtime from system_settings

# 14 canonical Hebrew category names
CANONICAL_CATEGORIES: List[str] = [
    "בלמים",
    "גלגלים וצמיגים",
    "דלק",
    "היגוי",
    "חשמל רכב",
    "כללי",
    "מגבים",
    "מיזוג",
    "מנוע",
    "מתלה",
    "פחיין ומרכב",
    "ריפוד ופנים",
    "שרשראות ורצועות",
    "תאורה",
]

# Mapping of synonyms → canonical category
CATEGORY_MAP: Dict[str, str] = {
    # brakes
    "brakes": "בלמים",
    "brake": "בלמים",
    "בלם": "בלמים",
    # wheels / tyres
    "wheels": "גלגלים וצמיגים",
    "tyres": "גלגלים וצמיגים",
    "tires": "גלגלים וצמיגים",
    "גלגלים": "גלגלים וצמיגים",
    "צמיגים": "גלגלים וצמיגים",
    # fuel
    "fuel": "דלק",
    "fuel system": "דלק",
    "מערכת דלק": "דלק",
    # steering
    "steering": "היגוי",
    # electrical
    "electrical": "חשמל רכב",
    "electric": "חשמל רכב",
    "electronics": "חשמל רכב",
    "חשמל": "חשמל רכב",
    # general / misc
    "general": "כללי",
    "misc": "כללי",
    "miscellaneous": "כללי",
    "other": "כללי",
    "אחר": "כללי",
    # wipers
    "wipers": "מגבים",
    "wiper": "מגבים",
    "מגב": "מגבים",
    # ac / climate
    "ac": "מיזוג",
    "air conditioning": "מיזוג",
    "climate": "מיזוג",
    "hvac": "מיזוג",
    # engine
    "engine": "מנוע",
    "motor": "מנוע",
    # suspension
    "suspension": "מתלה",
    "שוקים": "מתלה",
    # body
    "body": "פחיין ומרכב",
    "bodywork": "פחיין ומרכב",
    "מרכב": "פחיין ומרכב",
    # interior
    "interior": "ריפוד ופנים",
    "upholstery": "ריפוד ופנים",
    # chains / belts
    "belts": "שרשראות ורצועות",
    "chains": "שרשראות ורצועות",
    "belt": "שרשראות ורצועות",
    "timing": "שרשראות ורצועות",
    "רצועות": "שרשראות ורצועות",
    # lighting
    "lighting": "תאורה",
    "lights": "תאורה",
    "light": "תאורה",
    "lamps": "תאורה",
    "תאור": "תאורה",

    # reference taxonomy (Autodoc-like families)
    "tyres and related products": "גלגלים וצמיגים",
    "tires and related products": "גלגלים וצמיגים",
    "brake system": "בלמים",
    "filters": "כללי",
    "oils and fluids": "כללי",
    "body": "פחיין ומרכב",
    "suspension and arms": "מתלה",
    "turbocharger": "מנוע",
    "air conditioning": "מיזוג",
    "fuel supply system": "דלק",
    "steering": "היגוי",
    "transmission": "כללי",
    "fasteners": "כללי",
    "pipes and hoses": "מנוע",
    "gaskets and sealing rings": "מנוע",
    "damping": "מתלה",
    "windscreen cleaning system": "מגבים",
    "exhaust system": "כללי",
    "accessories": "כללי",
    "ignition and glowplug system": "חשמל רכב",
    "tuning": "כללי",
    "interior and comfort": "ריפוד ופנים",
    "belts, chains, rollers": "שרשראות ורצועות",
    "exhaust gas recirculation": "מנוע",
    "towbar / parts": "פחיין ומרכב",
    "towbar": "פחיין ומרכב",
    "heater": "מיזוג",
    "bearings": "מתלה",
    "air suspension": "מתלה",
    "sensors, relays, control units": "חשמל רכב",
    "repair kits": "כללי",
    "propshafts and differentials": "מתלה",
    "electrics": "חשמל רכב",
    "engine cooling system": "מנוע",
    "clutch / parts": "מנוע",
    "drive shaft and cv joint": "מתלה",
    "auto detailing & car care": "כללי",
    "tools": "כללי",

    # non-car families -> keep out of core car categories
    "motorcycle accessories": "כללי",
    "motorcycle clothing": "כללי",
    "motorcycle helmets": "כללי",
}

# Normalisation map for part_type
PART_TYPE_MAP: Dict[str, str] = {
    "original": "Original",
    "oem_original": "Original",
    "genuine": "Original",
    "מקורי": "Original",
    "oem": "OEM",
    "oem_equivalent": "OEM",
    "oe": "OEM",
    "aftermarket": "Aftermarket",
    "after market": "Aftermarket",
    "generic": "Aftermarket",
    "third party": "Aftermarket",
    "תחליפי": "Aftermarket",
    "שוק משני": "Aftermarket",
}

# Normalisation map for availability
AVAILABILITY_MAP: Dict[str, str] = {
    "in stock": "in_stock",
    "instock": "in_stock",
    "in-stock": "in_stock",
    "available": "in_stock",
    "במלאי": "in_stock",
    "out of stock": "out_of_stock",
    "outofstock": "out_of_stock",
    "out-of-stock": "out_of_stock",
    "unavailable": "out_of_stock",
    "אזל": "out_of_stock",
    "אין במלאי": "out_of_stock",
    "on order": "on_order",
    "onorder": "on_order",
    "on-order": "on_order",
    "order": "on_order",
    "להזמנה": "on_order",
    "בהזמנה": "on_order",
}

# Known Israeli importers + warranty info  {brand_lower: (importer, years, km, notes)}
BRAND_IMPORTER_MAP: Dict[str, Tuple[str, int, Optional[int], str]] = {
    "toyota":   ("Champion Motors",        3, 100_000, ""),
    "lexus":    ("Champion Motors",        3, 100_000, ""),
    "bmw":      ("PREMIUM MOTORS",         2, None,    "Unlimited km"),
    "mini":     ("PREMIUM MOTORS",         2, None,    "Unlimited km"),
    "volkswagen": ("Dubek",                2, None,    "Unlimited km"),
    "vw":       ("Dubek",                  2, None,    "Unlimited km"),
    "audi":     ("Dubek",                  2, None,    "Unlimited km"),
    "skoda":    ("Dubek",                  2, None,    "Unlimited km"),
    "škoda":    ("Dubek",                  2, None,    "Unlimited km"),
    "seat":     ("Dubek",                  2, None,    "Unlimited km"),
    "hyundai":  ("Colmobil",               5, 150_000, ""),
    "kia":      ("Colmobil",               5, 150_000, ""),
    "mazda":    ("Delek Motors",           3, None,    "Unlimited km"),
    "honda":    ("Car Trading Company",    3, 100_000, ""),
    "nissan":   ("Carasso Motors",         3, None,    "Unlimited km"),
    "infiniti": ("Carasso Motors",         3, None,    "Unlimited km"),
    "ford":     ("Shlomo Sixt",            3, 100_000, ""),
    "subaru":   ("Inovision",              3, 100_000, ""),
    "mitsubishi": ("Inbar Motors",         3, 100_000, ""),
    "jeep":     ("Auto Hadar",             3, 60_000,  ""),
    "chrysler": ("Auto Hadar",             3, 60_000,  ""),
    "dodge":    ("Auto Hadar",             3, 60_000,  ""),
    "mercedes-benz": ("Authorized Importers", 2, None, "Unlimited km"),
    "mercedes": ("Authorized Importers",   2, None,    "Unlimited km"),
    "volvo":    ("Volvo Cars Israel",      3, None,    "Unlimited km"),
    "peugeot":  ("Citroen Israel",         2, 60_000,  ""),
    "citroen":  ("Citroen Israel",         2, 60_000,  ""),
    "renault":  ("Renault Israel",         2, 60_000,  ""),
    "opel":     ("General Motors Israel",  2, 60_000,  ""),
    "chevrolet":("General Motors Israel",  2, 60_000,  ""),
    "fiat":     ("Fiat Israel",            2, 60_000,  ""),
    "suzuki":   ("Sela Motors",            3, 100_000, ""),
    "daihatsu": ("Sela Motors",            3, 100_000, ""),
    "tesla":    ("Tesla Israel",           4, None,    "Unlimited km / 8yr battery"),
}

# Curated generation/platform year ranges used for strict workbook fitment.
GENERATION_YEAR_RULES: Dict[Tuple[str, str, str], Tuple[int, int]] = {
    ("citroen", "berlingo", "b9"): (2008, 2018),
    ("citroen", "berlingo", "k9"): (2018, 2027),
    ("citroen", "berlingo", "k9 acc"): (2018, 2027),
    ("peugeot", "partner", "b9"): (2008, 2018),
    ("peugeot", "partner", "k9"): (2018, 2027),
    ("peugeot", "partner", "k9 acc"): (2018, 2027),
}

# Regex patterns that identify auto-generated / fake SKUs
_FAKE_SKU_PATTERNS = [
    re.compile(r"^[A-Z]{2,6}-[A-Z]{2,6}-\d{3,6}$"),        # OIL-TOY-001
    re.compile(r"^PART-\d+$"),                                # PART-12345
    re.compile(r"^AUTO-[A-Z]+-\d+$"),                        # AUTO-BRK-001
    re.compile(r"^TEMP[-_]\d+$", re.IGNORECASE),             # TEMP-001
    re.compile(r"^TBD[-_]?\d*$", re.IGNORECASE),             # TBD / TBD-1
    re.compile(r"^SKU[-_]\d+$", re.IGNORECASE),              # SKU-001
    re.compile(r"^[A-Z]{1,3}\d{1,3}$"),                      # A1 / AB12 (too short to be real)
    re.compile(r"^0+$"),                                      # 000
]

# Pattern to strip trailing car-model suffix from part names
# e.g., " - Toyota Corolla 2015-2023"  /  " (Ford Focus 2018)"
_NAME_SUFFIX_RE = re.compile(
    r"\s*[-–(]\s*"                                   # separator
    r"([A-Za-zא-ת]+\s+[A-Za-z0-9 À-öø-ÿ]+)"        # brand + model
    r"(?:\s+\d{4}(?:\s*[-–]\s*\d{4})?)?[)]*\s*$",  # optional year range
    re.UNICODE,
)

# -------------------------------------------------------------------------
# Shared state
# -------------------------------------------------------------------------
_last_report: Dict[str, Any] = {}
_agent_running: bool = False


# =========================================================================
# Helpers
# =========================================================================

async def _get_ils_rate(db: AsyncSession) -> float:
    """Read current ILS/USD rate from system_settings (or fall back to default)."""
    return await get_usd_to_ils_rate(db, fallback=ILS_PER_USD)


def _is_fake_sku(sku: str) -> bool:
    return any(p.match(sku) for p in _FAKE_SKU_PATTERNS)


def _normalize_part_type(raw: str) -> Optional[str]:
    return PART_TYPE_MAP.get(raw.strip().lower())


def _normalize_category(raw: str) -> Optional[str]:
    raw_stripped = raw.strip()
    if raw_stripped in CANONICAL_CATEGORIES:
        return None  # already canonical
    return CATEGORY_MAP.get(raw_stripped.lower())


def _normalize_availability(raw: str) -> Optional[str]:
    return AVAILABILITY_MAP.get(raw.strip().lower())


def _clean_vehicle_model(value: Optional[str]) -> str:
    """Normalize model names extracted from catalog compatibility blobs."""
    return normalize_vehicle_model_name(value)


async def ensure_part_vehicle_fitment_table(db: AsyncSession) -> None:
    """Create the scraped fitment table on demand when runtime code still relies on it."""
    await db.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
    await db.execute(text("""
        CREATE TABLE IF NOT EXISTS part_vehicle_fitment (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            part_id UUID NOT NULL REFERENCES parts_catalog(id) ON DELETE CASCADE,
            manufacturer VARCHAR(100) NOT NULL,
            model VARCHAR(100) NOT NULL,
            year_from INTEGER NOT NULL,
            year_to INTEGER NULL,
            engine_type VARCHAR(50) NULL,
            transmission VARCHAR(50) NULL,
            notes TEXT NULL,
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
        )
    """))
    await db.execute(text("ALTER TABLE part_vehicle_fitment ADD COLUMN IF NOT EXISTS tozeret_cd INTEGER NULL"))
    await db.execute(text("ALTER TABLE part_vehicle_fitment ADD COLUMN IF NOT EXISTS degem_cd INTEGER NULL"))
    await db.execute(text("ALTER TABLE part_vehicle_fitment ADD COLUMN IF NOT EXISTS shnat_yitzur INTEGER NULL"))
    await db.execute(text("ALTER TABLE part_vehicle_fitment ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()"))

    # Collapse duplicate keys before creating the unique index used by ON CONFLICT inserts.
    await db.execute(text("""
        DELETE FROM part_vehicle_fitment a
        USING part_vehicle_fitment b
        WHERE a.ctid < b.ctid
          AND a.part_id = b.part_id
          AND a.manufacturer = b.manufacturer
          AND a.model = b.model
          AND a.year_from = b.year_from
    """))

    await db.execute(text("CREATE INDEX IF NOT EXISTS idx_fitment_part_id ON part_vehicle_fitment (part_id)"))
    await db.execute(text("CREATE INDEX IF NOT EXISTS idx_fitment_mfr_model ON part_vehicle_fitment (manufacturer, model)"))
    await db.execute(text("CREATE INDEX IF NOT EXISTS idx_fitment_years ON part_vehicle_fitment (year_from, year_to)"))
    await db.execute(text("CREATE INDEX IF NOT EXISTS idx_pvf_tozeret_degem ON part_vehicle_fitment (tozeret_cd, degem_cd, shnat_yitzur)"))
    await db.execute(text("CREATE INDEX IF NOT EXISTS idx_pvf_manufacturer_model ON part_vehicle_fitment (manufacturer, model, year_from, year_to)"))
    await db.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uix_pvf_part_mfr_model_year_from ON part_vehicle_fitment (part_id, manufacturer, model, year_from)"))
    await db.commit()


# =========================================================================
# Task 1 – Clean part names
# =========================================================================

async def clean_part_names(db: AsyncSession) -> Dict[str, Any]:
    """
    Strip trailing car-model suffixes from part names.

    Pattern:  "<Part Name> - Toyota Corolla 2015"
              "<Part Name> (Ford Focus 2018)"

    The stripped model info is NOT written to part_vehicle_fitment here
    because we don't have structured year/make/model data from the suffix alone.
    The scraper agent is responsible for populating part_vehicle_fitment via
    autodoc fitment data.  We just clean the name so it reads correctly.
    """
    t0 = time.monotonic()
    rows_updated = 0
    rows_checked = 0

    try:
        result = await db.execute(
            text("SELECT id, name FROM parts_catalog WHERE name IS NOT NULL")
        )
        rows = result.fetchall()
        rows_checked = len(rows)

        for part_id, name in rows:
            match = _NAME_SUFFIX_RE.search(name)
            if match:
                clean_name = name[: match.start()].strip()
                if clean_name and clean_name != name:
                    await db.execute(
                        text(
                            "UPDATE parts_catalog SET name = :name, "
                            "updated_at = NOW() WHERE id = :id"
                        ),
                        {"name": clean_name, "id": part_id},
                    )
                    rows_updated += 1

        await db.commit()
        logger.info("clean_part_names: checked=%d updated=%d", rows_checked, rows_updated)
    except Exception as exc:
        await db.rollback()
        logger.error("clean_part_names failed: %s", exc)
        return {"task": "clean_part_names", "status": "error", "error": str(exc)}

    return {
        "task": "clean_part_names",
        "status": "ok",
        "rows_checked": rows_checked,
        "rows_updated": rows_updated,
        "elapsed_s": round(time.monotonic() - t0, 2),
    }


# =========================================================================
# Task 2 – Normalize part types
# =========================================================================

async def normalize_part_types(db: AsyncSession) -> Dict[str, Any]:
    """
    Unify part_type values to one of: "Original", "OEM", "Aftermarket".
    Acts on both parts_catalog and supplier_parts tables.
    """
    t0 = time.monotonic()
    catalog_updated = supplier_updated = 0

    try:
        for table in ("parts_catalog", "supplier_parts"):
            if table == "parts_catalog":
                result = await db.execute(
                    text("SELECT id, part_type FROM parts_catalog WHERE part_type IS NOT NULL")
                )
            else:
                result = await db.execute(
                    text("SELECT id, part_type FROM supplier_parts WHERE part_type IS NOT NULL")
                )
            rows = result.fetchall()
            for row_id, raw_type in rows:
                canonical = _normalize_part_type(raw_type)
                if canonical and canonical != raw_type:
                    if table == "parts_catalog":
                        await db.execute(
                            text("UPDATE parts_catalog SET part_type = :val, updated_at = NOW() WHERE id = :id"),
                            {"val": canonical, "id": row_id},
                        )
                    else:
                        await db.execute(
                            text("UPDATE supplier_parts SET part_type = :val, updated_at = NOW() WHERE id = :id"),
                            {"val": canonical, "id": row_id},
                        )
                    if table == "parts_catalog":
                        catalog_updated += 1
                    else:
                        supplier_updated += 1

        await db.commit()
        logger.info(
            "normalize_part_types: catalog=%d supplier=%d",
            catalog_updated,
            supplier_updated,
        )
    except Exception as exc:
        await db.rollback()
        logger.error("normalize_part_types failed: %s", exc)
        return {"task": "normalize_part_types", "status": "error", "error": str(exc)}

    return {
        "task": "normalize_part_types",
        "status": "ok",
        "catalog_updated": catalog_updated,
        "supplier_updated": supplier_updated,
        "elapsed_s": round(time.monotonic() - t0, 2),
    }


# =========================================================================
# Task 3 – Normalize categories
# =========================================================================

async def normalize_categories(db: AsyncSession) -> Dict[str, Any]:
    """
    Map non-canonical category values to the 14 canonical Hebrew categories.
    Unrecognised categories are set to "כללי" (general).
    """
    t0 = time.monotonic()
    rows_updated = 0

    try:
        result = await db.execute(
            text("SELECT id, category FROM parts_catalog WHERE category IS NOT NULL")
        )
        rows = result.fetchall()

        for part_id, raw_cat in rows:
            canonical = _normalize_category(raw_cat)
            if canonical is not None:
                await db.execute(
                    text(
                        "UPDATE parts_catalog SET category = :cat, updated_at = NOW() "
                        "WHERE id = :id"
                    ),
                    {"cat": canonical, "id": part_id},
                )
                rows_updated += 1
            elif raw_cat.strip() not in CANONICAL_CATEGORIES:
                # Unknown category — fall back to general
                await db.execute(
                    text(
                        "UPDATE parts_catalog SET category = 'כללי', "
                        "updated_at = NOW() WHERE id = :id"
                    ),
                    {"id": part_id},
                )
                rows_updated += 1

        await db.commit()
        logger.info("normalize_categories: updated=%d", rows_updated)
    except Exception as exc:
        await db.rollback()
        logger.error("normalize_categories failed: %s", exc)
        return {"task": "normalize_categories", "status": "error", "error": str(exc)}

    return {
        "task": "normalize_categories",
        "status": "ok",
        "rows_updated": rows_updated,
        "elapsed_s": round(time.monotonic() - t0, 2),
    }


# =========================================================================
# Task 4 – Normalize availability
# =========================================================================

async def normalize_availability(db: AsyncSession) -> Dict[str, Any]:
    """
    Unify availability strings on supplier_parts to:
    "in_stock" / "out_of_stock" / "on_order"
    """
    t0 = time.monotonic()
    rows_updated = 0

    try:
        result = await db.execute(
            text("SELECT id, availability FROM supplier_parts WHERE availability IS NOT NULL")
        )
        rows = result.fetchall()

        for row_id, raw_avail in rows:
            canonical = _normalize_availability(raw_avail)
            if canonical and canonical != raw_avail:
                await db.execute(
                    text(
                        "UPDATE supplier_parts SET availability = :val, "
                        "updated_at = NOW() WHERE id = :id"
                    ),
                    {"val": canonical, "id": row_id},
                )
                rows_updated += 1

        await db.commit()
        logger.info("normalize_availability: updated=%d", rows_updated)
    except Exception as exc:
        await db.rollback()
        logger.error("normalize_availability failed: %s", exc)
        return {"task": "normalize_availability", "status": "error", "error": str(exc)}

    return {
        "task": "normalize_availability",
        "status": "ok",
        "rows_updated": rows_updated,
        "elapsed_s": round(time.monotonic() - t0, 2),
    }


# =========================================================================
# Task 5 – Fix base prices
# =========================================================================

async def fix_base_prices(db: AsyncSession) -> Dict[str, Any]:
    """
    Ensure parts_catalog.base_price (incl. 18 % VAT) is not below the
    cheapest supplier cost:

        min_supplier_cost_ils * (1 + VAT) * MARGIN

    Where MARGIN = 1.30 (30 % retailer mark-up) if base_price is NULL or
    suspiciously low.

    All ILS prices in the catalogue include VAT; supplier cost (price_ils)
    does NOT include VAT.
    """
    MARGIN = 1.30
    t0 = time.monotonic()
    rows_updated = 0

    try:
        ils_rate = await _get_ils_rate(db)

        # Find parts where base_price < supplier cost+VAT+margin or is NULL
        result = await db.execute(
            text(
                """
                SELECT
                    pc.id,
                    pc.base_price,
                    MIN(
                        CASE
                            WHEN sp.price_ils IS NOT NULL THEN sp.price_ils
                            WHEN sp.price_usd IS NOT NULL THEN sp.price_usd * :rate
                            ELSE NULL
                        END
                    ) AS min_cost_ils
                FROM parts_catalog pc
                JOIN supplier_parts sp ON sp.part_id = pc.id AND sp.is_available = TRUE
                GROUP BY pc.id, pc.base_price
                HAVING
                    pc.base_price IS NULL
                    OR pc.base_price < MIN(
                        CASE
                            WHEN sp.price_ils IS NOT NULL THEN sp.price_ils
                            WHEN sp.price_usd IS NOT NULL THEN sp.price_usd * :rate
                            ELSE NULL
                        END
                    ) * :vat * :margin
                """
            ),
            {"rate": ils_rate, "vat": 1 + VAT, "margin": MARGIN},
        )
        rows = result.fetchall()

        for part_id, old_price, min_cost_ils in rows:
            if min_cost_ils is None:
                continue
            new_price = round(float(min_cost_ils) * (1 + VAT) * MARGIN, 2)
            await db.execute(
                text(
                    "UPDATE parts_catalog SET base_price = :price, "
                    "updated_at = NOW() WHERE id = :id"
                ),
                {"price": new_price, "id": part_id},
            )
            rows_updated += 1

        await db.commit()
        logger.info("fix_base_prices: updated=%d (rate=%.2f)", rows_updated, ils_rate)
    except Exception as exc:
        await db.rollback()
        logger.error("fix_base_prices failed: %s", exc)
        return {"task": "fix_base_prices", "status": "error", "error": str(exc)}

    return {
        "task": "fix_base_prices",
        "status": "ok",
        "rows_updated": rows_updated,
        "ils_per_usd_used": ils_rate,
        "elapsed_s": round(time.monotonic() - t0, 2),
    }


# =========================================================================
# Task 6 – Flag fake SKUs
# =========================================================================

async def flag_fake_skus(db: AsyncSession) -> Dict[str, Any]:
    """
    Set needs_oem_lookup = TRUE on parts whose SKU matches auto-generated
    patterns (e.g., "OIL-TOY-001", "PART-12345").
    """
    t0 = time.monotonic()
    rows_flagged = 0

    try:
        result = await db.execute(
            text(
                "SELECT id, sku FROM parts_catalog "
                "WHERE sku IS NOT NULL AND (needs_oem_lookup IS NULL OR needs_oem_lookup = FALSE)"
            )
        )
        rows = result.fetchall()

        for part_id, sku in rows:
            if _is_fake_sku(sku):
                await db.execute(
                    text(
                        "UPDATE parts_catalog SET needs_oem_lookup = TRUE, "
                        "updated_at = NOW() WHERE id = :id"
                    ),
                    {"id": part_id},
                )
                rows_flagged += 1

        await db.commit()
        logger.info("flag_fake_skus: flagged=%d", rows_flagged)
    except Exception as exc:
        await db.rollback()
        logger.error("flag_fake_skus failed: %s", exc)
        return {"task": "flag_fake_skus", "status": "error", "error": str(exc)}

    return {
        "task": "flag_fake_skus",
        "status": "ok",
        "rows_flagged": rows_flagged,
        "elapsed_s": round(time.monotonic() - t0, 2),
    }


# =========================================================================
# Task 7 – Fill car brand metadata
# =========================================================================

async def fill_car_brands(db: AsyncSession) -> Dict[str, Any]:
    """
    Seed il_importer, warranty_years, warranty_km, warranty_notes for
    known Israeli brands.  Only updates rows where the field is NULL to
    avoid overwriting manual edits.
    """
    t0 = time.monotonic()
    rows_updated = 0

    try:
        result = await db.execute(
            text("SELECT id, name FROM car_brands WHERE name IS NOT NULL")
        )
        brands = result.fetchall()

        for brand_id, brand_name in brands:
            data = BRAND_IMPORTER_MAP.get(brand_name.strip().lower())
            if not data:
                continue
            importer, years, km, notes = data

            # Build update only for NULL fields
            updates: List[str] = []
            params: Dict[str, Any] = {"id": brand_id}

            r = await db.execute(
                text(
                    "SELECT il_importer, warranty_years, warranty_km, warranty_notes "
                    "FROM car_brands WHERE id = :id"
                ),
                {"id": brand_id},
            )
            row = r.fetchone()
            if not row:
                continue
            cur_importer, cur_years, cur_km, cur_notes = row

            if cur_importer is None:
                updates.append("il_importer = :importer")
                params["importer"] = importer
            if cur_years is None:
                updates.append("warranty_years = :years")
                params["years"] = years
            if cur_km is None and km is not None:
                updates.append("warranty_km = :km")
                params["km"] = km
            if cur_notes is None and notes:
                updates.append("warranty_notes = :notes")
                params["notes"] = notes

            if updates:
                await db.execute(
                    text(
                        f"UPDATE car_brands SET {', '.join(updates)}, "
                        "updated_at = NOW() WHERE id = :id"
                    ),
                    params,
                )
                rows_updated += 1

        await db.commit()
        logger.info("fill_car_brands: updated=%d brands", rows_updated)
    except Exception as exc:
        await db.rollback()
        logger.error("fill_car_brands failed: %s", exc)
        return {"task": "fill_car_brands", "status": "error", "error": str(exc)}

    return {
        "task": "fill_car_brands",
        "status": "ok",
        "rows_updated": rows_updated,
        "elapsed_s": round(time.monotonic() - t0, 2),
    }


async def normalize_imported_manufacturers(db: AsyncSession) -> Dict[str, Any]:
    """
    Normalize free-text manufacturer values introduced by imports so search and
    registry mapping remain stable across reruns.
    """
    t0 = time.monotonic()
    catalog_updated = 0
    vehicles_updated = 0
    try:
        from manufacturer_normalization import normalize_manufacturer_name

        cat_rows = (await db.execute(text("""
            SELECT DISTINCT manufacturer
            FROM parts_catalog
            WHERE manufacturer IS NOT NULL
              AND manufacturer <> ''
        """))).fetchall()

        for (raw,) in cat_rows:
            if not raw:
                continue
            canon = normalize_manufacturer_name(raw, raw)
            if canon and canon != raw:
                res = await db.execute(
                    text("""
                        UPDATE parts_catalog
                        SET manufacturer = :canon,
                            updated_at = NOW()
                        WHERE manufacturer = :raw
                    """),
                    {"raw": raw, "canon": canon},
                )
                catalog_updated += res.rowcount or 0

        veh_rows = (await db.execute(text("""
            SELECT DISTINCT manufacturer
            FROM vehicles
            WHERE manufacturer IS NOT NULL
              AND manufacturer <> ''
        """))).fetchall()

        for (raw,) in veh_rows:
            if not raw:
                continue
            canon = normalize_manufacturer_name(raw, raw)
            if canon and canon != raw:
                res = await db.execute(
                    text("""
                        UPDATE vehicles
                        SET manufacturer = :canon
                        WHERE manufacturer = :raw
                    """),
                    {"raw": raw, "canon": canon},
                )
                vehicles_updated += res.rowcount or 0

        # Fix manufacturers based on OEM number prefix
        from manufacturer_normalization import normalize_oem_manufacturer, OEM_PREFIX_TO_MANUFACTURER
        oem_prefix_updated = 0
        for prefix, correct_mfr in OEM_PREFIX_TO_MANUFACTURER.items():
            res = await db.execute(
                text("""
                    UPDATE parts_catalog
                    SET manufacturer = :correct,
                        updated_at = NOW()
                    WHERE oem_number LIKE :prefix
                    AND manufacturer != :correct
                    AND is_active = true
                """),
                {"correct": correct_mfr, "prefix": f"{prefix}%"},
            )
            if res.rowcount:
                logger.info(
                    "normalize_oem_prefix: %s prefix=%s updated=%d",
                    correct_mfr, prefix, res.rowcount
                )
                oem_prefix_updated += res.rowcount

        await db.commit()
        return {
            "task": "normalize_imported_manufacturers",
            "status": "ok",
            "catalog_updated": catalog_updated,
            "vehicles_updated": vehicles_updated,
            "oem_prefix_updated": oem_prefix_updated,
            "elapsed_s": round(time.monotonic() - t0, 2),
        }
    except Exception as exc:
        await db.rollback()
        logger.error("normalize_imported_manufacturers failed: %s", exc)
        return {
            "task": "normalize_imported_manufacturers",
            "status": "error",
            "error": str(exc),
        }


async def sync_manufacturer_registries(db: AsyncSession) -> Dict[str, Any]:
    """
    Keep brand registries deployment-safe and idempotent:
    - canonicalize noisy manufacturers into car_brands
    - ensure baseline truck brands in truck_brands
    - keep logo_url/aliases populated
    """
    t0 = time.monotonic()
    try:
        from clean_manufacturers_registry import sync_manufacturer_registries as _sync
        report = await _sync(db)
        return {
            "task": "sync_manufacturer_registries",
            "status": "ok",
            **report,
            "elapsed_s": round(time.monotonic() - t0, 2),
        }
    except Exception as exc:
        try:
            await db.rollback()
        except Exception:
            pass
        logger.error("sync_manufacturer_registries failed: %s", exc)
        return {
            "task": "sync_manufacturer_registries",
            "status": "error",
            "error": str(exc),
        }


async def sync_models_from_catalog(db: AsyncSession) -> Dict[str, Any]:
    """Backfill vehicles(manufacturer, model, year) from parts_catalog.compatible_vehicles.

    This gives the manufacturer→model hierarchy a richer source even when
    imported vehicle rows are sparse.
    """
    t0 = time.monotonic()
    inserted = 0
    scanned = 0

    rows = (await db.execute(text("""
        SELECT manufacturer, compatible_vehicles
        FROM parts_catalog
        WHERE is_active = TRUE
          AND manufacturer IS NOT NULL
          AND TRIM(manufacturer) <> ''
          AND compatible_vehicles IS NOT NULL
          AND jsonb_typeof(compatible_vehicles) = 'array'
    """))).fetchall()

    if not rows:
        return {"task": "sync_models_from_catalog", "status": "ok", "scanned": 0, "inserted": 0}

    # Track seen combinations in this run to minimize duplicate DB checks.
    seen_keys = set()

    for manufacturer, compat in rows:
        mfr = (manufacturer or "").strip()
        if not mfr:
            continue
        if not isinstance(compat, list):
            continue

        for item in compat:
            if not isinstance(item, dict):
                continue

            raw_model = item.get("model") or item.get("model_year")
            model = _clean_vehicle_model(raw_model)
            if not model:
                continue

            # Prefer structured years when available; else use 0 placeholder.
            y_from = item.get("year_from")
            y_to = item.get("year_to")
            year = 0
            try:
                if isinstance(y_from, int):
                    year = y_from
                elif isinstance(y_from, str) and y_from.isdigit():
                    year = int(y_from)
                elif isinstance(y_to, int):
                    year = y_to
                elif isinstance(y_to, str) and y_to.isdigit():
                    year = int(y_to)
            except Exception:
                year = 0

            key = (mfr.casefold(), model.casefold(), int(year))
            if key in seen_keys:
                continue
            seen_keys.add(key)
            scanned += 1

            exists = (await db.execute(text("""
                SELECT 1
                FROM vehicles
                WHERE LOWER(TRIM(manufacturer)) = LOWER(TRIM(:mfr))
                  AND LOWER(TRIM(model)) = LOWER(TRIM(:model))
                  AND year = :year
                LIMIT 1
            """), {"mfr": mfr, "model": model, "year": year})).fetchone()
            if exists:
                continue

            await db.execute(text("""
                INSERT INTO vehicles
                    (id, license_plate, manufacturer, model, year, vin, created_at)
                VALUES
                    (gen_random_uuid(), NULL, :mfr, :model, :year, NULL, NOW())
            """), {"mfr": mfr, "model": model, "year": year})
            inserted += 1

    await db.commit()
    return {
        "task": "sync_models_from_catalog",
        "status": "ok",
        "scanned": scanned,
        "inserted": inserted,
        "elapsed_s": round(time.monotonic() - t0, 2),
    }


async def sync_models_from_catalog_file(db: AsyncSession) -> Dict[str, Any]:
    """Extract manufacturer->model pairs from backend/data/parts_database.xlsx.

    This uses the original catalog workbook (not only already-imported rows),
    so brand model dropdowns get a richer hierarchy (notably Citroen/Peugeot).
    """
    t0 = time.monotonic()
    scanned = 0
    inserted = 0
    hierarchy_rows: set[Tuple[str, str, str, int, int, str]] = set()

    def _add_hierarchy_row(mfr: Optional[str], model: Optional[str], sub_model: Optional[str], year_from: int = 0, year_to: int = 0, source_sheet: str = "derived") -> None:
        canonical_mfr = normalize_manufacturer_name(mfr, mfr)
        if not canonical_mfr or canonical_mfr.strip().lower() in PARTS_BRANDS:
            return
        canonical_model = canonicalize_vehicle_model_for_manufacturer(canonical_mfr, model)
        canonical_sub = normalize_vehicle_submodel_name(sub_model)
        if not canonical_mfr or not canonical_model:
            return
        hierarchy_rows.add((canonical_mfr, canonical_model, canonical_sub or "", int(year_from or 0), int(year_to or 0), source_sheet))

    try:
        import openpyxl
        from pathlib import Path
        
    except Exception as exc:
        return {
            "task": "sync_models_from_catalog_file",
            "status": "error",
            "error": f"dependency_error: {exc}",
        }

    xlsx_path = Path(__file__).resolve().parent / "data" / "parts_database.xlsx"
    if not xlsx_path.exists():
        return {
            "task": "sync_models_from_catalog_file",
            "status": "skipped",
            "reason": "catalog_file_missing",
            "path": str(xlsx_path),
        }

    # Keep consistent with importer sheet mapping.
    sheet_map = {
        "Chevrolet": ("Chevrolet", "A"),
        "Citroen": ("Citroen", "F"),
        "Peugeot": ("Peugeot", "F"),
    }

    def _norm_spaces(s: str) -> str:
        return re.sub(r"\s+", " ", (s or "").strip())

    def _extract_years(values: List[Any]) -> List[int]:
        years: set[int] = set()
        for v in values:
            if v is None:
                continue
            s = str(v)
            for m in re.findall(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)", s):
                try:
                    yy = int(m)
                except Exception:
                    continue
                if 1990 <= yy <= 2027:
                    years.add(yy)
        return sorted(years)

    def _split_model_submodel(raw: Optional[str], mfr_variants: List[str], row_values: Optional[List[Any]] = None) -> Tuple[str, str, int, int, int]:
        years = _extract_years([raw or ""])
        year_hint = years[0] if years else 0
        year_from = years[0] if years else 0
        year_to = years[-1] if years else 0
        target_mfr = normalize_manufacturer_name(
            mfr_variants[0] if mfr_variants else "",
            mfr_variants[0] if mfr_variants else "",
        )

        m = _clean_vehicle_model(raw)
        if not m:
            return "", "", year_hint, year_from, year_to
        m2 = _norm_spaces(m)

        # Remove leading manufacturer tokens from model labels
        # e.g. "CITROEN C4" -> "C4".
        low = m2.lower()
        for v in sorted({x.lower() for x in mfr_variants if x}, key=len, reverse=True):
            if low.startswith(v + " "):
                m2 = m2[len(v):].strip()
                break

        # Remove trailing noisy qualifiers often present in workbook model fields.
        m2 = re.sub(r"\b(new|basic|accessories|accessory)\b", "", m2, flags=re.IGNORECASE).strip()
        m2 = re.sub(r"\b(19|20)\d{2}\b", "", m2).strip()
        m2 = re.sub(r"\s{2,}", " ", m2).strip()

        if not _clean_vehicle_model(m2):
            return "", "", year_hint, year_from, year_to

        # Split base model and submodel/trim for hierarchy.
        if re.search(r"\s-\s*", m2):
            base, sub = [x.strip() for x in re.split(r"\s-\s*", m2, maxsplit=1)]
        else:
            # Platform/trim variants frequently appear as trailing tokens in workbook rows.
            # Examples: "BERLINGO B9", "BERLINGO K9", "BERLINGO K9 ACC".
            m_code = re.match(
                r"^(?P<base>[A-Za-z0-9\u0590-\u05FF\s]+?)\s+(?P<sub>[A-Z]\d{1,3}(?:\s+[A-Z]{2,8})?)$",
                m2,
                flags=re.IGNORECASE,
            )
            if m_code:
                base, sub = m_code.group("base").strip(), m_code.group("sub").upper()
            else:
                base, sub = m2, ""

        base = canonicalize_vehicle_model_for_manufacturer(target_mfr, base)
        sub = normalize_vehicle_submodel_name(sub)
        if not base:
            return "", "", year_hint, year_from, year_to
        return base, sub, year_hint, year_from, year_to

    wb = openpyxl.load_workbook(xlsx_path, read_only=True, data_only=True)

    seen_run = set()
    for sheet_name, (raw_mfr, stype) in sheet_map.items():
        if sheet_name not in wb.sheetnames:
            continue

        canonical_mfr = normalize_manufacturer_name(raw_mfr, raw_mfr)
        mfr_variants = [canonical_mfr, raw_mfr, sheet_name]
        ws = wb[sheet_name]

        # Sheet-specific model column location.
        if stype == "F":
            start_row = 8
            model_idx = 0
        elif stype == "A":
            start_row = 3
            model_idx = 7
        else:
            continue

        for row in ws.iter_rows(min_row=start_row, values_only=True):
            if model_idx >= len(row):
                continue
            model_raw = row[model_idx]
            model, sub_model, year_hint, year_from, year_to = _split_model_submodel(
                str(model_raw) if model_raw is not None else "",
                mfr_variants,
                None,
            )
            if not model:
                continue

            target_mfr = canonical_mfr
            # Known PSA cross-brand correction: Partner belongs under Peugeot.
            if canonical_mfr.casefold() == "citroen" and model.casefold().startswith("partner"):
                target_mfr = "Peugeot"

            key = (target_mfr.casefold(), model.casefold(), sub_model.casefold(), int(year_hint or 0))
            if key in seen_run:
                continue
            seen_run.add(key)
            scanned += 1
            _add_hierarchy_row(target_mfr, model, sub_model, int(year_from or 0), int(year_to or 0), sheet_name)

            exists = (await db.execute(text("""
                SELECT 1
                FROM vehicles
                WHERE LOWER(TRIM(manufacturer)) = LOWER(TRIM(:mfr))
                  AND LOWER(TRIM(model)) = LOWER(TRIM(:model))
                  AND LOWER(COALESCE(gov_api_data->>'sub_model', '')) = LOWER(:sub_model)
                                    AND year = :year
                LIMIT 1
                        """), {"mfr": target_mfr, "model": model, "sub_model": sub_model or "", "year": int(year_hint or 0)})).fetchone()
            if exists:
                continue

            await db.execute(text("""
                INSERT INTO vehicles
                    (id, license_plate, manufacturer, model, year, vin, gov_api_data, created_at)
                VALUES
                    (gen_random_uuid(), NULL, :mfr, :model, :year, NULL, CAST(:gov AS jsonb), NOW())
            """), {
                "mfr": target_mfr,
                "model": model,
                "year": int(year_hint or 0),
                "gov": json.dumps({"sub_model": sub_model} if sub_model else {}, ensure_ascii=False),
            })
            inserted += 1

    compat_rows = (await db.execute(text("""
        SELECT DISTINCT
            COALESCE(elem->>'make', elem->>'manufacturer') AS manufacturer,
            COALESCE(elem->>'model', elem->>'model_year') AS model,
            COALESCE(elem->>'sub_model', '') AS sub_model,
            COALESCE(elem->>'year_from', '') AS year_from,
            COALESCE(elem->>'year_to', '') AS year_to,
            COALESCE(elem->>'year', '') AS year_hint
        FROM parts_catalog,
             jsonb_array_elements(coalesce(compatible_vehicles, '[]'::jsonb)) AS elem
        WHERE compatible_vehicles IS NOT NULL
          AND jsonb_typeof(compatible_vehicles) = 'array'
          AND COALESCE(elem->>'make', elem->>'manufacturer') IS NOT NULL
          AND COALESCE(elem->>'make', elem->>'manufacturer') <> ''
          AND COALESCE(elem->>'model', elem->>'model_year') IS NOT NULL
          AND COALESCE(elem->>'model', elem->>'model_year') <> ''
    """))).fetchall()
    for mfr, model, sub_model, year_from, year_to, year_hint in compat_rows:
        try:
            yf = int(year_from) if str(year_from).isdigit() else 0
        except Exception:
            yf = 0
        try:
            yt = int(year_to) if str(year_to).isdigit() else 0
        except Exception:
            yt = 0
        if not yf or not yt:
            try:
                yv = int(year_hint) if str(year_hint).isdigit() else 0
            except Exception:
                yv = 0
            if yv:
                yf = yt = yv
        _add_hierarchy_row(mfr, model, sub_model, yf, yt, "compatible_vehicles")

    vehicle_rows = (await db.execute(text("""
        SELECT manufacturer, model, COALESCE(gov_api_data->>'sub_model', '') AS sub_model, year
        FROM vehicles
        WHERE manufacturer IS NOT NULL
          AND manufacturer <> ''
          AND model IS NOT NULL
          AND model <> ''
    """))).fetchall()
    for mfr, model, sub_model, year in vehicle_rows:
        yv = int(year or 0) if isinstance(year, int) else 0
        _add_hierarchy_row(mfr, model, sub_model, yv, yv, "vehicles")

    # Backfill model/sub-model year bounds from vehicles when workbook row lacks year data.
    v_rows = (await db.execute(text("""
        SELECT
            manufacturer,
            model,
            COALESCE(gov_api_data->>'sub_model', '') AS sub_model,
            MIN(year) AS y_from,
            MAX(year) AS y_to
        FROM vehicles
        WHERE year BETWEEN 1990 AND 2027
          AND manufacturer IS NOT NULL
          AND model IS NOT NULL
        GROUP BY manufacturer, model, COALESCE(gov_api_data->>'sub_model', '')
    """))).fetchall()
    exact_years: Dict[Tuple[str, str, str], Tuple[int, int]] = {}
    model_years: Dict[Tuple[str, str], Tuple[int, int]] = {}
    for mfr, mdl, sub, y_from, y_to in v_rows:
        if not mfr or not mdl:
            continue
        mf = str(mfr).strip().casefold()
        md = normalize_vehicle_model_name(str(mdl)).casefold()
        sb = normalize_vehicle_submodel_name(str(sub or "")).casefold()
        if not md:
            continue
        yf = int(y_from or 0)
        yt = int(y_to or 0)
        if yf and yt:
            exact_years[(mf, md, sb)] = (yf, yt)
            prev = model_years.get((mf, md))
            if not prev:
                model_years[(mf, md)] = (yf, yt)
            else:
                model_years[(mf, md)] = (min(prev[0], yf), max(prev[1], yt))

    enriched_rows: set[Tuple[str, str, str, int, int, str]] = set()
    for mfr, model, sub_model, y_from, y_to, source_sheet in hierarchy_rows:
        yf = int(y_from or 0)
        yt = int(y_to or 0)
        rule_span = GENERATION_YEAR_RULES.get((mfr.casefold(), model.casefold(), (sub_model or "").casefold()))
        if rule_span:
            yf, yt = int(rule_span[0]), int(rule_span[1])
        if not yf or not yt:
            ek = (mfr.casefold(), model.casefold(), (sub_model or "").casefold())
            mk = (mfr.casefold(), model.casefold())
            by_sub = exact_years.get(ek)
            by_model = model_years.get(mk)
            # For specific sub-model entries, avoid over-broad model-level spans.
            span = by_sub or (by_model if not (sub_model or "").strip() else None)
            if span:
                yf, yt = int(span[0]), int(span[1])
        enriched_rows.add((mfr, model, sub_model, yf, yt, source_sheet))
    hierarchy_rows = enriched_rows

    # Materialize a dedicated XLS hierarchy table for frontend filters.
    await db.execute(text("""
        CREATE TABLE IF NOT EXISTS vehicle_hierarchy_xls (
            id BIGSERIAL PRIMARY KEY,
            manufacturer TEXT NOT NULL,
            model TEXT NOT NULL,
            sub_model TEXT NOT NULL DEFAULT '',
            year_from INTEGER NOT NULL DEFAULT 0,
            year_to INTEGER NOT NULL DEFAULT 0,
            year_hint INTEGER NOT NULL DEFAULT 0,
            source_sheet TEXT,
            source_tag TEXT NOT NULL DEFAULT 'parts_database.xlsx',
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """))
    await db.execute(text("ALTER TABLE vehicle_hierarchy_xls ADD COLUMN IF NOT EXISTS year_from INTEGER NOT NULL DEFAULT 0"))
    await db.execute(text("ALTER TABLE vehicle_hierarchy_xls ADD COLUMN IF NOT EXISTS year_to INTEGER NOT NULL DEFAULT 0"))
    await db.execute(text("TRUNCATE TABLE vehicle_hierarchy_xls"))

    for mfr, model, sub_model, year_from, year_to, source_sheet in sorted(hierarchy_rows):
        await db.execute(text("""
            INSERT INTO vehicle_hierarchy_xls
                (manufacturer, model, sub_model, year_from, year_to, year_hint, source_sheet, source_tag, updated_at)
            VALUES
                (:mfr, :model, :sub_model, :year_from, :year_to, :year_hint, :source_sheet, 'parts_database.xlsx', NOW())
        """), {
            "mfr": mfr,
            "model": model,
            "sub_model": sub_model,
            "year_from": int(year_from or 0),
            "year_to": int(year_to or 0),
            "year_hint": int(year_from or 0),
            "source_sheet": source_sheet,
        })

    await db.commit()
    return {
        "task": "sync_models_from_catalog_file",
        "status": "ok",
        "scanned": scanned,
        "inserted": inserted,
        "hierarchy_rows": len(hierarchy_rows),
        "elapsed_s": round(time.monotonic() - t0, 2),
    }


async def backfill_catalog_fitment_from_xls(db: AsyncSession) -> Dict[str, Any]:
    """Backfill exact workbook fitment into parts_catalog.compatible_vehicles."""
    t0 = time.monotonic()
    try:
        import openpyxl
        from pathlib import Path
    except Exception as exc:
        return {
            "task": "backfill_catalog_fitment_from_xls",
            "status": "error",
            "error": f"dependency_error: {exc}",
        }

    xlsx_path = Path(__file__).resolve().parent / "data" / "parts_database.xlsx"
    if not xlsx_path.exists():
        return {
            "task": "backfill_catalog_fitment_from_xls",
            "status": "skipped",
            "reason": "catalog_file_missing",
            "path": str(xlsx_path),
        }

    sheet_map = {
        "Chevrolet": ("Chevrolet", "CHEV-", "A"),
        "Citroen": ("Citroen", "CITR-", "F"),
        "Peugeot": ("Peugeot", "PEUG-", "F"),
    }

    def _norm_spaces(s: str) -> str:
        return re.sub(r"\s+", " ", (s or "").strip())

    def _extract_years(values: List[Any]) -> List[int]:
        years: set[int] = set()
        for v in values:
            if v is None:
                continue
            for m in re.findall(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)", str(v)):
                try:
                    yy = int(m)
                except Exception:
                    continue
                if 1990 <= yy <= 2027:
                    years.add(yy)
        return sorted(years)

    def _split_model_submodel(raw: Optional[str], mfr_variants: List[str]) -> Tuple[str, str, int, int]:
        years = _extract_years([raw or ""])
        year_from = years[0] if years else 0
        year_to = years[-1] if years else 0
        target_mfr = normalize_manufacturer_name(
            mfr_variants[0] if mfr_variants else "",
            mfr_variants[0] if mfr_variants else "",
        )

        model_text = _clean_vehicle_model(raw)
        if not model_text:
            return "", "", year_from, year_to
        model_text = _norm_spaces(model_text)

        low = model_text.lower()
        for v in sorted({x.lower() for x in mfr_variants if x}, key=len, reverse=True):
            if low.startswith(v + " "):
                model_text = model_text[len(v):].strip()
                break

        model_text = re.sub(r"\b(new|basic|accessories|accessory)\b", "", model_text, flags=re.IGNORECASE).strip()
        model_text = re.sub(r"\b(19|20)\d{2}\b", "", model_text).strip()
        model_text = re.sub(r"\s{2,}", " ", model_text).strip()
        if not _clean_vehicle_model(model_text):
            return "", "", year_from, year_to

        if re.search(r"\s-\s*", model_text):
            base, sub = [x.strip() for x in re.split(r"\s-\s*", model_text, maxsplit=1)]
        else:
            m_code = re.match(
                r"^(?P<base>[A-Za-z0-9\u0590-\u05FF\s]+?)\s+(?P<sub>[A-Z]\d{1,3}(?:\s+[A-Z]{2,8})?)$",
                model_text,
                flags=re.IGNORECASE,
            )
            if m_code:
                base, sub = m_code.group("base").strip(), m_code.group("sub").upper()
            else:
                base, sub = model_text, ""

        base = canonicalize_vehicle_model_for_manufacturer(target_mfr, base)
        sub = normalize_vehicle_submodel_name(sub)
        return base, sub, year_from, year_to

    def _parse_fitment_row(row: Any, stype: str) -> Optional[Dict[str, str]]:
        row = list(row)
        if stype == "F":
            if len(row) < 8:
                return None
            model_raw = (str(row[0]).strip() if row[0] is not None else "")
            name = (str(row[6]).strip() if row[6] is not None else "")
            catalog = (str(row[7]).strip() if row[7] is not None else "")
        elif stype == "A":
            if len(row) < 8:
                return None
            model_raw = (str(row[7]).strip() if row[7] is not None else "")
            name = (str(row[2]).strip() if row[2] is not None else "")
            catalog = (str(row[1]).strip() if row[1] is not None else "")
        else:
            return None
        if not model_raw or not name or not catalog:
            return None
        return {"model_raw": model_raw, "name": name, "catalog": catalog}

    hierarchy_rows = (await db.execute(text("""
        SELECT manufacturer, model, sub_model, year_from, year_to
        FROM vehicle_hierarchy_xls
    """))).fetchall()
    hierarchy_map: Dict[Tuple[str, str, str], Tuple[int, int]] = {}
    for mfr, model, sub_model, year_from, year_to in hierarchy_rows:
        key = (
            str(mfr or "").strip().casefold(),
            normalize_vehicle_model_name(str(model or "")).casefold(),
            normalize_vehicle_submodel_name(str(sub_model or "")).casefold(),
        )
        if key[0] and key[1]:
            hierarchy_map[key] = (int(year_from or 0), int(year_to or 0))

    wb = openpyxl.load_workbook(xlsx_path, read_only=True, data_only=True)
    matched_rows = 0
    updated_parts = 0
    fitment_rows = 0

    for sheet_name, (raw_mfr, sku_prefix, stype) in sheet_map.items():
        if sheet_name not in wb.sheetnames:
            continue

        canonical_mfr = normalize_manufacturer_name(raw_mfr, raw_mfr)
        mfr_variants = [canonical_mfr, raw_mfr, sheet_name]

        part_rows = (await db.execute(text("""
            SELECT id, sku, oem_number, name, compatible_vehicles
            FROM parts_catalog
            WHERE manufacturer IS NOT NULL
              AND (
                    LOWER(TRIM(manufacturer)) = LOWER(TRIM(:m0))
                 OR LOWER(TRIM(manufacturer)) = LOWER(TRIM(:m1))
                 OR LOWER(TRIM(manufacturer)) = LOWER(TRIM(:m2))
              )
        """), {"m0": canonical_mfr, "m1": raw_mfr, "m2": sheet_name})).fetchall()

        by_sku: Dict[str, Tuple[str, Any]] = {}
        by_oem: Dict[str, Tuple[str, Any]] = {}
        by_name: Dict[str, Tuple[str, Any]] = {}
        part_compat_by_id: Dict[str, List[Any]] = {}
        for part_id, sku, oem_number, name, compat in part_rows:
            pid = str(part_id)
            part_compat_by_id[pid] = list(compat or []) if isinstance(compat, list) else []
            if sku:
                sku_str = str(sku).strip()
                by_sku[sku_str.upper()] = (pid, compat)
                if sku_str.upper().startswith(sku_prefix):
                    by_sku[sku_str[len(sku_prefix):].upper()] = (pid, compat)
            if oem_number:
                by_oem[str(oem_number).strip().upper()] = (pid, compat)
            if name:
                by_name[str(name).strip()] = (pid, compat)

        part_fitments: Dict[str, List[Dict[str, Any]]] = {}
        ws = wb[sheet_name]
        start_row = 8 if stype == "F" else 3
        seen_rows: set[Tuple[str, str, str]] = set()
        for row in ws.iter_rows(min_row=start_row, values_only=True):
            rec = _parse_fitment_row(row, stype)
            if not rec:
                continue

            model, sub_model, year_from, year_to = _split_model_submodel(rec["model_raw"], mfr_variants)
            if not model:
                continue

            target_mfr = canonical_mfr
            if canonical_mfr.casefold() == "citroen" and model.casefold().startswith("partner"):
                target_mfr = "Peugeot"

            row_key = (rec["catalog"].upper(), model.casefold(), sub_model.casefold())
            if row_key in seen_rows:
                continue
            seen_rows.add(row_key)

            match = by_sku.get(rec["catalog"].upper()) or by_oem.get(rec["catalog"].upper()) or by_name.get(rec["name"])
            if not match:
                continue

            span = hierarchy_map.get((target_mfr.casefold(), model.casefold(), sub_model.casefold()))
            if span:
                year_from, year_to = span
            if not year_from or not year_to:
                rule_span = GENERATION_YEAR_RULES.get((target_mfr.casefold(), model.casefold(), sub_model.casefold()))
                if rule_span:
                    year_from, year_to = rule_span

            part_id, _existing = match
            entry: Dict[str, Any] = {
                "manufacturer": target_mfr,
                "model": model,
                "source": "parts_database.xlsx",
            }
            if sub_model:
                entry["sub_model"] = sub_model
            if year_from and year_to and 1990 <= int(year_from) <= int(year_to) <= 2027:
                entry["year_from"] = int(year_from)
                entry["year_to"] = int(year_to)

            part_fitments.setdefault(part_id, []).append(entry)
            matched_rows += 1

        for part_id, entries in part_fitments.items():
            preserved = [
                item for item in part_compat_by_id.get(part_id, [])
                if not (isinstance(item, dict) and item.get("source") == "parts_database.xlsx")
            ]
            merged: List[Dict[str, Any]] = []
            seen_json = set()
            for item in preserved + entries:
                if not isinstance(item, dict):
                    continue
                key = json.dumps(item, sort_keys=True, ensure_ascii=False)
                if key in seen_json:
                    continue
                seen_json.add(key)
                merged.append(item)

            await db.execute(text("""
                UPDATE parts_catalog
                SET compatible_vehicles = CAST(:compat AS jsonb),
                    updated_at = NOW()
                WHERE id = CAST(:part_id AS uuid)
            """), {
                "part_id": part_id,
                "compat": json.dumps(merged, ensure_ascii=False),
            })
            updated_parts += 1
            fitment_rows += len(entries)

    await db.commit()
    return {
        "task": "backfill_catalog_fitment_from_xls",
        "status": "ok",
        "matched_rows": matched_rows,
        "updated_parts": updated_parts,
        "fitment_rows": fitment_rows,
        "elapsed_s": round(time.monotonic() - t0, 2),
    }


async def merge_catalog_fitment_from_part_vehicle_fitment(db: AsyncSession) -> Dict[str, Any]:
    """Promote scraped part_vehicle_fitment rows into parts_catalog.compatible_vehicles."""
    t0 = time.monotonic()
    await ensure_part_vehicle_fitment_table(db)

    def _normalize_fitment_json_list(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Deduplicate and sort fitment dicts so writes remain stable across runs."""
        unique: List[Dict[str, Any]] = []
        seen = set()
        for item in items:
            if not isinstance(item, dict):
                continue
            key = json.dumps(item, sort_keys=True, ensure_ascii=False)
            if key in seen:
                continue
            seen.add(key)
            unique.append(item)
        unique.sort(key=lambda row: json.dumps(row, sort_keys=True, ensure_ascii=False))
        return unique

    rows = (await db.execute(text("""
        SELECT
            pc.id,
            pc.compatible_vehicles,
            pvf.manufacturer,
            pvf.model,
            pvf.year_from,
            pvf.year_to,
            pvf.engine_type
        FROM parts_catalog pc
        JOIN part_vehicle_fitment pvf
          ON pvf.part_id = pc.id
        WHERE pvf.manufacturer IS NOT NULL
          AND TRIM(pvf.manufacturer) <> ''
          AND pvf.model IS NOT NULL
          AND TRIM(pvf.model) <> ''
    """))).fetchall()

    part_existing: Dict[str, List[Dict[str, Any]]] = {}
    part_fitments: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    scanned_rows = 0

    for part_id, compat, manufacturer, model, year_from, year_to, engine_type in rows:
        scanned_rows += 1
        pid = str(part_id)
        if pid not in part_existing:
            part_existing[pid] = list(compat or []) if isinstance(compat, list) else []

        canonical_manufacturer = normalize_manufacturer_name(str(manufacturer or ""), str(manufacturer or ""))
        canonical_model = canonicalize_vehicle_model_for_manufacturer(canonical_manufacturer, model)
        if not canonical_manufacturer or not canonical_model:
            continue

        fitment: Dict[str, Any] = {
            "manufacturer": canonical_manufacturer,
            "model": canonical_model,
            "source": "part_vehicle_fitment",
        }

        if engine_type:
            fitment["engine"] = str(engine_type).strip()[:50]

        try:
            yf = int(year_from or 0)
        except Exception:
            yf = 0
        try:
            yt = int(year_to or 0)
        except Exception:
            yt = 0
        if yf and not yt:
            yt = yf
        if yf and yt and 1990 <= yf <= yt <= 2027:
            fitment["year_from"] = yf
            fitment["year_to"] = yt

        part_fitments[pid].append(fitment)

    updated_parts = 0
    merged_fitment_rows = 0

    for part_id, entries in part_fitments.items():
        preserved = [
            item for item in part_existing.get(part_id, [])
            if not (isinstance(item, dict) and item.get("source") == "part_vehicle_fitment")
        ]
        merged = _normalize_fitment_json_list(preserved + entries)

        existing_json = json.dumps(
            _normalize_fitment_json_list(list(part_existing.get(part_id, []))),
            sort_keys=True,
            ensure_ascii=False,
        )
        merged_json = json.dumps(merged, sort_keys=True, ensure_ascii=False)
        if existing_json == merged_json:
            continue

        await db.execute(text("""
            UPDATE parts_catalog
            SET compatible_vehicles = CAST(:compat AS jsonb),
                updated_at = NOW()
            WHERE id = CAST(:part_id AS uuid)
        """), {
            "part_id": part_id,
            "compat": json.dumps(merged, ensure_ascii=False),
        })
        updated_parts += 1
        merged_fitment_rows += len(entries)

    await db.commit()
    return {
        "task": "merge_catalog_fitment_from_part_vehicle_fitment",
        "status": "ok",
        "scanned_rows": scanned_rows,
        "parts_with_fitment": len(part_fitments),
        "updated_parts": updated_parts,
        "merged_fitment_rows": merged_fitment_rows,
        "elapsed_s": round(time.monotonic() - t0, 2),
    }


# =========================================================================
# Task 8 – Refresh min / max prices on parts_catalog
# =========================================================================

async def refresh_min_max_prices(db: AsyncSession) -> Dict[str, Any]:
    """
    Recalculate parts_catalog.min_price_ils / max_price_ils from live
    supplier_parts prices (WITH 18% VAT applied).
    """
    t0 = time.monotonic()

    try:
        await db.execute(
            text(
                """
                WITH price_agg AS (
                    SELECT
                        part_id,
                        MIN(COALESCE(price_ils, price_usd * :rate)) * :vat AS min_p,
                        MAX(COALESCE(price_ils, price_usd * :rate)) * :vat AS max_p
                    FROM supplier_parts
                    WHERE is_available = TRUE
                    GROUP BY part_id
                )
                UPDATE parts_catalog pc
                SET
                    min_price_ils = pa.min_p,
                    max_price_ils = pa.max_p,
                    updated_at   = NOW()
                FROM price_agg pa
                WHERE pc.id = pa.part_id
                """
            ),
            {"rate": await _get_ils_rate(db), "vat": 1 + VAT},
        )
        await db.commit()
        logger.info("refresh_min_max_prices: done")
    except Exception as exc:
        await db.rollback()
        logger.error("refresh_min_max_prices failed: %s", exc)
        return {"task": "refresh_min_max_prices", "status": "error", "error": str(exc)}

    return {
        "task": "refresh_min_max_prices",
        "status": "ok",
        "elapsed_s": round(time.monotonic() - t0, 2),
    }


# =========================================================================
# Task 9 – Seed system_settings
# =========================================================================

async def seed_system_settings(db: AsyncSession) -> Dict[str, Any]:
    """
    Ensure required system_settings keys exist (insert if missing, never
    overwrite existing values).
    """
    DEFAULTS = {
        "search_results_per_type": "4",
        "search_type_order": "original,oem,aftermarket",
        "ils_per_usd": str(ILS_PER_USD),
        "vat_rate": "0.18",
    }
    t0 = time.monotonic()
    inserted = 0

    try:
        for key, value in DEFAULTS.items():
            result = await db.execute(
                text("SELECT 1 FROM system_settings WHERE key = :key"),
                {"key": key},
            )
            if not result.fetchone():
                await db.execute(
                    text(
                        "INSERT INTO system_settings (id, key, value, updated_at) "
                        "VALUES (gen_random_uuid(), :key, :value, NOW())"
                    ),
                    {"key": key, "value": value},
                )
                inserted += 1

        await db.commit()
        logger.info("seed_system_settings: inserted=%d", inserted)
    except Exception as exc:
        await db.rollback()
        logger.error("seed_system_settings failed: %s", exc)
        return {"task": "seed_system_settings", "status": "error", "error": str(exc)}

    return {
        "task": "seed_system_settings",
        "status": "ok",
        "inserted": inserted,
        "elapsed_s": round(time.monotonic() - t0, 2),
    }


# =========================================================================
# Orchestrator – run_all_tasks
# =========================================================================

async def _enrich_pending_parts_task(db: AsyncSession) -> Dict[str, Any]:
    """Thin wrapper so enrich_pending_parts integrates with TASK_REGISTRY."""
    from ai_catalog_builder import enrich_pending_parts
    return await enrich_pending_parts(db, limit=100)


async def _trigger_scraper_for_misses_task(db: AsyncSession) -> Dict[str, Any]:
    """Find high-frequency zero-result queries (miss_count >= 3, not yet triggered)
    and fire REX brand discovery for the likely brand."""
    from catalog_scraper import run_brand_discovery

    try:
        rows = (await db.execute(
            text("""
                SELECT id, query, normalized_query, vehicle_manufacturer
                FROM search_misses
                WHERE miss_count >= 3
                  AND triggered_scrape = FALSE
                ORDER BY miss_count DESC
                LIMIT 20
            """)
        )).fetchall()
    except Exception as exc:
        if "search_misses" in str(exc).lower() and "does not exist" in str(exc).lower():
            return {
                "task": "trigger_scraper_for_misses",
                "status": "skipped",
                "reason": "search_misses_table_missing",
                "triggered": 0,
                "errors": 0,
            }
        raise

    if not rows:
        return {"task": "trigger_scraper_for_misses", "status": "ok", "triggered": 0, "errors": 0}

    triggered = 0
    errors = 0
    triggered_ids = []

    for row in rows:
        # Prefer explicit vehicle_manufacturer; fall back to first token of query
        brand = (row.vehicle_manufacturer or "").strip()
        if not brand:
            first_token = (row.normalized_query or "").split()
            brand = first_token[0] if first_token else ""
        if not brand:
            continue

        try:
            asyncio.create_task(run_brand_discovery(brands=[brand]))
            triggered += 1
            triggered_ids.append(str(row.id))
        except Exception as e:
            logger.warning("trigger_scraper_for_misses: brand=%s error=%s", brand, e)
            errors += 1

    if triggered_ids:
        for _tid in triggered_ids:
            await db.execute(
                text("UPDATE search_misses SET triggered_scrape = TRUE WHERE id = :tid"),
                {"tid": _tid},
            )
        await db.commit()

    return {
        "task": "trigger_scraper_for_misses",
        "status": "ok",
        "triggered": triggered,
        "errors": errors,
    }


async def _trigger_scraper_for_registry_gaps_task(db: AsyncSession) -> Dict[str, Any]:
    """Queue brand discovery for all known manufacturers under-covered in parts_catalog.

    Pool includes:
    - active car_brands
    - active truck_brands
    - active manufacturers already present in parts_catalog

    We queue only a bounded batch per run for speed + operational safety.
    """
    from catalog_scraper import run_brand_discovery

    target = max(1, int(os.getenv("DISCOVERY_TARGET", "120")))
    per_run = min(50, max(1, int(os.getenv("DISCOVERY_PER_RUN", "20"))))
    truck_delay_days = max(0, int(os.getenv("DISCOVERY_TRUCK_DELAY_DAYS", "30")))

    include_trucks = False
    trucks_deferred_reason = "deploy_timestamp_missing"
    deployed_at_iso = (os.getenv("DEPLOYED_AT_ISO", "") or "").strip()
    trucks_unlock_at = None
    if deployed_at_iso:
        try:
            _deploy_ts = datetime.fromisoformat(deployed_at_iso.replace("Z", "+00:00"))
            if _deploy_ts.tzinfo is None:
                _deploy_ts = _deploy_ts.replace(tzinfo=timezone.utc)
            trucks_unlock_at = _deploy_ts + timedelta(days=truck_delay_days)
            include_trucks = datetime.now(timezone.utc) >= trucks_unlock_at
            trucks_deferred_reason = "within_truck_delay_window" if not include_trucks else "delay_window_completed"
        except Exception:
            include_trucks = False
            trucks_deferred_reason = "invalid_deploy_timestamp"

    rows = (await db.execute(
        text(
            """
            WITH manufacturer_pool AS (
                SELECT name
                FROM car_brands
                WHERE is_active = TRUE

                UNION

                SELECT name
                FROM truck_brands
                                WHERE is_active = TRUE
                                    AND :include_trucks = TRUE

                UNION

                SELECT DISTINCT TRIM(manufacturer) AS name
                FROM parts_catalog
                WHERE is_active = TRUE
                  AND manufacturer IS NOT NULL
                  AND TRIM(manufacturer) <> ''
            ),
            part_counts AS (
                SELECT LOWER(TRIM(manufacturer)) AS mkey, COUNT(*) AS cnt
                FROM parts_catalog
                WHERE is_active = TRUE
                  AND manufacturer IS NOT NULL
                GROUP BY LOWER(TRIM(manufacturer))
            )
                        SELECT mp.name, COALESCE(pc.cnt, 0) AS part_count
                        FROM manufacturer_pool mp
            LEFT JOIN part_counts pc
                            ON LOWER(TRIM(mp.name)) = pc.mkey
            WHERE COALESCE(pc.cnt, 0) < :target
                            AND mp.name IS NOT NULL
                            AND TRIM(mp.name) <> ''
                        ORDER BY COALESCE(pc.cnt, 0) ASC, mp.name ASC
            LIMIT :lim
            """
        ),
        {
            "target": target,
            "lim": per_run,
            "include_trucks": include_trucks,
        },
    )).fetchall()

    if not rows:
        return {
            "task": "trigger_scraper_for_registry_gaps",
            "status": "ok",
            "triggered": 0,
            "target": target,
            "per_run": per_run,
            "brands": [],
            "include_trucks": include_trucks,
            "trucks_deferred": not include_trucks,
            "trucks_deferred_reason": trucks_deferred_reason,
            "truck_delay_days": truck_delay_days,
            "deployed_at_iso": deployed_at_iso or None,
            "trucks_unlock_at": trucks_unlock_at.isoformat() if trucks_unlock_at else None,
            "reason": "no_undercovered_brands",
        }

    brands = [r[0] for r in rows if r[0]]
    if not brands:
        return {
            "task": "trigger_scraper_for_registry_gaps",
            "status": "ok",
            "triggered": 0,
            "target": target,
            "per_run": per_run,
            "brands": [],
            "include_trucks": include_trucks,
            "trucks_deferred": not include_trucks,
            "trucks_deferred_reason": trucks_deferred_reason,
            "truck_delay_days": truck_delay_days,
            "deployed_at_iso": deployed_at_iso or None,
            "trucks_unlock_at": trucks_unlock_at.isoformat() if trucks_unlock_at else None,
            "reason": "no_valid_brand_names",
        }

    asyncio.create_task(run_brand_discovery(brands=brands, target=target, per_run=per_run))

    return {
        "task": "trigger_scraper_for_registry_gaps",
        "status": "ok",
        "triggered": len(brands),
        "target": target,
        "per_run": per_run,
        "include_trucks": include_trucks,
        "trucks_deferred": not include_trucks,
        "trucks_deferred_reason": trucks_deferred_reason,
        "truck_delay_days": truck_delay_days,
        "deployed_at_iso": deployed_at_iso or None,
        "trucks_unlock_at": trucks_unlock_at.isoformat() if trucks_unlock_at else None,
        "brands": brands,
        "brand_counts": [{"name": r[0], "part_count": int(r[1] or 0)} for r in rows],
    }


async def _run_image_embedding_batch(rows: list) -> None:
    """Background worker: fetch image bytes, embed via CLIP, write vector to DB.
    Always launched via asyncio.create_task() — never awaited directly."""
    import base64
    from BACKEND_DATABASE_MODELS import async_session_factory as _sf
    from hf_client import hf_clip
    ok = 0
    async with httpx.AsyncClient() as client:
        for row in rows:
            try:
                r = await client.get(row.url, timeout=15.0, follow_redirects=True)
                r.raise_for_status()
                b64 = base64.b64encode(r.content).decode()
                vec = await hf_clip(b64, timeout=30.0)
                async with _sf() as db:
                    await db.execute(
                        text("UPDATE parts_catalog SET image_embedding = CAST(:v AS vector) WHERE id = :id"),
                        {"v": str(vec), "id": str(row.part_id)},
                    )
                    await db.execute(
                        text("UPDATE parts_images SET embedding_generated = TRUE WHERE id = :id"),
                        {"id": str(row.id)},
                    )
                    await db.commit()
                ok += 1
            except Exception as e:
                logger.warning("_run_image_embedding_batch: %s → %s", row.url[:80], e)
    logger.info("_run_image_embedding_batch: %d/%d embedded", ok, len(rows))


async def _generate_image_embeddings_task(db: AsyncSession) -> Dict[str, Any]:
    """Check for parts_images rows pending CLIP embedding; fire background batch.
    Returns immediately without blocking run_all_tasks."""
    if not os.getenv("HF_TOKEN", ""):
        return {"task": "generate_image_embeddings", "status": "ok", "triggered": 0, "note": "HF_TOKEN not set"}

    rows = (await db.execute(
        text("""
            SELECT id, part_id, url
            FROM parts_images
            WHERE embedding_generated = FALSE
              AND url IS NOT NULL
            ORDER BY is_primary DESC, created_at
            LIMIT 20
        """)
    )).fetchall()

    if not rows:
        return {"task": "generate_image_embeddings", "status": "ok", "triggered": 0}

    asyncio.create_task(_run_image_embedding_batch(rows))
    return {"task": "generate_image_embeddings", "status": "ok", "triggered": len(rows)}


async def dedup_catalog_parts(db: AsyncSession) -> Dict[str, Any]:
    """
        Remove duplicate rows in parts_catalog:
            1. Same SKU → null out the older duplicate's SKU (keep newest).
            2. Same (name, manufacturer key) → flag older rows with needs_oem_lookup=True for manual review.
                 Manufacturer key prefers manufacturer_id when the column exists, else falls back to manufacturer text.
    Both steps are idempotent.
    """
    t0 = time.monotonic()
    nulled_skus = 0
    flagged_dupes = 0

    try:
        # Step 1 — duplicate SKUs: null out older row's sku
        r1 = await db.execute(text("""
            WITH dupes AS (
                SELECT id FROM (
                    SELECT id,
                           ROW_NUMBER() OVER (PARTITION BY sku ORDER BY created_at DESC) AS rn
                    FROM parts_catalog
                    WHERE sku IS NOT NULL
                ) ranked
                WHERE rn > 1
            )
            UPDATE parts_catalog SET sku = NULL, updated_at = NOW()
            FROM dupes WHERE parts_catalog.id = dupes.id
            RETURNING parts_catalog.id
        """))
        nulled_skus = len(r1.fetchall())

        has_manufacturer_id = bool((await db.execute(text("""
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_name = 'parts_catalog'
                  AND column_name = 'manufacturer_id'
            )
        """))).scalar())

        # Step 2 — duplicate (name, manufacturer key): flag older rows for review
        if has_manufacturer_id:
            manufacturer_partition = "manufacturer_id"
            manufacturer_filter = "manufacturer_id IS NOT NULL"
        else:
            manufacturer_partition = "lower(COALESCE(manufacturer, ''))"
            manufacturer_filter = "manufacturer IS NOT NULL"

        r2 = await db.execute(text(f"""
            WITH dupes AS (
                SELECT id FROM (
                    SELECT id,
                           ROW_NUMBER() OVER (
                               PARTITION BY lower(name), {manufacturer_partition}
                               ORDER BY created_at DESC
                           ) AS rn
                    FROM parts_catalog
                    WHERE name IS NOT NULL AND {manufacturer_filter}
                ) ranked
                WHERE rn > 1
            )
            UPDATE parts_catalog SET needs_oem_lookup = TRUE, updated_at = NOW()
            FROM dupes WHERE parts_catalog.id = dupes.id
            RETURNING parts_catalog.id
        """))
        flagged_dupes = len(r2.fetchall())

        await db.commit()
        logger.info("dedup_catalog_parts: nulled_skus=%d flagged_dupes=%d", nulled_skus, flagged_dupes)

    except Exception as exc:
        await db.rollback()
        logger.error("dedup_catalog_parts failed: %s", exc)
        return {"task": "dedup_catalog_parts", "status": "error", "error": str(exc)}

    return {
        "task": "dedup_catalog_parts",
        "status": "ok",
        "nulled_skus": nulled_skus,
        "flagged_dupes": flagged_dupes,
        "elapsed_s": round(time.monotonic() - t0, 2),
    }



# ---------------------------------------------------------------------------
# Task: populate_supplier_parts
# Links every active part to every active supplier with correct pricing.
# Safe to re-run: uses ON CONFLICT DO NOTHING.
# Call via: POST /api/v1/admin/db-agent/run/populate_supplier_parts
# ---------------------------------------------------------------------------

#  (supplier_name, sku_prefix, price_multiplier, ship_ils, ship_usd, transit_days, avail, is_avail)
_UNIVERSAL_SUPPLIERS = [
    ("AutoParts Pro IL", "IL",  1.00,   0.0,  0.0,  3, "in_stock", True),
    ("Global Parts Hub", "DE",  1.10,  93.0, 25.0, 10, "on_order", False),
    ("EastAuto Supply",  "CN",  0.85, 130.0, 35.0, 21, "on_order", False),
    ("PartsPro USA",     "US1", 1.05, 110.0, 30.0, 12, "on_order", False),
    ("AutoZone Direct",  "US2", 1.15, 120.0, 33.0, 14, "on_order", False),
]
_MANUFACTURER_SUPPLIERS = [
    ("Hyundai Mobis",    "KR1", 0.95, 95.0, 26.0,  8, "on_order", False),
    ("Kia Parts Direct", "KR2", 0.95, 95.0, 26.0,  8, "on_order", False),
    ("Bosch Direct",     "DE2", 1.00, 80.0, 22.0,  7, "on_order", False),
    ("Toyota Genuine",   "JP",  1.05, 99.0, 27.0, 10, "on_order", False),
]
_CATEGORY_FALLBACK_ILS: Dict[str, float] = {
    "גוף ואקסטריור": 1206, "כללי": 1320, "מתלים והגה": 1178, "מנוע": 1522,
    "חשמל": 862, "בלמים": 648, "מערכת דלק": 909, "מסננים ושמנים": 958,
    "אטמים וחומרים": 302, "תאורה": 1560, "מיזוג ומערכת חימום": 997,
    "גלגלים וצמיגים": 889, "פנים הרכב": 1224, "תיבת הילוכים": 2398,
    "קירור": 1027, "מערכת פליטה": 2365, "סרן והינע": 891,
    "מגבים": 446, "שרשראות ורצועות": 429, "כלים וציוד": 350,
}
_WARRANTY_MAP = {"Original": 24, "OEM": 24, "Aftermarket": 12, "Refurbished": 6}
_BATCH = 5_000
_DEFAULT_PRICE = 800.0


async def _populate_supplier_parts_task(db: AsyncSession) -> Dict[str, Any]:
    """
    Link every active part to every active supplier with computed pricing.
    Idempotent — uses ON CONFLICT DO NOTHING on (supplier_id, part_id).
    Heavy operation; only runs on demand via the admin API, not in run_all_tasks.
    """
    import json
    import uuid as _uuid

    t0 = time.monotonic()

    # ── Load active suppliers ────────────────────────────────────────────────
    rows = (await db.execute(text(
        "SELECT id, name, is_manufacturer, manufacturer_name "
        "FROM suppliers WHERE is_active = TRUE ORDER BY priority"
    ))).mappings().fetchall()
    suppliers: Dict[str, Any] = {r["name"]: dict(r) for r in rows}

    if not suppliers:
        return {"task": "populate_supplier_parts", "status": "error",
                "error": "No active suppliers found — run seed_data.py first"}

    ils_rate: float = ILS_PER_USD  # from module-level constant (overridden by settings)
    total_inserted = 0

    async def _insert_batch(records: list) -> int:
        if not records:
            return 0
        payload = json.dumps(records)
        result = await db.execute(text("""
            INSERT INTO supplier_parts (
                id, supplier_id, part_id, supplier_sku,
                price_usd, price_ils, shipping_cost_usd, shipping_cost_ils,
                availability, warranty_months, estimated_delivery_days,
                is_available, last_checked_at, created_at
            )
            SELECT
                CAST(j->>'id' AS UUID),
                CAST(j->>'supplier_id' AS UUID),
                CAST(j->>'part_id' AS UUID),
                j->>'supplier_sku',
                CAST(j->>'price_usd' AS NUMERIC),
                CAST(j->>'price_ils' AS NUMERIC),
                CAST(j->>'shipping_cost_usd' AS NUMERIC),
                CAST(j->>'shipping_cost_ils' AS NUMERIC),
                j->>'availability',
                CAST(j->>'warranty_months' AS INT),
                CAST(j->>'estimated_delivery_days' AS INT),
                CAST(j->>'is_available' AS BOOLEAN),
                NOW(), NOW()
            FROM json_array_elements(:payload::json) AS j
            ON CONFLICT (supplier_id, part_id) DO NOTHING
        """), {"payload": payload})
        await db.commit()
        return result.rowcount if result.rowcount and result.rowcount > 0 else len(records)

    # ── Pass 1: Universal suppliers → all active parts ───────────────────────
    offset = 0
    while True:
        parts = (await db.execute(text(
            "SELECT id, category, part_type, base_price FROM parts_catalog "
            "WHERE is_active = TRUE ORDER BY id OFFSET :o LIMIT :l"
        ), {"o": offset, "l": _BATCH})).mappings().fetchall()
        if not parts:
            break

        records: list = []
        for part in parts:
            part_id = str(part["id"])
            base = float(part["base_price"] or 0)
            if base <= 1.0:
                base = _CATEGORY_FALLBACK_ILS.get(part["category"] or "כללי", _DEFAULT_PRICE)
            warranty = _WARRANTY_MAP.get(part["part_type"] or "", 12)

            for (s_name, prefix, mult, s_ils, s_usd, days, avail, is_av) in _UNIVERSAL_SUPPLIERS:
                if s_name not in suppliers:
                    continue
                price = round(base * mult, 2)
                records.append({
                    "id": str(_uuid.uuid4()),
                    "supplier_id": str(suppliers[s_name]["id"]),
                    "part_id": part_id,
                    "supplier_sku": f"{prefix}-{part_id}",
                    "price_usd": round(price / ils_rate, 2),
                    "price_ils": price,
                    "shipping_cost_usd": s_usd,
                    "shipping_cost_ils": s_ils,
                    "availability": avail,
                    "warranty_months": warranty,
                    "estimated_delivery_days": days,
                    "is_available": is_av,
                })

        total_inserted += await _insert_batch(records)
        offset += _BATCH

    # ── Pass 2: Manufacturer-direct suppliers → filtered by manufacturer ──────
    for (s_name, prefix, mult, s_ils, s_usd, days, avail, is_av) in _MANUFACTURER_SUPPLIERS:
        if s_name not in suppliers:
            continue
        sup = suppliers[s_name]
        mfr = sup.get("manufacturer_name") if sup.get("is_manufacturer") else None
        where = "AND LOWER(manufacturer) = LOWER(:mfr)" if mfr else ""
        params_base: Dict[str, Any] = {"mfr": mfr} if mfr else {}

        offset = 0
        while True:
            parts = (await db.execute(text(
                f"SELECT id, category, part_type, base_price FROM parts_catalog "
                f"WHERE is_active = TRUE {where} ORDER BY id OFFSET :o LIMIT :l"
            ), {**params_base, "o": offset, "l": _BATCH})).mappings().fetchall()
            if not parts:
                break

            records = []
            for part in parts:
                part_id = str(part["id"])
                base = float(part["base_price"] or 0)
                if base <= 1.0:
                    base = _CATEGORY_FALLBACK_ILS.get(part["category"] or "כללי", _DEFAULT_PRICE)
                warranty = _WARRANTY_MAP.get(part["part_type"] or "", 12)
                price = round(base * mult, 2)
                records.append({
                    "id": str(_uuid.uuid4()),
                    "supplier_id": str(sup["id"]),
                    "part_id": part_id,
                    "supplier_sku": f"{prefix}-{part_id}",
                    "price_usd": round(price / ils_rate, 2),
                    "price_ils": price,
                    "shipping_cost_usd": s_usd,
                    "shipping_cost_ils": s_ils,
                    "availability": avail,
                    "warranty_months": warranty,
                    "estimated_delivery_days": days,
                    "is_available": is_av,
                })

            total_inserted += await _insert_batch(records)
            offset += _BATCH

    # ── Final count ──────────────────────────────────────────────────────────
    total_rows = (await db.execute(text("SELECT COUNT(*) FROM supplier_parts"))).scalar_one()
    parts_covered = (await db.execute(text(
        "SELECT COUNT(*) FROM (SELECT part_id FROM supplier_parts "
        "GROUP BY part_id HAVING COUNT(DISTINCT supplier_id) >= 5) t"
    ))).scalar_one()

    logger.info("populate_supplier_parts: inserted=%d total_rows=%d", total_inserted, total_rows)
    return {
        "task": "populate_supplier_parts",
        "status": "ok",
        "inserted": total_inserted,
        "total_supplier_parts_rows": total_rows,
        "parts_with_5plus_suppliers": parts_covered,
        "elapsed_s": round(time.monotonic() - t0, 2),
    }


# ---------------------------------------------------------------------------
# Task: validate_migrations
# Pre-flight safety check for Alembic migration files.
# Call via: POST /api/v1/admin/db-agent/run/validate_migrations
# ---------------------------------------------------------------------------

import re as _re
from pathlib import Path as _Path


def _check_migration_file(path: _Path) -> Tuple[bool, List[str], List[str]]:
    """
    Scan a single Alembic migration file for unsafe patterns.
    Returns (is_safe, errors, warnings).
    """
    errors: List[str] = []
    warnings: List[str] = []

    content = path.read_text()
    m = _re.search(r"def upgrade\(\):(.*?)(?=def downgrade|$)", content, _re.DOTALL)
    if not m:
        errors.append(f"{path.name}: no upgrade() function found")
        return False, errors, warnings

    code = m.group(1)

    # 1. NOT NULL column without server_default
    for match in _re.finditer(r"sa\.Column\([^)]*nullable=False[^)]*\)", code):
        start = code.rfind("\n", 0, match.start()) + 1
        line = code[start:code.find("\n", match.end())].strip()
        if "server_default" not in line:
            errors.append(f"{path.name}: NOT NULL without server_default → {line[:120]}")

    # 2. Column drops (warn — app code may still reference)
    for match in _re.finditer(r"op\.drop_column\(", code):
        start = code.rfind("\n", 0, match.start()) + 1
        line = code[start:code.find("\n", match.end())].strip()
        warnings.append(f"{path.name}: column drop — verify no live references → {line[:120]}")

    # 3. Table renames (warn)
    if _re.search(r"op\.rename_table\(", code):
        warnings.append(f"{path.name}: table rename — ensure compatibility views exist")

    # 4. Type change without VARCHAR intermediate (warn)
    for match in _re.finditer(r"op\.alter_column\([^,]+,\s*type_=", code):
        start = code.rfind("\n", 0, match.start()) + 1
        line = code[start:code.find("\n", match.end())].strip()
        if "VARCHAR" not in line and "String" not in line:
            warnings.append(f"{path.name}: type change without VARCHAR step → {line[:120]}")

    return len(errors) == 0, errors, warnings


async def _validate_migrations_task(db: AsyncSession) -> Dict[str, Any]:
    """
    Scan all Alembic migration files in both catalog and PII directories
    for patterns that could cause downtime on production deployment.
    Safe read-only operation; does not touch the database.
    """
    import asyncio as _asyncio

    base = _Path(__file__).parent
    dirs = {
        "catalog": base / "alembic" / "versions",
        "pii":     base / "alembic_pii" / "versions",
    }

    all_errors: List[str] = []
    all_warnings: List[str] = []
    files_checked = 0
    files_failed = 0

    def _scan_dirs() -> Tuple[int, int, List[str], List[str]]:
        _checked = 0
        _failed = 0
        _errors: List[str] = []
        _warnings: List[str] = []
        for label, mdir in dirs.items():
            if not mdir.exists():
                _warnings.append(f"{label}: directory not found ({mdir})")
                continue
            for mfile in sorted(mdir.glob("*.py")):
                if mfile.name.startswith("__"):
                    continue
                _checked += 1
                safe, errs, warns = _check_migration_file(mfile)
                _errors.extend(errs)
                _warnings.extend(warns)
                if not safe:
                    _failed += 1
        return _checked, _failed, _errors, _warnings

    files_checked, files_failed, all_errors, all_warnings = await asyncio.to_thread(_scan_dirs)

    status = "ok" if files_failed == 0 else "unsafe"
    logger.info(
        "validate_migrations: checked=%d failed=%d errors=%d warnings=%d",
        files_checked, files_failed, len(all_errors), len(all_warnings),
    )
    return {
        "task": "validate_migrations",
        "status": status,
        "files_checked": files_checked,
        "files_failed": files_failed,
        "errors": all_errors,
        "warnings": all_warnings,
    }


TASK_REGISTRY: Dict[str, Any] = {
    "clean_part_names":          clean_part_names,
    "normalize_part_types":      normalize_part_types,
    "normalize_categories":      normalize_categories,
    "dedup_catalog_parts":       dedup_catalog_parts,
    "normalize_availability":    normalize_availability,
    "fix_base_prices":           fix_base_prices,
    "flag_fake_skus":            flag_fake_skus,
    "fill_car_brands":           fill_car_brands,
    "normalize_imported_manufacturers": normalize_imported_manufacturers,
    "sync_models_from_catalog": sync_models_from_catalog,
    "sync_models_from_catalog_file": sync_models_from_catalog_file,
    "backfill_catalog_fitment_from_xls": backfill_catalog_fitment_from_xls,
    "merge_catalog_fitment_from_part_vehicle_fitment": merge_catalog_fitment_from_part_vehicle_fitment,
    "sync_manufacturer_registries": sync_manufacturer_registries,
    "refresh_min_max_prices":    refresh_min_max_prices,
    "seed_system_settings":      seed_system_settings,
    "enrich_pending_parts":      _enrich_pending_parts_task,
    "trigger_scraper_for_registry_gaps": _trigger_scraper_for_registry_gaps_task,
    "trigger_scraper_for_misses": _trigger_scraper_for_misses_task,
    "generate_image_embeddings": _generate_image_embeddings_task,
    # on-demand heavy tasks — NOT included in run_all_tasks
    "populate_supplier_parts":   _populate_supplier_parts_task,
    "validate_migrations":       _validate_migrations_task,
}


async def run_task(task_name: str, db: AsyncSession) -> Dict[str, Any]:
    """Run a single named task and return its report dict."""
    fn = TASK_REGISTRY.get(task_name)
    if fn is None:
        return {
            "task": task_name,
            "status": "error",
            "error": f"Unknown task '{task_name}'. "
                     f"Valid tasks: {list(TASK_REGISTRY.keys())}",
        }
    return await fn(db)


async def run_all_tasks(db: AsyncSession) -> Dict[str, Any]:
    """
    Run all cleaning + normalisation tasks in the recommended order.
    Returns a summary report dict.
    """
    from BACKEND_AUTH_SECURITY import get_redis
    from distributed_lock import acquire_lock
    _agent_lock = await acquire_lock(await get_redis(), "db_update_agent", ttl_seconds=21600)
    if not _agent_lock:
        return {"status": "skipped", "reason": "db_update_agent already running on another worker",
                "tasks_ok": 0, "tasks_error": 0}
    global _agent_running, _last_report
    _agent_running = True
    started_at = datetime.now(timezone.utc).isoformat()
    t0 = time.monotonic()
    results: List[Dict[str, Any]] = []
    job_id: Optional[str] = None
    try:
        try:
            job_id = await job_registry_start(db, "run_all_tasks", ttl_seconds=21600)
        except Exception as exc:
            logger.warning("run_all_tasks job_registry_start failed: %s", exc)
            try:
                await db.rollback()
            except Exception:
                pass

        ordered_tasks = [
            "seed_system_settings",
            "fill_car_brands",
            "normalize_imported_manufacturers",
            "sync_models_from_catalog",
            "sync_models_from_catalog_file",
            "backfill_catalog_fitment_from_xls",
            "merge_catalog_fitment_from_part_vehicle_fitment",
            "sync_manufacturer_registries",
            "trigger_scraper_for_registry_gaps",
            "clean_part_names",
            "normalize_part_types",
            "normalize_categories",
            "dedup_catalog_parts",
            "normalize_availability",
            "flag_fake_skus",
            "fix_base_prices",
            "refresh_min_max_prices",
            "enrich_pending_parts",
            "trigger_scraper_for_misses",
            "generate_image_embeddings",
        ]

        for task_name in ordered_tasks:
            logger.info("run_all_tasks → starting: %s", task_name)
            result = await run_task(task_name, db)
            results.append(result)
            if result.get("status") == "error":
                logger.warning("run_all_tasks: task %s errored, continuing", task_name)

        total_elapsed = round(time.monotonic() - t0, 2)
        ok_count = sum(1 for r in results if r.get("status") == "ok")
        err_count = len(results) - ok_count

        report = {
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "total_elapsed_s": total_elapsed,
            "tasks_ok": ok_count,
            "tasks_error": err_count,
            "results": results,
        }

        _last_report = report
        _agent_running = False
        logger.info(
            "run_all_tasks finished: ok=%d err=%d elapsed=%.1fs",
            ok_count,
            err_count,
            total_elapsed,
        )
        if job_id:
            try:
                await job_registry_finish(db, job_id, status="completed")
            except Exception as exc:
                logger.warning("run_all_tasks job_registry_finish failed: %s", exc)
        await _agent_lock.release()
        return report

    except Exception as exc:
        if job_id:
            try:
                await job_registry_finish(db, job_id, status="dead", error_message=str(exc)[:500])
            except Exception:
                pass
        _agent_running = False
        await _agent_lock.release()
        raise


def get_last_report() -> Dict[str, Any]:
    """Return the last run report (or empty dict if never run)."""
    return _last_report


def is_running() -> bool:
    return _agent_running


# =========================================================================
# Optional background loop
# =========================================================================

_bg_task: Optional[asyncio.Task] = None


async def _agent_loop(get_db_fn, interval_hours: float = 6.0) -> None:
    """Periodic background loop.  Runs run_all_tasks every `interval_hours`."""
    from resilience import log_job_failure
    logger.info(
        "DB update agent background loop started (interval=%.1fh)", interval_hours
    )
    while True:
        try:
            async for db in get_db_fn():
                await run_all_tasks(db)
        except Exception as exc:
            error_msg = str(exc)[:500]
            logger.error("DB update agent loop error: %s", error_msg)
            # Log failure to DLQ (Gap 2b)
            try:
                # Import get_pii_db to access PII database for logging
                from BACKEND_DATABASE_MODELS import pii_session_factory
                async with pii_session_factory() as pii_db:
                    await log_job_failure(
                        pii_db,
                        job_name="run_all_tasks",
                        error=error_msg,
                        payload={},
                        attempts=1,
                    )
            except Exception as dlq_err:
                logger.error("Failed to log run_all_tasks to DLQ: %s", dlq_err)

        await asyncio.sleep(interval_hours * 3600)


def start_agent_task(get_db_fn, interval_hours: float = 6.0) -> None:
    """
    Call this from the FastAPI startup event to enable the periodic loop.
    ``get_db_fn`` should be the same ``get_db`` dependency used in routes.
    """
    global _bg_task
    _bg_task = asyncio.create_task(
        _agent_loop(get_db_fn, interval_hours),
        name="db_update_agent",
    )
    logger.info("DB update agent background task created")
