"""
email_templates.py — branded, RTL, mobile-friendly transactional email templates
(2026-07-14). Provider-agnostic: each builder returns (subject, html, text); sending
goes through routes.email_utils.send_email (Gmail SMTP / any SMTP / SendGrid fallback).

One shared shell (_shell) gives every email the same look: logo header, a white content
card, a bulletproof CTA button, and a footer with the website + WhatsApp/Telegram/email.
Email HTML is deliberately old-school — inline styles + tables, no flexbox/<style> — so it
renders in Gmail, Outlook and Apple Mail. dir="rtl" + right alignment for Hebrew.

Templates: welcome · verify_email · password_reset · password_changed · invoice ·
missing_details · order_confirmation · payment_received · delivery_update · review_request.
"""
import os
import html as _html
from typing import List, Optional, Tuple, Dict, Any

SITE_URL = os.getenv("FRONTEND_URL", "https://autosparefinder.co.il").rstrip("/")
LOGO_URL = os.getenv("EMAIL_LOGO_URL", f"{SITE_URL}/logo.png")
ICON_BASE = os.getenv("EMAIL_ICON_BASE", f"{SITE_URL}/email-icons")  # brand-identity PNG icons
BRAND = "AutoSpareFinder"
SUPPORT_EMAIL = os.getenv("SUPPORT_EMAIL", "support@autosparefinder.co.il")
WHATSAPP_URL = os.getenv("EMAIL_WHATSAPP_URL", "https://wa.me/972532426920")
TELEGRAM_URL = os.getenv("EMAIL_TELEGRAM_URL", "https://t.me/Noa_autosparefinder_bot")

# Brand palette
_NAVY = "#111827"
_BLUE = "#00A3FF"
_BLUE_DK = "#0284c7"
_BG = "#f4f6fb"
_MUTED = "#6b7280"
_LINE = "#e5e7eb"


def _e(v: Any) -> str:
    """HTML-escape a value for safe interpolation into template markup."""
    return _html.escape(str(v if v is not None else ""))


def _first_name(full_name: Optional[str]) -> str:
    n = (full_name or "").strip()
    return n.split()[0] if n else "לקוח יקר"


def _btn(label: str, url: str, color: str = _BLUE) -> str:
    """Bulletproof (table-based) CTA button — renders in Outlook too."""
    return f"""
    <table role="presentation" cellspacing="0" cellpadding="0" border="0" style="margin:22px 0">
      <tr><td align="center" bgcolor="{color}" style="border-radius:10px">
        <a href="{_e(url)}" target="_blank"
           style="display:inline-block;padding:13px 30px;font-family:Arial,Helvetica,sans-serif;
                  font-size:16px;font-weight:bold;color:#ffffff;text-decoration:none;border-radius:10px">
          {_e(label)}
        </a>
      </td></tr>
    </table>"""


def _info_box(rows: List[Tuple[str, str]], accent: str = _BLUE) -> str:
    inner = "".join(
        f"""<tr>
              <td style="padding:6px 0;color:{_MUTED};font-size:13px">{_e(k)}</td>
              <td style="padding:6px 0;color:{_NAVY};font-size:15px;font-weight:bold;text-align:left">{v}</td>
            </tr>"""
        for k, v in rows
    )
    return f"""
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"
           style="background:#f0f9ff;border-radius:12px;border-right:4px solid {accent};margin:18px 0">
      <tr><td style="padding:14px 18px">
        <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">{inner}</table>
      </td></tr>
    </table>"""


def _icon(name: str, label: str, url: str) -> str:
    """A hosted brand-identity PNG icon + label (email-safe: real <img>, no SVG/emoji)."""
    return (f'<td style="padding:0 9px;text-align:center;vertical-align:top">'
            f'<a href="{_e(url)}" target="_blank" '
            f'style="text-decoration:none;color:#9fb0c7;font-family:Arial,Helvetica,sans-serif;font-size:11px">'
            f'<img src="{ICON_BASE}/{name}.png" width="36" height="36" alt="{_e(label)}" '
            f'style="display:block;margin:0 auto 5px;border:0;outline:none">{_e(label)}</a></td>')


