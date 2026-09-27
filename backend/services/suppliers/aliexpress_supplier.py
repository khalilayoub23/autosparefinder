import base64
import hashlib
import hmac
import json
import logging
import os
import time
from typing import Any, Optional

import httpx

from resilience import retry_with_backoff
from services.suppliers.base_supplier import BaseSupplier, PartResult

logger = logging.getLogger(__name__)


class AliExpressSearchUnconfirmed(Exception):
    """The keyword search got NO answer from AliExpress — a transport failure, an HTTP error, flow control, an
    auth problem or an API error — as opposed to an ANSWER that happens to contain nothing.

    Root cause it exists for (2026-09-26 validation series): `text_search` swallowed every failure and returned
    `[]`, which `search_by_oem` and the price sync could not tell apart from "AliExpress answered: nothing
    found", so 3 transport timeouts were recorded as a confirmed `no_match` (30-day negative cache) without a
    real answer. Callers that persist negative results MUST NOT record this as a no-match.

    kind: transport | http_status | rate_limit (transient: may succeed on a later attempt)
          auth | api_error                     (not transient: retrying the same call cannot help)
    `detail` carries only an exception type / HTTP status / AliExpress error code+message — never credentials."""
    TRANSIENT_KINDS = frozenset({"transport", "http_status", "rate_limit"})

    def __init__(self, kind: str, detail: str = "") -> None:
        super().__init__(f"{kind}: {detail}" if detail else kind)
        self.kind = kind
        self.detail = detail

    @property
    def transient(self) -> bool:
        return self.kind in self.TRANSIENT_KINDS


# AliExpress error codes that mean "credentials/authorization problem" (observed live: IllegalAccessToken on
# aliexpress.ds.text.search, IllegalRefreshToken on /auth/token/refresh). Matched on unambiguous credential words
# only, so an unrelated business code can never be misread as an auth failure.
_AUTH_CODE_MARKERS = ("accesstoken", "refreshtoken", "signature", "appkey", "unauthorized")


def _error_kind(code: str) -> str:
    """Classify an AliExpress `error_response.code` for the search path (see AliExpressSearchUnconfirmed)."""
    if code == "ApiCallLimit":
        return "rate_limit"
    if any(m in (code or "").lower() for m in _AUTH_CODE_MARKERS):
        return "auth"
    return "api_error"

# AliExpress DS (Dropshipping) API — ONE OAuth architecture: the current IOP / Open Platform flow.
#   authorize : https://api-sg.aliexpress.com/oauth/authorize          (owner consent, once)
#   callback  : https://autosparefinder.co.il/api/aliexpress/callback   (routes/suppliers.py, GET+POST)
#   exchange  : POST {IOP}/rest/auth/token/create   (persist_tokens -> Fernet-encrypted suppliers.credentials)
#   refresh   : POST {IOP}/rest/auth/token/refresh  (_auto_refresh_token, same encrypted store)
#   API       : POST {IOP}/sync                     (aliexpress.ds.* methods, access_token required)
# App 546482 is known only to IOP (legacy oauth.aliexpress.com/token -> param-appkey.not.exists), so no
# legacy oauth.aliexpress.com endpoint may be used anywhere in the auth path.
_IOP_BASE = "https://api-sg.aliexpress.com"
ALIEXPRESS_API_URL = os.getenv("ALIEXPRESS_DS_API_URL", f"{_IOP_BASE}/sync")
ALIEXPRESS_TOKEN_URL = f"{_IOP_BASE}/rest/auth/token/create"
ALIEXPRESS_REFRESH_URL = f"{_IOP_BASE}/rest/auth/token/refresh"
ALIEXPRESS_AUTH_URL = f"{_IOP_BASE}/oauth/authorize"
# Must equal the callback registered in the AliExpress App Console for the app.
ALIEXPRESS_CALLBACK_URL = "https://autosparefinder.co.il/api/aliexpress/callback"

OE_BRANDS = {
    "bosch", "denso", "valeo", "ngk", "gates", "skf", "fag",
    "luk", "sachs", "monroe", "brembo", "ate", "hella", "mahle",
    "mann", "febi", "meyle", "trw", "delphi", "continental",
    "kayaba", "gabriel", "moog", "corteco", "elring", "victor reinz",
}

OEM_KEYWORDS = {"genuine", "original", "oem", "factory", "מקורי", "מקור"}


def classify_part_origin(title: str) -> str:
    t = _safe_text(title).lower()
    if any(k in t for k in OEM_KEYWORDS):
        return "original"
    if any(b in t for b in OE_BRANDS):
        return "oe_equivalent"
    return "aftermarket"



