"""
Script: procurement.py
Purpose: Supplier procurement AFTER a customer payment, with separate, explicit authorizations (owner task
         2026-10-27, section 6). A confirmed customer payment creates a PREPARED procurement only - never a
         supplier order and never a supplier payment.
Process:
  material_terms()      the terms an approval is bound to: supplier, items (supplier offer, part, quantity, unit
                        cost, currency), shipping cost. Hash = supplier_authorization.terms_hash.
  prepare()             idempotent per (customer order, supplier) -> AWAITING_PO_AUTHORIZATION, with the
                        verification checks (supplier active, offer available + priced, fitment evidence) recorded.
  revalidate()          current offer data vs the approved terms; a change -> back to AWAITING_PO_AUTHORIZATION
                        (the old approval no longer matches the hash, so it cannot be used).
  authorize_po()        owner approval (purchase_order, bound to the terms) -> PO_AUTHORIZED.
  submit_po()           requires that approval; marks the attempt in flight BEFORE calling the supplier; a definite
                        failure -> SUBMISSION_FAILED; an ambiguous result (timeout / exception) -> EXCEPTION
                        'reconcile before retry' and is NEVER resubmitted automatically. The connectors'
                        place_order is a stub today (base_supplier returns success=False), so a real submission
                        currently ends in SUBMISSION_FAILED; the owner can place the order himself and record it
                        with record_manual_submission (still requires the PO approval).
  reconcile()           asks the supplier connection (supplier_ordering) for the order's status; only an
                        authoritative answer moves SUBMITTED -> SUPPLIER_CONFIRMED (+ tracking).
  pay_via_connector()   pays through the supplier connection (AliExpress afterpay) with that approval; the
                        fulfillment spend path then refuses that order, so it can never be paid twice.
  authorize_payment()   a SEPARATE owner approval (supplier_payment), only after SUBMITTED / SUPPLIER_CONFIRMED.
  payment_refusal()     the gate trigger_supplier_fulfillment calls before ANY supplier spend: None only when every
                        supplier of the order has a valid, unconsumed supplier_payment approval for the CURRENT
                        terms; the approval is consumed there, so one approval cannot pay twice.
  Every transition writes procurement_events (actor, from, to, detail).
Data Imported/Modified: procurement_orders, procurement_events, agent_authorizations (PII DB, migration 0042).
Data Sources: orders / order_items (PII DB), supplier_parts / suppliers / part_vehicle_fitment (catalog DB).
Last Updated: 2026-10-28 (payment_refusal releases only when it wins the atomic consume)
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import supplier_authorization as _auth

TERMINAL = ("DELIVERED", "CANCELLED")
SUBMITTED_STATES = ("SUBMITTED", "SUPPLIER_CONFIRMED")


class ProcurementError(Exception):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def material_terms(order_id: str, supplier_id: str, items: List[Dict[str, Any]],
                   shipping: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    norm = sorted(({"supplier_part_id": str(i["supplier_part_id"]), "part_id": str(i.get("part_id") or ""),
                    "quantity": int(i["quantity"]), "unit_cost": str(i.get("unit_cost")),
                    "currency": str(i.get("currency") or "ILS")} for i in items),
                  key=lambda x: x["supplier_part_id"])
    return {"order_id": str(order_id), "supplier_id": str(supplier_id), "items": norm,
            "shipping": {"cost": str((shipping or {}).get("cost")), "currency": str((shipping or {}).get("currency") or "ILS")}}


def idempotency_key(order_id: str, supplier_id: str) -> str:
    return f"proc:{order_id}:{supplier_id}"


# ── stores ─────────────────────────────────────────────────────────────────────────────────────────────
class InMemoryProcurementStore:
    def __init__(self):
        self.rows: Dict[str, Dict[str, Any]] = {}
        self.events: List[Dict[str, Any]] = []

    async def get_by_key(self, key: str) -> Optional[Dict[str, Any]]:
        return next((r for r in self.rows.values() if r["idempotency_key"] == key), None)

    async def get(self, proc_id: str) -> Optional[Dict[str, Any]]:
        return self.rows.get(proc_id)

    async def for_order(self, order_id: str) -> List[Dict[str, Any]]:
        return [r for r in self.rows.values() if r["order_id"] == str(order_id)]

    async def by_prefix(self, prefix: str) -> List[Dict[str, Any]]:
        return [r for r in self.rows.values() if r["id"].startswith(prefix)]

    async def open_rows(self, limit: int = 20) -> List[Dict[str, Any]]:
        return [r for r in self.rows.values() if r["state"] not in TERMINAL][:limit]

    async def by_thread(self, thread_id: str) -> List[Dict[str, Any]]:
        return [r for r in self.rows.values() if (r.get("submission_evidence") or {}).get("gmail_thread_id") == thread_id]

    async def insert(self, row: Dict[str, Any]) -> bool:
        if await self.get_by_key(row["idempotency_key"]):
            return False
        self.rows[row["id"]] = dict(row)
        return True

    async def update(self, proc_id: str, **fields) -> None:
        r = self.rows[proc_id]
        new = {**r, **fields}
        if new["state"] in ("SUBMITTED", "SUPPLIER_CONFIRMED", "SUPPLIER_PAID", "SHIPPED", "DELIVERED") \
                and not new.get("po_authorization_id"):
            raise ValueError("procurement_submitted_requires_po_authorization")
        if new["state"] in ("SUPPLIER_PAID", "SHIPPED", "DELIVERED") and not new.get("payment_authorization_id"):
            raise ValueError("procurement_paid_requires_payment_authorization")
        r.update(fields, updated_at=_now())

    async def claim(self, proc_id: str, state: str, flag: str, info: Dict[str, Any]) -> bool:
        """Atomic compare-and-set: only one caller may start a submission / payment for this row."""
        r = self.rows.get(proc_id)
        ev = r.get("submission_evidence") or {} if r else {}
        if not r or r["state"] != state or ev.get(flag):
            return False
        r["submission_evidence"] = {**ev, flag: True, **info}
        return True

    async def event(self, proc_id: str, from_state: Optional[str], to_state: str, actor: str,
                    detail: Dict[str, Any]) -> None:
        self.events.append({"procurement_id": proc_id, "from_state": from_state, "to_state": to_state,
                            "actor": actor, "detail": detail, "at": _now()})


class PgProcurementStore:
    _COLS = ("id::text AS id, order_id, order_number, supplier_id, state, items, terms, terms_hash, idempotency_key, "
             "po_authorization_id::text AS po_authorization_id, payment_authorization_id::text AS "
             "payment_authorization_id, supplier_reference, submission_evidence, tracking_number, last_error, "
             "created_at, updated_at")

    def __init__(self, session_factory=None):
        self._factory = session_factory

    def _session(self):
        if self._factory is None:
            from BACKEND_DATABASE_MODELS import pii_session_factory
            self._factory = pii_session_factory
        return self._factory()

    async def _select(self, where: str, params: Dict[str, Any], limit: int = 50) -> List[Dict[str, Any]]:
        from sqlalchemy import text
        async with self._session() as db:
            res = await db.execute(text(f"SELECT {self._COLS} FROM procurement_orders WHERE {where} "
                                        "ORDER BY created_at LIMIT :lim"), {**params, "lim": limit})
            return [dict(r._mapping) for r in res.fetchall()]

    async def get_by_key(self, key):
        rows = await self._select("idempotency_key = :k", {"k": key}, 1)
        return rows[0] if rows else None

    async def get(self, proc_id):
        rows = await self._select("id = CAST(:i AS uuid)", {"i": proc_id}, 1)
        return rows[0] if rows else None

    async def for_order(self, order_id):
        return await self._select("order_id = :o", {"o": str(order_id)})

    async def by_prefix(self, prefix):
        p = "".join(ch for ch in (prefix or "").lower() if ch in "0123456789abcdef-")
        return await self._select("id::text LIKE :p", {"p": p + "%"}, 5) if len(p) >= 4 else []

    async def open_rows(self, limit=20):
        return await self._select("state NOT IN ('DELIVERED','CANCELLED')", {}, limit)

    async def by_thread(self, thread_id):
        return await self._select("submission_evidence->>'gmail_thread_id' = :t", {"t": thread_id}, 5) if thread_id else []

    async def insert(self, row) -> bool:
        from sqlalchemy import text
        async with self._session() as db:
            res = await db.execute(text("""
                INSERT INTO procurement_orders (id, order_id, order_number, supplier_id, state, items, terms, terms_hash,
                                                idempotency_key, created_at, updated_at)
                VALUES (CAST(:id AS uuid), :order_id, :order_number, :supplier_id, :state, CAST(:items AS jsonb),
                        CAST(:terms AS jsonb), :terms_hash, :idempotency_key, NOW(), NOW())
                ON CONFLICT (idempotency_key) DO NOTHING RETURNING id"""),
                {**{k: row.get(k) for k in ("id", "order_id", "order_number", "supplier_id", "state", "terms_hash",
                                            "idempotency_key")},
                 "items": json.dumps(row["items"], default=str), "terms": json.dumps(row["terms"], default=str)})
            ok = res.scalar() is not None
            await db.commit()
            return ok

    async def update(self, proc_id, **fields) -> None:
        from sqlalchemy import text
        jsonb = {"items", "terms", "submission_evidence"}
        uuids = {"po_authorization_id", "payment_authorization_id"}
        allowed = jsonb | uuids | {"state", "terms_hash", "supplier_reference", "tracking_number", "last_error"}
        f = {k: (json.dumps(v, default=str) if k in jsonb and v is not None else v)
             for k, v in fields.items() if k in allowed}
        if not f:
            return
        sets = ", ".join(f"{k} = CAST(:{k} AS jsonb)" if k in jsonb else
                         f"{k} = CAST(:{k} AS uuid)" if k in uuids else f"{k} = :{k}" for k in f)
        async with self._session() as db:
            await db.execute(text(f"UPDATE procurement_orders SET {sets}, updated_at = NOW() "
                                  "WHERE id = CAST(:id AS uuid)"), {"id": proc_id, **f})
            await db.commit()

    async def claim(self, proc_id, state, flag, info) -> bool:
        """Atomic compare-and-set in ONE statement: two workers / two console commands cannot both win."""
        from sqlalchemy import text
        async with self._session() as db:
            res = await db.execute(text(f"""
                UPDATE procurement_orders
                   SET submission_evidence = coalesce(submission_evidence, '{{}}'::jsonb)
                                             || jsonb_build_object('{flag}', true) || CAST(:info AS jsonb),
                       updated_at = NOW()
                 WHERE id = CAST(:i AS uuid) AND state = :st
                   AND coalesce(submission_evidence->>'{flag}', 'false') <> 'true'
             RETURNING id"""), {"i": proc_id, "st": state, "info": json.dumps(info, default=str)})
            won = res.scalar() is not None
            await db.commit()
            return won

    async def event(self, proc_id, from_state, to_state, actor, detail) -> None:
        from sqlalchemy import text
        async with self._session() as db:
            await db.execute(text("INSERT INTO procurement_events (procurement_id, from_state, to_state, actor, detail) "
                                  "VALUES (CAST(:p AS uuid), :f, :t, :a, CAST(:d AS jsonb))"),
                             {"p": proc_id, "f": from_state, "t": to_state, "a": actor,
                              "d": json.dumps(detail, default=str)})
            await db.commit()


async def _move(store, row, to_state: str, actor: str, detail: Dict[str, Any], **fields) -> Dict[str, Any]:
    from_state = row["state"]                  # captured BEFORE the update (a store may share the row object)
    await store.update(row["id"], state=to_state, **fields)
    await store.event(row["id"], from_state, to_state, actor, detail)
    row.update(fields, state=to_state)
    return row


# ── lifecycle ──────────────────────────────────────────────────────────────────────────────────────────
async def prepare(store, *, order_id: str, order_number: str, supplier_id: str, items: List[Dict[str, Any]],
                  shipping: Optional[Dict[str, Any]], checks: Dict[str, Any], actor: str = "BOAZ") -> Dict[str, Any]:
    """Create (or return) the procurement for one customer order + supplier. Never contacts the supplier."""
    key = idempotency_key(order_id, supplier_id)
    existing = await store.get_by_key(key)
    if existing:
        return {"created": False, "procurement": existing}
    terms = material_terms(order_id, supplier_id, items, shipping)
    row = {"id": str(uuid.uuid4()), "order_id": str(order_id), "order_number": order_number,
           "supplier_id": str(supplier_id), "state": "AWAITING_PO_AUTHORIZATION", "items": terms["items"],
           "terms": terms, "terms_hash": _auth.terms_hash(terms), "idempotency_key": key}
    if not await store.insert(row):
        return {"created": False, "procurement": await store.get_by_key(key)}
    await store.event(row["id"], None, "AWAITING_PO_AUTHORIZATION", actor,
                      {"reason": "customer payment confirmed - preparation only", "checks": checks})
    return {"created": True, "procurement": row}


async def revalidate(store, row: Dict[str, Any], current_terms: Dict[str, Any], actor: str = "BOAZ") -> bool:
    """True when the approved terms still hold. A change resets the row to awaiting a NEW approval."""
    th = _auth.terms_hash(current_terms)
    if th == row["terms_hash"]:
        return True
    await _move(store, row, "AWAITING_PO_AUTHORIZATION", actor,
                {"reason": "material terms changed - re-approval required", "old_hash": row["terms_hash"],
                 "new_hash": th}, terms=current_terms, items=current_terms["items"], terms_hash=th)
    return False


async def authorize_po(store, auth_store, proc_id: str, approver: str, source: str = "owner_whatsapp") -> Dict[str, Any]:
    row = await store.get(proc_id)
    if not row:
        raise ProcurementError("procurement not found")
    if row["state"] not in ("AWAITING_PO_AUTHORIZATION", "SUBMISSION_FAILED", "PREPARED"):
        raise ProcurementError(f"cannot authorize a purchase order in state {row['state']}")
    a = await _auth.grant(auth_store, domain="purchase_order", scope="create_po", entity_type="procurement",
                          entity_id=row["id"], approved_by=approver, source=source, terms=row["terms"])
    await _move(store, row, "PO_AUTHORIZED", approver, {"authorization_id": a.id, "terms_hash": row["terms_hash"]},
                po_authorization_id=a.id)
    return row


async def submit_po(store, auth_store, proc_id: str, connector, current_terms: Dict[str, Any],
                    actor: str = "BOAZ") -> Dict[str, Any]:
    row = await store.get(proc_id)
    if not row:
        raise ProcurementError("procurement not found")
    if row["state"] in SUBMITTED_STATES or (row.get("submission_evidence") or {}).get("in_flight"):
        return {"submitted": False, "reason": "already submitted or in flight - reconcile, never resubmit",
                "state": row["state"]}
    if row["state"] == "EXCEPTION":
        return {"submitted": False, "reason": "ambiguous earlier attempt - reconcile with the supplier first"}
    if not await revalidate(store, row, current_terms, actor):
        return {"submitted": False, "reason": "terms changed - re-approval required"}
    try:
        a = await _auth.require(auth_store, "purchase_order", "procurement", row["id"], scope="create_po",
                                terms=current_terms)
    except _auth.AuthorizationError as e:
        await store.event(row["id"], row["state"], row["state"], actor, {"refused": e.reason, "check": "purchase_order"})
        return {"submitted": False, "reason": f"purchase order not authorized: {e.reason}"}
    if row["state"] != "PO_AUTHORIZED" or not await store.claim(
            row["id"], "PO_AUTHORIZED", "in_flight", {"started_at": _now().isoformat(), "authorization_id": a.id}):
        return {"submitted": False, "reason": "another submission is in flight or the order is not approved - "
                                              "reconcile, never resubmit"}
    row = await store.get(row["id"]) or row
    try:
        result = await connector.place_order(row)
    except Exception as e:                      # timeout / transport: the supplier may or may not have the order
        await _auth.consume(auth_store, a, "ambiguous submission - re-approval after reconciliation")
        await _move(store, row, "EXCEPTION", actor, {"reason": "ambiguous supplier result - reconcile before retry",
                                                     "error": type(e).__name__},
                    submission_evidence={"in_flight": False, "ambiguous": True}, last_error=type(e).__name__)
        return {"submitted": False, "reason": "ambiguous - reconcile before retry"}
    if getattr(result, "success", False) and getattr(result, "order_id", None):
        await _auth.consume(auth_store, a, f"submitted {result.order_id}")
        ev = {"in_flight": False, "evidence": "connector", **(getattr(result, "evidence", None) or {})}
        await _move(store, row, "SUBMITTED", actor, {"supplier_reference": result.order_id, **ev},
                    supplier_reference=str(result.order_id), submission_evidence=ev)
        return {"submitted": True, "supplier_reference": result.order_id, "evidence": ev}
    msg = str(getattr(result, "error_message", None) or getattr(result, "error", None)
              or "supplier rejected / not supported")
    await _move(store, row, "SUBMISSION_FAILED", actor, {"error": msg[:300]},
                submission_evidence={"in_flight": False, "definite_failure": True}, last_error=msg[:300])
    return {"submitted": False, "reason": msg[:200]}


async def reconcile(store, proc_id: str, connector, actor: str = "BOAZ") -> Dict[str, Any]:
    """Ask the supplier connection what it knows about a submitted / ambiguous order. Only an authoritative
    answer moves the state: found + confirmed -> SUPPLIER_CONFIRMED (tracking recorded). Never resubmits."""
    row = await store.get(proc_id)
    if not row:
        raise ProcurementError("procurement not found")
    ref = row.get("supplier_reference")
    if not ref:
        return {"changed": False, "reason": "no supplier order number recorded - check with the supplier manually"}
    st = await connector.get_status(ref)
    if not st.get("found"):
        await store.event(row["id"], row["state"], row["state"], actor, {"reconcile": "not found", "detail": st.get("reason")})
        return {"changed": False, "reason": st.get("reason") or "supplier has no record"}
    fields = {"tracking_number": st["tracking"]} if st.get("tracking") else {}
    if row["state"] == "SUBMITTED":
        await _move(store, row, "SUPPLIER_CONFIRMED", actor, {"supplier_status": st.get("status"), **fields}, **fields)
        return {"changed": True, "state": "SUPPLIER_CONFIRMED", **st}
    if fields:
        await store.update(row["id"], **fields)
    await store.event(row["id"], row["state"], row["state"], actor, {"reconcile": st})
    return {"changed": bool(fields), "state": row["state"], **st}


async def record_manual_submission(store, auth_store, proc_id: str, supplier_reference: str, approver: str) -> Dict[str, Any]:
    """The owner placed the order with the supplier himself; still requires the purchase-order approval."""
    row = await store.get(proc_id)
    if not row:
        raise ProcurementError("procurement not found")
    if row["state"] in SUBMITTED_STATES:
        raise ProcurementError("already submitted")
    if not (supplier_reference or "").strip():
        raise ProcurementError("a supplier order reference is required as evidence")
    a = await _auth.require(auth_store, "purchase_order", "procurement", row["id"], scope="create_po",
                            terms=row["terms"])
    await _auth.consume(auth_store, a, f"owner placed manually: {supplier_reference}")
    await _move(store, row, "SUBMITTED", approver, {"evidence": "owner_manual", "supplier_reference": supplier_reference},
                supplier_reference=supplier_reference.strip()[:120],
                submission_evidence={"in_flight": False, "evidence": "owner_manual"})
    return row


async def pay_via_connector(store, auth_store, proc_id: str, connector, current_terms: Dict[str, Any],
                            actor: str = "BOAZ") -> Dict[str, Any]:
    """Pay the supplier THROUGH its order connection (AliExpress afterpay). REAL MONEY. Requires a valid,
    unconsumed supplier_payment approval for the CURRENT terms; a definite failure keeps the approval (the owner
    may retry), an ambiguous one consumes it and parks the row in EXCEPTION. Never pays twice."""
    row = await store.get(proc_id)
    if not row:
        raise ProcurementError("procurement not found")
    if row["state"] != "PAYMENT_AUTHORIZED":
        return {"paid": False, "reason": f"state {row['state']} - supplier payment not authorized"}
    if not getattr(connector, "can_pay", False):
        return {"paid": False, "reason": "this supplier connection cannot pay - pay on the supplier's site"}
    if not await revalidate(store, row, current_terms, actor):
        return {"paid": False, "reason": "terms changed - re-approval required"}
    try:
        a = await _auth.require(auth_store, "supplier_payment", "procurement", row["id"], scope="pay_supplier",
                                terms=current_terms)
    except _auth.AuthorizationError as e:
        return {"paid": False, "reason": f"supplier payment not authorized: {e.reason}"}
    if not await store.claim(row["id"], "PAYMENT_AUTHORIZED", "pay_in_flight",
                             {"pay_started_at": _now().isoformat(), "pay_authorization_id": a.id}):
        return {"paid": False, "reason": "a payment for this order is already in flight - reconcile, never pay twice"}
    try:
        res = await connector.pay_order(row.get("supplier_reference") or "")
    except Exception as e:
        await _auth.consume(auth_store, a, "ambiguous payment - reconcile before any retry")
        await _move(store, row, "EXCEPTION", actor, {"reason": "ambiguous supplier payment - reconcile",
                                                     "error": type(e).__name__}, last_error=type(e).__name__)
        return {"paid": False, "reason": "payment outcome unknown - reconcile before retrying"}
    if getattr(res, "success", False):
        await _auth.consume(auth_store, a, "paid via connector")
        ev = {**(row.get("submission_evidence") or {}), "paid": True, "payment": getattr(res, "evidence", {})}
        await _move(store, row, "SUPPLIER_PAID", actor, {"payment": getattr(res, "evidence", {})},
                    submission_evidence=ev)
        return {"paid": True}
    cur = await store.get(row["id"]) or row                 # definite failure: release the claim, keep the approval
    await store.update(row["id"], submission_evidence={**(cur.get("submission_evidence") or {}), "pay_in_flight": False})
    await store.event(row["id"], row["state"], row["state"], actor, {"payment_failed": getattr(res, "error_message", "")})
    return {"paid": False, "reason": str(getattr(res, "error_message", "payment failed"))[:200]}


async def authorize_payment(store, auth_store, proc_id: str, approver: str, source: str = "owner_whatsapp") -> Dict[str, Any]:
    row = await store.get(proc_id)
    if not row:
        raise ProcurementError("procurement not found")
    if row["state"] not in SUBMITTED_STATES:
        raise ProcurementError(f"supplier payment can be approved only after the order is submitted (state {row['state']})")
    a = await _auth.grant(auth_store, domain="supplier_payment", scope="pay_supplier", entity_type="procurement",
                          entity_id=row["id"], approved_by=approver, source=source, terms=row["terms"])
    await _move(store, row, "PAYMENT_AUTHORIZED", approver, {"authorization_id": a.id}, payment_authorization_id=a.id)
    return row


async def payment_refusal(store, auth_store, order_id: str, current_terms_by_supplier: Dict[str, Dict[str, Any]],
                          consume: bool = True) -> Optional[str]:
    """None = every supplier of this order may be paid now (and the approvals are consumed). Else the reason."""
    rows = {r["supplier_id"]: r for r in await store.for_order(order_id)}
    if not rows:
        return "no procurement prepared for this order"
    grants = []
    for sid, terms in current_terms_by_supplier.items():
        r = rows.get(str(sid))
        if not r:
            return f"supplier {str(sid)[:8]} has no procurement record"
        if r["state"] != "PAYMENT_AUTHORIZED":
            return f"procurement {r['id'][:8]} is {r['state']} - supplier payment not authorized"
        try:
            grants.append((r, await _auth.require(auth_store, "supplier_payment", "procurement", r["id"],
                                                  scope="pay_supplier", terms=terms)))
        except _auth.AuthorizationError as e:
            return f"procurement {r['id'][:8]}: {e.reason}"
    if consume:
        for r, a in grants:
            # atomic: a concurrent release that consumed this approval first wins; this caller is refused
            if not await _auth.consume(auth_store, a, "released to fulfillment"):
                await store.event(r["id"], r["state"], r["state"], "fulfillment",
                                  {"refused": "supplier_payment approval already used by a concurrent release",
                                   "authorization_id": a.id})
                return f"procurement {r['id'][:8]}: supplier payment approval already used"
            await store.event(r["id"], r["state"], r["state"], "fulfillment",
                              {"payment_authorization_consumed": a.id})
    return None


def gate_bypassed(env=None) -> bool:
    """Outside production only, a sandbox harness may bypass the gate explicitly. Never in production."""
    env = os.environ if env is None else env
    if (env.get("ENVIRONMENT") or "").strip().lower() == "production":
        return False
    return (env.get("PROCUREMENT_GATE_SANDBOX_BYPASS") or "0").strip() == "1"


# ── DB helpers for trigger_supplier_fulfillment ────────────────────────────────────────────────────────
async def current_terms_for_order(order, order_items) -> Dict[str, Dict[str, Any]]:
    """Group the order's lines by supplier from the CURRENT catalog offer rows (catalog DB)."""
    from sqlalchemy import select
    from BACKEND_DATABASE_MODELS import Supplier, SupplierPart, async_session_factory
    ids = [oi.supplier_part_id for oi in order_items if oi.supplier_part_id]
    groups: Dict[str, Dict[str, Any]] = {}
    if not ids:
        return {}
    async with async_session_factory() as cat:
        rows = (await cat.execute(select(SupplierPart.id, SupplierPart.part_id, SupplierPart.supplier_id,
                                         SupplierPart.price_ils, SupplierPart.shipping_cost_ils,
                                         SupplierPart.is_available, Supplier.is_active)
                                  .join(Supplier, Supplier.id == SupplierPart.supplier_id)
                                  .where(SupplierPart.id.in_(ids)))).all()
    meta = {str(r[0]): r for r in rows}
    for oi in order_items:
        m = meta.get(str(oi.supplier_part_id))
        if not m:
            continue
        sid = str(m[2])
        g = groups.setdefault(sid, {"items": [], "shipping": {"cost": m[4], "currency": "ILS"},
                                    "checks": {"supplier_active": bool(m[6]), "offers_available": True,
                                               "fitment": "not verified at procurement"}})
        g["items"].append({"supplier_part_id": str(m[0]), "part_id": str(m[1]), "quantity": int(oi.quantity or 1),
                           "unit_cost": m[3], "currency": "ILS"})
        g["checks"]["offers_available"] = g["checks"]["offers_available"] and bool(m[5]) and (m[3] or 0) > 0
    return {sid: {**g, "terms": material_terms(str(order.id), sid, g["items"], g["shipping"])}
            for sid, g in groups.items()}


