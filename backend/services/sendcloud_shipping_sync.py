"""
Script: sendcloud_shipping_sync.py
Purpose: Sync real Sendcloud EU/UK shipping rates into supplier_parts for
         Car-Parts.ie (IE), SNG Barratt (UK), and BMW Spare Parts EU (EU).
         Replaces NULL shipping_cost_ils/usd placeholders with real quoted rates.
Process:
  1. Load in-scope suppliers by name (never touches eBay/RockAuto).
  2. GET /shipping_methods (Redis-cached, TTL 3h).
     The `countries[]` field in each method is the list of DESTINATION countries
     (where you ship TO) with their per-destination prices.  The per-method
     top-level `price` field is always 0 and must not be used.
  3. Pre-filter to methods that include IL in countries[] with price > 0.
  4. For each supplier, fetch distinct categories with NULL shipping_cost_ils.
  5. Per (supplier, category): resolve default weight -> weight bucket ->
     pick cheapest method covering that bucket -> read IL price from countries[].
  6. Bulk UPDATE supplier_parts for that (supplier, category).
     Never overwrites existing non-NULL shipping values (WHERE IS NULL guard).
  7. On failure (no matching method, API error): log clearly, skip, leave NULL.
Data Modified: supplier_parts.shipping_cost_ils, shipping_cost_usd,
               estimated_delivery_days (rows where shipping_cost_ils IS NULL only)
Data Sources: Sendcloud API v2 (panel.sendcloud.sc/api/v2/shipping_methods)
Missing Data Delegation: Parts with no Sendcloud route stay NULL;
                         resolve_customer_shipping_fee() falls through to its
                         existing country-key or global fallback.
Last Updated: 2026-08-31
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# In-scope EU/UK suppliers only. eBay/RockAuto already have real shipping data.
_SCOPE_SUPPLIER_NAMES = ("Car-Parts.ie", "SNG Barratt", "BMW Spare Parts EU")

# Default weight (kg) by DB category slug. These suppliers have 0% weight_kg
# population so these defaults are always used.
# Weights are representative of typical parts in each category — not exact.
# All 3 suppliers' actual DB categories are enumerated here.
_DEFAULT_WEIGHT_KG: dict[str, float] = {
    # Drivetrain / engine
    "engine":              2.0,
    "exhaust":             2.0,
    "gearbox":             2.0,
    "clutch-drivetrain":   1.5,
    "belts-chains":        0.3,
    "fuel-air":            0.5,
    "fluids":              1.5,
    # Braking / steering / suspension
    "brakes":              1.5,
    "suspension-steering": 1.5,
    "suspension":          1.0,
    # Wheels / cooling / AC
    "wheels-bearings":     0.8,
    "cooling":             0.8,
    "air-conditioning-heating": 0.8,
    # Body / exterior
    "body-exterior":       0.8,
    # Filtration
    "filters":             0.3,
    # Electrical / lighting
    "electrical":          0.3,
    "lighting":            0.5,
    "audio-electronics":   0.3,
    "hybrid-ev":           0.5,
    "safety-systems":      0.5,
    # Interior / wipers
    "interior-comfort":    0.5,
    "wipers-washers":      0.3,
    # Small/misc
    "fasteners":           0.3,
    "service-general":     0.3,
    "accessories":         0.5,
    "merchandise":         0.5,
}
_DEFAULT_WEIGHT_FALLBACK = 1.0  # kg — used for every other / unrecognised category

SENDCLOUD_BASE = "https://panel.sendcloud.sc/api/v2"
_SENDCLOUD_PUBLIC_KEY = os.getenv("SENDCLOUD_PUBLIC_KEY", "")
_SENDCLOUD_SECRET_KEY = os.getenv("SENDCLOUD_SECRET_KEY", "")

# TODO: revisit EUR_TO_ILS periodically; this is a static approximation.
_EUR_TO_ILS = float(os.getenv("EUR_TO_ILS", "4.05"))

_METHODS_CACHE_KEY = "sendcloud:shipping_methods_v1"
_METHODS_CACHE_TTL = 10800   # 3 hours


# ---------------------------------------------------------------------------
# Weight helpers
# ---------------------------------------------------------------------------

def _bucket_weight(weight_kg: float) -> float:
    """Round to nearest 0.5 kg, minimum 0.5."""
    return max(0.5, round(weight_kg * 2) / 2)


def _default_weight(category: Optional[str]) -> float:
    cat = (category or "").strip().lower()
    return _DEFAULT_WEIGHT_KG.get(cat, _DEFAULT_WEIGHT_FALLBACK)


# ---------------------------------------------------------------------------
# Rate extraction — reads directly from the /shipping_methods response.
# The `countries[]` list in each method = destination countries this method
# ships TO, each with a per-destination price.  No separate API call needed.
# ---------------------------------------------------------------------------

def _il_price(method: dict) -> Optional[float]:
    """Return the EUR price for shipping TO Israel from this method, or None."""
    for c in method.get("countries", []):
        if str(c.get("iso_2") or "").upper() == "IL":
            try:
                p = float(c.get("price") or 0)
                if p > 0:
                    return p
            except Exception:
                pass
    return None


def _il_lead_days(method: dict) -> Optional[int]:
    """Return estimated delivery days to IL from lead_time_hours, or None."""
    for c in method.get("countries", []):
        if str(c.get("iso_2") or "").upper() == "IL":
            lt = c.get("lead_time_hours")
            if lt is not None:
                try:
                    return max(1, int(int(lt) / 24))
                except Exception:
                    pass
    return None


def _pick_cheapest_for_weight(il_methods: list[dict], bucket_kg: float) -> Optional[dict]:
    """
    From the pre-filtered IL-capable methods list, pick the cheapest method
    whose weight range covers bucket_kg.

    il_methods: methods already confirmed to have a positive IL price.
    Returns the method dict, or None if no method covers bucket_kg.
    """
    candidates: list[tuple[float, dict]] = []
    for m in il_methods:
        min_w = m.get("min_weight")
        max_w = m.get("max_weight")
        try:
            if min_w is not None and bucket_kg < float(min_w):
                continue
            if max_w is not None and bucket_kg > float(max_w):
                continue
        except Exception:
            pass
        price = _il_price(m)
        if price is not None:
            candidates.append((price, m))

    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0])
    return candidates[0][1]


# ---------------------------------------------------------------------------
# Sendcloud API
# ---------------------------------------------------------------------------

async def _sc_get(path: str, params: Optional[dict] = None, timeout: float = 20.0) -> Any:
    """GET from Sendcloud API with Basic Auth. Raises on non-2xx."""
    async with httpx.AsyncClient(
        auth=(_SENDCLOUD_PUBLIC_KEY, _SENDCLOUD_SECRET_KEY),
        timeout=timeout,
    ) as client:
        resp = await client.get(f"{SENDCLOUD_BASE}{path}", params=params or {})
        if not resp.is_success:
            logger.warning(
                "Sendcloud %s %s -> HTTP %s: %s",
                path, params, resp.status_code, resp.text[:400],
            )
        resp.raise_for_status()
        return resp.json()


async def _get_shipping_methods(redis) -> list[dict]:
    """Return /shipping_methods list, Redis-cached for 3h."""
    cached = await redis.get(_METHODS_CACHE_KEY)
    if cached:
        return json.loads(cached)
    data = await _sc_get("/shipping_methods")
    methods: list[dict] = data.get("shipping_methods", [])
    logger.info("Sendcloud: fetched %d shipping methods (caching for 3h)", len(methods))
    await redis.setex(_METHODS_CACHE_KEY, _METHODS_CACHE_TTL, json.dumps(methods))
    return methods


# ---------------------------------------------------------------------------
# Main sync entry point
# ---------------------------------------------------------------------------

async def sync_sendcloud_shipping(db: AsyncSession, redis) -> dict:
    """
    For each in-scope EU/UK supplier (Car-Parts.ie, SNG Barratt, BMW Spare Parts EU),
    look up the Sendcloud rate for each distinct parts_catalog.category and run one
    bulk UPDATE per (supplier, category) — replacing NULL shipping_cost_ils/usd rows.

    Rate selection: finds the cheapest Sendcloud method that ships TO Israel for
    the category's default weight bucket.  The rate is read directly from the
    method's countries[IL].price field — no separate /shipping-price/ API call.

    shipping_cost_usd stores the raw EUR amount (source-currency convention, same
    as eBay stores raw USD).  shipping_cost_ils is the authoritative field that
    resolve_customer_shipping_fee() reads first.

    Never overwrites existing non-NULL shipping_cost_ils values.
    """
    report: dict[str, Any] = {
        "suppliers": {},
        "total_rows_affected": 0,
        "total_categories_updated": 0,
        "total_categories_skipped": 0,
    }

    if not _SENDCLOUD_PUBLIC_KEY or not _SENDCLOUD_SECRET_KEY:
        logger.error("SENDCLOUD_PUBLIC_KEY or SENDCLOUD_SECRET_KEY not set — skipping shipping sync")
        report["error"] = "credentials_not_set"
        return report

    # 1. Load in-scope suppliers
    sup_res = await db.execute(
        text("""
            SELECT id::text, name, country
            FROM suppliers
            WHERE name = ANY(:names) AND is_active = TRUE
            ORDER BY name
        """),
        {"names": list(_SCOPE_SUPPLIER_NAMES)},
    )
    suppliers = sup_res.fetchall()
    if not suppliers:
        logger.warning("Sendcloud sync: no in-scope active suppliers found")
        return report

    # 2. Fetch shipping methods (Redis-cached 3h) and pre-filter to IL-capable
    try:
        all_methods = await _get_shipping_methods(redis)
    except Exception as exc:
        logger.error("Sendcloud: /shipping_methods fetch failed: %s", exc)
        report["error"] = f"shipping_methods_fetch_failed: {exc}"
        return report

    il_methods = [
        m for m in all_methods
        if not m.get("is_return", False) and _il_price(m) is not None
    ]
    logger.info(
        "Sendcloud: %d total methods, %d ship TO IL",
        len(all_methods), len(il_methods),
    )
    if not il_methods:
        logger.error("Sendcloud: no methods found that ship TO Israel — skipping sync")
        report["error"] = "no_il_destination_methods"
        return report

    # 3. Process each supplier
    for sup in suppliers:
        sup_id   = str(sup[0])
        sup_name = str(sup[1])

        sup_report: dict[str, Any] = {
            "supplier_id":                sup_id,
            "categories_updated":         0,
            "categories_skipped_no_rate": 0,
            "rows_affected":              0,
            "rows_used_default_weight":   0,
            "skipped_categories":         [],
        }

        # 4. Distinct categories with NULL shipping for this supplier
        cats_res = await db.execute(
            text("""
                SELECT DISTINCT COALESCE(pc.category, '') AS category
                FROM supplier_parts sp
                JOIN parts_catalog pc ON pc.id = sp.part_id
                WHERE sp.supplier_id = CAST(:sid AS uuid)
                  AND sp.shipping_cost_ils IS NULL
                ORDER BY category
            """),
            {"sid": sup_id},
        )
        categories = [r[0] for r in cats_res.fetchall()]
        logger.info(
            "Sendcloud: %s — %d category buckets with NULL shipping_cost_ils",
            sup_name, len(categories),
        )

        # 5-6. Per (supplier, category): look up rate -> bulk UPDATE
        for cat in categories:
            weight_kg = _default_weight(cat)
            bucket    = _bucket_weight(weight_kg)

            method = _pick_cheapest_for_weight(il_methods, bucket)
            if method is None:
                logger.warning(
                    "Sendcloud: no method covers %.1fkg->IL for %s cat=%r — skipping",
                    bucket, sup_name, cat,
                )
                sup_report["categories_skipped_no_rate"] += 1
                sup_report["skipped_categories"].append({
                    "category":  cat or "(null)",
                    "reason":    f"no_method_for_{bucket:.1f}kg_to_IL",
                    "bucket_kg": bucket,
                })
                report["total_categories_skipped"] += 1
                continue

            rate_eur  = _il_price(method)     # already confirmed non-None above
            assert rate_eur is not None        # mypy safety; can't reach None here
            rate_ils  = round(rate_eur * _EUR_TO_ILS, 2)
            days      = _il_lead_days(method)  # int or None

            try:
                upd = await db.execute(
                    text("""
                        UPDATE supplier_parts sp
                        SET shipping_cost_ils       = :rate_ils,
                            shipping_cost_usd       = :rate_eur,
                            estimated_delivery_days = :days,
                            updated_at              = NOW()
                        FROM parts_catalog pc
                        WHERE pc.id              = sp.part_id
                          AND sp.supplier_id     = CAST(:sid AS uuid)
                          AND pc.category        = :cat
                          AND sp.shipping_cost_ils IS NULL
                    """),
                    {
                        "rate_ils": rate_ils,
                        "rate_eur": rate_eur,
                        "days":     days,
                        "sid":      sup_id,
                        "cat":      cat,
                    },
                )
                rows = upd.rowcount
                await db.commit()

                sup_report["categories_updated"]       += 1
                sup_report["rows_affected"]            += rows
                sup_report["rows_used_default_weight"] += rows
                report["total_categories_updated"]     += 1
                report["total_rows_affected"]          += rows

                logger.info(
                    "Sendcloud: UPDATED %s  cat=%r  bucket=%.1fkg  "
                    "method=%r  rate=€%.2f=₪%.2f  days=%s  rows=%d",
                    sup_name, cat or "(null)", bucket,
                    method.get("name"), rate_eur, rate_ils, days, rows,
                )

            except Exception as exc:
                logger.error(
                    "Sendcloud: bulk UPDATE failed for %s cat=%r: %s",
                    sup_name, cat, exc,
                )
                try:
                    await db.rollback()
                except Exception:
                    pass
                sup_report["categories_skipped_no_rate"] += 1
                sup_report["skipped_categories"].append({
                    "category":  cat or "(null)",
                    "reason":    f"update_failed: {exc!s}",
                    "bucket_kg": bucket,
                })
                report["total_categories_skipped"] += 1

        report["suppliers"][sup_name] = sup_report

    logger.info(
        "Sendcloud shipping sync complete — rows_affected=%d  "
        "cats_updated=%d  cats_skipped=%d",
        report["total_rows_affected"],
        report["total_categories_updated"],
        report["total_categories_skipped"],
    )
    return report
