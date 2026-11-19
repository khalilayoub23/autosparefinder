"""
Script: email_agent/supplier_intake.py
Purpose: Turn a supplier's reply into STRUCTURED supplier data (owner task 2026-11-20). Reading and classifying a
         supplier mail is not enough: the contact, the carrier and the stated transit time must land in the
         supplier record with their evidence, or nobody downstream (NIR, BOAZ, Eurosender) can use them.
Process (deterministic, no LLM, content is untrusted data):
  1. match_supplier(): the sender domain equals the registered domain of EXACTLY ONE supplier website; zero or
     several matches -> no association (the router files an exception for a human, nothing is guessed).
  2. extract_facts(): contact name / role / phone from the signature, carrier names, stated transit days, and
     whether rates are stated as variable. Only what the text says; a price is never inferred.
  3. persist(): suppliers.credentials.contacts[] (dedup by email) and suppliers.shipping_info merge
     (carriers, transit_days_min/max, rates='variable_live_quote_required', evidence ref). A mail whose sender
     domain did not authenticate (no dkim/dmarc pass for that domain) is stored as an UNVERIFIED claim only.
     The supplier's statement is also recorded as a qualification CLAIM (never VERIFIED by itself).
  4. A pickup/dispatch address is NEVER taken from a signature/newsletter footer; it stays a separate, explicit
     confirmation (Eurosender allowlist rule).
Data Imported/Modified: suppliers.credentials (contacts), suppliers.shipping_info, suppliers.credentials.qualification
         (claims) in the catalog DB.
Last Updated: 2026-11-20
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

CARRIERS = ("DHL", "UPS", "FedEx", "TNT", "DPD", "Royal Mail", "Parcelforce", "USPS", "Eurosender", "GLS", "Aramex")
_PHONE = re.compile(r"(?:T|Tel|Phone|Mob|M)\s*[:.]?\s*(\+?\d[\d\s().-]{7,18}\d)", re.I)
_DAYS = re.compile(r"(\d{1,2})\s*(?:-|–|to)\s*(\d{1,2})\s*(?:working\s+|business\s+)?days", re.I)
_DAYS_ONE = re.compile(r"(?:within|in|about|approximately|takes?|usually)\s+(\d{1,2})\s*(?:working\s+|business\s+)?days", re.I)
_VARIABLE = re.compile(r"(rates?|prices?|costs?)[^.\n]{0,60}\b(vary|varies|variable|change|fluctuat)|\bvar(y|ies)\b[^.\n]{0,40}\b(rates?|prices?)", re.I)
_ROLE = re.compile(r"^(sales|customer|account|export|operations?|purchasing|logistics|warehouse|general|managing|marketing)\b.*"
                   r"(manager|executive|representative|advisor|assistant|director|coordinator|officer|agent)\b", re.I)
_NAME = re.compile(r"^[A-Z][a-zA-Z'’-]+(?:\s+[A-Z][a-zA-Z'’-]+){1,3}$")
_SIGN_OFF = re.compile(r"^(kind regards|best regards|regards|many thanks|thanks|thank you|sincerely|yours sincerely)\b", re.I)


def registered_domain(addr_or_host: str) -> str:
    h = (addr_or_host or "").strip().lower().rsplit("@", 1)[-1]
    h = re.sub(r"^https?://", "", h).split("/")[0].split(":")[0]
    h = h[4:] if h.startswith("www.") else h
    return h


def match_supplier(sender_domain: str, suppliers: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The one supplier whose website domain equals (or is a parent/sub-domain of) the sender domain."""
    d = registered_domain(sender_domain)
    if not d:
        return None
    hits = []
    for s in suppliers:
        sd = registered_domain(s.get("website") or "")
        if sd and (sd == d or d.endswith("." + sd) or sd.endswith("." + d)):
            hits.append(s)
    return hits[0] if len(hits) == 1 else None


def authenticated(auth_results: str, sender_domain: str) -> bool:
    """dkim=pass or dmarc=pass for the sender's own domain (Gmail Authentication-Results). Absent/failed -> False."""
    ar = (auth_results or "").lower()
    d = registered_domain(sender_domain)
    if not ar or not d:
        return False
    return bool(re.search(rf"dmarc=pass[^;]*header\.from={re.escape(d)}", ar)
                or re.search(rf"dkim=pass[^;]*header\.i=@?[\w.]*{re.escape(d)}", ar))


