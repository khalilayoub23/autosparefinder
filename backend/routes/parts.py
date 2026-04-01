"""
Parts — all /api/v1/parts/* endpoints extracted from BACKEND_API_ROUTES.py.

Endpoints:
  GET  /api/v1/parts/search
  GET  /api/v1/parts/categories
  GET  /api/v1/parts/autocomplete
  POST /api/v1/parts/search-by-vehicle
  GET  /api/v1/parts/manufacturers
  GET  /api/v1/parts/models
  GET  /api/v1/parts/search-by-vin
  GET  /api/v1/parts/{part_id}
  POST /api/v1/parts/compare
  POST /api/v1/parts/identify-from-image

NOTE: _mask_supplier is imported from routes.utils (STEP 8), so the
previous circular import to BACKEND_API_ROUTES is resolved.
"""
from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile, File, Form
from typing import Optional, List, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, text, and_, or_
import httpx
import os

from BACKEND_DATABASE_MODELS import (
    get_db, PartsCatalog, Vehicle, SupplierPart, Supplier,
    CarBrand, User,
)
from BACKEND_AUTH_SECURITY import (
    get_redis, check_rate_limit, get_current_user,
)
from BACKEND_AI_AGENTS import get_agent, get_supplier_shipping as _get_ship
from routes.utils import _mask_supplier

router = APIRouter()


# ==============================================================================
# GET /api/v1/parts/search
# ==============================================================================

