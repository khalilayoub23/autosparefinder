"""
Script: routes/public_api.py
Purpose: Partner / public REST API — a small, right-sized, API-key-authenticated surface so
         external sites & developers can search the catalog and get customer-ready prices,
         WITHOUT exposing internal data (supplier names, our cost, the 45% margin, base_price,
         importer/online price, or any internal flags).

Design:
  - Auth: `X-API-Key` header → sha256 → lookup in `api_keys` (issue keys with
    maintenance/issue_api_key.py). Per-key rate limit (Redis).
  - Prices: reuse `_customer_price_fields` (cost×1.45 + CONDITIONAL VAT — 18% IL suppliers,
    0% foreign) so the API returns EXACTLY what a customer would be charged. Never raw cost.
  - Data minimalism: a fixed, documented response schema — only fields a partner needs.

Endpoints (all under /api/public/v1):
  GET /health                      — no auth; liveness
  GET /search    ?q&manufacturer&category&limit&offset   — text search (Meilisearch) + browse
  GET /parts/{part_id}             — one part
  GET /fitment   ?make&model&year&category&limit&offset   — parts that fit a vehicle
  GET /manufacturers               — brand list (cached upstream)

Data Imported / Modified: reads parts_catalog / supplier_parts / suppliers / part_vehicle_fitment;
  updates api_keys.last_used_at + request_count (usage only). Writes no catalog data.

Author: AutoSpareFinder Agent
Last Updated: 2026-07-18
"""
import os
import hashlib
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from BACKEND_DATABASE_MODELS import get_db
from BACKEND_AUTH_SECURITY import get_redis, check_rate_limit
from routes.parts import _customer_price_fields

router = APIRouter()

_MEILI_URL = os.getenv("MEILI_URL", "")
_MAX_LIMIT = 50
_SEARCH_POOL = 200  # how many Meili hits to rank before applying DB filters


# ── Auth ─────────────────────────────────────────────────────────────────────
async def require_api_key(
    request: Request,
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
) -> Dict[str, Any]:
    """Validate the X-API-Key header, enforce the per-key rate limit, record usage."""
    raw = (request.headers.get("X-API-Key") or "").strip()
    if not raw:
        raise HTTPException(status_code=401, detail="Missing API key — send it in the 'X-API-Key' header.")
    key_hash = hashlib.sha256(raw.encode()).hexdigest()
    row = (await db.execute(text(
        "SELECT id, partner_name, rate_limit_per_min, is_active, scopes FROM api_keys WHERE key_hash=:h"
    ), {"h": key_hash})).first()
    if not row or not row[3]:
        raise HTTPException(status_code=401, detail="Invalid or inactive API key.")
    per_min = int(row[2] or 60)
    allowed = await check_rate_limit(redis, f"apikey_rl:{row[0]}", per_min, 60)
    if not allowed:
        raise HTTPException(status_code=429, detail=f"Rate limit exceeded ({per_min} requests/min).")
    try:  # usage bookkeeping — best-effort, never blocks the response
        await db.execute(text(
            "UPDATE api_keys SET last_used_at=NOW(), request_count=request_count+1 WHERE id=:id"
        ), {"id": row[0]})
        await db.commit()
    except Exception:
        pass
    return {"id": str(row[0]), "partner": row[1], "scopes": list(row[4] or [])}


# ── Response shaping (the ONLY fields we expose) ─────────────────────────────
def _shape(r) -> Dict[str, Any]:
    """Map a DB row → the minimal public schema. Deliberately omits supplier name, our cost,
    margin, base_price, importer/online price, and every internal flag."""
    price = _customer_price_fields(
        float(r["cost"]) if r["cost"] is not None else None, 0,
        supplier_name=r["supplier"], supplier_country=r["country"],
    )
    available = r["cost"] is not None and float(r["cost"]) > 0
    return {
        "part_id": str(r["id"]),
        "oem_number": r["oem_number"],
        "name": r["name"],
        "name_he": r["name_he"],
        "manufacturer": r["manufacturer"],
        "category": r["category"],
        "barcode": r["barcode"],
        "available": available,
        "price": None if not available else {
            "amount": price["customer_price_ils"],   # net (before VAT)
            "vat": price["customer_vat_ils"],
            "total": price["customer_total_ils"],     # what the customer pays (excl. shipping)
            "currency": "ILS",
            "vat_included": bool(price["customer_vat_ils"]),
        },
    }


_ROW_SQL = """
    pc.id, pc.oem_number, pc.name, pc.name_he, pc.manufacturer, pc.category, pc.barcode,
    sp.price_ils AS cost, s.name AS supplier, s.country
    FROM parts_catalog pc
    LEFT JOIN LATERAL (
        SELECT price_ils, supplier_id FROM supplier_parts
        WHERE part_id = pc.id AND is_available AND price_ils > 0
        ORDER BY price_ils ASC LIMIT 1
    ) sp ON true
    LEFT JOIN suppliers s ON s.id = sp.supplier_id
"""


async def _rows_for_ids(db: AsyncSession, ids: List[str]) -> Dict[str, Any]:
    if not ids:
        return {}
    rows = (await db.execute(text(
        f"SELECT {_ROW_SQL} WHERE pc.id = ANY(:ids) AND pc.is_active"
    ), {"ids": ids})).mappings().all()
    return {str(r["id"]): _shape(r) for r in rows}


# ── Endpoints ────────────────────────────────────────────────────────────────
@router.get("/api/public/v1/health", tags=["Public API"])
async def public_health():
    """Liveness — no auth."""
    return {"status": "ok", "service": "AutoSpareFinder Partner API", "version": "1"}


