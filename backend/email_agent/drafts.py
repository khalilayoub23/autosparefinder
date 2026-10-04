"""
Script: email_agent/drafts.py
Purpose: Prepare a reply DRAFT for the policy's safe actions and verify it. Truth-only: the text
         is built from fixed he / en / ar templates filled exclusively with DB-verified context
         (order number, order status, real carrier tracking). No price, discount, promise,
         supplier name or unverified fact can appear, because no template has a slot for one.
Process:
  build_draft()        action + email + context -> Draft (to, subject, body, reason, facts_used).
                       Raises DraftNotPossible when the verified facts cannot support the reply
                       (e.g. an order status the templates do not cover) - the caller escalates.
  build_rfc822()       Draft -> base64url RFC822 with In-Reply-To / References so Gmail attaches
                       it to the original thread.
  verify_gmail_draft() post-condition on the draft READ BACK from Gmail: id present, thread
                       matches, recipient matches the source sender, body matches, it carries
                       the DRAFT label and not SENT.
Data Imported/Modified: none (pure functions).
Last Updated: 2026-10-04
"""
from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Any, Dict, List

from email_agent.context import ResolvedContext
from email_agent.normalize import MalformedMessage, NormalizedEmail, normalize_message


class DraftNotPossible(Exception):
    pass


@dataclass
class Draft:
    to: str
    subject: str
    body: str
    language: str
    action: str
    reason: str
    facts_used: Dict[str, str] = field(default_factory=dict)


def detect_language(text: str) -> str:
    he = len(re.findall(r"[֐-׿]", text or ""))
    ar = len(re.findall(r"[؀-ۿ]", text or ""))
    if ar > he and ar >= 3:
        return "ar"
    if he >= 3:
        return "he"
    return "en"


_STATUS = {
    "pending_payment": {"he": "ממתינה לתשלום", "en": "awaiting payment", "ar": "بانتظار الدفع"},
    "paid": {"he": "התשלום התקבל וההזמנה בטיפול", "en": "paid and being prepared", "ar": "تم الدفع والطلب قيد التجهيز"},
    "processing": {"he": "בטיפול", "en": "being processed", "ar": "قيد المعالجة"},
    "supplier_ordered": {"he": "הוזמנה מהספק וממתינה למשלוח", "en": "ordered and awaiting dispatch",
                         "ar": "تم طلبها وبانتظار الشحن"},
    "shipped": {"he": "נשלחה", "en": "shipped", "ar": "تم شحنها"},
    "delivered": {"he": "נמסרה", "en": "delivered", "ar": "تم تسليمها"},
}

_T = {
    "greet": {"he": "שלום{name},", "en": "Hello{name},", "ar": "مرحباً{name}،"},
    "sign": {"he": "בברכה,\nצוות AutoSpareFinder", "en": "Best regards,\nThe AutoSpareFinder team",
             "ar": "مع التحية،\nفريق AutoSpareFinder"},
    "order_status": {
        "he": "תודה שפנית אלינו. בדקנו את הזמנה {order_number}: ההזמנה {status}.",
        "en": "Thank you for contacting us. We checked order {order_number}: it is {status}.",
        "ar": "شكراً لتواصلك معنا. تحققنا من الطلب {order_number}: الطلب {status}."},
    "tracking": {"he": "מספר מעקב: {tracking_number}", "en": "Tracking number: {tracking_number}",
                 "ar": "رقم التتبع: {tracking_number}"},
    "tracking_url": {"he": "קישור למעקב: {tracking_url}", "en": "Tracking link: {tracking_url}",
                     "ar": "رابط التتبع: {tracking_url}"},
    "more": {"he": "לכל שאלה נוספת אפשר להשיב למייל הזה.",
             "en": "If you have any other question, just reply to this email.",
             "ar": "لأي سؤال آخر يمكنك الرد على هذه الرسالة."},
    "need_order": {
        "he": "תודה שפנית אלינו. כדי שנוכל לבדוק את ההזמנה, נשמח לקבל את מספר ההזמנה "
              "(מופיע במייל אישור ההזמנה) מכתובת המייל שבה בוצעה ההזמנה.",
        "en": "Thank you for contacting us. So that we can check your order, please send us the order "
              "number (it appears in your order confirmation email) from the email address used for the order.",
        "ar": "شكراً لتواصلك معنا. لكي نتمكن من فحص طلبك، نرجو إرسال رقم الطلب "
              "(يظهر في رسالة تأكيد الطلب) من البريد الإلكتروني المستخدم في الطلب."},
    "need_details": {
        "he": "תודה שפנית אלינו. כדי שנאתר את החלק המתאים לרכב שלך, נשמח לקבל את מספר הרישוי "
              "או יצרן, דגם ושנת ייצור, ואת שם החלק או מספר ה-OEM אם ידוע.",
        "en": "Thank you for contacting us. To find the part that fits your car, please send us the "
              "licence plate number, or the make, model and year, and the part name or OEM number if you have it.",
        "ar": "شكراً لتواصلك معنا. لكي نجد القطعة المناسبة لسيارتك، نرجو إرسال رقم اللوحة "
              "أو الشركة المصنعة والموديل وسنة الصنع، واسم القطعة أو رقم OEM إن وجد."},
    "supplier_ack": {
        "he": "תודה, קיבלנו את הודעתך והיא הועברה לטיפול הצוות שלנו.",
        "en": "Thank you, we have received your message and it is with our team.",
        "ar": "شكراً، استلمنا رسالتك وهي قيد المتابعة لدى فريقنا."},
}