@router.get("/api/v1/parts/search")
async def search_parts(
    query: str = Query(default="", alias="q", max_length=200),
    vehicle_id: Optional[str] = None,
    category: Optional[str] = None,
    per_type: Optional[int] = None,        # override system_settings.search_results_per_type
    sort_by: str = "price_ils",            # cheapest first by default
    vehicle_manufacturer: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    request: Request = None,
    redis=Depends(get_redis),
):
    """
    Search the parts catalogue and return results grouped by part type.

    Response shape:
    {
      "original":    {"part": {...} | null, "suppliers": [...]},
      "oem":         {"part": {...} | null, "suppliers": [...]},
      "aftermarket": {"part": {...} | null, "suppliers": [...]},
      "results_per_type": <int>,
      "query": <str>
    }

    Suppliers are sorted price_ils ASC (cheapest first).
    The `per_type` param caps how many supplier offers are returned per type
    (default: system_settings.search_results_per_type → 4).
    Text search is powered by Meilisearch when available; falls back to ILIKE.
    """
    ip = request.client.host if (request and request.client) else "unknown"
    if redis:
        allowed = await check_rate_limit(redis, f'rate:search:{ip}', 30, 60)
        if not allowed:
            raise HTTPException(status_code=429, detail='יותר מדי בקשות — נסה שוב בעוד דקה')

    # ── Normalize mixed-language query (He→En for catalog matching) ──────────
    if query:
        try:
            from hf_client import hf_normalize_query
            query = await hf_normalize_query(query)
        except Exception:
            pass  # degrade silently — use raw query
    # ── Resolve results_per_type ─────────────────────────────────────────────
    if per_type is None:
        try:
            ss_res = await db.execute(
                text("SELECT value FROM system_settings WHERE key = 'search_results_per_type' LIMIT 1")
            )
            row_ss = ss_res.fetchone()
            per_type = int(row_ss[0]) if row_ss else 4
        except Exception:
            per_type = 4

    # ── Meilisearch text lookup (optional) ───────────────────────────────────
    # meili_ids: List[str]  → ranked UUIDs from Meilisearch (use unnest JOIN)
    # meili_ids: None       → Meilisearch unavailable → fall back to ILIKE
    # meili_ids: []         → Meilisearch returned 0 hits → short-circuit empty
    meili_ids: Optional[List[str]] = None
    _meili_url = os.getenv("MEILI_URL", "")
    if query and _meili_url:
        try:
            async with httpx.AsyncClient(timeout=2.0) as _mc:
                _resp = await _mc.post(
                    f"{_meili_url}/indexes/parts/search",
                    headers={"Authorization": f"Bearer {os.getenv('MEILI_MASTER_KEY', '')}"},
                    json={"q": query, "limit": 200, "attributesToRetrieve": ["id"]},
                )
                _resp.raise_for_status()
                meili_ids = [h["id"] for h in _resp.json().get("hits", [])]
        except Exception:
            meili_ids = None  # keep ILIKE fallback

    # ── Short-circuit when Meilisearch found zero hits ────────────────────────
    if meili_ids is not None and len(meili_ids) == 0:
        return {
            "original":         {"part": None, "suppliers": []},
            "oem":              {"part": None, "suppliers": []},
            "aftermarket":      {"part": None, "suppliers": []},
            "results_per_type": per_type,
            "query":            query,
        }

    # ── pgvector: embed the query and find nearest neighbours ────────────────
    # Runs only when Meilisearch returned results (meili_ids is a non-empty list).
    # vec_score: {id_str → cosine_similarity}  (empty dict if unavailable)
    _route_vec_score: Dict[str, float] = {}
    if meili_ids and query:
        from hf_client import hf_embed
        try:
            _qvec = await hf_embed(query, timeout=3.0)

            if _qvec:
                _vrows = (await db.execute(
                    text("""
                        SELECT id::text,
                               1 - (embedding <=> CAST(:qvec AS vector)) AS sim
                        FROM parts_catalog
                        WHERE is_active = TRUE
                          AND embedding IS NOT NULL
                        ORDER BY embedding <=> CAST(:qvec AS vector)
                        LIMIT 50
                    """),
                    {"qvec": str(_qvec)},
                )).fetchall()
                _route_vec_score = {r[0]: float(r[1]) for r in _vrows}
        except Exception:
            _route_vec_score = {}  # degrade silently to Meilisearch-only

    # ── Hybrid re-rank: 0.6 × meili_score + 0.4 × vec_score ─────────────────
    if _route_vec_score:
        _meili_scores = {uid: 1.0 / (i + 1) for i, uid in enumerate(meili_ids)}
        _all_ids = list(dict.fromkeys(list(_meili_scores) + list(_route_vec_score)))
        _combined = {
            uid: 0.6 * _meili_scores.get(uid, 0.0) + 0.4 * _route_vec_score.get(uid, 0.0)
            for uid in _all_ids
        }
        meili_ids = sorted(_combined, key=_combined.__getitem__, reverse=True)

    # ── Build shared WHERE conditions ────────────────────────────────────────
    conditions: List[str] = ["pc.is_active = TRUE"]
    params: Dict[str, Any] = {}

    # Text filter: if Meilisearch is live use id-array join (no ILIKE needed);
    # if it's unavailable fall back to the original ILIKE clause.
    if query and meili_ids is None:
        conditions.append(
            "(pc.name ILIKE :q OR pc.name_he ILIKE :q OR pc.sku ILIKE :q OR pc.manufacturer ILIKE :q "
            "OR pc.category ILIKE :q OR pc.oem_number ILIKE :q)"
        )
        params["q"]       = f"%{query}%"
        params["q_exact"] = query
        params["q_start"] = f"{query}%"

    if category:
        # category may be a part_type value (from supplier_parts) OR a legacy pc.category brand name
        conditions.append("(pc.category ILIKE :cat OR EXISTS (SELECT 1 FROM supplier_parts sp2 WHERE sp2.part_id = pc.id AND sp2.part_type ILIKE :cat))")
        params["cat"] = f"%{category}%"

    if vehicle_manufacturer:
        # Normalize to catalog brand name: vehicle.manufacturer may be Hebrew
        # (e.g. "סיטרואן ספרד") while parts_catalog stores English ("Citroen").
        # Look up car_brands by name, name_he, or aliases to find all variants.
        try:
            brand_row = (await db.execute(text("""
                SELECT name, name_he, aliases FROM car_brands
                WHERE name ILIKE :vmfr_lookup
                   OR name_he ILIKE :vmfr_lookup
                   OR :vmfr_lookup ILIKE CONCAT('%', name_he, '%')
                   OR EXISTS (
                       SELECT 1 FROM unnest(aliases) a
                       WHERE :vmfr_lookup ILIKE CONCAT('%', a, '%')
                          OR a ILIKE :vmfr_lookup
                   )
                LIMIT 1
            """), {"vmfr_lookup": vehicle_manufacturer})).fetchone()
        except Exception:
            brand_row = None

        if brand_row:
            variants = list({brand_row[0], brand_row[1], *(brand_row[2] or [])})
            vmfr_clauses = []
            for idx, v in enumerate(variants):
                if v:
                    k = f"vmfr_{idx}"
                    vmfr_clauses.append(f"pc.manufacturer ILIKE :{k}")
                    params[k] = f"%{v}%"
            if vmfr_clauses:
                conditions.append(f"({' OR '.join(vmfr_clauses)})")
        else:
            conditions.append("pc.manufacturer ILIKE :vmfr")
            params["vmfr"] = f"%{vehicle_manufacturer}%"

    if vehicle_id:
        conditions.append(
            "(pc.compatible_vehicles::text ILIKE :vid "
            "OR EXISTS (SELECT 1 FROM part_vehicle_fitment pvf "
            "           WHERE pvf.part_id = pc.id AND pvf.vehicle_id = :vid_exact))"
        )
        params["vid"] = f"%{vehicle_id}%"
        params["vid_exact"] = vehicle_id

    where_sql = " AND ".join(conditions)

    # ── ILIKE relevance score (only used when Meilisearch is unavailable) ─────
    if query and meili_ids is None:
        relevance_sql = """
                CASE
                    WHEN pc.name ILIKE :q_exact OR pc.name_he ILIKE :q_exact THEN 4
                    WHEN pc.name ILIKE :q_start OR pc.name_he ILIKE :q_start THEN 3
                    WHEN LENGTH(COALESCE(pc.name,'')) - LENGTH(:q_exact) <= 5 THEN 2
                    ELSE 1
                END DESC,"""
        score_col = """,
                    CASE
                        WHEN pc.name ILIKE :q_exact OR pc.name_he ILIKE :q_exact THEN 4
                        WHEN pc.name ILIKE :q_start OR pc.name_he ILIKE :q_start THEN 3
                        WHEN LENGTH(COALESCE(pc.name,'')) - LENGTH(:q_exact) <= 5 THEN 2
                        ELSE 1
                    END AS match_score"""
    else:
        relevance_sql = ""
        score_col = ""

    # ── Helper: fetch one part per type + its supplier list ──────────────────
    async def _fetch_type(part_type_values: list) -> Dict[str, Any]:
        type_params = {**params, "pt": part_type_values, "lim": per_type}
        _unsafe_sql_tokens = (";", "--", "/*", "*/")
        if any(tok in where_sql for tok in _unsafe_sql_tokens):
            raise HTTPException(status_code=400, detail="unsafe_query_rejected")

        if meili_ids:
            # ── Meilisearch path: rank-preserving unnest JOIN ─────────────────
            # UUIDs come from our own index — hex+dash only, no SQL injection risk.
            # Pass as a Python list so asyncpg maps it to a PostgreSQL text[] array.
            part_row = (await db.execute(
                text(f"""
                    SELECT
                        pc.id, pc.sku, pc.name, pc.name_he, pc.manufacturer,
                        pc.category, pc.part_type, pc.base_price,
                        pc.min_price_ils, pc.max_price_ils, pc.description,
                        pc.oem_number, pc.barcode, pc.weight_kg,
                        pc.is_safety_critical, pc.part_condition,
                        pc.created_at, pc.updated_at
                    FROM parts_catalog pc
                    JOIN (
                        SELECT t.id::uuid AS ranked_id, t.pos
                        FROM unnest(CAST(:uuid_arr AS text[])) WITH ORDINALITY AS t(id, pos)
                    ) ranked ON ranked.ranked_id = pc.id
                    WHERE {where_sql} AND pc.part_type = ANY(:pt)
                    ORDER BY ranked.pos ASC,
                    (
                        SELECT COUNT(*) FROM supplier_parts sp
                        WHERE sp.part_id = pc.id AND sp.is_available = TRUE
                    ) DESC
                    LIMIT 1
                """),
                {**type_params, "uuid_arr": meili_ids},
            )).fetchone()
        else:
            # ── ILIKE fallback path ───────────────────────────────────────────
            if relevance_sql and any(tok in relevance_sql for tok in _unsafe_sql_tokens):
                raise HTTPException(status_code=400, detail="unsafe_query_rejected")
            part_row = (await db.execute(
                text(f"""
                    SELECT
                        pc.id, pc.sku, pc.name, pc.name_he, pc.manufacturer,
                        pc.category, pc.part_type, pc.base_price,
                        pc.min_price_ils, pc.max_price_ils, pc.description,
                        pc.oem_number, pc.barcode, pc.weight_kg,
                        pc.is_safety_critical, pc.part_condition,
                        pc.created_at, pc.updated_at{score_col}
                    FROM parts_catalog pc
                    WHERE {where_sql} AND pc.part_type = ANY(:pt)
                    ORDER BY {relevance_sql}
                    (
                        SELECT COUNT(*) FROM supplier_parts sp
                        WHERE sp.part_id = pc.id AND sp.is_available = TRUE
                    ) DESC,
                    pc.base_price ASC NULLS LAST
                    LIMIT 1
                """),
                type_params,
            )).fetchone()

            # Allow all ILIKE matches — score just affects ordering, not rejection

        if not part_row:
            return {"part": None, "suppliers": []}

        part_id_str = str(part_row[0])

        # All available supplier offers for this part, sorted cheapest first
        sup_rows = (await db.execute(
            text("""
                SELECT
                    sp.id            AS sp_id,
                    s.name           AS supplier_name,
                    s.country        AS supplier_country,
                    sp.supplier_sku,
                    sp.price_usd,
                    sp.price_ils,
                    sp.shipping_cost_ils,
                    sp.availability,
                    sp.warranty_months,
                    sp.estimated_delivery_days,
                    sp.stock_quantity,
                    sp.supplier_url,
                    sp.express_available,
                    sp.express_price_ils,
                    sp.express_delivery_days,
                    sp.express_cutoff_time,
                    sp.last_checked_at
                FROM supplier_parts sp
                JOIN suppliers s ON s.id = sp.supplier_id
                WHERE sp.part_id = :part_id AND sp.is_available = TRUE
                ORDER BY COALESCE(sp.price_ils, sp.price_usd * 3.72) ASC
                LIMIT :lim
            """),
            {"part_id": part_id_str, "lim": per_type},
        )).fetchall()

        part_dict = {
            "id":               str(part_row[0]),
            "sku":              part_row[1],
            "name":             part_row[2],
            "name_he":          part_row[3],
            "manufacturer":     part_row[4],
            "category":         part_row[5],
            "part_type":        part_row[6],
            "base_price":       float(part_row[7]) if part_row[7] else None,
            "min_price_ils":    float(part_row[8]) if part_row[8] else None,
            "max_price_ils":    float(part_row[9]) if part_row[9] else None,
            "description":      part_row[10],
            "oem_number":       part_row[11],
            "barcode":          part_row[12],
            "weight_kg":        float(part_row[13]) if part_row[13] else None,
            "is_safety_critical": part_row[14],
            "part_condition":   part_row[15],
            "created_at":       part_row[16].isoformat() if part_row[16] else None,
            "updated_at":       part_row[17].isoformat() if part_row[17] else None,
        }

        suppliers_list = []
        for sp in sup_rows:
            price_ils = float(sp[5]) if sp[5] else (float(sp[4]) * 3.72 if sp[4] else None)
            suppliers_list.append({
                "supplier_part_id":      str(sp[0]),
                "supplier_name":         _mask_supplier(sp[1]),
                "supplier_country":      sp[2] or "",
                "supplier_sku":          sp[3],
                "price_usd":             float(sp[4]) if sp[4] else None,
                "price_ils":             round(price_ils, 2) if price_ils else None,
                "shipping_cost_ils":     float(sp[6]) if sp[6] else None,
                "availability":          sp[7],
                "warranty_months":       sp[8],
                "estimated_delivery_days": sp[9],
                "stock_quantity":        sp[10],
                "supplier_url":          sp[11],
                "express_available":     sp[12],
                "express_price_ils":     float(sp[13]) if sp[13] else None,
                "express_delivery_days": sp[14],
                "express_cutoff_time":   sp[15],
                "last_checked_at":       sp[16].isoformat() if sp[16] else None,
            })

        return {"part": part_dict, "suppliers": suppliers_list}

    # ── Run all 3 type queries sequentially (shared AsyncSession is not
    # ── safe for concurrent use — concurrent gather causes InvalidRequestError)
    original_res    = await _fetch_type(["Original"])
    oem_res         = await _fetch_type(["OEM"])
    aftermarket_res = await _fetch_type(["Aftermarket", "Refurbished"])

    return {
        "original":         original_res,
        "oem":              oem_res,
        "aftermarket":      aftermarket_res,
        "results_per_type": per_type,
        "query":            query,
    }


