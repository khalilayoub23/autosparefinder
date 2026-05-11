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
import asyncio
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
    CarBrand, User, async_session_factory,
)
from BACKEND_AUTH_SECURITY import (
    get_redis, check_rate_limit, get_current_user,
)
from currency_rate import get_usd_to_ils_rate
from BACKEND_AI_AGENTS import get_agent, get_supplier_shipping as _get_ship
from resilience import retry_with_backoff
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
SEARCH_ENABLE_HF_QUERY_NORMALIZATION = os.getenv("SEARCH_ENABLE_HF_QUERY_NORMALIZATION", "0").strip().lower() in ("1", "true", "yes", "on")
SEARCH_HF_QUERY_NORMALIZATION_TIMEOUT_S = float(os.getenv("SEARCH_HF_QUERY_NORMALIZATION_TIMEOUT_S", "0.35"))
SEARCH_ENABLE_VECTOR_RERANK = os.getenv("SEARCH_ENABLE_VECTOR_RERANK", "0").strip().lower() in ("1", "true", "yes", "on")
SEARCH_VECTOR_RERANK_MIN_QUERY_LEN = int(os.getenv("SEARCH_VECTOR_RERANK_MIN_QUERY_LEN", "4"))
GOV_IL_LICENSE_RESOURCE_ID = "053cea08-09bc-40ec-8f7a-156f0677aff3"
GOV_IL_DATASTORE_URL = "https://data.gov.il/api/3/action/datastore_search"

_EXTERNAL_API_MAX_CONCURRENCY = int(os.getenv("EXTERNAL_API_MAX_CONCURRENCY", "8"))
_EXTERNAL_API_SEMAPHORE = asyncio.Semaphore(max(1, _EXTERNAL_API_MAX_CONCURRENCY))

PLATE_LOOKUP_CACHE_TTL_S = float(os.getenv("PLATE_LOOKUP_CACHE_TTL_S", "180"))
VIN_LOOKUP_CACHE_TTL_S = float(os.getenv("VIN_LOOKUP_CACHE_TTL_S", "300"))
PLATE_LOOKUP_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}
VIN_LOOKUP_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}
PLATE_CALL_LOCKS: Dict[str, asyncio.Lock] = {}
VIN_CALL_LOCKS: Dict[str, asyncio.Lock] = {}


class _SimpleCircuitBreaker:
    def __init__(self, failure_threshold: int, recovery_seconds: float) -> None:
        self.failure_threshold = max(1, int(failure_threshold))
        self.recovery_seconds = max(1.0, float(recovery_seconds))
        self._failures = 0
        self._open_until = 0.0

    def allow_request(self) -> bool:
        return time.monotonic() >= self._open_until

    def record_success(self) -> None:
        self._failures = 0
        self._open_until = 0.0

    def record_failure(self) -> None:
        now = time.monotonic()
        if now < self._open_until:
            return
        self._failures += 1
        if self._failures >= self.failure_threshold:
            self._open_until = now + self.recovery_seconds
            self._failures = 0


_GOV_CIRCUIT = _SimpleCircuitBreaker(
    failure_threshold=int(os.getenv("GOV_API_CIRCUIT_FAILURE_THRESHOLD", "4")),
    recovery_seconds=float(os.getenv("GOV_API_CIRCUIT_RECOVERY_SECONDS", "30")),
)
_NHTSA_CIRCUIT = _SimpleCircuitBreaker(
    failure_threshold=int(os.getenv("NHTSA_API_CIRCUIT_FAILURE_THRESHOLD", "4")),
    recovery_seconds=float(os.getenv("NHTSA_API_CIRCUIT_RECOVERY_SECONDS", "30")),
)


def _get_cache_payload(cache_store: Dict[str, Tuple[float, Dict[str, Any]]], key: str) -> Optional[Dict[str, Any]]:
    cached = cache_store.get(key)
    if not cached:
        return None
    expires_at, payload = cached
    if expires_at <= time.monotonic():
        cache_store.pop(key, None)
        return None
    return copy.deepcopy(payload)


def _store_cache_payload(cache_store: Dict[str, Tuple[float, Dict[str, Any]]], key: str, payload: Dict[str, Any], ttl_seconds: float) -> None:
    if len(cache_store) >= 1024:
        expired = [k for k, (exp, _v) in cache_store.items() if exp <= time.monotonic()]
        for k in expired:
            cache_store.pop(k, None)
        if len(cache_store) >= 1024:
            oldest_key = min(cache_store, key=lambda x: cache_store[x][0])
            cache_store.pop(oldest_key, None)
    cache_store[key] = (time.monotonic() + max(1.0, ttl_seconds), copy.deepcopy(payload))


def _get_call_lock(lock_store: Dict[str, asyncio.Lock], key: str) -> asyncio.Lock:
    lock = lock_store.get(key)
    if lock is None:
        if len(lock_store) >= 2048:
            stale_keys = [k for k, lk in lock_store.items() if not lk.locked()]
            for k in stale_keys[:512]:
                lock_store.pop(k, None)
        lock = asyncio.Lock()
        lock_store[key] = lock
    return lock


@retry_with_backoff(max_retries=2, base_delay=0.4, max_delay=3.0, retry_on=(429, 500, 502, 503, 504))
async def _external_get_json(url: str, *, params: Optional[Dict[str, Any]] = None, timeout: float = 10.0) -> Dict[str, Any]:
    async with _EXTERNAL_API_SEMAPHORE:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            return response.json()


