"""
Script: routes/email_agent_routes.py
Purpose: Read-only observability endpoint for the Email Agent: configuration state
         (disabled / unconfigured / configured - presence only, never secret values), the last
         loop cycle, and counts + the most recent audit rows from email_agent_messages.
         It cannot trigger processing, create a draft or send anything.
Process: GET /api/v1/system/email-agent  (header X-Collect-Secret, fail-closed).
Data Imported/Modified: none (reads email_agent_messages).
Data Sources: PII DB.
Last Updated: 2026-10-04
"""
import hmac
import os

from fastapi import APIRouter, Header, HTTPException

router = APIRouter()


@router.get("/api/v1/system/email-agent")
async def email_agent_status(x_collect_secret: str = Header(default="")):
    secret = os.getenv("COLLECT_SECRET", "")
    if not secret or not hmac.compare_digest(x_collect_secret.encode(), secret.encode()):
        raise HTTPException(status_code=403, detail="forbidden")
    from email_agent import loop as _loop
    from email_agent.config import load_config
    from email_agent.redaction import safe_error
    from email_agent.store import PgStore
    out = {"config": load_config().public(), "send_enabled": False, "last_cycle": dict(_loop.last_cycle)}
    try:
        store = PgStore()
        out["store"] = await store.stats()
        out["recent"] = await store.recent(20)
    except Exception as e:  # e.g. migration 0039 not applied yet
        out["store_error"] = safe_error(e, 200)
    return out