# ==============================================================================
# GET /api/v1/parts/categories
# ==============================================================================

@router.get("/api/v1/parts/categories")
async def get_categories(db: AsyncSession = Depends(get_db)):
    from sqlalchemy import func
    # Return distinct part_type values with counts from supplier_parts
    result = await db.execute(
        text("""
            SELECT sp.part_type, COUNT(DISTINCT sp.part_id) as cnt
            FROM supplier_parts sp
            WHERE sp.part_type IS NOT NULL AND sp.part_type != ''
            GROUP BY sp.part_type
            ORDER BY cnt DESC
        """)
    )
    rows = result.fetchall()
    categories = [r[0] for r in rows if r[0]]
    counts = {r[0]: r[1] for r in rows if r[0]}
    return {"categories": categories, "counts": counts, "total": len(categories)}


# ==============================================================================
# GET /api/v1/parts/autocomplete
# ==============================================================================

@router.get("/api/v1/parts/autocomplete")
async def autocomplete_parts(q: str = "", limit: int = 8, db: AsyncSession = Depends(get_db), request: Request = None, redis=Depends(get_redis)):
    """Return distinct part names containing the query string (uses GIN trigram index)."""
    if redis and request:
        ip = request.client.host if request.client else "unknown"
        allowed = await check_rate_limit(redis, f'rate:autocomplete:{ip}', 30, 60)
        if not allowed:
            raise HTTPException(status_code=429, detail='יותר מדי בקשות — נסה שוב בעוד דקה')
    q = q.strip()
    if len(q) < 2:
        return {"suggestions": []}
    result = await db.execute(
        select(PartsCatalog.name, PartsCatalog.manufacturer, PartsCatalog.category)
        .where(PartsCatalog.is_active == True)
        .where(PartsCatalog.name.ilike(f"%{q}%"))
        .order_by(PartsCatalog.name)
        .limit(limit)
    )
    rows = result.fetchall()
    suggestions = [
        {"name": r[0], "manufacturer": r[1], "category": r[2]}
        for r in rows
    ]
    return {"suggestions": suggestions, "query": q}


