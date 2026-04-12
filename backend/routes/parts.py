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
import copy
import json
from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile, File, Form
from typing import Optional, List, Dict, Any, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, text, and_, or_
import httpx
import os
import re
import time
import unicodedata
from difflib import SequenceMatcher

from BACKEND_DATABASE_MODELS import (
    get_db, PartsCatalog, Vehicle, SupplierPart, Supplier,
    CarBrand, User,
)
from BACKEND_AUTH_SECURITY import (
    get_redis, check_rate_limit, get_current_user,
)
from BACKEND_AI_AGENTS import get_agent, get_supplier_shipping as _get_ship
from routes.utils import _mask_supplier
from manufacturer_normalization import (
    canonicalize_vehicle_model_for_manufacturer,
    normalize_vehicle_model_name,
    normalize_vehicle_submodel_name,
    normalize_manufacturer_name,
)
from part_type_taxonomy import (
    build_part_type_sql_clause,
    classify_part_type_family,
    get_part_type_groups,
    iter_part_type_families,
    resolve_part_type_family,
)

router = APIRouter()

CATEGORY_RESPONSE_TTL_S = 300.0
CATEGORY_RESPONSE_CACHE: Dict[Tuple[str, str, str, str], Tuple[float, Dict[str, Any]]] = {}
HIERARCHY_RESPONSE_TTL_S = 300.0
MODELS_RESPONSE_CACHE: Dict[Tuple[str], Tuple[float, Dict[str, Any]]] = {}
SUBMODELS_RESPONSE_CACHE: Dict[Tuple[str, str], Tuple[float, Dict[str, Any]]] = {}
YEARS_RESPONSE_CACHE: Dict[Tuple[str, str, str], Tuple[float, Dict[str, Any]]] = {}
SEARCH_RESPONSE_TTL_S = 120.0
SEARCH_RESPONSE_CACHE: Dict[Tuple, Tuple[float, Dict[str, Any]]] = {}
GOV_IL_LICENSE_RESOURCE_ID = "053cea08-09bc-40ec-8f7a-156f0677aff3"
GOV_IL_DATASTORE_URL = "https://data.gov.il/api/3/action/datastore_search"


def _search_cache_key(query: str, vehicle_manufacturer: Optional[str], vehicle_model: Optional[str],
                      vehicle_submodel: Optional[str], vehicle_year: Optional[int],
                      category: Optional[str], per_type: int, sort_by: str) -> Tuple:
    return (
        (query or '').strip().lower(),
        (vehicle_manufacturer or '').strip().lower(),
        (vehicle_model or '').strip().lower(),
        (vehicle_submodel or '').strip().lower(),
        str(vehicle_year or ''),
        (category or '').strip().lower(),
        per_type,
        sort_by,
    )


def _get_cached_search_response(cache_key: Tuple) -> Optional[Dict[str, Any]]:
    cached = SEARCH_RESPONSE_CACHE.get(cache_key)
    if not cached:
        return None
    expires_at, payload = cached
    if expires_at <= time.monotonic():
        SEARCH_RESPONSE_CACHE.pop(cache_key, None)
        return None
    return copy.deepcopy(payload)


def _store_cached_search_response(cache_key: Tuple, payload: Dict[str, Any]) -> None:
    if len(SEARCH_RESPONSE_CACHE) >= 256:
        expired_keys = [k for k, (exp, _) in SEARCH_RESPONSE_CACHE.items() if exp <= time.monotonic()]
        for k in expired_keys:
            SEARCH_RESPONSE_CACHE.pop(k, None)
        if len(SEARCH_RESPONSE_CACHE) >= 256:
            oldest = min(SEARCH_RESPONSE_CACHE, key=lambda k: SEARCH_RESPONSE_CACHE[k][0])
            SEARCH_RESPONSE_CACHE.pop(oldest, None)
    SEARCH_RESPONSE_CACHE[cache_key] = (time.monotonic() + SEARCH_RESPONSE_TTL_S, copy.deepcopy(payload))


def _to_int_or_none(value: Any) -> Optional[int]:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except Exception:
        return None


async def _lookup_vehicle_by_license_plate(license_plate: str) -> Dict[str, Any]:
    clean_plate = re.sub(r"[^0-9]", "", str(license_plate or ""))
    if not clean_plate:
        raise HTTPException(status_code=400, detail="invalid_license_plate")

    params = {
        "resource_id": GOV_IL_LICENSE_RESOURCE_ID,
        "filters": json.dumps({"mispar_rechev": clean_plate}),
        "limit": 1,
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(GOV_IL_DATASTORE_URL, params=params)
            response.raise_for_status()
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="mot_api_timeout")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"mot_api_error: {exc}")

    records = response.json().get("result", {}).get("records", [])
    if not records:
        raise HTTPException(status_code=404, detail="license_plate_not_found")

    record = records[0]
    return {
        "license_plate": clean_plate,
        "tozeret_cd": _to_int_or_none(record.get("tozeret_cd")),
        "degem_cd": _to_int_or_none(record.get("degem_cd")),
        "shnat_yitzur": _to_int_or_none(record.get("shnat_yitzur")),
        "manufacturer": record.get("tozeret_nm") or record.get("manufacturer") or record.get("tozeret_cd"),
        "model": record.get("kinuy_mishari") or record.get("degem_nm"),
        "engine": record.get("nefah_manoa") or record.get("degem_manoa"),
    }


