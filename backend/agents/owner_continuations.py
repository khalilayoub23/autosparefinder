"""
Script:  agents/owner_continuations.py
Purpose: The ORIGINATING agent's side of Resume Interaction (owner_interactions.py, 2026-11-26). Each continuation verb
         is also a normal typed owner command, so a button follow-up, a poll vote and a typed command all run the SAME
         agent code and the SAME gates:
           ערוך <post> [text]        NOA post - the owner's exact new text (campaign_manager.update_post_content)
           שנהפוסט <post> [request]  NOA post - NOA rewrites it per the owner's request (LLM + the G8 finalizer)
           ערוךתשובה <item> [text]   NOA engagement reply draft - replaces the DRAFT (never sends it)
           ערוךמייל <ref> [text]     Email Agent - a new verified Gmail draft (never sends it)
           שאלספק <supplier> [q]     NIR - answers a question about ONE candidate from its stored record (read-only)
           בדיקתשינוי <ref> [text]   inert device self-test
         Without text the verb shows the current proposal (the prompt). With text it revises and returns the revised
         proposal with a fresh choice trailer, so the WhatsApp chokepoint attaches NEW buttons bound to the new version
         (older versions are superseded). A revision never approves, publishes, sends, activates, orders or pays.
Data Imported/Modified: social_posts (+ social_post_revisions) via update_post_content; social_inbox.reply_text;
         email_agent_messages draft columns (via owner_flow.revise_draft). Nothing for NIR / self-test.
Data Sources: catalog DB (posts, inbox, suppliers), PII DB (email), Gmail API (drafts.create + read-back), LLM (NOA, NIR).
Missing Data Delegation: n/a.
Last Updated: 2026-11-26
"""
from __future__ import annotations

import contextvars
import json
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, List, Optional, Tuple

import owner_poll as _owner_poll

VERBS = ("ערוך", "שנהפוסט", "ערוךתשובה", "ערוךמייל", "שאלספק", "בדיקתשינוי")
_RE = re.compile(r"^(ערוך|edit|שנהפוסט|ערוךתשובה|ערוךמייל|שאלספק|בדיקתשינוי)\b\s*(\S+)?\s*(.*)$", re.I | re.S)

# test seams (production: hf_client.hf_text and NOA's own finalizer)
LLM: Optional[Callable[..., Awaitable[str]]] = None
NOA_FINALIZE: Optional[Callable[[str, list], str]] = None


@dataclass
class Result:
    ok: bool
    text: str
    choices: List[Tuple[str, str]] = field(default_factory=list)

    def render(self) -> str:
        if self.ok and self.choices:
            return _owner_poll.attach(self.text, "מה הלאה?", self.choices)
        return self.text


async def _llm(prompt: str, system: str) -> str:
    if LLM is not None:
        return await LLM(prompt, system=system)
    from hf_client import hf_text
    return await hf_text(prompt, system=system, priority=True, max_tokens=900, temperature=0.4, reasoning_effort="low")


def _finalize(raw: str, platforms: list) -> str:
    if NOA_FINALIZE is not None:
        return NOA_FINALIZE(raw, platforms)
    from BACKEND_AI_AGENTS import SocialMediaManagerAgent
    return SocialMediaManagerAgent._finalize_noa_post(raw, platforms=platforms)


def parse(message: str) -> Optional[Tuple[str, str, str]]:
    m = _RE.match(str(message or "").strip())
    if not m:
        return None
    verb = "ערוך" if m.group(1).lower() == "edit" else m.group(1)
    return verb, (m.group(2) or "").strip(), (m.group(3) or "").strip()


# ── NOA post ───────────────────────────────────────────────────────────────────────────────────────
async def _post(db, ref: str):
    from agents.owner_console import _resolve_post
    return await _resolve_post(db, ref) if ref else None


def _post_result(post_short: str, version, text: str, head: str) -> Result:
    return Result(True, f"{head} ({post_short}, גרסה {version}) — ממתין לאישורך מחדש, לא פורסם.\n\n{text[:900]}\n\n"
                        f"_לפרסום: *אשר {post_short}* · שינוי: *שנהפוסט {post_short} <מה>* · דחייה: *דחה {post_short}*_",
                  _owner_poll.post_choices(post_short))


