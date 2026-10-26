"""
Script: chat_requests.py
Purpose: Durable lifecycle record for every customer chat message (WhatsApp, Telegram) the AI agent answers, so a
         request survives a slow agent, a transport outage and a backend restart, and "answer generated",
         "saved in conversation history" and "delivered" are never conflated (FIXES_TRACKER #73, #74).
Process:
  record   Redis hash chat_req:<rid> (rid = "wa:<message id>" | "tg:<update id>"; Redis runs AOF; 2-day TTL);
           open ids in the set chat_req:open. Created BEFORE the agent task starts.
           status      received → processing → answered | failed  (ambiguous = outcome not yet reconciled)
           reason      deadline | exception:<Type> | empty_answer | malformed_answer | interrupted | unreconciled
           delivery    none → sending → sent | retrying | failed | unknown   (+ delivered_kind answer|failure)
           history     the authoritative history (PII `messages`) row of the outcome: pending | synced | failed
           owner       BOOT_ID of the process working on it; liveness = the owner's heartbeat key
  liveness heartbeat key chat_req:boot:<BOOT_ID> (TTL HEARTBEAT_TTL_S) refreshed by the recovery loop: a request
           whose owner has no heartbeat was left by a dead process. take_over() is an atomic compare-and-set, so
           concurrent recoverers (several workers, overlapping loops) never both take one request.
  classify a transport result is "sent", "not_sent" (provably nothing left → safe to retry), "invalid"/"rejected"
           (permanent) or "unknown" (timeout / 5xx — may have been delivered → never retried, no duplicates).
Data Imported/Modified: Redis keys chat_req:* only. Logs carry the request id and the last 4 digits only.
Last Updated: 2026-10-21
"""
from __future__ import annotations

import os
import time
import uuid
import contextvars
from typing import Any, Dict, Optional

BOOT_ID = uuid.uuid4().hex[:12]
# Set inside the agent's own task for one chat request (routes/webhooks._run_agent_for), read by side-effecting
# agent code (BACKEND_AI_AGENTS.create_checkout_link) and the history loader (#75).
CURRENT_REQUEST: contextvars.ContextVar = contextvars.ContextVar("chat_request_id", default="")
REUSE_USER_ROW: contextvars.ContextVar = contextvars.ContextVar("chat_reuse_user_row", default="")
KEY = "chat_req:{}"
OPEN = "chat_req:open"
BOOT_KEY = "chat_req:boot:{}"
TTL_S = 2 * 86400
HEARTBEAT_TTL_S = int(os.getenv("CHAT_REQ_HEARTBEAT_TTL_S", "45"))
MAX_ATTEMPTS = int(os.getenv("CHAT_REQ_MAX_ATTEMPTS", "2"))
MAX_AGE_S = int(os.getenv("CHAT_REQ_RECOVER_MAX_AGE_S", "900"))
MAX_RECONCILE = int(os.getenv("CHAT_REQ_MAX_RECONCILE", "5"))
MAX_HISTORY_ATTEMPTS = int(os.getenv("CHAT_REQ_MAX_HISTORY_ATTEMPTS", "10"))
DELIVERY_RETRY_DELAYS_S = [float(x) for x in os.getenv("CHAT_DELIVERY_RETRY_DELAYS_S", "30,120,300").split(",")
                           if x.strip()]
OPEN_STATUSES = ("received", "processing", "ambiguous")

_WA_NOT_SENT = ("bridge unreachable", "WhatsApp socket not created yet", "WhatsApp socket connecting",
                "WhatsApp disconnected", "WhatsApp socket closed", "not authenticated",
                "Missing params or socket not ready")

# compare-and-set owner: only the caller that saw the expected (dead) owner takes the request
_TAKE_OVER = """
local cur = redis.call('HGET', KEYS[1], 'owner')
if cur == false then return 0 end
if cur ~= ARGV[1] then return 0 end
redis.call('HSET', KEYS[1], 'owner', ARGV[2], 'updated_at', ARGV[3])
return 1
"""


