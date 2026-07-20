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
Last Updated: 2026-07-20
"""
import re

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()


def _channels() -> list:
    from BACKEND_AI_AGENTS import (
        NOA_WHATSAPP_URL, NOA_TELEGRAM_URL, NOA_FACEBOOK_URL,
        NOA_INSTAGRAM_URL, NOA_WEBSITE_URL,
    )
    return [
        ("💬", "WhatsApp", "דברו איתנו בוואטסאפ", NOA_WHATSAPP_URL, "#25D366"),
        ("✈️", "Telegram", "הבוט שלנו בטלגרם", NOA_TELEGRAM_URL, "#229ED9"),
        ("🌐", "האתר", "חיפוש חלק לפי מספר רישוי", NOA_WEBSITE_URL, "#0f766e"),
        ("📘", "Facebook", "העמוד שלנו בפייסבוק", NOA_FACEBOOK_URL, "#1877F2"),
        ("📸", "Instagram", "עקבו אחרינו באינסטגרם", NOA_INSTAGRAM_URL, "#E1306C"),
    ]


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
            f'<span class="ic">{icon}</span><span class="tx"><b>{name}</b>'
            f'<small>{sub}</small></span></a>'
        )
    html = f"""<!DOCTYPE html>
<html lang="he" dir="rtl"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>AutoSpareFinder — דברו איתנו</title>
<style>
 body{{margin:0;font-family:system-ui,-apple-system,'Segoe UI',Arial,sans-serif;
   background:#0f172a;color:#e2e8f0;min-height:100vh;display:flex;flex-direction:column;
   align-items:center;justify-content:center;padding:24px 16px;box-sizing:border-box}}
 h1{{font-size:1.5rem;margin:0 0 4px;text-align:center}}
 p.sub{{margin:0 0 6px;color:#94a3b8;text-align:center;font-size:.95rem}}
 p.tri{{margin:0 0 22px;color:#64748b;text-align:center;font-size:.8rem}}
 .wrap{{width:100%;max-width:420px;display:flex;flex-direction:column;gap:12px}}
 .btn{{display:flex;align-items:center;gap:14px;background:#1e293b;border:1px solid #334155;
   border-radius:14px;padding:14px 16px;text-decoration:none;color:#e2e8f0;
   transition:transform .1s}}
 .btn:active{{transform:scale(.98)}}
 .ic{{font-size:1.7rem;width:44px;height:44px;display:flex;align-items:center;
   justify-content:center;background:var(--c);border-radius:12px}}
 .tx{{display:flex;flex-direction:column;line-height:1.25}}
 .tx small{{color:#94a3b8}}
 footer{{margin-top:26px;color:#475569;font-size:.75rem;text-align:center}}
</style></head><body>
<h1>AutoSpareFinder 🚗</h1>
<p class="sub">חלקי חילוף לרכב — בחרו איפה נוח לכם לדבר איתנו</p>
<p class="tri">اختاروا القناة المفضلة لديكم · Pick your favorite channel</p>
<div class="wrap">{''.join(buttons)}</div>
<footer>autosparefinder.co.il</footer>
</body></html>"""
    return HTMLResponse(html, headers={"Cache-Control": "public, max-age=3600"})
