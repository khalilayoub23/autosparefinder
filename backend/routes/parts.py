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
    get_db, get_pii_db, PartsCatalog, Vehicle, SupplierPart, Supplier,
    CarBrand, User, PartImage, async_session_factory,
)
from BACKEND_AUTH_SECURITY import (
    get_redis, check_rate_limit, get_current_user,
)
from currency_rate import get_usd_to_ils_rate
from BACKEND_AI_AGENTS import get_agent, resolve_customer_shipping_fee as _resolve_ship_fee, get_supplier_vat_rate
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
    classify_part_subcategory,
    get_part_type_groups,
    iter_part_type_families,
    resolve_part_type_family,
)


def _supplier_source_tag(supplier_name: Optional[str], supplier_website: Optional[str] = None) -> str:
    name = (supplier_name or "").strip().lower()
    website = (supplier_website or "").strip().lower()
    if "ebay" in name or "ebay." in website:
        return "ebay"
    return "local"

router = APIRouter()

# Manufacturers list — an unindexed SELECT DISTINCT over 4.18M rows. Had NO
# cache: every hit ran the full scan, and concurrent hits stampeded (each ran
# its own scan), which took the whole box down under load / harvest (2026-07-07).
# 10-min cache + single-flight lock so only ONE request ever runs the scan while
# others wait for the shared result.
# 6h: the manufacturers list barely changes, and stale-while-revalidate serves instantly
# regardless — so a short TTL only means needlessly re-running the ~68s scan. The startup
# pre-warm fills it before first use; background refresh keeps it current a few times a day.
MANUFACTURERS_RESPONSE_TTL_S = 21600.0
MANUFACTURERS_RESPONSE_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_MANUFACTURERS_REBUILD_LOCK = asyncio.Lock()

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
SEARCH_MEILI_TIMEOUT_S = float(os.getenv("SEARCH_MEILI_TIMEOUT_S", "0.35"))
GOV_IL_LICENSE_RESOURCE_ID = "053cea08-09bc-40ec-8f7a-156f0677aff3"
GOV_IL_DATASTORE_URL = "https://data.gov.il/api/3/action/datastore_search"

_EXTERNAL_API_MAX_CONCURRENCY = int(os.getenv("EXTERNAL_API_MAX_CONCURRENCY", "8"))
_EXTERNAL_API_SEMAPHORE = asyncio.Semaphore(max(1, _EXTERNAL_API_MAX_CONCURRENCY))

# ── Canonical customer pricing (goal 2026-07-05: same price on every surface) ─
# ONE formula, identical to Stripe checkout (create_whatsapp_checkout):
#   sell_net = supplier_cost × 1.45   (uniform 45% margin — CLAUDE.md policy)
#   vat      = sell_net × 0.18
#   total    = sell_net + vat + shipping
# Every supplier offer the API returns carries these fields; the frontend and
# chat display them as-is and never invent their own margins again.
_MARGIN = 1.45
_VAT_RATE = 0.18

def _customer_price_fields(
    cost_ils: Optional[float],
    ship_ils: Optional[float],
    supplier_name: Optional[str] = None,
    supplier_country: Optional[str] = None,
) -> Dict[str, Any]:
    """Customer-facing price = cost×1.45 + CONDITIONAL VAT + shipping.

    VAT (2026-07-14 fix): 18% ONLY for LOCAL (IL) suppliers, 0% for foreign-sourced parts —
    identical to what checkout actually charges (create_whatsapp_checkout / get_supplier_vat_rate)
    and to brands.py / identify-from-image / NOA. Before, search applied a FLAT 18% to every
    supplier, so a foreign part DISPLAYED ~18% higher than it was charged at checkout. The
    displayed price must equal the charged price. When the supplier is unknown, fall back to the
    local rate so VAT is never silently dropped.
    """
    if not cost_ils or cost_ils <= 0:
        return {"customer_price_ils": None, "customer_vat_ils": None, "customer_total_ils": None}
    sell_net = round(float(cost_ils) * _MARGIN, 2)
    if supplier_name is not None or supplier_country is not None:
        vat_rate = get_supplier_vat_rate(supplier_name=supplier_name, supplier_country=supplier_country)
    else:
        vat_rate = _VAT_RATE
    vat = round(sell_net * vat_rate, 2)
    total = round(sell_net + vat + float(ship_ils or 0), 2)
    return {"customer_price_ils": sell_net, "customer_vat_ils": vat, "customer_total_ils": total}

async def _current_part_price(cat_db, part_id: str):
    """Cheapest available supplier → conditional-VAT customer price (net+VAT, no shipping).
    Returns (price_ils, part_name) or None. Shared by the watch endpoints + the checker loop."""
    row = (await cat_db.execute(text("""
        SELECT sp.price_ils, s.name, s.country, pc.name_he, pc.name
        FROM supplier_parts sp
        JOIN suppliers s ON s.id = sp.supplier_id
        JOIN parts_catalog pc ON pc.id = sp.part_id
        WHERE sp.part_id = :pid AND sp.is_available AND sp.price_ils > 0 AND s.is_active
        ORDER BY sp.price_ils ASC LIMIT 1
    """), {"pid": part_id})).first()
    if not row:
        return None
    fields = _customer_price_fields(float(row[0]), 0, supplier_name=row[1], supplier_country=row[2])
    return fields["customer_total_ils"], (row[3] or row[4] or "")


@router.post("/api/v1/parts/{part_id}/watch")
async def watch_part(part_id: str, current_user: User = Depends(get_current_user),
                     db: AsyncSession = Depends(get_pii_db)):
    """Watch a part's price — we email the customer (price_drop) if it drops ≥5%."""
    async with async_session_factory() as cat:
        cur = await _current_part_price(cat, part_id)
    if not cur:
        raise HTTPException(status_code=404, detail="החלק לא זמין לתמחור כרגע")
    price, name = cur
    await db.execute(text("""
        INSERT INTO part_price_watches (user_id, part_id, part_name, watch_price_ils)
        VALUES (:u, :p, :n, :pr)
        ON CONFLICT (user_id, part_id)
        DO UPDATE SET watch_price_ils = :pr, part_name = :n, last_notified_price_ils = NULL
    """), {"u": str(current_user.id), "p": part_id, "n": name, "pr": price})
    await db.commit()
    return {"watching": True, "price_ils": price}


@router.delete("/api/v1/parts/{part_id}/watch")
async def unwatch_part(part_id: str, current_user: User = Depends(get_current_user),
                       db: AsyncSession = Depends(get_pii_db)):
    await db.execute(text("DELETE FROM part_price_watches WHERE user_id = :u AND part_id = :p"),
                     {"u": str(current_user.id), "p": part_id})
    await db.commit()
    return {"watching": False}


