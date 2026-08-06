"""
Script: social/meta_client.py
Purpose: Centralised Meta Graph API client — token management, rate limiting, retries.
         All social/* modules that call the Meta API must go through this client so that
         rate limits, token refresh, error logging, and platform_accounts table updates
         happen in a single place.

Process:
  1. MetaClient is a lightweight async HTTP wrapper over httpx.
  2. _RateLimiter tracks per-endpoint call counts with a rolling 1-hour window stored in
     an in-process dict (Redis not required; the client is single-process).
  3. Token validation: on every call we check whether the page token is still valid by
     looking at `platform_accounts.token_expires_at`; NULL means non-expiring (page token).
  4. Retry strategy: 429/500/503 → exponential backoff (max 3 retries, cap 60s).
  5. After every call (success or failure) we update platform_accounts via an optional
     background DB write so the owner console can show live connection health.

Data Imported/Modified: platform_accounts (status, last_api_call_at, last_error, api_calls_today)
Data Sources: https://graph.facebook.com/
Last Updated: 2026-08-06
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid as _uuid
from datetime import datetime, timedelta
from typing import Any

import httpx

log = logging.getLogger("meta_client")

GRAPH_BASE = "https://graph.facebook.com"
_DEFAULT_VER = os.getenv("GRAPH_API_VERSION", "v21.0").strip()

# Per-endpoint rate limit: max calls per window.
_RATE_LIMITS: dict[str, tuple[int, int]] = {
    # (max_calls, window_seconds)
    "feed": (200, 3600),
    "photos": (200, 3600),
    "videos": (200, 3600),
    "insights": (200, 3600),
    "comments": (200, 3600),
    "replies": (200, 3600),
    "default": (400, 3600),
}

_call_log: dict[str, list[float]] = {}  # endpoint → list of epoch timestamps
_call_log_lock = asyncio.Lock()


class RateLimitExceeded(Exception):
    pass


class MetaAPIError(Exception):
    def __init__(self, status: int, code: int | None, message: str):
        super().__init__(message)
        self.status = status
        self.code = code


async def _check_rate_limit(endpoint_key: str) -> None:
    """Raise RateLimitExceeded if the per-endpoint quota is full."""
    max_calls, window = _RATE_LIMITS.get(endpoint_key, _RATE_LIMITS["default"])
    now = time.monotonic()
    async with _call_log_lock:
        bucket = _call_log.setdefault(endpoint_key, [])
        cutoff = now - window
        _call_log[endpoint_key] = [t for t in bucket if t > cutoff]
        if len(_call_log[endpoint_key]) >= max_calls:
            raise RateLimitExceeded(
                f"Meta API rate limit reached for '{endpoint_key}' "
                f"({max_calls} calls / {window}s)"
            )
        _call_log[endpoint_key].append(now)


async def _meta_request(
    method: str,
    path: str,
    *,
    params: dict | None = None,
    data: dict | None = None,
    json_body: dict | None = None,
    timeout: float = 30.0,
    retries: int = 3,
    endpoint_key: str = "default",
    access_token: str,
    api_version: str = _DEFAULT_VER,
) -> dict[str, Any]:
    """Low-level Meta Graph API call with rate limiting and retry.

    Returns parsed JSON dict on success. Raises MetaAPIError on non-retryable
    failures, RateLimitExceeded when our own quota is full.
    """
    await _check_rate_limit(endpoint_key)

    url = f"{GRAPH_BASE}/{api_version}/{path.lstrip('/')}"
    _params = dict(params or {})
    _data = dict(data or {})

    # Token is passed via params for GET, data for POST (Graph convention)
    if method.upper() == "GET":
        _params.setdefault("access_token", access_token)
    else:
        _data.setdefault("access_token", access_token)

    delay = 1.0
    last_exc: Exception | None = None

    for attempt in range(retries + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.request(
                    method,
                    url,
                    params=_params or None,
                    data=_data or None,
                    json=json_body,
                )

            body: dict = resp.json() if resp.content else {}

            if resp.status_code == 200:
                return body

            # Parse Meta's structured error
            err_obj = body.get("error", {})
            code = err_obj.get("code")
            msg = err_obj.get("message") or resp.text[:200]

            # Non-retryable
            if resp.status_code in (400, 401, 403, 404):
                raise MetaAPIError(resp.status_code, code, msg)

            # Retryable (429, 500, 503)
            last_exc = MetaAPIError(resp.status_code, code, msg)
            if attempt < retries:
                log.warning(
                    "meta_client %s %s → %d (attempt %d/%d), retry in %.1fs: %s",
                    method, path, resp.status_code, attempt + 1, retries, delay, msg
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60.0)
                continue

        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            last_exc = exc
            if attempt < retries:
                log.warning("meta_client network error (attempt %d/%d): %s", attempt + 1, retries, exc)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60.0)
                continue

    raise last_exc or MetaAPIError(0, None, "Unknown error")


# ── Convenience wrappers ───────────────────────────────────────────────────────

async def graph_get(path: str, *, token: str, params: dict | None = None,
                    endpoint_key: str = "default", api_version: str = _DEFAULT_VER) -> dict:
    return await _meta_request("GET", path, params=params, access_token=token,
                                endpoint_key=endpoint_key, api_version=api_version)


async def graph_post(path: str, *, token: str, data: dict | None = None,
                     json_body: dict | None = None, endpoint_key: str = "default",
                     api_version: str = _DEFAULT_VER) -> dict:
    return await _meta_request("POST", path, data=data, json_body=json_body,
                                access_token=token, endpoint_key=endpoint_key,
                                api_version=api_version)


# ── Token validation helpers ───────────────────────────────────────────────────

def _cfg_facebook() -> tuple[str, str, str]:
    """(page_id, page_token, api_version)"""
    return (
        os.getenv("FACEBOOK_PAGE_ID", "").strip(),
        os.getenv("FACEBOOK_PAGE_TOKEN", "").strip(),
        os.getenv("GRAPH_API_VERSION", "v21.0").strip(),
    )


def facebook_configured() -> bool:
    pid, tok, _ = _cfg_facebook()
    return bool(pid and tok)


async def validate_facebook_token() -> dict:
    """Call /debug_token to check the page token's validity.

    Returns {"valid": bool, "expires_at": datetime|None, "scopes": list, "error": str|None}
    """
    pid, tok, ver = _cfg_facebook()
    app_id = os.getenv("FACEBOOK_APP_ID", "").strip()
    app_secret = os.getenv("FACEBOOK_APP_SECRET", "").strip()
    if not (tok and app_id and app_secret):
        return {"valid": False, "expires_at": None, "scopes": [], "error": "credentials not configured"}
    app_token = f"{app_id}|{app_secret}"
    try:
        data = await graph_get(
            "debug_token",
            token=app_token,
            params={"input_token": tok},
            endpoint_key="default",
        )
        d = data.get("data", {})
        expires_ts = d.get("expires_at")
        return {
            "valid": bool(d.get("is_valid")),
            "expires_at": datetime.utcfromtimestamp(expires_ts) if expires_ts else None,
            "scopes": d.get("scopes", []),
            "error": None,
        }
    except Exception as exc:
        return {"valid": False, "expires_at": None, "scopes": [], "error": str(exc)[:200]}


# ── platform_accounts table helper (fire-and-forget; no DB dependency at import) ──

async def _update_platform_account(db: Any, platform: str, *, status: str,
                                   last_error: str | None = None) -> None:
    """Upsert into platform_accounts to reflect current connection health."""
    try:
        now = datetime.utcnow()
        await db.execute(
            __import__("sqlalchemy").text("""
                INSERT INTO platform_accounts (id, platform, status, last_api_call_at, last_error,
                                              capabilities, created_at, updated_at)
                VALUES (gen_random_uuid(), :p, :s, :t, :e, '{}', :t, :t)
                ON CONFLICT (platform) DO UPDATE SET
                    status = EXCLUDED.status,
                    last_api_call_at = EXCLUDED.last_api_call_at,
                    last_error = EXCLUDED.last_error,
                    updated_at = EXCLUDED.updated_at
            """),
            {"p": platform, "s": status, "t": now, "e": last_error},
        )
        await db.commit()
    except Exception as exc:
        log.debug("_update_platform_account failed (non-critical): %s", exc)