CANONICAL_FILTER_CATEGORIES: List[str] = [
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

FILTER_CATEGORY_MAP: Dict[str, str] = {
    "brakes": "בלמים",
    "brake": "בלמים",
    "בלם": "בלמים",
    "wheels": "גלגלים וצמיגים",
    "tyres": "גלגלים וצמיגים",
    "tires": "גלגלים וצמיגים",
    "גלגלים": "גלגלים וצמיגים",
    "צמיגים": "גלגלים וצמיגים",
    "fuel": "דלק",
    "fuel system": "דלק",
    "מערכת דלק": "דלק",
    "steering": "היגוי",
    "electrical": "חשמל רכב",
    "electric": "חשמל רכב",
    "electronics": "חשמל רכב",
    "חשמל": "חשמל רכב",
    "general": "כללי",
    "misc": "כללי",
    "miscellaneous": "כללי",
    "other": "כללי",
    "אחר": "כללי",
    "wipers": "מגבים",
    "wiper": "מגבים",
    "מגב": "מגבים",
    "ac": "מיזוג",
    "air conditioning": "מיזוג",
    "climate": "מיזוג",
    "hvac": "מיזוג",
    "engine": "מנוע",
    "motor": "מנוע",
    "suspension": "מתלה",
    "body": "פחיין ומרכב",
    "bodywork": "פחיין ומרכב",
    "מרכב": "פחיין ומרכב",
    "interior": "ריפוד ופנים",
    "upholstery": "ריפוד ופנים",
    "belts": "שרשראות ורצועות",
    "chains": "שרשראות ורצועות",
    "belt": "שרשראות ורצועות",
    "timing": "שרשראות ורצועות",
    "רצועות": "שרשראות ורצועות",
    "lighting": "תאורה",
    "lights": "תאורה",
    "light": "תאורה",
    "lamps": "תאורה",
    "תאור": "תאורה",
}

FILTER_CATEGORY_KEYWORDS: Dict[str, List[str]] = {
    "בלמים": ["בלם", "בלמים", "רפיד", "צלחת", "דיסק בלם", "brake", "brakes", "pad", "pads", "rotor", "disc"],
    "מנוע": ["מנוע", "אטם", "שסתום", "פילטר שמן", "engine", "gasket", "valve", "oil filter", "thermostat"],
    "מיזוג": ["מזגן", "מדחס", "מעבה", "מאייד", "ac", "a/c", "compressor", "condenser", "evaporator"],
    "חשמל רכב": ["חשמל", "אלטרנטור", "סטרטר", "חיישן", "מצת", "alternator", "starter", "sensor", "spark plug"],
    "מתלה": ["בולם", "זרוע", "תפוח", "מיסב", "suspension", "shock", "strut", "bearing", "arm"],
    "היגוי": ["הגה", "מסרק", "קצה הגה", "steering", "rack", "tie rod"],
    "תאורה": ["פנס", "תאורה", "נורה", "lamp", "light", "headlight", "tail light", "bulb"],
    "מגבים": ["מגב", "wiper", "washer"],
    "דלק": ["דלק", "משאבת דלק", "מזרק", "fuel", "injector", "pump"],
    "פחיין ומרכב": ["פגוש", "גריל", "כנף", "דלת", "מכסה", "bumper", "grille", "fender", "door", "hood"],
    "ריפוד ופנים": ["ריפוד", "מושב", "דשבורד", "trim", "interior", "seat", "dashboard"],
    "שרשראות ורצועות": ["רצוע", "שרשרת", "טיימינג", "belt", "chain", "timing"],
    "גלגלים וצמיגים": ["צמיג", "גלגל", "גנט", "tire", "tyre", "wheel", "rim"],
}


def _normalize_filter_category(raw: Optional[str]) -> str:
    v = (raw or "").strip()
    if not v:
        return ""
    if v in CANONICAL_FILTER_CATEGORIES:
        return v
    low = v.lower()
    if low in FILTER_CATEGORY_MAP:
        return FILTER_CATEGORY_MAP[low]
    for k, mapped in FILTER_CATEGORY_MAP.items():
        if k and (k in low or low in k):
            return mapped
    return "כללי"


def _is_probable_variant_submodel(value: Optional[str]) -> bool:
    clean = normalize_vehicle_model_name(value)
    if not clean:
        return False

    upper = clean.upper()
    trim_tokens = {
        "BASE",
        "CLASSIC",
        "COMFORT",
        "DX",
        "EX",
        "DIESEL",
        "GL",
        "GLS",
        "GLX",
        "GT",
        "HIGHLINE",
        "HYBRID",
        "L",
        "LE",
        "LIMITED",
        "LS",
        "LT",
        "LTZ",
        "LX",
        "MIDNIGHT",
        "PREMIER",
        "PRESTIGE",
        "RS",
        "SE",
        "SEL",
        "SPORT",
        "SS",
        "STD",
        "SV",
        "SX",
        "TREND",
        "TURBO",
        "CHASSIS",
        "XL",
        "XLT",
    }
    if upper in trim_tokens:
        return True
    if re.fullmatch(r"\d+DR", upper):
        return True
    if re.fullmatch(r"\d+(?:\.\d+)?\s*[LT](?:\s*(?:TURBO|DIESEL))?", upper):
        return True
    if re.fullmatch(r"\d+(?:\.\d+)?(?:L|T|CC|V\d+|HDI|TURBO|TSI|TFSI|CDI|MPI)", upper):
        return True
    if re.fullmatch(r"\d+(?:\.\d+)?\s+(?:L|TURBO|DIESEL|HDI|TSI|TFSI|CDI|MPI)", upper):
        return True
    if re.fullmatch(r"[A-Z\u0590-\u05FF]+", upper):
        return False
    if normalize_vehicle_submodel_name(clean):
        return True
    return False


def _split_vehicle_model_variant(manufacturer: Optional[str], value: Optional[str]) -> Tuple[str, str]:
    clean = canonicalize_vehicle_model_for_manufacturer(manufacturer, value)
    if not clean:
        return "", ""

    canonical_manufacturer = normalize_manufacturer_name(manufacturer, manufacturer).casefold()
    clean = re.sub(r"\s*\+\s*$", "", clean).strip()
    if canonical_manufacturer == "chevrolet":
        pickup_match = re.match(r"^PICK(?:\s*-\s*|\s+)?UP(?:\s+(?P<sub>.+))?$", clean, flags=re.IGNORECASE)
        if clean == "PICK":
            return "PICK UP", ""
        if pickup_match:
            pickup_sub = (pickup_match.group("sub") or "").strip()
            return "PICK UP", normalize_vehicle_submodel_name(pickup_sub) or normalize_vehicle_model_name(pickup_sub)

    separator_match = re.split(r"\s*(?:\+|&|/)\s*", clean, maxsplit=1)
    if len(separator_match) == 2:
        primary = canonicalize_vehicle_model_for_manufacturer(manufacturer, separator_match[0])
        secondary = normalize_vehicle_submodel_name(separator_match[1])
        return primary, secondary

    if re.search(r"\s*-\s*", clean):
        base, sub = [x.strip() for x in re.split(r"\s*-\s*", clean, maxsplit=1)]
        base = canonicalize_vehicle_model_for_manufacturer(manufacturer, base)
        if _is_probable_variant_submodel(sub):
            return base, normalize_vehicle_submodel_name(sub) or normalize_vehicle_model_name(sub)
        return clean, ""

    platform_match = re.match(
        r"^(?P<base>[A-Za-z0-9\u0590-\u05FF\s]+?)\s+(?P<sub>[A-Z]\d{1,3}(?:\s+[A-Z]{2,8})?)\b",
        clean,
        flags=re.IGNORECASE,
    )
    if platform_match:
        return (
            canonicalize_vehicle_model_for_manufacturer(manufacturer, platform_match.group("base")),
            normalize_vehicle_submodel_name(platform_match.group("sub").upper()),
        )

    tokens = clean.split()
    for idx in range(1, len(tokens)):
        base = canonicalize_vehicle_model_for_manufacturer(manufacturer, " ".join(tokens[:idx]))
        sub = " ".join(tokens[idx:])
        if base and _is_probable_variant_submodel(sub):
            return base, normalize_vehicle_submodel_name(sub) or normalize_vehicle_model_name(sub)

    return clean, ""


def _category_cache_key(
    vehicle_manufacturer: Optional[str],
    vehicle_model: Optional[str],
    vehicle_submodel: Optional[str],
    vehicle_year: Optional[int],
) -> Tuple[str, str, str, str]:
    return (
        str(vehicle_manufacturer or "").strip().casefold(),
        str(vehicle_model or "").strip().casefold(),
        str(vehicle_submodel or "").strip().casefold(),
        str(vehicle_year or "").strip(),
    )


def _get_cached_category_response(cache_key: Tuple[str, str, str, str]) -> Optional[Dict[str, Any]]:
    cached = CATEGORY_RESPONSE_CACHE.get(cache_key)
    if not cached:
        return None
    expires_at, payload = cached
    if expires_at <= time.monotonic():
        CATEGORY_RESPONSE_CACHE.pop(cache_key, None)
        return None
    return copy.deepcopy(payload)


def _store_cached_category_response(cache_key: Tuple[str, str, str, str], payload: Dict[str, Any]) -> None:
    if len(CATEGORY_RESPONSE_CACHE) >= 128:
        expired_keys = [key for key, (expires_at, _value) in CATEGORY_RESPONSE_CACHE.items() if expires_at <= time.monotonic()]
        for key in expired_keys:
            CATEGORY_RESPONSE_CACHE.pop(key, None)
        if len(CATEGORY_RESPONSE_CACHE) >= 128:
            oldest_key = min(CATEGORY_RESPONSE_CACHE, key=lambda key: CATEGORY_RESPONSE_CACHE[key][0])
            CATEGORY_RESPONSE_CACHE.pop(oldest_key, None)
    CATEGORY_RESPONSE_CACHE[cache_key] = (time.monotonic() + CATEGORY_RESPONSE_TTL_S, copy.deepcopy(payload))


def _get_cached_hierarchy_response(
    cache_store: Dict[Tuple[Any, ...], Tuple[float, Dict[str, Any]]],
    cache_key: Tuple[Any, ...],
) -> Optional[Dict[str, Any]]:
    cached = cache_store.get(cache_key)
    if not cached:
        return None
    expires_at, payload = cached
    if expires_at <= time.monotonic():
        cache_store.pop(cache_key, None)
        return None
    return copy.deepcopy(payload)


def _store_cached_hierarchy_response(
    cache_store: Dict[Tuple[Any, ...], Tuple[float, Dict[str, Any]]],
    cache_key: Tuple[Any, ...],
    payload: Dict[str, Any],
) -> None:
    if len(cache_store) >= 256:
        expired_keys = [key for key, (expires_at, _value) in cache_store.items() if expires_at <= time.monotonic()]
        for key in expired_keys:
            cache_store.pop(key, None)
        if len(cache_store) >= 256:
            oldest_key = min(cache_store, key=lambda key: cache_store[key][0])
            cache_store.pop(oldest_key, None)
    cache_store[cache_key] = (time.monotonic() + HIERARCHY_RESPONSE_TTL_S, copy.deepcopy(payload))


async def _build_vehicle_fitment_clause(
    db: AsyncSession,
    params: Dict[str, Any],
    vehicle_manufacturer: Optional[str] = None,
    vehicle_model: Optional[str] = None,
    vehicle_submodel: Optional[str] = None,
    vehicle_year: Optional[int] = None,
    prefix: str = "fit",
) -> Optional[str]:
    if not (vehicle_manufacturer or vehicle_model or vehicle_submodel or vehicle_year):
        return None

    vehicle_fitment_checks: List[str] = []

    # --- GIN pre-filter: uses the idx_pc_compat_vehicles_gin index to shrink
    # the candidate set before the per-element EXISTS scan.
    # We build a @> containment probe for the primary manufacturer name so
    # Postgres can bitmap-AND the GIN result with the rest of the WHERE clause.
    gin_prefilter: Optional[str] = None

    if vehicle_manufacturer:
        raw_vmfr = vehicle_manufacturer
        cleaned_vmfr = re.sub(r"\s+", " ", re.sub(r"[^\w\u0590-\u05FF]+", " ", (raw_vmfr or "").lower())).strip()
        cleaned_vmfr = re.sub(r"\b(parts?|spare\s*parts?)\b", "", cleaned_vmfr)
        cleaned_vmfr = re.sub(r"\bחלפים\b", "", cleaned_vmfr)
        cleaned_vmfr = re.sub(r"\s+", " ", cleaned_vmfr).strip()
        variants: List[str] = [raw_vmfr, cleaned_vmfr]
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
            """), {"vmfr_lookup": raw_vmfr})).fetchone()
            if brand_row:
                variants = list(dict.fromkeys([*variants, brand_row[0], brand_row[1], *(brand_row[2] or [])]))
        except Exception:
            pass

        clean_variants = [x for x in variants if x and str(x).strip()]

        # Build the GIN @> pre-filter using the first (canonical) variant.
        # This lets the GIN index eliminate rows that have no entry at all for
        # this manufacturer before we run the slower per-element scan below.
        gin_key = f"{prefix}_gin_mfr"
        params[gin_key] = f'[{{"manufacturer": "{clean_variants[0].replace(chr(34), chr(39))}"}}]'
        gin_prefilter = f"pc.compatible_vehicles @> CAST(:{gin_key} AS jsonb)"

        vmfr_clauses = []
        for idx, variant in enumerate(clean_variants):
            key = f"{prefix}_mfr_{idx}"
            vmfr_clauses.append(f"LOWER(TRIM(COALESCE(cv_fit->>'manufacturer', ''))) = LOWER(TRIM(:{key}))")
            params[key] = str(variant).strip()
        if vmfr_clauses:
            vehicle_fitment_checks.append(f"({' OR '.join(vmfr_clauses)})")

    if vehicle_model:
        key = f"{prefix}_model"
        vehicle_fitment_checks.append(
            f"COALESCE(cv_fit->>'model', cv_fit->>'model_year', '') ILIKE :{key}"
        )
        params[key] = f"%{vehicle_model}%"

    if vehicle_submodel:
        key = f"{prefix}_submodel"
        vehicle_fitment_checks.append(
            "LOWER(TRIM(COALESCE(cv_fit->>'sub_model', cv_fit->>'trim', cv_fit->>'generation', ''))) "
            f"= LOWER(TRIM(:{key}))"
        )
        params[key] = vehicle_submodel

    if vehicle_year:
        str_key = f"{prefix}_year_str"
        int_key = f"{prefix}_year_int"
        vehicle_fitment_checks.append(
            f"(cv_fit->>'model_year' ILIKE :{str_key}"
            " OR ("
            "     cv_fit->>'year_from' ~ '^[0-9]+$'"
            "     AND cv_fit->>'year_to' ~ '^[0-9]+$'"
            f"     AND (cv_fit->>'year_from')::int <= :{int_key}"
            f"     AND (cv_fit->>'year_to')::int >= :{int_key}"
            " ))"
        )
        params[str_key] = f"%{vehicle_year}%"
        params[int_key] = vehicle_year

    if not vehicle_fitment_checks:
        return None

    exists_clause = (
        "(pc.compatible_vehicles IS NOT NULL"
        " AND jsonb_typeof(pc.compatible_vehicles) = 'array'"
        " AND EXISTS ("
        "     SELECT 1 FROM jsonb_array_elements(pc.compatible_vehicles) cv_fit"
        f"     WHERE {' AND '.join(vehicle_fitment_checks)}"
        " ))"
    )

    # Combine GIN pre-filter (index hit) with exact EXISTS scan (correctness).
    if gin_prefilter:
        return f"({gin_prefilter} AND {exists_clause})"
    return exists_clause


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
    vehicle_model: Optional[str] = None,
    vehicle_submodel: Optional[str] = None,
    vehicle_year: Optional[int] = None,
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

    # ── In-memory response cache (survives repeated searches with same params) ─
    _s_cache_key = _search_cache_key(query, vehicle_manufacturer, vehicle_model,
                                     vehicle_submodel, vehicle_year, category,
                                     per_type or 4, sort_by)
    _cached_search = _get_cached_search_response(_s_cache_key)
    if _cached_search is not None:
        return _cached_search

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
        family_clause = build_part_type_sql_clause(category, params)
        if family_clause:
            conditions.append(family_clause)
        else:
            canonical_cat = _normalize_filter_category(category)
            keyword_terms = FILTER_CATEGORY_KEYWORDS.get(canonical_cat, [])
            keyword_clauses: List[str] = []
            for idx, term in enumerate(keyword_terms):
                key = f"cat_kw_{idx}"
                keyword_clauses.append(
                    f"pc.name ILIKE :{key} OR pc.name_he ILIKE :{key} OR COALESCE(pc.description, '') ILIKE :{key}"
                )
                params[key] = f"%{term}%"
            keyword_sql = f" OR ({' OR '.join(keyword_clauses)})" if keyword_clauses else ""
            conditions.append(f"(pc.category ILIKE :cat OR EXISTS (SELECT 1 FROM supplier_parts sp2 WHERE sp2.part_id = pc.id AND sp2.part_type ILIKE :cat){keyword_sql})")
            params["cat"] = f"%{category}%"

    selected_part_family = resolve_part_type_family(category) if category else None

    if vehicle_id:
        conditions.append(
            "(pc.compatible_vehicles::text ILIKE :vid "
            "OR EXISTS (SELECT 1 FROM part_vehicle_fitment pvf "
            "           WHERE pvf.part_id = pc.id AND pvf.vehicle_id = :vid_exact))"
        )
        params["vid"] = f"%{vehicle_id}%"
        params["vid_exact"] = vehicle_id

    # Exact fitment path: once any vehicle hierarchy filter is selected, require
    # a single compatible_vehicles entry to satisfy all selected vehicle fields.
    vehicle_fitment_clause = await _build_vehicle_fitment_clause(
        db,
        params,
        vehicle_manufacturer=vehicle_manufacturer,
        vehicle_model=vehicle_model,
        vehicle_submodel=vehicle_submodel,
        vehicle_year=vehicle_year,
    )
    if vehicle_fitment_clause:
        conditions.append(vehicle_fitment_clause)

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

    # ── Helper: fetch all matching parts per type + supplier list ──────────────
    async def _fetch_type(part_type_values: list, include_general: bool = False) -> List[Dict[str, Any]]:
        candidate_limit = max(int(per_type or 4) * 6, 24)
        type_params = {**params, "pt": part_type_values, "lim": per_type, "candidate_lim": candidate_limit}
        _unsafe_sql_tokens = (";", "--", "/*", "*/")
        if any(tok in where_sql for tok in _unsafe_sql_tokens):
            raise HTTPException(status_code=400, detail="unsafe_query_rejected")

        if meili_ids:
            # ── Meilisearch path: rank-preserving unnest JOIN ─────────────────
            # UUIDs come from our own index — hex+dash only, no SQL injection risk.
            # Pass as a Python list so asyncpg maps it to a PostgreSQL text[] array.
            pt_condition = "pc.part_type = ANY(:pt)"
            if include_general:
                pt_condition = "(pc.part_type = ANY(:pt) OR pc.part_type IS NULL OR pc.part_type = '' OR pc.part_type ILIKE 'unknown' OR pc.part_type ILIKE 'general' OR pc.part_type ILIKE 'כללי')"

            part_rows = (await db.execute(
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
                    WHERE {where_sql} AND {pt_condition}
                    ORDER BY ranked.pos ASC,
                    (
                        SELECT COUNT(*) FROM supplier_parts sp
                        WHERE sp.part_id = pc.id AND sp.is_available = TRUE
                    ) DESC
                    LIMIT :candidate_lim
                """),
                {**type_params, "uuid_arr": meili_ids},
            )).fetchall()
        else:
            # ── ILIKE fallback path ───────────────────────────────────────────
            if relevance_sql and any(tok in relevance_sql for tok in _unsafe_sql_tokens):
                raise HTTPException(status_code=400, detail="unsafe_query_rejected")
            pt_condition = "pc.part_type = ANY(:pt)"
            if include_general:
                pt_condition = "(pc.part_type = ANY(:pt) OR pc.part_type IS NULL OR pc.part_type = '' OR pc.part_type ILIKE 'unknown' OR pc.part_type ILIKE 'general' OR pc.part_type ILIKE 'כללי')"

            part_rows = (await db.execute(
                text(f"""
                    SELECT
                        pc.id, pc.sku, pc.name, pc.name_he, pc.manufacturer,
                        pc.category, pc.part_type, pc.base_price,
                        pc.min_price_ils, pc.max_price_ils, pc.description,
                        pc.oem_number, pc.barcode, pc.weight_kg,
                        pc.is_safety_critical, pc.part_condition,
                        pc.created_at, pc.updated_at{score_col}
                    FROM parts_catalog pc
                    WHERE {where_sql} AND {pt_condition}
                    ORDER BY {relevance_sql}
                    (
                        SELECT COUNT(*) FROM supplier_parts sp
                        WHERE sp.part_id = pc.id AND sp.is_available = TRUE
                    ) DESC,
                    pc.base_price ASC NULLS LAST
                    LIMIT :candidate_lim
                """),
                type_params,
            )).fetchall()

            # Allow all ILIKE matches — score just affects ordering, not rejection

        part_row = None
        matching_rows: List[Any] = []
        for candidate_row in part_rows:
            if not selected_part_family:
                matching_rows.append(candidate_row)
            else:
                candidate_family = classify_part_type_family(
                    candidate_row[5],
                    candidate_row[6],
                    candidate_row[2],
                    candidate_row[3],
                    candidate_row[10],
                )
                if candidate_family and candidate_family.id == selected_part_family.id:
                    matching_rows.append(candidate_row)

        # Backwards compat: also keep the first-match logic for TypeSection display
        part_row = matching_rows[0] if matching_rows else None

        if not part_row:
            return []

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

        results: List[Dict[str, Any]] = [{"part": part_dict, "suppliers": suppliers_list}]

        # ── Batch-fetch best supplier for additional matching rows ────────────
        if len(matching_rows) > 1:
            extra_ids = [str(row[0]) for row in matching_rows[1:]]
            try:
                batch_rows = (await db.execute(
                    text("""
                        SELECT DISTINCT ON (sp.part_id)
                            sp.part_id::text,
                            sp.id::text             AS sp_id,
                            s.name                  AS supplier_name,
                            s.country               AS supplier_country,
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
                        WHERE sp.part_id = ANY(CAST(:extra_ids AS uuid[]))
                          AND sp.is_available = TRUE
                        ORDER BY sp.part_id,
                                 COALESCE(sp.price_ils, sp.price_usd * 3.72) ASC
                    """),
                    {"extra_ids": extra_ids},
                )).fetchall()
            except Exception:
                batch_rows = []
            best_sup_map: Dict[str, Any] = {row[0]: row for row in batch_rows}
            for extra_row in matching_rows[1:]:
                extra_id = str(extra_row[0])
                extra_dict = {
                    "id":               extra_id,
                    "sku":              extra_row[1],
                    "name":             extra_row[2],
                    "name_he":          extra_row[3],
                    "manufacturer":     extra_row[4],
                    "category":         extra_row[5],
                    "part_type":        extra_row[6],
                    "base_price":       float(extra_row[7]) if extra_row[7] else None,
                    "min_price_ils":    float(extra_row[8]) if extra_row[8] else None,
                    "max_price_ils":    float(extra_row[9]) if extra_row[9] else None,
                    "description":      extra_row[10],
                    "oem_number":       extra_row[11],
                    "barcode":          extra_row[12],
                    "weight_kg":        float(extra_row[13]) if extra_row[13] else None,
                    "is_safety_critical": extra_row[14],
                    "part_condition":   extra_row[15],
                    "created_at":       extra_row[16].isoformat() if extra_row[16] else None,
                    "updated_at":       extra_row[17].isoformat() if extra_row[17] else None,
                }
                bsp = best_sup_map.get(extra_id)
                extra_suppliers: List[Dict[str, Any]] = []
                if bsp:
                    b_price_ils = float(bsp[6]) if bsp[6] else (float(bsp[5]) * 3.72 if bsp[5] else None)
                    extra_suppliers = [{
                        "supplier_part_id":        bsp[1],
                        "supplier_name":           _mask_supplier(bsp[2]),
                        "supplier_country":        bsp[3] or "",
                        "supplier_sku":            bsp[4],
                        "price_usd":               float(bsp[5]) if bsp[5] else None,
                        "price_ils":               round(b_price_ils, 2) if b_price_ils else None,
                        "shipping_cost_ils":       float(bsp[7]) if bsp[7] else None,
                        "availability":            bsp[8],
                        "warranty_months":         bsp[9],
                        "estimated_delivery_days": bsp[10],
                        "stock_quantity":          bsp[11],
                        "supplier_url":            bsp[12],
                        "express_available":       bsp[13],
                        "express_price_ils":       float(bsp[14]) if bsp[14] else None,
                        "express_delivery_days":   bsp[15],
                        "express_cutoff_time":     bsp[16],
                        "last_checked_at":         bsp[17].isoformat() if bsp[17] else None,
                    }]
                results.append({"part": extra_dict, "suppliers": extra_suppliers})

        return results

    # ── Run all 3 type queries sequentially (shared AsyncSession is not
    # ── safe for concurrent use — concurrent gather causes InvalidRequestError)
    original_res    = await _fetch_type(["Original"])
    oem_res         = await _fetch_type(["OEM"])
    aftermarket_res = await _fetch_type(["Aftermarket", "Refurbished"], include_general=True)

    # Primary bucket (first/best per type) — kept for the 3-column TypeSection widget
    def _primary(res: List[Dict[str, Any]]) -> Dict[str, Any]:
        return res[0] if res else {"part": None, "suppliers": []}

    _search_result = {
        "original":         _primary(original_res),
        "oem":              _primary(oem_res),
        "aftermarket":      _primary(aftermarket_res),
        "all_parts":        [*original_res, *oem_res, *aftermarket_res],
        "results_per_type": per_type,
        "query":            query,
    }
    _store_cached_search_response(_s_cache_key, _search_result)
    return _search_result


