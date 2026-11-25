"""
Script:  maintenance/wa_interactive_selftest.py
Purpose: ONE minimal, side-effect-free WhatsApp Reply-Button test addressed ONLY to the owner's private chat, for the real-device
         verification of the interactive layer (owner_interactions.py). Default is a DRY RUN that prints what would be sent.
Process: 1. refuses to send unless `--send` AND `--owner-approved <ref>` are given (an explicit owner authorization) and
            WA_INTERACTIVE_ENABLED=1 in this process; 2. persists a SELFTEST interaction (אישור / דחייה / שינוי, inert commands
            `בדיקתאישור|בדיקתדחייה|בדיקתשינוי <ref>`) BEFORE sending; 3. sends one plain text + the buttons to OWNER_WHATSAPP_PHONE;
            4. `--verify <interaction_id>` reads the audit trail and the consumed action (no WhatsApp traffic).
Data Imported/Modified: PII DB owner_interactions / owner_interaction_events (one SELFTEST row) - only with --send.
Data Sources: none. Missing Data Delegation: n/a. Last Updated: 2026-11-25
Prerequisites (deployment, owner approval): migration 0043 applied, bridge running the interactive.js build with WA_INTERACTIVE_ENABLED=1,
backend with WA_INTERACTIVE_ENABLED=1. Nothing here executes an agent action: the commands only return an acknowledgement text.
"""
import argparse
import asyncio
import os
import secrets
import sys


async def _verify(iid: str) -> int:
    from sqlalchemy import text
    from BACKEND_DATABASE_MODELS import pii_session_factory
    async with pii_session_factory() as s:
        row = (await s.execute(text("SELECT id,agent,state,consumed_action,wa_message_id FROM owner_interactions WHERE id=:i"), {"i": iid})).mappings().first()
        ev = (await s.execute(text("SELECT kind, detail, at FROM owner_interaction_events WHERE interaction_id=:i ORDER BY at"), {"i": iid})).mappings().all()
    print("interaction:", dict(row) if row else None)
    for e in ev:
        print(" event:", e["kind"], e["detail"], e["at"])
    return 0 if row and row["state"] == "consumed" else 1


async def _main(a) -> int:
    if a.verify:
        return await _verify(a.verify)
    import owner_interactions as oi
    ref = "T" + secrets.token_hex(3)
    choices = [("אישור", f"בדיקתאישור {ref}"), ("דחייה", f"בדיקתדחייה {ref}"), ("שינוי", f"בדיקתשינוי {ref}")]
    ix = oi.build(choices, task_ref=ref)
    print("agent:", ix.agent, "| actions:", [(x.id, x.label) for x in ix.actions], "| ref:", ref)
    if not a.send:
        print("DRY RUN - nothing persisted or sent. Re-run with --send --owner-approved <ref> after explicit owner authorization.")
        return 0
    if not a.owner_approved or not oi.enabled():
        print("REFUSED: needs --owner-approved <ref> and WA_INTERACTIVE_ENABLED=1", file=sys.stderr)
        return 2
    owner = os.getenv("OWNER_WHATSAPP_PHONE", "")
    if not owner:
        print("REFUSED: OWNER_WHATSAPP_PHONE not set", file=sys.stderr)
        return 2
    from BACKEND_DATABASE_MODELS import pii_session_factory
    from owner_interactions_store import PgStore
    from social.whatsapp_provider import _post_send, _normalize_bridge_phone
    store = PgStore(pii_session_factory)
    ix = await oi.create_from_choices(store, choices, task_ref=ref)          # persisted FIRST
    await _post_send({"to": _normalize_bridge_phone(owner), "text": f"🧪 בדיקת כפתורים ({ref}) — לא מבצעת שום פעולה. הקש על אחד הכפתורים למטה."}, 15.0)
    data = await _post_send({"to": _normalize_bridge_phone(owner), "interactive": oi.bridge_payload(ix, "בדיקת כפתורים — בחר:")}, 15.0)
    print("bridge:", {k: data.get(k) for k in ("ok", "error", "interactive")})
    mid = (data.get("key") or {}).get("id")
    if mid:
        await store.set_wa_message_id(ix.id, mid)
    print("interaction_id:", ix.id, "-> after the tap run: --verify", ix.id)
    return 0 if data.get("ok") else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--owner-approved", default="")
    ap.add_argument("--verify", default="")
    sys.exit(asyncio.run(_main(ap.parse_args())))