def _s(v) -> str:
    return v.decode() if isinstance(v, bytes) else v


def classify_whatsapp(result: Dict[str, Any]) -> str:
    if (result or {}).get("ok"):
        return "sent"
    err = str((result or {}).get("error") or "")
    if err.startswith("INVALID_PAYLOAD"):
        return "invalid"
    if any(m in err for m in _WA_NOT_SENT):
        return "not_sent"
    return "unknown"


def classify_telegram(result: Dict[str, Any]) -> str:
    if (result or {}).get("ok"):
        return "sent"
    if (result or {}).get("not_sent"):
        return "not_sent"
    code = int((result or {}).get("status_code") or 0)
    if 400 <= code < 500:
        return "rejected"                 # bad request / bot blocked — permanent, nothing delivered
    return "unknown"


async def heartbeat(r) -> None:
    if r is not None:
        await r.set(BOOT_KEY.format(BOOT_ID), "1", ex=HEARTBEAT_TTL_S)


async def owner_alive(r, owner: Optional[str]) -> bool:
    if not owner:
        return False
    if owner == BOOT_ID:
        return True
    return bool(await r.exists(BOOT_KEY.format(owner)))


async def create(r, rid: str, **fields) -> bool:
    """False when a record for rid already exists (a redelivery) — it is never overwritten."""
    if r is None:
        return True
    if not await r.hsetnx(KEY.format(rid), "rid", rid):
        return False
    now = str(time.time())
    data = {"status": "received", "attempts": "0", "delivery": "none", "delivery_attempts": "0",
            "reconcile_attempts": "0", "history": "none", "history_attempts": "0",
            "created_at": now, "updated_at": now, "owner": BOOT_ID}
    data.update({k: ("" if v is None else str(v)) for k, v in fields.items()})
    pipe = r.pipeline()
    pipe.hset(KEY.format(rid), mapping=data)
    pipe.expire(KEY.format(rid), TTL_S)
    pipe.sadd(OPEN, rid)
    pipe.set(BOOT_KEY.format(BOOT_ID), "1", ex=HEARTBEAT_TTL_S)    # the owner is visibly alive from the start
    await pipe.execute()
    return True


async def update(r, rid: str, **fields) -> None:
    if r is None:
        return
    fields["updated_at"] = time.time()
    await r.hset(KEY.format(rid), mapping={k: ("" if v is None else str(v)) for k, v in fields.items()})


async def get(r, rid: str) -> Optional[Dict[str, str]]:
    if r is None or not rid:
        return None
    d = await r.hgetall(KEY.format(rid))
    return {_s(k): _s(v) for k, v in d.items()} if d else None


async def incr(r, rid: str, field: str) -> int:
    if r is None:
        return 0
    return int(await r.hincrby(KEY.format(rid), field, 1))


async def close(r, rid: str) -> None:
    if r is not None:
        await r.srem(OPEN, rid)
        await r.hset(KEY.format(rid), mapping={"closed_at": str(time.time())})


async def take_over(r, rid: str, expected_owner: str) -> bool:
    if r is None:
        return False
    return bool(await r.eval(_TAKE_OVER, 1, KEY.format(rid), expected_owner or "", BOOT_ID, str(time.time())))


async def open_requests(r):
    if r is None:
        return []
    out = []
    for rid in await r.smembers(OPEN):
        rid = _s(rid)
        rec = await get(r, rid)
        if rec is None:
            await r.srem(OPEN, rid)
            continue
        out.append((rid, rec))
    return out


def summary(rid: str, rec: Dict[str, str]) -> str:
    """Log-safe one-liner: request id and states, recipient's last 4 digits only."""
    return (f"rid={rid} status={rec.get('status')} reason={rec.get('reason') or '-'} attempts={rec.get('attempts')} "
            f"delivery={rec.get('delivery')} kind={rec.get('delivered_kind') or '-'} history={rec.get('history')} "
            f"to=...{str(rec.get('target') or '')[-4:]}")
