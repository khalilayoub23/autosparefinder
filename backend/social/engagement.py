"""
social/engagement.py — NOA's inbound social ENGAGEMENT engine (read + reply).

The social/*_publisher.py modules only PUBLISH. This adds the other half NOA needs:
READ activity (comments / mentions / DMs) on the platform pages we run, and REPLY to it.

Design (uniform, like registry.py):
  • Each platform exposes two coroutines with the same contract:
        async def fetch_new(limit) -> list[EngagementItem]   # new inbound items
        async def post_reply(item, text) -> {"ok","id","error"}
    A platform whose credentials are missing/expired simply returns [] / not-configured,
    so the loop degrades gracefully (never crashes, never blocks other platforms).
  • Items are recorded in `social_inbox` (dedupe by (platform,external_id)) with a status
    lifecycle: new → pending_approval → replied | skipped. NOA drafts the reply; the owner
    approves it from the WhatsApp console (or NOA_ENGAGEMENT_AUTOREPLY=1 auto-sends).

EngagementItem = {
    "platform", "kind" (comment|mention|dm), "external_id", "parent_id",
    "author", "text", "permalink", "created_at"
}

Implemented free (official platform APIs, no paid aggregator):
  • Facebook  — Page post comments (Graph API)
  • Instagram — media comments (Graph API, via the linked FB page)
  • Telegram  — groups/channels/DMs via a DEDICATED NOA bot (getUpdates + sendMessage)
  • Reddit    — subreddit comments + inbox replies/mentions (OAuth script app)
  • Discord   — server-channel messages + DMs via a bot token (REST)
  • Google Business — reply to Google reviews (Business Profile API v4, OAuth refresh token)
  • YouTube — read + reply to comments on our channel (Data API v3, free, OAuth refresh token)
Still walled for EVERYONE (documented, not faked): Facebook GROUPS (Meta deprecated the
Groups API, Apr-2024), X reading (paywalled), TikTok comment API (approval-gated),
Meta DMs (Messenger/IG direct need pages_messaging / instagram_manage_messages app review).
Last Updated: 2026-07-25
"""
from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Any, Dict, List

from sqlalchemy import text as _sql

GRAPH = "https://graph.facebook.com/v21.0"


def log(*a):
    print("[engagement]", *a, flush=True)