async def noa_edit(db, ref: str, text: str) -> Result:
    from agents.owner_console import _short, _edit_post
    post = await _post(db, ref)
    if not post:
        return Result(False, "פורמט: *ערוך <מזהה> <הטקסט המתוקן>* (המזהה מהרשימה: *פוסטים*). לא נמצא פוסט ממתין.")
    sid = _short(post["id"])
    if not text:
        cur = str(post.get("content") or "").strip()
        return Result(True, f"✏️ עריכת פוסט {sid}. הנוסח הנוכחי:\n\n{cur[:900]}\n\n"
                            f"שלח את הנוסח החדש המלא (או *ערוך {sid} <הטקסט החדש>*). הפוסט יחכה לאישור מחדש.")
    verdict = notes_verdict(text, str(post.get("content") or "")) if FROM_BUTTON_FOLLOWUP.get() else "post"
    if verdict == "notes":
        # #158: notes sent after the "own wording" button are not the post - NOA rewrites instead of pasting them in.
        # The typed `ערוך <id> <text>` never comes here (always exact), so the hint below really works.
        res = await noa_change(db, ref, text)
        if res.ok:
            res.text = (res.text + f"\n\nℹ️ ההודעה שלך נראתה כמו הערות ולא כמו פוסט, אז NOA שכתבה לפיהן. "
                        f"להחלפה מילה במילה: *ערוך {sid} <הנוסח המלא>*")
        return res
    if verdict == "ask":
        return Result(False, f"🤔 לא שיניתי כלום בפוסט {sid}: לא ברור אם זה הנוסח המלא או הערות "
                             f"(חסרים בו המחיר/הקישור/התגיות של הפוסט, או שהוא קצר בהרבה).\n"
                             f"• אם אלה הערות ל-NOA: *שנהפוסט {sid} <ההערות>*\n"
                             f"• אם זה הנוסח המלא: *ערוך {sid} <הנוסח המלא>*")
    ok, msg, ver = await _edit_post(db, post["id"], text, source="whatsapp", actor="owner")
    return _post_result(sid, ver, text.strip(), "✏️ הפוסט עודכן לנוסח שלך") if ok else Result(False, msg)


_NOTE_OPENERS = ("הערה", "הערות", "תשנ", "שנה", "שני", "תקצר", "קצר", "תוסיף", "תוסיפי", "הוסף", "הוסיפי", "תוריד",
                 "תורידי", "הורד", "בלי ", "אל ת", "תכתוב", "תכתבי", "תעשה", "תעשי", "תדגיש", "תדגישי", "במקום", "צריך",
                 "לא צריך", "פשוט ת", "notes", "note:", "make it", "shorter", "add ", "remove ")
# words that talk ABOUT a post's wording (a post never calls itself "the note" or discusses "the words")
_META_RE = re.compile(r"הער(ה|ות|תי)|תתייחס|תתקנ|תקני|ניסוח|המילים|מילים שלא")
_FACT_RE = re.compile(r"₪|\d+\s*ש\"?ח|autosparefinder|https?://|#\S+", re.I)
SHORT_NOTE_CHARS = 60

# True only while a BUTTON follow-up (owner_interaction_flow.handle_owner_text) runs the continuation. A typed
# `ערוך <id> <text>` is always the owner's exact text (#158 expert review: the typed escape hatch must stay exact).
FROM_BUTTON_FOLLOWUP: contextvars.ContextVar = contextvars.ContextVar("owner_followup_from_button", default=False)