@router.get("/api/v1/parts/watches")
async def list_watches(current_user: User = Depends(get_current_user),
                       db: AsyncSession = Depends(get_pii_db)):
    rows = (await db.execute(text("""
        SELECT part_id, part_name, watch_price_ils, created_at
        FROM part_price_watches WHERE user_id = :u ORDER BY created_at DESC
    """), {"u": str(current_user.id)})).fetchall()
    return {"watches": [{"part_id": str(r[0]), "part_name": r[1],
                         "watch_price_ils": float(r[2]),
                         "created_at": r[3].isoformat() if r[3] else None} for r in rows]}


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


def _search_redis_key(cache_key: Tuple) -> str:
    """Stable Redis key for a search cache tuple."""
    import hashlib, json as _json
    raw = _json.dumps(list(cache_key), ensure_ascii=False, sort_keys=True, default=str)
    return "autospare:search_cache:" + hashlib.sha256(raw.encode()).hexdigest()[:32]


async def _get_cached_search_response(cache_key: Tuple) -> Optional[Dict[str, Any]]:
    """Redis-backed search cache with in-memory fallback."""
    import json as _json
    # L1: in-memory
    cached = SEARCH_RESPONSE_CACHE.get(cache_key)
    if cached:
        expires_at, payload = cached
        if expires_at > time.monotonic():
            return copy.deepcopy(payload)
        SEARCH_RESPONSE_CACHE.pop(cache_key, None)

    # L2: Redis
    try:
        from BACKEND_AUTH_SECURITY import get_redis
        redis = await get_redis()
        raw = await redis.get(_search_redis_key(cache_key))
        if raw:
            payload = _json.loads(raw)
            # Warm L1 from Redis hit
            SEARCH_RESPONSE_CACHE[cache_key] = (time.monotonic() + SEARCH_RESPONSE_TTL_S, payload)
            return copy.deepcopy(payload)
    except Exception:
        pass  # Redis unavailable — cache miss is acceptable

    return None


async def _store_cached_search_response(cache_key: Tuple, payload: Dict[str, Any]) -> None:
    """Write-through to Redis and in-memory."""
    import json as _json
    # L1: in-memory with size cap
    if len(SEARCH_RESPONSE_CACHE) >= 256:
        expired_keys = [k for k, (exp, _) in SEARCH_RESPONSE_CACHE.items() if exp <= time.monotonic()]
        for k in expired_keys:
            SEARCH_RESPONSE_CACHE.pop(k, None)
        if len(SEARCH_RESPONSE_CACHE) >= 256:
            oldest = min(SEARCH_RESPONSE_CACHE, key=lambda k: SEARCH_RESPONSE_CACHE[k][0])
            SEARCH_RESPONSE_CACHE.pop(oldest, None)
    SEARCH_RESPONSE_CACHE[cache_key] = (time.monotonic() + SEARCH_RESPONSE_TTL_S, copy.deepcopy(payload))

    # L2: Redis write-through
    try:
        from BACKEND_AUTH_SECURITY import get_redis
        redis = await get_redis()
        await redis.setex(
            _search_redis_key(cache_key),
            int(SEARCH_RESPONSE_TTL_S),
            _json.dumps(payload, ensure_ascii=False, default=str),
        )
    except Exception:
        pass  # Redis write failure is non-fatal — L1 still serves


def _to_int_or_none(value: Any) -> Optional[int]:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except Exception:
        return None


