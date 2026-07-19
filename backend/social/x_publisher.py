"""
social/x_publisher.py — post to X (Twitter) via API v2 POST /2/tweets.

Posting a tweet is a user-context write → OAuth 1.0a (HMAC-SHA1) signed request. We sign
with stdlib (hmac/hashlib/base64) — no external OAuth library needed.

Setup (docs/SOCIAL_SETUP.md):
  1. Create a Project + App at https://developer.x.com/ (Basic tier or higher — write
     access to /2/tweets is a paid tier).
  2. In the App → Keys and tokens: generate API Key/Secret (consumer) AND an Access
     Token/Secret with **Read and Write** permission.
Env:
    X_API_KEY=<consumer api key>
    X_API_SECRET=<consumer api secret>
    X_ACCESS_TOKEN=<user access token>
    X_ACCESS_SECRET=<user access token secret>

Text-only (with link) for now; image/video needs the v1.1 media-upload endpoint
(documented as a follow-up in SOCIAL_SETUP.md).

Author: AutoSpareFinder Agent — Last Updated: 2026-07-19
"""
import base64
import hashlib
import hmac
import os
import time
import urllib.parse
import uuid

import httpx

PLATFORM = "x"
_ENDPOINT = "https://api.twitter.com/2/tweets"


def _cfg():
    return (os.getenv("X_API_KEY", "").strip(), os.getenv("X_API_SECRET", "").strip(),
            os.getenv("X_ACCESS_TOKEN", "").strip(), os.getenv("X_ACCESS_SECRET", "").strip())


def is_configured() -> bool:
    return all(_cfg())


def _quote(s: str) -> str:
    return urllib.parse.quote(str(s), safe="~")


def _oauth1_header(method: str, url: str, ck: str, cs: str, at: str, ats: str) -> str:
    """Build the OAuth 1.0a Authorization header for a JSON-body request (no body params
    are signed for a POST with a JSON payload — only the oauth_* params)."""
    params = {
        "oauth_consumer_key": ck,
        "oauth_nonce": uuid.uuid4().hex,
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_token": at,
        "oauth_version": "1.0",
    }
    param_str = "&".join(f"{_quote(k)}={_quote(params[k])}" for k in sorted(params))
    base_str = "&".join([method.upper(), _quote(url), _quote(param_str)])
    signing_key = f"{_quote(cs)}&{_quote(ats)}"
    sig = base64.b64encode(hmac.new(signing_key.encode(), base_str.encode(), hashlib.sha1).digest()).decode()
    params["oauth_signature"] = sig
    return "OAuth " + ", ".join(f'{_quote(k)}="{_quote(v)}"' for k, v in sorted(params.items()))


async def publish(content: str, *, media_url: str | None = None,
                  hashtags: list | None = None, link: str | None = None) -> dict:
    ck, cs, at, ats = _cfg()
    if not all((ck, cs, at, ats)):
        return {"ok": False, "id": None, "not_configured": True,
                "error": "X_API_KEY / X_API_SECRET / X_ACCESS_TOKEN / X_ACCESS_SECRET not set"}

    text = (content or "").strip()
    if link and link not in text:
        text = f"{text}\n{link}".strip()
    text = text[:280]  # X hard limit
    try:
        header = _oauth1_header("POST", _ENDPOINT, ck, cs, at, ats)
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.post(_ENDPOINT, json={"text": text},
                                  headers={"Authorization": header, "Content-Type": "application/json"})
        j = r.json() if r.content else {}
        if r.status_code in (200, 201) and j.get("data", {}).get("id"):
            return {"ok": True, "id": j["data"]["id"], "error": None, "not_configured": False}
        return {"ok": False, "id": None, "not_configured": False,
                "error": f"x {r.status_code}: {str(j.get('detail') or j.get('errors') or r.text)[:180]}"}
    except Exception as exc:
        return {"ok": False, "id": None, "not_configured": False, "error": str(exc)[:180]}