def notes_verdict(text: str, current: str) -> str:
    """'notes' | 'ask' | 'post' for a text sent after the "own wording" button.
    notes = strong evidence it is editing notes: talks about notes/wording (real cases, post 3e253a49 2026-11-26:
            "...מילים שלא קשורות..." and "פשוט תתייחסי להערה שרשמתי מקודם"), or a SHORT instruction.
    ask   = weak evidence (the post's price/link/hashtags are gone, or it is far shorter): change nothing, ask.
    post  = a replacement post."""
    t, cur = (text or "").strip(), (current or "").strip()
    if not t:
        return "post"
    if _META_RE.search(t) or (len(t) < SHORT_NOTE_CHARS and t.lower().startswith(_NOTE_OPENERS)
                              and not _FACT_RE.search(t)):     # a short post with a price/hashtag is a post
        return "notes"
    cur_facts = bool(_FACT_RE.search(cur))
    if cur_facts and not _FACT_RE.search(t):
        return "ask"                                   # the post's price/link/hashtags are gone
    if not cur_facts and len(cur) >= 200 and len(t) < 0.35 * len(cur):
        return "ask"                                   # nothing to compare but the size: far shorter
    return "post"


def looks_like_notes(text: str, current: str) -> bool:
    """Strong evidence only (used to rebase rewrites and to refuse publishing pasted notes)."""
    return notes_verdict(text, current) == "notes"


async def _rewrite_base(db, post: dict) -> Tuple[str, List[str]]:
    """(text NOA should rewrite, the owner's earlier notes on this post). Newest revisions that are owner notes pasted
    in by the old exact-edit path are notes, not the post: the base is the last real version before them."""
    cur = str(post.get("content") or "").strip()
    try:
        from social.campaign_manager import get_revisions
        revs = await get_revisions(db, str(post["id"]))
    except Exception:
        return cur, []
    notes: List[str] = []
    for i in range(len(revs) - 1, 0, -1):
        r, prev = revs[i], revs[i - 1]
        if r.get("actor") == "owner" and looks_like_notes(str(r.get("content") or ""), str(prev.get("content") or "")):
            notes.insert(0, str(r.get("content") or "").strip())
            continue
        return str(r.get("content") or cur).strip(), notes
    return (str(revs[0].get("content") or cur).strip() if revs else cur), notes


async def noa_change(db, ref: str, request: str) -> Result:
    """NOA revises her own proposal per the owner's request (the originating agent receives the actual request)."""
    from agents.owner_console import _short, _edit_post, _OWNER_SYSTEM, _noa_guidelines_get
    post = await _post(db, ref)
    if not post:
        return Result(False, "פורמט: *שנהפוסט <מזהה> <מה לשנות>* (המזהה מהרשימה: *פוסטים*). לא נמצא פוסט ממתין.")
    sid = _short(post["id"])
    cur, earlier_notes = await _rewrite_base(db, post)
    if not request:
        return Result(True, f"🔁 שינוי פוסט {sid} על ידי NOA. הנוסח הנוכחי:\n\n{cur[:900]}\n\n"
                            f"מה לשנות? (למשל: קצר יותר, טון מצחיק יותר, להדגיש את המחיר). NOA תכתוב גרסה חדשה לאישורך.")
    system = _OWNER_SYSTEM["social_media_manager_agent"]
    g = await _noa_guidelines_get(db)
    if g:
        system += "\n\n--- הנחיות קבועות מהבעלים (חובה לפעול לפיהן) ---\n" + g
    prompt = ("זה פוסט שכתבת וממתין לאישור הבעלים:\n<<<\n" + cur[:3000] + "\n>>>\n\n"
              + ("הערות קודמות של הבעלים על הפוסט הזה (גם אותן צריך לקיים):\n<<<\n" + "\n---\n".join(earlier_notes)[:1500]
                 + "\n>>>\n\n" if earlier_notes else "")
              + "הבעלים ביקש את השינוי הבא:\n<<<\n" + request[:1000] + "\n>>>\n\n"
              "כתבי מחדש את הפוסט לפי הבקשה. שמרי על כל העובדות (מוצר, מחיר, דגם רכב, קישורים) בדיוק כפי שהם — "
              "אל תמציאי מחיר, הנחה או מבצע. כתבי עברית טבעית ונכונה: כל משפט חייב להיות קשור לחלק ולשימוש בו, "
              "בלי ביטויים חסרי משמעות או מטאפורות מאולצות. אם הבעלים ציטט מילים שלא קשורות — הן לא יופיעו בנוסח החדש. "
              "החזירי רק את הטקסט הסופי של הפוסט, בלי הסברים.")
    try:
        raw = await _llm(prompt, system)
    except Exception as exc:
        return Result(False, f"⚠️ NOA לא הצליחה לנסח גרסה חדשה ({str(exc)[:80]}). הפוסט לא השתנה — נסה שוב.")
    plats = post.get("platforms") or []
    new = (_finalize(raw or "", list(plats)) or "").strip()
    if len(new) < 20:
        return Result(False, "⚠️ NOA החזירה טקסט ריק/לא תקין. הפוסט לא השתנה — נסה שוב או *ערוך* בעצמך.")
    ok, msg, ver = await _edit_post(db, post["id"], new, source="whatsapp_change", actor="noa")
    return _post_result(sid, ver, new, f"🔁 NOA כתבה גרסה חדשה לפי הבקשה \"{request[:60]}\"") if ok else Result(False, msg)


