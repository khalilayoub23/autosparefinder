"""
Script: supplier_outreach.py
Purpose: NIR's authorized, routine, NON-BINDING supplier inquiries (owner task 2026-10-27, sections 3.4 / 3.5 / 4.4).
         The owner authorized NIR to email suppliers to ask for missing API / Israel-shipping / stock / catalog /
         pricing-terms / contact information and to ask whether they are interested in working together - and
         NOTHING more: no accepting or negotiating terms, no promises of purchases/volume/exclusivity, no orders,
         no payments, no activation, no credentials or customer data.
Process (every gate is enforced here, at the sending boundary, not in a prompt):
  1. purposes      must be a subset of PURPOSES; the body is RENDERED from fixed templates (render_inquiry) -
                   no free text, no model output - and must equal the rendered text exactly (template-bound).
  2. scope scan    the rendered text is re-checked against COMMITMENT_PATTERNS (defense in depth).
  3. recipient     must be a VERIFIED contact in the supplier's qualification record (supplier_qualification).
  4. dedupe        one open inquiry per supplier; a follow-up only after FOLLOWUP_AFTER with no reply, at most
                   MAX_ATTEMPTS in total; idempotency_key UNIQUE in the store.
  5. permission    a recorded supplier_message / routine_inquiry authorization (standing grant by the owner) -
                   supplier_authorization.require(); plus the kill switch NIR_OUTREACH_ENABLED=1 (default OFF).
  6. send          draft created -> read back and compared to the authorized recipient + body -> sent with a
                   SupplierInquiryAuthorization through GmailClient.send_supplier_inquiry (its own endpoint set).
  State: READY_TO_CONTACT -> SENT/AWAITING_REPLY -> REPLY_RECEIVED (Email Agent routing) -> INFORMATION_REVIEWED
         -> QUALIFIED | MORE_INFORMATION_REQUIRED | NOT_QUALIFIED; FAILED on a send error (never retried blindly).
Data Imported/Modified: supplier_outreach (PII DB, migration 0042); Gmail drafts + one send per inquiry.
Data Sources: suppliers.credentials.qualification (catalog DB), agent_authorizations (PII DB).
Missing Data Delegation: suppliers with no VERIFIED contact are skipped and reported ("no verified contact").
Last Updated: 2026-10-27
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Any, Dict, List, Optional

import supplier_authorization as _auth
import supplier_qualification as _q

TEMPLATE_VERSION = "inquiry-v1"
PURPOSES = ("api_info", "israel_shipping", "availability", "catalog_access", "pricing_terms",
            "contact_routing", "dropshipping_terms", "collaboration_interest")
# requirement gaps -> the inquiry purpose that asks about them
GAP_PURPOSE = {"api": "api_info", "israel_shipping": "israel_shipping", "availability": "availability",
               "catalog": "catalog_access", "business": "pricing_terms", "contacts": "contact_routing",
               **{k: "dropshipping_terms" for k in _q.DROPSHIP_REQUIREMENTS}}
OPEN_STATES = ("READY_TO_CONTACT", "SENT", "AWAITING_REPLY")
FOLLOWUP_AFTER = timedelta(days=int(os.getenv("NIR_OUTREACH_FOLLOWUP_DAYS", "7")))
MAX_ATTEMPTS = 3            # first inquiry + at most two follow-ups
MAX_PER_CYCLE = int(os.getenv("NIR_OUTREACH_MAX_PER_CYCLE", "5"))

_PARAGRAPH = {
    "api_info": "- Do you offer an API or another official integration (catalog, prices, availability, order "
                "status)? If so, could you share the documentation and how to request access?",
    "israel_shipping": "- Do you ship to Israel? If so, which shipping methods, typical costs, delivery times and "
                       "tracking are available, and are there any restrictions?",
    "availability": "- How is stock / availability published (real-time API, data feed, or another method), and "
                    "how often is it updated?",
    "catalog_access": "- Is your catalog available in a machine-readable form (API, feed or file), including part "
                      "numbers, OEM references and vehicle fitment data where available?",
    "pricing_terms": "- Could you share your trade / wholesale pricing information, currency, minimum order "
                     "quantities, payment terms and return / warranty conditions?",
    "contact_routing": "- Who would be the right sales or technical contact for these questions?",
    "dropshipping_terms": "- Do you ship orders directly to end customers in Israel (dropshipping)? If so, could you share "
                          "any fees or minimums, the shipping methods and tracking, whether parcels can be sent in "
                          "neutral packaging without your invoices or promotional material, and how returns, damaged "
                          "or wrong items and warranty claims are handled?",
    "collaboration_interest": "- Would you be interested in exploring a possible collaboration with an online "
                              "car-parts marketplace serving customers in Israel?",
}
_INTRO = ("Hello,\n\nWe are AutoSpareFinder (autosparefinder.co.il), an online car-parts marketplace serving "
          "customers in Israel. We are gathering information about potential suppliers and would appreciate "
          "your help with the following questions:\n\n")
_FOLLOWUP_INTRO = ("Hello,\n\nWe are following up on our earlier message about the questions below. We would "
                   "appreciate any information you can share:\n\n")
_OUTRO = ("\n\nThis is an information request only and does not constitute an order, offer or commitment of "
          "any kind.\n\nThank you,\nAutoSpareFinder\nhttps://autosparefinder.co.il\n")
# anything that would turn an inquiry into a commitment / disclosure (checked on the FINAL text)
COMMITMENT_PATTERNS = tuple(re.compile(p, re.I) for p in (
    r"\bwe (?:accept|agree|confirm)\b", r"\bpurchase order\b", r"\bwe (?:will|shall) (?:buy|order|purchase|pay)\b",
    r"\bplace (?:an|the) order\b", r"\bexclusiv", r"\bguarantee", r"\bcontract\b", r"\bminimum commitment\b",
    r"\bcredit card\b", r"\biban\b", r"\bpassword\b", r"\bapi[_ -]?key\b", r"\btoken\b", r"\binvoice us\b",
    r"\b\d{13,19}\b"))


class OutreachRefused(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _now() -> datetime:
    return datetime.now(timezone.utc)


def enabled() -> bool:
    import supplier_flags
    return supplier_flags.enabled("NIR_OUTREACH_ENABLED")


def render_inquiry(purposes: List[str], *, followup: bool = False) -> Dict[str, str]:
    bad = [p for p in purposes if p not in PURPOSES]
    if bad or not purposes:
        raise OutreachRefused(f"purpose outside the authorized inquiry scope: {bad or 'none'}")
    ordered = [p for p in PURPOSES if p in purposes]
    body = (_FOLLOWUP_INTRO if followup else _INTRO) + "\n".join(_PARAGRAPH[p] for p in ordered) + _OUTRO
    subject = ("Follow-up: " if followup else "") + "Supplier information request - AutoSpareFinder"
    return {"subject": subject, "body": body, "purposes": ordered}


def check_scope(purposes: List[str], body: str, *, followup: bool = False) -> None:
    """The scope gate: only the fixed template text for these purposes, and no commitment language."""
    expected = render_inquiry(purposes, followup=followup)["body"]
    if body != expected:
        raise OutreachRefused("body is not the authorized inquiry template (free text is not in NIR's scope)")
    hits = [p.pattern for p in COMMITMENT_PATTERNS if p.search(body)]
    if hits:
        raise OutreachRefused(f"text contains commitment / disclosure language: {hits[:3]}")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def idempotency_key(supplier_id: str, recipient: str, purposes: List[str], attempt: int) -> str:
    return _sha(f"{supplier_id}|{recipient.lower()}|{','.join(sorted(purposes))}|{attempt}")[:64]


def purposes_for_gaps(qualification: Dict[str, Any]) -> List[str]:
    c = _q.classify(qualification)
    wanted = [GAP_PURPOSE[k] for k in c["unverified"] if k in GAP_PURPOSE]
    wanted += [GAP_PURPOSE[k] for k, v in c["dropship_statuses"].items() if v == _q.UNVERIFIED]
    return [p for p in PURPOSES if p in set(wanted) | {"collaboration_interest"}]


def build_raw(sender: str, recipient: str, subject: str, body: str) -> str:
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = sender, recipient, subject
    msg.set_content(body, charset="utf-8")
    return base64.urlsafe_b64encode(msg.as_bytes()).decode()


# ── stores ─────────────────────────────────────────────────────────────────────────────────────────────
class InMemoryOutreachStore:
    def __init__(self):
        self.rows: List[Dict[str, Any]] = []

    async def for_supplier(self, supplier_id: str) -> List[Dict[str, Any]]:
        return sorted([r for r in self.rows if r["supplier_id"] == supplier_id], key=lambda r: r["created_at"])

    async def insert(self, row: Dict[str, Any]) -> bool:
        if any(r["idempotency_key"] == row["idempotency_key"] for r in self.rows):
            return False
        self.rows.append(dict(row))
        return True

    async def update(self, outreach_id: str, **fields) -> None:
        for r in self.rows:
            if r["id"] == outreach_id:
                if fields.get("sent_at") and not (fields.get("authorization_id") or r.get("authorization_id")):
                    raise ValueError("supplier_outreach_sent_requires_authorization")
                r.update(fields, updated_at=_now())

    async def by_thread(self, thread_id: str) -> List[Dict[str, Any]]:
        return [r for r in self.rows if r.get("gmail_thread_id") == thread_id]

    async def awaiting_by_recipient_domain(self, domain: str) -> List[Dict[str, Any]]:
        return [r for r in self.rows if r["state"] in ("SENT", "AWAITING_REPLY")
                and r["recipient"].lower().rsplit("@", 1)[-1] == domain.lower()]


class PgOutreachStore:
    def __init__(self, session_factory=None):
        self._factory = session_factory

    def _session(self):
        if self._factory is None:
            from BACKEND_DATABASE_MODELS import pii_session_factory
            self._factory = pii_session_factory
        return self._factory()

    _COLS = ("id::text AS id, supplier_id, recipient, purposes, template_version, state, idempotency_key, attempt, "
             "parent_id::text AS parent_id, authorization_id::text AS authorization_id, source_task_id, "
             "gmail_draft_id, gmail_message_id, gmail_thread_id, reply_message_id, reply_at, last_error, "
             "created_at, sent_at, updated_at")

    async def _select(self, where: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        from sqlalchemy import text
        async with self._session() as db:
            res = await db.execute(text(f"SELECT {self._COLS} FROM supplier_outreach WHERE {where} "
                                        "ORDER BY created_at"), params)
            return [dict(r._mapping) for r in res.fetchall()]

    async def for_supplier(self, supplier_id: str) -> List[Dict[str, Any]]:
        return await self._select("supplier_id = :s", {"s": supplier_id})

    async def by_thread(self, thread_id: str) -> List[Dict[str, Any]]:
        return await self._select("gmail_thread_id = :t", {"t": thread_id})

    async def awaiting_by_recipient_domain(self, domain: str) -> List[Dict[str, Any]]:
        return await self._select("state IN ('SENT','AWAITING_REPLY') AND lower(split_part(recipient,'@',2)) = :d",
                                  {"d": domain.lower()})

    async def insert(self, row: Dict[str, Any]) -> bool:
        import json
        from sqlalchemy import text
        async with self._session() as db:
            res = await db.execute(text("""
                INSERT INTO supplier_outreach (id, supplier_id, recipient, purposes, template_version, state,
                    idempotency_key, attempt, parent_id, source_task_id, created_at, updated_at)
                VALUES (CAST(:id AS uuid), :supplier_id, :recipient, CAST(:purposes AS jsonb), :template_version,
                        :state, :idempotency_key, :attempt, CAST(:parent_id AS uuid), :source_task_id, NOW(), NOW())
                ON CONFLICT (idempotency_key) DO NOTHING RETURNING id"""),
                {**{k: row.get(k) for k in ("id", "supplier_id", "recipient", "template_version", "state",
                                            "idempotency_key", "attempt", "parent_id", "source_task_id")},
                 "purposes": json.dumps(row["purposes"])})
            created = res.scalar() is not None
            await db.commit()
            return created

    async def update(self, outreach_id: str, **fields) -> None:
        from sqlalchemy import text
        allowed = {"state", "authorization_id", "gmail_draft_id", "gmail_message_id", "gmail_thread_id",
                   "reply_message_id", "reply_at", "last_error", "sent_at"}
        f = {k: v for k, v in fields.items() if k in allowed}
        if not f:
            return
        sets = ", ".join(f"{k} = CAST(:{k} AS uuid)" if k == "authorization_id" else f"{k} = :{k}" for k in f)
        async with self._session() as db:
            await db.execute(text(f"UPDATE supplier_outreach SET {sets}, updated_at = NOW() "
                                  "WHERE id = CAST(:id AS uuid)"), {"id": outreach_id, **f})
            await db.commit()


# ── planning + sending ─────────────────────────────────────────────────────────────────────────────────
async def plan(store, supplier_id: str, qualification: Dict[str, Any], *, purposes: Optional[List[str]] = None,
               source_task_id: Optional[str] = None, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Decide whether ONE inquiry (or follow-up) may be prepared for this supplier. Never sends."""
    now = now or _now()
    contacts = _q.verified_contacts(qualification)
    if not contacts:
        return {"action": "skip", "reason": "no verified contact"}
    recipient = str(contacts[0]["address"]).strip()
    history = await store.for_supplier(supplier_id)
    if any(r["state"] == "READY_TO_CONTACT" for r in history):
        return {"action": "skip", "reason": "an inquiry is already prepared"}
    if any(r["state"] == "REPLY_RECEIVED" for r in history):
        return {"action": "skip", "reason": "supplier replied - NIR must review the reply first"}
    sent = [r for r in history if r["state"] in ("SENT", "AWAITING_REPLY")]
    followup = False
    parent = None
    if sent:
        last = sent[-1]
        if len(history) >= MAX_ATTEMPTS:
            return {"action": "skip", "reason": f"maximum {MAX_ATTEMPTS} attempts reached"}
        sent_at = last.get("sent_at") or last.get("created_at")
        if sent_at.tzinfo is None:
            sent_at = sent_at.replace(tzinfo=timezone.utc)
        if now - sent_at < FOLLOWUP_AFTER:
            return {"action": "skip", "reason": "awaiting reply (follow-up not due yet)"}
        followup, parent = True, last
    p = list(parent["purposes"]) if parent else (purposes or purposes_for_gaps(qualification))
    rendered = render_inquiry(p, followup=followup)
    check_scope(rendered["purposes"], rendered["body"], followup=followup)
    attempt = len(history) + 1
    row = {"id": str(uuid.uuid4()), "supplier_id": supplier_id, "recipient": recipient,
           "purposes": rendered["purposes"], "template_version": TEMPLATE_VERSION, "state": "READY_TO_CONTACT",
           "idempotency_key": idempotency_key(supplier_id, recipient, rendered["purposes"], attempt),
           "attempt": attempt, "parent_id": parent["id"] if parent else None, "source_task_id": source_task_id,
           "created_at": now, "gmail_thread_id": parent.get("gmail_thread_id") if parent else None}
    if not await store.insert(row):
        return {"action": "skip", "reason": "duplicate (idempotency key exists)"}
    return {"action": "prepared", "outreach": row, "followup": followup, **rendered}