def _http_json(url: str, *, method: str = "GET", headers: Dict[str, str] | None = None,
               data: Any = None, timeout: int = 25) -> Dict[str, Any]:
    """Generic authorized JSON HTTP call used by the Discord/Telegram/Reddit adapters.
    Returns parsed JSON, or {"error": {...}} — never raises."""
    headers = dict(headers or {})
    body = data
    if isinstance(data, dict):
        if headers.get("Content-Type", "").startswith("application/json"):
            body = json.dumps(data).encode()
        else:
            body = urllib.parse.urlencode(data).encode()
    try:
        req = urllib.request.Request(url, data=body, method=method, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        return {"error": {"http": e.code, "body": e.read().decode("utf-8", "replace")[:300]}}
    except Exception as e:
        return {"error": {"body": str(e)[:200]}}


# ── social_inbox store ────────────────────────────────────────────────────────
async def ensure_inbox_table(db) -> None:
    await db.execute(_sql("""
        CREATE TABLE IF NOT EXISTS social_inbox (
            id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            platform         varchar(24)  NOT NULL,
            kind             varchar(16)  NOT NULL,     -- comment | mention | dm
            external_id      varchar(128) NOT NULL,
            parent_id        varchar(128),
            author           varchar(200),
            message          text,
            permalink        text,
            status           varchar(20)  NOT NULL DEFAULT 'new',  -- new|pending_approval|replied|skipped
            reply_text       text,
            reply_external_id varchar(128),
            created_at       timestamptz  NOT NULL DEFAULT NOW(),
            replied_at       timestamptz,
            UNIQUE (platform, external_id)
        )
    """))
    await db.commit()


async def record_item(db, item: Dict[str, Any]) -> str | None:
    """Insert a new inbound item. Returns its row id if NEW (not seen before), else None."""
    res = await db.execute(_sql("""
        INSERT INTO social_inbox (platform, kind, external_id, parent_id, author, message, permalink, created_at)
        VALUES (:platform, :kind, :external_id, :parent_id, :author, :message, :permalink, :created_at)
        ON CONFLICT (platform, external_id) DO NOTHING
        RETURNING id::text
    """), {
        "platform": item["platform"], "kind": item.get("kind", "comment"),
        "external_id": str(item["external_id"]), "parent_id": item.get("parent_id"),
        "author": (item.get("author") or "")[:200], "message": item.get("text") or "",
        "permalink": item.get("permalink"),
        "created_at": item.get("created_at") or datetime.utcnow(),
    })
    row = res.first()
    return row[0] if row else None


async def set_draft(db, item_id: str, reply_text: str) -> None:
    """Attach NOA's drafted reply and move the item to pending_approval."""
    await db.execute(_sql("""
        UPDATE social_inbox SET reply_text = :t, status = 'pending_approval'
        WHERE id = CAST(:id AS uuid) AND status = 'new'
    """), {"t": reply_text, "id": item_id})
    await db.commit()


async def mark_replied(db, item_id: str, reply_external_id: str | None) -> None:
    await db.execute(_sql("""
        UPDATE social_inbox
           SET status = 'replied', reply_external_id = :rid, replied_at = NOW()
         WHERE id = CAST(:id AS uuid)
    """), {"rid": reply_external_id, "id": item_id})
    await db.commit()


async def mark_skipped(db, item_id: str) -> None:
    await db.execute(_sql("UPDATE social_inbox SET status='skipped' WHERE id = CAST(:id AS uuid)"),
                     {"id": item_id})
    await db.commit()


async def pending_for_owner(db, limit: int = 8) -> List[Dict[str, Any]]:
    """Items NOA has drafted a reply for and is waiting on the owner to approve."""
    res = await db.execute(_sql("""
        SELECT id::text, platform, kind, author, message, reply_text, permalink, external_id
          FROM social_inbox
         WHERE status = 'pending_approval'
         ORDER BY created_at DESC
         LIMIT :lim
    """), {"lim": limit})
    return [dict(r._mapping) for r in res.fetchall()]


async def resolve_inbox(db, token: str) -> Dict[str, Any] | None:
    """Resolve a pending item by an 8-char id prefix (what the owner sees), else newest."""
    token = (token or "").strip()
    if token:
        res = await db.execute(_sql("""
            SELECT id::text, platform, external_id, reply_text, author, message
              FROM social_inbox
             WHERE status='pending_approval' AND id::text LIKE :pfx
             LIMIT 1
        """), {"pfx": token + "%"})
    else:
        res = await db.execute(_sql("""
            SELECT id::text, platform, external_id, reply_text, author, message
              FROM social_inbox WHERE status='pending_approval'
             ORDER BY created_at DESC LIMIT 1
        """))
    row = res.first()
    return dict(row._mapping) if row else None


async def send_reply(platform: str, external_id: str, text: str) -> Dict[str, Any]:
    """Dispatch a reply to the right platform via the PLATFORMS registry."""
    h = PLATFORMS.get(platform)
    if not h:
        return {"ok": False, "error": f"unknown platform {platform}"}
    return await h["reply"](external_id, text)


# ── Facebook (Page comments) ──────────────────────────────────────────────────
def _fb_token() -> str:
    return (os.getenv("FACEBOOK_PAGE_TOKEN", "") or "").strip()


def _fb_page_id() -> str:
    return (os.getenv("FACEBOOK_PAGE_ID", "") or "").strip()


def fb_configured() -> bool:
    return bool(_fb_token() and _fb_page_id())


def _graph_get(path: str, params: Dict[str, str]) -> Dict[str, Any]:
    params = {**params, "access_token": _fb_token()}
    url = f"{GRAPH}/{path}?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=25) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        return {"error": {"http": e.code, "body": body[:300]}}
    except Exception as e:
        return {"error": {"body": str(e)[:200]}}


def _graph_post(path: str, data: Dict[str, str]) -> Dict[str, Any]:
    data = {**data, "access_token": _fb_token()}
    body = urllib.parse.urlencode(data).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(f"{GRAPH}/{path}", data=body, method="POST"),
                                    timeout=25) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"error": {"http": e.code, "body": e.read().decode("utf-8", "replace")[:300]}}
    except Exception as e:
        return {"error": {"body": str(e)[:200]}}


async def fb_fetch_new(limit: int = 25) -> List[Dict[str, Any]]:
    """Recent comments across the Page's recent posts. Read scope: pages_read_engagement."""
    if not fb_configured():
        return []
    out: List[Dict[str, Any]] = []
    posts = _graph_get(f"{_fb_page_id()}/posts", {"fields": "id,permalink_url", "limit": "10"})
    if posts.get("error"):
        log("FB posts read failed:", posts["error"])
        return []
    for post in posts.get("data", []):
        pid = post.get("id")
        if not pid:
            continue
        cm = _graph_get(f"{pid}/comments", {
            "fields": "id,message,from,created_time,permalink_url", "limit": str(limit), "order": "reverse_chronological"})
        for c in cm.get("data", []):
            out.append({
                "platform": "facebook", "kind": "comment", "external_id": c.get("id"),
                "parent_id": pid, "author": (c.get("from") or {}).get("name") or "",
                "text": c.get("message") or "", "permalink": c.get("permalink_url") or post.get("permalink_url"),
                "created_at": _parse_ts(c.get("created_time")),
            })
    return out


async def fb_post_reply(external_id: str, text: str) -> Dict[str, Any]:
    """Reply to a comment. Needs pages_manage_engagement on the token."""
    if not fb_configured():
        return {"ok": False, "error": "facebook not configured"}
    r = _graph_post(f"{external_id}/comments", {"message": text})
    if r.get("error"):
        return {"ok": False, "error": str(r["error"])[:200]}
    return {"ok": True, "id": r.get("id")}


# ── Instagram (comments on IG business posts, via the linked FB page) ─────────
def _ig_user_id() -> str:
    igid = (os.getenv("INSTAGRAM_BUSINESS_ID", "") or os.getenv("IG_USER_ID", "") or "").strip()
    if igid:
        return igid
    # resolve from the FB page (instagram_business_account) and cache in-process
    if fb_configured():
        r = _graph_get(_fb_page_id(), {"fields": "instagram_business_account"})
        return ((r.get("instagram_business_account") or {}).get("id")) or ""
    return ""