@router.get("/api/public/v1/search", tags=["Public API"])
async def public_search(
    q: Optional[str] = Query(None, description="Free-text query (part name / OEM)"),
    manufacturer: Optional[str] = Query(None, description="Filter by car brand, e.g. Toyota"),
    category: Optional[str] = Query(None, description="Filter by category slug, e.g. brakes"),
    limit: int = Query(20, ge=1, le=_MAX_LIMIT),
    offset: int = Query(0, ge=0, le=1000),
    db: AsyncSession = Depends(get_db),
    _key: dict = Depends(require_api_key),
):
    """Search the catalog. Provide `q` (text) and/or `manufacturer`/`category` filters.
    Returns customer-ready prices (conditional VAT). At least one of q/manufacturer is required."""
    if not q and not manufacturer:
        raise HTTPException(status_code=400, detail="Provide at least 'q' or 'manufacturer'.")

    items: List[Dict[str, Any]] = []
    if q and _MEILI_URL:
        # Meilisearch ranks the text hits; DB then filters + prices them (order preserved).
        try:
            async with httpx.AsyncClient(timeout=2.0) as mc:
                resp = await mc.post(
                    f"{_MEILI_URL}/indexes/parts/search",
                    headers={"Authorization": f"Bearer {os.getenv('MEILI_MASTER_KEY', '')}"},
                    json={"q": q, "limit": _SEARCH_POOL, "attributesToRetrieve": ["id"]},
                )
                hits = resp.json().get("hits", []) if resp.status_code == 200 else []
        except Exception:
            hits = []
        ranked_ids = [h["id"] for h in hits if h.get("id")]
        shaped = await _rows_for_ids(db, ranked_ids)
        for pid in ranked_ids:  # keep Meili relevance order
            it = shaped.get(pid)
            if not it:
                continue
            if manufacturer and (it["manufacturer"] or "").lower() != manufacturer.lower():
                continue
            if category and (it["category"] or "").lower() != category.lower():
                continue
            items.append(it)
    else:
        # Browse by manufacturer (+optional category), name-ordered.
        rows = (await db.execute(text(
            f"SELECT {_ROW_SQL} WHERE pc.is_active AND pc.manufacturer ILIKE :mfr "
            f"{'AND pc.category = :cat' if category else ''} "
            f"ORDER BY pc.name LIMIT :lim OFFSET :off"
        ), {"mfr": manufacturer, "cat": category, "lim": min(limit, _MAX_LIMIT) + offset, "off": 0}
        )).mappings().all()
        items = [_shape(r) for r in rows]

    page = items[offset: offset + limit]
    return {"query": {"q": q, "manufacturer": manufacturer, "category": category},
            "count": len(page), "limit": limit, "offset": offset, "results": page}


@router.get("/api/public/v1/parts/{part_id}", tags=["Public API"])
async def public_part(part_id: str, db: AsyncSession = Depends(get_db), _key: dict = Depends(require_api_key)):
    """One part by id."""
    shaped = await _rows_for_ids(db, [part_id])
    if part_id not in shaped:
        raise HTTPException(status_code=404, detail="Part not found.")
    return shaped[part_id]


@router.get("/api/public/v1/fitment", tags=["Public API"])
async def public_fitment(
    make: str = Query(..., description="Car brand, e.g. Toyota"),
    model: str = Query(..., description="Model, e.g. Corolla"),
    year: Optional[int] = Query(None, ge=1950, le=2100),
    category: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=_MAX_LIMIT),
    offset: int = Query(0, ge=0, le=1000),
    db: AsyncSession = Depends(get_db),
    _key: dict = Depends(require_api_key),
):
    """Parts that fit a specific vehicle (make + model [+ year])."""
    year_clause = ("AND f.year_from <= :year AND (f.year_to IS NULL OR f.year_to >= :year)"
                   if year else "")
    cat_clause = "AND pc.category = :cat" if category else ""
    # Resolve the fitting part-ids FIRST (uses the pvf trgm/norm indexes + LIMIT), THEN price
    # only those ≤limit parts — otherwise the LATERAL price + price-sort runs over every match
    # (measured 28s). This keeps it sub-second.
    rows = (await db.execute(text(
        f"""WITH fit AS (
                SELECT DISTINCT pc.id
                FROM parts_catalog pc
                JOIN part_vehicle_fitment f ON f.part_id = pc.id
                WHERE pc.is_active
                  AND lower(btrim(f.manufacturer)) = lower(btrim(:make))
                  AND lower(btrim(f.model)) LIKE lower(btrim(:model)) || '%'
                  {year_clause}
                  {cat_clause}
                LIMIT :lim OFFSET :off
            )
            SELECT {_ROW_SQL} WHERE pc.id IN (SELECT id FROM fit)"""
    ), {"make": make, "model": model, "year": year, "cat": category, "lim": limit, "off": offset}
    )).mappings().all()
    results = [_shape(r) for r in rows]
    return {"vehicle": {"make": make, "model": model, "year": year}, "category": category,
            "count": len(results), "limit": limit, "offset": offset, "results": results}


@router.get("/api/public/v1/manufacturers", tags=["Public API"])
async def public_manufacturers(db: AsyncSession = Depends(get_db), _key: dict = Depends(require_api_key)):
    """List of car brands present in the catalog."""
    rows = (await db.execute(text(
        "SELECT name FROM car_brands WHERE is_active ORDER BY name"
    ))).all()
    return {"count": len(rows), "manufacturers": [r[0] for r in rows]}
