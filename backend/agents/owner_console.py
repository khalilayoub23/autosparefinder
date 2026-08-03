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
    "🎛️ *מרכז הבקרה שלך* (WhatsApp)\n\n"
    "*לפנות לסוכן — עם @:*\n"
    "• *@אבי <הודעה>* — AVI, מנהל/מתאם המערכת\n"
    "• *@נועה <הודעה>* — NOA, שיווק וסושיאל\n"
    "• *@עוזר <הודעה>* — עוזר אישי כללי (חכם ומועיל)\n"
    "(בלי @ — פונה ל-AVI כברירת מחדל)\n\n"
    "*פקודות מהירות:*\n"
    "• *סטטוס* — סקירת מערכת חיה\n"
    "• *שאיבה* — התקדמות שאיבת הקטלוג\n"
    "• *פוסטים* — פוסטים של NOA שממתינים לאישור\n"
    "• *אשר [מזהה]* / *דחה [מזהה]* — אשר/דחה פוסט\n"
    "• *תגובות* — תגובות ברשתות שממתינות לתשובה (NOA כבר ניסחה)\n"
    "• *ענה [מזהה]* / *דלג [מזהה]* — שלח את תשובת NOA / דלג\n"
    "• *הנחיות* — ההנחיות הקבועות שנתת ל-NOA\n"
    "• *ספקים* — ספקים חדשים ש-NIR מצא וממתינים לאישור\n"
    "• *מילים* — מילות סיווג חדשות שהמערכת למדה וממתינות לאישורך\n"
    "• *מודל* — מצב מנוע הסיווג: אילו סוגי טקסט הוכיחו דיוק\n"
    "• *תור* — תור המשימות הכבדות: מה רץ, כמה נותר (נמדד מה-DB)\n"
    "• *עצור* / *המשך* — עצירה בטוחה של התור (בסוף המנה) והמשך\n"
    "• *מקורות* — NIR יחפש ספקים חדשים ברשת עכשיו\n"
    "• *אשרספק [מזהה]* / *דחהספק [מזהה]* — הפעל/דחה ספק\n"
    "• *עזרה* — התפריט הזה\n\n"
    "דוגמה: *@נועה תשמרי: 2 פוסטים ביום בשעות שיא, עם קריאה לפעולה* — "
    "וזה יישמר וייושם בפועל."
)


# ── agent chat (owner mode) ───────────────────────────────────────────────────
_AGENT_TOKENS = {
    "noa": "social_media_manager_agent", "נועה": "social_media_manager_agent",
    "avi": "router_agent", "אבי": "router_agent",
    # A general-purpose owner assistant you can @-call (owner request 2026-07-25). It is an
    # assistant agent running on the platform's own LLM — not the external dev tool.
    "claude": "assistant_agent", "קלוד": "assistant_agent",
    "assistant": "assistant_agent", "עוזר": "assistant_agent",
}

# Shared roster context so an agent doesn't hallucinate who the others are.
_ROSTER = (
    "\n\nצוות הסוכנים של AutoSpareFinder (לידיעתך): AVI (מתאם/מנהל מערכת), "
    "NOA (שיווק וסושיאל), NIR (חלקים/התאמה/OEM), MAYA (מכירות/תמחור), LIOR (הזמנות), "
    "TAL (כספים/מע\"מ/חשבוניות), DANA (תמיכה/החזרות), OREN (אבטחה/הונאות), "
    "BOAZ (ספקים/סנכרון מחירים), REX (שאיבת קטלוג)."
)


def _pick_agent(message: str) -> tuple[str, str, bool]:
    """Return (agent_key, stripped_message, explicit). Routing:
      • '@noa …' / '@נועה …' / '@avi …' / '@אבי …'  → that agent (explicit=True)
      • bare 'noa …' / 'avi …' prefix               → that agent (explicit=True)
      • anything else                               → AVI, the orchestrator (explicit=False)
    The '@' form is the clear way to call an agent (owner request 2026-07-25)."""
    m = message.strip()
    _toks = "|".join(re.escape(t) for t in sorted(_AGENT_TOKENS, key=len, reverse=True))
    mm = re.match(rf"^@\s*({_toks})\b[\s:,،.\-–]*", m, re.I)
    if not mm:
        mm = re.match(rf"^({_toks})\b[\s:,،.\-–]+", m, re.I)
    if mm:
        key = _AGENT_TOKENS[mm.group(1).lower()]
        rest = m[mm.end():].strip()
        return key, (rest or m), True
    return "router_agent", m, False