# ==============================================================================
# GET /api/v1/parts/categories
# ==============================================================================

@router.get("/api/v1/parts/categories")
async def get_categories(
    vehicle_manufacturer: Optional[str] = None,
    vehicle_model: Optional[str] = None,
    vehicle_submodel: Optional[str] = None,
    vehicle_year: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
):
    cache_key = _category_cache_key(vehicle_manufacturer, vehicle_model, vehicle_submodel, vehicle_year)
    cached_payload = _get_cached_category_response(cache_key)
    if cached_payload is not None:
        return cached_payload

    if not any([vehicle_manufacturer, vehicle_model, vehicle_submodel, vehicle_year]):
        family_counts: Dict[str, int] = {family.id: 0 for family in iter_part_type_families()}
        flat_counts: Dict[str, int] = {family.label: 0 for family in iter_part_type_families()}
        fallback_counts: Dict[str, int] = {c: 0 for c in CANONICAL_FILTER_CATEGORIES}
        families = [family.serialize(count=0) for family in iter_part_type_families()]
        response = {
            "categories": [family["id"] for family in families],
            "counts": {**fallback_counts, **flat_counts},
            "family_counts": family_counts,
            "families": families,
            "groups": get_part_type_groups(family_counts),
            "total": len(families),
        }
        _store_cached_category_response(cache_key, response)
        return response

    conditions: List[str] = ["pc.is_active = TRUE"]
    params: Dict[str, Any] = {}

    vehicle_fitment_clause = await _build_vehicle_fitment_clause(
        db,
        params,
        vehicle_manufacturer=vehicle_manufacturer,
        vehicle_model=vehicle_model,
        vehicle_submodel=vehicle_submodel,
        vehicle_year=vehicle_year,
        prefix="catfit",
    )
    if vehicle_fitment_clause:
        conditions.append(vehicle_fitment_clause)

    where_sql = " AND ".join(conditions)
    rows = (
        await db.execute(
            text(
                f"""
                SELECT
                    pc.category,
                    pc.part_type,
                    pc.name,
                    pc.name_he,
                    pc.description
                FROM parts_catalog pc
                WHERE {where_sql}
                """
            ),
            params,
        )
    ).fetchall()

    family_counts: Dict[str, int] = {family.id: 0 for family in iter_part_type_families()}
    flat_counts: Dict[str, int] = {family.label: 0 for family in iter_part_type_families()}
    fallback_counts: Dict[str, int] = {c: 0 for c in CANONICAL_FILTER_CATEGORIES}

    for raw_category, raw_part_type, name, name_he, description in rows:
        family = classify_part_type_family(raw_category, raw_part_type, name, name_he, description)
        if family:
            family_counts[family.id] = family_counts.get(family.id, 0) + 1
            flat_counts[family.label] = flat_counts.get(family.label, 0) + 1
            continue
        canonical = _normalize_filter_category(raw_category)
        if canonical:
            fallback_counts[canonical] = fallback_counts.get(canonical, 0) + 1

    groups = get_part_type_groups(family_counts)
    group_order = {group["id"]: idx for idx, group in enumerate(groups)}
    families = sorted(
        [family.serialize(count=family_counts.get(family.id, 0)) for family in iter_part_type_families()],
        key=lambda family: (
            group_order.get(family["group_id"], 999),
            -int(family.get("count", 0)),
            family["label"],
        ),
    )
    counts: Dict[str, int] = {**fallback_counts}
    counts.update(flat_counts)
    categories = [family["id"] for family in families]
    response = {
        "categories": categories,
        "counts": counts,
        "family_counts": family_counts,
        "families": families,
        "groups": groups,
        "total": len(families),
    }
    _store_cached_category_response(cache_key, response)
    return response


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
# GET /api/parts/by-license-plate/{license_plate}
# ==============================================================================

