from __future__ import annotations

import base64
import os
import time
import threading
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus, urlencode, urlparse

DEFAULT_AUTODOC_PROVIDER_URLS: List[str] = []

# Module-level eBay OAuth2 token cache — auto-refreshes via client credentials.
# Thread-safe: only used from within async tasks but cached at module level.
_ebay_token_lock = threading.Lock()
_ebay_token_cache: Dict[str, Any] = {"token": "", "expires_at": 0.0}


def _get_ebay_oauth_token() -> str:
    """
    Return a valid eBay OAuth2 client-credentials access token.
    Reads EBAY_APP_ID + EBAY_CERT_ID from env, caches the token until 60s
    before expiry, and uses EBAY_BEARER_TOKEN as a static fallback if set to
    a real JWT (starts with 'v^1.1').
    """
    # Fast path: cached token still valid
    with _ebay_token_lock:
        if _ebay_token_cache["token"] and time.time() < _ebay_token_cache["expires_at"]:
            return _ebay_token_cache["token"]

    # Check if EBAY_BEARER_TOKEN is already a valid JWT-style token
    static = os.getenv("EBAY_BEARER_TOKEN", "").strip()
    if static.startswith("v^1.1"):
        with _ebay_token_lock:
            _ebay_token_cache["token"] = static
            _ebay_token_cache["expires_at"] = time.time() + 3600
        return static

    # Fetch a fresh token via OAuth2 client credentials
    app_id = os.getenv("EBAY_APP_ID", "").strip() or os.getenv("EBAY_CLIENT_ID", "").strip()
    cert_id = os.getenv("EBAY_CERT_ID", "").strip() or os.getenv("EBAY_CLIENT_SECRET", "").strip()
    if not app_id or not cert_id:
        return ""

    try:
        import httpx as _httpx
        creds = base64.b64encode(f"{app_id}:{cert_id}".encode()).decode()
        token_url = "https://api.ebay.com/identity/v1/oauth2/token"
        resp = _httpx.Client(timeout=10).post(
            token_url,
            headers={
                "Authorization": f"Basic {creds}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={
                "grant_type": "client_credentials",
                "scope": "https://api.ebay.com/oauth/api_scope",
            },
        )
        if resp.status_code == 200:
            payload = resp.json()
            token = str(payload.get("access_token") or "")
            expires_in = int(payload.get("expires_in") or 0)
            if token and expires_in > 0:
                with _ebay_token_lock:
                    _ebay_token_cache["token"] = token
                    _ebay_token_cache["expires_at"] = time.time() + max(0, expires_in - 60)
                return token
    except Exception:
        pass
    return ""

