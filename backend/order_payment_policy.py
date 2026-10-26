"""
Script: order_payment_policy.py
Purpose: ONE rule for whether the platform may spend money on a supplier for an order (FIXES_TRACKER #111).
         In production an order qualifies only if it was really paid: a `paid` Stripe payment whose reference is a
         LIVE checkout session (`cs_live_…`, also as `cs_live_…:<order id>`), and its customer is not a reserved
         test address. Test-mode (`cs_test_…`), simulated (`SIM-…`) and manual test markers (`manual-pi_…`) never
         qualify. Outside production nothing changes (the sandbox exercises fulfilment with test payments).
Process:
  is_reserved_test_email(email)            RFC 2606 / 6761 test domains + `.internal`
  is_live_paid_payment(status, provider, *refs)
  production_enforced(env)                 ENVIRONMENT == production (same convention as night_pipeline, outbound_policy)
  supplier_spend_refusal(db, order)        None = may spend; else the reason (used by routes.utils.trigger_supplier_fulfillment)
  live_paid_order_ids() / test_customer_user_ids()   SQLAlchemy subqueries with the SAME rule, for the stuck-order agent
Why: 2026-10-25 — every day the stuck-order agent re-detected four April TEST orders (Stripe test sessions, manual
  test markers, `@example.com` customers), tried a REAL supplier payment (Stripe Issuing — blocked only because it is
  off in live mode) and told the owner "the customer payment was received". Production has never had a paid live
  order: all 46 `paid` payments are test-mode, simulated or manual markers (read-only check).
Data Imported/Modified: none (read-only checks).
Data Sources: payments, users (PII DB).
Missing Data Delegation: n/a.
Last Updated: 2026-10-25
"""
from __future__ import annotations

import os
from typing import Mapping, Optional

LIVE_REF_PREFIX = "cs_live_"
RESERVED_TEST_DOMAINS = ("example.com", "example.org", "example.net")
RESERVED_TEST_TLDS = (".test", ".invalid", ".localhost", ".example", ".internal")


def is_reserved_test_email(email: Optional[str]) -> bool:
    """A reserved test address (RFC 2606 / 6761, plus `.internal`). An empty address is NOT treated as a test one."""
    e = (email or "").strip().lower()
    if "@" not in e:
        return False
    domain = e.rsplit("@", 1)[1].rstrip(".")
    return domain in RESERVED_TEST_DOMAINS or any(domain.endswith(t) or domain == t[1:] for t in RESERVED_TEST_TLDS)


def is_live_paid_payment(status: Optional[str], provider: Optional[str], *refs: Optional[str]) -> bool:
    if (status or "").strip().lower() != "paid" or (provider or "").strip().lower() != "stripe":
        return False
    return any((r or "").strip().startswith(LIVE_REF_PREFIX) for r in refs)


def production_enforced(env: Optional[Mapping[str, str]] = None) -> bool:
    env = os.environ if env is None else env
    return (env.get("ENVIRONMENT") or "").strip().lower() == "production"


async def supplier_spend_refusal(db, order, env: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """Why the platform must NOT spend on a supplier for this order (None = it may). Read-only."""
    if not production_enforced(env):
        return None
    from sqlalchemy import select
    from BACKEND_DATABASE_MODELS import Payment, User
    email = (await db.execute(select(User.email).where(User.id == order.user_id))).scalar_one_or_none()
    if is_reserved_test_email(email):
        return "customer is a reserved test address"
    rows = (await db.execute(select(Payment.status, Payment.provider, Payment.payment_intent_id,
                                    Payment.provider_transaction_id).where(Payment.order_id == order.id))).all()
    if not any(is_live_paid_payment(s, p, a, b) for s, p, a, b in rows):
        return "no live-mode paid payment (test, simulated, manual or unpaid only)"
    return None


def live_paid_order_ids():
    """Subquery: order ids that have a live-mode paid Stripe payment (same rule as is_live_paid_payment)."""
    from sqlalchemy import or_, select
    from BACKEND_DATABASE_MODELS import Payment
    return select(Payment.order_id).where(
        Payment.status == "paid", Payment.provider == "stripe",
        or_(Payment.payment_intent_id.like(LIVE_REF_PREFIX + "%"),
            Payment.provider_transaction_id.like(LIVE_REF_PREFIX + "%")),
    ).distinct()


def test_customer_user_ids():
    """Subquery: user ids whose email is a reserved test address (same rule as is_reserved_test_email)."""
    from sqlalchemy import func, or_, select
    from BACKEND_DATABASE_MODELS import User
    e = func.lower(User.email)
    conds = [e.like("%@" + d) for d in RESERVED_TEST_DOMAINS]
    conds += [e.like("%" + t) for t in RESERVED_TEST_TLDS] + [e.like("%@" + t[1:]) for t in RESERVED_TEST_TLDS]
    return select(User.id).where(or_(*conds))