def _own_text(body: str) -> str:
    """The reply itself, without the quoted original (a quoted block is OUR text, not the supplier's)."""
    out = []
    for line in (body or "").splitlines():
        if re.match(r"^\s*(From|Sent|On .* wrote|-----\s*Original|_{5,})", line):
            break
        out.append(line)
    return "\n".join(out)


def extract_facts(body: str, sender_email: str, display_name: str = "") -> Dict[str, Any]:
    text = _own_text(body)
    lines = [l.strip() for l in text.splitlines()]
    facts: Dict[str, Any] = {"email": (sender_email or "").lower(), "name": None, "role": None, "phone": None,
                             "carriers": [], "transit_days_min": None, "transit_days_max": None,
                             "rates_variable": False}
    # signature: the first "Name" line after the sign-off, then the role line
    idx = max((i for i, l in enumerate(lines) if len(l) < 40 and _SIGN_OFF.match(l)), default=None)
    sig = lines[idx + 1: idx + 12] if idx is not None else lines[-12:]
    sig = [l for l in sig if l]
    for i, l in enumerate(sig):
        if facts["name"] is None and _NAME.match(l):
            facts["name"] = l
            continue
        if facts["role"] is None and re.search(r"[–-]", l) and _ROLE.match(l.split("–")[0].split(" - ")[0].strip()):
            facts["role"] = re.sub(r"\s*[–-]\s*", " - ", l, count=1)
    if facts["name"] is None and display_name and _NAME.match(display_name.strip()):
        facts["name"] = display_name.strip()
    m = _PHONE.search("\n".join(sig) or text)
    if m:
        facts["phone"] = re.sub(r"\s+", " ", m.group(1)).strip()
    low = text.lower()
    facts["carriers"] = [c for c in CARRIERS if re.search(rf"\b{re.escape(c.lower())}\b", low)]
    d = _DAYS.search(text)
    if d:
        facts["transit_days_min"], facts["transit_days_max"] = int(d.group(1)), int(d.group(2))
    else:
        d1 = _DAYS_ONE.search(text)
        if d1:
            facts["transit_days_min"] = facts["transit_days_max"] = int(d1.group(1))
    facts["rates_variable"] = bool(_VARIABLE.search(text))
    return facts