@router.get("/api/parts/by-license-plate/{license_plate}")
async def get_parts_by_license_plate(
    license_plate: str,
    db: AsyncSession = Depends(get_db),
    request: Request = None,
    redis=Depends(get_redis),
):
    if redis and request:
        ip = request.client.host if request.client else "unknown"
        allowed = await check_rate_limit(redis, f"rate:parts_by_plate:{ip}", 20, 60)
        if not allowed:
            raise HTTPException(status_code=429, detail="too_many_requests")

    vehicle = await _lookup_vehicle_by_license_plate(license_plate)

    if not vehicle["tozeret_cd"] or not vehicle["degem_cd"] or not vehicle["shnat_yitzur"]:
        raise HTTPException(status_code=422, detail="missing_vehicle_codes")

    from catalog_scraper import get_fitment_by_gov_codes

    parts_rows = await get_fitment_by_gov_codes(
        tozeret_cd=int(vehicle["tozeret_cd"]),
        degem_cd=int(vehicle["degem_cd"]),
        shnat_yitzur=int(vehicle["shnat_yitzur"]),
        db=db,
    )

    grouped_parts: Dict[str, List[Dict[str, Any]]] = {
        "OEM": [],
        "OE_equivalent": [],
        "economy": [],
    }

    for row in parts_rows:
        part_payload = {
            "id": str(row.get("id")) if row.get("id") is not None else None,
            "name": row.get("name"),
            "part_condition": row.get("part_condition"),
            "oem_number": row.get("oem_number"),
            "online_price_ils": float(row["online_price_ils"]) if row.get("online_price_ils") is not None else None,
            "brand_name": row.get("brand_name"),
            "brand_tier": row.get("brand_tier"),
            "brand_logo": row.get("brand_logo"),
        }

        part_condition = str(row.get("part_condition") or "").strip().lower()
        brand_tier = str(row.get("brand_tier") or "").strip().lower()
        if part_condition == "oem":
            grouped_parts["OEM"].append(part_payload)
        elif brand_tier == "oe_equivalent":
            grouped_parts["OE_equivalent"].append(part_payload)
        else:
            grouped_parts["economy"].append(part_payload)

    vehicle_payload = {
        "manufacturer": vehicle.get("manufacturer"),
        "model": vehicle.get("model"),
        "year": vehicle.get("shnat_yitzur"),
        "engine": vehicle.get("engine"),
    }

    if parts_rows:
        top_row = parts_rows[0]
        vehicle_payload["manufacturer"] = vehicle_payload["manufacturer"] or top_row.get("manufacturer")
        vehicle_payload["model"] = vehicle_payload["model"] or top_row.get("model")
        vehicle_payload["year"] = vehicle_payload["year"] or top_row.get("year")
        vehicle_payload["engine"] = vehicle_payload["engine"] or top_row.get("engine")

    return {
        "vehicle": vehicle_payload,
        "parts": grouped_parts,
    }


