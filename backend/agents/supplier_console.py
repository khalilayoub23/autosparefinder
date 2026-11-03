"""
Script: agents/supplier_console.py
Purpose: The owner's WhatsApp commands for BOAZ, procurement and NIR outreach (owner task 2026-10-27). Called by
         agents/owner_console.process_owner_message, which only runs for the owner's own number - these commands
         ARE the explicit owner approvals that supplier_authorization records. Nothing here comes from email,
         tasks or model output.
Process:
  משימות בועז | boaz tasks              BOAZ's open / escalated tasks
  רכש | procurement                      procurement waiting for a decision (nothing is ordered or paid by itself)
  אשררכש <מזהה>                          approve ONE purchase order for its exact current terms (purchase_order)
  הוזמןידנית <מזהה> <אסמכתה>             record that the owner placed that order himself (needs the PO approval)
  אשרתשלוםספק <מזהה>                     approve paying that supplier for that submitted order (supplier_payment);
                                         a connection that can pay (AliExpress afterpay) pays right then
  בדוקרכש <מזהה>                         ask the supplier connection for the order's status / tracking
  אשרפניותספקים                          standing approval for NIR's routine, non-binding supplier inquiries
  בטלפניותספקים                          revoke it
  Returns None for any other text.
Data Imported/Modified: agent_authorizations, procurement_orders/events (PII DB); agent_todos (catalog DB).
Last Updated: 2026-10-27
"""
from __future__ import annotations

import re
from typing import Optional

_REF = r"([0-9a-fA-F][0-9a-fA-F-]{3,35})"
_PO = re.compile(rf"^(?:אשררכש|אשר רכש|approve po)\s+{_REF}$", re.I)
_MANUAL = re.compile(rf"^(?:הוזמןידנית|הוזמן ידנית|po placed)\s+{_REF}\s+(\S.{{0,120}})$", re.I)
_PAY = re.compile(rf"^(?:אשרתשלוםספק|אשר תשלום ספק|approve supplier payment)\s+{_REF}$", re.I)
_CHECK = re.compile(rf"^(?:בדוקרכש|בדוק רכש|check po)\s+{_REF}$", re.I)
_BOAZ = ("משימות בועז", "משימותבועז", "boaz tasks")
_PROC = ("רכש", "procurement")
_OUTREACH_ON = ("אשרפניותספקים", "אשר פניות ספקים")
_OUTREACH_OFF = ("בטלפניותספקים", "בטל פניות ספקים")

HELP_LINES = (
    "🔌 *ספקים ורכש*\n"
    "• *משימות בועז* — משימות שבועז ביצע או מחכות להחלטה שלך\n"
    "• *רכש* — הזמנות רכש שהוכנו (לא הוזמן ולא שולם כלום)\n"
    "• *אשררכש <מזהה>* · *הוזמןידנית <מזהה> <אסמכתה>* · *אשרתשלוםספק <מזהה>* · *בדוקרכש <מזהה>*\n"
    "• *אשרפניותספקים* / *בטלפניותספקים* — הרשאה קבועה ל-NIR לפניות מידע לא מחייבות לספקים\n\n"
)


async def _one(store, token: str):
    rows = await store.by_prefix(token)
    if not rows:
        return None, f"לא נמצאה הזמנת רכש עם המזהה {token}. כתוב *רכש* לרשימה."
    if len(rows) > 1:
        return None, "המזהה מתאים ליותר מרשומה אחת — כתוב עוד תווים."
    return rows[0], None


def _terms_line(r) -> str:
    t = r.get("terms") or {}
    items = t.get("items") or []
    total = 0.0
    for i in items:
        try:
            total += float(i.get("unit_cost") or 0) * int(i.get("quantity") or 1)
        except (TypeError, ValueError):
            pass
    ship = (t.get("shipping") or {}).get("cost")
    return f"{len(items)} פריטים · עלות ספק ₪{total:.2f} · משלוח {ship if ship not in (None, 'None') else 'לא ידוע'}"


async def _default_connector(supplier_id):
    import supplier_ordering as _o
    return await _o.production_connector(supplier_id)


async def _default_terms(row):
    import procurement as _p
    return await _p.current_terms_for_procurement(row)