def _draft_matches(read_back: Dict[str, Any], recipient: str, body: str) -> bool:
    from email_agent.normalize import normalize_message
    try:
        got = normalize_message(read_back["message"])
    except Exception:
        return False
    to_ok = [a.lower() for a in got.to] == [recipient.lower()]       # exactly the authorized recipient, no one else
    return to_ok and not got.cc and re.sub(r"\s+", " ", got.body_text).strip() == re.sub(r"\s+", " ", body).strip()


async def send_outreach(store, auth_store, client, prepared: Dict[str, Any], *, sender: str) -> Dict[str, Any]:
    """Send ONE prepared inquiry after every gate passes. Returns the recorded outcome."""
    row = prepared["outreach"]
    if not enabled():
        return {"sent": False, "reason": "NIR_OUTREACH_ENABLED is off"}
    try:
        check_scope(row["purposes"], prepared["body"], followup=prepared.get("followup", False))
        a = await _auth.require(auth_store, "supplier_message", "supplier", row["supplier_id"],
                                scope="routine_inquiry")
    except (OutreachRefused, _auth.AuthorizationError) as e:
        await store.update(row["id"], state="CANCELLED", last_error=str(e)[:300])
        return {"sent": False, "reason": str(e)}
    raw = build_raw(sender, row["recipient"], prepared["subject"], prepared["body"])
    try:
        created = await asyncio.to_thread(client.create_new_draft, raw, row.get("gmail_thread_id"))
        draft_id = str(created["id"])
        await store.update(row["id"], gmail_draft_id=draft_id)
        read_back = await asyncio.to_thread(client.get_draft, draft_id)
        if not _draft_matches(read_back, row["recipient"], prepared["body"]):
            await store.update(row["id"], state="FAILED", last_error="draft read-back did not match the inquiry")
            return {"sent": False, "reason": "draft read-back mismatch - not sent"}
        from email_agent.gmail_client import SupplierInquiryAuthorization
        sia = SupplierInquiryAuthorization(outreach_id=row["id"], draft_id=draft_id, authorization_id=a.id,
                                           recipient=row["recipient"], body_sha256=_sha(prepared["body"]))
        res = await asyncio.to_thread(client.send_supplier_inquiry, sia)
    except Exception as e:                        # a POST is never retried: mark FAILED, a human looks at it
        from email_agent.redaction import safe_error
        await store.update(row["id"], state="FAILED", last_error=safe_error(e, 300))
        return {"sent": False, "reason": f"send failed: {safe_error(e, 160)}"}
    await store.update(row["id"], state="AWAITING_REPLY", authorization_id=a.id, sent_at=_now(),
                       gmail_message_id=str(res.get("id")), gmail_thread_id=str(res.get("threadId") or ""))
    await _auth.consume(auth_store, a, f"inquiry {row['id']} sent")
    return {"sent": True, "outreach_id": row["id"], "gmail_message_id": res.get("id")}