def _shell(preheader: str, heading: str, body_html: str) -> str:
    """Wrap content in the shared branded, RTL, responsive email shell."""
    return f"""<!DOCTYPE html>
<html lang="he" dir="rtl" xmlns="http://www.w3.org/1999/xhtml">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta name="color-scheme" content="light only">
  <title>{_e(heading)}</title>
  <style>
    /* Fluid on phones: never wider than the screen, tighter padding, readable type.
       Base inline styles already keep it fluid; this is polish for clients that honor
       media queries (Gmail app, Apple Mail). */
    @media only screen and (max-width:620px) {{
      .asf-card {{ width:100% !important; border-radius:0 !important; }}
      .asf-body {{ padding:24px 18px !important; }}
      .asf-head {{ padding:22px 14px !important; }}
      .asf-h1   {{ font-size:20px !important; }}
    }}
  </style>
</head>
<body style="margin:0;padding:0;background:{_BG};direction:rtl;-webkit-text-size-adjust:100%">
  <span style="display:none!important;visibility:hidden;opacity:0;height:0;width:0;overflow:hidden">{_e(preheader)}</span>
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="background:{_BG};padding:20px 10px">
    <tr><td align="center">
      <!-- fluid container: 100% up to a 600px cap, so the full text fits any phone width -->
      <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" class="asf-card"
             style="max-width:600px;background:#ffffff;border-radius:18px;overflow:hidden;
                    box-shadow:0 10px 40px rgba(17,24,39,0.08)">
        <!-- header: square emblem logo at its true 1:1 ratio + wordmark (survives image-blocking) -->
        <tr><td align="center" class="asf-head" style="background:{_NAVY};padding:24px 20px 20px">
          <a href="{SITE_URL}" target="_blank" style="text-decoration:none;color:#ffffff">
            <img src="{LOGO_URL}" width="72" height="72" alt="{BRAND}"
                 style="width:72px;height:72px;display:block;margin:0 auto 10px;border:0;border-radius:16px">
            <span style="font-family:Arial,Helvetica,sans-serif;font-size:19px;font-weight:bold;
                         color:#ffffff;letter-spacing:.3px">AutoSpareFinder</span>
          </a>
        </td></tr>
        <!-- body -->
        <tr><td class="asf-body" style="padding:32px 28px;font-family:Arial,Helvetica,sans-serif;color:{_NAVY};text-align:right">
          <h1 class="asf-h1" style="margin:0 0 6px;font-size:22px;color:{_NAVY};line-height:1.3">{heading}</h1>
          {body_html}
        </td></tr>
        <!-- footer: brand-identity PNG icons (real images — no emoji/SVG) -->
        <tr><td style="padding:22px 20px;background:#0b1220;text-align:center;font-family:Arial,Helvetica,sans-serif">
          <table role="presentation" cellspacing="0" cellpadding="0" border="0" align="center" style="margin:0 auto 14px">
            <tr>
              {_icon("whatsapp", "וואטסאפ", WHATSAPP_URL)}
              {_icon("telegram", "טלגרם", TELEGRAM_URL)}
              {_icon("email", "מייל", "mailto:" + SUPPORT_EMAIL)}
              {_icon("web", "האתר", SITE_URL)}
            </tr>
          </table>
          <p style="margin:0;color:#7b879c;font-size:11px;line-height:1.7">
            {BRAND} — חלקי חילוף לרכב בהתאמה חכמה<br>
            <a href="{SITE_URL}" target="_blank" style="color:#7b879c;text-decoration:none">{SITE_URL.replace('https://','')}</a>
            &nbsp;·&nbsp; לתמיכה: <a href="mailto:{SUPPORT_EMAIL}" style="color:#7b879c;text-decoration:none">{SUPPORT_EMAIL}</a>
          </p>
        </td></tr>
      </table>
      <p style="color:#9ca3af;font-size:11px;font-family:Arial,sans-serif;margin:14px 0 0">
        קיבלת את המייל הזה כי יש לך חשבון ב-{BRAND}.
      </p>
    </td></tr>
  </table>
</body></html>"""


def _p(text: str) -> str:
    return f'<p style="margin:0 0 14px;font-size:15px;line-height:1.7;color:#374151">{text}</p>'


# ── Templates ────────────────────────────────────────────────────────────────