def ig_configured() -> bool:
    return bool(_fb_token() and _ig_user_id())


async def ig_fetch_new(limit: int = 25) -> List[Dict[str, Any]]:
    if not ig_configured():
        return []
    out: List[Dict[str, Any]] = []
    igid = _ig_user_id()
    media = _graph_get(f"{igid}/media", {"fields": "id,permalink", "limit": "10"})
    if media.get("error"):
        log("IG media read failed:", media["error"])
        return []
    for m in media.get("data", []):
        mid = m.get("id")
        if not mid:
            continue
        cm = _graph_get(f"{mid}/comments", {"fields": "id,text,username,timestamp", "limit": str(limit)})
        for c in cm.get("data", []):
            out.append({
                "platform": "instagram", "kind": "comment", "external_id": c.get("id"),
                "parent_id": mid, "author": c.get("username") or "",
                "text": c.get("text") or "", "permalink": m.get("permalink"),
                "created_at": _parse_ts(c.get("timestamp")),
            })
    return out


async def ig_post_reply(external_id: str, text: str) -> Dict[str, Any]:
    if not ig_configured():
        return {"ok": False, "error": "instagram not configured"}
    r = _graph_post(f"{external_id}/replies", {"message": text})
    if r.get("error"):
        return {"ok": False, "error": str(r["error"])[:200]}
    return {"ok": True, "id": r.get("id")}


# ── Discord (server-channel messages + DMs via a bot token) ───────────────────
# external_id is COMPOSITE "channel_id:message_id" so post_reply can route without
# extra context (the uniform contract only passes external_id).
DISCORD_API = "https://discord.com/api/v10"
_discord_bot_id = ""


def _discord_token() -> str:
    return (os.getenv("DISCORD_BOT_TOKEN", "") or "").strip()


def _discord_channels() -> List[str]:
    return [c.strip() for c in os.getenv("DISCORD_ENGAGE_CHANNELS", "").split(",") if c.strip()]


def discord_configured() -> bool:
    return bool(_discord_token() and _discord_channels())


def _discord_headers() -> Dict[str, str]:
    return {"Authorization": f"Bot {_discord_token()}",
            "Content-Type": "application/json",
            "User-Agent": "AutoSpareFinder-NOA/1.0"}


def _discord_me() -> str:
    global _discord_bot_id
    if _discord_bot_id:
        return _discord_bot_id
    r = _http_json(f"{DISCORD_API}/users/@me", headers=_discord_headers())
    _discord_bot_id = str(r.get("id") or "")
    return _discord_bot_id


async def discord_fetch_new(limit: int = 25) -> List[Dict[str, Any]]:
    if not discord_configured():
        return []
    me = _discord_me()
    out: List[Dict[str, Any]] = []
    for ch in _discord_channels():
        msgs = _http_json(f"{DISCORD_API}/channels/{ch}/messages?limit={min(limit, 50)}",
                          headers=_discord_headers())
        if isinstance(msgs, dict) and msgs.get("error"):
            log("Discord read failed:", msgs["error"])
            continue
        for m in (msgs or []):
            author = m.get("author") or {}
            if str(author.get("id")) == me or author.get("bot"):
                continue  # skip our own bot + other bots
            out.append({
                "platform": "discord", "kind": "comment",
                "external_id": f"{ch}:{m.get('id')}", "parent_id": ch,
                "author": author.get("username") or "", "text": m.get("content") or "",
                "permalink": None, "created_at": _parse_ts(m.get("timestamp")),
            })
    return out


async def discord_post_reply(external_id: str, text: str) -> Dict[str, Any]:
    if not discord_configured():
        return {"ok": False, "error": "discord not configured"}
    ch, _, mid = external_id.partition(":")
    payload: Dict[str, Any] = {"content": text}
    if mid:
        payload["message_reference"] = {"message_id": mid}
    r = _http_json(f"{DISCORD_API}/channels/{ch}/messages", method="POST",
                   headers=_discord_headers(), data=payload)
    if r.get("error"):
        return {"ok": False, "error": str(r["error"])[:200]}
    return {"ok": True, "id": r.get("id")}


# ── Telegram (groups/channels/DMs via a DEDICATED NOA bot) ────────────────────
# Uses its OWN bot token (NOA_TELEGRAM_BOT_TOKEN), NOT the customer bot — getUpdates
# would 409 against the customer bot's webhook. external_id = "chat_id:message_id".
def _tg_token() -> str:
    # NOA's engagement bot. Prefer a dedicated NOA_TELEGRAM_BOT_TOKEN; otherwise reuse the
    # admin bot (TELEGRAM_ADMIN_BOT_TOKEN) which is already webhook-connected.
    return (os.getenv("NOA_TELEGRAM_BOT_TOKEN", "")
            or os.getenv("TELEGRAM_ADMIN_BOT_TOKEN", "") or "").strip()


def telegram_configured() -> bool:
    return bool(_tg_token())


def _tg_api(method: str) -> str:
    return f"https://api.telegram.org/bot{_tg_token()}/{method}"


