"""
agents/owner_console.py — the OWNER's private command console over WhatsApp.

When Khalil (OWNER_WHATSAPP_PHONE) messages the platform's WhatsApp, he is NOT a customer:
his messages are routed here (by routes/webhooks.py) instead of the customer sales brain
(process_user_message). This lets him hold a real two-way conversation with his agents —
AVI (the orchestrator/router) and NOA (social/marketing) — ask them about the live system,
give them tasks, and act on their notifications (e.g. approve/reject the NOA posts they send
him for approval) simply by replying.

Design:
  • Deterministic commands (fast, reliable, bilingual HE/EN) for the high-value actions:
      status | סטטוס            → full live system review
      harvester | שאיבה         → harvest-queue progress
      posts | פוסטים            → list NOA posts waiting for approval
      approve [id] | אשר        → approve + PUBLISH a pending NOA post (registry.dispatch)
      reject [id] | דחה         → reject a pending NOA post
      help | עזרה               → the command menu
  • Anything else → a real conversation with the chosen agent (prefix "noa"/"נועה" → NOA,
    else AVI), run in OWNER MODE: the agent is told it's talking to the owner (not a customer)
    and is given a LIVE system-status block so its answers are grounded, plus a short rolling
    history (Redis) so the conversation has memory.

Owner replies are sent back over WhatsApp by the webhook that calls this.
Last Updated: 2026-07-23
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List

from sqlalchemy import text as _sql

OWNER_PHONE = (os.getenv("OWNER_WHATSAPP_PHONE", "") or "").strip()
# WhatsApp now routes some contacts by a stable LID ("<digits>@lid") instead of the phone
# number (privacy update). The owner's device sends as a LID, so phone-only matching missed
# him and his messages fell through to the customer bot (found 2026-07-23: his inbound arrived
# as 98058566160397@lid, not +972586050155). Match the LID too. Comma-separated digits/LIDs OK.
OWNER_LIDS = {re.sub(r"[^\d]", "", x) for x in os.getenv("OWNER_WHATSAPP_LID", "").split(",") if x.strip()}
_HISTORY_KEY = "owner:console:history"
_HISTORY_MAX = 12  # turns kept for conversational memory


def _norm_phone(p: str) -> str:
    return re.sub(r"[^\d]", "", (p or "").replace("whatsapp:", ""))


def is_owner(phone: str) -> bool:
    """True if this sender is the platform owner — by phone OR by his WhatsApp LID. Never
    matches the platform's OWN number (that would be a self-loop); OWNER_PHONE is
    +972586050155, the platform account is a different number (see [[status-update-loop]])."""
    norm = _norm_phone(phone)
    if not norm:
        return False
    if OWNER_PHONE and norm == _norm_phone(OWNER_PHONE):
        return True
    if norm in OWNER_LIDS:
        return True
    return False


# ── live system status ────────────────────────────────────────────────────────
async def _harvest_stats(db) -> Dict[str, Any]:
    row = (await db.execute(_sql("""
        SELECT COUNT(*) FILTER (WHERE status IN ('done','empty')) done,
               COUNT(*) total,
               COUNT(DISTINCT brand_en) FILTER (WHERE status='done') bdone,
               COUNT(DISTINCT brand_en) btot,
               COUNT(*) FILTER (WHERE status='pending') pending,
               COUNT(*) FILTER (WHERE status='in_progress') in_progress,
               COALESCE(SUM(parts_found),0) parts
        FROM harvest_queue"""))).fetchone()
    if not row:
        return {}
    done, total, bdone, btot, pending, inprog, parts = row
    return {"done": done, "total": total, "pct": round(done * 100.0 / total, 1) if total else 0,
            "bdone": bdone, "btot": btot, "pending": pending, "in_progress": inprog, "parts": parts}


async def _catalog_stats(db) -> Dict[str, Any]:
    try:
        row = (await db.execute(_sql("""
            SELECT COUNT(*) FILTER (WHERE is_active) total,
                   COUNT(*) FILTER (WHERE is_active AND importer_price_ils>0) priced
            FROM parts_catalog"""))).fetchone()
        return {"total": row[0], "priced": row[1]} if row else {}
    except Exception:
        return {}


async def _agent_health(db) -> Dict[str, Any]:
    """Recent worker failures (real ones only — restart-orphans excluded by the guard)."""
    try:
        rows = (await db.execute(_sql("""
            SELECT split_part(job_id,':',1) job, status
            FROM job_registry
            WHERE started_at > NOW() - INTERVAL '3h' AND status IN ('failed','dead')
            ORDER BY started_at DESC LIMIT 6"""))).fetchall()
        return {"recent_failures": [f"{r[0]} ({r[1]})" for r in rows]}
    except Exception:
        return {"recent_failures": []}


async def build_status_snapshot(db, *, brief: bool = False) -> str:
    h = await _harvest_stats(db)
    parts_line = ""
    if h:
        parts_line = (f"🔧 שאיבת קטלוג: {h['done']:,}/{h['total']:,} דגמים ({h['pct']}%) · "
                      f"{h['bdone']}/{h['btot']} מותגים · בתהליך {h['in_progress']} · "
                      f"ממתינים {h['pending']:,} · חלקים {h['parts']:,}")
    if brief:
        return parts_line
    c = await _catalog_stats(db)
    cat_line = (f"📦 קטלוג פעיל: {c['total']:,} חלקים · עם מחיר {c['priced']:,}"
                if c else "")
    a = await _agent_health(db)
    fails = a.get("recent_failures") or []
    fail_line = ("✅ סוכנים ועובדים: אין כשלים ב-3 השעות האחרונות"
                 if not fails else "⚠️ כשלים אחרונים: " + ", ".join(fails))
    import subprocess
    try:
        load = subprocess.check_output(["cat", "/proc/loadavg"], timeout=5).decode().split()[0]
        load_line = f"🖥️ עומס מערכת (1דק'): {load} (6 ליבות)"
    except Exception:
        load_line = ""
    return "\n".join(x for x in [parts_line, cat_line, fail_line, load_line] if x)


# ── NOA post queue actions ────────────────────────────────────────────────────
async def _list_pending_posts(db, limit: int = 8) -> List[Dict[str, Any]]:
    rows = (await db.execute(_sql("""
        SELECT id, content, platforms, created_at
        FROM social_posts WHERE status='pending_approval'
        ORDER BY created_at DESC LIMIT :lim"""), {"lim": limit})).fetchall()
    out = []
    for r in rows:
        plats = r[2] if isinstance(r[2], list) else (json.loads(r[2]) if r[2] else [])
        out.append({"id": str(r[0]), "content": r[1] or "", "platforms": plats})
    return out


def _short(pid: str) -> str:
    return pid.replace("-", "")[:8]


async def _resolve_post(db, token: str):
    """Find a pending post by short-id token, or the newest pending if token empty."""
    token = (token or "").strip()
    rows = await _list_pending_posts(db, limit=25)
    if not rows:
        return None
    if not token:
        return rows[0]
    for p in rows:
        if _short(p["id"]).startswith(token.lower()) or p["id"].startswith(token.lower()):
            return p
    return None


async def _approve_and_publish(db, post: Dict[str, Any]) -> str:
    """Mark approved + publish to each platform via the registry (same path the admin
    panel uses). Honest per-platform result back to the owner."""
    pid = post["id"]
    await db.execute(_sql("""UPDATE social_posts SET status='approved',
                             approved_by=:who, updated_at=NOW() WHERE id=:id"""),
                     {"who": "owner_whatsapp", "id": pid})
    await db.commit()
    content = post["content"]
    platforms = post.get("platforms") or []
    # media/link best-effort from the app's meta helper (image-required platforms need it)
    media_url = link = None
    try:
        from BACKEND_API_ROUTES import _social_meta  # type: ignore
        res = await db.execute(_sql("SELECT * FROM social_posts WHERE id=:id"), {"id": pid})
        meta = _social_meta(res.fetchone())
        media_url = (meta or {}).get("media_url") or (meta or {}).get("image_url")
        link = (meta or {}).get("link") or (meta or {}).get("cta_url")
    except Exception:
        pass
    from social import registry
    hashtags = [f"#{m.group(1)}" for m in re.finditer(r"#([A-Za-z0-9_֐-׿؀-ۿ]+)", content)]
    results = []
    published = {}
    for p in platforms:
        p = str(p).strip().lower()
        if p in registry.MEDIA_REQUIRED and not media_url:
            results.append(f"• {p}: ⏭️ דורש תמונה (אין מדיה)")
            continue
        if not registry.is_configured(p):
            results.append(f"• {p}: ⚠️ לא מוגדר")
            continue
        try:
            r = await registry.dispatch(p, content, media_url=media_url, hashtags=hashtags, link=link)
            if r.get("ok"):
                published[p] = r.get("id")
                results.append(f"• {p}: ✅ פורסם")
            else:
                results.append(f"• {p}: ❌ {str(r.get('error'))[:80]}")
        except Exception as e:
            results.append(f"• {p}: ❌ {str(e)[:80]}")
    if published:
        try:
            await db.execute(_sql("""UPDATE social_posts SET status='published',
                                     published_at=NOW(), external_post_ids=:ext, updated_at=NOW()
                                     WHERE id=:id"""),
                             {"ext": json.dumps(published), "id": pid})
            await db.commit()
        except Exception:
            await db.rollback()
    head = f"✅ אושר ופורסם ({_short(pid)}):" if published else f"אושר ({_short(pid)}) אך הפרסום נכשל:"
    return head + "\n" + "\n".join(results)


async def _reject_post(db, post: Dict[str, Any], reason: str = "") -> str:
    await db.execute(_sql("""UPDATE social_posts SET status='rejected',
                             rejection_reason=:r, approved_by=:who, updated_at=NOW()
                             WHERE id=:id"""),
                     {"r": reason or "נדחה ע\"י הבעלים ב-WhatsApp", "who": "owner_whatsapp",
                      "id": post["id"]})
    await db.commit()
    return f"🚫 הפוסט נדחה ({_short(post['id'])})."


# ── rolling conversation memory (Redis) ───────────────────────────────────────
async def _load_history() -> List[Dict[str, str]]:
    try:
        from BACKEND_AUTH_SECURITY import get_redis
        r = await get_redis()
        raw = await r.get(_HISTORY_KEY)
        return json.loads(raw) if raw else []
    except Exception:
        return []


async def _save_history(hist: List[Dict[str, str]]) -> None:
    try:
        from BACKEND_AUTH_SECURITY import get_redis
        r = await get_redis()
        await r.set(_HISTORY_KEY, json.dumps(hist[-_HISTORY_MAX:], ensure_ascii=False), ex=86400)
    except Exception:
        pass


_HELP = (
    "🎛️ *מרכז הבקרה שלך* (WhatsApp)\n"
    "אני יכול לחבר אותך ל-*AVI* (מנהל/מתאם) ו-*NOA* (שיווק/סושיאל).\n\n"
    "פקודות מהירות:\n"
    "• *סטטוס* — סקירת מערכת חיה\n"
    "• *שאיבה* — התקדמות שאיבת הקטלוג\n"
    "• *פוסטים* — פוסטים של NOA שממתינים לאישור\n"
    "• *אשר [מזהה]* — אשר ופרסם פוסט\n"
    "• *דחה [מזהה]* — דחה פוסט\n"
    "• *עזרה* — התפריט הזה\n\n"
    "לשיחה: פשוט כתוב. להפניה ל-NOA התחל ב-\"נועה\". "
    "אפשר לשאול אותי לבצע משימות ולנהל את המערכת."
)


# ── agent chat (owner mode) ───────────────────────────────────────────────────
def _pick_agent(message: str) -> tuple[str, str]:
    """Return (agent_key, stripped_message). Default AVI (router)."""
    m = message.strip()
    low = m.lower()
    for tok, key in (("noa", "social_media_manager_agent"), ("נועה", "social_media_manager_agent"),
                     ("avi", "router_agent"), ("אבי", "router_agent")):
        if low.startswith(tok):
            return key, m[len(tok):].lstrip(" :,-–").strip() or m
    return "router_agent", m


_OWNER_SYSTEM = {
    "router_agent": (
        "אתה AVI — המתאם הראשי של מערכת AutoSpareFinder. אתה מדבר עכשיו עם *חליל, הבעלים* "
        "של הפלטפורמה — לא לקוח. דבר אליו ישירות, מקצועי, קצר וברור בעברית. אתה מכיר את "
        "מצב המערכת החי המצורף למטה — השתמש בו כדי לתת תשובות מבוססות. הבעלים יכול לבקש "
        "ממך מידע על המערכת, לנתב משימות לסוכנים, ולבקש פעולות. אם פעולה דורשת פקודה "
        "מובנית (סטטוס/שאיבה/פוסטים/אשר/דחה) — הצע לו לכתוב אותה. לעולם אל תתייחס אליו "
        "כלקוח ואל תמכור לו. אל תמציא נתונים — אם אינך יודע, אמור זאת."
    ),
    "social_media_manager_agent": (
        "אתה NOA — מנהלת הסושיאל והשיווק של AutoSpareFinder. את מדברת עכשיו עם *חליל, "
        "הבעלים* — לא קהל. דברי אליו ישירות בעברית, חכם ואנושי. את יכולה לדון ברעיונות "
        "לפוסטים/קמפיינים, לתת המלצות, ולהסביר מה מפורסם. הבעלים יכול לאשר או לדחות "
        "פוסטים ממתינים ע\"י כתיבת 'פוסטים' ואז 'אשר'/'דחה'. אל תמציאי מחירים או נתונים."
    ),
}


async def process_owner_message(message: str, source: str = "whatsapp") -> str:
    """Entry point for owner WhatsApp messages. Returns the reply text.

    Opens its own CATALOG-DB session (harvest_queue / parts_catalog / job_registry /
    social_posts all live in the catalog DB — NOT the PII DB the webhook hands us)."""
    from BACKEND_DATABASE_MODELS import async_session_factory
    async with async_session_factory() as db:
        return await _process_owner_message(message, db, source)


async def _process_owner_message(message: str, db, source: str = "whatsapp") -> str:
    msg = (message or "").strip()
    low = msg.lower()

    # ── deterministic commands ────────────────────────────────────────────────
    if low in ("help", "menu", "עזרה", "תפריט", "?", "start", "היי", "hi"):
        return _HELP
    if low in ("status", "סטטוס", "review", "סקירה", "מצב"):
        return "📊 *סטטוס מערכת*\n" + await build_status_snapshot(db)
    if low in ("harvester", "שאיבה", "קטלוג", "harvest"):
        snap = await build_status_snapshot(db, brief=True)
        return snap or "אין נתוני שאיבה כרגע."
    if low in ("posts", "פוסטים", "פוסט"):
        pend = await _list_pending_posts(db)
        if not pend:
            return "אין פוסטים שממתינים לאישור. ✅"
        lines = ["📝 *פוסטים ממתינים לאישור:*"]
        for p in pend:
            first = (p["content"].splitlines() or [""])[0][:70]
            lines.append(f"🆔 {_short(p['id'])} [{', '.join(p['platforms'])}]\n   {first}")
        lines.append("\nלאישור: *אשר <מזהה>* · לדחייה: *דחה <מזהה>*")
        return "\n".join(lines)
    m_ap = re.match(r"^(approve|אשר)\b\s*(\S+)?", msg, re.I)
    if m_ap:
        post = await _resolve_post(db, m_ap.group(2) or "")
        if not post:
            return "לא מצאתי פוסט ממתין לאישור. כתוב *פוסטים* לרשימה."
        return await _approve_and_publish(db, post)
    m_rj = re.match(r"^(reject|דחה)\b\s*(\S+)?", msg, re.I)
    if m_rj:
        post = await _resolve_post(db, m_rj.group(2) or "")
        if not post:
            return "לא מצאתי פוסט ממתין. כתוב *פוסטים* לרשימה."
        return await _reject_post(db, post)

    # ── conversational path (AVI / NOA in owner mode) ─────────────────────────
    # Call the LLM DIRECTLY (not via get_agent): the router_agent is a JSON classifier
    # and produces garbage on freeform chat. We just need a grounded conversational reply.
    agent_key, clean = _pick_agent(msg)
    try:
        from hf_client import hf_text
        status_block = await build_status_snapshot(db)
        system = (_OWNER_SYSTEM[agent_key]
                  + "\n\n--- מצב המערכת החי (עכשיו) ---\n" + status_block
                  + "\n(אם הבעלים מבקש פעולה מהירה, הפנה לפקודות: סטטוס/שאיבה/פוסטים/אשר/דחה. "
                  + "השב בעברית, קצר וברור, עד ~6 שורות.)")
        hist = await _load_history()
        convo = "\n".join(f"{m['role']}: {m['content']}" for m in hist[-8:])
        prompt = (convo + "\n" if convo else "") + f"user: {clean}"
        reply = await hf_text(prompt, system=system, priority=True, max_tokens=700)
        reply = (reply or "").strip() or "לא הצלחתי לייצר תשובה כרגע, נסה שוב."
        hist2 = hist + [{"role": "user", "content": clean},
                        {"role": "assistant", "content": reply}]
        await _save_history(hist2)
        tag = "NOA" if agent_key == "social_media_manager_agent" else "AVI"
        return f"[{tag}] {reply}"
    except Exception as e:
        return f"⚠️ שגיאה בעיבוד ההודעה: {str(e)[:120]}"
