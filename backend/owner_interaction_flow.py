"""
Script:  owner_interaction_flow.py
Purpose: The webhook side of owner interactions (routes/webhooks.py calls these; kept here so the whole path - tap,
         prompt, follow-up binding, continuation, revised proposal with new buttons - is testable without FastAPI).
Process:
  handle_tap()        a button/list tap from the owner's PRIVATE chat -> owner_interactions.resolve_response:
                        execute  -> returns the typed command; the caller runs it through the owner console as if typed
                        followup -> runs the read-only prompt command, sends it + the hint, records the prompt message id
                                    (a quote-reply to it later binds exactly this interaction)
                        reply    -> sends the refusal text (expired / duplicate / superseded)
  handle_owner_text() the owner's next private text -> owner_interactions.bind_followup:
                        bound    -> runs the continuation through the ORIGINATING agent (agents/owner_continuations.run);
                                    on failure the interaction is released (buttons usable again) and the owner is told;
                                    on success the revised proposal is sent with a choice trailer -> NEW buttons
                        ambiguous / cancelled -> one explanatory reply, nothing executed
Data Imported/Modified: via owner_interactions_store and the agents' own handlers.
Data Sources: none. Missing Data Delegation: n/a. Last Updated: 2026-11-26
"""
from __future__ import annotations

import os
from typing import Any, Awaitable, Callable, Optional

import owner_interactions as oi

Send = Callable[..., Awaitable[dict]]


def _key_id(res: Any) -> str:
    return str(((res or {}).get("key") or {}).get("id") or "") if isinstance(res, dict) else ""


async def handle_tap(store, ir: dict, *, sender_is_owner: bool, owner_dest: str, send: Send,
                     console: Callable[[str], Awaitable[str]]) -> Optional[str]:
    """Returns the command to run as typed (status execute), else None (already answered or ignored)."""
    res = await oi.resolve_response(store, ir, sender_is_owner=sender_is_owner)
    if res.status == "execute":
        return res.command
    if res.status == "followup":
        prompt = (await console(res.command) or "").strip()
        sent = await send(to=owner_dest, text=f"{prompt}\n\n{res.reply}" if prompt else res.reply)
        mid = _key_id(sent)
        if mid:
            await store.set_prompt_message_id(res.interaction_id, mid)
        await store.event(res.interaction_id, "followup_prompt_sent", {"ok": bool((sent or {}).get("ok")), "message_id": bool(mid)})
        return None
    if res.status == "reply" and res.reply:
        await send(to=owner_dest, text=res.reply)
    return None


async def handle_owner_text(store, text: str, quoted_message_id: str, *, owner_dest: str, send: Send,
                            run_continuation: Callable[[str], Awaitable[Any]]) -> bool:
    """True = handled here (bound / ambiguous / cancelled / duplicate); False = normal console routing."""
    b = await oi.bind_followup(store, text, quoted_message_id)
    if b is None:
        return False
    if b.status in ("ambiguous", "cancelled"):
        await send(to=owner_dest, text=b.reply)
        return True
    if b.status == "duplicate":
        return True
    ix = b.interaction
    err = ""
    from agents.owner_continuations import FROM_BUTTON_FOLLOWUP
    _tok = FROM_BUTTON_FOLLOWUP.set(True)   # a button follow-up: the "own wording" path may recognise notes (#158)
    try:
        result = await run_continuation(b.command)
    except Exception as exc:  # the agent failed: nothing changed, the request stays usable
        result, err = None, str(exc)[:120]
    finally:
        FROM_BUTTON_FOLLOWUP.reset(_tok)
    if result is None or not getattr(result, "ok", False):
        await store.release_followup(ix.id)
        await store.event(ix.id, "followup_failed", {"error": err or (getattr(result, "text", "") or "")[:120]})
        msg = (getattr(result, "text", "") if result is not None else f"⚠️ {err}") or "⚠️ הפעולה נכשלה."
        await send(to=owner_dest, text=f"{msg}\n\nהבקשה {ix.task_ref or ix.id} עדיין פתוחה — אפשר ללחוץ שוב על הכפתור.")
        return True
    await store.event(ix.id, "followup_completed", {"verb": (ix.continuation or {}).get("verb", "")})
    rendered = result.render()
    await send(to=owner_dest, text=rendered)
    try:   # the console must know what just happened in this thread (#158)
        from agents.owner_console import record_exchange
        await record_exchange(text, rendered, (ix.continuation or {}).get("agent", "") or ix.agent, ix.task_ref or ix.id)
    except Exception:
        pass
    return True


async def run_continuation(command: str):
    """Production runner: the originating agent's handler with its own catalog-DB session."""
    from BACKEND_DATABASE_MODELS import async_session_factory
    from agents import owner_continuations as oc
    async with async_session_factory() as db:
        return await oc.run(command, db)


def pg_store() -> Any:
    from BACKEND_DATABASE_MODELS import pii_session_factory
    from owner_interactions_store import PgStore
    return PgStore(pii_session_factory)


def group_inbound_enabled() -> bool:
    return os.getenv("WA_GROUP_INBOUND_ENABLED", "0") == "1"