def _tg_item_from_message(update: Dict[str, Any]) -> Dict[str, Any] | None:
    """Build an EngagementItem from a Telegram update's message (or None to skip)."""
    msg = update.get("message") or update.get("channel_post") or {}
    txt = msg.get("text") or msg.get("caption")
    if not txt:
        return None
    frm = msg.get("from") or {}
    if frm.get("is_bot"):
        return None
    chat = msg.get("chat") or {}
    return {
        "platform": "telegram",
        "kind": "dm" if chat.get("type") == "private" else "comment",
        "external_id": f"{chat.get('id')}:{msg.get('message_id')}",
        "parent_id": str(chat.get("id")),
        "author": frm.get("username") or frm.get("first_name") or "",
        "text": txt, "permalink": None,
        "created_at": datetime.utcfromtimestamp(msg["date"]) if msg.get("date") else datetime.utcnow(),
    }


async def ingest_telegram_update(db, update: Dict[str, Any], owner_chat_id: str = "") -> bool:
    """Called by the /webhooks/telegram-admin handler. Records an inbound Telegram
    message into social_inbox (status 'new'; the loop drafts it). Returns True if a new
    item was recorded. By default the owner's OWN DMs ARE recorded so the owner can
    self-test by DMing the bot; set NOA_ENGAGEMENT_SKIP_OWNER=1 to skip them (the owner
    normally drives NOA from the WhatsApp console, not by DMing the bot)."""
    item = _tg_item_from_message(update)
    if not item:
        return False
    if (owner_chat_id and os.getenv("NOA_ENGAGEMENT_SKIP_OWNER", "0") == "1"
            and item["parent_id"] == str(owner_chat_id)):
        return False
    await ensure_inbox_table(db)
    iid = await record_item(db, item)
    await db.commit()
    return bool(iid)


async def telegram_fetch_new(limit: int = 25) -> List[Dict[str, Any]]:
    # Telegram is WEBHOOK-FED by default (the admin bot already holds a webhook, so
    # getUpdates would 409). The webhook calls ingest_telegram_update(); the loop drafts
    # via draft_new_items(). Opt into polling only for a dedicated non-webhooked bot with
    # NOA_TELEGRAM_POLL=1.
    if not telegram_configured() or os.getenv("NOA_TELEGRAM_POLL", "0") != "1":
        return []
    r = _http_json(_tg_api("getUpdates") +
                   f"?limit={min(limit, 100)}&timeout=0&allowed_updates=%5B%22message%22%5D")
    if r.get("error") or not r.get("ok"):
        log("Telegram getUpdates failed:", r.get("error") or r.get("description"))
        return []
    out: List[Dict[str, Any]] = []
    for u in r.get("result", []):
        it = _tg_item_from_message(u)
        if it:
            out.append(it)
    return out


async def telegram_post_reply(external_id: str, text: str) -> Dict[str, Any]:
    if not telegram_configured():
        return {"ok": False, "error": "telegram not configured"}
    chat_id, _, mid = external_id.partition(":")
    data: Dict[str, Any] = {"chat_id": chat_id, "text": text}
    if mid:
        data["reply_to_message_id"] = mid
    r = _http_json(_tg_api("sendMessage"), method="POST", data=data)
    if r.get("error") or not r.get("ok"):
        return {"ok": False, "error": str(r.get("error") or r.get("description"))[:200]}
    return {"ok": True, "id": str((r.get("result") or {}).get("message_id") or "")}


# ── Reddit (subreddit comments + inbox replies/mentions, OAuth script app) ────
_REDDIT_UA = os.getenv("REDDIT_USER_AGENT", "") or "autosparefinder:noa:v1"
_reddit_tok = {"tok": "", "exp": 0.0}


def _reddit_cfg() -> Dict[str, str]:
    g = lambda k: (os.getenv(k, "") or "").strip()
    return {"cid": g("REDDIT_CLIENT_ID"), "csec": g("REDDIT_CLIENT_SECRET"),
            "user": g("REDDIT_USERNAME"), "pw": g("REDDIT_PASSWORD"),
            "sub": g("REDDIT_SUBREDDIT").lstrip("r/").strip("/")}


def reddit_configured() -> bool:
    c = _reddit_cfg()
    return bool(c["cid"] and c["csec"] and c["user"] and c["pw"])


def _reddit_token() -> str:
    if _reddit_tok["tok"] and _reddit_tok["exp"] > time.time() + 60:
        return _reddit_tok["tok"]
    c = _reddit_cfg()
    auth = base64.b64encode(f"{c['cid']}:{c['csec']}".encode()).decode()
    r = _http_json("https://www.reddit.com/api/v1/access_token", method="POST",
                   headers={"Authorization": f"Basic {auth}", "User-Agent": _REDDIT_UA,
                            "Content-Type": "application/x-www-form-urlencoded"},
                   data={"grant_type": "password", "username": c["user"], "password": c["pw"]})
    tok = r.get("access_token", "")
    if tok:
        _reddit_tok["tok"] = tok
        _reddit_tok["exp"] = time.time() + int(r.get("expires_in", 3600))
    else:
        log("Reddit auth failed:", r.get("error") or r)
    return tok


