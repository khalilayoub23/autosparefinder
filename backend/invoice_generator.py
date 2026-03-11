"""
invoice_generator.py — Professional Hebrew RTL PDF invoices for Auto Spare.

Uses reportlab + DejaVu Sans (Unicode/Hebrew) + python-bidi for RTL layout.
Designed to fill the full A4 page with generous section spacing.
"""

import io
from datetime import datetime

from reportlab.lib.pagesizes import A4
from reportlab.lib.colors import HexColor, white
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

def _register_fonts():
    """Register DejaVu fonts once; safe to call multiple times."""
    global _FONT_REG, _FONT_BOLD
    registered = pdfmetrics.getRegisteredFontNames()
    try:
        if "DV" not in registered:
            pdfmetrics.registerFont(TTFont("DV", _FONT_REG))
        if "DV-Bold" not in registered:
            pdfmetrics.registerFont(TTFont("DV-Bold", _FONT_BOLD))
    except Exception as exc:
        # Fonts not installed — fall back to the built-in Helvetica family.
        # The invoice will still render but Hebrew glyphs will not display.
        print(f"[invoice_generator] WARNING: DejaVu fonts unavailable ({exc}). Falling back to Helvetica.")
        _FONT_REG = _FONT_BOLD = None  # signal to use Helvetica below

_register_fonts()


def _font(bold: bool = False) -> str:
    """Return the best available font name."""
    if _FONT_REG is None:
        return "Helvetica-Bold" if bold else "Helvetica"
    return "DV-Bold" if bold else "DV"

# ── Brand colours ─────────────────────────────────────────────────────────────
BRAND = HexColor("#ea580c")
DARK  = HexColor("#111827")
MID   = HexColor("#374151")
LIGHT = HexColor("#F3F4F6")
GREY  = HexColor("#6B7280")