# --- Persistent OAuth token store -------------------------------------------
# Tokens live in the EXISTING `suppliers.credentials` JSONB of the AliExpress
# supplier row (key below), Fernet-encrypted with the already-provisioned
# ENCRYPTION_KEY. The container has no writable .env, so env vars are only the
# bootstrap fallback; the DB copy (which survives restarts) wins when present.
_SUPPLIER_ROW_NAME = "AliExpress"
_CRED_KEY = "aliexpress_oauth"


def _fernet():
    from cryptography.fernet import Fernet
    key = os.getenv("ENCRYPTION_KEY", "")
    if not key:
        raise RuntimeError("ENCRYPTION_KEY not set — refusing to store AliExpress tokens unencrypted")
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(key.encode("utf-8")).digest()))


def _sign(params: dict[str, Any], app_secret: str) -> str:
    """AliExpress TOP HMAC-SHA256: key=app_secret, msg=sorted_key+value pairs."""
    msg = "".join(f"{k}{v}" for k, v in sorted(params.items()))
    return hmac.new(
        app_secret.encode("utf-8"),
        msg.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest().upper()


def _sign_rest(path: str, params: dict[str, Any], app_secret: str) -> str:
    """IOP REST signing (auth/token/*): HMAC-SHA256(key=secret, msg=path + sorted key+value pairs)."""
    msg = path + "".join(f"{k}{v}" for k, v in sorted(params.items()))
    return hmac.new(app_secret.encode("utf-8"), msg.encode("utf-8"), hashlib.sha256).hexdigest().upper()


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


class AliExpressSupplier(BaseSupplier):
    name = "aliexpress"

    def __init__(self) -> None:
        self.api_calls = 0          # real /sync calls made by THIS instance (targeting yield accounting)
        # AliExpress's own gateway-level flow-control rejection (code "ApiCallLimit", e.g. "Api access
        # frequency exceeds the limit. this ban will last 1 seconds" — observed real 2026-09-22). Counted
        # SEPARATELY from api_calls: a call that gets this response was made (charged against whatever
        # quota exists) but never succeeded, so it must never be read as a successful call. Distinct from
        # ordinary business "errors" (ITEM_ID_NOT_FOUND etc.) and from client-side exceptions — see
        # _classify_api_error().
        self.api_call_limit_hits = 0
        self._app_key = os.getenv("ALIEXPRESS_APP_KEY", "")
        self._app_secret = os.getenv("ALIEXPRESS_APP_SECRET", "")
        self._access_token = os.getenv("ALIEXPRESS_ACCESS_TOKEN", "")
        self._refresh_token = os.getenv("ALIEXPRESS_REFRESH_TOKEN", "")
        # expire_time is Unix ms from AliExpress; refresh when within 3 days of expiry
        self._token_expire = int(os.getenv("ALIEXPRESS_TOKEN_EXPIRE", "0"))

    def _credentials_ok(self) -> bool:
        return bool(self._app_key and self._app_secret and self._access_token)

    def _classify_api_error(self, api_name: str, err: dict) -> None:
        """Single point of truth for classifying an AliExpress `error_response` envelope (2026-09-24
        observability fix — root cause: every call site logged this generically, so a real gateway-level
        ApiCallLimit rejection was indistinguishable from any other business error and invisible to a
        run's report). Does NOT change caller behavior: the caller still returns its own empty/None value,
        still falls back to whatever data it already has, still never retries — this only makes the
        ApiCallLimit case observable via a dedicated counter and a clearly-labeled log line."""
        err = err or {}
        code, msg = err.get("code", ""), err.get("msg", "")
        if code == "ApiCallLimit":
            self.api_call_limit_hits += 1
            logger.warning("AliExpress ApiCallLimit hit: api=%s code=%s message=%s count=%d",
                           api_name, code, msg, self.api_call_limit_hits)
        else:
            logger.warning("AliExpress %s error: code=%s message=%s", api_name, code, msg)

    def _token_needs_refresh(self) -> bool:
        """True if access_token expires within 3 days."""
        if not self._token_expire:
            return False
        three_days_ms = 3 * 24 * 3600 * 1000
        return (self._token_expire - int(time.time() * 1000)) < three_days_ms

    async def persist_tokens(self, access_token: str, refresh_token: str, expire_time: Any) -> None:
        """Encrypt + store tokens on the AliExpress supplier row. Raises on failure (never silent)."""
        import sqlalchemy as sa
        from BACKEND_DATABASE_MODELS import async_session_factory
        blob = _fernet().encrypt(json.dumps(
            {"access_token": access_token, "refresh_token": refresh_token}).encode()).decode()
        meta = {"enc": blob, "expire_time": int(expire_time or 0), "obtained_at": int(time.time())}
        async with async_session_factory() as db:
            res = await db.execute(sa.text(
                "UPDATE suppliers SET credentials = coalesce(credentials,'{}'::jsonb) || CAST(:m AS jsonb) "
                "WHERE name = :n"), {"m": json.dumps({_CRED_KEY: meta}), "n": _SUPPLIER_ROW_NAME})
            if res.rowcount != 1:
                await db.rollback()
                raise RuntimeError("AliExpress supplier row not found — tokens NOT persisted")
            await db.commit()

    async def _load_persisted(self) -> bool:
        """Load tokens from the supplier row into memory. Returns True if applied."""
        import sqlalchemy as sa
        from BACKEND_DATABASE_MODELS import async_session_factory
        try:
            async with async_session_factory() as db:
                row = (await db.execute(sa.text(
                    "SELECT credentials -> :k FROM suppliers WHERE name = :n"),
                    {"k": _CRED_KEY, "n": _SUPPLIER_ROW_NAME})).fetchone()
            meta = row[0] if row else None
            if not meta or not meta.get("enc"):
                return False
            tok = json.loads(_fernet().decrypt(meta["enc"].encode()).decode())
        except Exception as exc:
            logger.warning("AliExpress: persisted token load failed (%s)", type(exc).__name__)
            return False
        self._access_token = tok.get("access_token", "") or self._access_token
        self._refresh_token = tok.get("refresh_token", "") or self._refresh_token
        self._token_expire = int(meta.get("expire_time") or 0)
        return True

    def token_status(self) -> dict:
        """Masked, non-secret status for display/diagnostics."""
        return {"has_access_token": bool(self._access_token),
                "has_refresh_token": bool(self._refresh_token),
                "expire_time": self._token_expire}

    # Refresh-failure cooldown (class-level: the price-sync holds a long-lived singleton while search
    # builds new instances). A dead refresh token used to be retried on EVERY request.
    _refresh_fail_digest: str = ""
    _refresh_fail_until: float = 0.0
    _REFRESH_COOLDOWN_S = 900

    @staticmethod
    def _digest(value: str) -> str:
        return hashlib.sha256((value or "").encode("utf-8")).hexdigest()[:16]

    async def _auto_refresh_token(self) -> bool:
        """Refresh via IOP /auth/token/refresh and persist the result to the encrypted supplier-row store.
        Returns True on success. A failing refresh token is not retried for a cooldown period; a newly
        authorized (different) refresh token is tried immediately."""
        if not self._refresh_token:
            logger.warning("AliExpress: no refresh_token available — re-authorization needed")
            return False
        cls = type(self)
        if (self._digest(self._refresh_token) == cls._refresh_fail_digest
                and time.time() < cls._refresh_fail_until):
            return False
        path = "/auth/token/refresh"
        params = {
            "app_key": self._app_key,
            "refresh_token": self._refresh_token,
            "timestamp": str(int(time.time() * 1000)),
            "sign_method": "sha256",
        }
        params["sign"] = _sign_rest(path, params, self._app_secret)
        data: dict = {}
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(ALIEXPRESS_REFRESH_URL, data=params)
                data = resp.json()
        except Exception as exc:
            logger.error("AliExpress token refresh request failed: %s", type(exc).__name__)

        new_token = data.get("access_token", "") if isinstance(data, dict) else ""
        if not new_token:
            cls._refresh_fail_digest = self._digest(self._refresh_token)
            cls._refresh_fail_until = time.time() + self._REFRESH_COOLDOWN_S
            # log only AliExpress's error code/message — never the payload (may contain tokens on odd replies)
            logger.error("AliExpress token refresh failed: code=%s message=%s (retry in %ss)",
                         (data or {}).get("code") if isinstance(data, dict) else None,
                         str((data or {}).get("message", ""))[:120] if isinstance(data, dict) else "",
                         self._REFRESH_COOLDOWN_S)
            return False

        new_refresh = data.get("refresh_token", "")
        new_expire = data.get("expire_time", 0)
        self._access_token = new_token
        if new_refresh:
            self._refresh_token = new_refresh
        if new_expire:
            self._token_expire = int(new_expire)
        cls._refresh_fail_digest, cls._refresh_fail_until = "", 0.0

        # Persist (encrypted) to the supplier row so it survives restarts
        try:
            await self.persist_tokens(self._access_token, self._refresh_token, self._token_expire)
            logger.info("AliExpress token auto-refreshed and persisted")
        except Exception as exc:
            logger.warning("AliExpress: token refreshed in memory but persistence failed (%s)", type(exc).__name__)

        return True

    def get_oauth_url(self, redirect_uri: str = ALIEXPRESS_CALLBACK_URL) -> str:
        """IOP authorization URL the store owner opens once to authorize the DS app.
        Only response_type/client_id/redirect_uri (documented, required). No legacy `sp`/`view`;
        `force_auth` is optional in the IOP docs and not needed."""
        from urllib.parse import urlencode
        return f"{ALIEXPRESS_AUTH_URL}?" + urlencode(
            {"response_type": "code", "client_id": self._app_key, "redirect_uri": redirect_uri})

    async def exchange_code_for_token(self, code: str) -> dict:
        """Exchange the OAuth code via IOP POST /rest/auth/token/create.
        Returns dict with access_token, refresh_token, expire_time."""
        path = "/auth/token/create"
        params = {
            "app_key": self._app_key,
            "code": code,
            "timestamp": str(int(time.time() * 1000)),
            "sign_method": "sha256",
        }
        params["sign"] = _sign_rest(path, params, self._app_secret)
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(ALIEXPRESS_TOKEN_URL, data=params)
            resp.raise_for_status()
            data = resp.json()
        if data.get("code") not in (None, "0", 0) or data.get("type") == "ISV":
            # AliExpress's error envelope (code/type/message) — contains no credentials
            raise ValueError(f"Token exchange failed: code={data.get('code')} message={str(data.get('message', ''))[:160]}")
        return data

    def _build_request(self, method: str, extra: dict[str, Any], with_token: bool = True) -> dict[str, Any]:
        params: dict[str, Any] = {
            "method": method,
            "app_key": self._app_key,
            "timestamp": str(int(time.time() * 1000)),
            "sign_method": "sha256",
            "format": "json",
            "v": "2.0",
            **extra,
        }
        if with_token and self._access_token:
            params["access_token"] = self._access_token
        params["sign"] = _sign(params, self._app_secret)
        return params

    async def _ensure_token(self) -> bool:
        """Refresh token proactively if expiring within 3 days. Returns True if ready."""
        await self._load_persisted()
        if self._token_needs_refresh():
            logger.info("AliExpress access_token expiring soon — auto-refreshing")
            await self._auto_refresh_token()
        return self._credentials_ok()

    @staticmethod
    def _parse_rating(raw: Any) -> Optional[float]:
        txt = _safe_text(raw)
        if not txt:
            return None
        try:
            return float(txt.replace("%", ""))
        except Exception:
            return None

    async def search(self, query: str, limit: int = 10) -> list[PartResult]:
        if not await self._ensure_token():
            logger.warning(
                "AliExpress DS API not ready. Ensure ALIEXPRESS_APP_KEY, ALIEXPRESS_APP_SECRET, "
                "and ALIEXPRESS_ACCESS_TOKEN are set. "
                "Authorize via: AliExpressSupplier().get_oauth_url() (IOP flow)"
            )
            return []

        # DS wholesale.get requires a purely numeric AliExpress product_id (10-18 digits).
        # OEM strings (e.g. "NI277609HM0A") will always return MissingParameter — skip them.
        _q = query.strip()
        if not _q.isdigit() or not (10 <= len(_q) <= 18):
            logger.debug("AliExpress DS: skipping non-numeric query '%s' (not a product_id)", _q)
            return []

        params = self._build_request(
            "aliexpress.ds.product.wholesale.get",
            {
                "product_id": _q,
                "ship_to_country": "IL",
                "target_currency": "USD",
                "target_language": "EN",
            },
        )

        try:
            self.api_calls += 1
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(ALIEXPRESS_API_URL, data=params)
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            logger.error("AliExpress DS search failed for '%s': %s", _q, exc)
            return []

        err = data.get("error_response", {})
        if err:
            err_code = err.get("code", "")
            # ITEM_ID_NOT_FOUND is expected when an AliExpress ID is no longer listed
            if err_code in ("15", "27"):
                logger.debug("AliExpress DS: product not found for id '%s'", _q)
            else:
                self._classify_api_error("aliexpress.ds.product.wholesale.get", err)
            return []

        body = (
            data.get("aliexpress_ds_product_wholesale_get_response", {})
            .get("result", {})
        )
        if not body:
            return []

        import re as _re
        def _p(v):
            t = _safe_text(v)
            m = _re.search(r"[\d]+\.[\d]+|[\d]+", t.replace(",", ""))
            return float(m.group()) if m else 0.0

        results: list[PartResult] = []
        try:
            price = _p(body.get("activity_price")) or _p(body.get("sale_price")) or _p(body.get("sku_price_list"))
            if price <= 0:
                return []
            title = str(body.get("subject") or "")
            main_image = _safe_text(body.get("image_url"))
            origin = classify_part_origin(title)
            results.append(PartResult(
                supplier=self.name,
                item_id=str(body.get("product_id") or query),
                title=title,
                price=price,
                currency="USD",
                shipping_cost=0.0,
                total_cost=price,
                condition="New",
                seller=str(body.get("store_id") or ""),
                seller_rating=None,
                item_url=f"https://www.aliexpress.com/item/{query}.html",
                image_url=main_image or None,
                location="CN",
                estimated_delivery_days=20,
                ships_to_israel=True,
                image_urls=[main_image] if main_image else None,
                tech_specs={"part_origin": origin},
                warranty_text=None,
                warranty_months=None,
            ))
        except Exception as exc:
            logger.error("AliExpress DS map item error: %s", exc)

        logger.info("AliExpress DS query '%s' returned %d results", query, len(results))
        return results

    async def search_by_oem(self, oem_number: str, limit: int = 10, *, brand: str = "", name: str = "",
                            raise_on_unconfirmed: bool = False) -> list[PartResult]:
        """OEM lookup: ALWAYS a keyword search accepted only through the strict OEM-in-title guard.
        (A purely numeric OEM — BMW 11 digits, Toyota/Kia 10 — is NOT an AliExpress product id. Routing
        10-18 digit numerics to the product-id fetch skipped the keyword search for every such OEM and just
        returned "not found". Product ids are fetched explicitly via search()/get_part_details().)
        AliExpress's DS "selection" feed is fuzzy/category-based, so without the guard we would attach a
        WRONG part's price — forbidden by the fitment-first rule. Few but CORRECT matches beats many wrong."""
        return await self._oem_guarded_text_search(_safe_text(oem_number), limit, brand=_safe_text(brand), name=_safe_text(name),
                                                   raise_on_unconfirmed=raise_on_unconfirmed)

    @staticmethod
    def _norm_oem(s: str) -> str:
        import re as _re
        return _re.sub(r"[^0-9A-Z]", "", _safe_text(s).upper())

    async def _post_text_search_once(self, params: dict) -> Any:
        """ONE HTTP round-trip of aliexpress.ds.text.search. Every attempt is a real API call and is counted in
        api_calls."""
        self.api_calls += 1
        async with httpx.AsyncClient(timeout=25.0) as client:
            resp = await client.post(ALIEXPRESS_API_URL, data=params)
            resp.raise_for_status()
            return resp.json()

    @retry_with_backoff(max_retries=1, base_delay=1.0, max_delay=5.0, retry_on=(429, 503, 504), skip_on=(401, 403, 404), jitter=True)
    async def _post_text_search_retrying(self, params: dict) -> Any:
        """The same round-trip wrapped by the project's existing resilience helper (resilience.retry_with_backoff —
        the CLAUDE.md rule for external calls): at most ONE retry (2 attempts) with exponential backoff + jitter on
        transport errors and 429/503/504, fail-fast on 401/403/404. Used ONLY by the price sync
        (raise_on_unconfirmed=True), where completeness matters more than latency; the customer-facing search and
        discovery keep their single attempt, so their worst-case latency is unchanged. ApiCallLimit arrives as an
        HTTP-200 envelope, so it is never retried here (a flow-control response is not hammered)."""
        return await self._post_text_search_once(params)

    async def text_search(self, keyword: str, limit: int = 20, page: int = 1, *, raise_on_unconfirmed: bool = False) -> list[dict]:
        """Raw DS keyword search (aliexpress.ds.text.search). Returns the curated
        'selection' product list. Fuzzy — callers must apply their own guard.

        Default contract (customer search, discovery): NEVER raises — any failure returns []. With
        raise_on_unconfirmed=True (the price sync, which PERSISTS negative results) a search that got no answer raises
        AliExpressSearchUnconfirmed(kind) instead, so "no answer" can never be mistaken for "answered: nothing found".
        A successful response with an empty selection is a real answer and returns [] either way."""
        def _unconfirmed(kind: str, detail: str) -> list:
            if raise_on_unconfirmed:
                raise AliExpressSearchUnconfirmed(kind, detail)
            return []

        if not await self._ensure_token():
            return _unconfirmed("auth", "no valid AliExpress credentials")
        params = self._build_request(
            "aliexpress.ds.text.search",
            {"keyWord": keyword, "countryCode": "IL", "currency": "USD",
             "local": "en_US", "pageSize": str(min(50, max(1, limit))), "pageIndex": str(max(1, int(page)))},
        )
        try:
            data = await (self._post_text_search_retrying(params) if raise_on_unconfirmed else self._post_text_search_once(params))
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            logger.error("AliExpress ds.text.search failed for '%s': HTTP %s", keyword, status)
            return _unconfirmed("auth" if status in (401, 403) else "http_status", f"HTTP {status}")
        except Exception as exc:
            # type name matters: a timeout's str() is empty, which made the original log line undiagnosable
            logger.error("AliExpress ds.text.search failed for '%s': %s %s", keyword, type(exc).__name__, exc)
            return _unconfirmed("api_error" if isinstance(exc, ValueError) else "transport", type(exc).__name__)
        if not isinstance(data, dict):
            logger.error("AliExpress ds.text.search returned a non-object body for '%s'", keyword)
            return _unconfirmed("api_error", "malformed response")
        if data.get("error_response"):
            err = data["error_response"] if isinstance(data["error_response"], dict) else {"code": "", "msg": str(data["error_response"])[:120]}
            self._classify_api_error("aliexpress.ds.text.search", err)
            return _unconfirmed(_error_kind(err.get("code", "")), f"{err.get('code', '')}: {str(err.get('msg', ''))[:120]}")
        return (
            data.get("aliexpress_ds_text_search_response", {})
            .get("data", {})
            .get("products", {})
            .get("selection_search_product", [])
        ) or []

    @staticmethod
    def _oem_matches_title(oem: str, title: str) -> bool:
        """Bulletproof fitment guard: the OEM must appear in the title as a bounded TOKEN, never merely as a
        substring (a plain substring test would attach a WRONG part's price, e.g. '12345' inside '123456').
        Requires len >= 8 (short codes collide too easily). Hyphens carry no information in part numbers, so
        the match is hyphen-INSENSITIVE ('15400PLMA01' == '15400-PLM-A01' == '15400-PLMA01'), but the token
        must stand alone: it may not be glued to more letters/digits on either side, directly or through a
        hyphen ('AB-12345678' is a DIFFERENT number than '12345678'). Fuzzy AliExpress titles rarely pass
        this — by design: for a fuzzy source, correctness beats recall."""
        import re as _re
        norm = AliExpressSupplier._norm_oem(oem)
        if len(norm) < 8:
            return False
        # Junk "OEM" values in the catalog (model-year ranges like "2007-2008") normalise to 8 chars and would
        # match the year range in ANY listing title (e.g. a motorcycle brake pad) — never a part number.
        if _re.fullmatch(r"(19|20)\d{2}\s*[-/]\s*(19|20)\d{2}", oem.strip()):
            return False
        t = (title or "").upper()
        left = r"(?<![0-9A-Z])(?<![0-9A-Z]-)"
        right = r"(?![0-9A-Z])(?!-[0-9A-Z])"
        if _re.search(left + "-?".join(_re.escape(c) for c in norm) + right, t):
            return True
        # catalog written with spaces/other separators ("11 42 8 507 694") or dashes spelled as spaces
        for v in {oem.upper().strip(), oem.upper().replace("-", " ").strip()}:
            if len(v) >= 8 and " " in v and _re.search(left + _re.escape(v) + right, t):
                return True
        return False

    @staticmethod
    def _build_oem_query(oem: str, brand: str = "", name: str = "") -> str:
        """Retrieval query = "<brand> <part name> <OEM>". AliExpress's keyword search gives a bare part
        number only fuzzy, mostly unrelated results. Measured 2026-09-21 on 30 OEMs known to be cited in real
        listing titles (strict guard throughout): bare OEM 1/30, brand+OEM 12/30, brand+name+OEM 17/30.
        Brand and name are RETRIEVAL CONTEXT ONLY — acceptance is decided exclusively by _oem_matches_title
        (strict OEM-token guard), never by brand/name/similarity. Only the NAME is ever truncated (the OEM
        must reach the API intact) and it is reduced to plain words (catalog names carry junk punctuation)."""
        import re as _re
        clean = _re.sub(r"[^0-9A-Za-z ]+", " ", _safe_text(name))
        clean = _re.sub(r"\s+", " ", clean).strip()[:60].rstrip()
        return " ".join(x for x in (_safe_text(brand), clean, _safe_text(oem)) if x)

    _MULTI_UNIT_RX = None

    @staticmethod
    def _multi_unit_reason(title: str) -> Optional[str]:
        """Why a listing's price is NOT a single-unit price (None = looks like one unit). The OEM guard proves
        a listing cites the OEM; it does not prove the price is for ONE piece. Real examples seen 2026-09-21:
        'Three (3) Oil Filter 04152-YZZA6', '5X Engine Oil Filter …', '20pcs/lot …', '1/2/3/5Pcs …'.
        Recording such a price as the part's unit cost would overprice it by the pack size, so these listings
        are rejected (correctness beats recall). Rejection-only: it can never make a listing acceptable."""
        import re as _re
        rx = AliExpressSupplier._MULTI_UNIT_RX
        if rx is None:
            unit = r"(?:pcs?|pce|pieces?|units?|sets?|packs?|pairs?|lots?)"
            rx = AliExpressSupplier._MULTI_UNIT_RX = [
                ("qty_options", _re.compile(r"\b\d+(?:\s*/\s*\d+)+\s*" + unit + r"\b", _re.I)),          # 1/2/3/5Pcs
                ("qty_units",   _re.compile(r"\b(\d{1,4})\s*-?\s*" + unit + r"\b", _re.I)),                # 3 pcs, 6Sets, 20pcs/lot
                ("qty_times",   _re.compile(r"\b(\d{1,3})\s*x\b(?!\s*\d)", _re.I)),                       # 5X  (not '12 x 1.5' specs)
                ("qty_paren",   _re.compile(r"\((\d{1,3})\)")),                                              # Three (3)
                ("bulk_words",  _re.compile(r"\b(?:lot|bulk|wholesale)\b", _re.I)),
            ]
        t = title or ""
        for name, r in rx:
            for m in r.finditer(t):
                if name in ("qty_options", "bulk_words"):
                    return name
                if int(m.group(1)) > 1:
                    return name
        return None

    async def _oem_guarded_text_search(self, oem: str, limit: int, brand: str = "", name: str = "",
                                       raise_on_unconfirmed: bool = False) -> list[PartResult]:
        norm = self._norm_oem(oem)
        if len(norm) < 8:  # short codes collide too easily — never guess (a LOCAL decision, no API call: a real negative)
            return []
        kw = {"raise_on_unconfirmed": True} if raise_on_unconfirmed else {}
        prods = await self.text_search(self._build_oem_query(oem, brand, name), limit=30, **kw)
        import re as _re
        def _p(v):
            m = _re.search(r"[\d]+\.[\d]+|[\d]+", _safe_text(v).replace(",", ""))
            return float(m.group()) if m else 0.0
        results: list[PartResult] = []
        for p in prods:
            title = _safe_text(p.get("title"))
            if not self._oem_matches_title(oem, title):   # bounded-token guard
                continue
            if self._multi_unit_reason(title):            # price is not a single-unit price
                continue
            price = _p(p.get("targetSalePrice")) or _p(p.get("salePrice")) or _p(p.get("originalPrice"))
            if price <= 0:
                continue
            item_id = _safe_text(p.get("itemId"))
            results.append(PartResult(
                supplier=self.name, item_id=item_id, title=title, price=price,
                currency="USD", shipping_cost=0.0, total_cost=price, condition="New",
                seller="", seller_rating=None,
                item_url=f"https://www.aliexpress.com/item/{item_id}.html",
                image_url=_safe_text(p.get("itemMainPic")) or None, location="CN",
                estimated_delivery_days=20, ships_to_israel=True,
                image_urls=[_safe_text(p.get("itemMainPic"))] if p.get("itemMainPic") else None,
                tech_specs={"part_origin": classify_part_origin(title), "oem_verified": True},
                warranty_text=None, warranty_months=None,
            ))
        return results[:limit]

    async def get_part_details(self, item_id: str) -> Optional[PartResult]:
        if not await self._ensure_token():
            logger.error("AliExpress credentials missing")
            return None

        params = self._build_request(
            "aliexpress.ds.product.get",
            {
                "product_id": int(item_id),
                "ship_to_country": "IL",
                "target_currency": "USD",
                "target_language": "EN",
            },
        )

        try:
            self.api_calls += 1
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(ALIEXPRESS_API_URL, data=params)
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            logger.error("AliExpress DS get_part_details failed for '%s': %s", item_id, exc)
            return None

        if data.get("error_response"):
            self._classify_api_error("aliexpress.ds.product.get", data["error_response"])
            return None

        item = (
            data.get("aliexpress_ds_product_get_response", {})
            .get("result", {})
        )
        if not item:
            return None

        try:
            import re as _re
            def _p(v):
                t = _safe_text(v)
                m = _re.search(r"[\d]+\.[\d]+|[\d]+", t.replace(",", ""))
                return float(m.group()) if m else 0.0

            # DS product.get returns aeop_ae_product_skus for pricing
            skus = item.get("aeop_ae_product_skus", {}).get("aeop_ae_sku", []) or []
            price = 0.0
            for sku in skus:
                offer = sku.get("aeop_sku_latest_price_module", {}) or {}
                p = _p(offer.get("activity_amount")) or _p(offer.get("sale_amount"))
                if p > 0 and (price == 0 or p < price):
                    price = p
            if price <= 0:
                price = _p(item.get("aeop_ae_product_display_dto", {}).get("sale_price"))
            if price <= 0:
                return None

            image_urls: list[str] = []
            for img_url in str(item.get("image_u_r_ls") or "").split(";"):
                img_url = img_url.strip()
                if img_url:
                    image_urls.append(img_url)
            image_urls = image_urls[:8]

            title = str(item.get("aeop_ae_product_display_dto", {}).get("product_title") or item_id)
            origin = classify_part_origin(title)
            return PartResult(
                supplier=self.name,
                item_id=str(item.get("product_id") or item_id),
                title=title,
                price=price,
                currency="USD",
                shipping_cost=0.0,
                total_cost=price,
                condition="New",
                seller=str(item.get("store_id") or ""),
                seller_rating=None,
                item_url=f"https://www.aliexpress.com/item/{item_id}.html",
                image_url=image_urls[0] if image_urls else None,
                location="CN",
                estimated_delivery_days=20,
                ships_to_israel=True,
                image_urls=image_urls or None,
                tech_specs={"part_origin": origin},
                warranty_text=None,
                warranty_months=None,
            )
        except Exception as exc:
            logger.error("AliExpress DS map details error: %s", exc)
            return None


        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(ALIEXPRESS_API_URL, data=params)
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            logger.error("AliExpress get_part_details failed for '%s': %s", item_id, exc)
            return None

        if data.get("error_response"):
            logger.warning("AliExpress get_part_details error: %s", data["error_response"])
            return None

        products = (
            data.get("aliexpress_affiliate_productdetail_get_response", {})
            .get("resp_result", {})
            .get("result", {})
            .get("products", {})
            .get("product", [])
        )
        item = products[0] if products else {}
        if not item:
            return None

        try:
            import re as _re
            def _p(v):
                t = _safe_text(v)
                m = _re.search(r"[\d]+\.[\d]+|[\d]+", t.replace(",", ""))
                return float(m.group()) if m else 0.0

            price = _p(item.get("target_sale_price")) or _p(item.get("sale_price")) or _p(item.get("original_price"))
            if price <= 0:
                return None

            image_urls: list[str] = []
            main_image = _safe_text(item.get("product_main_image_url"))
            if main_image:
                image_urls.append(main_image)
            extra_imgs = item.get("product_small_image_urls") or {}
            if isinstance(extra_imgs, dict):
                extra_imgs = extra_imgs.get("string", [])
            image_urls.extend([str(u) for u in extra_imgs if _safe_text(u)])
            image_urls = list(dict.fromkeys(image_urls))

            title = str(item.get("product_title") or "")
            origin = classify_part_origin(title)
            return PartResult(
                supplier=self.name,
                item_id=str(item.get("product_id") or item_id),
                title=title,
                price=price,
                currency="USD",
                shipping_cost=0.0,
                total_cost=price,
                condition="New",
                seller=str(item.get("store_id") or ""),
                seller_rating=self._parse_rating(item.get("evaluate_rate")),
                item_url=str(item.get("product_detail_url") or f"https://www.aliexpress.com/item/{item_id}.html"),
                image_url=image_urls[0] if image_urls else None,
                location="CN",
                estimated_delivery_days=20,
                ships_to_israel=True,
                image_urls=image_urls or None,
                tech_specs={"part_origin": origin},
                warranty_text=None,
                warranty_months=None,
            )
        except Exception as exc:
            logger.error("AliExpress DS map details error: %s", exc)
            return None