async def reddit_fetch_new(limit: int = 25) -> List[Dict[str, Any]]:
    if not reddit_configured():
        return []
    tok = _reddit_token()
    if not tok:
        return []
    c = _reddit_cfg()
    hdr = {"Authorization": f"bearer {tok}", "User-Agent": _REDDIT_UA}
    out: List[Dict[str, Any]] = []
    inbox = _http_json(f"https://oauth.reddit.com/message/unread?limit={min(limit, 100)}", headers=hdr)
    for ch in ((inbox.get("data") or {}).get("children") or []):
        d = ch.get("data") or {}
        out.append({
            "platform": "reddit", "kind": "mention" if d.get("was_comment") else "dm",
            "external_id": d.get("name"), "parent_id": d.get("parent_id"),
            "author": d.get("author") or "", "text": d.get("body") or "",
            "permalink": ("https://reddit.com" + d["context"]) if d.get("context") else None,
            "created_at": datetime.utcfromtimestamp(d["created_utc"]) if d.get("created_utc") else datetime.utcnow(),
        })
    if c["sub"]:
        cm = _http_json(f"https://oauth.reddit.com/r/{c['sub']}/comments?limit={min(limit, 100)}", headers=hdr)
        for ch in ((cm.get("data") or {}).get("children") or []):
            d = ch.get("data") or {}
            if d.get("author") == c["user"]:
                continue
            out.append({
                "platform": "reddit", "kind": "comment", "external_id": d.get("name"),
                "parent_id": d.get("link_id"), "author": d.get("author") or "",
                "text": d.get("body") or "",
                "permalink": ("https://reddit.com" + d["permalink"]) if d.get("permalink") else None,
                "created_at": datetime.utcfromtimestamp(d["created_utc"]) if d.get("created_utc") else datetime.utcnow(),
            })
    return out


async def reddit_post_reply(external_id: str, text: str) -> Dict[str, Any]:
    if not reddit_configured():
        return {"ok": False, "error": "reddit not configured"}
    tok = _reddit_token()
    if not tok:
        return {"ok": False, "error": "reddit auth failed"}
    r = _http_json("https://oauth.reddit.com/api/comment", method="POST",
                   headers={"Authorization": f"bearer {tok}", "User-Agent": _REDDIT_UA,
                            "Content-Type": "application/x-www-form-urlencoded"},
                   data={"api_type": "json", "thing_id": external_id, "text": text})
    if r.get("error"):
        return {"ok": False, "error": str(r["error"])[:200]}
    errs = ((r.get("json") or {}).get("errors")) or []
    if errs:
        return {"ok": False, "error": str(errs)[:200]}
    return {"ok": True, "id": external_id}


# ── Google Business Profile (reply to REVIEWS) ────────────────────────────────
# Reviews API is the older v4 host; account/location discovery uses the v1 hosts.
# external_id is the review resource name "accounts/{a}/locations/{l}/reviews/{r}",
# which is self-contained for the reply PUT.
_GBP_TOKEN_URL = "https://oauth2.googleapis.com/token"
_GBP_V4 = "https://mybusiness.googleapis.com/v4"
_GBP_ACCT_API = "https://mybusinessaccountmanagement.googleapis.com/v1"
_GBP_INFO_API = "https://mybusinessbusinessinformation.googleapis.com/v1"
_gbp_tok = {"tok": "", "exp": 0.0}
_gbp_loc = {"name": ""}
_GBP_STARS = {"ONE": "★☆☆☆☆", "TWO": "★★☆☆☆", "THREE": "★★★☆☆", "FOUR": "★★★★☆", "FIVE": "★★★★★"}


def _gbp_cfg() -> Dict[str, str]:
    g = lambda k: (os.getenv(k, "") or "").strip()
    return {"cid": g("GOOGLE_BUSINESS_CLIENT_ID"), "csec": g("GOOGLE_BUSINESS_CLIENT_SECRET"),
            "refresh": g("GOOGLE_BUSINESS_REFRESH_TOKEN"),
            "account": g("GOOGLE_BUSINESS_ACCOUNT"), "location": g("GOOGLE_BUSINESS_LOCATION")}


def gbp_configured() -> bool:
    c = _gbp_cfg()
    return bool(c["cid"] and c["csec"] and c["refresh"])


def _gbp_token() -> str:
    if _gbp_tok["tok"] and _gbp_tok["exp"] > time.time() + 60:
        return _gbp_tok["tok"]
    c = _gbp_cfg()
    r = _http_json(_GBP_TOKEN_URL, method="POST", data={
        "client_id": c["cid"], "client_secret": c["csec"],
        "refresh_token": c["refresh"], "grant_type": "refresh_token"})
    tok = r.get("access_token", "")
    if tok:
        _gbp_tok["tok"] = tok
        _gbp_tok["exp"] = time.time() + int(r.get("expires_in", 3600))
    else:
        log("GBP token refresh failed:", r.get("error") or r)
    return tok


def _gbp_location_name(tok: str) -> str:
    """Resolve 'accounts/{a}/locations/{l}' from env, else discover the first one."""
    if _gbp_loc["name"]:
        return _gbp_loc["name"]
    c = _gbp_cfg()
    acct, loc = c["account"], c["location"]
    hdr = {"Authorization": f"Bearer {tok}"}
    if not acct:
        a = _http_json(f"{_GBP_ACCT_API}/accounts", headers=hdr)
        accts = a.get("accounts") or []
        acct = accts[0]["name"] if accts else ""
    if acct and not loc:
        li = _http_json(f"{_GBP_INFO_API}/{acct}/locations?readMask=name", headers=hdr)
        locs = li.get("locations") or []
        loc = locs[0]["name"] if locs else ""
    if not acct or not loc:
        return ""
    name = loc if loc.startswith("accounts/") else (
        f"{acct}/{loc}" if loc.startswith("locations/") else f"{acct}/locations/{loc}")
    _gbp_loc["name"] = name
    return name


