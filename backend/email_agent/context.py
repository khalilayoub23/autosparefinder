"""
Script: email_agent/context.py
Purpose: Resolve an inbound email against REAL AutoSpareFinder data - customer account, order,
         shipment, supplier - using the existing tables as the only source of business state.
Process (no guessing):
  - customer: users.email == sender address, and only when Gmail authenticated the sender.
  - order: candidate identifier tokens are taken from the thread text and matched by EXACT
    equality against orders.order_number / tracking_number / eurosender_order_code and
    order_items.supplier_order_id. No identifier format is assumed; a token that equals no row
    resolves nothing. A customer resolves only orders that belong to their own account; an
    order referenced by an unverified sender, or more than one candidate, stays unresolved
    (sender_mismatch / ambiguous) and its details are withheld.
  - shipment: read from the resolved order. Tracking is reported only for a real carrier
    shipment (shipping_provider='eurosender' with an order code) - orders created by the
    test-cycle auto_fake_tracking flag carry synthetic tracking that must never be quoted.
  - supplier: suppliers.website host == sender's registrable domain, exactly one match.
  - verify_context(): re-reads every resolved entity by primary key and re-checks the
    relationship, as an independent post-condition.
Data Imported/Modified: none (SELECT only).
Data Sources: PII DB (users, orders, order_items), catalog DB (suppliers). Supplier credential
         columns are never selected.
Last Updated: 2026-10-04
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol
from urllib.parse import urlsplit

from email_agent.normalize import NormalizedEmail
from email_agent.senders import is_authenticated, org_domain, sender_kind

_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9\-_/]{3,38}[A-Za-z0-9]")
MAX_TOKENS = 300
_TRUSTED_PLATFORMS = ("eurosender", "ebay", "aliexpress", "payment")


def candidate_tokens(text: str) -> List[str]:
    """Identifier-shaped tokens (must contain a digit). Candidates only - never trusted as IDs."""
    seen: Dict[str, None] = {}
    for tok in _TOKEN.findall(text or ""):
        if not any(c.isdigit() for c in tok):
            continue
        for v in (tok, tok.upper()):
            seen.setdefault(v, None)
        if len(seen) >= MAX_TOKENS:
            break
    return list(seen)


class ContextSource(Protocol):
    async def find_user_by_email(self, email: str) -> List[dict]: ...
    async def find_orders_by_tokens(self, tokens: List[str]) -> List[dict]: ...
    async def find_suppliers_by_domain(self, domain: str) -> List[dict]: ...
    async def get_user(self, user_id: str) -> Optional[dict]: ...
    async def get_order(self, order_id: str) -> Optional[dict]: ...
    async def get_supplier(self, supplier_id: str) -> Optional[dict]: ...


def _unresolved(reason: str, status: str = "unresolved") -> Dict[str, Any]:
    return {"status": status, "reason": reason}


@dataclass
class ResolvedContext:
    sender_kind: str = "unknown"
    sender_authenticated: bool = False
    customer: Dict[str, Any] = field(default_factory=lambda: _unresolved("not evaluated"))
    order: Dict[str, Any] = field(default_factory=lambda: _unresolved("not evaluated"))
    shipment: Dict[str, Any] = field(default_factory=lambda: _unresolved("no resolved order"))
    supplier: Dict[str, Any] = field(default_factory=lambda: _unresolved("not evaluated"))
    notes: List[str] = field(default_factory=list)

    def references(self) -> Dict[str, str]:
        """Business identifiers that were verified against the DB. Nothing else may be cited."""
        refs: Dict[str, str] = {}
        if self.customer.get("status") == "resolved":
            refs["customer_id"] = self.customer["user_id"]
        if self.order.get("status") == "resolved":
            refs["order_id"] = self.order["order_id"]
            refs["order_number"] = self.order["order_number"]
        if self.shipment.get("status") == "resolved":
            for k in ("tracking_number", "eurosender_order_code"):
                if self.shipment.get(k):
                    refs[k] = self.shipment[k]
        if self.supplier.get("status") == "resolved":
            refs["supplier_id"] = self.supplier["supplier_id"]
        return refs

    def to_dict(self) -> Dict[str, Any]:
        return {"sender_kind": self.sender_kind, "sender_authenticated": self.sender_authenticated,
                "customer": self.customer, "order": self.order, "shipment": self.shipment,
                "supplier": self.supplier, "notes": self.notes}


def _shipment_from_order(o: dict) -> Dict[str, Any]:
    if (o.get("shipping_provider") or "") == "eurosender" and o.get("eurosender_order_code"):
        return {"status": "resolved", "provider": "eurosender",
                "eurosender_order_code": o["eurosender_order_code"],
                "eurosender_status": o.get("eurosender_status") or "",
                "tracking_number": o.get("tracking_number") or "",
                "tracking_url": o.get("tracking_url") or "",
                "shipped_at": o.get("shipped_at") or "", "delivered_at": o.get("delivered_at") or ""}
    return _unresolved("order has no verified carrier shipment", "no_shipment")


async def resolve_context(email: NormalizedEmail, thread: List[NormalizedEmail],
                          source: ContextSource) -> ResolvedContext:
    kind = sender_kind(email.sender_email)
    authed = is_authenticated(email.headers, email.sender_email)
    ctx = ResolvedContext(sender_kind=kind, sender_authenticated=authed)
    is_platform = kind in _TRUSTED_PLATFORMS or kind == "google"

    # ── customer ─────────────────────────────────────────────────────────────
    if is_platform:
        ctx.customer = _unresolved("sender is a platform address, not a customer")
    elif not authed:
        ctx.customer = _unresolved("sender address was not authenticated by Gmail (DMARC/DKIM)")
    else:
        users = await source.find_user_by_email(email.sender_email)
        if len(users) == 1:
            u = users[0]
            ctx.customer = {"status": "resolved", "user_id": str(u["id"]),
                            "full_name": u.get("full_name") or ""}
        elif not users:
            ctx.customer = _unresolved("no account with this email address")
        else:
            ctx.customer = _unresolved("more than one account matched", "ambiguous")

    # ── supplier ─────────────────────────────────────────────────────────────
    dom = org_domain(email.sender_domain)
    if kind == "free_mail":
        ctx.supplier = _unresolved("free-mail sender cannot be matched to a supplier by domain")
    elif not authed:
        ctx.supplier = _unresolved("sender address was not authenticated by Gmail (DMARC/DKIM)")
    elif dom:
        rows = [s for s in await source.find_suppliers_by_domain(dom)
                if org_domain(urlsplit(s.get("website") or "").hostname
                              or str(s.get("website") or "").split("/")[0]) == dom]
        if len(rows) == 1:
            ctx.supplier = {"status": "resolved", "supplier_id": str(rows[0]["id"]),
                            "name": rows[0].get("name") or "", "is_active": bool(rows[0].get("is_active"))}
        elif not rows:
            ctx.supplier = _unresolved("no supplier website matches the sender domain")
        else:
            ctx.supplier = _unresolved(f"{len(rows)} suppliers share this domain", "ambiguous")

    # ── order ────────────────────────────────────────────────────────────────
    inbound_text = "\n".join(f"{m.subject}\n{m.new_text}" for m in thread if m.is_inbound)
    tokens = candidate_tokens(f"{email.subject}\n{email.body_text}\n{inbound_text}")
    matches: Dict[str, dict] = {}
    if tokens:
        for o in await source.find_orders_by_tokens(tokens):
            matches.setdefault(str(o["id"]), o)
    found = list(matches.values())
    trusted_party = authed and (kind in _TRUSTED_PLATFORMS or ctx.supplier.get("status") == "resolved")
    chosen: Optional[dict] = None
    if not found:
        ctx.order = _unresolved("no identifier in the email matches an order")
    elif ctx.customer.get("status") == "resolved":
        own = [o for o in found if str(o.get("user_id")) == ctx.customer["user_id"]]
        other = len(found) - len(own)
        if len(own) == 1 and not other:
            chosen = own[0]
        elif not own:
            ctx.order = _unresolved("referenced order belongs to a different account", "sender_mismatch")
        else:
            ctx.order = _unresolved(f"{len(found)} candidate orders referenced", "ambiguous")
    elif trusted_party:
        if len(found) == 1:
            chosen = found[0]
        else:
            ctx.order = _unresolved(f"{len(found)} candidate orders referenced", "ambiguous")
    else:
        ctx.order = _unresolved("an order is referenced but the sender is not verified as its customer",
                                "sender_mismatch")
    if chosen:
        ctx.order = {"status": "resolved", "order_id": str(chosen["id"]),
                     "order_number": chosen["order_number"], "order_status": chosen.get("status") or "",
                     "user_id": str(chosen.get("user_id") or ""), "matched_on": chosen.get("matched_on") or ""}
        ctx.shipment = _shipment_from_order(chosen)
    return ctx


async def verify_context(ctx: ResolvedContext, source: ContextSource) -> Dict[str, Any]:
    """Post-condition: every resolved entity exists (re-read by primary key) and is related."""
    checks: Dict[str, bool] = {}
    if ctx.customer.get("status") == "resolved":
        u = await source.get_user(ctx.customer["user_id"])
        checks["customer_exists"] = bool(u)
    if ctx.order.get("status") == "resolved":
        o = await source.get_order(ctx.order["order_id"])
        checks["order_exists"] = bool(o) and o.get("order_number") == ctx.order["order_number"]
        if ctx.customer.get("status") == "resolved":
            checks["order_belongs_to_customer"] = bool(o) and str(o.get("user_id")) == ctx.customer["user_id"]
        if ctx.shipment.get("status") == "resolved":
            checks["shipment_matches_order"] = bool(o) and (
                o.get("eurosender_order_code") == ctx.shipment.get("eurosender_order_code"))
    if ctx.supplier.get("status") == "resolved":
        checks["supplier_exists"] = bool(await source.get_supplier(ctx.supplier["supplier_id"]))
    for part in (ctx.customer, ctx.order, ctx.supplier):
        if part.get("status") in ("ambiguous", "sender_mismatch"):
            # ambiguous / mismatched matches must carry no entity details
            checks.setdefault("unresolved_carries_no_ids", True)
            if any(k.endswith("_id") or k == "order_number" for k in part):
                checks["unresolved_carries_no_ids"] = False
    return {"ok": all(checks.values()), "checks": checks}


class DbContextSource:
    """Production ContextSource. Sessions are opened per call and only ever SELECT."""

    _ORDER_COLS = ("o.id::text AS id, o.order_number, o.user_id::text AS user_id, o.status, "
                   "o.tracking_number, o.tracking_url, o.shipping_provider, o.eurosender_order_code, "
                   "o.eurosender_status, o.shipped_at::text AS shipped_at, o.delivered_at::text AS delivered_at")

    def __init__(self, pii_factory=None, catalog_factory=None):
        self._pii = pii_factory
        self._cat = catalog_factory

    def _factories(self):
        if self._pii is None or self._cat is None:
            from BACKEND_DATABASE_MODELS import async_session_factory, pii_session_factory
            self._pii = self._pii or pii_session_factory
            self._cat = self._cat or async_session_factory
        return self._pii, self._cat

    async def _rows(self, which: str, sql: str, params: dict) -> List[dict]:
        from sqlalchemy import text
        pii, cat = self._factories()
        async with (pii if which == "pii" else cat)() as db:
            res = await db.execute(text(sql), params)
            return [dict(r._mapping) for r in res.fetchall()]

    async def find_user_by_email(self, email: str) -> List[dict]:
        return await self._rows("pii", "SELECT id::text AS id, full_name FROM users "
                                       "WHERE lower(email) = lower(:e) AND is_active LIMIT 2", {"e": email})

    async def find_orders_by_tokens(self, tokens: List[str]) -> List[dict]:
        sql = f"""
            SELECT {self._ORDER_COLS},
                   CASE WHEN o.order_number = ANY(CAST(:t AS text[])) THEN 'order_number'
                        WHEN o.eurosender_order_code = ANY(CAST(:t AS text[])) THEN 'eurosender_order_code'
                        WHEN o.tracking_number = ANY(CAST(:t AS text[])) THEN 'tracking_number'
                        ELSE 'supplier_order_id' END AS matched_on
            FROM orders o
            WHERE o.deleted_at IS NULL AND (
                  o.order_number = ANY(CAST(:t AS text[]))
               OR o.eurosender_order_code = ANY(CAST(:t AS text[]))
               OR o.tracking_number = ANY(CAST(:t AS text[]))
               OR EXISTS (SELECT 1 FROM order_items i WHERE i.order_id = o.id
                          AND i.supplier_order_id = ANY(CAST(:t AS text[]))))
            LIMIT 10"""
        return await self._rows("pii", sql, {"t": list(tokens)})

    async def find_suppliers_by_domain(self, domain: str) -> List[dict]:
        return await self._rows("cat", "SELECT id::text AS id, name, website, is_active FROM suppliers "
                                       "WHERE website ILIKE :p LIMIT 20", {"p": f"%{domain}%"})

    async def get_user(self, user_id: str) -> Optional[dict]:
        r = await self._rows("pii", "SELECT id::text AS id FROM users WHERE id = CAST(:i AS uuid)", {"i": user_id})
        return r[0] if r else None

    async def get_order(self, order_id: str) -> Optional[dict]:
        r = await self._rows("pii", f"SELECT {self._ORDER_COLS} FROM orders o "
                                    "WHERE o.id = CAST(:i AS uuid) AND o.deleted_at IS NULL", {"i": order_id})
        return r[0] if r else None

    async def get_supplier(self, supplier_id: str) -> Optional[dict]:
        r = await self._rows("cat", "SELECT id::text AS id FROM suppliers WHERE id = CAST(:i AS uuid)",
                             {"i": supplier_id})
        return r[0] if r else None