# ── NOA owner-guidelines (persisted so NOA's posting loop actually applies them) ──
_SAVE_INTENT = re.compile(
    r"תשמר|שמר[יי]?|נוהל|הנחי|מעכשיו|מעתה|תמיד תפרסמ|save|remember|guideline|from now",
    re.I)


def _guidelines_text(raw) -> str:
    """AgentMemory.get double-decodes plain strings across processes, so we store the
    guidelines as a DICT {'text': …} (dicts round-trip correctly). Accept legacy string too."""
    if isinstance(raw, dict):
        return str(raw.get("text") or "")
    return str(raw or "")


async def _noa_guidelines_get(db) -> str:
    try:
        from agents.memory import AgentMemory, ensure_memory_table
        await ensure_memory_table(db)
        return _guidelines_text(await AgentMemory(db, agent_name="noa").get("owner_guidelines"))
    except Exception:
        return ""


async def _noa_guidelines_save(db, text: str) -> None:
    """Append a new owner directive to NOA's persisted guidelines (deduped, kept compact).
    NOA's marketing loop reads this same key and injects it into every generation."""
    try:
        from agents.memory import AgentMemory, ensure_memory_table
        await ensure_memory_table(db)
        mem = AgentMemory(db, agent_name="noa")
        try:
            cur = _guidelines_text(await mem.get("owner_guidelines"))
        except Exception:
            cur = ""   # corrupt/legacy value → start fresh (the dict form below round-trips)
        line = re.sub(r"\s+", " ", text).strip()
        if line and line not in cur:
            cur = (cur + "\n• " + line).strip()[-2000:]   # keep last ~2000 chars
        await mem.set("owner_guidelines", {"text": cur}, ttl_hours=24 * 365)  # dict → safe
    except Exception as e:
        print(f"[owner_console] guideline save failed: {e}")


# WhatsApp-facing reply rules shared by both agents. Two things ruined the earlier replies:
# (1) the fallback LLM dumped its chain-of-thought ("1. Analyze the Request… 5. Constructing
#     the Final Output") instead of the answer; (2) markdown **bold** doesn't render in
# WhatsApp (bold is a single *). These rules + the post-processor below fix both.
_WA_REPLY_RULES = (
    "\n\nכללי מענה מחייבים (וואטסאפ):\n"
    "• ענה בעברית בלבד, קצר וישיר — עד ~6 שורות. בלי הקדמות ובלי סיכומים מיותרים.\n"
    "• תן אך ורק את התשובה הסופית. אסור בהחלט להראות שלבי חשיבה/ניתוח/תכנון "
    "(אסור 'Analyze', 'Draft', 'Step', '1. …', 'Internal Monologue', רשימת שלבים).\n"
    "• עיצוב וואטסאפ: הדגשה עם כוכבית *בודדת* בלבד — לעולם לא **. בלי כותרות markdown (#).\n"
    "• השתמש בנתוני המערכת החיים למטה; אל תמציא מספרים.\n"
    "• *אסור להמציא קישורים.* הקישור היחיד המותר הוא https://autosparefinder.co.il "
    "(או קישור שנמסר לך במפורש). לעולם אל תמציא נתיב כמו /oil-filters-corolla — "
    "הוא לא קיים והלקוח יגיע לעמוד שגוי.\n"
    "• *אסור להמציא מבצעים, הנחות, קופונים או תוכניות נאמנות.* אין קופונים פעילים. "
    "אם אין מבצע אמיתי — אל תרמוז שיש.\n"
    "• אם צריך פעולה מובנית — הפנה לפקודה: סטטוס / שאיבה / פוסטים / אשר / דחה."
)

