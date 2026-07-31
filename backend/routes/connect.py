"""
Script: routes/connect.py
Purpose: Public channel-picker hub page — the landing target of the QR code printed on
         every NOA social post (goal G8, 2026-07-20). One scan → the visitor picks the
         channel THEY prefer: WhatsApp / Telegram / Website / Facebook / Instagram.
Process:
  GET /api/v1/go?src=<tag>  → self-contained tri-lingual (HE/AR/EN) mobile-first HTML.
  The src tag (e.g. qr_tiktok_w29) is propagated into the website link as UTM params so
  QR-driven traffic is measurable per platform/week. src is sanitized to [a-z0-9_-].
Data Imported/Modified: none (read-only page; no DB access — cannot be a load vector).
Data Sources: NOA_* public channel URLs from BACKEND_AI_AGENTS (env-configurable).
Missing Data Delegation: channels with no configured URL are simply omitted.

REDESIGN 2026-07-29 (owner: the page did not look like our product):
  • Now uses the REAL design tokens from docs/UI_UX.md — hero #070e1d, header
    #0d1524, footer #021737, primary #2563eb, highlight #3b82f6, text #ffffff /
    #cbd5e1 / #94a3b8, Inter. It previously used generic slate (#0f172a,
    system-ui) that belonged to no part of the platform.
  • BIDI ISOLATION. The Hebrew stored here was always in correct logical order —
    what looked "reversed" was the browser reordering LATIN runs (WhatsApp,
    Instagram, AutoSpareFinder, the domain) inside a dir="rtl" container, which
    also throws punctuation to the wrong end. Every Latin run is now wrapped in
    <bdi dir="ltr">, which isolates it from the surrounding RTL paragraph. This is
    a RENDERING fix — do not "fix" it by reversing any string.
Last Updated: 2026-07-29
"""
import re

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()

# docs/UI_UX.md — keep in sync with the landing page.
C_HERO = "#070e1d"
C_CARD = "#0d1524"
C_FOOTER = "#021737"
C_BORDER = "#1e2a44"
C_PRIMARY = "#2563eb"
C_HIGHLIGHT = "#3b82f6"
C_TEXT = "#ffffff"
C_TEXT2 = "#cbd5e1"
C_MUTED = "#94a3b8"


def _channels() -> list:
    from BACKEND_AI_AGENTS import (
        NOA_WHATSAPP_URL, NOA_TELEGRAM_URL, NOA_FACEBOOK_URL,
        NOA_INSTAGRAM_URL, NOA_WEBSITE_URL,
    )
    # (icon, latin_name, hebrew_subtitle, url, brand_colour)
    return [
        ("💬", "WhatsApp", "דברו איתנו בוואטסאפ", NOA_WHATSAPP_URL, "#25D366"),
        ("✈️", "Telegram", "הבוט שלנו בטלגרם", NOA_TELEGRAM_URL, "#229ED9"),
        ("🌐", "האתר", "חיפוש חלק לפי מספר רישוי", NOA_WEBSITE_URL, C_PRIMARY),
        ("📘", "Facebook", "העמוד שלנו בפייסבוק", NOA_FACEBOOK_URL, "#1877F2"),
        ("📸", "Instagram", "עקבו אחרינו באינסטגרם", NOA_INSTAGRAM_URL, "#E1306C"),
    ]


_LATIN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .\-_/&']*$")


def _bidi(text: str) -> str:
    """Isolate a Latin run inside an RTL page.

    Without this the browser reorders `WhatsApp`, `AutoSpareFinder` and the domain
    against the surrounding Hebrew and pushes punctuation to the wrong end — which
    is what read as "reversed Hebrew". <bdi> scopes the bidi algorithm to the run.
    """
    return f'<bdi dir="ltr">{text}</bdi>' if _LATIN.match(text or "") else text