def _search_cache_key(query: str, vehicle_id: Optional[str], vehicle_manufacturer: Optional[str], vehicle_model: Optional[str],
                      vehicle_submodel: Optional[str], vehicle_year: Optional[int],
                      category: Optional[str], per_type: int, sort_by: str,
                      cross_ref_enabled: bool = False) -> Tuple:
    return (
        (query or '').strip().lower(),
    str(vehicle_id or '').strip().lower(),
        (vehicle_manufacturer or '').strip().lower(),
        (vehicle_model or '').strip().lower(),
        (vehicle_submodel or '').strip().lower(),
        str(vehicle_year or ''),
        (category or '').strip().lower(),
        per_type,
        sort_by,
        bool(cross_ref_enabled),
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

    cached_payload = _get_cache_payload(PLATE_LOOKUP_CACHE, clean_plate)
    if cached_payload is not None:
        return cached_payload

    plate_lock = _get_call_lock(PLATE_CALL_LOCKS, clean_plate)
    async with plate_lock:
        cached_payload = _get_cache_payload(PLATE_LOOKUP_CACHE, clean_plate)
        if cached_payload is not None:
            return cached_payload

        if not _GOV_CIRCUIT.allow_request():
            raise HTTPException(status_code=503, detail="mot_api_temporarily_unavailable")

        params = {
            "resource_id": GOV_IL_LICENSE_RESOURCE_ID,
            "filters": json.dumps({"mispar_rechev": clean_plate}),
            "limit": 1,
        }
        try:
            payload = await _external_get_json(GOV_IL_DATASTORE_URL, params=params, timeout=10.0)
            _GOV_CIRCUIT.record_success()
        except httpx.TimeoutException:
            _GOV_CIRCUIT.record_failure()
            raise HTTPException(status_code=504, detail="mot_api_timeout")
        except httpx.HTTPStatusError as exc:
            _GOV_CIRCUIT.record_failure()
            status_code = int(getattr(exc.response, "status_code", 502) or 502)
            if status_code == 404:
                raise HTTPException(status_code=404, detail="license_plate_not_found")
            raise HTTPException(status_code=502, detail=f"mot_api_http_{status_code}")
        except Exception as exc:
            _GOV_CIRCUIT.record_failure()
            raise HTTPException(status_code=502, detail=f"mot_api_error: {exc}")

        records = payload.get("result", {}).get("records", [])
        if not records:
            raise HTTPException(status_code=404, detail="license_plate_not_found")

        record = records[0]
        raw_manufacturer = record.get("tozeret_nm") or record.get("manufacturer") or record.get("tozeret_cd")
        manufacturer = normalize_manufacturer_name(raw_manufacturer, raw_manufacturer) or raw_manufacturer

        raw_model = record.get("kinuy_mishari") or record.get("degem_nm")
        model_base, parsed_submodel = _split_vehicle_model_variant(manufacturer, raw_model)
        trim_submodel = normalize_vehicle_submodel_name(str(record.get("ramat_gimur") or ""))
        resolved_submodel = parsed_submodel or trim_submodel or None

        result = {
            "license_plate": clean_plate,
            "tozeret_cd": _to_int_or_none(record.get("tozeret_cd")),
            "degem_cd": _to_int_or_none(record.get("degem_cd")),
            "shnat_yitzur": _to_int_or_none(record.get("shnat_yitzur")),
            "manufacturer": manufacturer,
            "model": model_base or canonicalize_vehicle_model_for_manufacturer(manufacturer, raw_model) or normalize_vehicle_model_name(raw_model),
            "submodel": resolved_submodel,
            "engine": record.get("nefah_manoa") or record.get("degem_manoa"),
        }

        _store_cache_payload(PLATE_LOOKUP_CACHE, clean_plate, result, PLATE_LOOKUP_CACHE_TTL_S)
        return result


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

        vmfr_clauses = []
        cv_mfr_expr = "LOWER(TRIM(COALESCE(cv_fit->>'make', cv_fit->>'manufacturer', '')))"
        for idx, variant in enumerate(clean_variants):
            key = f"{prefix}_mfr_{idx}"
            # Support values like "Peugeot" vs "Peugeot France" and aliases.
            vmfr_clauses.append(
                f"({cv_mfr_expr} = LOWER(TRIM(:{key}))"
                f" OR {cv_mfr_expr} LIKE CONCAT('%', LOWER(TRIM(:{key})), '%')"
                f" OR LOWER(TRIM(:{key})) LIKE CONCAT('%', {cv_mfr_expr}, '%'))"
            )
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
            " )"
            " OR ("
            "     COALESCE(cv_fit->>'model_year', '') = ''"
            "     AND COALESCE(cv_fit->>'year_from', '') = ''"
            "     AND COALESCE(cv_fit->>'year_to', '') = ''"
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
    return exists_clause


async def _build_fast_vehicle_fitment_json_clause(
    db: AsyncSession,
    params: Dict[str, Any],
    vehicle_manufacturer: Optional[str] = None,
    vehicle_model: Optional[str] = None,
    vehicle_submodel: Optional[str] = None,
    vehicle_year: Optional[int] = None,
    prefix: str = "fitfast",
) -> Optional[str]:
    if not (vehicle_manufacturer and vehicle_model):
        return None

    mfr_variants: List[str] = [vehicle_manufacturer]
    normalized_mfr = normalize_manufacturer_name(vehicle_manufacturer, vehicle_manufacturer)
    if normalized_mfr and normalized_mfr not in mfr_variants:
        mfr_variants.append(normalized_mfr)

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
        if brand_row:
            mfr_variants = list(dict.fromkeys([*mfr_variants, brand_row[0], brand_row[1], *(brand_row[2] or [])]))
    except Exception:
        pass

    canonical_model = canonicalize_vehicle_model_for_manufacturer(vehicle_manufacturer, vehicle_model) or normalize_vehicle_model_name(vehicle_model)
    model_variants: List[str] = [vehicle_model]
    if canonical_model and canonical_model not in model_variants:
        model_variants.append(canonical_model)
    if vehicle_submodel:
        submodel_clean = normalize_vehicle_submodel_name(vehicle_submodel)
        if submodel_clean:
            for base_model in list(model_variants):
                combined = " ".join([base_model, submodel_clean]).strip()
                if combined and combined not in model_variants:
                    model_variants.append(combined)

    clean_mfr = [str(v).strip() for v in mfr_variants if v and str(v).strip()][:6]
    clean_models = [str(v).strip() for v in model_variants if v and str(v).strip()][:6]
    if not clean_mfr or not clean_models:
        return None

    clauses: List[str] = []
    idx = 0
    for mfr in clean_mfr:
        for mdl in clean_models:
            for mkey in ("make", "manufacturer"):
                payload = [{mkey: mfr, "model": mdl}]
                key = f"{prefix}_{idx}"
                params[key] = json.dumps(payload, ensure_ascii=False)
                clauses.append(f"pc.compatible_vehicles @> CAST(:{key} AS jsonb)")
                idx += 1

                if vehicle_year:
                    payload_year_str = [{mkey: mfr, "model": mdl, "model_year": str(int(vehicle_year))}]
                    key = f"{prefix}_{idx}"
                    params[key] = json.dumps(payload_year_str, ensure_ascii=False)
                    clauses.append(f"pc.compatible_vehicles @> CAST(:{key} AS jsonb)")
                    idx += 1

                    payload_year_int = [{mkey: mfr, "model": mdl, "model_year": int(vehicle_year)}]
                    key = f"{prefix}_{idx}"
                    params[key] = json.dumps(payload_year_int, ensure_ascii=False)
                    clauses.append(f"pc.compatible_vehicles @> CAST(:{key} AS jsonb)")
                    idx += 1

            if idx >= 36:
                break
        if idx >= 36:
            break

    if not clauses:
        return None

    return (
        "(pc.compatible_vehicles IS NOT NULL"
        " AND jsonb_typeof(pc.compatible_vehicles) = 'array'"
        f" AND ({' OR '.join(clauses)}))"
    )


async def _resolve_vehicle_search_context(
    db: AsyncSession,
    vehicle_id: Optional[str],
) -> Optional[Dict[str, Any]]:
    if not vehicle_id:
        return None

    vehicle = (await db.execute(select(Vehicle).where(Vehicle.id == vehicle_id))).scalar_one_or_none()
    if not vehicle:
        return None

    gov = vehicle.gov_api_data if isinstance(vehicle.gov_api_data, dict) else {}
    manufacturer = normalize_manufacturer_name(vehicle.manufacturer, vehicle.manufacturer) or vehicle.manufacturer
    model = canonicalize_vehicle_model_for_manufacturer(manufacturer, vehicle.model) or normalize_vehicle_model_name(vehicle.model)
    submodel = normalize_vehicle_submodel_name(str(gov.get("sub_model") or "")) or ""
    year = vehicle.year if isinstance(vehicle.year, int) and vehicle.year > 0 else None

    return {
        "manufacturer": manufacturer,
        "model": model,
        "submodel": submodel or None,
        "year": year,
    }


async def _build_strict_vehicle_match_clause(
    db: AsyncSession,
    params: Dict[str, Any],
    vehicle_manufacturer: Optional[str] = None,
    vehicle_model: Optional[str] = None,
    vehicle_submodel: Optional[str] = None,
    vehicle_year: Optional[int] = None,
    prefix: str = "strictfit",
    include_json: bool = True,
) -> Optional[str]:
    if not (vehicle_manufacturer and vehicle_model and vehicle_year):
        return None

    json_clause = None
    if include_json:
        json_clause = await _build_vehicle_fitment_clause(
            db,
            params,
            vehicle_manufacturer=vehicle_manufacturer,
            vehicle_model=vehicle_model,
            vehicle_submodel=vehicle_submodel,
            vehicle_year=vehicle_year,
            prefix=f"{prefix}_json",
        )

    variants: List[str] = [vehicle_manufacturer]
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
        if brand_row:
            variants = list(dict.fromkeys([*variants, brand_row[0], brand_row[1], *(brand_row[2] or [])]))
    except Exception:
        pass

    clean_variants = [str(v).strip() for v in variants if v and str(v).strip()]
    normalized_model = canonicalize_vehicle_model_for_manufacturer(vehicle_manufacturer, vehicle_model) or normalize_vehicle_model_name(vehicle_model)
    model_variants = [normalized_model]
    if vehicle_submodel:
        combined = " ".join([normalized_model, vehicle_submodel]).strip()
        if combined and combined not in model_variants:
            model_variants.append(combined)

    pvf_mfr_clauses = []
    for idx, variant in enumerate(clean_variants):
        key = f"{prefix}_mfr_{idx}"
        params[key] = variant
        pvf_mfr_clauses.append(
            f"(LOWER(TRIM(pvf.manufacturer)) = LOWER(TRIM(:{key}))"
            f" OR LOWER(TRIM(pvf.manufacturer)) LIKE CONCAT('%', LOWER(TRIM(:{key})), '%')"
            f" OR LOWER(TRIM(:{key})) LIKE CONCAT('%', LOWER(TRIM(pvf.manufacturer)), '%'))"
        )

    pvf_model_clauses = []
    for idx, variant in enumerate(model_variants):
        key = f"{prefix}_model_{idx}"
        params[key] = variant
        pvf_model_clauses.append(
            f"(LOWER(TRIM(pvf.model)) = LOWER(TRIM(:{key}))"
            f" OR LOWER(TRIM(pvf.model)) LIKE CONCAT(LOWER(TRIM(:{key})), ' %')"
            f" OR LOWER(TRIM(:{key})) LIKE CONCAT(LOWER(TRIM(pvf.model)), ' %'))"
        )

    params[f"{prefix}_year"] = int(vehicle_year)
    pvf_clause = (
        "EXISTS ("
        " SELECT 1 FROM part_vehicle_fitment pvf"
        " WHERE pvf.part_id = pc.id"
        f"   AND ({' OR '.join(pvf_mfr_clauses)})"
        f"   AND ({' OR '.join(pvf_model_clauses)})"
        f"   AND pvf.year_from <= :{prefix}_year"
        f"   AND COALESCE(pvf.year_to, pvf.year_from) >= :{prefix}_year"
        ")"
    )

    if json_clause:
        return f"(({json_clause}) OR ({pvf_clause}))"
    return pvf_clause


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
    enable_cross_refs: Optional[bool] = Query(None),
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

    cross_refs_default = os.getenv("ENABLE_CROSS_REFERENCE_EXPANSION", "1").strip().lower() in ("1", "true", "yes", "on")
    cross_refs_enabled = cross_refs_default if enable_cross_refs is None else bool(enable_cross_refs)

    # ── In-memory response cache (survives repeated searches with same params) ─
    _s_cache_key = _search_cache_key(query, vehicle_id, vehicle_manufacturer, vehicle_model,
                                     vehicle_submodel, vehicle_year, category,
                                     per_type or 4, sort_by, cross_refs_enabled)
    _cached_search = _get_cached_search_response(_s_cache_key)
    if _cached_search is not None:
        return _cached_search

    # Keep the request path local-first by default. Remote HF normalization is
    # opt-in for Hebrew/mixed-script queries because provider retries create large
    # first-hit tail latency when rate-limited.
    if query and SEARCH_ENABLE_HF_QUERY_NORMALIZATION and any('֐' <= ch <= '׿' for ch in query):
        try:
            from hf_client import hf_normalize_query
            query = await asyncio.wait_for(
                hf_normalize_query(query),
                timeout=SEARCH_HF_QUERY_NORMALIZATION_TIMEOUT_S,
            )
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
        if cross_refs_enabled:
            meili_ids = None
        else:
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
    if meili_ids and query and SEARCH_ENABLE_VECTOR_RERANK and len(query.strip()) >= SEARCH_VECTOR_RERANK_MIN_QUERY_LEN:
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
    conditions_base: List[str] = ["pc.is_active = TRUE"]
    query_condition_sql = ""
    params: Dict[str, Any] = {}

    vehicle_context = await _resolve_vehicle_search_context(db, vehicle_id)
    strict_vehicle_context = bool(vehicle_context)
    strict_vehicle_clause_added: Optional[str] = None
    strict_vehicle_json_fast_clause: Optional[str] = None
    strict_vehicle_json_fallback_clause: Optional[str] = None
    manual_strict_clause_added: Optional[str] = None
    manual_json_fast_clause: Optional[str] = None
    manual_json_fallback_clause: Optional[str] = None
    if vehicle_context:
        vehicle_manufacturer = vehicle_context["manufacturer"]
        vehicle_model = vehicle_context["model"]
        vehicle_submodel = vehicle_context["submodel"]
        vehicle_year = vehicle_context["year"]

    # Text filter: if Meilisearch is live use id-array join (no ILIKE needed);
    # if it's unavailable fall back to the original ILIKE clause.
    if query and meili_ids is None:
        query_condition_sql = (
            "(pc.name ILIKE :q OR pc.name_he ILIKE :q OR pc.sku ILIKE :q OR pc.manufacturer ILIKE :q "
            "OR pc.category ILIKE :q OR pc.oem_number ILIKE :q "
            "OR EXISTS (SELECT 1 FROM part_cross_reference pcrq WHERE pcrq.part_id = pc.id AND COALESCE(pcrq.ref_number, '') ILIKE :q))"
        )
        params["q"]       = f"%{query}%"
        params["q_exact"] = query
        params["q_start"] = f"{query}%"

    if category:
        family_clause = build_part_type_sql_clause(category, params)
        if family_clause:
            conditions_base.append(family_clause)
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
            conditions_base.append(f"(pc.category ILIKE :cat OR EXISTS (SELECT 1 FROM supplier_parts sp2 WHERE sp2.part_id = pc.id AND sp2.part_type ILIKE :cat){keyword_sql})")
            params["cat"] = f"%{category}%"

    selected_part_family = resolve_part_type_family(category) if category else None

    if strict_vehicle_context:
        strict_vehicle_clause = await _build_strict_vehicle_match_clause(
            db,
            params,
            vehicle_manufacturer=vehicle_manufacturer,
            vehicle_model=vehicle_model,
            vehicle_submodel=vehicle_submodel,
            vehicle_year=vehicle_year,
            include_json=False,
        )
        if strict_vehicle_clause:
            conditions_base.append(strict_vehicle_clause)
            strict_vehicle_clause_added = strict_vehicle_clause
            strict_vehicle_json_fast_clause = await _build_fast_vehicle_fitment_json_clause(
                db,
                params,
                vehicle_manufacturer=vehicle_manufacturer,
                vehicle_model=vehicle_model,
                vehicle_submodel=vehicle_submodel,
                vehicle_year=vehicle_year,
                prefix="strictfit_json_fast",
            )
            strict_vehicle_json_fallback_clause = await _build_vehicle_fitment_clause(
                db,
                params,
                vehicle_manufacturer=vehicle_manufacturer,
                vehicle_model=vehicle_model,
                vehicle_submodel=vehicle_submodel,
                vehicle_year=vehicle_year,
                prefix="strictfit_json_fallback",
            )
        else:
            conditions_base.append("1 = 0")
    else:
        # Manual selection path should also be strict when full vehicle context
        # is provided (manufacturer + model + year), even without vehicle_id.
        if vehicle_manufacturer and vehicle_model and vehicle_year:
            strict_manual_clause = await _build_strict_vehicle_match_clause(
                db,
                params,
                vehicle_manufacturer=vehicle_manufacturer,
                vehicle_model=vehicle_model,
                vehicle_submodel=vehicle_submodel,
                vehicle_year=vehicle_year,
                prefix="manualfit",
                include_json=False,
            )
            if strict_manual_clause:
                conditions_base.append(strict_manual_clause)
                manual_strict_clause_added = strict_manual_clause
                manual_json_fast_clause = await _build_fast_vehicle_fitment_json_clause(
                    db,
                    params,
                    vehicle_manufacturer=vehicle_manufacturer,
                    vehicle_model=vehicle_model,
                    vehicle_submodel=vehicle_submodel,
                    vehicle_year=vehicle_year,
                    prefix="manualfit_json_fast",
                )
                manual_json_fallback_clause = await _build_vehicle_fitment_clause(
                    db,
                    params,
                    vehicle_manufacturer=vehicle_manufacturer,
                    vehicle_model=vehicle_model,
                    vehicle_submodel=vehicle_submodel,
                    vehicle_year=vehicle_year,
                    prefix="manualfit_json_fallback",
                )
            else:
                conditions_base.append("1 = 0")
        else:
            # Partial manual filters still use the JSON-fitment lane.
            vehicle_fitment_clause = await _build_vehicle_fitment_clause(
                db,
                params,
                vehicle_manufacturer=vehicle_manufacturer,
                vehicle_model=vehicle_model,
                vehicle_submodel=vehicle_submodel,
                vehicle_year=vehicle_year,
            )
            if vehicle_fitment_clause:
                conditions_base.append(vehicle_fitment_clause)

    conditions = [*conditions_base]
    if query_condition_sql:
        conditions.append(query_condition_sql)

    where_sql_base = " AND ".join(conditions_base)
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
    async def _fetch_type(
        part_type_values: list,
        include_general: bool = False,
        where_sql_override: Optional[str] = None,
        where_sql_base_override: Optional[str] = None,
        db_session: Optional[AsyncSession] = None,
    ) -> List[Dict[str, Any]]:
        query_db = db_session or db
        usd_to_ils_rate = await get_usd_to_ils_rate(query_db)
        where_sql_effective = where_sql_override or where_sql
        where_sql_base_effective = where_sql_base_override or where_sql_base
        candidate_limit = max(int(per_type or 4) * 6, 24)
        type_params = {**params, "pt": part_type_values, "lim": per_type, "candidate_lim": candidate_limit}
        _unsafe_sql_tokens = (";", "--", "/*", "*/")
        if any(tok in where_sql_effective for tok in _unsafe_sql_tokens):
            raise HTTPException(status_code=400, detail="unsafe_query_rejected")

        if meili_ids:
            # ── Meilisearch path: rank-preserving unnest JOIN ─────────────────
            # UUIDs come from our own index — hex+dash only, no SQL injection risk.
            # Pass as a Python list so asyncpg maps it to a PostgreSQL text[] array.
            pt_condition = "pc.part_type = ANY(:pt)"
            if include_general:
                pt_condition = "(pc.part_type = ANY(:pt) OR pc.part_type IS NULL OR pc.part_type = '' OR pc.part_type ILIKE 'unknown' OR pc.part_type ILIKE 'general' OR pc.part_type ILIKE 'כללי')"

            part_rows = (await query_db.execute(
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
                    WHERE {where_sql_effective} AND {pt_condition}
                    ORDER BY ranked.pos ASC,
                             pc.base_price ASC NULLS LAST,
                             pc.updated_at DESC
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

            # Empty-text vehicle/category queries are latency-sensitive and can
            # match a very large set. Avoid global price sorting in that mode,
            # because it forces scanning/sorting the full match set before LIMIT.
            if query:
                order_by_sql = f"{relevance_sql}\n                    pc.base_price ASC NULLS LAST"
            else:
                order_by_sql = "pc.id ASC"

            part_rows = (await query_db.execute(
                text(f"""
                    SELECT
                        pc.id, pc.sku, pc.name, pc.name_he, pc.manufacturer,
                        pc.category, pc.part_type, pc.base_price,
                        pc.min_price_ils, pc.max_price_ils, pc.description,
                        pc.oem_number, pc.barcode, pc.weight_kg,
                        pc.is_safety_critical, pc.part_condition,
                        pc.created_at, pc.updated_at{score_col}
                    FROM parts_catalog pc
                    WHERE {where_sql_effective} AND {pt_condition}
                    ORDER BY {order_by_sql}
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

        if cross_refs_enabled and matching_rows:
            xref_seed_ids = [str(row[0]) for row in matching_rows[: max(int(per_type or 4) * 2, 8)]]
            xref_limit = max(int(per_type or 4) * 2, 6)
            xref_pt_condition = "pc.part_type = ANY(:pt)"
            if include_general:
                xref_pt_condition = "(pc.part_type = ANY(:pt) OR pc.part_type IS NULL OR pc.part_type = '' OR pc.part_type ILIKE 'unknown' OR pc.part_type ILIKE 'general' OR pc.part_type ILIKE 'כללי')"

            if xref_seed_ids and where_sql_base_effective:
                xref_rows = (await query_db.execute(
                    text(f"""
                        SELECT DISTINCT ON (pc.id)
                            pc.id, pc.sku, pc.name, pc.name_he, pc.manufacturer,
                            pc.category, pc.part_type, pc.base_price,
                            pc.min_price_ils, pc.max_price_ils, pc.description,
                            pc.oem_number, pc.barcode, pc.weight_kg,
                            pc.is_safety_critical, pc.part_condition,
                            pc.created_at, pc.updated_at
                        FROM part_cross_reference pcr
                        JOIN parts_catalog src ON src.id = pcr.part_id
                        JOIN parts_catalog pc ON pc.id <> src.id
                        WHERE pcr.part_id = ANY(CAST(:xref_seed_ids AS uuid[]))
                          AND COALESCE(pcr.is_superseded, FALSE) = FALSE
                          AND COALESCE(pcr.ref_number, '') <> ''
                          AND (
                               regexp_replace(UPPER(COALESCE(pc.oem_number, '')), '[^A-Z0-9]', '', 'g') = regexp_replace(UPPER(COALESCE(pcr.ref_number, '')), '[^A-Z0-9]', '', 'g')
                            OR regexp_replace(UPPER(COALESCE(pc.sku, '')), '[^A-Z0-9]', '', 'g') = regexp_replace(UPPER(COALESCE(pcr.ref_number, '')), '[^A-Z0-9]', '', 'g')
                          )
                                                    AND {where_sql_base_effective}
                          AND {xref_pt_condition}
                        ORDER BY pc.id, pc.updated_at DESC
                        LIMIT :xref_lim
                    """),
                    {**type_params, "xref_seed_ids": xref_seed_ids, "xref_lim": xref_limit},
                )).fetchall()

                if xref_rows:
                    existing_ids = {str(row[0]) for row in matching_rows}
                    for xrow in xref_rows:
                        xrow_id = str(xrow[0])
                        if xrow_id in existing_ids:
                            continue
                        if selected_part_family:
                            xrow_family = classify_part_type_family(
                                xrow[5],
                                xrow[6],
                                xrow[2],
                                xrow[3],
                                xrow[10],
                            )
                            if not (xrow_family and xrow_family.id == selected_part_family.id):
                                continue
                        matching_rows.append(xrow)
                        existing_ids.add(xrow_id)

        # Backwards compat: also keep the first-match logic for TypeSection display
        part_row = matching_rows[0] if matching_rows else None

        if not part_row:
            return []

        part_id_str = str(part_row[0])

        # All available supplier offers for this part, sorted cheapest first
        sup_rows = (await query_db.execute(
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
                ORDER BY COALESCE(sp.price_ils, sp.price_usd * :usd_to_ils_rate) ASC
                LIMIT :lim
            """),
            {"part_id": part_id_str, "lim": per_type, "usd_to_ils_rate": usd_to_ils_rate},
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
            price_ils = float(sp[5]) if sp[5] else (float(sp[4]) * usd_to_ils_rate if sp[4] else None)
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
                batch_rows = (await query_db.execute(
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
                                                                 COALESCE(sp.price_ils, sp.price_usd * :usd_to_ils_rate) ASC
                    """),
                                        {"extra_ids": extra_ids, "usd_to_ils_rate": usd_to_ils_rate},
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
                    b_price_ils = float(bsp[6]) if bsp[6] else (float(bsp[5]) * usd_to_ils_rate if bsp[5] else None)
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

    async def _fetch_three_buckets(
        where_sql_override: Optional[str] = None,
        where_sql_base_override: Optional[str] = None,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
        async with async_session_factory() as db_original, async_session_factory() as db_oem, async_session_factory() as db_after:
            original_task = _fetch_type(
                ["Original"],
                where_sql_override=where_sql_override,
                where_sql_base_override=where_sql_base_override,
                db_session=db_original,
            )
            oem_task = _fetch_type(
                ["OEM"],
                where_sql_override=where_sql_override,
                where_sql_base_override=where_sql_base_override,
                db_session=db_oem,
            )
            aftermarket_task = _fetch_type(
                ["Aftermarket", "Refurbished"],
                include_general=True,
                where_sql_override=where_sql_override,
                where_sql_base_override=where_sql_base_override,
                db_session=db_after,
            )
            original_bucket, oem_bucket, aftermarket_bucket = await asyncio.gather(
                original_task, oem_task, aftermarket_task
            )
            return original_bucket, oem_bucket, aftermarket_bucket

    original_res, oem_res, aftermarket_res = await _fetch_three_buckets()

    # Fallback for strict vehicle searches (vehicle_id/manual full-context):
    # if structured (PVF) path returns nothing, retry JSON-compatible_vehicles.
    should_try_json_fallback = (
        not original_res and not oem_res and not aftermarket_res and (
            (strict_vehicle_context and strict_vehicle_clause_added)
            or (not strict_vehicle_context and manual_strict_clause_added)
        )
    )
    if should_try_json_fallback:
        strict_clause_added = strict_vehicle_clause_added if strict_vehicle_context else manual_strict_clause_added
        json_fast_clause = strict_vehicle_json_fast_clause if strict_vehicle_context else manual_json_fast_clause
        json_fallback_clause = strict_vehicle_json_fallback_clause if strict_vehicle_context else manual_json_fallback_clause

        base_without_strict: List[str] = []
        removed_strict = False
        for clause in conditions_base:
            if not removed_strict and clause == strict_clause_added:
                removed_strict = True
                continue
            base_without_strict.append(clause)

        if json_fast_clause:
            json_fast_base_conditions = [*base_without_strict, json_fast_clause]
            json_fast_where_base = " AND ".join(json_fast_base_conditions)
            json_fast_conditions = [*json_fast_base_conditions]
            if query_condition_sql:
                json_fast_conditions.append(query_condition_sql)
            json_fast_where = " AND ".join(json_fast_conditions)
            original_res, oem_res, aftermarket_res = await _fetch_three_buckets(
                where_sql_override=json_fast_where,
                where_sql_base_override=json_fast_where_base,
            )

        if json_fallback_clause and not original_res and not oem_res and not aftermarket_res:
            json_base_conditions = [*base_without_strict, json_fallback_clause]
            json_where_base = " AND ".join(json_base_conditions)
            json_conditions = [*json_base_conditions]
            if query_condition_sql:
                json_conditions.append(query_condition_sql)
            json_where = " AND ".join(json_conditions)
            original_res, oem_res, aftermarket_res = await _fetch_three_buckets(
                where_sql_override=json_where,
                where_sql_base_override=json_where_base,
            )

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
    used_strict_lane = False

    if vehicle_manufacturer and vehicle_model and vehicle_year:
        strict_category_clause = await _build_strict_vehicle_match_clause(
            db,
            params,
            vehicle_manufacturer=vehicle_manufacturer,
            vehicle_model=vehicle_model,
            vehicle_submodel=vehicle_submodel,
            vehicle_year=vehicle_year,
            prefix="catstrict",
            include_json=False,
        )
        if strict_category_clause:
            conditions.append(strict_category_clause)
            used_strict_lane = True
        else:
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
    else:
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

    if used_strict_lane and not rows:
        fallback_params: Dict[str, Any] = {}
        fallback_conditions: List[str] = ["pc.is_active = TRUE"]
        fallback_fitment_clause = await _build_vehicle_fitment_clause(
            db,
            fallback_params,
            vehicle_manufacturer=vehicle_manufacturer,
            vehicle_model=vehicle_model,
            vehicle_submodel=vehicle_submodel,
            vehicle_year=vehicle_year,
            prefix="catfit_fallback",
        )
        if fallback_fitment_clause:
            fallback_conditions.append(fallback_fitment_clause)
            fallback_where_sql = " AND ".join(fallback_conditions)
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
                        WHERE {fallback_where_sql}
                        """
                    ),
                    fallback_params,
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

    gov = vehicle.gov_api_data if isinstance(vehicle.gov_api_data, dict) else {}
    submodel = normalize_vehicle_submodel_name(str(gov.get("sub_model") or gov.get("trim") or "")) or getattr(vehicle, "submodel", None)

    grouped = await search_parts(
        query="",
        vehicle_id=vehicle_id,
        category=category,
        per_type=4,
        sort_by="price_ils",
        vehicle_manufacturer=vehicle.manufacturer,
        vehicle_model=vehicle.model,
        vehicle_submodel=submodel,
        vehicle_year=vehicle.year,
        db=db,
        request=request,
        redis=redis,
    )

    return {
        "vehicle": {
            "manufacturer": vehicle.manufacturer,
            "model": vehicle.model,
            "submodel": submodel,
            "year": vehicle.year,
        },
        "parts": grouped.get("all_parts", []),
        **grouped,
    }


# ==============================================================================
# GET /api/parts/by-license-plate/{license_plate}
# ==============================================================================

@router.get("/api/parts/by-license-plate/{license_plate}")
async def get_parts_by_license_plate(
    license_plate: str,
    part_type: Optional[str] = None,
    category: Optional[str] = None,
    query: Optional[str] = "",
    per_type: int = 4,
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

    effective_category = (category or part_type or "").strip() or None

    grouped = await search_parts(
        query=(query or "").strip(),
        vehicle_id=None,
        category=effective_category,
        per_type=per_type,
        sort_by="price_ils",
        vehicle_manufacturer=vehicle.get("manufacturer"),
        vehicle_model=vehicle.get("model"),
        vehicle_submodel=vehicle.get("submodel"),
        vehicle_year=vehicle.get("shnat_yitzur"),
        db=db,
        request=request,
        redis=None,
    )

    return {
        "vehicle": {
            "manufacturer": vehicle.get("manufacturer"),
            "model": vehicle.get("model"),
            "submodel": vehicle.get("submodel"),
            "year": vehicle.get("shnat_yitzur"),
            "engine": vehicle.get("engine"),
        },
        "query": (query or "").strip(),
        "part_type": effective_category,
        "parts": grouped.get("all_parts", []),
        **grouped,
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

    # Preferred source: manufacturers that are actually searchable via
    # compatible_vehicles JSON in /parts/search.
    try:
        fitment_rows = (await db.execute(text("""
            SELECT
                TRIM(COALESCE(elem->>'make', elem->>'manufacturer', '')) AS manufacturer,
                COUNT(DISTINCT pc.id) AS cnt
            FROM parts_catalog pc
            CROSS JOIN LATERAL jsonb_array_elements(pc.compatible_vehicles) AS elem
            WHERE pc.is_active = TRUE
              AND pc.compatible_vehicles IS NOT NULL
              AND jsonb_typeof(pc.compatible_vehicles) = 'array'
              AND COALESCE(TRIM(COALESCE(elem->>'make', elem->>'manufacturer', '')), '') <> ''
            GROUP BY 1
            ORDER BY cnt DESC, manufacturer ASC
        """))).fetchall()
    except Exception:
        fitment_rows = []

    if fitment_rows:
        try:
            brand_rows = (await db.execute(text("""
                SELECT name, name_he, aliases, logo_url
                FROM car_brands
                WHERE is_active = TRUE
            """))).fetchall()
        except Exception:
            brand_rows = []

        canonical_fitment_counts: Dict[str, int] = {}
        for raw_name, raw_count in fitment_rows:
            base_name = str(raw_name or "").strip()
            if not base_name:
                continue
            canonical_name = normalize_manufacturer_name(base_name, base_name) or base_name
            display_name = canonical_name.title() if canonical_name.isascii() and canonical_name.islower() else canonical_name
            canonical_fitment_counts[display_name] = canonical_fitment_counts.get(display_name, 0) + int(raw_count or 0)

        manufacturers: List[str] = []
        counts: Dict[str, int] = {}
        logos: Dict[str, str] = {}
        seen_norm = set()

        for display_name, raw_count in sorted(canonical_fitment_counts.items(), key=lambda item: (-item[1], item[0])):
            norm_name = _norm(display_name)
            if not norm_name or norm_name in seen_norm:
                continue

            seen_norm.add(norm_name)
            manufacturers.append(display_name)
            counts[display_name] = int(raw_count or 0)

            matched_logo = None
            for b_name, b_name_he, b_aliases, b_logo in brand_rows:
                variants = [b_name, b_name_he, *((b_aliases or []))]
                if any(
                    _norm(v) and (
                        _norm(v) == norm_name
                        or _norm(v) in norm_name
                        or norm_name in _norm(v)
                    )
                    for v in variants
                    if v
                ):
                    matched_logo = b_logo
                    break

            if matched_logo:
                logos[display_name] = matched_logo

        if manufacturers:
            return {
                "manufacturers": manufacturers,
                "counts": counts,
                "logos": logos,
                "total": len(manufacturers),
            }

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
        canonical_raw = normalize_manufacturer_name(raw, raw) or raw
        n = _norm(canonical_raw)
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
        manufacturers.append(canonical_raw)

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
        normalized_variants = sorted(
            {
                str(v).strip().casefold()
                for v in variants
                if v is not None and str(v).strip()
            }
        )
        if not normalized_variants:
            normalized_variants = [str(manufacturer).strip().casefold()]
        params_mfr: Dict[str, Any] = {"mfr_variants": normalized_variants}

        # 0) Preferred curated source: XLS hierarchy table.
        try:
            x_rows = (await db.execute(text("""
                SELECT DISTINCT model
                FROM vehicle_hierarchy_xls
                WHERE LOWER(TRIM(manufacturer)) = ANY(CAST(:mfr_variants AS text[]))
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
        v_rows = (await db.execute(text("""
            SELECT DISTINCT model
            FROM vehicles
            WHERE model IS NOT NULL
              AND model <> ''
              AND LOWER(TRIM(manufacturer)) = ANY(CAST(:mfr_variants AS text[]))
            ORDER BY model
        """), params_mfr)).fetchall()
        models = _dedupe_clean(v_rows, manufacturer)
        if models:
            response = {"models": models, "total": len(models)}
            _store_cached_hierarchy_response(MODELS_RESPONSE_CACHE, models_cache_key, response)
            return response

        # 2) Fallback: compatible_vehicles extracted from parts_catalog
        p_rows = (await db.execute(text("""
            SELECT DISTINCT COALESCE(elem->>'model', elem->>'model_year') AS model
            FROM parts_catalog,
                 jsonb_array_elements(compatible_vehicles) AS elem
            WHERE compatible_vehicles IS NOT NULL
              AND jsonb_typeof(compatible_vehicles) = 'array'
              AND LOWER(TRIM(COALESCE(elem->>'make', elem->>'manufacturer', ''))) = ANY(CAST(:mfr_variants AS text[]))
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

    # Fast fallback: vehicles hierarchy usually has enough structured variants.
    try:
        v_rows = (await db.execute(text("""
            SELECT DISTINCT model, gov_api_data
            FROM vehicles
            WHERE LOWER(TRIM(manufacturer)) = LOWER(TRIM(:mfr))
              AND (
                    LOWER(TRIM(model)) = LOWER(TRIM(:model))
                    OR LOWER(TRIM(model)) LIKE LOWER(TRIM(:model)) || ' %'
              )
              AND model IS NOT NULL
              AND model <> ''
            ORDER BY model
        """), {"mfr": canon, "model": base_model})).fetchall()
        submodel_values.update(_derived_submodel(row[0], row[1]) for row in v_rows)
    except Exception:
        pass

    if submodel_values:
        submodels = sorted(s for s in submodel_values if s)
        response = {"submodels": submodels, "total": len(submodels)}
        _store_cached_hierarchy_response(SUBMODELS_RESPONSE_CACHE, submodels_cache_key, response)
        return response

    # Last fallback: scan compatible_vehicles, but prefilter with GIN @>.
    try:
        _safe_canon = canon.replace("'", "''")
        _gin_mfr = f'[{{"manufacturer": "{_safe_canon}"}}]'
        _gin_make = f'[{{"make": "{_safe_canon}"}}]'
        p_rows = (await db.execute(text("""
            SELECT DISTINCT elem->>'sub_model' AS sub_model
            FROM parts_catalog,
                 jsonb_array_elements(compatible_vehicles) AS elem
            WHERE is_active = TRUE
              AND compatible_vehicles IS NOT NULL
              AND jsonb_typeof(compatible_vehicles) = 'array'
              AND (
                    compatible_vehicles @> CAST(:gin_mfr AS jsonb)
                    OR compatible_vehicles @> CAST(:gin_make AS jsonb)
              )
              AND LOWER(TRIM(COALESCE(elem->>'make', elem->>'manufacturer', ''))) = LOWER(TRIM(:mfr))
              AND (
                    LOWER(TRIM(COALESCE(elem->>'model', ''))) = LOWER(TRIM(:model))
                    OR LOWER(TRIM(COALESCE(elem->>'model_year', ''))) LIKE LOWER(TRIM(:model)) || ' %'
              )
              AND COALESCE(TRIM(elem->>'sub_model'), '') <> ''
            ORDER BY sub_model
        """), {"mfr": canon, "model": base_model, "gin_mfr": _gin_mfr, "gin_make": _gin_make})).fetchall()
        submodel_values.update(
            normalize_vehicle_submodel_name(r[0])
            for r in p_rows
            if r and r[0]
        )
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
            AND (
                LOWER(TRIM(model)) = LOWER(TRIM(:model))
                OR LOWER(TRIM(model)) LIKE LOWER(TRIM(:model)) || ' %'
            )
          AND model IS NOT NULL
          AND model <> ''
        """), {"mfr": canon, "model": base_model})).fetchall()
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


async def _decode_vin_with_resilience(vin_clean: str) -> Dict[str, Any]:
    cached_payload = _get_cache_payload(VIN_LOOKUP_CACHE, vin_clean)
    if cached_payload is not None:
        return cached_payload

    vin_lock = _get_call_lock(VIN_CALL_LOCKS, vin_clean)
    async with vin_lock:
        cached_payload = _get_cache_payload(VIN_LOOKUP_CACHE, vin_clean)
        if cached_payload is not None:
            return cached_payload

        if not _NHTSA_CIRCUIT.allow_request():
            raise HTTPException(status_code=503, detail="vin_api_temporarily_unavailable")

        nhtsa_url = f"https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVinValuesExtended/{vin_clean}?format=json"
        try:
            data = await _external_get_json(nhtsa_url, timeout=10.0)
            _NHTSA_CIRCUIT.record_success()
        except httpx.TimeoutException:
            _NHTSA_CIRCUIT.record_failure()
            raise HTTPException(status_code=504, detail="vin_api_timeout")
        except httpx.HTTPStatusError as exc:
            _NHTSA_CIRCUIT.record_failure()
            status_code = int(getattr(exc.response, "status_code", 502) or 502)
            raise HTTPException(status_code=502, detail=f"vin_api_http_{status_code}")
        except Exception as exc:
            _NHTSA_CIRCUIT.record_failure()
            raise HTTPException(status_code=502, detail=f"vin_api_error: {exc}")

        results = data.get("Results", [{}])[0]

        def nhtsa(key: str) -> Optional[str]:
            return (results.get(key) or "").strip() or None

        manufacturer = nhtsa("Make") or nhtsa("Manufacturer") or ""
        raw_model = nhtsa("Model") or ""
        raw_submodel = " ".join(
            x for x in [nhtsa("Trim"), nhtsa("Series"), nhtsa("Series2")]
            if x
        ).strip()
        canonical_manufacturer = normalize_manufacturer_name(manufacturer, manufacturer) or manufacturer
        model_base, parsed_submodel = _split_vehicle_model_variant(canonical_manufacturer, raw_model)
        normalized_submodel = normalize_vehicle_submodel_name(raw_submodel) or parsed_submodel
        model = model_base or canonicalize_vehicle_model_for_manufacturer(canonical_manufacturer, raw_model) or normalize_vehicle_model_name(raw_model)

        year_str = nhtsa("ModelYear") or ""
        engine_cc = nhtsa("DisplacementCC")
        fuel_type = nhtsa("FuelTypePrimary")
        transmission = nhtsa("TransmissionStyle")
        drive_type = nhtsa("DriveType")
        body_class = nhtsa("BodyClass")
        doors = nhtsa("Doors")
        plant_country = nhtsa("PlantCountry")
        year_int = int(year_str) if year_str and year_str.isdigit() else 0

        vehicle_info = {
            "vin": vin_clean,
            "manufacturer": manufacturer,
            "model": model,
            "submodel": normalized_submodel,
            "year": year_int,
            "engine_cc": engine_cc,
            "fuel_type": fuel_type,
            "transmission": transmission,
            "drive_type": drive_type,
            "body_class": body_class,
            "doors": doors,
            "country_of_origin": plant_country,
        }

        _store_cache_payload(VIN_LOOKUP_CACHE, vin_clean, vehicle_info, VIN_LOOKUP_CACHE_TTL_S)
        return vehicle_info


# ==============================================================================
# GET /api/v1/parts/search-by-vin
# ==============================================================================

@router.get("/api/v1/parts/search-by-vin")
async def search_parts_by_vin(
    vin: str,
    query: Optional[str] = Query(None),
    part_query: Optional[str] = "",
    part_type: Optional[str] = None,
    category: Optional[str] = None,
    limit: int = 50,
    per_type: Optional[int] = Query(None),
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

    vehicle_info = await _decode_vin_with_resilience(vin_clean)
    engine_type = (
        f"{vehicle_info.get('fuel_type') or 'Unknown'} {vehicle_info.get('engine_cc')}cc"
        if vehicle_info.get("engine_cc")
        else vehicle_info.get("fuel_type")
    )

    if not vehicle_info.get("manufacturer"):
        raise HTTPException(status_code=404, detail=f"לא נמצא מידע עבור VIN: {vin_clean}")

    if not vehicle_info.get("model") or not vehicle_info.get("year"):
        raise HTTPException(status_code=422, detail="VIN decode missing required vehicle context")

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

    # ── Search parts (grouped original/oem/aftermarket) ─────────────────────
    search_q = (query if query is not None else part_query or "").strip()
    effective_category = (category or part_type or "").strip() or None
    effective_per_type = per_type if per_type is not None else limit
    grouped = await search_parts(
        query=search_q,
        vehicle_id=cached_vehicle_id,
        category=effective_category,
        per_type=effective_per_type,
        sort_by="price_ils",
        vehicle_manufacturer=vehicle_info["manufacturer"],
        vehicle_model=vehicle_info.get("model"),
        vehicle_submodel=vehicle_info.get("submodel"),
        vehicle_year=vehicle_info.get("year"),
        db=db,
        request=request,
        redis=None,
    )

    return {
        "vehicle": vehicle_info,
        "query": search_q,
        "part_type": effective_category,
        "parts": grouped.get("all_parts", []),
        "total": len(grouped.get("all_parts", [])),
        "offset": offset,
        "limit": effective_per_type,
        **grouped,
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
    usd_to_ils_rate = await get_usd_to_ils_rate(db)
    comparisons = []
    for sp, supplier in rows:
        cost_ils = float(sp.price_ils or 0)
        ship_ils = float(sp.shipping_cost_ils or 0)
        delivery_fee = _get_ship(supplier.name or "", supplier.country or "")
        if cost_ils > 0:
            pricing = agent.calculate_customer_price_from_ils(
                cost_ils,
                ship_ils,
                customer_shipping=delivery_fee,
                supplier_name=supplier.name,
                supplier_country=supplier.country,
                local_vat_only=True,
            )
        else:
            usd_total = float(sp.price_usd or 0) + float(sp.shipping_cost_usd or 0)
            pricing = agent.calculate_customer_price_from_ils(
                usd_total * usd_to_ils_rate,
                0.0,
                customer_shipping=delivery_fee,
                supplier_name=supplier.name,
                supplier_country=supplier.country,
                local_vat_only=True,
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