# When the owner ISSUES AN INSTRUCTION (rather than asking for output), the reply must
# be an acknowledgement of what will change — not a freshly generated artefact.
# Without this NOA answered "from now on always put a real price in every post" by
# writing a post, which is what reads as bot-like: it responded to the topic instead of
# to the intent. (owner, 2026-07-29: "act like agents not like bots")
_DIRECTIVE_NOTE = (
    "\n\n--- שים לב: ההודעה הזו היא הוראה/נוהל, לא בקשה לתוכן ---\n"
    "הבעלים נותן לך הנחיה קבועה. אשר בקצרה שקלטת, ונסח במשפט אחד מה ישתנה בפועל מעכשיו. "
    "אל תייצר פוסט, אל תבקש ממנו מידע שכבר יש לך במערכת, ואל תשאל שאלה מיותרת."
)
_OWNER_SYSTEM = {
    "router_agent": (
        "אתה AVI — המתאם הראשי של AutoSpareFinder, מדבר עם *חליל, הבעלים* (לא לקוח). "
        "תפקידך: לתת לו תמונת מצב מדויקת של המערכת, המלצות תפעוליות, ולנתב משימות. "
        "היה ישיר, מקצועי ומועיל. אל תמכור לו ואל תתייחס אליו כלקוח."
        + _ROSTER + _WA_REPLY_RULES
    ),
    "social_media_manager_agent": (
        "את NOA — מנהלת השיווק והסושיאל של AutoSpareFinder, מדברת עם *חליל, הבעלים*. "
        "כשהוא מבקש פוסט — כתבי את הפוסט המוכן לפרסום בלבד (פתיח קולע, גוף קצר, וקריאה "
        "לפעולה), אנושי וחכם, בלי להסביר את התהליך. כשהוא שואל על שיווק — תני תשובה ממוקדת. "
        "כשהוא נותן לך *הוראה* — אשרי מה נקלט ומה ישתנה, אל תכתבי פוסט. "
        "אל תמציאי מחירים או נתונים. "
        "הקריאה לפעולה בפוסט היא תמיד חיפוש לפי מספר רישוי באתר — לא נתיב מוצר מומצא, "
        "ולא מבצע/הנחה שלא קיימים. "
        "לאישור/דחיית פוסטים ממתינים: 'פוסטים' ואז 'אשר'/'דחה'."
        + _ROSTER + _WA_REPLY_RULES
    ),
    "assistant_agent": (
        "אתה *העוזר האישי* של חליל, הבעלים של AutoSpareFinder (הוא קורא לך גם 'קלוד'). "
        "אתה עוזר כללי, חכם ומועיל — עונה על כל שאלה, מסביר, מתכנן, ונותן עצה טכנית ועסקית "
        "על המערכת והעסק. יש לך גישה למצב המערכת החי למטה. אתה עוזר תפעולי, לא סוכן שירות "
        "לקוחות ולא מוכר. אם צריך פעולה מובנית — הפנה לפקודה."
        + _ROSTER + _WA_REPLY_RULES
    ),
}
_AGENT_TAG = {"social_media_manager_agent": "NOA", "assistant_agent": "עוזר"}