# ── NOA engagement reply draft ─────────────────────────────────────────────────────────────────────
async def engagement_edit(db, ref: str, text: str) -> Result:
    from social import engagement as _eng
    from agents.owner_console import _short
    item = await _eng.resolve_inbox(db, ref) if ref else None
    if not item:
        return Result(False, "לא מצאתי תגובה ממתינה עם המזהה הזה. כתוב *תגובות* לרשימה.")
    sid = _short(item["id"])
    if not text:
        return Result(True, f"✏️ עריכת התשובה ל-{item.get('author') or 'לקוח'} ({item['platform']}):\n"
                            f"💬 {str(item.get('message') or '')[:300]}\n✍️ הטיוטה: {str(item.get('reply_text') or '')[:500]}\n\n"
                            "שלח את הנוסח החדש. הוא יישמר כטיוטה ויחכה לאישורך — לא יישלח לבד.")
    await _eng.set_draft(db, item["id"], text.strip())
    return Result(True, f"✏️ טיוטת התשובה עודכנה ({sid}) — לא נשלחה. ממתינה לאישורך:\n\n{text.strip()[:600]}\n\n"
                        f"_לשליחה: *ענה {sid}* · לדילוג: *דלג {sid}*_",
                  _owner_poll.inbox_choices(sid))


# ── Email Agent draft ──────────────────────────────────────────────────────────────────────────────
async def email_edit(ref: str, text: str, *, store=None, client=None, mailbox: str = "") -> Result:
    from email_agent import owner_flow as of
    if store is None:
        from email_agent.store import PgStore
        store = PgStore()
    if not text:
        return Result(True, (await of.describe_item(store, ref.lower())) +
                      "\n\n✏️ שלח את הנוסח החדש המלא של התשובה. תיווצר טיוטה חדשה ב-Gmail לאישורך — שום דבר לא יישלח.")
    if client is None:
        from email_agent.config import load_config
        from email_agent.gmail_client import GmailClient
        cfg = load_config()
        if cfg.missing():
            return Result(False, "חיבור Gmail של סוכן המיילים לא מוגדר — אי אפשר ליצור טיוטה.")
        client, mailbox = GmailClient(cfg), cfg.mailbox
    ok, msg = await of.revise_draft(store, client, ref.lower(), text, mailbox=mailbox)
    rows = await store.by_ref(ref.lower()) if ok else []
    r = of.ref(rows[0]) if rows else ref
    return Result(ok, msg, _owner_poll.email_choices(r, has_draft=True) if ok else [])


# ── NIR candidate question ─────────────────────────────────────────────────────────────────────────
_NIR_SYSTEM = ("את/ה NIR, סוכן איתור הספקים של AutoSpareFinder, ועונה לבעלים על ספק מועמד אחד. "
               "ענה רק מתוך הרשומה שמסופקת (שם, אתר, מדינה, ציון, סטטוס, נימוקים, אותות, מה חסר). "
               "אם המידע לא ברשומה — אמור במפורש שאין לך את המידע ומה צריך כדי לברר. "
               "אל תמציא מחירים, תנאים, משלוח או אנשי קשר, ואל תצהיר שבוצעה פעולה. עברית קצרה וברורה.")


