"""
integrations/meta/auth_manager.py — Meta OAuth token lifecycle.

Wraps social/meta_client.py for structured access. Adds:
  - exchange_for_long_lived_token()  (token exchange flow)
  - is_configured()                  (quick env check without full validation)
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger("integrations.meta.auth_manager")


def is_configured() -> bool:
    """True if the minimum Facebook credentials are present in env."""
    return bool(
        os.getenv("FACEBOOK_PAGE_TOKEN", "").strip()
        and os.getenv("FACEBOOK_PAGE_ID", "").strip()
    )


async def validate_token() -> dict:
    """Validate the current page token via /debug_token.

    Returns dict: {valid, expires_at, scopes, app_id, error}.
    """
    from social.meta_client import validate_facebook_token
    return await validate_facebook_token()


async def exchange_for_long_lived_token(short_lived_user_token: str) -> dict:
    """
    Exchange a short-lived user token for a long-lived page token.

    Flow:  short-lived → long-lived user token → page access tokens → non-expiring page token
    The non-expiring page token is returned and should be stored in FACEBOOK_PAGE_TOKEN.

    Returns: {ok, page_id, page_token, expires_at, error}
    """
    from social.meta_client import graph_get, _cfg_facebook
    page_id, _, api_version = _cfg_facebook()
    app_id = os.getenv("FACEBOOK_APP_ID", "")
    app_secret = os.getenv("FACEBOOK_APP_SECRET", "")

    if not app_id or not app_secret:
        return {"ok": False, "error": "FACEBOOK_APP_ID or FACEBOOK_APP_SECRET not set"}

    # Step 1: exchange for long-lived user token
    ll = await graph_get(
        "oauth/access_token",
        params={
            "grant_type": "fb_exchange_token",
            "client_id": app_id,
            "client_secret": app_secret,
            "fb_exchange_token": short_lived_user_token,
        },
        token=f"{app_id}|{app_secret}",
    )
    if "error" in ll:
        return {"ok": False, "error": str(ll.get("error"))}

    long_lived_token = ll.get("access_token")
    if not long_lived_token:
        return {"ok": False, "error": "no access_token in exchange response"}

    # Step 2: get page access tokens
    me = await graph_get(
        "me/accounts",
        params={"fields": "id,name,access_token"},
        token=long_lived_token,
    )
    pages = me.get("data", [])
    page = next((p for p in pages if p.get("id") == page_id), None)
    if not page and pages:
        page = pages[0]
    if not page:
        return {"ok": False, "error": f"page {page_id!r} not in /me/accounts"}

    return {
        "ok": True,
        "page_id": page.get("id"),
        "page_name": page.get("name"),
        "page_token": page.get("access_token"),
        "expires_at": None,  # Page tokens from a long-lived user token don't expire
    }
