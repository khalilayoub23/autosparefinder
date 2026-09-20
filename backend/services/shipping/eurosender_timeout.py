"""
Script: eurosender_timeout.py
Purpose: The Eurosender order-creation timeout/idempotency state machine.
         Eurosender's API exposes NO idempotency key and NO persistent quote
         id (confirmed in the 2026-09-08 sandbox-contract research) — the
         ONLY reconciliation handle after an ambiguous POST /v1/orders
         failure is customerInternalReference, echoed back by
         GET /v1/orders/{orderCode}. This module encodes the safe recovery
         protocol so a retry NEVER creates a second real shipment.
Process:
  States (orders.eurosender_status):
    pending_creation              -> set immediately before POST
    created                       -> POST succeeded, orderCode persisted
    timeout_pending_reconciliation -> POST result ambiguous (timeout/network
                                       error with no orderCode) — DO NOT
                                       retry the POST from this state
    reconciled                    -> a later webhook/GET matched this order
                                      via customerInternalReference
    failed                        -> a clean (non-ambiguous) 4xx/5xx; safe to
                                      alert ops, NOT safe to auto-retry POST
                                      without human review
    cancelled                     -> order_cancelled webhook received
Data Imported/Modified: none directly (pure state-transition helpers) —
  callers apply the returned state to their own Order-like object.
Missing Data Delegation: if a webhook's orderCode never resolves to a
  customerInternalReference match, the order stays timeout_pending_reconciliation
  forever until a human intervenes (manual_intervention_required=True) — never
  silently retried.
Last Updated: 2026-09-08
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class EurosenderStatus(str, Enum):
    PENDING_CREATION = "pending_creation"
    CREATED = "created"
    TIMEOUT_PENDING_RECONCILIATION = "timeout_pending_reconciliation"
    RECONCILED = "reconciled"
    LABEL_READY = "label_ready"
    AWAITING_CUSTOMS = "awaiting_customs"
    FAILED = "failed"
    CANCELLED = "cancelled"


# States from which a fresh POST /v1/orders is safe to attempt.
# CRITICAL: TIMEOUT_PENDING_RECONCILIATION is deliberately NOT in this set —
# an automatic retry from that state could create a second real shipment.
RETRY_SAFE_STATES: frozenset[EurosenderStatus] = frozenset({
    EurosenderStatus.FAILED,  # a clean, non-ambiguous failure (e.g. 422 validation
                              # error) is safe to retry ONLY after the underlying
                              # cause (bad data) has been fixed — never automatically.
})


@dataclass(frozen=True)
class ReconciliationResult:
    matched: bool
    order_code: str | None
    reason: str


def can_attempt_creation(current_status: str | None) -> bool:
    """True only if no creation attempt is in flight or ambiguous.

    None (never attempted) and FAILED (clean failure, human has reviewed and
    is re-triggering deliberately) are the only safe starting points besides
    a fresh order. TIMEOUT_PENDING_RECONCILIATION always returns False.
    """
    if current_status is None:
        return True
    try:
        status = EurosenderStatus(current_status)
    except ValueError:
        return True  # unrecognized/legacy value — treat as never-attempted
    return status in RETRY_SAFE_STATES


def reconcile(
    customer_internal_reference: str,
    pending_order_ids: set[str],
    get_order_response: dict,
) -> ReconciliationResult:
    """Given a GET /v1/orders/{orderCode} response for an orderCode that
    arrived via an unrecognized webhook, determine whether it belongs to one
    of our own timeout_pending_reconciliation orders.

    customer_internal_reference: what THIS webhook's order claims to be ours
                                  (from get_order_response, not the webhook
                                  payload itself — the webhook payload does
                                  NOT carry customerInternalReference).
    pending_order_ids: the set of str(order.id) for every order currently in
                        TIMEOUT_PENDING_RECONCILIATION.
    """
    ref = str(get_order_response.get("customerInternalReference") or "").strip()
    order_code = get_order_response.get("orderCode")

    if not ref:
        return ReconciliationResult(
            matched=False, order_code=order_code,
            reason="GET /v1/orders response carried no customerInternalReference — cannot reconcile.",
        )
    if ref not in pending_order_ids:
        return ReconciliationResult(
            matched=False, order_code=order_code,
            reason=f"customerInternalReference '{ref}' does not match any timeout_pending_reconciliation order.",
        )
    return ReconciliationResult(
        matched=True, order_code=order_code,
        reason=f"orderCode {order_code} reconciled to pending order {ref} via customerInternalReference.",
    )