# ─────────────────────────────────────────────────────────────────────────────
def generate_invoice_pdf(order, items, user, invoice) -> bytes:
    W, H = A4   # 595.28 x 841.89 pt
    buf  = io.BytesIO()
    c    = Canvas(buf, pagesize=A4)

    issued  = invoice.issued_at if invoice.issued_at else datetime.utcnow()
    biz_num = invoice.business_number or "060633880"

    # ─────────────────────────────────────────────────────────────────────────
    # SECTION 1 — Header bar  (top 100 pt)
    # ─────────────────────────────────────────────────────────────────────────
    BAR_H = 100
    c.setFillColor(BRAND)
    c.rect(0, H - BAR_H, W, BAR_H, fill=1, stroke=0)

    # Company name — left
    c.setFillColor(white)
    c.setFont("DV-Bold", 28)
    c.drawString(36, H - 44,  "Auto Spare")
    c.setFont("DV", 10)
    c.drawString(36, H - 63,  "support@autospare.co.il  |  www.autospare.co.il")
    c.drawString(36, H - 79,  rtl(f"עוסק מורשה: {biz_num}"))

    # Invoice title — right
    c.setFont("DV-Bold", 20)
    c.drawRightString(W - 36, H - 42, rtl("חשבונית מס / קבלה"))
    c.setFont("DV", 10)
    c.drawRightString(W - 36, H - 60, rtl(f"מספר: {invoice.invoice_number}"))
    c.drawRightString(W - 36, H - 75, rtl(f"תאריך: {issued.strftime('%d/%m/%Y')}"))
    c.drawRightString(W - 36, H - 90, rtl(f"הזמנה: {order.order_number}"))

    # ─────────────────────────────────────────────────────────────────────────
    # SECTION 2 — Thin orange rule
    # ─────────────────────────────────────────────────────────────────────────
    y = H - BAR_H - 36
    c.setStrokeColor(BRAND)
    c.setLineWidth(1.5)
    c.line(36, y, W - 36, y)

    # ─────────────────────────────────────────────────────────────────────────
    # SECTION 3 — Customer (RIGHT) / Order (LEFT)  ← RTL natural reading order
    # ─────────────────────────────────────────────────────────────────────────
    y -= 56
    c.setFont("DV-Bold", 10.5)
    c.setFillColor(DARK)
    c.drawString(36,      y, rtl("פרטי הזמנה"))
    c.drawRightString(W - 36, y, rtl("פרטי לקוח"))

    # Thin separator under headers
    y -= 12
    c.setStrokeColor(LIGHT)
    c.setLineWidth(0.5)
    c.line(36, y, W - 36, y)

    y -= 36
    c.setFont("DV", 10)
    c.setFillColor(MID)
    c.drawString(36, y, rtl(f"סטטוס: {order.status}"))
    c.drawRightString(W - 36, y, rtl(str(user.full_name or "")))

    y -= 32
    c.drawString(36, y,
        rtl(f"תאריך: {order.created_at.strftime('%d/%m/%Y') if order.created_at else ''}"))
    c.drawRightString(W - 36, y, user.email or "")

    # Shipping address on the right
    if order.shipping_address:
        addr = order.shipping_address
        if isinstance(addr, dict):
            parts = []
            for k in ("street", "city", "postal_code", "country"):
                v = addr.get(k, "")
                if v:
                    parts.append(str(v))
            addr = ", ".join(parts) if parts else ", ".join(str(v) for v in addr.values() if v)
        y -= 32
        c.drawRightString(W - 36, y, rtl(str(addr)[:70]))

    # ─────────────────────────────────────────────────────────────────────────
    # SECTION 4 — Items table
    # ─────────────────────────────────────────────────────────────────────────
    y -= 72   # generous gap before table

    COL = {
        "warranty": (36,   "drawString"),
        "total":    (104,  "drawCentredString"),
        "unit":     (178,  "drawCentredString"),
        "qty":      (248,  "drawCentredString"),
        "sku":      (336,  "drawCentredString"),
        "type":     (422,  "drawCentredString"),
        "name":     (W-36, "drawRightString"),
    }

    HEADER_H = 24
    c.setFillColor(DARK)
    c.rect(30, y - 2, W - 60, HEADER_H, fill=1, stroke=0)
    c.setFillColor(white)
    c.setFont("DV-Bold", 9)
    _draw_row(c, y + 7,
              rtl("אחריות"), rtl('סה"כ'), rtl("מחיר יח׳"),
              rtl("כמות"), rtl('מק"ט'), rtl("סוג"), rtl("פריט"),
              COL, is_header=True)

    # Fallback when no items in DB
    display_items = list(items)
    if not display_items:
        class _FallbackItem:
            part_name       = f"פריטי הזמנה | {order.order_number}"
            part_sku        = ""
            part_type       = "מקורי"
            quantity        = 1
            unit_price      = float(order.subtotal or order.total_amount or 0)
            warranty_months = 12
        display_items = [_FallbackItem()]

    ROW_H = 22
    row_colors = [LIGHT, white]
    y -= HEADER_H
    for idx, item in enumerate(display_items):
        c.setFillColor(row_colors[idx % 2])
        c.rect(30, y - 3, W - 60, ROW_H, fill=1, stroke=0)
        c.setFillColor(DARK)
        c.setFont("DV", 8.5)

        war_text  = rtl(f"{item.warranty_months or 12} חודש")
        tot_text  = f"\u20aa{float(item.unit_price) * int(item.quantity):.2f}"
        unit_text = f"\u20aa{float(item.unit_price):.2f}"
        qty_text  = str(item.quantity)
        sku_text  = str(getattr(item, "part_sku", "") or "")[:18]
        type_text = rtl(str(item.part_type or "מקורי"))
        name_text = rtl(str(item.part_name or "")[:42])

        _draw_row(c, y + 4,
                  war_text, tot_text, unit_text,
                  qty_text, sku_text, type_text, name_text,
                  COL, is_header=False)
        y -= ROW_H

    # ─────────────────────────────────────────────────────────────────────────
    # SECTION 5 — Totals box (LEFT side)
    # ─────────────────────────────────────────────────────────────────────────
    y -= 72   # generous gap after table

    box_x = 36
    box_w = 196
    box_h = 100

    c.setFillColor(LIGHT)
    c.roundRect(box_x, y - box_h, box_w, box_h, 6, fill=1, stroke=0)

    subtotal = float(order.subtotal      or 0)
    vat      = float(order.vat_amount    or 0)
    ship     = float(order.shipping_cost or 0)
    total    = float(order.total_amount  or 0)

    tot_lines = [
        (rtl('סה"כ לפני מע"מ'), f"\u20aa{subtotal:.2f}"),
        (rtl('מע"מ  17%'),       f"\u20aa{vat:.2f}"),
        (rtl("דמי משלוח"),       f"\u20aa{ship:.2f}"),
    ]

    ly = y - 14
    c.setFont("DV", 9.5)
    c.setFillColor(MID)
    for label, val in tot_lines:
        c.drawString(box_x + 14, ly, label)
        c.drawRightString(box_x + box_w - 12, ly, val)
        ly -= 18

    c.setStrokeColor(BRAND)
    c.setLineWidth(0.75)
    c.line(box_x + 10, ly + 8, box_x + box_w - 10, ly + 8)

    ly -= 8
    c.setFont("DV-Bold", 12)
    c.setFillColor(DARK)
    c.drawString(box_x + 14, ly, rtl('סה"כ לתשלום'))
    c.setFillColor(BRAND)
    c.drawRightString(box_x + box_w - 12, ly, f"\u20aa{total:.2f}")

    # ─────────────────────────────────────────────────────────────────────────
    # SECTION 6 — Legal note
    # ─────────────────────────────────────────────────────────────────────────
    legal_y = y - box_h + 10   # anchor below totals box
    legal_y = min(legal_y, 90)  # never overlap footer
    c.setFont("DV", 8)
    c.setFillColor(GREY)
    c.drawString(36, legal_y, rtl(
        'מסמך זה מהווה חשבונית מס/קבלה כמשמעותה בחוק מע"מ. '
        "עוסק מורשה — מספר רישום: " + biz_num
    ))
    c.drawString(36, legal_y - 14, rtl(
        "מדיניות ביטולים: ניתן לבטל עד 14 יום מיום קבלת המוצר. לפרטים: support@autospare.co.il"
    ))

    # ─────────────────────────────────────────────────────────────────────────
    # SECTION 7 — Footer bar
    # ─────────────────────────────────────────────────────────────────────────
    c.setFillColor(BRAND)
    c.rect(0, 0, W, 42, fill=1, stroke=0)
    c.setFillColor(white)
    c.setFont("DV-Bold", 9.5)
    c.drawCentredString(W / 2, 26, rtl("תודה על הזמנתך! | Auto Spare"))
    c.setFont("DV", 8)
    c.drawCentredString(W / 2, 12, "support@autospare.co.il  |  www.autospare.co.il")

    c.showPage()
    c.save()
    buf.seek(0)
    return buf.read()


# ─────────────────────────────────────────────────────────────────────────────
def _draw_row(c, y, warranty, total, unit, qty, sku, ptype, name, COL, is_header):
    """Draw one table row across 7 columns."""
    def _put(key, text):
        x, method = COL[key]
        getattr(c, method)(x, y, text)

    _put("warranty", warranty)
    _put("total",    total)
    _put("unit",     unit)
    _put("qty",      qty)
    _put("sku",      sku)
    _put("type",     ptype)
    _put("name",     name)
