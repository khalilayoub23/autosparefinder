"""
DB Update Agent — backend/db_update_agent.py

Runs a set of autonomous cleaning / normalisation tasks against the catalogue DB.
Each task is idempotent: re-running it is always safe.

Tasks
-----
1. clean_part_names          – strip trailing car-model suffixes from part names
2. normalize_part_types      – unify to "Original" / "OEM" / "Aftermarket"
3. normalize_categories      – map variants to 14 canonical Hebrew categories
4. normalize_availability    – unify to "in_stock" / "out_of_stock" / "on_order"
5. fix_base_prices           – ensure base_price = supplier min + 17 % VAT markup
6. flag_fake_skus            – set needs_oem_lookup=True for auto-generated SKUs
7. fill_car_brands           – seed il_importer / warranty_* for known makes
8. run_all_tasks             – orchestrator that runs 1-7 and returns a report dict

Admin endpoints call run_all_tasks or individual tasks through get_db.
run_agent_background_loop()  – optional periodic loop (disabled by default).
"""
# DATA QUALITY PIPELINE OWNER: DB Update Agent — normalises and enriches parts_catalog

from __future__ import annotations

import asyncio
import httpx
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from resilience import job_registry_start, job_registry_finish

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
    try:
        result = await db.execute(
            text("SELECT value FROM system_settings WHERE key = 'ils_per_usd' LIMIT 1")
        )
        row = result.fetchone()
        if row:
            return float(row[0])
    except Exception:
        pass
    return ILS_PER_USD


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
            col = "part_type"
            result = await db.execute(
                text(f"SELECT id, {col} FROM {table} WHERE {col} IS NOT NULL")
            )
            rows = result.fetchall()
            for row_id, raw_type in rows:
                canonical = _normalize_part_type(raw_type)
                if canonical and canonical != raw_type:
                    await db.execute(
                        text(
                            f"UPDATE {table} SET {col} = :val, updated_at = NOW() "
                            "WHERE id = :id"
                        ),
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
    Ensure parts_catalog.base_price (incl. 17 % VAT) is not below the
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
        await db.execute(
            text("""
                UPDATE search_misses
                SET triggered_scrape = TRUE
                WHERE id = ANY(:ids::uuid[])
            """),
            {"ids": triggered_ids},
        )
        await db.commit()

    return {
        "task": "trigger_scraper_for_misses",
        "status": "ok",
        "triggered": triggered,
        "errors": errors,
    }


async def _run_image_embedding_batch(rows: list) -> None:
    """Background worker: fetch image bytes, embed via CLIP, write vector to DB.
    Always launched via asyncio.create_task() — never awaited directly."""
    import base64
    from BACKEND_DATABASE_MODELS import async_session_factory as _sf
    OLLAMA_URL = os.getenv("OLLAMA_URL", "")
    CLIP_MODEL = os.getenv("CLIP_MODEL", "clip")
    ok = 0
    async with httpx.AsyncClient() as client:
        for row in rows:
            try:
                r = await client.get(row.url, timeout=15.0, follow_redirects=True)
                r.raise_for_status()
                b64 = base64.b64encode(r.content).decode()
                er = await client.post(
                    f"{OLLAMA_URL}/api/embed",
                    json={"model": CLIP_MODEL, "input": b64},
                    timeout=30.0,
                )
                er.raise_for_status()
                data = er.json()
                emb = data.get("embeddings") or data.get("embedding")
                if not emb:
                    continue
                vec = emb[0] if isinstance(emb[0], list) else emb
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
    OLLAMA_URL = os.getenv("OLLAMA_URL", "")
    if not OLLAMA_URL:
        return {"task": "generate_image_embeddings", "status": "ok", "triggered": 0, "note": "OLLAMA_URL not set"}

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
      2. Same (name, manufacturer_id) → flag older rows with needs_oem_lookup=True for manual review.
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

        # Step 2 — duplicate (name, manufacturer_id): flag older rows for review
        r2 = await db.execute(text("""
            WITH dupes AS (
                SELECT id FROM (
                    SELECT id,
                           ROW_NUMBER() OVER (
                               PARTITION BY lower(name), manufacturer_id
                               ORDER BY created_at DESC
                           ) AS rn
                    FROM parts_catalog
                    WHERE name IS NOT NULL AND manufacturer_id IS NOT NULL
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


TASK_REGISTRY: Dict[str, Any] = {
    "clean_part_names":          clean_part_names,
    "normalize_part_types":      normalize_part_types,
    "normalize_categories":      normalize_categories,
    "dedup_catalog_parts":       dedup_catalog_parts,
    "normalize_availability":    normalize_availability,
    "fix_base_prices":           fix_base_prices,
    "flag_fake_skus":            flag_fake_skus,
    "fill_car_brands":           fill_car_brands,
    "refresh_min_max_prices":    refresh_min_max_prices,
    "seed_system_settings":      seed_system_settings,
    "enrich_pending_parts":      _enrich_pending_parts_task,
    "trigger_scraper_for_misses": _trigger_scraper_for_misses_task,
    "generate_image_embeddings": _generate_image_embeddings_task,
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
    from distributed_lock import acquire_lock
    _agent_lock = acquire_lock("db_update_agent", ttl=3600)
    if not await _agent_lock.__aenter__():
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
            job_id = await job_registry_start(db, "run_all_tasks", ttl_seconds=3600)
        except Exception as exc:
            logger.warning("run_all_tasks job_registry_start failed: %s", exc)

        ordered_tasks = [
            "seed_system_settings",
            "fill_car_brands",
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
        await _agent_lock.__aexit__(None, None, None)
        return report

    except Exception as exc:
        if job_id:
            try:
                await job_registry_finish(db, job_id, status="dead", error_message=str(exc)[:500])
            except Exception:
                pass
        _agent_running = False
        await _agent_lock.__aexit__(None, None, None)
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