# ==============================================================================
# GET /api/v1/parts/manufacturers
# ==============================================================================

@router.get("/api/v1/parts/manufacturers")
async def get_manufacturers(db: AsyncSession = Depends(get_db)):
    non_display = {
        "Stellantis", "General Motors", "Volkswagen Group", "BMW Group", "Toyota Group",
        "Honda Group", "Hyundai Motor Group", "Geely Group", "Tata Motors", "SAIC", "GAC",
        "GEN", "Renault Samsung", "Citroën", "JAECOO",
    }

    def _fold(v: str) -> str:
        if not v:
            return ""
        v = unicodedata.normalize("NFKD", v)
        v = "".join(ch for ch in v if not unicodedata.combining(ch))
        return v.lower().strip()

    def _norm(v: str) -> str:
        if not v:
            return ""
        return re.sub(r"\s+", " ", re.sub(r"[^\w\u0590-\u05FF]+", " ", v.lower())).strip()

    parts_brand_blocklist = {
        "bosch", "brembo", "champion", "fram", "mann", "ngk", "valeo", "denso",
        "luk", "sachs", "delphi", "mahle", "hella", "mando", "trw", "aisin",
    }
    truck_brand_blocklist = {
        "man", "hino", "scania", "daf", "iveco", "kenworth", "peterbilt",
        "freightliner", "mack", "western star", "volvo trucks", "renault trucks",
    }

    # 1) Primary source: curated passenger-car registry.
    bres = await db.execute(text("""
        SELECT name, name_he, aliases, logo_url
        FROM car_brands
        WHERE is_active = TRUE
        ORDER BY name ASC
    """))
    brand_rows = bres.fetchall()

    if brand_rows:
        vres = await db.execute(text("""
            SELECT manufacturer, COUNT(*) AS cnt
            FROM vehicles
            WHERE manufacturer IS NOT NULL
              AND manufacturer <> ''
            GROUP BY manufacturer
        """))
        vehicle_counts = {r[0]: int(r[1]) for r in vres.fetchall() if r[0]}

        manufacturers: List[str] = []
        counts: Dict[str, int] = {}
        logos: Dict[str, str] = {}

        for bname, _bname_he, baliases, blogo in brand_rows:
            if not bname:
                continue
            if bname in non_display:
                continue
            manufacturers.append(bname)
            variants = [bname, *((baliases or []))]
            c = 0
            for vmfr, cnt in vehicle_counts.items():
                vmfr_norm = _norm(vmfr)
                if any(vmfr_norm and _norm(v) and (vmfr_norm == _norm(v) or vmfr_norm in _norm(v) or _norm(v) in vmfr_norm) for v in variants if v):
                    c += cnt
            counts[bname] = c
            if blogo:
                logos[bname] = blogo

        folded_seen = set()
        filtered = []
        for m in manufacturers:
            fk = _fold(m)
            if fk in folded_seen:
                continue
            folded_seen.add(fk)
            filtered.append(m)

        counts = {k: v for k, v in counts.items() if k in filtered}
        logos = {k: v for k, v in logos.items() if k in filtered}

        return {
            "manufacturers": filtered,
            "counts": counts,
            "logos": logos,
            "total": len(filtered),
        }

    # 2) Fallback: derive from vehicles/parts but filter out parts brands and truck brands.
    raw_rows = (await db.execute(text("""
        SELECT manufacturer FROM vehicles
        WHERE manufacturer IS NOT NULL AND manufacturer <> ''
        UNION
        SELECT manufacturer FROM parts_catalog
        WHERE is_active = TRUE
          AND manufacturer IS NOT NULL
          AND manufacturer <> ''
    """))).fetchall()

    manufacturers = []
    seen = set()
    for row in raw_rows:
        raw = (row[0] or "").strip()
        if not raw:
            continue
        n = _norm(raw)
        if not n:
            continue
        if "חלפים" in n and "מרצדס" not in n:
            continue
        if n in parts_brand_blocklist:
            continue
        if n in truck_brand_blocklist:
            continue
        if n in seen:
            continue
        seen.add(n)
        manufacturers.append(raw)

    manufacturers.sort(key=lambda x: x.lower())
    counts = {m: 0 for m in manufacturers}

    return {
        "manufacturers": manufacturers,
        "counts": counts,
        "logos": {},
        "total": len(manufacturers),
    }


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
    models_cache_key = (str(manufacturer or '').strip().casefold(),)
    cached_models = _get_cached_hierarchy_response(MODELS_RESPONSE_CACHE, models_cache_key)
    if cached_models is not None:
        return cached_models

    def _is_model_allowed_for_mfr(mfr: str, model: str) -> bool:
        cm = normalize_manufacturer_name(mfr, mfr).casefold()
        mm = (model or "").casefold()
        if cm == "citroen" and mm.startswith("partner"):
            return False
        if cm == "chevrolet":
            if mm == "gm" or mm.startswith("gm ") or mm.startswith("gmc") or mm.startswith("gnc") or "gmc" in mm:
                return False
            if mm in {"acadia", "park", "parck", "xt6", "yukon"}:
                return False
            if mm.startswith("park ") or mm.startswith("parck ") or mm == "roadmaster" or mm == "certury":
                return False
        if cm == "peugeot":
            if mm in {"חומרים", "פריטים סטנדרטים", "גרנדלנד", "קורסה", "קורסה חשמלית"}:
                return False
        return True

    async def _resolve_variants(mfr: str) -> List[str]:
        variants: list[str] = [mfr]
        try:
            from manufacturer_normalization import normalize_manufacturer_name as _norm_mfr
            canonical = _norm_mfr(mfr, mfr)
            if canonical and canonical not in variants:
                variants.append(canonical)
        except Exception:
            pass
        try:
            brand_row = (await db.execute(text("""
                SELECT name, name_he, aliases
                FROM car_brands
                WHERE is_active = TRUE
                  AND (
                    name ILIKE :mfr_lookup
                                        OR name ILIKE CONCAT('%', :mfr_lookup, '%')
                    OR name_he ILIKE :mfr_lookup
                                        OR name_he ILIKE CONCAT('%', :mfr_lookup, '%')
                    OR :mfr_lookup ILIKE CONCAT('%', name_he, '%')
                    OR EXISTS (
                        SELECT 1 FROM unnest(aliases) a
                        WHERE :mfr_lookup ILIKE CONCAT('%', a, '%')
                           OR a ILIKE :mfr_lookup
                                                     OR a ILIKE CONCAT('%', :mfr_lookup, '%')
                    )
                  )
                LIMIT 1
            """), {"mfr_lookup": mfr})).fetchone()
            if brand_row:
                variants = list({brand_row[0], brand_row[1], *(brand_row[2] or []), *variants})
        except Exception:
            variants = [mfr]
        return [v for v in variants if v and str(v).strip()]

    def _dedupe_clean(rows: List[Any], mfr_filter: Optional[str] = None) -> List[str]:
        out: Dict[str, str] = {}
        for row in rows:
            raw = row[0] if isinstance(row, (tuple, list)) else row
            if not isinstance(raw, str):
                try:
                    raw = row[0]
                except Exception:
                    raw = str(raw) if raw is not None else ""
            base, _sub = _split_vehicle_model_variant(mfr_filter or manufacturer, raw)
            if not base:
                continue
            if mfr_filter and not _is_model_allowed_for_mfr(mfr_filter, base):
                continue
            k = base.upper()
            prev = out.get(k)
            if prev is None or len(base) < len(prev):
                out[k] = base
        return sorted(out.values())

    if manufacturer:
        variants = await _resolve_variants(manufacturer)
        clauses = []
        compat_clauses = []
        params_mfr: Dict[str, Any] = {}
        for idx, v in enumerate(variants):
            k = f"m_{idx}"
            clauses.append(f"manufacturer ILIKE :{k}")
            compat_clauses.append(
                f"LOWER(TRIM(COALESCE(elem->>'make', elem->>'manufacturer', ''))) = LOWER(TRIM(:{k}))"
            )
            params_mfr[k] = f"%{v}%"
        where_mfr = " OR ".join(clauses) if clauses else "manufacturer ILIKE :m_0"
        where_compat_mfr = " OR ".join(compat_clauses) if compat_clauses else "LOWER(TRIM(COALESCE(elem->>'make', elem->>'manufacturer', ''))) = LOWER(TRIM(:m_0))"
        if not clauses:
            params_mfr["m_0"] = f"%{manufacturer}%"

        # 0) Preferred curated source: XLS hierarchy table.
        try:
            x_rows = (await db.execute(text(f"""
                SELECT DISTINCT model
                FROM vehicle_hierarchy_xls
                WHERE ({where_mfr})
                ORDER BY model
            """), params_mfr)).fetchall()
            x_models = _dedupe_clean(x_rows, manufacturer)
            if x_models:
                response = {"models": x_models, "total": len(x_models)}
                _store_cached_hierarchy_response(MODELS_RESPONSE_CACHE, models_cache_key, response)
                return response
        except Exception:
            pass

        # 1) Preferred source: vehicles table (real manufacturer->model hierarchy)
        v_rows = (await db.execute(text(f"""
            SELECT DISTINCT model
            FROM vehicles
            WHERE model IS NOT NULL
              AND model <> ''
              AND ({where_mfr})
            ORDER BY model
        """), params_mfr)).fetchall()
        models = _dedupe_clean(v_rows, manufacturer)
        if models:
            response = {"models": models, "total": len(models)}
            _store_cached_hierarchy_response(MODELS_RESPONSE_CACHE, models_cache_key, response)
            return response

        # 2) Fallback: compatible_vehicles extracted from parts_catalog
        p_rows = (await db.execute(text(f"""
            SELECT DISTINCT COALESCE(elem->>'model', elem->>'model_year') AS model
            FROM parts_catalog,
                 jsonb_array_elements(compatible_vehicles) AS elem
            WHERE compatible_vehicles IS NOT NULL
              AND jsonb_typeof(compatible_vehicles) = 'array'
                            AND ({where_compat_mfr})
              AND COALESCE(elem->>'model', elem->>'model_year') IS NOT NULL
              AND COALESCE(elem->>'model', elem->>'model_year') <> ''
            ORDER BY model
        """), params_mfr)).fetchall()
        models = _dedupe_clean(p_rows, manufacturer)
        if models:
            response = {"models": models, "total": len(models)}
            _store_cached_hierarchy_response(MODELS_RESPONSE_CACHE, models_cache_key, response)
            return response

        # Keep hierarchy strict: no cross-manufacturer fallback.
        response = {"models": [], "total": 0}
        _store_cached_hierarchy_response(MODELS_RESPONSE_CACHE, models_cache_key, response)
        return response

    # Global (no manufacturer selected): keep existing broad extraction.
    p_rows = (await db.execute(text("""
        SELECT DISTINCT COALESCE(elem->>'model', elem->>'model_year') AS model
        FROM parts_catalog,
             jsonb_array_elements(compatible_vehicles) AS elem
        WHERE compatible_vehicles IS NOT NULL
          AND jsonb_typeof(compatible_vehicles) = 'array'
          AND COALESCE(elem->>'model', elem->>'model_year') IS NOT NULL
          AND COALESCE(elem->>'model', elem->>'model_year') <> ''
        ORDER BY model
    """))).fetchall()
    models = _dedupe_clean(p_rows, manufacturer)

    if not models:
        v_rows = (await db.execute(text("""
            SELECT DISTINCT model
            FROM vehicles
            WHERE model IS NOT NULL
              AND model <> ''
            ORDER BY model
        """))).fetchall()
        models = _dedupe_clean(v_rows, manufacturer)

    response = {"models": models, "total": len(models)}
    _store_cached_hierarchy_response(MODELS_RESPONSE_CACHE, models_cache_key, response)
    return response


