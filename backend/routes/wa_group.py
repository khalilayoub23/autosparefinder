"""
Script:  routes/wa_group.py
Purpose: Admin API behind the group @mention composer: list the REAL participants of the updates group (read-only) and send
         a message whose mentions are real WhatsApp mention JIDs.
Process: members -> bridge GET /groups/<jid>/participants (live metadata, 60 s cache); each participant gets an opaque
         HMAC token, so the UI never sees phone numbers/JIDs. send -> the UI sends text containing `@{token}` placeholders;
         the server re-reads the participants, maps tokens -> JIDs, rewrites each placeholder to `@<digits>` (what WhatsApp
         renders as the mention) and passes `mentions=[jid,...]` to the bridge, which re-validates against the group.
Data Imported/Modified: none (sends one WhatsApp message on explicit admin request).
Data Sources: whatsapp-bridge /groups/<jid>/participants. Group JID: the configured updates group (never guessed).
Missing Data Delegation: n/a.
Last Updated: 2026-11-25

Switch: WA_GROUP_COMPOSER_ENABLED=1 (default OFF) - both endpoints answer 403 until enabled.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import time
from typing import List

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from BACKEND_AUTH_SECURITY import get_current_admin_user

router = APIRouter()
_BRIDGE = os.getenv("WHATSAPP_BRIDGE_URL", "http://whatsapp-bridge:3001/send").replace("/send", "")
_CACHE: dict = {"at": 0.0, "jid": "", "data": None}
_CACHE_TTL_S = 60
_PLACEHOLDER = re.compile(r"@\{([0-9a-f]{16})\}")


def _enabled() -> None:
    if os.getenv("WA_GROUP_COMPOSER_ENABLED", "0") != "1":
        raise HTTPException(status_code=403, detail="group composer disabled")


def _token(jid: str) -> str:
    key = (os.getenv("JWT_SECRET_KEY") or "x").encode()
    return hmac.new(key, jid.encode(), hashlib.sha256).hexdigest()[:16]


async def _group_jid() -> str:
    from BACKEND_API_ROUTES import _updates_group_jid
    # Explicit composer group first (never guessed), else the configured updates group.
    jid = os.getenv("WA_COMPOSER_GROUP_JID", "").strip() or await _updates_group_jid()
    if not jid.endswith("@g.us"):
        raise HTTPException(status_code=409, detail="no updates group configured")
    return jid


async def _participants(jid: str, force: bool = False) -> dict:
    if not force and _CACHE["data"] and _CACHE["jid"] == jid and time.monotonic() - _CACHE["at"] < _CACHE_TTL_S:
        return _CACHE["data"]
    async with httpx.AsyncClient(timeout=20.0) as c:
        r = await c.get(f"{_BRIDGE}/groups/{jid}/participants")
    data = r.json()
    if not data.get("ok"):
        raise HTTPException(status_code=502, detail="participants unavailable")
    _CACHE.update(at=time.monotonic(), jid=jid, data=data)
    return data


def shape_members(data: dict, own_ids: List[str]) -> List[dict]:
    """UI-safe members: opaque token + display name only. Our own account is not a mention target."""
    out = []
    for i, p in enumerate(data.get("participants") or [], 1):
        jid = p.get("jid") or ""
        if not jid or jid.split("@")[0].split(":")[0] in own_ids:
            continue
        out.append({"token": _token(jid), "name": (p.get("name") or "").strip() or f"משתתף {i}", "admin": bool(p.get("admin"))})
    return out


def _own_ids() -> List[str]:
    return [x for x in (re.sub(r"\D", "", os.getenv("WHATSAPP_ACCOUNT_NUMBER", "972532426920")),) if x]


@router.get("/api/v1/admin/whatsapp/group-members")
async def group_members(refresh: bool = False, _=Depends(get_current_admin_user)):
    _enabled()
    jid = await _group_jid()
    data = await _participants(jid, force=refresh)
    return {"subject": data.get("subject", ""), "members": shape_members(data, _own_ids())}


class GroupMessage(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


def render_mentions(text: str, data: dict) -> tuple[str, List[str]]:
    """`@{token}` placeholders -> `@<digits>` + the participant JIDs. An unknown token is an error, never ignored."""
    by_token = {_token(p["jid"]): p["jid"] for p in data.get("participants") or [] if p.get("jid")}
    jids: List[str] = []

    def sub(m):
        jid = by_token.get(m.group(1))
        if jid is None:
            raise ValueError("unknown or removed participant")
        if jid not in jids:
            jids.append(jid)
        return "@" + jid.split("@")[0].split(":")[0]

    return _PLACEHOLDER.sub(sub, text), jids


@router.post("/api/v1/admin/whatsapp/group-message")
async def send_group_message(body: GroupMessage, _=Depends(get_current_admin_user)):
    _enabled()
    jid = await _group_jid()
    data = await _participants(jid, force=True)
    try:
        text, jids = render_mentions(body.text, data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    from social.whatsapp_provider import send_message
    res = await send_message(to=jid, text=text, mentions=jids)
    if not res.get("ok"):
        raise HTTPException(status_code=502, detail=res.get("error") or "send failed")
    return {"ok": True, "mentions": len(jids)}