def welcome(full_name: str) -> Tuple[str, str, str]:
    name = _first_name(full_name)
    body = (
        _p(f"היי {_e(name)} 👋 ברוכים הבאים ל-<b>{BRAND}</b> — שמחים שהצטרפת!")
        + _p("מהיום מציאת החלק הנכון לרכב שלך פשוטה: הזן מספר רישוי או דגם, "
             "ואנחנו נמצא את החלק המתאים ונשווה מחירים בשבילך — במקום אחד.")
        + _btn("מצא חלק לרכב שלך →", f"{SITE_URL}/parts")
        + _p("צריך עזרה? אנחנו זמינים בוואטסאפ, טלגרם ובמייל — פשוט השב להודעה זו.")
    )
    return (f"ברוכים הבאים ל-{BRAND} 🚗",
            _shell("ברוכים הבאים! מצא חלקים לרכב שלך בקלות.", f"היי {_e(name)}, ברוכים הבאים 🎉", body),
            f"היי {name}, ברוכים הבאים ל-{BRAND}!\nמצא חלקים: {SITE_URL}/parts\nתמיכה: {SUPPORT_EMAIL}")


def verify_email(full_name: str, verify_url: str) -> Tuple[str, str, str]:
    name = _first_name(full_name)
    body = (_p(f"היי {_e(name)}, כדי להפעיל את החשבון שלך ב-{BRAND} נותר רק לאמת את כתובת המייל:")
            + _btn("אימות המייל שלי", verify_url)
            + _p("הקישור בתוקף ל-24 שעות. אם לא נרשמת אצלנו — אפשר להתעלם מהמייל."))
    return (f"אימות המייל שלך — {BRAND}",
            _shell("אמת את כתובת המייל כדי להפעיל את החשבון.", "אימות כתובת מייל ✉️", body),
            f"היי {name}, אמת את המייל: {verify_url} (בתוקף 24 שעות).")


def password_reset(full_name: str, reset_url: str, minutes: int = 30) -> Tuple[str, str, str]:
    name = _first_name(full_name)
    body = (_p(f"היי {_e(name)}, קיבלנו בקשה לאיפוס הסיסמה לחשבונך ב-{BRAND}.")
            + _btn("איפוס סיסמה", reset_url)
            + _p(f"הקישור בתוקף ל-{minutes} דקות ולשימוש חד-פעמי.")
            + _p("<b>לא ביקשת לאפס סיסמה?</b> אפשר להתעלם מהמייל — הסיסמה שלך לא השתנתה, "
                 "ומומלץ ליצור איתנו קשר אם זה חוזר."))
    return (f"איפוס סיסמה — {BRAND}",
            _shell("בקשה לאיפוס סיסמה לחשבונך.", "איפוס סיסמה 🔑", body),
            f"היי {name}, לאיפוס הסיסמה: {reset_url} (בתוקף {minutes} דקות). לא ביקשת? התעלם מהמייל.")


def password_changed(full_name: str) -> Tuple[str, str, str]:
    name = _first_name(full_name)
    body = (_p(f"היי {_e(name)}, הסיסמה לחשבונך ב-{BRAND} עודכנה בהצלחה. ✅")
            + _p("<b>לא ביצעת את השינוי?</b> יש לאבטח את החשבון מיד — צור קשר בוואטסאפ או במייל.")
            + _btn("כניסה לחשבון", f"{SITE_URL}/login"))
    return (f"הסיסמה שלך עודכנה — {BRAND}",
            _shell("הסיסמה לחשבונך עודכנה.", "הסיסמה עודכנה ✅", body),
            f"היי {name}, הסיסמה שלך עודכנה. לא אתה? צור קשר מיד: {SUPPORT_EMAIL}")


def order_confirmation(full_name: str, order_number: str, items: List[Dict[str, Any]],
                       total_ils: float, order_url: str) -> Tuple[str, str, str]:
    name = _first_name(full_name)
    li = "".join(
        f"""<tr>
              <td style="padding:8px 0;border-bottom:1px solid {_LINE};font-size:14px;color:#374151">
                {_e(it.get('name',''))} <span style="color:{_MUTED}">×{_e(it.get('qty',1))}</span></td>
              <td style="padding:8px 0;border-bottom:1px solid {_LINE};font-size:14px;color:{_NAVY};font-weight:bold;text-align:left">
                ₪{_e(round(float(it.get('price',0))))}</td>
            </tr>"""
        for it in (items or [])
    )
    body = (
        _p(f"היי {_e(name)}, תודה על ההזמנה! קיבלנו אותה והיא בטיפול. 🎉")
        + _info_box([("מספר הזמנה", _e(order_number)), ("סה\"כ לתשלום", f"₪{round(total_ils)}")], accent=_BLUE)
        + (f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" '
           f'style="margin:6px 0 4px">{li}</table>' if li else "")
        + _btn("צפייה בהזמנה", order_url)
        + _p("נעדכן אותך בכל שלב — מאישור הספק ועד מספר מעקב למשלוח.")
    )
    return (f"✅ הזמנתך התקבלה — {order_number}",
            _shell(f"הזמנה {order_number} התקבלה ובטיפול.", f"ההזמנה שלך אושרה 🎉", body),
            f"היי {name}, הזמנה {order_number} התקבלה. סה\"כ ₪{round(total_ils)}. פרטים: {order_url}")