# ==============================================================================
# POST /api/v1/parts/search-by-vehicle
# ==============================================================================

@router.post("/api/v1/parts/search-by-vehicle")
async def search_parts_by_vehicle(vehicle_id: str, category: Optional[str] = None, db: AsyncSession = Depends(get_db), request: Request = None, redis=Depends(get_redis)):
    if redis and request:
        ip = request.client.host if request.client else "unknown"
        allowed = await check_rate_limit(redis, f'rate:search_by_vehicle:{ip}', 30, 60)
        if not allowed:
            raise HTTPException(status_code=429, detail='יותר מדי בקשות — נסה שוב בעוד דקה')
    result = await db.execute(select(Vehicle).where(Vehicle.id == vehicle_id))
    vehicle = result.scalar_one_or_none()
    if not vehicle:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    agent = get_agent("parts_finder_agent")
    parts = await agent.search_parts_in_db("", vehicle_id, category, db)
    return {"vehicle": {"manufacturer": vehicle.manufacturer, "model": vehicle.model, "year": vehicle.year}, "parts": parts}


# ==============================================================================
# GET /api/v1/parts/manufacturers
# ==============================================================================

@router.get("/api/v1/parts/manufacturers")
async def get_manufacturers(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(PartsCatalog.manufacturer).distinct().where(PartsCatalog.is_active == True))
    return {"manufacturers": [m for m in result.scalars().all() if m]}


