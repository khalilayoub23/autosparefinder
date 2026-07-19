"""
social/reddit_publisher.py — submit NOA content to a subreddit via the Reddit API (OAuth2).

Uses the "script" app password grant (no interactive OAuth redirect):
  token = POST https://www.reddit.com/api/v1/access_token
          (HTTP basic auth client_id/secret, grant_type=password&username&password)
  submit = POST https://oauth.reddit.com/api/submit

Reddit posts need a TITLE — we use the first line of NOA's content as the title and the
remainder as the self-text body (kind='self'); the CTA link is appended into the body.

Setup (docs/SOCIAL_SETUP.md):
  1. https://www.reddit.com/prefs/apps → "create app" → type: **script**.
     redirect uri can be http://localhost:8080 (unused by the password grant).
  2. Note the client id (under the app name) and secret.
Env:
    REDDIT_CLIENT_ID=<id>
    REDDIT_CLIENT_SECRET=<secret>
    REDDIT_USERNAME=<the posting account>
    REDDIT_PASSWORD=<its password>
    REDDIT_SUBREDDIT=<target sub, no r/ prefix>
    REDDIT_USER_AGENT=autosparefinder:noa:v1 (by /u/<username>)   # optional

Author: AutoSpareFinder Agent — Last Updated: 2026-07-19
"""
import os

import httpx

PLATFORM = "reddit"


def _cfg():
    return {
        "cid": os.getenv("REDDIT_CLIENT_ID", "").strip(),
        "csecret": os.getenv("REDDIT_CLIENT_SECRET", "").strip(),
        "user": os.getenv("REDDIT_USERNAME", "").strip(),
        "pw": os.getenv("REDDIT_PASSWORD", "").strip(),
        "sub": os.getenv("REDDIT_SUBREDDIT", "").strip().lstrip("r/").strip("/"),
        "ua": os.getenv("REDDIT_USER_AGENT", "").strip() or "autosparefinder:noa:v1",
    }


def is_configured() -> bool:
    c = _cfg()
    return all((c["cid"], c["csecret"], c["user"], c["pw"], c["sub"]))


async def publish(content: str, *, media_url: str | None = None,
                  hashtags: list | None = None, link: str | None = None) -> dict:
    c = _cfg()
    if not is_configured():
        return {"ok": False, "id": None, "not_configured": True,
                "error": "REDDIT_CLIENT_ID/SECRET/USERNAME/PASSWORD/SUBREDDIT not set"}

    text = (content or "").strip()
    lines = [ln for ln in text.splitlines() if ln.strip()]
    title = (lines[0] if lines else text)[:300].strip() or "AutoSpareFinder"
    body = "\n".join(lines[1:]).strip()
    if link and link not in body:
        body = f"{body}\n\n{link}".strip()

    headers = {"User-Agent": c["ua"]}
    try:
        async with httpx.AsyncClient(timeout=25.0) as client:
            tok_r = await client.post(
                "https://www.reddit.com/api/v1/access_token",
                auth=(c["cid"], c["csecret"]),
                data={"grant_type": "password", "username": c["user"], "password": c["pw"]},
                headers=headers,
            )
            tj = tok_r.json() if tok_r.content else {}
            token = tj.get("access_token")
            if not token:
                return {"ok": False, "id": None, "not_configured": False,
                        "error": f"reddit auth {tok_r.status_code}: {str(tj)[:150]}"}
            sub_r = await client.post(
                "https://oauth.reddit.com/api/submit",
                headers={**headers, "Authorization": f"bearer {token}"},
                data={"sr": c["sub"], "kind": "self", "title": title, "text": body,
                      "api_type": "json", "resubmit": "true"},
            )
            sj = sub_r.json() if sub_r.content else {}
            errs = (sj.get("json", {}) or {}).get("errors") or []
            data = (sj.get("json", {}) or {}).get("data") or {}
            if sub_r.status_code == 200 and not errs and (data.get("id") or data.get("url")):
                return {"ok": True, "id": data.get("name") or data.get("id") or data.get("url"),
                        "error": None, "not_configured": False}
            return {"ok": False, "id": None, "not_configured": False,
                    "error": f"reddit submit {sub_r.status_code}: {str(errs or sj)[:180]}"}
    except Exception as exc:
        return {"ok": False, "id": None, "not_configured": False, "error": str(exc)[:180]}