def payment_received(full_name: str, order_number: str, amount_ils: float,
                     invoice_url: Optional[str] = None) -> Tuple[str, str, str]:
    name = _first_name(full_name)
    body = (_p(f"היי {_e(name)}, קיבלנו את התשלום עבור הזמנה <b>{_e(order_number)}</b> — תודה! 💳")
            + _info_box([("סכום ששולם", f"₪{round(amount_ils)}"), ("הזמנה", _e(order_number))], accent="#22c55e")
            + (_btn("צפייה בחשבונית", invoice_url) if invoice_url else "")
            + _p("אנחנו כבר מזמינים את החלק מהספק ונעדכן אותך עם פרטי המשלוח."))
    return (f"💳 התשלום התקבל — {order_number}",
            _shell(f"התשלום עבור {order_number} התקבל.", "התשלום התקבל 💳", body),
            f"היי {name}, התשלום ₪{round(amount_ils)} עבור {order_number} התקבל. תודה!")


def invoice(full_name: str, order_number: str, invoice_number: str, total_ils: float,
            invoice_url: str) -> Tuple[str, str, str]:
    name = _first_name(full_name)
    body = (_p(f"היי {_e(name)}, מצורפת החשבונית עבור הזמנה <b>{_e(order_number)}</b>.")
            + _info_box([("מספר חשבונית", _e(invoice_number)),
                         ("הזמנה", _e(order_number)),
                         ("סה\"כ כולל מע\"מ", f"₪{round(total_ils)}")], accent=_BLUE)
            + _btn("הורדת חשבונית (PDF)", invoice_url)
            + _p("שמור את החשבונית לצרכי אחריות והחזרות. לשאלות — אנחנו כאן."))
    return (f"🧾 חשבונית {invoice_number} — {BRAND}",
            _shell(f"חשבונית {invoice_number} עבור הזמנה {order_number}.", "החשבונית שלך 🧾", body),
            f"היי {name}, חשבונית {invoice_number} להזמנה {order_number}, ₪{round(total_ils)}: {invoice_url}")


def missing_details(full_name: str, missing_field: str, update_url: str,
                    order_number: Optional[str] = None) -> Tuple[str, str, str]:
    name = _first_name(full_name)
    _field_he = {"address": "כתובת למשלוח", "phone": "מספר טלפון",
                 "city": "עיר", "postal_code": "מיקוד"}.get(missing_field, missing_field)
    ctx = f" עבור הזמנה <b>{_e(order_number)}</b>" if order_number else ""
    body = (_p(f"היי {_e(name)}, כדי שנוכל להשלים את הטיפול{ctx} חסר לנו פרט אחד: "
               f"<b>{_e(_field_he)}</b>.")
            + _p("זה ייקח פחות מדקה — פשוט לחץ ועדכן, ואנחנו ממשיכים מיד.")
            + _btn(f"עדכון {_field_he}", update_url)
            + _p("רוצה לעדכן אותנו ישירות? השב למייל או כתוב לנו בוואטסאפ."))
    return (f"נדרש פרט אחד קטן להשלמת הטיפול — {BRAND}",
            _shell(f"חסר {_field_he} כדי להמשיך.", "חסר לנו פרט אחד 📝", body),
            f"היי {name}, חסר לנו {_field_he}{(' עבור '+order_number) if order_number else ''}. עדכון: {update_url}")