# ==============================================================================
# GET /api/v1/parts/models
# ==============================================================================

@router.get("/api/v1/parts/models")
async def get_models(manufacturer: Optional[str] = None, db: AsyncSession = Depends(get_db)):
    """Return distinct car models from compatible_vehicles JSON, optionally filtered by manufacturer.

    Handles two data shapes:
      {"model": "Elantra", "year_from": ..., "year_to": ...}  — new structured format
      {"model_year": "SILVERADO 2016"}                         — legacy combined format
    Uses COALESCE so either key works.
    """
    import re as _re
    if manufacturer:
        sql = text("""
            SELECT DISTINCT COALESCE(elem->>'model', elem->>'model_year') AS model
            FROM parts_catalog,
                 jsonb_array_elements(compatible_vehicles) AS elem
            WHERE compatible_vehicles IS NOT NULL
              AND jsonb_typeof(compatible_vehicles) = 'array'
              AND manufacturer ILIKE :mfr
              AND COALESCE(elem->>'model', elem->>'model_year') IS NOT NULL
              AND COALESCE(elem->>'model', elem->>'model_year') <> ''
            ORDER BY model
        """)
        result = await db.execute(sql, {"mfr": manufacturer})
    else:
        sql = text("""
            SELECT DISTINCT COALESCE(elem->>'model', elem->>'model_year') AS model
            FROM parts_catalog,
                 jsonb_array_elements(compatible_vehicles) AS elem
            WHERE compatible_vehicles IS NOT NULL
              AND jsonb_typeof(compatible_vehicles) = 'array'
              AND COALESCE(elem->>'model', elem->>'model_year') IS NOT NULL
              AND COALESCE(elem->>'model', elem->>'model_year') <> ''
            ORDER BY model
        """)
        result = await db.execute(sql)
    raw = [row[0] for row in result.fetchall() if row[0]]
    # Pass 1: strip 4-digit era year (19xx/20xx) and everything following it
    _era_year_re = _re.compile(r'\s*(?:19|20)\d{2}(?=[^\d]|$).*$')
    # Pass 2: strip trailing 2-digit years or year-ranges ("CAVALIER 99" → "CAVALIER")
    _trail_num_re = _re.compile(r'\s+\d[\d\-/\.]*\s*$')
    # Deduplicate case-insensitively; keep the shortest/cleanest variant
    models_map: dict[str, str] = {}
    for my in raw:
        model = _era_year_re.sub('', my).strip()
        model = _trail_num_re.sub('', model).strip()
        model = _re.sub(r'\s{2,}', ' ', model).strip()
        if not model or model.replace('-', '').replace(' ', '').isdigit():
            continue
        key = model.upper()
        existing = models_map.get(key)
        if existing is None or len(model) < len(existing):
            models_map[key] = model
    models = sorted(models_map.values())
    return {"models": models, "total": len(models)}


# ==============================================================================
# GET /api/v1/parts/search-by-vin
# ==============================================================================

