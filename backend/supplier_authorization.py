"""
Script: supplier_authorization.py
Purpose: The ONE enforcement point for supplier-side permissions (owner task 2026-10-27, section 1.1).
         Each permission is its own DOMAIN and never implies another:
           supplier_message             send a message to a supplier (scope: routine_inquiry = NIR's non-binding
                                        information requests; anything else is per message)
           supplier_activation          approve / activate a supplier (is_active=TRUE)
           commercial_commitment        accept terms, negotiate, promise volume - NO code path performs it today
           purchase_order               create a purchase order with ONE supplier for ONE customer order
           supplier_payment             pay that supplier for that purchase order
           supplier_integration_change  change a supplier's integration / credentials / operational status
         A customer payment is NOT an authorization of anything here.
Process:
  grant(...)      records who approved what, for which entity and (purchase_order / supplier_payment) which
                  exact terms (terms_hash over supplier, items, quantities, unit costs, shipping, currency).
  require(...)    the check every execution boundary calls: raises AuthorizationError unless a valid,
                  unrevoked, unexpired, unconsumed grant exists for that domain + entity (+ terms_hash).
                  A standing grant is accepted ONLY for supplier_message/routine_inquiry.
  consume(...)    ATOMIC for one-shot domains (store.consume_once: only one caller wins); one-shot domains (purchase_order, supplier_payment) are consumed on use, so one approval
                  cannot be replayed for a second purchase.
Data Imported/Modified: agent_authorizations (PII DB, migration 0042).
Data Sources: owner WhatsApp console commands (the approval source), never email/task/model content.
Last Updated: 2026-10-28 (consume is atomic for one-shot domains: consume_once)
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

DOMAINS = ("supplier_message", "supplier_activation", "commercial_commitment", "purchase_order",
           "supplier_payment", "supplier_integration_change")
ONE_SHOT = frozenset({"purchase_order", "supplier_payment", "commercial_commitment", "supplier_activation"})
TERMS_BOUND = frozenset({"purchase_order", "supplier_payment", "commercial_commitment"})
# (domain, scope) pairs that may be granted as a standing policy instead of per entity
STANDING_ALLOWED = frozenset({("supplier_message", "routine_inquiry")})
DEFAULT_TTL = {"purchase_order": timedelta(hours=48), "supplier_payment": timedelta(hours=48)}
# approval sources that count: an explicit owner action. Agents, emails, tasks and models are not sources.
APPROVAL_SOURCES = frozenset({"owner_whatsapp", "owner_cli", "owner_task_prompt", "test"})


class AuthorizationError(Exception):
    def __init__(self, domain: str, entity: str, reason: str):
        super().__init__(f"{domain} not authorized for {entity}: {reason}")
        self.domain, self.entity, self.reason = domain, entity, reason


def _now() -> datetime:
    return datetime.now(timezone.utc)


def terms_hash(terms: Dict[str, Any]) -> str:
    """Stable hash of the material terms. Any changed price/quantity/item/shipping -> a different hash."""
    return hashlib.sha256(json.dumps(terms, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


@dataclass
class Authorization:
    id: str
    domain: str
    scope: str
    entity_type: str
    entity_id: str
    approved_by: str
    source: str
    terms_hash: Optional[str] = None
    terms: Dict[str, Any] = field(default_factory=dict)
    standing: bool = False
    created_at: datetime = field(default_factory=_now)
    expires_at: Optional[datetime] = None
    consumed_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None
    result: Optional[str] = None

    def valid_at(self, now: datetime) -> bool:
        return (self.revoked_at is None and self.consumed_at is None
                and (self.expires_at is None or self.expires_at > now))


def _validate_grant(domain: str, scope: str, entity_type: str, entity_id: str, approved_by: str, source: str,
                    standing: bool, th: Optional[str]) -> None:
    if domain not in DOMAINS:
        raise ValueError(f"unknown authorization domain {domain!r}")
    if source not in APPROVAL_SOURCES:
        raise ValueError(f"{source!r} is not an approval source (only an explicit owner action is)")
    if not approved_by:
        raise ValueError("approved_by is required")
    if standing and (domain, scope) not in STANDING_ALLOWED:
        raise ValueError(f"{domain}/{scope} cannot be a standing grant")
    if not standing and (not entity_type or not entity_id or entity_id == "*"):
        raise ValueError("a non-standing grant must name one entity")
    if domain in TERMS_BOUND and not th:
        raise ValueError(f"{domain} must be bound to exact terms")


class InMemoryAuthStore:
    def __init__(self):
        self.rows: List[Authorization] = []

    async def insert(self, a: Authorization) -> Authorization:
        self.rows.append(a)
        return a

    async def candidates(self, domain: str, entity_type: str, entity_id: str) -> List[Authorization]:
        return [a for a in self.rows if a.domain == domain and a.revoked_at is None and
                ((a.entity_type == entity_type and a.entity_id == entity_id) or a.standing)]

    async def mark(self, auth_id: str, **fields) -> None:
        for a in self.rows:
            if a.id == auth_id:
                for k, v in fields.items():
                    setattr(a, k, v)

    async def consume_once(self, auth_id: str, at: datetime, result: str) -> bool:
        for a in self.rows:
            if a.id == auth_id and a.consumed_at is None and a.revoked_at is None:
                a.consumed_at, a.result = at, result
                return True
        return False


class PgAuthStore:
    """agent_authorizations in the PII DB."""

    def __init__(self, session_factory=None):
        self._factory = session_factory

    def _session(self):
        if self._factory is None:
            from BACKEND_DATABASE_MODELS import pii_session_factory
            self._factory = pii_session_factory
        return self._factory()

    async def insert(self, a: Authorization) -> Authorization:
        from sqlalchemy import text
        async with self._session() as db:
            await db.execute(text("""
                INSERT INTO agent_authorizations (id, domain, scope, entity_type, entity_id, terms_hash, terms,
                    approved_by, source, standing, created_at, expires_at)
                VALUES (CAST(:id AS uuid), :domain, :scope, :et, :eid, :th, CAST(:terms AS jsonb), :by, :src,
                        :standing, :created, :expires)"""),
                {"id": a.id, "domain": a.domain, "scope": a.scope, "et": a.entity_type, "eid": a.entity_id,
                 "th": a.terms_hash, "terms": json.dumps(a.terms, default=str), "by": a.approved_by,
                 "src": a.source, "standing": a.standing, "created": a.created_at, "expires": a.expires_at})
            await db.commit()
        return a

    async def candidates(self, domain: str, entity_type: str, entity_id: str) -> List[Authorization]:
        from sqlalchemy import text
        async with self._session() as db:
            res = await db.execute(text("""
                SELECT id::text AS id, domain, scope, entity_type, entity_id, approved_by, source, terms_hash,
                       terms, standing, created_at, expires_at, consumed_at, revoked_at, result
                FROM agent_authorizations
                WHERE domain = :d AND revoked_at IS NULL
                  AND ((entity_type = :et AND entity_id = :eid) OR standing)
                ORDER BY created_at DESC LIMIT 20"""), {"d": domain, "et": entity_type, "eid": entity_id})
            return [Authorization(**dict(r._mapping)) for r in res.fetchall()]

    async def mark(self, auth_id: str, **fields) -> None:
        from sqlalchemy import text
        allowed = {"consumed_at", "revoked_at", "result"}
        sets = ", ".join(f"{k} = :{k}" for k in fields if k in allowed)
        if not sets:
            return
        async with self._session() as db:
            await db.execute(text(f"UPDATE agent_authorizations SET {sets} WHERE id = CAST(:id AS uuid)"),
                             {"id": auth_id, **{k: v for k, v in fields.items() if k in allowed}})
            await db.commit()

    async def consume_once(self, auth_id: str, at: datetime, result: str) -> bool:
        """Atomic one-shot use: True only for the ONE caller whose UPDATE flipped consumed_at from NULL."""
        from sqlalchemy import text
        async with self._session() as db:
            res = await db.execute(text(
                "UPDATE agent_authorizations SET consumed_at = :at, result = :r WHERE id = CAST(:id AS uuid) "
                "AND consumed_at IS NULL AND revoked_at IS NULL RETURNING id"), {"id": auth_id, "at": at, "r": result})
            won = res.scalar() is not None
            await db.commit()
            return won


async def grant(store, *, domain: str, scope: str, entity_type: str, entity_id: str, approved_by: str,
                source: str, terms: Optional[Dict[str, Any]] = None, standing: bool = False,
                ttl: Optional[timedelta] = None, now: Optional[datetime] = None) -> Authorization:
    now = now or _now()
    th = terms_hash(terms) if terms is not None else None
    _validate_grant(domain, scope, entity_type, entity_id, approved_by, source, standing, th)
    ttl = ttl if ttl is not None else DEFAULT_TTL.get(domain)
    a = Authorization(id=str(uuid.uuid4()), domain=domain, scope=scope,
                      entity_type="policy" if standing else entity_type,
                      entity_id="*" if standing else str(entity_id), approved_by=approved_by, source=source,
                      terms_hash=th, terms=terms or {}, standing=standing, created_at=now,
                      expires_at=(now + ttl) if ttl else None)
    return await store.insert(a)


async def require(store, domain: str, entity_type: str, entity_id: str, *, scope: str,
                  terms: Optional[Dict[str, Any]] = None, now: Optional[datetime] = None) -> Authorization:
    """The execution-boundary check. Returns the matching grant or raises AuthorizationError."""
    now = now or _now()
    entity = f"{entity_type}:{entity_id}"
    if domain not in DOMAINS:
        raise AuthorizationError(domain, entity, "unknown domain")
    if domain in TERMS_BOUND and terms is None:
        raise AuthorizationError(domain, entity, "no terms to check the approval against")
    th = terms_hash(terms) if terms is not None else None
    found = await store.candidates(domain, entity_type, str(entity_id))
    stale = False
    for a in found:
        if a.scope != scope:
            continue
        if a.standing and (domain, scope) not in STANDING_ALLOWED:
            continue
        if not a.standing and (a.entity_type != entity_type or a.entity_id != str(entity_id)):
            continue
        if not a.valid_at(now):
            stale = True
            continue
        if domain in TERMS_BOUND and a.terms_hash != th:
            stale = True                    # approved for different terms: changed price/qty -> re-approval
            continue
        return a
    raise AuthorizationError(domain, entity, "approval is for different terms, expired or already used"
                             if stale else "no approval recorded")


async def consume(store, a: Authorization, result: str, now: Optional[datetime] = None) -> bool:
    """Record the use of a grant. For one-shot domains it is ATOMIC: True only for the one caller that consumed it
    (2026-10-28: a check-then-mark let 3 of 5 concurrent payment webhooks pass on one supplier_payment approval)."""
    if a.domain in ONE_SHOT:
        return await store.consume_once(a.id, now or _now(), result[:500])
    await store.mark(a.id, result=result[:500])
    return True


async def revoke(store, auth_id: str, reason: str, now: Optional[datetime] = None) -> None:
    await store.mark(auth_id, revoked_at=now or _now(), result=f"revoked: {reason}"[:500])