async def record_reply(store, outreach_id: str, message_id: str, at: Optional[datetime] = None) -> None:
    await store.update(outreach_id, state="REPLY_RECEIVED", reply_message_id=message_id, reply_at=at or _now())


async def record_review(store, outreach_id: str, outcome: str) -> None:
    if outcome not in ("INFORMATION_REVIEWED", "QUALIFIED", "MORE_INFORMATION_REQUIRED", "NOT_QUALIFIED"):
        raise ValueError(f"not a review outcome: {outcome}")
    await store.update(outreach_id, state=outcome)


async def _candidates(limit: int = 50) -> List[Dict[str, Any]]:
    """NIR candidates (catalog DB) that are not rejected and carry a qualification record."""
    import json
    from sqlalchemy import text
    from BACKEND_DATABASE_MODELS import async_session_factory
    async with async_session_factory() as db:
        res = await db.execute(text("""
            SELECT id::text AS id, name, credentials->'qualification' AS qualification FROM suppliers
            WHERE credentials->>'source' = 'nir_sourcing' AND coalesce(credentials->>'status','') <> 'rejected'
              AND credentials ? 'qualification'
            ORDER BY updated_at DESC LIMIT :n"""), {"n": limit})
        out = []
        for r in res.fetchall():
            q = r.qualification
            out.append({"id": r.id, "name": r.name, "qualification": json.loads(q) if isinstance(q, str) else q})
        return out


async def run_outreach_cycle(store, auth_store, client, *, sender: str, candidates=None,
                             max_send: int = MAX_PER_CYCLE) -> Dict[str, Any]:
    """One pass: plan + send at most max_send inquiries. Every gate is re-checked inside send_outreach."""
    summary: Dict[str, Any] = {"considered": 0, "prepared": 0, "sent": 0, "skipped": 0, "refused": 0, "details": []}
    if not enabled():
        summary["disabled"] = True
        return summary
    for c in (candidates if candidates is not None else await _candidates()):
        if summary["sent"] >= max_send:
            break
        summary["considered"] += 1
        try:
            p = await plan(store, c["id"], c["qualification"])
        except OutreachRefused as e:
            summary["refused"] += 1
            summary["details"].append({"supplier_id": c["id"], "refused": e.reason})
            continue
        if p["action"] != "prepared":
            summary["skipped"] += 1
            summary["details"].append({"supplier_id": c["id"], "skip": p["reason"]})
            continue
        summary["prepared"] += 1
        r = await send_outreach(store, auth_store, client, p, sender=sender)
        summary["sent"] += int(bool(r.get("sent")))
        summary["details"].append({"supplier_id": c["id"], **r})
    return summary