@router.get("/api/v1/parts/search-by-vin")
async def search_parts_by_vin(
    vin: str,
    part_query: Optional[str] = "",
    category: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    request: Request = None,
    redis=Depends(get_redis),
):
    """Decode a VIN via NHTSA free API, cache in vehicles table, and search parts."""
    if redis and request:
        _vin_ip = request.client.host if request.client else "anon"
        await check_rate_limit(redis, f"search_by_vin:{_vin_ip}", 10, 60)
    vin_clean = vin.strip().upper().replace("-", "")
    if len(vin_clean) != 17:
        raise HTTPException(status_code=400, detail="VIN must be exactly 17 characters")

    nhtsa_url = f"https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVinValuesExtended/{vin_clean}?format=json"
    vehicle_info = {}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(nhtsa_url)
            resp.raise_for_status()
            data = resp.json()
            results = data.get("Results", [{}])[0]
            def nhtsa(key): return (results.get(key) or "").strip() or None
            manufacturer = nhtsa("Make") or nhtsa("Manufacturer") or ""
            model        = nhtsa("Model") or ""
            year_str     = nhtsa("ModelYear") or ""
            engine_cc    = nhtsa("DisplacementCC")
            fuel_type    = nhtsa("FuelTypePrimary")
            transmission = nhtsa("TransmissionStyle")
            drive_type   = nhtsa("DriveType")
            body_class   = nhtsa("BodyClass")
            doors        = nhtsa("Doors")
            plant_country = nhtsa("PlantCountry")
            year_int     = int(year_str) if year_str and year_str.isdigit() else 0
            engine_type  = f"{fuel_type or 'Unknown'} {engine_cc}cc" if engine_cc else fuel_type
            vehicle_info = {
                "vin": vin_clean,
                "manufacturer": manufacturer,
                "model": model,
                "year": year_int,
                "engine_cc": engine_cc,
                "fuel_type": fuel_type,
                "transmission": transmission,
                "drive_type": drive_type,
                "body_class": body_class,
                "doors": doors,
                "country_of_origin": plant_country,
            }
    except HTTPException:
        raise
    except Exception as e:
        print(f"[VIN] NHTSA error: {e}")
        raise HTTPException(status_code=502, detail="שגיאה בפענוח ה-VIN – נסה שוב")

    if not vehicle_info.get("manufacturer"):
        raise HTTPException(status_code=404, detail=f"לא נמצא מידע עבור VIN: {vin_clean}")

    # ── Cache VIN in vehicles table (catalog DB) ─────────────────────────────
    cached_vehicle_id: Optional[str] = None
    try:
        vin_row = (await db.execute(
            select(Vehicle).where(Vehicle.vin == vin_clean)
        )).scalar_one_or_none()
        if vin_row:
            cached_vehicle_id = str(vin_row.id)
        else:
            new_vehicle = Vehicle(
                manufacturer = vehicle_info["manufacturer"],
                model        = vehicle_info["model"],
                year         = vehicle_info["year"] or 0,
                vin          = vin_clean,
                engine_type  = engine_type,
                fuel_type    = vehicle_info["fuel_type"],
                transmission = vehicle_info["transmission"],
            )
            db.add(new_vehicle)
            await db.flush()
            cached_vehicle_id = str(new_vehicle.id)
            await db.commit()
        vehicle_info["id"] = cached_vehicle_id
    except Exception as e:
        print(f"[VIN] vehicle cache error (non-fatal): {e}")
        await db.rollback()

    # ── Search parts ──────────────────────────────────────────────────────────
    agent = get_agent("parts_finder_agent")
    search_q = (part_query or "").strip()
    parts_list = await agent.search_parts_in_db(
        search_q,
        cached_vehicle_id,
        category,
        db,
        limit=limit,
        offset=offset,
        vehicle_manufacturer=vehicle_info["manufacturer"],
    )

    return {
        "vehicle": vehicle_info,
        "parts": parts_list,
        # len(parts_list) reflects actual search results (Meilisearch / ILIKE)
        "total": len(parts_list),
        "offset": offset,
        "limit": limit,
    }


# ==============================================================================
# GET /api/v1/parts/{part_id}
# ==============================================================================

@router.get("/api/v1/parts/{part_id}")
async def get_part(part_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(PartsCatalog).where(PartsCatalog.id == part_id))
    part = result.scalar_one_or_none()
    if not part:
        raise HTTPException(status_code=404, detail="Part not found")
    return {"id": str(part.id), "name": part.name, "manufacturer": part.manufacturer, "category": part.category, "part_type": part.part_type, "description": part.description, "specifications": part.specifications}


# ==============================================================================
# POST /api/v1/parts/compare
# ==============================================================================

@router.post("/api/v1/parts/compare")
async def compare_parts(part_id: str, db: AsyncSession = Depends(get_db), request: Request = None, redis=Depends(get_redis)):
    """Return all supplier options for a part (in_stock first, then on_order fallback)."""
    if redis and request:
        ip = request.client.host if request.client else "unknown"
        allowed = await check_rate_limit(redis, f'rate:parts_compare:{ip}', 30, 60)
        if not allowed:
            raise HTTPException(status_code=429, detail='יותר מדי בקשות — נסה שוב בעוד דקה')
    # Try in_stock first
    result = await db.execute(
        select(SupplierPart, Supplier).join(Supplier)
        .where(and_(SupplierPart.part_id == part_id, SupplierPart.is_available == True, Supplier.is_active == True))
        .order_by(Supplier.priority.asc())
    )
    rows = result.all()

    # Fallback to on_order if nothing in stock
    if not rows:
        result2 = await db.execute(
            select(SupplierPart, Supplier).join(Supplier)
            .where(and_(SupplierPart.part_id == part_id, Supplier.is_active == True))
            .order_by(Supplier.priority.asc())
        )
        rows = result2.all()

    agent = get_agent("parts_finder_agent")
    comparisons = []
    for sp, supplier in rows:
        cost_ils = float(sp.price_ils or 0)
        ship_ils = float(sp.shipping_cost_ils or 0)
        delivery_fee = _get_ship(supplier.name or "")
        if cost_ils > 0:
            pricing = agent.calculate_customer_price_from_ils(cost_ils, ship_ils, customer_shipping=delivery_fee)
        else:
            pricing = agent.calculate_customer_price(
                float(sp.price_usd),
                float(sp.shipping_cost_usd or 0),
                customer_shipping=delivery_fee,
            )
        comparisons.append({
            "supplier_part_id": str(sp.id),
            "supplier_name": _mask_supplier(supplier.name),
            "supplier_country": supplier.country or "",
            "availability": "in_stock" if sp.is_available else "on_order",
            "subtotal": pricing["price_no_vat"],
            "vat": pricing["vat"],
            "shipping": pricing["shipping"],
            "total": pricing["total"],
            "profit": pricing["profit"],
            "warranty_months": sp.warranty_months,
            "estimated_delivery": f"{sp.estimated_delivery_days}-{sp.estimated_delivery_days + 7} ימים",
        })
    return {"comparisons": sorted(comparisons, key=lambda x: (x["availability"] != "in_stock", x["total"]))}