async def gbp_fetch_new(limit: int = 25) -> List[Dict[str, Any]]:
    if not gbp_configured():
        return []
    tok = _gbp_token()
    if not tok:
        return []
    name = _gbp_location_name(tok)
    if not name:
        log("GBP: no account/location resolved")
        return []
    r = _http_json(f"{_GBP_V4}/{name}/reviews?pageSize={min(limit, 50)}",
                   headers={"Authorization": f"Bearer {tok}"})
    if r.get("error"):
        log("GBP reviews read failed:", r["error"])
        return []
    out: List[Dict[str, Any]] = []
    for rv in r.get("reviews", []):
        if rv.get("reviewReply"):
            continue  # already replied (reviews get one owner reply)
        star = _GBP_STARS.get(rv.get("starRating", ""), rv.get("starRating", ""))
        comment = rv.get("comment") or ""
        out.append({
            "platform": "google_business", "kind": "review",
            "external_id": rv.get("name"), "parent_id": name,
            "author": (rv.get("reviewer") or {}).get("displayName") or "",
            "text": f"[{star}] {comment}".strip(), "permalink": None,
            "created_at": _parse_ts(rv.get("createTime")),
        })
    return out


async def gbp_post_reply(external_id: str, text: str) -> Dict[str, Any]:
    if not gbp_configured():
        return {"ok": False, "error": "google_business not configured"}
    tok = _gbp_token()
    if not tok:
        return {"ok": False, "error": "gbp auth failed"}
    r = _http_json(f"{_GBP_V4}/{external_id}/reply", method="PUT",
                   headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
                   data={"comment": text})
    if r.get("error"):
        return {"ok": False, "error": str(r["error"])[:200]}
    return {"ok": True, "id": external_id}


# ── YouTube (read + reply to comments on our channel's videos) ────────────────
# Free (YouTube Data API v3, 10k units/day). OAuth refresh token, youtube.force-ssl scope.
# external_id = the TOP-LEVEL comment id (which is the parentId for a reply).
_YT_TOKEN_URL = "https://oauth2.googleapis.com/token"
_YT_API = "https://www.googleapis.com/youtube/v3"
_yt_tok = {"tok": "", "exp": 0.0}
_yt_channel = {"id": ""}


def _yt_cfg() -> Dict[str, str]:
    g = lambda k: (os.getenv(k, "") or "").strip()
    return {"cid": g("YOUTUBE_CLIENT_ID"), "csec": g("YOUTUBE_CLIENT_SECRET"),
            "refresh": g("YOUTUBE_REFRESH_TOKEN"), "channel": g("YOUTUBE_CHANNEL_ID")}


def youtube_configured() -> bool:
    c = _yt_cfg()
    return bool(c["cid"] and c["csec"] and c["refresh"])


def _yt_token() -> str:
    if _yt_tok["tok"] and _yt_tok["exp"] > time.time() + 60:
        return _yt_tok["tok"]
    c = _yt_cfg()
    r = _http_json(_YT_TOKEN_URL, method="POST", data={
        "client_id": c["cid"], "client_secret": c["csec"],
        "refresh_token": c["refresh"], "grant_type": "refresh_token"})
    tok = r.get("access_token", "")
    if tok:
        _yt_tok["tok"] = tok
        _yt_tok["exp"] = time.time() + int(r.get("expires_in", 3600))
    else:
        log("YouTube token refresh failed:", r.get("error") or r)
    return tok


def _yt_channel_id(tok: str) -> str:
    if _yt_channel["id"]:
        return _yt_channel["id"]
    c = _yt_cfg()
    if c["channel"]:
        _yt_channel["id"] = c["channel"]
        return c["channel"]
    r = _http_json(f"{_YT_API}/channels?part=id&mine=true", headers={"Authorization": f"Bearer {tok}"})
    items = r.get("items") or []
    _yt_channel["id"] = items[0]["id"] if items else ""
    return _yt_channel["id"]


async def youtube_fetch_new(limit: int = 25) -> List[Dict[str, Any]]:
    if not youtube_configured():
        return []
    tok = _yt_token()
    if not tok:
        return []
    ch = _yt_channel_id(tok)
    if not ch:
        log("YouTube: no channel id resolved")
        return []
    r = _http_json(f"{_YT_API}/commentThreads?part=snippet&allThreadsRelatedToChannelId={ch}"
                   f"&order=time&maxResults={min(limit, 100)}&textFormat=plainText",
                   headers={"Authorization": f"Bearer {tok}"})
    if r.get("error"):
        log("YouTube comments read failed:", r["error"])
        return []
    out: List[Dict[str, Any]] = []
    for th in r.get("items", []):
        top = ((th.get("snippet") or {}).get("topLevelComment") or {})
        sn = top.get("snippet") or {}
        # skip our own channel's comments
        if ((sn.get("authorChannelId") or {}).get("value")) == ch:
            continue
        vid = sn.get("videoId")
        out.append({
            "platform": "youtube", "kind": "comment",
            "external_id": top.get("id"), "parent_id": vid,
            "author": sn.get("authorDisplayName") or "", "text": sn.get("textOriginal") or sn.get("textDisplay") or "",
            "permalink": (f"https://www.youtube.com/watch?v={vid}" if vid else None),
            "created_at": _parse_ts(sn.get("publishedAt")),
        })
    return out


