"""
invoice_generator.py — Professional Hebrew RTL PDF invoices for Auto Spare.

Uses reportlab + DejaVu Sans (Unicode/Hebrew) + python-bidi for RTL layout.
"""

import io
from datetime import datetime

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib.colors import HexColor, white, black
from reportlab.pdfgen.canvas import Canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# ── Hebrew RTL support ────────────────────────────────────────────────────────
try:
    from bidi.algorithm import get_display as _bidi_display
    _BIDI = True
except ImportError:
    _BIDI = False


def rtl(text: str) -> str:
    """Apply bidi algorithm so Hebrew renders correctly in a LTR canvas."""
    if not text:
        return ""
    if _BIDI:
        return _bidi_display(str(text))
    return str(text)


# ── Fonts ─────────────────────────────────────────────────────────────────────
_FONT_REG  = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
_FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
pdfmetrics.registerFont(TTFont("DV",     _FONT_REG))
pdfmetrics.registerFont(TTFont("DV-Bold", _FONT_BOLD))

# ── Brand colours ─────────────────────────────────────────────────────────────
BRAND = HexColor("#ea580c")
DARK  = HexColor("#111827")
MID   = HexColor("#374151")
LIGHT = HexColor("#F3F4F6")
GREY  = HexColor("#6B7280")