# ==============================================================================
# POST /api/v1/parts/identify-from-image
# ==============================================================================

@router.post("/api/v1/parts/identify-from-image")
async def identify_part_from_image(
    file: UploadFile = File(...),
    vehicle_make:  Optional[str] = Form(None),
    vehicle_model: Optional[str] = Form(None),
    vehicle_year:  Optional[str] = Form(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    request: Request = None,
    redis=Depends(get_redis),
):
    """Identify a car part from a photo using GPT-4o Vision.

    Flow:
    1. Hash the image → check part_diagram_cache (DB) for an instant answer.
    2. If not cached: pre-fetch catalog part names for the vehicle and build a
       context-rich prompt (acts as a digital parts diagram).
    3. Call GPT-4o Vision with vehicle + catalog context.
    4. Save the result to part_diagram_cache so future identical searches skip GPT.
    """
    import base64
    import hashlib
    import json as _json
    from hf_client import hf_vision
    from BACKEND_DATABASE_MODELS import PartDiagramCache

    if redis and request:
        ip = request.client.host if request.client else "unknown"
        allowed = await check_rate_limit(redis, f'rate:identify_part_image:{ip}', 10, 60)
        if not allowed:
            raise HTTPException(status_code=429, detail='יותר מדי בקשות — נסה שוב בעוד דקה')

    # ── Read & hash image ────────────────────────────────────────────────────
    img_bytes = await file.read()
    if len(img_bytes) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Image too large (max 10 MB)")
    image_hash = hashlib.sha256(img_bytes).hexdigest()
    b64  = base64.b64encode(img_bytes).decode()
    mime = file.content_type or "image/jpeg"

    identified_name = ""
    identified_en   = ""
    confidence      = 0.0
    possible_names: list = []
    cache_hit       = False

    # ── 1. Check diagram cache ───────────────────────────────────────────────
    try:
        cache_row = (await db.execute(
            text("""
                SELECT part_name_he, part_name_en, possible_names, confidence
                FROM part_diagram_cache
                WHERE image_hash = :h
                  AND (vehicle_make ILIKE :mk OR (:mk IS NULL AND vehicle_make IS NULL))
                ORDER BY times_seen DESC
                LIMIT 1
            """),
            {"h": image_hash, "mk": vehicle_make},
        )).fetchone()
        if cache_row:
            identified_name = cache_row[0]
            identified_en   = cache_row[1] or ""
            possible_names  = cache_row[2] or []
            confidence      = float(cache_row[3] or 0)
            cache_hit       = True
            # Increment times_seen counter
            await db.execute(
                text("""
                    UPDATE part_diagram_cache SET times_seen = times_seen + 1, updated_at = NOW()
                    WHERE image_hash = :h AND (vehicle_make ILIKE :mk OR (:mk IS NULL AND vehicle_make IS NULL))
                """),
                {"h": image_hash, "mk": vehicle_make},
            )
            await db.commit()
    except Exception as e:
        print(f"[Vision] Cache lookup error: {e}")
        await db.rollback()  # reset aborted transaction so subsequent queries work

    # ── 2 & 3. GPT call if no cache hit ─────────────────────────────────────
    if not cache_hit:
        # Pre-fetch catalog names for this vehicle → "digital diagram"
        catalog_hint   = ""
        vehicle_context = ""
        if vehicle_make:
            try:
                brand_row = (await db.execute(text("""
                    SELECT name, aliases FROM car_brands
                    WHERE name ILIKE :m OR name_he ILIKE :m
                       OR :m ILIKE CONCAT('%', name_he, '%')
                       OR EXISTS (SELECT 1 FROM unnest(aliases) a WHERE a ILIKE :m OR :m ILIKE CONCAT('%',a,'%'))
                    LIMIT 1
                """), {"m": vehicle_make})).fetchone()
                mfr_variants = list({vehicle_make, *((brand_row[1] or []) if brand_row else [])})
                if brand_row and brand_row[0]:
                    mfr_variants.append(brand_row[0])
                mfr_filters = [PartsCatalog.manufacturer.ilike(f"%{v}%") for v in mfr_variants if v]
                catalog_rows = []
                if mfr_filters:
                    catalog_rows = (await db.execute(
                        select(PartsCatalog.name)
                        .distinct()
                        .where(PartsCatalog.is_active == True, or_(*mfr_filters))
                        .order_by(PartsCatalog.name)
                        .limit(120)
                    )).fetchall()
                if catalog_rows:
                    names_csv = ", ".join(r[0] for r in catalog_rows)
                    label = vehicle_make + (f" {vehicle_model}" if vehicle_model else "") + (f" {vehicle_year}" if vehicle_year else "")
                    catalog_hint = (
                        f"Our catalog contains these Hebrew part names for {label}: [{names_csv}]. "
                        "Select the BEST matching name from this list as part_name_he if it visually matches the image. "
                        "If nothing matches, use your own SHORT Hebrew name (1–3 words)."
                    )
            except Exception as e:
                print(f"[Vision] Catalog hint error: {e}")

            vehicle_context = (
                f"The vehicle in question is a {vehicle_make}"
                + (f" {vehicle_model}" if vehicle_model else "")
                + (f" (year {vehicle_year})" if vehicle_year else "")
                + ". "
            )

        if os.getenv("HF_TOKEN", ""):
            try:
                prompt = (
                    "You are an expert automotive parts identifier for an Israeli auto parts store. "
                    + vehicle_context
                    + catalog_hint
                    + " Look at this image and identify the car part shown. "
                    "Think step by step: 1) What vehicle system does this part belong to? "
                    "2) What is the exact part? "
                    "3) Does it match a name from the catalog list above? "
                    "Respond ONLY with a valid JSON object, no markdown: "
                    '{"part_name_he": "<best Hebrew name — prefer exact catalog match>", '
                    '"part_name_en": "<English name>", '
                    '"possible_names": ["<alt 1>","<alt 2>","<alt 3>","<alt 4>","<alt 5>","<alt 6>"], '
                    '"confidence": <0.0-1.0>, '
                    '"description": "<brief Hebrew description>"}. '
                    'IMPORTANT: part_name_he and ALL possible_names must be SHORT Hebrew terms '
                    '(1-3 words) as written in Israeli auto parts price lists. '
                    'Do NOT use English in possible_names.'
                )
                raw = await hf_vision(b64, prompt, mime=(file.content_type or "image/jpeg"))
                raw = raw.strip().strip("`").removeprefix("json").strip()
                parsed = _json.loads(raw)
                identified_name = parsed.get("part_name_he") or parsed.get("part_name_en", "")
                identified_en   = parsed.get("part_name_en", "")
                confidence      = float(parsed.get("confidence", 0.0))
                possible_names  = parsed.get("possible_names", [])
            except Exception as e:
                print(f"[Vision] HF Vision error: {e}")

        # ── 4. Persist to diagram cache ──────────────────────────────────────
        if identified_name:
            try:
                await db.execute(
                    text("""
                        INSERT INTO part_diagram_cache
                            (id, image_hash, vehicle_make, vehicle_model, vehicle_year,
                             part_name_he, part_name_en, possible_names, confidence,
                             times_seen, created_at, updated_at)
                        VALUES
                            (gen_random_uuid(), :h, :mk, :mo, :yr,
                             :phe, :pen, :pn, :conf,
                             1, NOW(), NOW())
                        ON CONFLICT (image_hash, vehicle_make, vehicle_model)
                        DO UPDATE SET
                            part_name_he   = EXCLUDED.part_name_he,
                            part_name_en   = EXCLUDED.part_name_en,
                            possible_names = EXCLUDED.possible_names,
                            confidence     = EXCLUDED.confidence,
                            times_seen     = part_diagram_cache.times_seen + 1,
                            updated_at     = NOW()
                    """),
                    {
                        "h":    image_hash,
                        "mk":   vehicle_make,
                        "mo":   vehicle_model,
                        "yr":   vehicle_year,
                        "phe":  identified_name,
                        "pen":  identified_en,
                        "pn":   possible_names,
                        "conf": confidence,
                    },
                )
                await db.commit()
            except Exception as e:
                print(f"[Vision] Cache save error: {e}")

    # Search the DB with the identified Hebrew name (most accurate match)
    parts_results = []
    total = 0
    search_term = identified_name or identified_en
    if search_term:
        agent = get_agent("parts_finder_agent")
        parts_results = await agent.search_parts_in_db(search_term, None, None, db, limit=20, offset=0)
        from sqlalchemy import func
        from BACKEND_DATABASE_MODELS import PartsCatalog
        count_stmt = select(func.count()).select_from(PartsCatalog).where(
            PartsCatalog.is_active == True,
            PartsCatalog.name.ilike(f"%{search_term}%"),
        )
        total = (await db.execute(count_stmt)).scalar_one()

    return {
        "identified_part":    identified_name,
        "identified_part_en": identified_en,
        "possible_names":     possible_names,
        "confidence":         confidence,
        "cache_hit":          cache_hit,
        "parts":              parts_results,
        "total":              total,
    }