async def youtube_post_reply(external_id: str, text: str) -> Dict[str, Any]:
    if not youtube_configured():
        return {"ok": False, "error": "youtube not configured"}
    tok = _yt_token()
    if not tok:
        return {"ok": False, "error": "youtube auth failed"}
    r = _http_json(f"{_YT_API}/comments?part=snippet", method="POST",
                   headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
                   data={"snippet": {"parentId": external_id, "textOriginal": text}})
    if r.get("error"):
        return {"ok": False, "error": str(r["error"])[:200]}
    return {"ok": True, "id": r.get("id")}


# ── NOA reply generator ───────────────────────────────────────────────────────
def _detect_lang(text: str) -> str:
    """he | ar | en — reply in the same language the customer wrote in."""
    t = text or ""
    if any("֐" <= c <= "׿" for c in t):
        return "he"
    if any("؀" <= c <= "ۿ" for c in t):
        return "ar"
    return "en"


_REPLY_SYS = {
    "he": ("את NOA, מנהלת הרשתות של AutoSpareFinder — פלטפורמת חלפים לרכב. "
           "עני לתגובה בעברית, בגובה העיניים, אנושי, קצר (1-2 משפטים), חברי ומקצועי. "
           "אם רלוונטי, הזמיני אותם לחפש את החלק לרכב שלהם באתר לפי מספר רישוי. "
           "אל תמציאי מחירים, מלאי או הבטחות. בלי האשטגים. בלי לחשוף מנגנונים פנימיים."),
    "ar": ("أنتِ NOA، مديرة وسائل التواصل لـ AutoSpareFinder — منصة قطع غيار السيارات. "
           "ردّي على التعليق بالعربية، بأسلوب إنساني ودود ومهني وقصير (جملة أو جملتين). "
           "إن كان مناسبًا، ادعيهم للبحث عن القطعة المناسبة لسيارتهم على الموقع برقم اللوحة. "
           "لا تختلقي أسعارًا أو مخزونًا أو وعودًا. بدون وسوم. بدون كشف أي تفاصيل داخلية."),
    "en": ("You are NOA, the social manager of AutoSpareFinder — a car-parts platform. "
           "Reply to the comment in English: human, warm, professional and short (1-2 sentences). "
           "Where relevant, invite them to find the right part for their car on the site by plate number. "
           "Never invent prices, stock or promises. No hashtags. Never reveal internal mechanics."),
}


async def draft_reply_text(item: Dict[str, Any]) -> str:
    """Draft NOA's reply to one inbound item. Returns '' if it can't (caller skips)."""
    text = (item.get("text") or "").strip()
    if not text:
        return ""
    lang = _detect_lang(text)
    try:
        from hf_client import hf_text
    except Exception:
        return ""
    author = item.get("author") or ""
    prompt = (f"Platform: {item.get('platform')}\n"
              f"Commenter: {author}\n"
              f"Their comment: {text}\n\n"
              "Write ONLY the reply text, nothing else.")
    try:
        out = await hf_text(prompt, system=_REPLY_SYS[lang], max_tokens=220, timeout=60.0)
    except Exception as e:
        log("draft_reply LLM failed:", str(e)[:120])
        return ""
    out = (out or "").strip().strip('"').strip()
    # keep replies tight and safe: single paragraph, no leaked hashtags/handles-only lines
    out = "\n".join(l for l in out.splitlines() if l.strip())
    return out[:600]


# ── reply cap + hand-off to a private support chat ────────────────────────────
# NOA does not run an endless public back-and-forth. She answers a customer a few times
# (NOA_ENGAGEMENT_MAX_REPLIES, default 3), and once that's reached she stops answering
# publicly and invites them to a private customer-service chat (the /api/v1/go channel
# picker → WhatsApp / Telegram / web chat).
_HANDOFF = {
    "he": ("שמח/ה לעזור! כדי להמשיך ולתת לך שירות מלא ומהיר יותר — זמינות, מחיר והזמנה — "
           "בוא/י נמשיך בצ׳אט השירות שלנו כאן: {url} 🙌"),
    "ar": ("يسعدني المساعدة! لمتابعة الأمر بشكل أسرع وأكمل — التوفر والسعر والطلب — "
           "لنكمل عبر دردشة خدمة العملاء لدينا هنا: {url} 🙌"),
    "en": ("Happy to help! To sort this out faster and in full — availability, price and order — "
           "let's continue on our support chat here: {url} 🙌"),
}


def _connect_url() -> str:
    return os.getenv("NOA_CONNECT_URL", "https://autosparefinder.co.il/api/v1/go?src=noa_reply")


def _handoff_text(lang: str) -> str:
    return _HANDOFF.get(lang, _HANDOFF["en"]).format(url=_connect_url())