_FITMENT_CAPABLE_KINDS = {
    "autodoc_like",
    "ebay_browse",
    "rockauto_json",
    "oem_epc_json",
}


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _dedupe_keep_order(items: List[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for item in items:
        value = str(item or "").strip()
        if not value:
            continue
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _provider_slug_from_url(base_url: str) -> str:
    parsed = urlparse(base_url)
    host = (parsed.netloc or "provider").lower()
    if host.startswith("www."):
        host = host[4:]
    slug = host.replace(".", "_").replace("-", "_")
    return slug or "provider"


def _provider_urls_from_env() -> List[str]:
    raw = os.getenv("EXTERNAL_FITMENT_PROVIDER_URLS", "")
    if raw.strip():
        parsed = [chunk.strip().rstrip("/") for chunk in raw.split(",") if chunk.strip()]
        return _dedupe_keep_order(parsed)
    return list(DEFAULT_AUTODOC_PROVIDER_URLS)


def _format_endpoint_template(template: str, part_number: str, brand: str) -> str:
    safe_template = str(template or "").strip()
    if not safe_template:
        return ""
    try:
        return safe_template.format(
            part_number=part_number,
            brand=brand,
            part_number_encoded=quote_plus(part_number),
            brand_encoded=quote_plus(brand),
        )
    except Exception:
        return safe_template


def provider_endpoint_summary() -> List[str]:
    out: List[str] = []
    out.extend(_provider_urls_from_env())

    if _env_bool("EXTERNAL_ENABLE_NHTSA", default=False):
        nhtsa_base = os.getenv("NHTSA_API_BASE", "https://vpic.nhtsa.dot.gov/api").rstrip("/")
        out.append(f"{nhtsa_base}/vehicles/GetModelsForMake/{{brand}}?format=json")

    if _env_bool("EXTERNAL_ENABLE_EBAY", default=False):
        ebay_base = os.getenv("EBAY_BROWSE_API_BASE", "https://api.ebay.com/buy/browse/v1").rstrip("/")
        out.append(f"{ebay_base}/item_summary/search?q={{brand}}+{{part_number}}&limit=20")

    if _env_bool("EXTERNAL_ENABLE_ROCKAUTO", default=False):
        rockauto_template = os.getenv("ROCKAUTO_CROSSREF_ENDPOINT_TEMPLATE", "").strip()
        if rockauto_template:
            out.append(rockauto_template)

    if _env_bool("EXTERNAL_ENABLE_OEM_EPC", default=False):
        raw = os.getenv("OEM_EPC_ENDPOINT_TEMPLATES", "")
        out.extend([t.strip() for t in raw.split(",") if t.strip()])

    return _dedupe_keep_order(out)


def provider_enablement_snapshot() -> Dict[str, Any]:
    ebay_token_present = bool(_get_ebay_oauth_token())
    rockauto_template_present = bool(str(os.getenv("ROCKAUTO_CROSSREF_ENDPOINT_TEMPLATE", "")).strip())
    oem_templates_raw = str(os.getenv("OEM_EPC_ENDPOINT_TEMPLATES", "")).strip()
    oem_templates_count = len([t for t in oem_templates_raw.split(",") if t.strip()]) if oem_templates_raw else 0

    return {
        "autodoc_like": {
            "enabled": len(_provider_urls_from_env()) > 0,
            "configured_endpoints": len(_provider_urls_from_env()),
        },
        "nhtsa_vpic": {
            "enabled": _env_bool("EXTERNAL_ENABLE_NHTSA", default=False),
            "configured_endpoints": 1,
            "fitment_capable": False,
        },
        "ebay_browse": {
            "enabled": _env_bool("EXTERNAL_ENABLE_EBAY", default=False),
            "token_present": ebay_token_present,
            "fitment_capable": True,
        },
        "rockauto_json": {
            "enabled": _env_bool("EXTERNAL_ENABLE_ROCKAUTO", default=False),
            "template_present": rockauto_template_present,
            "fitment_capable": True,
        },
        "oem_epc_json": {
            "enabled": _env_bool("EXTERNAL_ENABLE_OEM_EPC", default=False),
            "templates_count": oem_templates_count,
            "fitment_capable": True,
        },
    }


def provider_configuration_gaps() -> List[Dict[str, str]]:
    gaps: List[Dict[str, str]] = []

    if _env_bool("EXTERNAL_ENABLE_EBAY", default=False):
        token = _get_ebay_oauth_token()
        if not token:
            gaps.append(
                {
                    "provider": "ebay_browse",
                    "gap": "missing_ebay_oauth_token",
                    "how_to_fix": "Set EBAY_APP_ID and EBAY_CERT_ID (PRD credentials) so the token is auto-fetched via OAuth2.",
                }
            )

    if _env_bool("EXTERNAL_ENABLE_ROCKAUTO", default=False):
        tpl = str(os.getenv("ROCKAUTO_CROSSREF_ENDPOINT_TEMPLATE", "")).strip()
        if not tpl:
            gaps.append(
                {
                    "provider": "rockauto_json",
                    "gap": "missing_rockauto_endpoint_template",
                    "how_to_fix": "Set ROCKAUTO_CROSSREF_ENDPOINT_TEMPLATE with placeholders {part_number}/{brand}.",
                }
            )

    if _env_bool("EXTERNAL_ENABLE_OEM_EPC", default=False):
        templates = [t.strip() for t in str(os.getenv("OEM_EPC_ENDPOINT_TEMPLATES", "")).split(",") if t.strip()]
        if not templates:
            gaps.append(
                {
                    "provider": "oem_epc_json",
                    "gap": "missing_oem_epc_endpoint_templates",
                    "how_to_fix": "Set OEM_EPC_ENDPOINT_TEMPLATES with one or more comma-separated endpoint templates.",
                }
            )

    return gaps


def build_external_provider_attempts(part_number: str, brand: str) -> List[Dict[str, Any]]:
    normalized_part = str(part_number or "").strip()
    normalized_brand = str(brand or "").strip()
    if not normalized_part:
        return []

    attempts: List[Dict[str, Any]] = []

    def _add(
        provider: str,
        url: str,
        use_proxy: bool,
        source_kind: str,
        supports_fitment: bool,
        headers: Optional[Dict[str, str]] = None,
        skip_reason: str = "",
    ) -> None:
        attempts.append(
            {
                "provider": provider,
                "url": str(url or ""),
                "use_proxy": bool(use_proxy),
                "source_kind": source_kind,
                "supports_fitment": bool(supports_fitment),
                "headers": headers or {},
                "skip_reason": skip_reason,
            }
        )

    # Existing autodoc-like lane.
    brand_candidates: List[str] = []
    if normalized_brand:
        brand_candidates.append(normalized_brand)
        if normalized_brand.lower() == "mercedes-benz":
            brand_candidates.append("Mercedes")
    deduped_brand_candidates = _dedupe_keep_order(brand_candidates)

    params_common = {
        "partNumber": normalized_part,
        "lang": "en",
        "perPage": 20,
    }

    for base_url in _provider_urls_from_env():
        provider_slug = _provider_slug_from_url(base_url)
        for candidate in deduped_brand_candidates:
            brand_url = f"{base_url}{'&' if '?' in base_url else '?'}{urlencode({**params_common, 'brand': candidate})}"
            _add(
                provider=f"{provider_slug}_brand_no_proxy",
                url=brand_url,
                use_proxy=False,
                source_kind="autodoc_like",
                supports_fitment=True,
                headers={"Accept": "application/json"},
            )
            _add(
                provider=f"{provider_slug}_brand_proxy",
                url=brand_url,
                use_proxy=True,
                source_kind="autodoc_like",
                supports_fitment=True,
                headers={"Accept": "application/json"},
            )

        no_brand_url = f"{base_url}{'&' if '?' in base_url else '?'}{urlencode(params_common)}"
        _add(
            provider=f"{provider_slug}_no_brand_no_proxy",
            url=no_brand_url,
            use_proxy=False,
            source_kind="autodoc_like",
            supports_fitment=True,
            headers={"Accept": "application/json"},
        )
        _add(
            provider=f"{provider_slug}_no_brand_proxy",
            url=no_brand_url,
            use_proxy=True,
            source_kind="autodoc_like",
            supports_fitment=True,
            headers={"Accept": "application/json"},
        )

    # NHTSA vPIC (reference lane for model validation; not part-fitment API by part number).
    if _env_bool("EXTERNAL_ENABLE_NHTSA", default=False) and normalized_brand:
        nhtsa_base = os.getenv("NHTSA_API_BASE", "https://vpic.nhtsa.dot.gov/api").rstrip("/")
        nhtsa_url = f"{nhtsa_base}/vehicles/GetModelsForMake/{quote_plus(normalized_brand)}?format=json"
        _add(
            provider="nhtsa_vpic_models",
            url=nhtsa_url,
            use_proxy=False,
            source_kind="nhtsa_vpic",
            supports_fitment=False,
            headers={"Accept": "application/json"},
        )

    # eBay Motors / Browse API lane (requires bearer token).
    if _env_bool("EXTERNAL_ENABLE_EBAY", default=False):
        ebay_base = os.getenv("EBAY_BROWSE_API_BASE", "https://api.ebay.com/buy/browse/v1").rstrip("/")
        marketplace = os.getenv("EBAY_MARKETPLACE_ID", "EBAY_US").strip() or "EBAY_US"
        token = _get_ebay_oauth_token()
        query = f"{normalized_brand} {normalized_part}".strip()
        ebay_url = f"{ebay_base}/item_summary/search?{urlencode({'q': query, 'limit': 20})}"
        headers = {
            "Accept": "application/json",
            "X-EBAY-C-MARKETPLACE-ID": marketplace,
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
            _add(
                provider="ebay_browse_no_proxy",
                url=ebay_url,
                use_proxy=False,
                source_kind="ebay_browse",
                supports_fitment=True,
                headers=headers,
            )
        else:
            _add(
                provider="ebay_browse_no_proxy",
                url="",
                use_proxy=False,
                source_kind="ebay_browse",
                supports_fitment=True,
                headers=headers,
                skip_reason="missing_ebay_bearer_token",
            )

    # RockAuto cross-reference (endpoint template must be provided by user/contract).
    if _env_bool("EXTERNAL_ENABLE_ROCKAUTO", default=False):
        rockauto_template = os.getenv("ROCKAUTO_CROSSREF_ENDPOINT_TEMPLATE", "").strip()
        if rockauto_template:
            rockauto_url = _format_endpoint_template(rockauto_template, normalized_part, normalized_brand)
            _add(
                provider="rockauto_crossref",
                url=rockauto_url,
                use_proxy=False,
                source_kind="rockauto_json",
                supports_fitment=True,
                headers={"Accept": "application/json"},
            )
        else:
            _add(
                provider="rockauto_crossref",
                url="",
                use_proxy=False,
                source_kind="rockauto_json",
                supports_fitment=True,
                headers={"Accept": "application/json"},
                skip_reason="missing_rockauto_endpoint_template",
            )

    # OEM EPC endpoints (one or many templates, comma-separated).
    if _env_bool("EXTERNAL_ENABLE_OEM_EPC", default=False):
        raw = os.getenv("OEM_EPC_ENDPOINT_TEMPLATES", "")
        templates = [t.strip() for t in raw.split(",") if t.strip()]
        if templates:
            for idx, tpl in enumerate(templates, start=1):
                epc_url = _format_endpoint_template(tpl, normalized_part, normalized_brand)
                _add(
                    provider=f"oem_epc_{idx}",
                    url=epc_url,
                    use_proxy=False,
                    source_kind="oem_epc_json",
                    supports_fitment=True,
                    headers={"Accept": "application/json"},
                )
        else:
            _add(
                provider="oem_epc_1",
                url="",
                use_proxy=False,
                source_kind="oem_epc_json",
                supports_fitment=True,
                headers={"Accept": "application/json"},
                skip_reason="missing_oem_epc_endpoint_templates",
            )

    return attempts


def _json_by_path(data: Any, path: str) -> Any:
    node = data
    for part in (path or "").split("."):
        key = part.strip()
        if not key:
            continue
        if isinstance(node, dict):
            node = node.get(key)
        else:
            return None
    return node


def _extract_raw_items(data: Any, source_kind: str) -> List[Any]:
    if source_kind == "autodoc_like":
        if isinstance(data, dict):
            items = data.get("items") or data.get("cars") or data.get("results") or []
            return items if isinstance(items, list) else []
        return data if isinstance(data, list) else []

    if source_kind == "nhtsa_vpic":
        if isinstance(data, dict):
            items = data.get("Results") or []
            return items if isinstance(items, list) else []
        return []

    if source_kind == "ebay_browse":
        if isinstance(data, dict):
            items = data.get("itemSummaries") or []
            return items if isinstance(items, list) else []
        return []

    if source_kind == "rockauto_json":
        path = os.getenv("ROCKAUTO_ITEMS_PATH", "items")
        node = _json_by_path(data, path)
        return node if isinstance(node, list) else []

    if source_kind == "oem_epc_json":
        path = os.getenv("OEM_EPC_ITEMS_PATH", "items")
        node = _json_by_path(data, path)
        return node if isinstance(node, list) else []

    return []


def _to_int(value: Any) -> Optional[int]:
    try:
        return int(str(value).strip())
    except Exception:
        return None


def _normalize_ebay_fitment(raw: Dict[str, Any], default_brand: str) -> Optional[Dict[str, Any]]:
    props = raw.get("compatibilityProperties") or []
    if not isinstance(props, list):
        return None

    by_name: Dict[str, str] = {}
    for prop in props:
        if not isinstance(prop, dict):
            continue
        key = str(prop.get("name") or prop.get("propertyName") or "").strip().lower()
        value = str(prop.get("value") or prop.get("propertyValue") or "").strip()
        if key and value:
            by_name[key] = value

    make = by_name.get("make") or by_name.get("manufacturer") or by_name.get("brand") or default_brand
    model = by_name.get("model")
    year_value = by_name.get("year") or by_name.get("model year") or by_name.get("year from")
    year_from = _to_int(year_value)
    year_to = _to_int(by_name.get("year to")) or year_from

    if not (make and model and year_from):
        return None

    out: Dict[str, Any] = {
        "make": make,
        "model": model,
        "yearFrom": year_from,
        "yearTo": year_to,
    }
    engine = by_name.get("engine")
    if engine:
        out["engine"] = engine
    return out


def _normalize_generic_fitment(raw: Dict[str, Any], default_brand: str) -> Optional[Dict[str, Any]]:
    make = (
        raw.get("make")
        or raw.get("brand")
        or raw.get("manufacturer")
        or raw.get("Make")
        or raw.get("Brand")
        or default_brand
    )
    model = (
        raw.get("model")
        or raw.get("modelName")
        or raw.get("vehicleModel")
        or raw.get("Model")
    )

    year_from = (
        raw.get("yearFrom")
        or raw.get("year_from")
        or raw.get("from")
        or raw.get("year")
        or raw.get("YearFrom")
    )
    year_to = (
        raw.get("yearTo")
        or raw.get("year_to")
        or raw.get("to")
        or raw.get("YearTo")
        or year_from
    )
    engine = raw.get("engine") or raw.get("engineCode") or raw.get("Engine")

    if not (make and model and year_from):
        return None

    yf = _to_int(year_from)
    yt = _to_int(year_to) or yf
    if yf is None:
        return None

    out: Dict[str, Any] = {
        "make": str(make).strip(),
        "model": str(model).strip(),
        "yearFrom": yf,
        "yearTo": yt,
    }
    if engine:
        out["engine"] = str(engine).strip()
    return out


def classify_external_payload(
    response,
    *,
    source_kind: str,
    default_brand: str = "",
    supports_fitment: bool = True,
) -> Dict[str, Any]:
    if response is None:
        return {
            "payload_kind": "none",
            "items_count": 0,
            "fitment_items": [],
            "fitment_usable": False,
            "content_type": "",
        }

    content_type = str(response.headers.get("content-type") or "").lower()
    if int(response.status_code) != 200:
        return {
            "payload_kind": "non_200",
            "items_count": 0,
            "fitment_items": [],
            "fitment_usable": False,
            "content_type": content_type,
        }

    try:
        data = response.json()
    except Exception:
        return {
            "payload_kind": "html" if "text/html" in content_type else "non_json",
            "items_count": 0,
            "fitment_items": [],
            "fitment_usable": False,
            "content_type": content_type,
        }

    raw_items = _extract_raw_items(data, source_kind=source_kind)

    fitment_items: List[Dict[str, Any]] = []
    if source_kind == "ebay_browse":
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            normalized = _normalize_ebay_fitment(item, default_brand=default_brand)
            if normalized:
                fitment_items.append(normalized)
    elif source_kind != "nhtsa_vpic":
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            normalized = _normalize_generic_fitment(item, default_brand=default_brand)
            if normalized:
                fitment_items.append(normalized)

    payload_kind = "json_reference" if source_kind == "nhtsa_vpic" else "json"
    fitment_usable = bool(supports_fitment and source_kind in _FITMENT_CAPABLE_KINDS and payload_kind == "json")

    return {
        "payload_kind": payload_kind,
        "items_count": len(raw_items),
        "fitment_items": fitment_items,
        "fitment_usable": fitment_usable,
        "content_type": content_type,
    }