def _clean_wa_reply(text: str) -> str:
    """Post-process an agent reply for WhatsApp: strip any leaked chain-of-thought, and
    convert markdown the app can't render. Belt-and-braces on top of the prompt rules —
    the fallback model in particular still leaks a numbered 'analysis' when Cerebras 429s."""
    t = (text or "").strip()
    # 1) strip leaked reasoning via the shared customer-flow stripper
    try:
        from BACKEND_AI_AGENTS import _strip_leaked_reasoning
        t = _strip_leaked_reasoning(t) or t
    except Exception:
        pass
    # 2) targeted salvage: if it's still a numbered "analysis dump" (…Constructing the Final
    #    Output / Final Output / Final Response), keep only what comes AFTER that heading
    #    (consuming any trailing markdown/colon/dashes so no "**:" junk remains).
    looks_like_cot = bool(re.search(
        r"(?im)^\s*\d+\.\s|Analyze the Request|Internal Monologue|Drafting the Post|"
        r"Refining the Post|Constructing the Final", t))
    if looks_like_cot:
        m = None
        for mm in re.finditer(r"(?:Constructing the Final Output|Final Output|Final Response|"
                              r"התוצר הסופי|הפוסט הסופי|התשובה הסופית)[\s*:\-.]*", t, re.I):
            m = mm  # take the LAST such marker
        if m:
            t = t[m.end():].strip()
        else:
            # no explicit "final" marker — drop obvious analysis lines, keep the rest
            t = "\n".join(ln for ln in t.splitlines() if not re.match(
                r"\s*(?:\d+\.\s*)?\*{0,2}(?:Analyze|Draft|Refin|Construct|Present|Step|User|"
                r"Request|Role|Tone|Context|Topic|Target|Format|Headline|Body|CTA)\b", ln, re.I)).strip()
    # 3) markdown → WhatsApp: **bold** → *bold*, drop markdown headers/list-star noise
    t = re.sub(r"\*\*+([^*\n]+?)\*\*+", r"*\1*", t)
    t = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", t)
    # 4) INVENTED LINKS. NOA produced `https://autosparefinder.co.il/oil-filters-corolla`
    #    — a route that does not exist. A prompt rule alone does not hold under a
    #    fallback model, and an owner who forwards that link sends a customer to a 404,
    #    so the deep path is collapsed to the site root here as well. Only genuinely
    #    routed prefixes survive (checkout, admin, the QR channel picker, search).
    t = re.sub(
        r"(https?://(?:www\.)?autosparefinder\.co\.il)/(?!(?:pay/|admin\b|api/v1/go\b|search\b))"
        r"[^\s)\],.]*",
        r"\1", t)
    t = re.sub(r"\n{3,}", "\n\n", t).strip()
    return t


async def _engagement_inbox(db) -> str:
    """List social comments/DMs NOA has drafted a reply for, awaiting owner approval."""
    from social import engagement as _eng
    try:
        await _eng.ensure_inbox_table(db)
        pend = await _eng.pending_for_owner(db)
    except Exception as e:
        return f"⚠️ שגיאה בקריאת התגובות: {str(e)[:120]}"
    if not pend:
        cfg = _eng.configured_platforms()
        if not cfg:
            return ("אין ערוץ חברתי מחובר לקריאת תגובות עדיין (חסר FACEBOOK_PAGE_TOKEN). "
                    "ברגע שיחובר טוקן, NOA תתחיל לקרוא ולנסח תשובות אוטומטית.")
        return "אין תגובות שממתינות לתשובה כרגע. ✅"
    lines = ["💬 *תגובות ממתינות לתשובתך:*"]
    for p in pend:
        who = p.get("author") or "לקוח"
        msg = (p.get("message") or "")[:90]
        draft = (p.get("reply_text") or "")[:140]
        lines.append(f"\n🆔 {_short(p['id'])} · {p['platform']} · {who}\n"
                     f"   💬 {msg}\n   ✍️ טיוטת NOA: {draft}")
    lines.append("\nלשליחה: *ענה <מזהה>* (או *ענה <מזהה> טקסט משלך*) · לדילוג: *דלג <מזהה>*")
    return "\n".join(lines)


async def _engagement_reply(db, token: str, override: str = "") -> str:
    """Approve+send NOA's drafted reply (or the owner's own text) to a social comment."""
    from social import engagement as _eng
    item = await _eng.resolve_inbox(db, token)
    if not item:
        return "לא מצאתי תגובה ממתינה. כתוב *תגובות* לרשימה."
    text = (override or item.get("reply_text") or "").strip()
    if not text:
        return "אין טקסט לשליחה. כתוב *ענה <מזהה> הטקסט שלך*."
    res = await _eng.send_reply(item["platform"], str(item["external_id"]), text)
    if not res.get("ok"):
        return f"⚠️ שליחה נכשלה: {str(res.get('error'))[:140]}"
    await _eng.mark_replied(db, item["id"], res.get("id"))
    return f"✅ נשלחה תשובה ב-{item['platform']} ({_short(item['id'])})."


async def _engagement_skip(db, token: str) -> str:
    from social import engagement as _eng
    item = await _eng.resolve_inbox(db, token)
    if not item:
        return "לא מצאתי תגובה ממתינה. כתוב *תגובות* לרשימה."
    await _eng.mark_skipped(db, item["id"])
    return f"⏭️ דילגתי על התגובה {_short(item['id'])}."