def merge_shipping_info(existing: Optional[Dict[str, Any]], facts: Dict[str, Any], *, evidence: str,
                        verified: bool, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Merge supplier-stated shipping facts into suppliers.shipping_info WITHOUT touching keys other code reads
    (`avg_delivery_days` stays untouched: a stated range is not a measured average). Unverified senders add nothing
    here - they are claims only."""
    info = dict(existing or {})
    if not verified:
        return info
    ts = (now or datetime.now(timezone.utc)).isoformat()
    stated = dict(info.get("supplier_stated") or {})
    if facts.get("carriers"):
        stated["carriers"] = sorted(set(stated.get("carriers", [])) | set(facts["carriers"]))
    if facts.get("transit_days_min") is not None:
        stated["transit_days_min"], stated["transit_days_max"] = facts["transit_days_min"], facts["transit_days_max"]
    if facts.get("rates_variable"):
        stated["rates"] = "variable_live_quote_required"       # never a number: no price was stated
    stated["evidence"] = evidence
    stated["recorded_at"] = ts
    info["supplier_stated"] = stated
    return info


def merge_contact(contacts: Optional[List[Dict[str, Any]]], facts: Dict[str, Any], *, evidence: str,
                  verified: bool, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    out = [dict(c) for c in (contacts or [])]
    ts = (now or datetime.now(timezone.utc)).isoformat()
    entry = {"email": facts["email"], "name": facts.get("name"), "role": facts.get("role"),
             "phone": facts.get("phone"), "channel": "email_reply", "authenticated_sender": verified,
             "evidence": evidence, "recorded_at": ts}
    for c in out:
        if (c.get("email") or "").lower() == entry["email"]:
            for k in ("name", "role", "phone"):
                c[k] = entry[k] or c.get(k)
            c.update(authenticated_sender=bool(c.get("authenticated_sender")) or verified, evidence=evidence,
                     recorded_at=ts)
            return out
    out.append(entry)
    return out


async def persist(conn, supplier_id: str, facts: Dict[str, Any], *, evidence: str, verified: bool) -> Dict[str, Any]:
    """Write the facts (catalog DB, asyncpg connection). Idempotent: re-running the same message changes nothing."""
    import supplier_qualification as Q
    row = await conn.fetchrow("SELECT shipping_info, credentials FROM suppliers WHERE id = $1::uuid", supplier_id)
    if row is None:
        return {"stored": False, "reason": "supplier not found"}
    j = lambda v: (json.loads(v) if isinstance(v, str) else v) if v is not None else None
    ship, cred = j(row["shipping_info"]) or {}, j(row["credentials"]) or {}
    before = json.dumps([ship, cred.get("contacts")], sort_keys=True, default=str)
    ship2 = merge_shipping_info(ship, facts, evidence=evidence, verified=verified)
    cred["contacts"] = merge_contact(cred.get("contacts"), facts, evidence=evidence, verified=verified)
    rec = cred.get("qualification")
    if rec is not None or facts.get("carriers") or facts.get("transit_days_min") is not None:
        rec = Q.ensure_schema(rec) if rec else Q.new_record(supplier_id)
        if facts.get("transit_days_min") is not None or facts.get("carriers"):
            Q.record_supplier_claim(
                rec, "israel_shipping",
                f"carriers {','.join(facts['carriers']) or 'n/a'}; transit {facts['transit_days_min']}-"
                f"{facts['transit_days_max']} days; rates {'variable' if facts['rates_variable'] else 'not stated'}",
                value=None, source_ref=evidence)
        cred["qualification"] = rec
    await conn.execute("UPDATE suppliers SET shipping_info = $2::jsonb, credentials = $3::jsonb, updated_at = NOW() "
                       "WHERE id = $1::uuid", supplier_id, json.dumps(ship2), json.dumps(cred, default=str))
    return {"stored": True, "changed": before != json.dumps([ship2, cred.get("contacts")], sort_keys=True, default=str)}


class PgIntake:
    """Production intake: reads the supplier list and the message from Gmail, persists to the catalog DB."""

    def __init__(self, client, mailbox: str):
        self.client, self.mailbox = client, mailbox

    @staticmethod
    async def _conn():
        import os
        import asyncpg
        return await asyncpg.connect(os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://"))

    async def suppliers(self) -> List[Dict[str, Any]]:
        conn = await self._conn()
        try:
            rows = await conn.fetch("SELECT id::text AS id, name, website, is_active, "
                                    "credentials->>'source' AS source FROM suppliers WHERE website IS NOT NULL")
            return [dict(r) for r in rows]
        finally:
            await conn.close()

    async def process(self, row: Dict[str, Any], supplier: Dict[str, Any]) -> Dict[str, Any]:
        import asyncio
        from email_agent.normalize import normalize_thread
        raw = await asyncio.to_thread(self.client.get_thread, row["gmail_thread_id"])
        thread = normalize_thread(raw, self.mailbox)
        email = next((m for m in thread if m.message_id == row["gmail_message_id"]), None)
        if email is None or not email.is_inbound:
            return {"is_reply": False, "stored": False, "reason": "message not found / outbound"}
        ours_before = any((not m.is_inbound) and m.date and email.date and m.date < email.date for m in thread)
        verified = authenticated((email.headers or {}).get("authentication-results", ""), email.sender_domain)
        facts = extract_facts(email.new_text or email.body_text, email.sender_email, email.sender_name)
        ev = f"gmail:{row['gmail_message_id']} thread:{row['gmail_thread_id']} received:{email.date}"
        out = {"is_reply": ours_before, "authenticated": verified, "facts": facts, "stored": False}
        if ours_before:
            conn = await self._conn()
            try:
                out.update(await persist(conn, supplier["id"], facts, evidence=ev, verified=verified))
            finally:
                await conn.close()
        return out