async def nir_ask(ref: str, question: str, *, lookup=None) -> Result:
    from services import supplier_sourcing as ss
    res = await (lookup or ss.candidate_record)(ref)
    if not res.get("ok"):
        return Result(False, {"short": "כתוב לפחות 4 תווים של מזהה הספק.",
                              "ambiguous": "המזהה מתאים ליותר מספק אחד — כתוב עוד תווים."}.get(
                                  res.get("error"), "לא מצאתי ספק מועמד עם המזהה הזה. כתוב *ספקים* לרשימה."))
    rec = res["record"]
    cred = rec.get("credentials") or {}
    sid = str(rec["id"])[:8]
    summary = (f"🔌 {rec.get('name')} · {rec.get('website') or ''} · {rec.get('country') or '?'} · ציון {rec.get('reliability_score')}\n"
               f"סטטוס: {cred.get('status') or '?'}" + (f" · חסר: {str(cred.get('needs'))[:200]}" if cred.get("needs") else ""))
    choices = [] if rec.get("is_active") else _owner_poll.supplier_candidate_choices(sid)
    if not question:
        return Result(True, summary + "\n\nמה תרצה לשאול את NIR על הספק הזה?")
    facts = json.dumps({k: rec.get(k) for k in ("name", "website", "country", "reliability_score", "is_active")} |
                       {"status": cred.get("status"), "needs": cred.get("needs"), "reasons": cred.get("reasons"),
                        "signals": cred.get("signals"), "qualification": cred.get("qualification"),
                        "dropshipping": cred.get("dropshipping")}, ensure_ascii=False, default=str)[:4000]
    try:
        answer = (await _llm(f"רשומת הספק:\n{facts}\n\nשאלת הבעלים: {question[:800]}", _NIR_SYSTEM) or "").strip()
    except Exception as exc:
        return Result(False, f"⚠️ NIR לא הצליח לענות כרגע ({str(exc)[:80]}). נסה שוב.")
    if not answer:
        return Result(False, "⚠️ NIR החזיר תשובה ריקה. נסה שוב.")
    hint = "" if rec.get("is_active") else f"\n_להפעלה: *אשרספק {sid}* · לדחייה: *דחהספק {sid}*_"
    return Result(True, f"🔎 NIR על {rec.get('name')} ({sid}):\n{answer[:1200]}\n\n{summary}{hint}", choices)


# ── dispatcher ─────────────────────────────────────────────────────────────────────────────────────
async def run(message: str, db) -> Optional[Result]:
    """None when `message` is not a continuation verb (the console keeps its own routing)."""
    p = parse(message)
    if not p:
        return None
    verb, ref, text = p
    if verb == "בדיקתשינוי":
        if not ref:
            return Result(False, "פורמט: *בדיקתשינוי <ref>*")
        if not text:
            return Result(True, f"✅ בדיקת כפתורים: התקבלה הבחירה *שינוי* ({ref}). לא בוצעה שום פעולה.")
        return Result(True, f"✅ בדיקת המשך: הטקסט שלך חזר לבקשה {ref} ({len(text)} תווים). לא בוצעה שום פעולה.",
                      _owner_poll.selftest_choices(ref))
    if verb == "ערוך":
        return await noa_edit(db, ref, text)
    if verb == "שנהפוסט":
        return await noa_change(db, ref, text)
    if verb == "ערוךתשובה":
        return await engagement_edit(db, ref, text)
    if verb == "ערוךמייל":
        if not ref:
            return Result(False, "פורמט: *ערוךמייל <מזהה> <הנוסח החדש>*")
        return await email_edit(ref, text)
    if verb == "שאלספק":
        if not ref:
            return Result(False, "פורמט: *שאלספק <מזהה> <שאלה>*")
        return await nir_ask(ref, text)
    return None