async def _replied_count(db, platform: str, author: str) -> int:
    """How many times we've already SENT a reply to this customer on this platform."""
    if not author:
        return 0
    res = await db.execute(_sql("""
        SELECT count(*) FROM social_inbox
         WHERE platform = :p AND author = :a AND status = 'replied'
    """), {"p": platform, "a": author})
    return int(res.scalar() or 0)


async def _draft_or_handoff(db, item: Dict[str, Any]) -> str:
    """Normal NOA draft — unless we've already replied to this customer the max number of
    times, in which case hand off to a private support chat instead of continuing publicly."""
    limit = int(os.getenv("NOA_ENGAGEMENT_MAX_REPLIES", "3"))
    author = item.get("author") or ""
    if author and await _replied_count(db, item["platform"], author) >= limit:
        return _handoff_text(_detect_lang(item.get("text") or ""))
    return await draft_reply_text(item)


# ── orchestration: one polling pass ───────────────────────────────────────────
async def poll_once(db, *, per_platform_limit: int = 25, autoreply: bool = False) -> Dict[str, Any]:
    """
    One engagement pass across all configured platforms:
      fetch new comments → record (dedupe) → NOA drafts a reply → pending_approval
      (or auto-send if autoreply). Returns a summary dict for logging/observability.
    Fully graceful: a platform with no/expired creds contributes nothing and never raises.
    """
    await ensure_inbox_table(db)
    summary: Dict[str, Any] = {"platforms": {}, "new": 0, "drafted": 0, "auto_sent": 0, "errors": []}
    own_names = {(os.getenv("SOCIAL_PAGE_NAME", "") or "").lower().strip()}
    for platform, h in PLATFORMS.items():
        if not h["configured"]():
            summary["platforms"][platform] = "not_configured"
            continue
        try:
            items = await h["fetch"](per_platform_limit)
        except Exception as e:
            summary["errors"].append(f"{platform}:fetch:{str(e)[:100]}")
            summary["platforms"][platform] = "fetch_error"
            continue
        pnew = 0
        for it in items:
            if not it.get("external_id"):
                continue
            # skip our own replies (page commenting on itself)
            if (it.get("author") or "").lower().strip() in own_names and own_names != {""}:
                continue
            item_id = await record_item(db, it)
            await db.commit()
            if not item_id:
                continue  # already seen
            pnew += 1
            summary["new"] += 1
            reply = await _draft_or_handoff(db, it)
            if not reply:
                await mark_skipped(db, item_id)
                continue
            await set_draft(db, item_id, reply)
            summary["drafted"] += 1
            if autoreply:
                r = await send_reply(platform, str(it["external_id"]), reply)
                if r.get("ok"):
                    await mark_replied(db, item_id, r.get("id"))
                    summary["auto_sent"] += 1
                else:
                    summary["errors"].append(f"{platform}:reply:{r.get('error')}")
        summary["platforms"][platform] = f"ok:{pnew}new"
    return summary


async def draft_new_items(db, *, limit: int = 20, autoreply: bool = False) -> Dict[str, Any]:
    """Draft replies for inbox rows recorded as 'new' by a WEBHOOK (e.g. Telegram) rather
    than by poll_once. Same outcome: draft → pending_approval (or auto-send)."""
    await ensure_inbox_table(db)
    res = await db.execute(_sql("""
        SELECT id::text, platform, external_id, author, message
          FROM social_inbox WHERE status = 'new'
         ORDER BY created_at LIMIT :lim
    """), {"lim": limit})
    rows = res.fetchall()
    summary: Dict[str, Any] = {"drafted": 0, "auto_sent": 0, "errors": []}
    for r in rows:
        m = r._mapping
        reply = await _draft_or_handoff(db, {"platform": m["platform"], "author": m["author"], "text": m["message"]})
        if not reply:
            await mark_skipped(db, m["id"])
            continue
        await set_draft(db, m["id"], reply)
        summary["drafted"] += 1
        if autoreply:
            rr = await send_reply(m["platform"], str(m["external_id"]), reply)
            if rr.get("ok"):
                await mark_replied(db, m["id"], rr.get("id"))
                summary["auto_sent"] += 1
            else:
                summary["errors"].append(f'{m["platform"]}:{rr.get("error")}')
    return summary


# ── registry ──────────────────────────────────────────────────────────────────
PLATFORMS = {
    "facebook":  {"configured": fb_configured,       "fetch": fb_fetch_new,       "reply": fb_post_reply},
    "instagram": {"configured": ig_configured,       "fetch": ig_fetch_new,       "reply": ig_post_reply},
    "telegram":  {"configured": telegram_configured, "fetch": telegram_fetch_new, "reply": telegram_post_reply},
    "reddit":    {"configured": reddit_configured,   "fetch": reddit_fetch_new,   "reply": reddit_post_reply},
    "discord":   {"configured": discord_configured,  "fetch": discord_fetch_new,  "reply": discord_post_reply},
    "google_business": {"configured": gbp_configured, "fetch": gbp_fetch_new,     "reply": gbp_post_reply},
    "youtube":   {"configured": youtube_configured,  "fetch": youtube_fetch_new,  "reply": youtube_post_reply},
}


def configured_platforms() -> List[str]:
    return [p for p, h in PLATFORMS.items() if h["configured"]()]


def _parse_ts(s: str | None):
    if not s:
        return datetime.utcnow()
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S+0000"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=None)
        except Exception:
            continue
    return datetime.utcnow()