@router.get("/api/v1/parts/submodels")
async def get_submodels(
    manufacturer: Optional[str] = None,
    model: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """Return sub-model/trim options for selected manufacturer + model."""
    if not manufacturer or not model:
        return {"submodels": [], "total": 0}

    base_model = normalize_vehicle_model_name(model)
    if not base_model:
        return {"submodels": [], "total": 0}

    submodels_cache_key = (
        str(manufacturer or '').strip().casefold(),
        str(base_model or '').strip().casefold(),
    )
    cached_submodels = _get_cached_hierarchy_response(SUBMODELS_RESPONSE_CACHE, submodels_cache_key)
    if cached_submodels is not None:
        return cached_submodels

    canon = normalize_manufacturer_name(manufacturer, manufacturer)
    submodel_values = set()

    def _derived_submodel(raw_model: Optional[str], gov: Any = None) -> str:
        model_clean, derived_submodel = _split_vehicle_model_variant(canon, raw_model)
        gov_submodel = ""
        if isinstance(gov, dict):
            gov_submodel = normalize_vehicle_submodel_name(str(gov.get("sub_model") or ""))
        if gov_submodel and model_clean.casefold() == base_model.casefold():
            return gov_submodel
        if model_clean.casefold() != base_model.casefold():
            return ""
        return derived_submodel

    # Preferred curated source: XLS hierarchy table.
    try:
        x_rows = (await db.execute(text("""
            SELECT DISTINCT sub_model
            FROM vehicle_hierarchy_xls
            WHERE LOWER(TRIM(manufacturer)) = LOWER(TRIM(:mfr))
              AND LOWER(TRIM(model)) = LOWER(TRIM(:model))
              AND COALESCE(TRIM(sub_model), '') <> ''
            ORDER BY sub_model
        """), {"mfr": canon, "model": base_model})).fetchall()
        submodel_values.update(
            normalize_vehicle_submodel_name(r[0])
            for r in x_rows
            if r and r[0]
        )
        curated_submodels = sorted(s for s in submodel_values if s)
        if curated_submodels:
            response = {"submodels": curated_submodels, "total": len(curated_submodels)}
            _store_cached_hierarchy_response(SUBMODELS_RESPONSE_CACHE, submodels_cache_key, response)
            return response
    except Exception:
        pass

    # Preferred fallback: imported structured compatibility data from parts_catalog.
    try:
        p_rows = (await db.execute(text("""
            SELECT DISTINCT elem->>'sub_model' AS sub_model
            FROM parts_catalog,
                 jsonb_array_elements(compatible_vehicles) AS elem
            WHERE is_active = TRUE
              AND compatible_vehicles IS NOT NULL
              AND jsonb_typeof(compatible_vehicles) = 'array'
              AND LOWER(TRIM(COALESCE(elem->>'make', elem->>'manufacturer', ''))) = LOWER(TRIM(:mfr))
              AND LOWER(TRIM(COALESCE(elem->>'model', ''))) = LOWER(TRIM(:model))
              AND COALESCE(TRIM(elem->>'sub_model'), '') <> ''
            ORDER BY sub_model
        """), {"mfr": canon, "model": base_model})).fetchall()
        submodel_values.update(
            normalize_vehicle_submodel_name(r[0])
            for r in p_rows
            if r and r[0]
        )
    except Exception:
        pass

    try:
        v_rows = (await db.execute(text("""
            SELECT DISTINCT model, gov_api_data
            FROM vehicles
            WHERE LOWER(TRIM(manufacturer)) = LOWER(TRIM(:mfr))
              AND model IS NOT NULL
              AND model <> ''
            ORDER BY model
        """), {"mfr": canon})).fetchall()
        submodel_values.update(_derived_submodel(row[0], row[1]) for row in v_rows)
    except Exception:
        pass

    submodels = sorted(s for s in submodel_values if s)
    response = {"submodels": submodels, "total": len(submodels)}
    _store_cached_hierarchy_response(SUBMODELS_RESPONSE_CACHE, submodels_cache_key, response)
    return response

@router.get("/api/v1/parts/years")
async def get_years(
    manufacturer: Optional[str] = None,
    model: Optional[str] = None,
    sub_model: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """Return available years for selected hierarchy (manufacturer/model/sub-model)."""
    if not manufacturer or not model:
        return {"years": [], "total": 0}

    canon = normalize_manufacturer_name(manufacturer, manufacturer)
    base_model = normalize_vehicle_model_name(model)
    wanted_sub = normalize_vehicle_submodel_name(sub_model or "")
    years_cache_key = (
        str(canon or '').strip().casefold(),
        str(base_model or '').strip().casefold(),
        str(wanted_sub or '').strip().casefold(),
    )
    cached_years = _get_cached_hierarchy_response(YEARS_RESPONSE_CACHE, years_cache_key)
    if cached_years is not None:
        return cached_years
    years = set()

    def _derived_submodel(raw_model: Optional[str], gov: Any = None) -> str:
        model_clean, derived_submodel = _split_vehicle_model_variant(canon, raw_model)
        gov_submodel = ""
        if isinstance(gov, dict):
            gov_submodel = normalize_vehicle_submodel_name(str(gov.get("sub_model") or ""))
        if gov_submodel and model_clean.casefold() == base_model.casefold():
            return gov_submodel
        if model_clean.casefold() != base_model.casefold():
            return ""
        return derived_submodel

    # Preferred curated source: XLS hierarchy table.
    try:
        if wanted_sub:
            x_rows = (await db.execute(text("""
                SELECT DISTINCT year_hint, year_from, year_to
                FROM vehicle_hierarchy_xls
                WHERE LOWER(TRIM(manufacturer)) = LOWER(TRIM(:mfr))
                  AND LOWER(TRIM(model)) = LOWER(TRIM(:model))
                  AND LOWER(TRIM(COALESCE(sub_model, ''))) = LOWER(TRIM(:sub_model))
                ORDER BY year_from, year_to, year_hint
            """), {"mfr": canon, "model": base_model, "sub_model": wanted_sub})).fetchall()
        else:
            x_rows = (await db.execute(text("""
                SELECT DISTINCT year_hint, year_from, year_to
                FROM vehicle_hierarchy_xls
                WHERE LOWER(TRIM(manufacturer)) = LOWER(TRIM(:mfr))
                  AND LOWER(TRIM(model)) = LOWER(TRIM(:model))
                ORDER BY year_from, year_to, year_hint
            """), {"mfr": canon, "model": base_model})).fetchall()
        for yy, y_from, y_to in x_rows:
            if isinstance(y_from, int) and isinstance(y_to, int) and 1990 <= y_from <= y_to <= 2027:
                for yv in range(y_from, y_to + 1):
                    years.add(yv)
            elif isinstance(yy, int) and 1990 <= yy <= 2027:
                years.add(yy)
    except Exception:
        pass

    if years:
        out = sorted(years)
        response = {"years": out, "total": len(out)}
        _store_cached_hierarchy_response(YEARS_RESPONSE_CACHE, years_cache_key, response)
        return response

    # 1) Fast path from vehicles table (indexed manufacturer/model/year) before
    # scanning parts_catalog compatible_vehicles JSON.
    v_rows = (await db.execute(text("""
        SELECT model, year, gov_api_data
        FROM vehicles
        WHERE LOWER(TRIM(manufacturer)) = LOWER(TRIM(:mfr))
          AND model IS NOT NULL
          AND model <> ''
    """), {"mfr": canon})).fetchall()
    for m, y, gov in v_rows:
        model_clean, derived_submodel = _split_vehicle_model_variant(canon, m)
        if not model_clean:
            continue
        if model_clean.casefold() != base_model.casefold():
            continue
        if wanted_sub:
            gs = _derived_submodel(m, gov) or derived_submodel
            if gs.casefold() != wanted_sub.casefold():
                continue
        if isinstance(y, int) and 1990 <= y <= 2027:
            years.add(y)

    if years:
        out = sorted(years)
        response = {"years": out, "total": len(out)}
        _store_cached_hierarchy_response(YEARS_RESPONSE_CACHE, years_cache_key, response)
        return response

    # 2) compatibility range fallback from parts_catalog
    # Use GIN @> pre-filter so Postgres uses idx_pc_compat_vehicles_gin to
    # narrow down candidate rows before the expensive lateral expansion.
    _safe_canon = canon.replace("'", "''")
    _gin_mfr  = f'[{{"manufacturer": "{_safe_canon}"}}]'
    _gin_make = f'[{{"make": "{_safe_canon}"}}]'
    p_rows = (await db.execute(text("""
        SELECT
            elem->>'model' AS item_model,
            elem->>'model_year' AS item_model_year,
            elem->>'sub_model' AS item_sub_model,
            elem->>'year_from' AS item_year_from,
            elem->>'year_to' AS item_year_to,
            elem->>'year' AS item_year
        FROM parts_catalog,
             jsonb_array_elements(compatible_vehicles) AS elem
        WHERE is_active = TRUE
          AND compatible_vehicles IS NOT NULL
          AND jsonb_typeof(compatible_vehicles) = 'array'
          AND (
              compatible_vehicles @> CAST(:gin_mfr AS jsonb)
              OR compatible_vehicles @> CAST(:gin_make AS jsonb)
          )
          AND (
              LOWER(TRIM(COALESCE(elem->>'model', ''))) = LOWER(TRIM(:model))
              OR LOWER(TRIM(COALESCE(elem->>'model_year', ''))) LIKE LOWER(TRIM(:model)) || ' %'
          )
          AND (:sub_model = '' OR LOWER(TRIM(COALESCE(elem->>'sub_model', ''))) = LOWER(TRIM(:sub_model)))
          AND COALESCE(elem->>'model', elem->>'model_year') IS NOT NULL
          AND COALESCE(elem->>'model', elem->>'model_year') <> ''
    """), {"mfr": canon, "gin_mfr": _gin_mfr, "gin_make": _gin_make, "model": base_model, "sub_model": wanted_sub})).fetchall()
    for item_model, item_model_year, item_sub_model, item_year_from, item_year_to, item_year in p_rows:
        model_value = item_model or item_model_year
        resolved_model, derived_submodel = _split_vehicle_model_variant(canon, model_value)
        if not resolved_model or resolved_model.casefold() != base_model.casefold():
            continue
        if wanted_sub:
            resolved_submodel = normalize_vehicle_submodel_name(str(item_sub_model or "")) or derived_submodel
            if resolved_submodel.casefold() != wanted_sub.casefold():
                continue
        try:
            y_from = int(item_year_from) if item_year_from is not None and str(item_year_from).isdigit() else None
            y_to = int(item_year_to) if item_year_to is not None and str(item_year_to).isdigit() else None
        except Exception:
            y_from = y_to = None
        if y_from and y_to and 1990 <= y_from <= y_to <= 2027:
            for yy in range(y_from, y_to + 1):
                years.add(yy)
            continue
        try:
            yv = int(item_year) if item_year is not None and str(item_year).isdigit() else None
        except Exception:
            yv = None
        if yv and 1990 <= yv <= 2027:
            years.add(yv)

    out = sorted(years)
    response = {"years": out, "total": len(out)}
    _store_cached_hierarchy_response(YEARS_RESPONSE_CACHE, years_cache_key, response)
    return response


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