@router.get("/api/v1/go", response_class=HTMLResponse, include_in_schema=False)
async def channel_hub(src: str = ""):
    src_tag = re.sub(r"[^a-z0-9_\-]", "", (src or "").lower())[:40] or "qr"
    buttons = []
    for icon, name, sub, url, color in _channels():
        u = (url or "").strip()
        if not u:
            continue
        if "autosparefinder.co.il" in u and "utm_" not in u:
            sep = "&" if "?" in u else "?"
            u = f"{u}{sep}utm_source=qr&utm_medium=social&utm_campaign={src_tag}"
        buttons.append(
            f'<a class="btn" style="--c:{color}" href="{u}" rel="noopener">'
            f'<span class="ic" aria-hidden="true">{icon}</span>'
            f'<span class="tx"><b>{_bidi(name)}</b><small>{sub}</small></span>'
            f'<span class="go" aria-hidden="true">‹</span></a>'
        )

    html = f"""<!DOCTYPE html>
<html lang="he" dir="rtl"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<meta name="theme-color" content="{C_HERO}">
<title>AutoSpareFinder — דברו איתנו</title>
<style>
 *{{box-sizing:border-box}}
 body{{margin:0;font-family:Inter,system-ui,-apple-system,'Segoe UI',Arial,sans-serif;
   background:linear-gradient(180deg,{C_HERO} 0%,#0a1525 55%,{C_FOOTER} 100%);
   color:{C_TEXT};min-height:100vh;display:flex;flex-direction:column;
   align-items:center;justify-content:center;padding:28px 16px}}
 .brand{{display:flex;align-items:center;gap:10px;margin-bottom:6px}}
 .brand .logo{{width:38px;height:38px;border-radius:10px;background:{C_PRIMARY};
   display:flex;align-items:center;justify-content:center;font-size:1.15rem}}
 h1{{font-size:1.35rem;font-weight:800;margin:0;letter-spacing:-.2px}}
 h1 .hl{{color:{C_HIGHLIGHT}}}
 p.sub{{margin:10px 0 4px;color:{C_TEXT2};text-align:center;font-size:.95rem;line-height:1.6}}
 p.tri{{margin:0 0 24px;color:{C_MUTED};text-align:center;font-size:.8rem}}
 .wrap{{width:100%;max-width:420px;display:flex;flex-direction:column;gap:10px}}
 .btn{{display:flex;align-items:center;gap:14px;background:{C_CARD};
   border:1px solid {C_BORDER};border-radius:14px;padding:13px 15px;
   text-decoration:none;color:{C_TEXT};transition:border-color .15s,transform .1s}}
 .btn:hover{{border-color:{C_PRIMARY}}}
 .btn:active{{transform:scale(.985)}}
 .ic{{font-size:1.55rem;width:44px;height:44px;flex:0 0 44px;display:flex;
   align-items:center;justify-content:center;background:var(--c);border-radius:11px}}
 .tx{{display:flex;flex-direction:column;line-height:1.3;flex:1;min-width:0}}
 .tx b{{font-weight:700;font-size:.98rem}}
 .tx small{{color:{C_MUTED};font-size:.82rem}}
 .go{{color:{C_MUTED};font-size:1.3rem;flex:0 0 auto}}
 footer{{margin-top:26px;color:{C_MUTED};font-size:.75rem;text-align:center}}
 footer a{{color:{C_HIGHLIGHT};text-decoration:none}}
 @media(max-width:360px){{h1{{font-size:1.2rem}}.ic{{width:40px;height:40px;flex-basis:40px}}}}
</style></head><body>
<div class="brand">
  <span class="logo" aria-hidden="true">⚙️</span>
  <h1><bdi dir="ltr">AutoSpare<span class="hl">Finder</span></bdi></h1>
</div>
<p class="sub">חלקי חילוף לרכב — בחרו איפה נוח לכם לדבר איתנו</p>
<p class="tri">اختاروا القناة المفضلة لديكم · Pick your favorite channel</p>
<div class="wrap">{''.join(buttons)}</div>
<footer><a href="https://autosparefinder.co.il" rel="noopener"><bdi dir="ltr">autosparefinder.co.il</bdi></a></footer>
</body></html>"""
    return HTMLResponse(html, headers={"Cache-Control": "public, max-age=3600"})