# ── NIR supplier sourcing (discover sellers → onboard → owner approves) ────────

# ── Category keyword learning (LLM assist) — owner approval gate ─────────────
# One keyword can move thousands of parts ('bolt' matches 16,541), so a token the
# LLM proposed does not go live until the owner approves it here.


async def _embed_phase_report() -> str:
    """Phase-1/2 scorecard: which input types have earned auto-write."""
    from catalog_scraper import scraper_session_factory
    import embed_policy as ep
    import category_input_type as cit
    try:
        async with scraper_session_factory() as db:
            card = await ep.type_scorecard(db)
    except Exception as e:
        return f"⚠️ שגיאה: {str(e)[:120]}"

    lines = ["🧠 *מצב מנוע הסיווג (AI מקומי):*", "", ep.describe_phase(), ""]
    if not card:
        lines.append("עדיין לא נאספו הצעות מהמודל. ההיסטוריה תתחיל להיבנות בסבב הקרוב.")
        lines.append("")
        lines.append(f"תנאי מעבר לשלב 2: לפחות {ep.MIN_DECISIONS} החלטות שלך לכל סוג, "
                     f"ואישור של {ep.MIN_APPROVAL_RATE:.0%} לפחות.")
        return "\n".join(lines)

    lines.append("*לפי סוג טקסט:*")
    for ty, d in sorted(card.items(), key=lambda kv: -(kv[1]["decisions"])):
        rate = f"{d['approval_rate']:.0%}" if d["approval_rate"] is not None else "—"
        mark = "✅ מוכן לשלב 2" if d["meets_bar"] else (
            "⏳ אוסף היסטוריה" if d["eligible"] else "🚫 לא מורשה לכתיבה")
        lines.append(
            f"• *{ty}* — אושרו {d['approved']} · נדחו {d['rejected']} · "
            f"ממתינים {d['pending']}\n   ↳ אחוז אישור {rate} · {mark}")
    lines.append("")
    lines.append(f"תנאי מעבר: ≥{ep.MIN_DECISIONS} החלטות ו-≥{ep.MIN_APPROVAL_RATE:.0%} אישור.")
    lines.append("כרגע: המודל *מציע חוקים בלבד* — שום חלק לא משתנה בלי אישורך (*מילים*).")
    return "\n".join(lines)


async def _keywords_list() -> str:
    from catalog_scraper import scraper_session_factory
    import category_learning as cl
    try:
        async with scraper_session_factory() as db:
            pend = await cl.pending_for_owner(db)
    except Exception as e:
        return f"⚠️ שגיאה בקריאת המילים: {str(e)[:120]}"
    if not pend:
        return ("אין מילות־קטלוג חדשות שממתינות לאישור. ✅\n"
                "המערכת לומדת מילים חדשות רק כשהיא נתקעת על חלק שהיא לא מזהה.")
    lines = ["🔤 *מילים חדשות שהמערכת למדה וממתינות לאישורך:*", ""]
    # Show the EVIDENCE next to the vote. "96% agreement" only means the votes
    # were consistent with each other, and the votes come from parts nobody has
    # classified correctly yet — so it can be 96% consistent and still wrong
    # (קופסת→gearbox was 98%, and the real parts are storage/relay/control
    # boxes). The evidence line is measured against parts that ARE already
    # filed in a real category, which is independent of the votes.
    async with scraper_session_factory() as db:
        for k in pend:
            he = cl.category_map.display_name(k["category"], "he")
            row = [f"• *{k['token']}* → {he} ({k['category']})",
                   f"   ↳ {k['observations']} חלקים הסכימו · {k['agreement']:.0%} הסכמה"]
            try:
                p = await cl.evidence_profile(db, k["token"], proposed=k["category"])
                if p["verdict"] == "ok":
                    row.append(f"   ✅ נתוני אמת תומכים (פי {p['margin']} מהשנייה)")
                elif p["verdict"] == "category_mismatch":
                    top_he = cl.category_map.display_name(p["top"], "he") if p["top"] else "?"
                    row.append(f"   ⚠️ נתוני אמת מצביעים על *{top_he}* — לא על מה שהוצע")
                elif p["verdict"] == "ambiguous":
                    row.append(f"   ⚠️ מילה כללית מדי (פי {p['margin']} בלבד מהקטגוריה השנייה)")
                else:
                    row.append("   ⚠️ אין מספיק נתוני אמת להחליט")
            except Exception:
                pass
            lines.append("\n".join(row))
    lines.append("")
    lines.append("לאישור: *אשרמילה <מילה>* · לדחייה: *דחהמילה <מילה>*")
    lines.append("מילה עם ⚠️ תיחסם — לאישור בכל זאת: *אשרמילה <מילה> בכוח*")
    lines.append("אחרי אישור המילה תסווג *כל* החלקים שמכילים אותה.")
    return "\n".join(lines)