async def current_terms_for_procurement(row: Dict[str, Any]) -> Dict[str, Any]:
    """The CURRENT material terms of one procurement, re-read from the catalog offers it was prepared from."""
    from sqlalchemy import select
    from BACKEND_DATABASE_MODELS import SupplierPart, async_session_factory
    ids = [i["supplier_part_id"] for i in row["items"]]
    async with async_session_factory() as cat:
        rows = (await cat.execute(select(SupplierPart.id, SupplierPart.part_id, SupplierPart.price_ils,
                                         SupplierPart.shipping_cost_ils).where(SupplierPart.id.in_(ids)))).all()
    meta = {str(r[0]): r for r in rows}
    items, ship = [], None
    for i in row["items"]:
        m = meta.get(i["supplier_part_id"])
        if not m:
            raise ProcurementError(f"offer {i['supplier_part_id'][:8]} no longer exists")
        items.append({"supplier_part_id": i["supplier_part_id"], "part_id": str(m[1]), "quantity": int(i["quantity"]),
                      "unit_cost": m[2], "currency": "ILS"})
        ship = m[3] if ship is None else ship
    return material_terms(row["order_id"], row["supplier_id"], items, {"cost": ship, "currency": "ILS"})


async def gate_paid_order(order, order_items, *, store=None, auth_store=None, notify=None) -> Optional[str]:
    """Called by trigger_supplier_fulfillment before ANY supplier spend. Prepares procurement on first sight
    (idempotent) and refuses until the owner has separately approved the purchase order AND the payment."""
    if gate_bypassed():
        return None
    store = store or PgProcurementStore()
    auth_store = auth_store or _auth.PgAuthStore()
    groups = await current_terms_for_order(order, order_items)
    if not groups:
        return "no supplier offers resolved for this order - manual handling"
    created = 0
    for sid, g in groups.items():
        res = await prepare(store, order_id=str(order.id), order_number=order.order_number or "", supplier_id=sid,
                            items=g["items"], shipping=g["shipping"], checks=g["checks"])
        created += int(res["created"])
    if created and notify:
        try:
            await notify("orders", f"רכש ממתין לאישורך — {order.order_number}",
                         f"התקבל תשלום לקוח להזמנה {order.order_number}. הוכנו {created} הזמנות רכש לספקים "
                         "— שום דבר לא הוזמן ולא שולם. כתוב *רכש* לפרטים ולאישור.",
                         severity="warning", alert_key=f"procurement_{order.id}", cooldown_s=30 * 86400)
        except Exception:
            pass
    for r in await store.for_order(str(order.id)):
        if (r.get("submission_evidence") or {}).get("connector") == "aliexpress_ds":
            return (f"procurement {r['id'][:8]} is paid through its supplier connection (אשרתשלוםספק) - "
                    "no second payment through fulfillment")
    return await payment_refusal(store, auth_store, str(order.id), {sid: g["terms"] for sid, g in groups.items()})