async def handle(message: str, *, procurement_store=None, auth_store=None, task_repo=None,
                 connector_factory=None, terms_fn=None) -> Optional[str]:
    msg = (message or "").strip()
    low = msg.lower()
    if not msg:
        return None
    import procurement as _p
    import supplier_authorization as _auth
    if low in _BOAZ:
        import boaz_tasks as _b
        return await _b.open_tasks_text(task_repo or _b.PgTaskRepo())
    if low in _OUTREACH_ON:
        st = auth_store or _auth.PgAuthStore()
        await _auth.grant(st, domain="supplier_message", scope="routine_inquiry", entity_type="policy",
                          entity_id="*", approved_by="owner_whatsapp", source="owner_whatsapp", standing=True)
        return ("✅ NIR רשאי לשלוח לספקים פניות מידע שגרתיות ולא מחייבות (API, משלוח לישראל, מלאי, קטלוג, "
                "תנאים, איש קשר, עניין בשיתוף פעולה) — רק לאנשי קשר מאומתים, מתבנית קבועה, בלי כפילויות.\n"
                "זה לא מאשר הסכמה לתנאים, הזמנה, תשלום או הפעלת ספק. השליחה בפועל כבויה עד NIR_OUTREACH_ENABLED=1.")
    if low in _OUTREACH_OFF:
        st = auth_store or _auth.PgAuthStore()
        found = [a for a in await st.candidates("supplier_message", "policy", "*") if a.standing and a.revoked_at is None]
        for a in found:
            await _auth.revoke(st, a.id, "owner revoked via WhatsApp")
        return f"🛑 בוטלה ההרשאה לפניות NIR לספקים ({len(found)} הרשאות)."
    store = procurement_store or _p.PgProcurementStore()
    if low in _PROC:
        rows = await store.open_rows(15)
        if not rows:
            return "📦 אין הזמנות רכש פתוחות ✅"
        lines = [f"📦 רכש פתוח ({len(rows)}) — שום דבר לא הוזמן או שולם בלי אישורך:"]
        for r in rows:
            lines.append(f"• *{r['id'][:8]}* · הזמנה {r.get('order_number') or r['order_id'][:8]} · ספק "
                         f"{str(r['supplier_id'])[:8]} · {r['state']}\n  {_terms_line(r)}")
        lines.append("פקודות: *אשררכש <מזהה>* · *הוזמןידנית <מזהה> <אסמכתה>* · *אשרתשלוםספק <מזהה>*")
        return "\n".join(lines)
    st = auth_store or _auth.PgAuthStore()
    get_conn = connector_factory or _default_connector
    get_terms = terms_fn or _default_terms
    m = _CHECK.match(msg)
    if m:
        row, err = await _one(store, m.group(1).lower())
        if err:
            return err
        r = await _p.reconcile(store, row["id"], await get_conn(row["supplier_id"]))
        return (f"🔎 {row['id'][:8]}: {r.get('state') or row['state']} · ספק: {r.get('status') or '-'} · "
                f"מעקב: {r.get('tracking') or '-'}" + ("" if r.get("found", r.get("changed")) else f"\n{r.get('reason')}"))
    m = _PO.match(msg)
    if m:
        row, err = await _one(store, m.group(1).lower())
        if err:
            return err
        try:
            await _p.authorize_po(store, st, row["id"], approver="owner_whatsapp")
        except _p.ProcurementError as e:
            return f"לא ניתן לאשר: {e}"
        head = (f"✅ אושרה הזמנת רכש {row['id'][:8]} לתנאים הנוכחיים ({_terms_line(row)}). שינוי מחיר/כמות יחייב "
                "אישור מחדש. האישור לא כולל תשלום לספק.")
        conn = await get_conn(row["supplier_id"])
        if conn.mode not in ("api", "email"):
            msg_ = (await conn.place_order(row)).error_message
            return f"{head}\n{msg_}"
        res = await _p.submit_po(store, st, row["id"], conn, await get_terms(row))
        if res.get("submitted"):
            tail = ("נשלחה הזמנה במייל לספק — ממתין לאישור שלו." if conn.mode == "email"
                    else f"הוזמן אצל הספק · מספר הזמנה {res['supplier_reference']} · עדיין לא שולם.")
            return f"{head}\n📦 {tail}\nלתשלום: *אשרתשלוםספק {row['id'][:8]}*"
        return f"{head}\n⚠️ לא הוזמן: {res.get('reason')}"
    m = _MANUAL.match(msg)
    if m:
        row, err = await _one(store, m.group(1).lower())
        if err:
            return err
        try:
            await _p.record_manual_submission(store, st, row["id"], m.group(2).strip(), approver="owner_whatsapp")
        except (_p.ProcurementError, _auth.AuthorizationError) as e:
            return f"לא נרשם: {e}"
        return f"✅ נרשם שההזמנה {row['id'][:8]} בוצעה אצל הספק (אסמכתה {m.group(2).strip()[:40]})."
    m = _PAY.match(msg)
    if m:
        row, err = await _one(store, m.group(1).lower())
        if err:
            return err
        try:
            await _p.authorize_payment(store, st, row["id"], approver="owner_whatsapp")
        except _p.ProcurementError as e:
            return f"לא ניתן לאשר תשלום: {e}"
        head = f"✅ אושר תשלום לספק עבור {row['id'][:8]} ({_terms_line(row)}) — חד-פעמי, לתנאים האלה בלבד."
        conn = await get_conn(row["supplier_id"])
        if getattr(conn, "can_pay", False):
            res = await _p.pay_via_connector(store, st, row["id"], conn, await get_terms(row))
            return f"{head}\n" + ("💳 שולם אצל הספק." if res.get("paid") else f"⚠️ לא שולם: {res.get('reason')}")
        return head
    return None