async def _keyword_approve(token: str, force: bool = False) -> str:
    from catalog_scraper import scraper_session_factory
    import category_learning as cl
    if not token:
        return "צריך מילה. כתוב *מילים* לרשימה."
    try:
        async with scraper_session_factory() as db:
            res = await cl.approve(db, token, force=force)
    except Exception as e:
        return f"⚠️ שגיאה: {str(e)[:120]}"
    if not res.get("ok"):
        if res.get("error") == "blocklisted":
            return (f"❌ *{token}* חסומה לצמיתות (שם יצרן / בורג / מיקום) "
                    "ולא תיהפך לכלל סיווג.")
        # The evidence gate refused. Show WHY, with the real numbers, and offer
        # the override — the owner has domain knowledge the catalogue does not.
        ev = res.get("evidence") or {}
        if res.get("error") in ("category_mismatch", "ambiguous", "insufficient_evidence"):
            top_he = (cl.category_map.display_name(ev.get("top"), "he")
                      if ev.get("top") else "—")
            spread = " · ".join(f"{c}:{n:,}" for c, n in (ev.get("spread") or [])[:3])
            why = {
                "category_mismatch": f"נתוני האמת מצביעים על *{top_he}*, לא על מה שהוצע",
                "ambiguous": f"מילה כללית מדי — פי {ev.get('margin')} בלבד מהקטגוריה השנייה",
                "insufficient_evidence": "אין מספיק חלקים מסווגים כדי להחליט",
            }[res["error"]]
            return (f"⛔ *{token}* לא אושרה — {why}.\n"
                    f"פילוח אמיתי: {spread}\n"
                    f"אם את/ה בטוח/ה בכל זאת: *אשרמילה {token} בכוח*")
        return f"לא מצאתי מילה ממתינה בשם *{token}*. כתוב *מילים* לרשימה."
    he = cl.category_map.display_name(res["category"], "he")
    return (f"✅ *{res['token']}* אושרה → {he}.\n"
            "הכלל פעיל עכשיו, וכל החלקים שמכילים את המילה יסווגו בסבב הקרוב.")


async def _keyword_reject(token: str) -> str:
    from catalog_scraper import scraper_session_factory
    import category_learning as cl
    if not token:
        return "צריך מילה. כתוב *מילים* לרשימה."
    try:
        async with scraper_session_factory() as db:
            res = await cl.reject(db, token)
    except Exception as e:
        return f"⚠️ שגיאה: {str(e)[:120]}"
    if not res.get("ok"):
        return f"לא מצאתי מילה בשם *{token}*."
    return f"🚫 *{token}* נדחתה — לא תיטען ולא תוצע שוב."


async def _sourcing_list() -> str:
    from services import supplier_sourcing as ss
    pending = await ss.list_pending()
    if not pending:
        return "אין ספקים חדשים שממתינים לאישור. כתוב *מקורות* כדי ש-NIR יחפש ספקים ברשת."
    cred = [p for p in pending if p.get("status") == "pending_credentials"]
    rev = [p for p in pending if p.get("status") != "pending_credentials"]
    lines = ["🔌 *ספקים שממתינים לאישור (NIR):*"]
    if rev:
        lines.append("\n*מוכנים להפעלה:*")
        for p in rev[:10]:
            lines.append(f"🆔 {p['id'][:8]} · {p['name'][:30]} · {p.get('website','')} · ציון {p['reliability_score']}")
    if cred:
        lines.append("\n*ממתינים לפרטי חשבון/טוקן ממך:*")
        for p in cred[:10]:
            lines.append(f"🆔 {p['id'][:8]} · {p['name'][:30]}\n   ↳ {(p.get('needs') or '')[:150]}")
    lines.append("\nלהפעלה: *אשרספק <מזהה>* · לדחייה: *דחהספק <מזהה>*")
    return "\n".join(lines)


