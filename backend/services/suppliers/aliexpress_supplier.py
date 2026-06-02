import hashlib
import hmac
import logging
import os
import time
from typing import Any, Optional

import httpx

from services.suppliers.base_supplier import BaseSupplier, PartResult

logger = logging.getLogger(__name__)

# AliExpress DS (Dropshipping) API
# App is registered as Dropshipping Individual — DS methods require access_token.
# One-time OAuth: visit the URL printed by get_oauth_url() and paste the code to
# generate a token via POST https://api-sg.aliexpress.com/rest/auth/token/create
# Store the resulting access_token in ALIEXPRESS_ACCESS_TOKEN env var.
ALIEXPRESS_API_URL = os.getenv("ALIEXPRESS_DS_API_URL", "https://api-sg.aliexpress.com/sync")
ALIEXPRESS_TOKEN_URL = "https://api-sg.aliexpress.com/rest/auth/token/create"
ALIEXPRESS_AUTH_URL = "https://oauth.aliexpress.com/authorize"

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



def _sign(params: dict[str, Any], app_secret: str) -> str:
    """AliExpress TOP HMAC-SHA256: key=app_secret, msg=sorted_key+value pairs."""
    msg = "".join(f"{k}{v}" for k, v in sorted(params.items()))
    return hmac.new(
        app_secret.encode("utf-8"),
        msg.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest().upper()


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


class AliExpressSupplier(BaseSupplier):
    name = "aliexpress"

    def __init__(self) -> None:
        self._app_key = os.getenv("ALIEXPRESS_APP_KEY", "")
        self._app_secret = os.getenv("ALIEXPRESS_APP_SECRET", "")
        self._access_token = os.getenv("ALIEXPRESS_ACCESS_TOKEN", "")
        self._refresh_token = os.getenv("ALIEXPRESS_REFRESH_TOKEN", "")
        # expire_time is Unix ms from AliExpress; refresh when within 3 days of expiry
        self._token_expire = int(os.getenv("ALIEXPRESS_TOKEN_EXPIRE", "0"))

    def _credentials_ok(self) -> bool:
        return bool(self._app_key and self._app_secret and self._access_token)

    def _token_needs_refresh(self) -> bool:
        """True if access_token expires within 3 days."""
        if not self._token_expire:
            return False
        three_days_ms = 3 * 24 * 3600 * 1000
        return (self._token_expire - int(time.time() * 1000)) < three_days_ms

    async def _auto_refresh_token(self) -> bool:
        """Refresh access_token using refresh_token. Updates .env and in-memory state.
        Returns True on success."""
        if not self._refresh_token:
            logger.warning("AliExpress: no refresh_token available — manual re-auth needed")
            return False
        path = "/auth/token/refresh"
        params = {
            "app_key": self._app_key,
            "refresh_token": self._refresh_token,
            "timestamp": str(int(time.time() * 1000)),
            "sign_method": "sha256",
        }
        msg = path + "".join(k + str(v) for k, v in sorted(params.items()))
        params["sign"] = hmac.new(
            self._app_secret.encode("utf-8"),
            msg.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest().upper()
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(
                    "https://api-sg.aliexpress.com/rest/auth/token/refresh",
                    data=params,
                )
                data = resp.json()
        except Exception as exc:
            logger.error("AliExpress token refresh request failed: %s", exc)
            return False

        new_token = data.get("access_token", "")
        new_refresh = data.get("refresh_token", "")
        new_expire = data.get("expire_time", 0)

        if not new_token:
            logger.error("AliExpress token refresh failed: %s", data)
            return False

        # Update in-memory
        self._access_token = new_token
        if new_refresh:
            self._refresh_token = new_refresh
        if new_expire:
            self._token_expire = int(new_expire)

        # Persist to .env
        env_path = os.getenv("ENV_FILE_PATH", "/app/.env")
        try:
            with open(env_path, "r") as f:
                lines = f.readlines()
            updated = {
                "ALIEXPRESS_ACCESS_TOKEN": new_token,
                "ALIEXPRESS_REFRESH_TOKEN": new_refresh or self._refresh_token,
                "ALIEXPRESS_TOKEN_EXPIRE": str(new_expire or self._token_expire),
            }
            new_lines = []
            found = set()
            for line in lines:
                key = line.split("=", 1)[0]
                if key in updated:
                    new_lines.append(f"{key}={updated[key]}\n")
                    found.add(key)
                else:
                    new_lines.append(line)
            for key, val in updated.items():
                if key not in found:
                    new_lines.append(f"{key}={val}\n")
            with open(env_path, "w") as f:
                f.writelines(new_lines)
            logger.info("AliExpress token auto-refreshed and saved to %s", env_path)
        except Exception as exc:
            logger.warning("AliExpress: token refreshed in memory but .env write failed: %s", exc)

        return True

    def get_oauth_url(self, redirect_uri: str = "https://autosparefinder.co.il/aliexpress/callback") -> str:
        """Returns the URL the store owner must visit once to authorize the DS app."""
        return (
            f"{ALIEXPRESS_AUTH_URL}?response_type=code"
            f"&force_auth=true&redirect_uri={redirect_uri}"
            f"&client_id={self._app_key}"
        )

    async def exchange_code_for_token(self, code: str) -> dict:
        """Exchange OAuth code for access_token using the correct REST endpoint.
        POST https://api-sg.aliexpress.com/rest/auth/token/create
        IOP signing: HMAC-SHA256(key=secret, msg=path+sorted_kv)
        Returns dict with access_token, refresh_token, expire_time."""
        path = "/auth/token/create"
        params = {
            "app_key": self._app_key,
            "code": code,
            "timestamp": str(int(time.time() * 1000)),
            "sign_method": "sha256",
        }
        msg = path + "".join(k + str(v) for k, v in sorted(params.items()))
        params["sign"] = hmac.new(
            self._app_secret.encode("utf-8"),
            msg.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest().upper()
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(ALIEXPRESS_TOKEN_URL, data=params)
            resp.raise_for_status()
            data = resp.json()
        if data.get("code") not in (None, "0", 0) or data.get("type") == "ISV":
            raise ValueError(f"Token exchange failed: {data}")
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
                "Get token via: AliExpressSupplier().get_oauth_url()"
            )
            return []

        params = self._build_request(
            "aliexpress.ds.product.specialinfo.get",
            {
                "product_ids": query,  # used when query is a product_id list
                "ship_to_country": "IL",
                "target_currency": "USD",
                "target_language": "EN",
            },
        )
        # DS API has no text-search endpoint; search by OEM/product_id via specialinfo.
        # For keyword search, fall back to wholesale.get with a known product_id.
        # Primary use is search_by_oem (product_id lookup).
        params = self._build_request(
            "aliexpress.ds.product.wholesale.get",
            {
                "product_id": query,
                "ship_to_country": "IL",
                "target_currency": "USD",
                "target_language": "EN",
            },
        )

        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(ALIEXPRESS_API_URL, data=params)
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            logger.error("AliExpress DS search failed for '%s': %s", query, exc)
            return []

        err = data.get("error_response", {})
        if err:
            logger.warning("AliExpress DS search error for '%s': %s", query, err)
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

    async def search_by_oem(self, oem_number: str, limit: int = 10) -> list[PartResult]:
        return await self.search(oem_number, limit)

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
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(ALIEXPRESS_API_URL, data=params)
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            logger.error("AliExpress DS get_part_details failed for '%s': %s", item_id, exc)
            return None

        if data.get("error_response"):
            logger.warning("AliExpress DS get_part_details error: %s", data["error_response"])
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
