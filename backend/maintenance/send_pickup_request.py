#!/usr/bin/env python3
"""
Script:  maintenance/send_pickup_request.py
Purpose: Ask ONE supplier, through the authorized NIR outreach path, for its real dispatch / collection address
         (Eurosender allowlist rule: a registered office, a newsletter footer or a signature is never a pickup address).
Process: 1. the recipient must be a contact already stored on the supplier (suppliers.credentials.contacts, written by
            the email intake from an AUTHENTICATED reply, or a VERIFIED contact of the NIR qualification record);
         2. supplier_outreach.plan() -> fixed `pickup_address` template (scope-checked, placeholder-checked, idempotent,
            max-attempt rules) -> send_outreach() (standing owner `routine_inquiry` authorization, draft read-back, one send).
         Prepares the row (state READY_TO_CONTACT) and stops unless --send; re-running with --send sends that row. Never an order, offer or commitment.
Data Imported/Modified: supplier_outreach (PII DB) row; one Gmail message sent from the business mailbox.
Last Updated: 2026-11-20
"""
import asyncio
import json
import os
import sys

import asyncpg

import supplier_outreach as so
import supplier_qualification as Q


async def main(prefix: str, send: bool) -> int:
    c = await asyncpg.connect(os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://"))
    row = await c.fetchrow("SELECT id::text id, name, credentials FROM suppliers WHERE id::text LIKE $1 || '%'", prefix)
    await c.close()
    if not row:
        print("supplier not found"); return 2
    cred = json.loads(row["credentials"]) if isinstance(row["credentials"], str) else (row["credentials"] or {})
    contacts = [x for x in cred.get("contacts", []) if x.get("authenticated_sender") and Q.valid_email(x.get("email"))]
    contacts += [x for x in Q.verified_contacts(cred.get("qualification") or Q.new_record(row["id"])) if x not in contacts]
    if not contacts:
        print("no usable contact on", row["name"]); return 3
    addr = (contacts[0].get("email") or contacts[0].get("address"))
    rec = Q.new_record(row["id"])
    rec["requirements"]["contacts"]["details"] = {"contacts": [{"address": addr, "status": Q.VERIFIED,
                                                               "source": contacts[0].get("evidence") or "stored contact"}]}
    store = so.PgOutreachStore()
    plan = await so.plan(store, row["id"], rec, purposes=["pickup_address"])
    if plan.get("action") == "skip" and "already prepared" in plan.get("reason", ""):
        # an earlier (dry) run already prepared the row: send exactly that one, never a second
        ready = [r for r in await store.for_supplier(row["id"]) if r["state"] == "READY_TO_CONTACT"][-1]
        plan = {"action": "prepared", "outreach": ready, "followup": bool(ready.get("parent_id")),
                **so.render_inquiry(list(ready["purposes"]), followup=bool(ready.get("parent_id")))}
    print(row["name"], "->", addr, "plan:", plan.get("action"), plan.get("reason", ""))
    if plan.get("action") != "prepared" or not send:
        return 0
    from email_agent.config import load_config
    from email_agent.gmail_client import GmailClient
    from supplier_authorization import PgAuthStore
    cfg = load_config()
    res = await so.send_outreach(store, PgAuthStore(), GmailClient(cfg), plan, sender=cfg.mailbox)
    print(res)
    return 0 if res.get("sent") else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1], "--send" in sys.argv)))