async def _sourcing_run() -> str:
    from services import supplier_sourcing as ss
    res = await ss.run_sourcing_cycle()
    ob = res.get("onboarded", [])
    head = f"🔎 NIR סרק את הרשת ({len(res.get('queries',[]))} חיפושים): נמצאו {res.get('discovered',0)} מועמדים, צורפו {len(ob)} חדשים לאישור."
    if ob:
        head += "\n" + "\n".join(f"• {o['name'][:34]} ({o['domain']}) ציון {o['score']}" for o in ob[:8])
    head += "\n\nכתוב *ספקים* לרשימה ואישור."
    return head


async def _sourcing_approve(token: str) -> str:
    from services import supplier_sourcing as ss
    if not token:
        return "ציין מזהה ספק: *אשרספק <מזהה>*"
    r = await ss.approve_supplier(token)
    return (f"✅ הופעל הספק *{r['name']}* — יופיע בהשוואת המחירים ברגע שיהיו לו מחירים." if r.get("ok")
            else "לא מצאתי ספק ממתין עם המזהה הזה. כתוב *ספקים* לרשימה.")


async def _sourcing_reject(token: str) -> str:
    from services import supplier_sourcing as ss
    if not token:
        return "ציין מזהה ספק: *דחהספק <מזהה>*"
    r = await ss.reject_supplier(token)
    return (f"🗑️ נדחה הספק *{r['name']}*." if r.get("ok")
            else "לא מצאתי ספק ממתין עם המזהה הזה. כתוב *ספקים* לרשימה.")


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
    if low in ("הנחיות", "guidelines", "נהלים", "כללים"):
        g = await _noa_guidelines_get(db)
        return ("📋 *הנחיות NOA (נשמרות ומיושמות):*\n" + g) if g else \
            "אין הנחיות שמורות ל-NOA עדיין. כתוב לה למשל: @נועה תשמרי: 2 פוסטים ביום בשעות שיא."

    # ── NOA social engagement inbox (read + reply to comments/DMs) ────────────
    if low in ("inbox", "תגובות", "תגובה", "comments", "engagement"):
        return await _engagement_inbox(db)
    m_rep = re.match(r"^(reply|ענה|תעני|ענו)\b\s*(\S+)?\s*(.*)?$", msg, re.I | re.S)
    if m_rep:
        return await _engagement_reply(db, m_rep.group(2) or "", (m_rep.group(3) or "").strip())
    m_skip = re.match(r"^(skip|דלג|דלגי)\b\s*(\S+)?", msg, re.I)
    if m_skip:
        return await _engagement_skip(db, m_skip.group(2) or "")

    # ── NIR supplier sourcing (discover sellers on the web → onboard → approve) ─
    m_aps = re.match(r"^(approve[\-_ ]?supplier|אשרספק|אשר ספק)\b\s*(\S+)?", msg, re.I)
    if m_aps:
        return await _sourcing_approve(m_aps.group(2) or "")
    m_rjs = re.match(r"^(reject[\-_ ]?supplier|דחהספק|דחה ספק)\b\s*(\S+)?", msg, re.I)
    if m_rjs:
        return await _sourcing_reject(m_rjs.group(2) or "")
    # ── category keyword learning (approve/reject what the LLM taught) ────────
    m_kwa = re.match(r"^(approve[\-_ ]?word|אשרמילה|אשר מילה)\b\s*(\S+)?", msg, re.I)
    if m_kwa:
        _kw_arg = (m_kwa.group(2) or "").strip()
        # "אשרמילה <word> בכוח" / "... force" = owner override of the evidence gate
        _rest = msg[m_kwa.end():].strip().lower()
        _force = bool(re.search(r"\b(בכוח|force|בכל מקרה)\b", _rest))
        return await _keyword_approve(_kw_arg, force=_force)
    m_kwr = re.match(r"^(reject[\-_ ]?word|דחהמילה|דחה מילה)\b\s*(\S+)?", msg, re.I)
    if m_kwr:
        return await _keyword_reject((m_kwr.group(2) or "").strip())
    if low in ("ai", "מנוע", "מודל", "embed", "שלב"):
        return await _embed_phase_report()
    if low in ("words", "מילים", "מילה", "keywords", "מילות מפתח"):
        return await _keywords_list()

    # ── job queue: observe, stop, resume ─────────────────────────────────────
    if low in ("queue", "תור", "משימות", "jobs", "pipeline"):
        import job_queue as _jq
        # live=True measures the running step RIGHT NOW (~10-20s) instead of
        # reporting a periodic figure that can be hours old. The owner asked for
        # status; handing back a stale number without saying so is worse than
        # taking twenty seconds.
        return _jq.render_status(await _jq.status(db, live=True))
    if low in ("עצור", "stop", "עצירה"):
        import job_queue as _jq
        ok = await _jq.request_stop(True)
        return ("🛑 בקשת עצירה נשלחה — התור ייעצר בסוף המנה הנוכחית "
                "(לא באמצע כתיבה). להמשך: *המשך*." if ok else
                "⚠️ לא הצלחתי לרשום את בקשת העצירה (Redis לא זמין).")
    if low in ("המשך", "resume", "continue"):
        import job_queue as _jq
        await _jq.request_stop(False)
        return "▶️ בקשת העצירה בוטלה — התור ימשיך מהמקום שבו עצר."

    if low in ("suppliers", "ספקים", "מקורות ספקים"):
        return await _sourcing_list()
    if low in ("discover", "מקורות", "sourcing", "חפש ספקים"):
        return await _sourcing_run()

    # ── conversational path (AVI / NOA in owner mode) ─────────────────────────
    # Call the LLM DIRECTLY (not via get_agent): the router_agent is a JSON classifier
    # and produces garbage on freeform chat. We just need a grounded conversational reply.
    agent_key, clean, explicit = _pick_agent(msg)
    is_noa = agent_key == "social_media_manager_agent"

    # If the owner gives NOA a DIRECTIVE (save/always/from-now/guideline …), persist it so
    # NOA's actual posting loop applies it — not just an ack in chat. (This is the fix for
    # "I told AVI guidelines for NOA — did NOA get them?": now she really does.)
    saved_note = ""
    is_directive = bool(_SAVE_INTENT.search(clean))
    if is_noa and is_directive:
        await _noa_guidelines_save(db, clean)
        saved_note = "\n\n📋 שמרתי את ההנחיה ואפעל לפיה מעכשיו. (לצפייה: כתוב *הנחיות*)"

    try:
        from hf_client import hf_text
        status_block = await build_status_snapshot(db)
        system = _OWNER_SYSTEM[agent_key] + "\n\n--- מצב המערכת החי (עכשיו) ---\n" + status_block
        # A directive is answered with an acknowledgement, by ANY agent — not just NOA.
        if is_directive:
            system += _DIRECTIVE_NOTE
        if is_noa:
            g = await _noa_guidelines_get(db)
            if g:
                system += ("\n\n--- הנחיות קבועות מהבעלים (חובה לפעול לפיהן) ---\n" + g)
        hist = await _load_history()
        convo = "\n".join(f"{m['role']}: {m['content']}" for m in hist[-8:])
        prompt = (convo + "\n" if convo else "") + f"user: {clean}"
        reply = await hf_text(prompt, system=system, priority=True, max_tokens=600)
        reply = _clean_wa_reply(reply) or "בסדר, קיבלתי."
        reply += saved_note
        hist2 = hist + [{"role": "user", "content": clean},
                        {"role": "assistant", "content": reply}]
        await _save_history(hist2)
        tag = _AGENT_TAG.get(agent_key, "AVI")
        return f"[{tag}] {reply}"
    except Exception as e:
        return f"⚠️ שגיאה בעיבוד ההודעה: {str(e)[:120]}"