# ─────────────────────────────────────────────────────────────────────────────
def generate_invoice_pdf(order, items, user, invoice) -> bytes:
    """
    Generate a professional A4 Hebrew/English invoice PDF.

    Args:
        order   — ORM Order object
        items   — list of ORM OrderItem objects
        user    — ORM User object
        invoice — ORM Invoice object

    Returns bytes of the PDF.
    """
    W, H = A4  # 595.28 x 841.89 pt
    buf  = io.BytesIO()
    c    = Canvas(buf, pagesize=A4)

    issued = invoice.issued_at if invoice.issued_at else datetime.utcnow()
    biz_num = invoice.business_number or "060633880"

    # ── 1. Header bar ─────────────────────────────────────────────────────────
    c.setFillColor(BRAND)
    c.rect(0, H - 85, W, 85, fill=1, stroke=0)

    # Company name left
    c.setFillColor(white)
    c.setFont("DV-Bold", 24)
    c.drawString(30, H - 40, "Auto Spare")
    c.setFont("DV", 9)
    c.drawString(30, H - 55, rtl("ייבוא ישיר — חלפים לרכב"))
    c.drawString(30, H - 66, "support@autospare.co.il  |  www.autospare.co.il")
    c.drawString(30, H - 77, rtl(f"עוסק מורשה: {biz_num}"))

    # Invoice title right
    c.setFont("DV-Bold", 18)
    c.drawRightString(W - 30, H - 38, rtl("חשבונית מס / קבלה"))
    c.setFont("DV", 9.5)
    c.drawRightString(W - 30, H - 54, rtl(f"מספר: {invoice.invoice_number}"))
    c.drawRightString(W - 30, H - 67, rtl(f"תאריך: {issued.strftime('%d/%m/%Y')}"))
    c.drawRightString(W - 30, H - 80, rtl(f"הזמנה: {order.order_number}"))

    # ── 2. Orange rule ────────────────────────────────────────────────────────
    y = H - 100
    c.setStrokeColor(BRAND)
    c.setLineWidth(1)
    c.line(30, y, W - 30, y)

    # ── 3. Customer info left / Order info right ───────────────────────────────
    y -= 16
    c.setFont("DV-Bold", 9)
    c.setFillColor(DARK)
    c.drawString(30, y, rtl("פרטי לקוח"))
    c.drawRightString(W - 30, y, rtl("פרטי הזמנה"))

    y -= 14
    c.setFont("DV", 9)
    c.setFillColor(MID)
    c.drawString(30, y, rtl(user.full_name or ""))
    c.drawRightString(W - 30, y, rtl(f"סטטוס: {order.status}"))

    y -= 13
    c.drawString(30, y, user.email or "")
    c.drawRightString(W - 30, y, rtl(f"תאריך: {order.created_at.strftime('%d/%m/%Y') if order.created_at else ''}"))

    if order.shipping_address:
        y -= 13
        addr = order.shipping_address
        if isinstance(addr, dict):
            addr = ", ".join(str(v) for v in addr.values() if v)
        c.drawString(30, y, rtl(str(addr)[:65]))

    # ── 4. Items table  (RTL column order: פריט ← סוג ← כמות ← מחיר יח׳ ← סה"כ ← אחריות) ──
    y -= 28
    # RTL column x-positions (right → left in visual reading order)
    # In PDF coords x grows left→right, so "rightmost visual" = largest x
    # Visual RTL: אחריות | סה"כ | מחיר יח׳ | כמות | סוג | פריט
    #              left                                      right
    COL = {
        "warranty": (38,   "drawString"),          # leftmost  — אחריות
        "total":    (110,  "drawCentredString"),   #            — סה"כ
        "unit":     (195,  "drawCentredString"),   #            — מחיר יח׳
        "qty":      (272,  "drawCentredString"),   #            — כמות
        "type":     (355,  "drawCentredString"),   #            — סוג
        "name":     (W-38, "drawRightString"),     # rightmost  — פריט
    }

    # Header bar
    c.setFillColor(DARK)
    c.rect(30, y - 2, W - 60, 18, fill=1, stroke=0)
    c.setFillColor(white)
    c.setFont("DV-Bold", 8.5)
    _draw_row(c, y + 3,
              rtl("אחריות"), rtl('סה"כ'), rtl("מחיר יח׳"),
              rtl("כמות"),   rtl("סוג"),  rtl("פריט"),
              COL, is_header=True)

    # ── Fallback: synthetic row from order totals when DB items are missing ──
    display_items = list(items)
    if not display_items:
        class _FallbackItem:
            part_name       = f"פריטי הזמנה | {order.order_number}"
            part_type       = "מקורי"
            quantity        = 1
            unit_price      = float(order.subtotal or order.total_amount or 0)
            warranty_months = 12
        display_items = [_FallbackItem()]

    # Table rows
    row_colors = [LIGHT, white]
    y -= 18
    for idx, item in enumerate(display_items):
        row_h = 16
        c.setFillColor(row_colors[idx % 2])
        c.rect(30, y - 2, W - 60, row_h, fill=1, stroke=0)
        c.setFillColor(DARK)
        c.setFont("DV", 8)

        war_text   = rtl(f"{item.warranty_months or 12} חודש")
        total_text = f"\u20aa{float(item.unit_price) * int(item.quantity):.2f}"
        unit_text  = f"\u20aa{float(item.unit_price):.2f}"
        qty_text   = str(item.quantity)
        type_text  = rtl(str(item.part_type or "מקורי"))
        name_text  = rtl(str(item.part_name or "")[:48])

        _draw_row(c, y + 2,
                  war_text, total_text, unit_text,
                  qty_text, type_text,  name_text,
                  COL, is_header=False)
        y -= row_h

    # ── 5. Totals box (bottom-right) ──────────────────────────────────────────
    y -= 20
    box_x = W - 210
    box_w = 178
    box_h = 78

    c.setFillColor(LIGHT)
    c.roundRect(box_x, y - box_h + 10, box_w, box_h, 4, fill=1, stroke=0)

    subtotal = float(order.subtotal     or 0)
    vat      = float(order.vat_amount   or 0)
    ship     = float(order.shipping_cost or 0)
    total    = float(order.total_amount  or 0)

    lines = [
        (rtl('סה"כ לפני מע"מ'), f"\u20aa{subtotal:.2f}"),
        (rtl("מע\"מ  17%"),       f"\u20aa{vat:.2f}"),
        (rtl("דמי משלוח"),        f"\u20aa{ship:.2f}"),
    ]

    ly = y - 2
    c.setFont("DV", 8.5)
    c.setFillColor(MID)
    for label, val in lines:
        c.drawString(box_x + 10, ly, label)
        c.drawRightString(box_x + box_w - 8, ly, val)
        ly -= 14

    # Divider
    c.setStrokeColor(BRAND)
    c.setLineWidth(0.5)
    c.line(box_x + 8, ly + 6, box_x + box_w - 8, ly + 6)

    # Total line
    ly -= 4
    c.setFont("DV-Bold", 10)
    c.setFillColor(DARK)
    c.drawString(box_x + 10, ly, rtl('סה"כ לתשלום'))
    c.setFillColor(BRAND)
    c.drawRightString(box_x + box_w - 8, ly, f"\u20aa{total:.2f}")

    # ── 6. Legal note ─────────────────────────────────────────────────────────
    note_y = max(y - box_h - 10, 60)
    c.setFont("DV", 7.5)
    c.setFillColor(GREY)
    c.drawString(30, note_y, rtl(
        "מסמך זה מהווה חשבונית מס/קבלה כמשמעותה בחוק מע\"מ. "
        "עוסק מורשה — מספר רישום: " + biz_num
    ))
    c.drawString(30, note_y - 11, rtl("מדיניות ביטולים: ניתן לבטל עד 14 יום מיום קבלת המוצר. לפרטים: support@autospare.co.il"))

    # ── 7. Footer bar ─────────────────────────────────────────────────────────
    c.setFillColor(BRAND)
    c.rect(0, 0, W, 36, fill=1, stroke=0)
    c.setFillColor(white)
    c.setFont("DV-Bold", 8.5)
    c.drawCentredString(W / 2, 22, rtl("תודה על הזמנתך! | Auto Spare — ייבוא ישיר חלפים לרכב"))
    c.setFont("DV", 7.5)
    c.drawCentredString(W / 2, 10, "support@autospare.co.il  |  www.autospare.co.il")

    c.showPage()
    c.save()
    buf.seek(0)
    return buf.read()


# ─────────────────────────────────────────────────────────────────────────────
def _draw_row(c, y, warranty, total, unit, qty, ptype, name, COL, is_header):
    """Draw one table row. Argument order matches RTL visual: right→left."""
    font = "DV-Bold" if is_header else "DV"
    size = 8.5 if is_header else 8
    c.setFont(font, size)

    def _put(key, text):
        x, method = COL[key]
        getattr(c, method)(x, y, text)

    _put("warranty", warranty)
    _put("total",    total)
    _put("unit",     unit)
    _put("qty",      qty)
    _put("type",     ptype)
    _put("name",     name)
