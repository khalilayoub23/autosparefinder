"""
Script: email_agent/classifier.py
Purpose: Classify an inbound email into one explicit category with a confidence, a reason and
         risk flags. Deterministic (no LLM call - background LLM use is not allowed without a
         budget gate), but schema-first so an LLM classifier can be plugged in later: any
         classifier's output must pass validate_classification().
Process: evidence is weighed in this order, never from the subject line alone -
           Gmail SPAM label -> authenticated platform sender -> bulk / automated headers ->
           resolved supplier -> content keywords (he / en / ar) over the new text of the
           message, falling back to the earlier inbound messages of the thread -> unknown.
         Risk flags (refund, cancellation, dispute, price, address, security, customs ...) are
         detected independently of the category; the policy layer turns them into escalation.
         A classification may cite only identifiers that context resolution verified.
Data Imported/Modified: none.
Last Updated: 2026-10-04
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Tuple

from email_agent.context import ResolvedContext
from email_agent.normalize import NormalizedEmail
from email_agent.senders import is_automated_address

CATEGORIES = (
    "customer_inquiry", "order_shipping", "supplier", "shipping_eurosender", "ebay", "aliexpress",
    "payment_billing", "account_security", "complaint_dispute", "refund_cancellation",
    "automated_notification", "newsletter_marketing", "spam_irrelevant", "unknown",
)

ACTIONS = ("draft_order_status", "draft_request_order_number", "draft_request_details",
           "draft_supplier_ack", "escalate_to_human", "log_only", "ignore")

_KW: Dict[str, Tuple[str, ...]] = {
    "order_shipping": ("order", "tracking", "track", "shipment", "shipping", "delivery", "delivered",
                       "parcel", "package", "where is my", "הזמנה", "משלוח", "מעקב", "חבילה", "הגיע",
                       "מתי יגיע", "طلب", "طلبي", "شحن", "توصيل", "الشحنة"),
    "customer_inquiry": ("part", "parts", "price", "fit", "fits", "compatible", "oem", "available",
                         "in stock", "quote", "חלק", "חלקים", "מחיר", "מתאים", "זמין", "במלאי", "רכב",
                         "הצעת מחיר", "קטלוגי", "قطعة", "قطع", "سعر", "متوفر", "سيارة"),
    "payment_billing": ("invoice", "receipt", "payment", "charged", "charge", "billing", "paid twice",
                        "חשבונית", "קבלה", "תשלום", "חיוב", "חויבתי", "فاتورة", "دفع", "الدفع"),
}

_FLAGS: Dict[str, Tuple[str, ...]] = {
    "refund": ("refund", "money back", "reimburse", "החזר כספי", "זיכוי", "החזר", "استرداد", "استرجاع"),
    "cancellation": ("cancel", "cancellation", "ביטול", "לבטל", "בטלו", "إلغاء", "الغاء"),
    "dispute_legal": ("dispute", "chargeback", "lawyer", "attorney", "legal action", "lawsuit", "court",
                      "case opened", "claim opened", "small claims", "תביעה", "עורך דין", "משפטי",
                      "הכחשת עסקה", "בית משפט", "محامي", "دعوى", "نزاع"),
    "complaint": ("complaint", "unacceptable", "scam", "fraud", "terrible", "worst", "disappointed",
                  "תלונה", "רמאות", "הונאה", "מאוכזב", "מאוכזבת", "חוצפה", "شكوى", "احتيال", "نصب"),
    "price_change": ("discount", "price match", "lower the price", "cheaper", "coupon", "הנחה",
                     "להוריד מחיר", "קופון", "זול יותר", "خصم", "تخفيض"),
    "financial_commitment": ("wire transfer", "bank transfer", "bank details", "payment request",
                             "pay now", "overdue", "העברה בנקאית", "פרטי בנק", "דרישת תשלום",
                             "تحويل بنكي"),
    "address_change": ("change my address", "change the address", "new address", "wrong address",
                       "update address", "שינוי כתובת", "כתובת חדשה", "לשנות את הכתובת", "כתובת שגויה",
                       "تغيير العنوان", "عنوان جديد"),
    "security": ("password", "verification code", "2fa", "two-factor", "account recovery", "hacked",
                 "security alert", "new sign-in", "suspicious", "סיסמה", "סיסמא", "קוד אימות", "נפרץ",
                 "התחברות חשודה", "كلمة المرور", "رمز التحقق", "اختراق"),
    "customs": ("customs", "import duty", "duties", "clearance", "מכס", "עמילות", "جمارك"),
}

# Flags that make even an automated / platform notification worth a human look.
SERIOUS_FLAGS = ("refund", "cancellation", "dispute_legal", "financial_commitment", "security", "customs")


def _hits(text: str, keywords: Tuple[str, ...]) -> List[str]:
    found = []
    for kw in keywords:
        if kw.isascii():
            if re.search(rf"(?<![a-z0-9]){re.escape(kw)}(?![a-z0-9])", text):
                found.append(kw)
        elif kw in text:
            found.append(kw)
    return found


@dataclass
class Classification:
    classification: str
    confidence: float
    reason: str
    thread_id: str
    message_id: str
    sender: str
    references: Dict[str, str] = field(default_factory=dict)
    risk_flags: List[str] = field(default_factory=list)
    recommended_action: str = "escalate_to_human"   # set by the policy layer
    requires_human: bool = True                     # set by the policy layer

    def to_dict(self) -> dict:
        return asdict(self)


def detect_flags(email: NormalizedEmail, ctx: ResolvedContext) -> List[str]:
    text = f"{email.subject}\n{email.new_text or email.body_text}".lower()
    flags = [name for name, kws in _FLAGS.items() if _hits(text, kws)]
    if any(a.get("risky") for a in email.attachments):
        flags.append("risky_attachment")
    if not ctx.sender_authenticated:
        flags.append("unauthenticated_sender")
    return flags


def classify(email: NormalizedEmail, thread: List[NormalizedEmail], ctx: ResolvedContext) -> Classification:
    flags = detect_flags(email, ctx)
    text = f"{email.subject}\n{email.new_text or email.body_text}".lower()
    h = email.headers
    kind, authed = ctx.sender_kind, ctx.sender_authenticated

    def done(cat: str, conf: float, reason: str) -> Classification:
        return Classification(classification=cat, confidence=round(max(0.0, min(conf, 1.0)), 2),
                              reason=reason, thread_id=email.thread_id, message_id=email.message_id,
                              sender=email.sender_email, references=ctx.references(), risk_flags=flags)

    if "SPAM" in email.labels:
        return done("spam_irrelevant", 0.95, "Gmail labelled the message SPAM")

    platform_cat = {"eurosender": "shipping_eurosender", "ebay": "ebay", "aliexpress": "aliexpress",
                    "payment": "payment_billing"}
    if kind in platform_cat or kind == "google":
        if not authed:
            return done("unknown", 0.3, f"sender claims a {kind} address but Gmail did not authenticate it")
        if kind == "google":
            if "security" in flags:
                return done("account_security", 0.9, "authenticated Google sender with security wording")
            return done("automated_notification", 0.8, "authenticated Google system sender")
        return done(platform_cat[kind], 0.95, f"authenticated sender domain {email.sender_domain} ({kind})")

    bulk = ("list-unsubscribe" in h or "list-id" in h
            or h.get("precedence", "").strip().lower() in ("bulk", "list", "junk"))
    auto = (h.get("auto-submitted", "no").strip().lower() not in ("", "no")
            or "x-autoreply" in h or is_automated_address(email.sender_email))
    if ctx.supplier.get("status") != "resolved" and ctx.customer.get("status") != "resolved":
        if bulk:
            return done("newsletter_marketing", 0.85, "bulk-mail headers (List-Unsubscribe / List-Id / Precedence)")
        if auto:
            return done("automated_notification", 0.8, "automated sender (Auto-Submitted header or no-reply address)")

    if ctx.supplier.get("status") == "resolved":
        return done("supplier", 0.9, "authenticated sender domain matches exactly one supplier website")

    def content(t: str, origin: str):
        if "security" in flags and origin == "message":
            return "account_security", ["security"]
        if origin == "message" and ("dispute_legal" in flags or "complaint" in flags):
            return "complaint_dispute", [f for f in ("dispute_legal", "complaint") if f in flags]
        if origin == "message" and ("refund" in flags or "cancellation" in flags):
            return "refund_cancellation", [f for f in ("refund", "cancellation") if f in flags]
        for cat in ("payment_billing", "order_shipping", "customer_inquiry"):
            hits = _hits(t, _KW[cat])
            if hits:
                return cat, hits
        return None, []

    cat, hits = content(text, "message")
    origin = "this message"
    if not cat:
        earlier = "\n".join(f"{m.subject}\n{m.new_text}" for m in thread
                            if m.is_inbound and m.message_id != email.message_id).lower()
        if earlier.strip():
            cat, hits = content(earlier, "thread")
            origin = "earlier messages in the thread"
    if cat:
        conf = 0.55 + 0.1 * min(len(hits), 3)
        if ctx.customer.get("status") == "resolved":
            conf += 0.05
        if origin != "this message":
            conf -= 0.15
        return done(cat, conf, f"content signals in {origin}: {', '.join(hits[:5])}")

    return done("unknown", 0.2, "no sender, header or content signal matched a known category")


def validate_classification(c: Classification, ctx: ResolvedContext) -> List[str]:
    """Schema + no-fabrication check. Returns a list of problems (empty = valid)."""
    problems = []
    if c.classification not in CATEGORIES:
        problems.append(f"invalid classification {c.classification!r}")
    if not isinstance(c.confidence, (int, float)) or not 0.0 <= c.confidence <= 1.0:
        problems.append("confidence missing or out of range")
    if not c.reason:
        problems.append("reason missing")
    if not c.message_id or not c.thread_id:
        problems.append("message_id / thread_id missing")
    if c.recommended_action not in ACTIONS:
        problems.append(f"invalid recommended_action {c.recommended_action!r}")
    if not isinstance(c.requires_human, bool):
        problems.append("requires_human must be a bool")
    verified = ctx.references()
    for key, value in (c.references or {}).items():
        if verified.get(key) != value:
            problems.append(f"reference {key} is not a DB-verified identifier")
    return problems
