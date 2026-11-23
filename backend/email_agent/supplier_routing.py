"""
Script: email_agent/supplier_routing.py
Purpose: Give every relevant supplier email an owner (owner task 2026-10-27, section 4.1). Runs after each armed
         Email Agent cycle and BEFORE owner_flow, so the owner alert and the task name the right owner.
Process (per processed email not routed yet; idempotent via supplier_routed_at):
  0. reply in one of OUR purchase-order email threads, sender domain == the PO recipient's -> owner BOAZ
        (task kind procurement_reply); a different domain -> EXCEPTION.
  1. reply in one of NIR's outreach threads, sender domain == the inquiry's recipient domain
        -> owner NIR: outreach REPLY_RECEIVED, one NIR task carrying the email ref, outreach id, supplier id,
           thread id and source folder (inbox | spam). Content stays in the PII email row (`מייל <ref>`).
  2. a NEW thread from the domain of exactly one inquiry awaiting a reply -> same as 1.
  3. thread match but a different sender domain, or several awaiting inquiries for that domain
        -> EXCEPTION: requires_human, no guess, no supplier attached.
  4. otherwise mail from a known supplier (classifier 'supplier' / AliExpress / eBay / Eurosender)
        -> owner BOAZ (operational supplier mail; owner_flow files the BOAZ task when it is important)
  5. anything else -> no supplier owner (normal owner_flow).
  The email's content is untrusted: nothing in it is executed, and a supplier's claims are recorded by NIR as
  claims (supplier_qualification.record_supplier_claim), never as verified evidence.
Data Imported/Modified: email_agent_messages.supplier_route / supplier_routed_at / requires_human (PII DB);
         supplier_outreach state (PII DB); agent_todos (catalog DB, one NIR task per routed reply).
Last Updated: 2026-10-27
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict

import supplier_outreach as _out
from email_agent import supplier_intake as _intake
from email_agent.senders import FREE_MAIL

SUPPLIER_CATEGORIES = ("supplier", "aliexpress", "ebay", "shipping_eurosender")


def _domain(addr: str) -> str:
    a = (addr or "").strip().lower()
    return a.rsplit("@", 1)[-1] if "@" in a else ""


def _same_party(sender: str, recipient: str) -> bool:
    """The sender is the party we wrote to. A business domain identifies the company; a FREE-MAIL domain (gmail.com, ...)
    identifies nobody, so only the exact address counts. 2026-11-23: our own mailbox (gmail.com) matched an inquiry sent
    to a gmail.com supplier and was recorded as that supplier's REPLY."""
    s_, r_ = (sender or "").strip().lower(), (recipient or "").strip().lower()
    if not s_ or not r_:
        return False
    if s_ == r_:
        return True
    return _domain(s_) == _domain(r_) and _domain(s_) not in FREE_MAIL


def _is_own_mailbox(addr: str) -> bool:
    from email_agent.config import DEFAULT_MAILBOX
    own = {DEFAULT_MAILBOX.lower(), (os.getenv("EMAIL_AGENT_MAILBOX") or "").strip().lower()} - {""}
    return (addr or "").strip().lower() in own


def _ref(row: Dict[str, Any]) -> str:
    return str(row.get("id", ""))[:8]


async def _to_nir(store, outreach_store, todos, row, o, how: str) -> Dict[str, Any]:
    await _out.record_reply(outreach_store, o["id"], row["gmail_message_id"])
    folder = row.get("source_folder") or "inbox"
    tid = await todos.create(
        "NIR", f"תשובת ספק לפנייה ({folder})",
        f"Supplier reply to NIR inquiry {o['id'][:8]} (email ref {_ref(row)}, {how}). Review the supplier's "
        "claims against evidence and update the qualification record; claims stay UNVERIFIED until checked.",
        "medium",
        {"source": "email_agent", "kind": "supplier_reply", "email_ref": _ref(row), "outreach_id": o["id"],
         "supplier_id": o["supplier_id"], "gmail_thread_id": row.get("gmail_thread_id"), "source_folder": folder})
    prior = row.get("task_ids") or []
    prior = json.loads(prior) if isinstance(prior, str) else list(prior)
    await store.set_task_ids(row["id"], prior + [tid])
    route = {"owner": "NIR", "outreach_id": o["id"], "supplier_id": o["supplier_id"], "match": how,
             "task_id": tid, "source_folder": folder}
    await store.set_supplier_route(row["id"], route)
    return route


async def _to_boaz_procurement(store, todos, row, proc) -> Dict[str, Any]:
    tid = await todos.create(
        "BOAZ", "תשובת ספק להזמנת רכש",
        f"Supplier reply to purchase order {proc['id'][:8]} (email ref {_ref(row)}). A confirmation, price change or "
        "stock-out is recorded by the owner - BOAZ never accepts changed terms.", "high",
        {"source": "email_agent", "kind": "procurement_reply", "email_ref": _ref(row), "procurement_id": proc["id"],
         "gmail_thread_id": row.get("gmail_thread_id"), "source_folder": row.get("source_folder") or "inbox"})
    prior = row.get("task_ids") or []
    prior = json.loads(prior) if isinstance(prior, str) else list(prior)
    await store.set_task_ids(row["id"], prior + [tid])
    route = {"owner": "BOAZ", "procurement_id": proc["id"], "task_id": tid, "match": "purchase_order_thread"}
    await store.set_supplier_route(row["id"], route)
    return route