def _reply_subject(subject: str) -> str:
    s = (subject or "").strip()
    return s if re.match(r"(?i)^(re|תשובה|رد)\s*:", s) else f"Re: {s}".strip()


def build_draft(action: str, email: NormalizedEmail, ctx: ResolvedContext) -> Draft:
    lang = detect_language(f"{email.subject}\n{email.new_text or email.body_text}")
    if not email.reply_address:
        raise DraftNotPossible("source email has no reply address")
    facts: Dict[str, str] = {}
    name = ""
    if ctx.customer.get("status") == "resolved" and ctx.customer.get("full_name"):
        name = " " + str(ctx.customer["full_name"]).split()[0]
        facts["customer_first_name"] = name.strip()
    lines: List[str] = [_T["greet"][lang].format(name=name), ""]

    if action == "draft_order_status":
        if ctx.order.get("status") != "resolved" or ctx.customer.get("status") != "resolved":
            raise DraftNotPossible("order status draft requires a verified order owned by the sender")
        label = _STATUS.get(ctx.order.get("order_status", ""))
        if not label:
            raise DraftNotPossible(f"order status {ctx.order.get('order_status')!r} is not safe to auto-explain")
        facts.update(order_number=ctx.order["order_number"], order_status=ctx.order["order_status"])
        lines.append(_T["order_status"][lang].format(order_number=ctx.order["order_number"], status=label[lang]))
        sh = ctx.shipment
        if sh.get("status") == "resolved" and sh.get("tracking_number"):
            facts["tracking_number"] = sh["tracking_number"]
            lines.append(_T["tracking"][lang].format(tracking_number=sh["tracking_number"]))
            if str(sh.get("tracking_url", "")).startswith("https://"):
                facts["tracking_url"] = sh["tracking_url"]
                lines.append(_T["tracking_url"][lang].format(tracking_url=sh["tracking_url"]))
        lines += ["", _T["more"][lang]]
        reason = "verified order status for the authenticated account owner"
    elif action == "draft_request_order_number":
        lines.append(_T["need_order"][lang])
        reason = "order question without a verifiable order: ask for the order number"
    elif action == "draft_request_details":
        lines.append(_T["need_details"][lang])
        reason = "parts inquiry: ask for vehicle and part details"
    elif action == "draft_supplier_ack":
        if ctx.supplier.get("status") != "resolved":
            raise DraftNotPossible("supplier acknowledgement requires a verified supplier")
        lines.append(_T["supplier_ack"][lang])
        reason = "acknowledgement to a verified supplier"
    else:
        raise DraftNotPossible(f"action {action!r} does not produce a draft")

    lines += ["", _T["sign"][lang]]
    return Draft(to=email.reply_address, subject=_reply_subject(email.subject), body="\n".join(lines),
                 language=lang, action=action, reason=reason, facts_used=facts)


def build_rfc822(draft: Draft, email: NormalizedEmail, mailbox: str) -> str:
    msg = EmailMessage()
    msg["From"] = mailbox
    msg["To"] = draft.to
    msg["Subject"] = draft.subject
    if email.rfc822_message_id:
        msg["In-Reply-To"] = email.rfc822_message_id
        msg["References"] = f"{email.references} {email.rfc822_message_id}".strip()
    msg.set_content(draft.body, charset="utf-8")
    return base64.urlsafe_b64encode(msg.as_bytes()).decode()


def _canon(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def verify_local_draft(draft: Draft, email: NormalizedEmail) -> Dict[str, Any]:
    checks = {"recipient_matches_source": draft.to == email.reply_address,
              "body_present": bool(draft.body.strip()),
              "subject_references_thread": _canon(email.subject) in _canon(draft.subject)}
    return {"ok": all(checks.values()), "mode": "local", "checks": checks, "send_calls": 0}


def verify_gmail_draft(gmail_draft: Any, draft: Draft, email: NormalizedEmail, draft_id: str) -> Dict[str, Any]:
    """Checks the draft as READ BACK from Gmail (not the create response)."""
    checks: Dict[str, bool] = {"draft_exists": False}
    try:
        if not isinstance(gmail_draft, dict) or not isinstance(gmail_draft.get("message"), dict):
            raise MalformedMessage("draft has no message")
        got = normalize_message(gmail_draft["message"])
    except MalformedMessage:
        return {"ok": False, "mode": "gmail", "checks": checks, "send_calls": 0}
    checks.update({
        "draft_exists": True,
        "draft_id_matches": bool(draft_id) and gmail_draft.get("id") == draft_id,
        "thread_matches": got.thread_id == email.thread_id,
        "recipient_matches_source": got.to == [email.reply_address],
        "body_matches": _canon(got.body_text) == _canon(draft.body),
        "has_draft_label": "DRAFT" in got.labels,
        "not_sent": "SENT" not in got.labels,
    })
    return {"ok": all(checks.values()), "mode": "gmail", "checks": checks, "send_calls": 0}
