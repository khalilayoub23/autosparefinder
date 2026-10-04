"""
Script: email_agent/policy.py
Purpose: Response policy. Decides, for a classified email with resolved context, whether a reply
         is (a) safe for FUTURE automation, (b) requires human approval, or (c) needs no reply -
         and which draft, if any, may be prepared.
Process:
  SAFE FOR FUTURE AUTOMATION (a draft is prepared, still not sendable):
    order / shipping status when the order is verified and belongs to the authenticated sender;
    a request for the missing order number; a request for missing vehicle / part details;
    a supplier acknowledgement for a verified supplier.
  HUMAN APPROVAL REQUIRED (no draft is prepared):
    refund, cancellation, price change, financial commitment, address change, legal / dispute,
    complaint, security / account recovery, customs, payment / billing, unknown, a risky
    attachment, an unauthenticated sender, and any ambiguous or conflicting context.
  NO REPLY: newsletters, spam, automated / platform notifications without a serious flag.
  SENDING: every decision is sendable=False. send_allowed() returns False unconditionally in
  this phase - it does not read the environment or any flag, so no configuration can enable it.
Data Imported/Modified: none.
Last Updated: 2026-10-04
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from email_agent.classifier import SERIOUS_FLAGS, Classification
from email_agent.context import ResolvedContext

TIER_SAFE = "safe_for_future_automation"
TIER_HUMAN = "human_approval_required"
TIER_NONE = "no_reply"

HUMAN_CATEGORIES = ("complaint_dispute", "refund_cancellation", "payment_billing",
                    "account_security", "unknown")
HUMAN_FLAGS = ("refund", "cancellation", "dispute_legal", "complaint", "price_change",
               "financial_commitment", "address_change", "security", "customs",
               "risky_attachment", "unauthenticated_sender")
NOTIFICATION_CATEGORIES = ("automated_notification", "shipping_eurosender", "ebay", "aliexpress")
DRAFT_ACTIONS = ("draft_order_status", "draft_request_order_number", "draft_request_details",
                 "draft_supplier_ack")


@dataclass
class PolicyDecision:
    tier: str
    recommended_action: str
    requires_human: bool
    reasons: List[str] = field(default_factory=list)
    sendable: bool = False

    @property
    def wants_draft(self) -> bool:
        return self.recommended_action in DRAFT_ACTIONS

    def to_dict(self) -> dict:
        return {"tier": self.tier, "recommended_action": self.recommended_action,
                "requires_human": self.requires_human, "reasons": self.reasons, "sendable": False}


def send_allowed(decision: PolicyDecision | None = None) -> bool:
    """Phase 1: never. Kept as the single future enforcement point for an autonomous send path."""
    return False


def _human(reasons: List[str]) -> PolicyDecision:
    return PolicyDecision(TIER_HUMAN, "escalate_to_human", True, reasons)


def decide(c: Classification, ctx: ResolvedContext, already_answered: bool = False) -> PolicyDecision:
    cat, flags = c.classification, list(c.risk_flags)

    if cat in ("spam_irrelevant", "newsletter_marketing"):
        return PolicyDecision(TIER_NONE, "ignore", False, [f"{cat}: no reply"])

    if cat in NOTIFICATION_CATEGORIES:
        serious = [f for f in flags if f in SERIOUS_FLAGS]
        if serious:
            return _human([f"notification carries a serious topic: {', '.join(serious)}"])
        if ctx.order.get("status") in ("ambiguous", "sender_mismatch"):
            return _human([f"order context is {ctx.order['status']}: {ctx.order.get('reason', '')}"])
        return PolicyDecision(TIER_NONE, "log_only", False, [f"{cat}: informational, no reply"])

    if cat in HUMAN_CATEGORIES:
        return _human([f"category {cat} always requires human approval"])

    risky = [f for f in flags if f in HUMAN_FLAGS]
    if risky:
        return _human([f"risk flags: {', '.join(risky)}"])
    if not ctx.sender_authenticated:
        return _human(["sender was not authenticated by Gmail"])
    for name, part in (("order", ctx.order), ("customer", ctx.customer), ("supplier", ctx.supplier)):
        if part.get("status") in ("ambiguous", "sender_mismatch"):
            return _human([f"{name} context is {part['status']}: {part.get('reason', '')}"])

    if already_answered:
        return PolicyDecision(TIER_NONE, "log_only", False, ["thread already has a later reply from us"])

    if cat == "order_shipping":
        if ctx.order.get("status") == "resolved" and ctx.customer.get("status") == "resolved":
            return PolicyDecision(TIER_SAFE, "draft_order_status", False,
                                  ["order verified in DB and owned by the authenticated sender"])
        return PolicyDecision(TIER_SAFE, "draft_request_order_number", False,
                              ["no verified order: ask for the order number (non-sensitive)"])
    if cat == "customer_inquiry":
        return PolicyDecision(TIER_SAFE, "draft_request_details", False,
                              ["general inquiry: ask for vehicle / part details (non-sensitive)"])
    if cat == "supplier":
        if ctx.supplier.get("status") == "resolved":
            return PolicyDecision(TIER_SAFE, "draft_supplier_ack", False, ["verified supplier: acknowledgement"])
        return _human(["supplier is not verified"])
    return _human([f"no policy rule for category {cat}"])