def delivery_update(full_name: str, order_number: str, status_he: str,
                    tracking_number: Optional[str], tracking_url: Optional[str],
                    eta: Optional[str] = None) -> Tuple[str, str, str]:
    name = _first_name(full_name)
    rows = [("הזמנה", _e(order_number)), ("סטטוס", _e(status_he))]
    if tracking_number:
        rows.append(("מספר מעקב", _e(tracking_number)))
    if eta:
        rows.append(("צפי הגעה", _e(eta)))
    body = (_p(f"היי {_e(name)}, יש עדכון על ההזמנה שלך: <b>{_e(status_he)}</b> 📦")
            + _info_box(rows, accent=_BLUE)
            + (_btn("מעקב אחר המשלוח →", tracking_url) if tracking_url else "")
            + _p("נמשיך לעדכן אותך עד שהחבילה מגיעה אליך."))
    return (f"📦 עדכון משלוח — {order_number}: {status_he}",
            _shell(f"{status_he} — הזמנה {order_number}.", "עדכון משלוח 📦", body),
            f"היי {name}, הזמנה {order_number}: {status_he}."
            + (f" מעקב: {tracking_url}" if tracking_url else ""))


def abandoned_cart(full_name: str, items_summary: str, total_ils: float, cart_url: str) -> Tuple[str, str, str]:
    name = _first_name(full_name)
    body = (_p(f"היי {_e(name)}, השארת פריטים בסל וחבל שיברחו לך! 🛒")
            + _info_box([("הפריטים שלך", _e(items_summary)), ("שווי הסל", f"₪{round(total_ils)}")], accent=_BLUE)
            + _btn("חזרה לסל והשלמת הרכישה", cart_url)
            + _p("צריך עזרה בבחירה או בהתאמה לרכב? אנחנו כאן בוואטסאפ."))
    return (f"שכחת משהו בסל? 🛒 {BRAND}",
            _shell("הפריטים שבחרת עדיין מחכים לך בסל.", "הסל שלך מחכה 🛒", body),
            f"היי {name}, יש לך פריטים בסל בשווי ₪{round(total_ils)}: {cart_url}")


def refund_confirmation(full_name: str, order_number: str, amount_ils: float,
                        days: str = "3-10 ימי עסקים") -> Tuple[str, str, str]:
    name = _first_name(full_name)
    body = (_p(f"היי {_e(name)}, הזיכוי עבור הזמנה <b>{_e(order_number)}</b> אושר ובוצע. ✅")
            + _info_box([("סכום הזיכוי", f"₪{round(amount_ils)}"), ("הזמנה", _e(order_number)),
                         ("זמן זיכוי צפוי", _e(days))], accent="#22c55e")
            + _p("הכסף יופיע באמצעי התשלום שלך תוך מספר ימי עסקים. לשאלות — אנחנו כאן."))
    return (f"↩️ הזיכוי אושר — {order_number}",
            _shell(f"זיכוי ₪{round(amount_ils)} עבור {order_number} בוצע.", "הזיכוי בוצע ↩️", body),
            f"היי {name}, זיכוי ₪{round(amount_ils)} עבור {order_number} אושר ({days}).")


def price_drop(full_name: str, part_name: str, new_price_ils: float, part_url: str) -> Tuple[str, str, str]:
    name = _first_name(full_name)
    body = (_p(f"היי {_e(name)}, חדשות טובות — המחיר של חלק שעקבת אחריו ירד! 📉")
            + _info_box([("החלק", _e(part_name)), ("מחיר עכשיו", f"₪{round(new_price_ils)}")], accent="#22c55e")
            + _btn("צפייה בחלק", part_url)
            + _p("המחירים משתנים לפי זמינות — כדאי לתפוס עכשיו."))
    return (f"📉 ירידת מחיר — {part_name[:30]}",
            _shell("מחיר של חלק שעקבת אחריו ירד.", "ירידת מחיר 📉", body),
            f"היי {name}, {part_name} עכשיו ₪{round(new_price_ils)}: {part_url}")


def review_request(full_name: str, order_number: str, review_url: str) -> Tuple[str, str, str]:
    name = _first_name(full_name)
    body = (_p(f"היי {_e(name)}, מקווים שהחלק מההזמנה <b>{_e(order_number)}</b> הגיע והתאים מצוין! ⭐")
            + _p("נשמח לשמוע איך היתה החוויה — חוות דעת קצרה עוזרת לנו ולנהגים אחרים.")
            + _btn("דרג את החוויה שלך", review_url)
            + _p("תודה שבחרת ב-" + BRAND + " 🙏"))
    return (f"איך היה? נשמח לחוות דעתך — {BRAND}",
            _shell("ספר לנו איך היתה החוויה שלך.", "איך היתה החוויה? ⭐", body),
            f"היי {name}, נשמח לחוות דעתך על הזמנה {order_number}: {review_url}")