def _to_bool_or_none(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
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

    # PERF (2026-07-11): this EXISTS was a ~57s scan (×3 part types) on the cold
    # path because its predicates were un-indexable. Rewritten to be index-usable
    # via pg_trgm GIN (manufacturer/model LIKE) + btree (model = ANY), WITHOUT
    # losing recall (verified match-count parity on Corolla / Corolla Verso /
    # Mazda 3 / i35 / Sportage).
    #
    # Manufacturer: equality (single-brand rows) + forward substring LIKE
    # (comma-joined multi-brand rows like "FORD, …, TOYOTA, …"). The old reverse
    # ":q LIKE '%'||manufacturer||'%'" branch is dropped — measured to add 0
    # recall for real brands and it made the whole OR un-indexable.
    pvf_mfr_clauses = []
    for idx, variant in enumerate(clean_variants):
        key = f"{prefix}_mfr_{idx}"
        params[key] = str(variant).strip().lower()
        pvf_mfr_clauses.append(
            f"(LOWER(TRIM(pvf.manufacturer)) = :{key}"
            f" OR LOWER(TRIM(pvf.manufacturer)) LIKE '%' || :{key} || '%')"
        )

    # Model: match TERMS = each model variant PLUS its progressive word-prefixes,
    # so a SPECIFIC customer model ("Corolla Verso") still matches a GENERAL
    # fitment model ("Corolla"). This replaces the old un-indexable reverse LIKE
    # with an indexable "= ANY(...)" (btree) — SAME recall (Corolla Verso →
    # 43,704 rows both ways). The forward prefix LIKE ("Corolla %") catches the
    # opposite direction (fitment MORE specific, e.g. "Corolla Cross").
    model_terms: set = set()
    model_fwd: List[str] = []
    for variant in model_variants:
        v = str(variant or "").strip().lower()
        if not v:
            continue
        model_fwd.append(v)
        words = v.split()
        for i in range(1, len(words) + 1):
            model_terms.add(" ".join(words[:i]))
    pvf_model_clauses = []
    if model_terms:
        mt_key = f"{prefix}_model_terms"
        params[mt_key] = sorted(model_terms)
        pvf_model_clauses.append(f"LOWER(TRIM(pvf.model)) = ANY(:{mt_key})")
    for idx, v in enumerate(model_fwd):
        key = f"{prefix}_modelfwd_{idx}"
        params[key] = v
        pvf_model_clauses.append(f"LOWER(TRIM(pvf.model)) LIKE :{key} || ' %'")

    params[f"{prefix}_year"] = int(vehicle_year)
    _mfr_sql = f"({' OR '.join(pvf_mfr_clauses)})" if pvf_mfr_clauses else "TRUE"
    _model_sql = f"({' OR '.join(pvf_model_clauses)})" if pvf_model_clauses else "TRUE"
    pvf_clause = (
        "EXISTS ("
        " SELECT 1 FROM part_vehicle_fitment pvf"
        " WHERE pvf.part_id = pc.id"
        f"   AND {_mfr_sql}"
        f"   AND {_model_sql}"
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
        "original_options":    [{"part": {...}, "suppliers": [...]}],
        "oem_options":         [{"part": {...}, "suppliers": [...]}],
        "aftermarket_options": [{"part": {...}, "suppliers": [...]}],
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
    _cached_search = await _get_cached_search_response(_s_cache_key)
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
        # Parity with chat search (2026-07-05): when a vehicle filter will
        # intersect the candidates, pull a deeper pool — 200 text matches
        # intersected with one vehicle's fitment rows often leaves 0-1
        # survivors. And expand Hebrew queries to English so global-catalog
        # parts ("Brake Pad Set") are reachable from a Hebrew search.
        _has_vehicle_filter = bool(vehicle_id or (vehicle_manufacturer and vehicle_model and vehicle_year))
        _meili_limit = 1000 if _has_vehicle_filter else 200
        _expanded_q = ""
        try:
            from hf_client import expand_hebrew_query
            _cand = expand_hebrew_query(query)
            if _cand and _cand.strip().lower() != query.strip().lower():
                _expanded_q = _cand.strip()
        except Exception:
            pass

        # Check Redis cache first — shared across all workers (30-min TTL).
        # Key includes limit + expansion so old shallow entries don't shadow.
        _meili_redis_key = f"meili:v2:{_meili_limit}:{query.lower().strip()[:100]}|{_expanded_q.lower()[:50]}"
        try:
            if redis:
                _cached_ids = await redis.get(_meili_redis_key)
                if _cached_ids:
                    import json as _json
                    meili_ids = _json.loads(_cached_ids)
        except Exception:
            pass

        if meili_ids is None:
            try:
                # Semaphore: max 8 concurrent Meilisearch queries per worker
                async with _EXTERNAL_API_SEMAPHORE:
                    async with httpx.AsyncClient(timeout=SEARCH_MEILI_TIMEOUT_S) as _mc:
                        _resp = await _mc.post(
                            f"{_meili_url}/indexes/parts/search",
                            headers={"Authorization": f"Bearer {os.getenv('MEILI_MASTER_KEY', '')}"},
                            json={"q": query, "limit": _meili_limit, "attributesToRetrieve": ["id"]},
                        )
                        _resp.raise_for_status()
                        meili_ids = [h["id"] for h in _resp.json().get("hits", [])]
                        if _expanded_q:
                            try:
                                _r2 = await _mc.post(
                                    f"{_meili_url}/indexes/parts/search",
                                    headers={"Authorization": f"Bearer {os.getenv('MEILI_MASTER_KEY', '')}"},
                                    json={"q": _expanded_q, "limit": _meili_limit, "attributesToRetrieve": ["id"]},
                                )
                                _r2.raise_for_status()
                                _seen = set(meili_ids)
                                meili_ids.extend(
                                    h["id"] for h in _r2.json().get("hits", [])
                                    if h["id"] not in _seen
                                )
                            except Exception:
                                pass  # expansion is best-effort
                        # Cache in Redis for 30 min — shared across all workers
                        if redis and meili_ids is not None:
                            try:
                                import json as _json2
                                await redis.set(_meili_redis_key, _json2.dumps(meili_ids), ex=1800)
                            except Exception:
                                pass
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
    normalized_query_token = re.sub(r"[^A-Z0-9]", "", (query or "").upper())
    identifier_query = bool(
        normalized_query_token
        and len(normalized_query_token) >= 5
        and any(ch.isdigit() for ch in normalized_query_token)
    )

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

    if category and not identifier_query:
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

    selected_part_family = resolve_part_type_family(category) if (category and not identifier_query) else None

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
        force_direct_db: bool = False,
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

        if meili_ids and not force_direct_db:
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
        # Fetch up to 20 (marketplace comparison) — frontend caps display via per_type
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
                    sp.shipping_cost_usd,
                    sp.availability,
                    sp.warranty_months,
                    sp.estimated_delivery_days,
                    sp.stock_quantity,
                    sp.supplier_url,
                    sp.express_available,
                    sp.express_price_ils,
                    sp.express_delivery_days,
                    sp.express_cutoff_time,
                    sp.last_checked_at,
                    s.website        AS supplier_website
                FROM supplier_parts sp
                JOIN suppliers s ON s.id = sp.supplier_id
                WHERE sp.part_id = :part_id
                  AND sp.is_available = TRUE
                  AND s.is_active = TRUE
                  AND s.name NOT IN ('Official Manufacturer Sites', 'Sandbox Supplier QA')
                  AND NULLIF(BTRIM(sp.supplier_url), '') IS NOT NULL
                ORDER BY COALESCE(sp.price_ils, sp.price_usd * :usd_to_ils_rate) ASC
                LIMIT 20
            """),
            {"part_id": part_id_str, "usd_to_ils_rate": usd_to_ils_rate},
        )).fetchall()

        # Supplier count + price range (for marketplace comparison badge)
        sup_agg = (await query_db.execute(
            text("""
                SELECT COUNT(*) as total_suppliers,
                       MIN(COALESCE(sp.price_ils, sp.price_usd * :rate)) as cheapest_ils,
                       MAX(COALESCE(sp.price_ils, sp.price_usd * :rate)) as most_expensive_ils
                FROM supplier_parts sp
                JOIN suppliers s ON s.id = sp.supplier_id
                WHERE sp.part_id = :part_id
                  AND sp.is_available = TRUE
                  AND s.is_active = TRUE
                  AND s.name NOT IN ('Official Manufacturer Sites', 'Sandbox Supplier QA')
                  AND NULLIF(BTRIM(sp.supplier_url), '') IS NOT NULL
            """),
            {"part_id": part_id_str, "rate": usd_to_ils_rate},
        )).fetchone()

        # Resolve barcode per quality type:
        # Original/OEM-equiv: use oem_number (it IS the barcode)
        # Aftermarket: use cross-reference ref_number if available
        _oem_barcode = part_row[11] or part_row[12]  # oem_number or barcode field
        _part_type_lower = (part_row[6] or "").lower()
        _aftermarket_barcode = None
        if "aftermarket" in _part_type_lower:
            try:
                _xref = await query_db.execute(
                    text("SELECT ref_number FROM part_cross_reference WHERE part_id = :pid::uuid AND ref_type ILIKE 'aftermarket' LIMIT 1"),
                    {"pid": part_id_str}
                )
                _xref_row = _xref.fetchone()
                if _xref_row:
                    _aftermarket_barcode = _xref_row[0]
            except Exception:
                pass
        _display_barcode = _aftermarket_barcode or _oem_barcode

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
            "barcode":          _display_barcode,   # quality-specific barcode
            "weight_kg":        float(part_row[13]) if part_row[13] else None,
            "is_safety_critical": part_row[14],
            "part_condition":   part_row[15],
            "created_at":       part_row[16].isoformat() if part_row[16] else None,
            "updated_at":       part_row[17].isoformat() if part_row[17] else None,
            # Marketplace comparison metadata
            "supplier_count":       int(sup_agg[0]) if sup_agg else 0,
            "cheapest_price_ils":   round(float(sup_agg[1]), 2) if sup_agg and sup_agg[1] else None,
            "most_expensive_ils":   round(float(sup_agg[2]), 2) if sup_agg and sup_agg[2] else None,
        }

        suppliers_list = []
        for sp in sup_rows:
            price_ils = float(sp[5]) if sp[5] else (float(sp[4]) * usd_to_ils_rate if sp[4] else None)
            shipping_cost_ils = float(sp[6]) if sp[6] is not None else None
            shipping_cost_usd = float(sp[7]) if sp[7] is not None else None
            supplier_source = _supplier_source_tag(sp[1], sp[18])
            shipping_cost_ils_resolved = _resolve_ship_fee(
                supplier_shipping_ils=shipping_cost_ils,
                supplier_shipping_usd=shipping_cost_usd,
                usd_to_ils_rate=usd_to_ils_rate,
                supplier_name=sp[1],
                supplier_country=sp[2],
            )
            suppliers_list.append({
                "supplier_part_id":      str(sp[0]),
                "supplier_name":         _mask_supplier(sp[1]),
                "supplier_country":      sp[2] or "",
                "supplier_sku":          sp[3],
                "source":                supplier_source,
                "price_usd":             float(sp[4]) if sp[4] else None,
                "price_ils":             round(price_ils, 2) if price_ils else None,
                **_customer_price_fields(price_ils, shipping_cost_ils_resolved,
                                         supplier_name=sp[1], supplier_country=sp[2]),
                "shipping_cost_ils":     shipping_cost_ils,
                "shipping_cost_usd":     shipping_cost_usd,
                "shipping_cost_ils_resolved": shipping_cost_ils_resolved,
                "availability":          sp[8],
                "warranty_months":       sp[9],
                "estimated_delivery_days": sp[10],
                "stock_quantity":        sp[11],
                # supplier_url omitted — internal use only for order placement
                "express_available":     sp[13],
                "express_price_ils":     float(sp[14]) if sp[14] else None,
                "express_delivery_days": sp[15],
                "express_cutoff_time":   sp[16],
                "last_checked_at":       sp[17].isoformat() if sp[17] else None,
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
                            sp.shipping_cost_usd,
                            sp.availability,
                            sp.warranty_months,
                            sp.estimated_delivery_days,
                            sp.stock_quantity,
                            sp.supplier_url,
                            sp.express_available,
                            sp.express_price_ils,
                            sp.express_delivery_days,
                            sp.express_cutoff_time,
                            sp.last_checked_at,
                            s.website               AS supplier_website
                        FROM supplier_parts sp
                        JOIN suppliers s ON s.id = sp.supplier_id
                        WHERE sp.part_id = ANY(CAST(:extra_ids AS uuid[]))
                          AND sp.is_available = TRUE
                          AND s.is_active = TRUE
                          AND s.name NOT IN ('Official Manufacturer Sites', 'Sandbox Supplier QA')
                          AND NULLIF(BTRIM(sp.supplier_url), '') IS NOT NULL
                        ORDER BY sp.part_id,
                                                                 COALESCE(sp.price_ils, sp.price_usd * :usd_to_ils_rate) ASC
                    """),
                                        {"extra_ids": extra_ids, "usd_to_ils_rate": usd_to_ils_rate},
                )).fetchall()
            except Exception:
                batch_rows = []
            best_sup_map: Dict[str, Any] = {row[0]: row for row in batch_rows}
            # Batch cross-ref for aftermarket barcodes
            aftermarket_ids = [str(r[0]) for r in matching_rows[1:] if (r[6] or "").lower().find("aftermarket") >= 0]
            xref_barcode_map: Dict[str, str] = {}
            if aftermarket_ids:
                try:
                    _xref_rows = (await query_db.execute(
                        text("SELECT part_id::text, ref_number FROM part_cross_reference WHERE part_id = ANY(CAST(:ids AS uuid[])) AND ref_type ILIKE 'aftermarket' ORDER BY id LIMIT 500"),
                        {"ids": aftermarket_ids}
                    )).fetchall()
                    for _xr in _xref_rows:
                        if _xr[0] not in xref_barcode_map:
                            xref_barcode_map[_xr[0]] = _xr[1]
                except Exception:
                    pass
            for extra_row in matching_rows[1:]:
                extra_id = str(extra_row[0])
                _ex_oem_barcode = extra_row[11] or extra_row[12]
                _ex_pt = (extra_row[6] or "").lower()
                _ex_barcode = (xref_barcode_map.get(extra_id) if "aftermarket" in _ex_pt else None) or _ex_oem_barcode
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
                    "barcode":          _ex_barcode,
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
                    b_shipping_cost_ils = float(bsp[7]) if bsp[7] is not None else None
                    b_shipping_cost_usd = float(bsp[8]) if bsp[8] is not None else None
                    b_supplier_source = _supplier_source_tag(bsp[2], bsp[19])
                    b_shipping_cost_ils_resolved = _resolve_ship_fee(
                        supplier_shipping_ils=b_shipping_cost_ils,
                        supplier_shipping_usd=b_shipping_cost_usd,
                        usd_to_ils_rate=usd_to_ils_rate,
                        supplier_name=bsp[2],
                        supplier_country=bsp[3],
                    )
                    extra_suppliers = [{
                        "supplier_part_id":        bsp[1],
                        "supplier_name":           _mask_supplier(bsp[2]),
                        "supplier_country":        bsp[3] or "",
                        "supplier_sku":            bsp[4],
                        "source":                  b_supplier_source,
                        "price_usd":               float(bsp[5]) if bsp[5] else None,
                        "price_ils":               round(b_price_ils, 2) if b_price_ils else None,
                        **_customer_price_fields(b_price_ils, b_shipping_cost_ils_resolved,
                                                 supplier_name=bsp[2], supplier_country=bsp[3]),
                        "shipping_cost_ils":       b_shipping_cost_ils,
                        "shipping_cost_usd":       b_shipping_cost_usd,
                        "shipping_cost_ils_resolved": b_shipping_cost_ils_resolved,
                        "availability":            bsp[9],
                        "warranty_months":         bsp[10],
                        "estimated_delivery_days": bsp[11],
                        "stock_quantity":          bsp[12],
                        # supplier_url omitted — internal only
                        "express_available":       bsp[14],
                        "express_price_ils":       float(bsp[15]) if bsp[15] else None,
                        "express_delivery_days":   bsp[16],
                        "express_cutoff_time":     bsp[17],
                        "last_checked_at":         bsp[18].isoformat() if bsp[18] else None,
                    }]
                results.append({"part": extra_dict, "suppliers": extra_suppliers})

        return results

    async def _fetch_three_buckets(
        where_sql_override: Optional[str] = None,
        where_sql_base_override: Optional[str] = None,
        force_direct_db: bool = False,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
        async with async_session_factory() as db_original, async_session_factory() as db_oem, async_session_factory() as db_after:
            original_task = _fetch_type(
                ["Original"],
                where_sql_override=where_sql_override,
                where_sql_base_override=where_sql_base_override,
                force_direct_db=force_direct_db,
                db_session=db_original,
            )
            oem_task = _fetch_type(
                ["OEM"],
                where_sql_override=where_sql_override,
                where_sql_base_override=where_sql_base_override,
                force_direct_db=force_direct_db,
                db_session=db_oem,
            )
            aftermarket_task = _fetch_type(
                ["Aftermarket", "Refurbished"],
                include_general=True,
                where_sql_override=where_sql_override,
                where_sql_base_override=where_sql_base_override,
                force_direct_db=force_direct_db,
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
    if identifier_query:
        strict_clause_to_remove = strict_vehicle_clause_added if strict_vehicle_context else manual_strict_clause_added
        identifier_base_conditions: List[str] = []
        removed_strict = False
        for clause in conditions_base:
            if strict_clause_to_remove and not removed_strict and clause == strict_clause_to_remove:
                removed_strict = True
                continue
            identifier_base_conditions.append(clause)

        identifier_match_sql = (
            "("
            "regexp_replace(UPPER(COALESCE(pc.sku, '')), '[^A-Z0-9]', '', 'g') = :identifier_token "
            "OR regexp_replace(UPPER(COALESCE(pc.oem_number, '')), '[^A-Z0-9]', '', 'g') = :identifier_token "
            "OR EXISTS ("
            "SELECT 1 FROM part_cross_reference pcr_exact "
            "WHERE pcr_exact.part_id = pc.id "
            "AND regexp_replace(UPPER(COALESCE(pcr_exact.ref_number, '')), '[^A-Z0-9]', '', 'g') = :identifier_token"
            ")"
            ")"
        )
        identifier_base_conditions.append(identifier_match_sql)
        identifier_where_base = " AND ".join(identifier_base_conditions)
        identifier_where = identifier_where_base
        params["identifier_token"] = normalized_query_token
        if query_condition_sql:
            identifier_where = f"{identifier_where_base} AND {query_condition_sql}"

        exact_original, exact_oem, exact_aftermarket = await _fetch_three_buckets(
            where_sql_override=identifier_where,
            where_sql_base_override=identifier_where_base,
            force_direct_db=True,
        )

        def _merge_exact_rows(
            existing_rows: List[Dict[str, Any]],
            exact_rows: List[Dict[str, Any]],
        ) -> List[Dict[str, Any]]:
            merged: List[Dict[str, Any]] = []
            seen_ids: set = set()
            for row in [*exact_rows, *existing_rows]:
                part_id = str((row.get("part") or {}).get("id") or "")
                if not part_id or part_id in seen_ids:
                    continue
                seen_ids.add(part_id)
                merged.append(row)
            return merged

        original_res = _merge_exact_rows(original_res, exact_original)
        oem_res = _merge_exact_rows(oem_res, exact_oem)
        aftermarket_res = _merge_exact_rows(aftermarket_res, exact_aftermarket)


    all_bucket_rows = [*original_res, *oem_res, *aftermarket_res]

    # Attach part images to search buckets so result cards can render pictures.
    part_ids_for_images = list(dict.fromkeys([
        str((row.get("part") or {}).get("id"))
        for row in all_bucket_rows
        if (row.get("part") or {}).get("id")
    ]))

    image_map: Dict[str, List[str]] = {}
    spec_payload_map: Dict[str, Dict[str, Any]] = {}
    if part_ids_for_images:
        image_rows = (await db.execute(
            text(
                """
                SELECT part_id::text AS part_id, url
                FROM parts_images
                WHERE part_id = ANY(CAST(:part_ids AS uuid[]))
                  AND url IS NOT NULL
                ORDER BY part_id, is_primary DESC, sort_order ASC, created_at DESC
                """
            ),
            {"part_ids": part_ids_for_images},
        )).fetchall()

        for img_row in image_rows:
            pid = str(img_row[0])
            url = str(img_row[1] or "").strip()
            if not url:
                continue
            pid_images = image_map.setdefault(pid, [])
            if url not in pid_images:
                pid_images.append(url)

        missing_part_ids = [pid for pid in part_ids_for_images if pid not in image_map]
        if missing_part_ids:
            spec_rows = (await db.execute(
                text(
                    """
                    SELECT
                        id::text AS part_id,
                        COALESCE(specifications->'ebay'->'image_urls', '[]'::jsonb) AS image_urls
                    FROM parts_catalog
                    WHERE id = ANY(CAST(:part_ids AS uuid[]))
                    """
                ),
                {"part_ids": missing_part_ids},
            )).fetchall()

            for spec_row in spec_rows:
                pid = str(spec_row[0])
                raw_urls = spec_row[1]
                if isinstance(raw_urls, str):
                    try:
                        raw_urls = json.loads(raw_urls)
                    except Exception:
                        raw_urls = []

                if isinstance(raw_urls, list):
                    parsed_urls = [str(u).strip() for u in raw_urls if str(u).strip()]
                    if parsed_urls:
                        image_map[pid] = list(dict.fromkeys(parsed_urls))

    if part_ids_for_images:
        spec_payload_rows = (await db.execute(
            text(
                "SELECT id::text AS part_id, COALESCE(specifications, jsonb_build_object()) AS specifications FROM parts_catalog WHERE id = ANY(CAST(:part_ids AS uuid[]))"
            ),
            {"part_ids": part_ids_for_images},
        )).fetchall()

        for spec_payload_row in spec_payload_rows:
            pid = str(spec_payload_row[0])
            raw_specs = spec_payload_row[1]
            if isinstance(raw_specs, str):
                try:
                    raw_specs = json.loads(raw_specs)
                except Exception:
                    raw_specs = {}
            if not isinstance(raw_specs, dict):
                raw_specs = {}

            ebay_meta = raw_specs.get("ebay") if isinstance(raw_specs.get("ebay"), dict) else {}
            technical_specs = ebay_meta.get("tech_specs") if isinstance(ebay_meta.get("tech_specs"), dict) else {}
            warranty_text = ebay_meta.get("warranty_text") if isinstance(ebay_meta, dict) else None
            warranty_months_raw = ebay_meta.get("warranty_months") if isinstance(ebay_meta, dict) else None
            ships_to_israel = _to_bool_or_none(ebay_meta.get("ships_to_israel") if isinstance(ebay_meta, dict) else None)
            try:
                warranty_months = int(warranty_months_raw) if warranty_months_raw is not None else None
                if warranty_months is not None and warranty_months <= 0:
                    warranty_months = None
            except Exception:
                warranty_months = None

            spec_payload_map[pid] = {
                "specifications": raw_specs,
                "technical_specs": technical_specs if isinstance(technical_specs, dict) else {},
                "warranty_details": {
                    "text": str(warranty_text).strip()[:500] if warranty_text else None,
                    "months": warranty_months,
                    "source": "ebay",
                } if (warranty_text or warranty_months is not None) else None,
                "ships_to_israel": ships_to_israel,
            }
    # Clean thumbnails (Contabo bucket) are the ONLY image surfaced to customers, so a raw
    # supplier ad/placeholder image can never slip through (owner rule 2026-07-18). Raw
    # supplier image URLs from parts_images/eBay are intentionally NOT returned.
    thumb_map: Dict[str, str] = {}
    if part_ids_for_images:
        try:
            _tr = (await db.execute(text(
                "SELECT part_id::text, url FROM part_thumbnails "
                "WHERE part_id = ANY(CAST(:ids AS uuid[])) AND status='ok' AND url IS NOT NULL"
            ), {"ids": part_ids_for_images})).fetchall()
            thumb_map = {str(r[0]): str(r[1]) for r in _tr}
        except Exception:
            thumb_map = {}

    for bucket in (original_res, oem_res, aftermarket_res):
        for row in bucket:
            part_payload = row.get("part") or {}
            pid = str(part_payload.get("id") or "")
            _thumb = thumb_map.get(pid)
            part_payload["images"] = [_thumb] if _thumb else []
            part_payload["primary_image"] = _thumb
            spec_payload = spec_payload_map.get(pid) or {}
            part_payload["specifications"] = spec_payload.get("specifications") or {}
            part_payload["technical_specs"] = spec_payload.get("technical_specs") or {}
            part_payload["warranty_details"] = spec_payload.get("warranty_details")
            part_payload["ships_to_israel"] = spec_payload.get("ships_to_israel")

            suppliers_payload = row.get("suppliers") or []
            filtered_suppliers: List[Dict[str, Any]] = []
            for supplier_payload in suppliers_payload:
                supplier_data = supplier_payload or {}
                supplier_source = str(supplier_data.get("source") or "").strip().lower()
                if supplier_source == "ebay":
                    ebay_ships_to_israel = bool(spec_payload.get("ships_to_israel"))
                    supplier_data["ships_to_israel"] = ebay_ships_to_israel
                    if not ebay_ships_to_israel:
                        continue
                filtered_suppliers.append(supplier_data)
            row["suppliers"] = filtered_suppliers

    # Primary bucket (best surfaced card per type) for the 3-column TypeSection widget.
    # Prefer entries with real supplier offers and images to avoid blank cards.
    def _primary(res: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not res:
            return {"part": None, "suppliers": []}

        def _best_price_ils(suppliers: List[Dict[str, Any]]) -> float:
            for sup in suppliers or []:
                try:
                    price = sup.get("price_ils")
                    if price is not None:
                        return float(price)
                except Exception:
                    continue
            return float("inf")

        def _rank(entry: Dict[str, Any]) -> Tuple[int, int, int, int, float]:
            part_payload = entry.get("part") or {}
            suppliers = entry.get("suppliers") or []
            has_suppliers = 1 if suppliers else 0
            has_in_stock = 1 if any((s or {}).get("availability") == "in_stock" for s in suppliers) else 0
            images = part_payload.get("images") or []
            has_image = 1 if (part_payload.get("primary_image") or images) else 0
            has_warranty = 1 if any(((s or {}).get("warranty_months") or 0) > 0 for s in suppliers) else 0
            return (-has_suppliers, -has_image, -has_warranty, -has_in_stock, _best_price_ils(suppliers))

        return min(res, key=_rank)

    # ── External supplier results — returned from cache only, never blocks search ──
    # External lookups run in a background task after this response returns.
    # On the NEXT search for the same query, cached ext results are included.
    _ext_cache_key = f"ext_sup:{query.lower().strip()[:80]}"
    external_supplier_results: list = []
    try:
        if redis:
            _cached_ext = await redis.get(_ext_cache_key)
            if _cached_ext:
                import json as _json
                external_supplier_results = _json.loads(_cached_ext)
    except Exception:
        pass

    # Fire external lookup in background — doesn't block this response at all
    if query and os.getenv("ENABLE_EXTERNAL_SUPPLIERS", "1").strip() in ("1", "true", "yes"):
        async def _fetch_ext_suppliers(_q: str, _key: str, _redis):
            try:
                from services.supplier_aggregator import ACTIVE_SUPPLIERS
                _suppliers = [s for s in ACTIVE_SUPPLIERS if getattr(s, "name", "") != "local_db"]
                _tasks = [asyncio.wait_for(s.search(_q, limit=3), timeout=2.0) for s in _suppliers]
                _responses = await asyncio.gather(*_tasks, return_exceptions=True)
                _results = []
                for _res in _responses:
                    if isinstance(_res, list):
                        _results.extend(r.__dict__ if hasattr(r, "__dict__") else r for r in _res)
                if _results and _redis:
                    import json as _json2
                    await _redis.set(_key, _json2.dumps(_results, default=str), ex=3600)
            except Exception:
                pass
        asyncio.create_task(_fetch_ext_suppliers(query, _ext_cache_key, redis))

    _search_result = {
        "original":            _primary(original_res),
        "oem":                 _primary(oem_res),
        "aftermarket":         _primary(aftermarket_res),
        "original_options":    original_res,
        "oem_options":         oem_res,
        "aftermarket_options": aftermarket_res,
        "all_parts":           [*original_res, *oem_res, *aftermarket_res],
        "external_suppliers":  external_supplier_results,  # cached from prev lookup
        "results_per_type":    per_type,
        "query":               query,
    }
    await _store_cached_search_response(_s_cache_key, _search_result)
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
        # Query real per-category counts from DB (aggregate, fast)
        agg_rows = (
            await db.execute(
                text(
                    """
                    SELECT category, part_type, COUNT(*) AS cnt
                    FROM parts_catalog
                    WHERE is_active = TRUE
                    GROUP BY category, part_type
                    """
                )
            )
        ).fetchall()
        family_counts: Dict[str, int] = {family.id: 0 for family in iter_part_type_families()}
        subcategory_counts: Dict[str, int] = {}
        flat_counts: Dict[str, int] = {family.label: 0 for family in iter_part_type_families()}
        fallback_counts: Dict[str, int] = {c: 0 for c in CANONICAL_FILTER_CATEGORIES}
        for raw_category, raw_part_type, cnt in agg_rows:
            family = classify_part_type_family(raw_category, raw_part_type, None, None, None)
            if family:
                family_counts[family.id] = family_counts.get(family.id, 0) + cnt
                flat_counts[family.label] = flat_counts.get(family.label, 0) + cnt
            else:
                canonical = _normalize_filter_category(raw_category)
                if canonical:
                    fallback_counts[canonical] = fallback_counts.get(canonical, 0) + cnt
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
        for family in families:
            family["subcategories"] = [
                subcategory.serialize(count=subcategory_counts.get(subcategory.id, 0))
                for subcategory in next(item for item in iter_part_type_families() if item.id == family["id"]).subcategories
            ]
        response = {
            "categories": [family["id"] for family in families],
            "counts": {**fallback_counts, **flat_counts},
            "family_counts": family_counts,
            "subcategory_counts": subcategory_counts,
            "families": families,
            "groups": groups,
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
    subcategory_counts: Dict[str, int] = {}
    flat_counts: Dict[str, int] = {family.label: 0 for family in iter_part_type_families()}
    fallback_counts: Dict[str, int] = {c: 0 for c in CANONICAL_FILTER_CATEGORIES}

    for raw_category, raw_part_type, name, name_he, description in rows:
        family = classify_part_type_family(raw_category, raw_part_type, name, name_he, description)
        if family:
            family_counts[family.id] = family_counts.get(family.id, 0) + 1
            flat_counts[family.label] = flat_counts.get(family.label, 0) + 1
            subcategory_match = classify_part_subcategory(raw_category, raw_part_type, name, name_he, description)
            if subcategory_match:
                _, subcategory = subcategory_match
                subcategory_counts[subcategory.id] = subcategory_counts.get(subcategory.id, 0) + 1
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
    for family in families:
        family["subcategories"] = [
            {**subcategory.serialize(count=subcategory_counts.get(subcategory.id, 0)), "group_id": family["group_id"]}
            for subcategory in next(item for item in iter_part_type_families() if item.id == family["id"]).subcategories
        ]
    counts: Dict[str, int] = {**fallback_counts}
    counts.update(flat_counts)
    categories = [family["id"] for family in families]
    response = {
        "categories": categories,
        "counts": counts,
        "family_counts": family_counts,
        "subcategory_counts": subcategory_counts,
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
async def _refresh_manufacturers_cache() -> None:
    """Run the expensive manufacturers scan and refresh the cache. Opens its own DB
    session so it can run in the background (never tied to a user request). Single-flight
    via the shared lock so only one scan ever runs at a time."""
    if _MANUFACTURERS_REBUILD_LOCK.locked():
        return  # a refresh is already in flight
    async with _MANUFACTURERS_REBUILD_LOCK:
        _mc = MANUFACTURERS_RESPONSE_CACHE.get("all")
        if _mc and _mc[0] > time.monotonic():
            return  # someone else just refreshed it
        try:
            async with async_session_factory() as _db:
                result = await _get_manufacturers_uncached(_db)
            if isinstance(result, dict) and result.get("manufacturers"):
                MANUFACTURERS_RESPONSE_CACHE["all"] = (
                    time.monotonic() + MANUFACTURERS_RESPONSE_TTL_S,
                    copy.deepcopy(result),
                )
        except Exception as _e:
            print(f"[manufacturers] background refresh failed: {_e}")


async def get_manufacturers(db: AsyncSession = Depends(get_db)):
    # The underlying scan (CROSS JOIN LATERAL over compatible_vehicles across 4.18M
    # rows) takes ~60-70s and NO index can fix an unnest+aggregate — so a user request
    # must NEVER trigger it inline (that was the "pages hang after login" symptom, since
    # the parts page fetches the brand dropdown). Strategy: stale-while-revalidate.
    #   • fresh cache        → serve instantly
    #   • stale cache        → serve the stale copy instantly + refresh in the background
    #   • cold (no cache)    → single-flight compute (happens once, then it's warm; the
    #                          startup pre-warm normally fills this before any user hits it)
    _mc = MANUFACTURERS_RESPONSE_CACHE.get("all")
    if _mc:
        if _mc[0] <= time.monotonic():
            # stale — kick a background refresh but DON'T block the user on it
            asyncio.create_task(_refresh_manufacturers_cache())
        return copy.deepcopy(_mc[1])
    # Cold cache: single-flight so only one request runs the scan; others wait for it.
    async with _MANUFACTURERS_REBUILD_LOCK:
        _mc = MANUFACTURERS_RESPONSE_CACHE.get("all")
        if _mc and _mc[0] > time.monotonic():
            return copy.deepcopy(_mc[1])
        result = await _get_manufacturers_uncached(db)
        if isinstance(result, dict) and result.get("manufacturers"):
            MANUFACTURERS_RESPONSE_CACHE["all"] = (
                time.monotonic() + MANUFACTURERS_RESPONSE_TTL_S,
                copy.deepcopy(result),
            )
        return result


async def _get_manufacturers_uncached(db: AsyncSession):
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
        _vin_ip = request.headers.get("X-Real-IP") or (request.client.host if request.client else "anon")
        allowed = await check_rate_limit(redis, f"search_by_vin:{_vin_ip}", 10, 60)
        if not allowed:
            raise HTTPException(status_code=429, detail="יותר מדי בקשות VIN — נסה שוב בעוד דקה")
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

@router.get("/api/v1/parts/{part_id}/suppliers")
async def get_part_suppliers(
    part_id: str,
    limit: int = Query(default=50, le=200),
    db: AsyncSession = Depends(get_db),
    request: Request = None,
    redis=Depends(get_redis),
):
    """
    Marketplace comparison endpoint — returns ALL supplier offers for a part,
    sorted cheapest total (price + shipping) first.
    Used by the product detail page comparison table (eBay/AliExpress style).
    """
    if redis and request:
        ip = request.client.host if request.client else "unknown"
        allowed = await check_rate_limit(redis, f"rate:part_suppliers:{ip}", 60, 60)
        if not allowed:
            raise HTTPException(status_code=429, detail="יותר מדי בקשות — נסה שוב בעוד דקה")

    usd_to_ils_rate = float(await get_usd_to_ils_rate(db))

    # Fetch all available suppliers — no URL filter (marketplace shows all price sources)
    rows = (await db.execute(
        text("""
            SELECT
                sp.id::text,
                s.name,
                s.country,
                s.website,
                sp.supplier_sku,
                sp.price_usd,
                sp.price_ils,
                sp.shipping_cost_ils,
                sp.shipping_cost_usd,
                sp.availability,
                sp.stock_quantity,
                sp.warranty_months,
                sp.estimated_delivery_days,
                sp.supplier_url,
                sp.express_available,
                sp.express_price_ils,
                sp.express_delivery_days,
                sp.last_checked_at,
                sp.part_type,
                COALESCE(sp.price_ils, sp.price_usd * :rate)
                    + COALESCE(sp.shipping_cost_ils, 0) AS total_cost_ils
            FROM supplier_parts sp
            JOIN suppliers s ON s.id = sp.supplier_id
            WHERE sp.part_id = :pid
              AND sp.is_available = TRUE
              AND s.is_active = TRUE
              AND s.name NOT IN ('Official Manufacturer Sites', 'Sandbox Supplier QA')
            ORDER BY total_cost_ils ASC NULLS LAST
            LIMIT :lim
        """),
        {"pid": part_id, "rate": usd_to_ils_rate, "lim": limit},
    )).fetchall()

    suppliers = []
    for r in rows:
        price_ils = float(r[6]) if r[6] else (float(r[5]) * usd_to_ils_rate if r[5] else None)
        ship_ils = _resolve_ship_fee(
            supplier_shipping_ils=float(r[7]) if r[7] else None,
            supplier_shipping_usd=float(r[8]) if r[8] else None,
            usd_to_ils_rate=usd_to_ils_rate,
            supplier_name=r[1],
            supplier_country=r[2],
        )
        _cust = _customer_price_fields(price_ils, ship_ils, supplier_name=r[1], supplier_country=r[2])
        suppliers.append({
            "supplier_part_id":        r[0],
            # Supplier identity is masked — customer only sees AutoSpareFinder
            # Real supplier name/URL stored internally for order placement only
            "supplier_name":           _mask_supplier(r[1]),
            "supplier_country":        r[2] or "",
            # supplier_website and supplier_url intentionally OMITTED from response
            # — exposing these lets customers bypass us and order directly
            "supplier_sku":            r[4],
            "price_usd":               float(r[5]) if r[5] else None,
            # price_ils = CUSTOMER sell price (cost×1.45), never raw supplier
            # cost — this endpoint used to leak our purchase cost (2026-07-05)
            "price_ils":               _cust["customer_price_ils"],
            **_cust,
            "shipping_cost_ils":       ship_ils,
            "availability":            r[9],
            "stock_quantity":          r[10],
            "warranty_months":         r[11],
            "estimated_delivery_days": r[12],
            "express_available":       r[14],
            "express_price_ils":       float(r[15]) if r[15] else None,
            "express_delivery_days":   r[16],
            "last_checked_at":         r[17].isoformat() if r[17] else None,
            "part_type":               r[18],
            "total_cost_ils":          _cust["customer_total_ils"],
            "source":                  _supplier_source_tag(r[1], r[3]),
        })

    # Fallback: if no supplier_parts, check if part has IL importer price
    # Add AutoSpareFinder as a synthetic supplier offer (marketplace own stock)
    if not suppliers:
        part_row = (await db.execute(
            text("SELECT base_price, importer_price_ils, part_condition, sku FROM parts_catalog WHERE id = :pid AND is_active"),
            {"pid": part_id}
        )).fetchone()
        if part_row and part_row[0] and float(part_row[0]) > 0:
            suppliers.append({
                "supplier_part_id":       None,
                "supplier_name":          "AutoSpareFinder",
                "supplier_country":       "IL",
                "supplier_website":       "https://autosparefinder.co.il",
                "supplier_logo":          None,
                "supplier_sku":           part_row[3],
                "price_usd":              None,
                # base_price is already cost×1.45 — add VAT for the customer total
                "price_ils":              float(part_row[0]),
                "customer_price_ils":     float(part_row[0]),
                "customer_vat_ils":       round(float(part_row[0]) * _VAT_RATE, 2),
                "customer_total_ils":     round(float(part_row[0]) * (1 + _VAT_RATE), 2),
                "shipping_cost_ils":      0.0,
                "availability":           "in_stock",
                "stock_quantity":         None,
                "warranty_months":        12,
                "estimated_delivery_days": 3,
                "supplier_url":           f"https://autosparefinder.co.il/parts/{part_id}",
                "express_available":      False,
                "express_price_ils":      None,
                "express_delivery_days":  None,
                "last_checked_at":        None,
                "part_condition":         part_row[2] or "new",
                "part_type":              "aftermarket",
                "total_cost_ils":         round(float(part_row[0]) * (1 + _VAT_RATE), 2),
                "source":                 "autosparefinder",
            })

    return {
        "part_id":        part_id,
        "supplier_count": len(suppliers),
        "cheapest_ils":   suppliers[0]["price_ils"] if suppliers else None,
        "suppliers":      suppliers,
    }


# ==============================================================================

@router.get("/api/v1/parts/{part_id}")
async def get_part(part_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(PartsCatalog).where(PartsCatalog.id == part_id))
    part = result.scalar_one_or_none()
    if not part:
        raise HTTPException(status_code=404, detail="Part not found")

    images_res = await db.execute(
        select(PartImage.url)
        .where(and_(PartImage.part_id == part.id, PartImage.url.is_not(None)))
        .order_by(PartImage.is_primary.desc(), PartImage.sort_order.asc(), PartImage.created_at.desc())
    )
    image_urls = [str(r[0]) for r in images_res.fetchall() if r[0]]

    specs = part.specifications or {}
    ebay_meta = specs.get("ebay") if isinstance(specs, dict) else {}
    warranty_details = None
    if isinstance(ebay_meta, dict):
        warranty_text = ebay_meta.get("warranty_text")
        warranty_months = ebay_meta.get("warranty_months")
        if warranty_text or warranty_months is not None:
            warranty_details = {
                "text": warranty_text,
                "months": warranty_months,
                "source": "ebay",
            }

    return {
        "id": str(part.id),
        "name": part.name,
        "manufacturer": part.manufacturer,
        "category": part.category,
        "part_type": part.part_type,
        "description": part.description,
        "specifications": specs,
        "images": image_urls,
        "primary_image": image_urls[0] if image_urls else None,
        "warranty_details": warranty_details,
    }


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
        .where(and_(
            SupplierPart.part_id == part_id,
            SupplierPart.is_available == True,
            Supplier.is_active == True,
            Supplier.name.notin_(["Official Manufacturer Sites", "Sandbox Supplier QA"]),
            SupplierPart.supplier_url.is_not(None),
            func.length(func.btrim(SupplierPart.supplier_url)) > 0,
        ))
        .order_by(Supplier.priority.asc())
    )
    rows = result.all()

    # Fallback to on_order if nothing in stock
    if not rows:
        result2 = await db.execute(
            select(SupplierPart, Supplier).join(Supplier)
            .where(and_(
                SupplierPart.part_id == part_id,
                Supplier.is_active == True,
                Supplier.name.notin_(["Official Manufacturer Sites", "Sandbox Supplier QA"]),
                SupplierPart.supplier_url.is_not(None),
                func.length(func.btrim(SupplierPart.supplier_url)) > 0,
            ))
            .order_by(Supplier.priority.asc())
        )
        rows = result2.all()

    agent = get_agent("parts_finder_agent")
    usd_to_ils_rate = await get_usd_to_ils_rate(db)
    comparisons = []
    for sp, supplier in rows:
        cost_ils = float(sp.price_ils or 0)
        ship_ils = float(sp.shipping_cost_ils or 0)
        delivery_fee = _resolve_ship_fee(
            supplier_shipping_ils=sp.shipping_cost_ils,
            supplier_shipping_usd=sp.shipping_cost_usd,
            usd_to_ils_rate=usd_to_ils_rate,
            supplier_name=supplier.name,
            supplier_country=supplier.country,
        )
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
            "warranty_months": sp.warranty_months,
            "estimated_delivery": (f"{sp.estimated_delivery_days}-{sp.estimated_delivery_days + 7} ימים" if sp.estimated_delivery_days is not None else None),
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
                    "If SEVERAL parts are visible (e.g. a component still mounted in the engine "
                    "bay), identify the part in the CENTER / FOREGROUND that the photo is framed "
                    "and focused on — NOT the largest hose, pipe, duct or cover filling the "
                    "background. If crowded and unsure, LOWER confidence and list the other "
                    "likely foreground parts in possible_names. "
                    "Think step by step: 1) What vehicle system does this part belong to? "
                    "2) What is the exact part in the foreground? "
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