async def _to_known_supplier(store, todos, row, sup, res) -> Dict[str, Any]:
    """A reply from a KNOWN supplier (matched by registered domain) that no outreach row covers (a manual inquiry,
    e.g. SNG Barratt's shipping question). The extracted facts are already persisted by the intake; the owner agent
    gets ONE task naming the supplier and what was stored. A NIR candidate -> NIR, an operating supplier -> BOAZ."""
    owner = "NIR" if (sup.get("source") == "nir_sourcing") else "BOAZ"
    f = res.get("facts") or {}
    tid = await todos.create(
        owner, f"תשובת ספק: {sup.get('name', '')[:40]}",
        f"Reply from {sup.get('name')} (email ref {_ref(row)}). Stored in the supplier record: contact "
        f"{f.get('name')} / {f.get('role')} / {f.get('phone')}; carriers {f.get('carriers')}; transit "
        f"{f.get('transit_days_min')}-{f.get('transit_days_max')} days; rates variable: {f.get('rates_variable')}. "
        f"Sender authenticated: {res.get('authenticated')}. Claims stay UNVERIFIED; a pickup address was NOT stated.",
        "medium",
        {"source": "email_agent", "kind": "supplier_reply_known", "email_ref": _ref(row), "supplier_id": sup["id"],
         "gmail_thread_id": row.get("gmail_thread_id"), "source_folder": row.get("source_folder") or "inbox"})
    prior = row.get("task_ids") or []
    prior = json.loads(prior) if isinstance(prior, str) else list(prior)
    await store.set_task_ids(row["id"], prior + [tid])
    route = {"owner": owner, "supplier_id": sup["id"], "match": "supplier_domain", "task_id": tid,
             "facts_stored": bool(res.get("stored")), "authenticated": bool(res.get("authenticated")),
             "source_folder": row.get("source_folder")}
    await store.set_supplier_route(row["id"], route)
    return route


async def route_supplier_replies(store, outreach_store, todos, limit: int = 50,
                                 procurement_store=None, intake=None) -> Dict[str, int]:
    out = {"to_nir": 0, "to_boaz": 0, "exceptions": 0, "other": 0}
    for row in await store.supplier_route_pending(limit):
        dom = _domain(row.get("sender_email") or "")
        if _is_own_mailbox(row.get("sender_email") or ""):      # our own notifications are never a supplier's reply
            await store.set_supplier_route(row["id"], {"owner": None, "reason": "own mailbox"})
            out["other"] += 1
            continue
        # a reply to one of OUR purchase-order emails (supplier_ordering.EmailPurchaseOrderConnector) -> BOAZ
        procs = await procurement_store.by_thread(row.get("gmail_thread_id") or "") if (
            procurement_store is not None and row.get("gmail_thread_id")) else []
        if procs:
            p = procs[-1]
            if _same_party(row.get("sender_email") or "", (p.get("submission_evidence") or {}).get("recipient") or ""):
                await _to_boaz_procurement(store, todos, row, p)
                out["to_boaz"] += 1
            else:
                await store.set_supplier_route(row["id"], {"owner": "exception", "procurement_id": p["id"],
                    "reason": "reply in a purchase-order thread from a different sender domain"}, requires_human=True)
                out["exceptions"] += 1
            continue
        thread = [o for o in await outreach_store.by_thread(row.get("gmail_thread_id") or "")
                  if o["state"] in ("SENT", "AWAITING_REPLY", "REPLY_RECEIVED")] if row.get("gmail_thread_id") else []
        if thread:
            o = thread[-1]
            if _same_party(row.get("sender_email") or "", o["recipient"]):
                await _to_nir(store, outreach_store, todos, row, o, "thread")
                out["to_nir"] += 1
            else:
                await store.set_supplier_route(row["id"], {"owner": "exception", "outreach_id": o["id"],
                    "reason": "reply in an outreach thread from a different sender domain"}, requires_human=True)
                out["exceptions"] += 1
            continue
        awaiting = await outreach_store.awaiting_by_recipient_domain(dom) if dom and dom not in FREE_MAIL else []
        if dom in FREE_MAIL:                       # a free-mail sender matches only an inquiry sent to that exact address
            awaiting = [o for o in await outreach_store.awaiting_by_recipient_domain(dom)
                        if _same_party(row.get("sender_email") or "", o["recipient"])]
        if len(awaiting) == 1:
            await _to_nir(store, outreach_store, todos, row, awaiting[0], "sender_domain")
            out["to_nir"] += 1
        elif len(awaiting) > 1:
            await store.set_supplier_route(row["id"], {"owner": "exception", "reason":
                f"{len(awaiting)} open inquiries for this sender domain - cannot tell which one"}, requires_human=True)
            out["exceptions"] += 1
        elif intake is not None and dom and (sup := _intake.match_supplier(dom, await intake.suppliers())) \
                and (res := await intake.process(row, sup)).get("is_reply"):
            await _to_known_supplier(store, todos, row, sup, res)
            out["to_nir" if sup.get("source") == "nir_sourcing" else "to_boaz"] += 1
        elif row.get("classification") in SUPPLIER_CATEGORIES:
            await store.set_supplier_route(row["id"], {"owner": "BOAZ", "source_folder": row.get("source_folder")})
            out["to_boaz"] += 1
        else:
            await store.set_supplier_route(row["id"], {"owner": None})
            out["other"] += 1
    return out
