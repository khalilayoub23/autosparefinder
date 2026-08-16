"""
social/coherence_guard.py — Hebrew fluency/hallucination guard for NOA's posts.

WHY THIS EXISTS (2026-08-14). post_guard.py already checks whether a post is
about the right PART (topic-relevance via embedding similarity). It has no
concept of whether the SENTENCE ITSELF makes sense. Real published posts kept
reaching the owner's approval queue with two distinct failure classes prompt
instructions alone could not reliably prevent (gpt-oss-120b at temperature 0.35
is stochastic — an instruction reduces but does not eliminate the failure rate):

  1. Invented words replacing a real given fact — "מוט ייצוב (שלדג)" where שלדג
     (kingfisher, the bird) was fabricated in place of the real manufacturer
     name "Toyota".
  2. Nonsensical anthropomorphic metaphors — a battery that "ממריא למנוחה"
     (takes off/flies to rest), a radiator that "מתחיל לשיר" (starts to sing).
     The Hebrew is grammatically fluent; the CLAIM is meaningless. A stronger
     Hebrew generator would only produce more fluent nonsense (same lesson as
     post_guard.py's original grounding-vs-fluency distinction).

Calibrated on 8 real cases (3 real published bad posts, 5 good) — see
FIXES_TRACKER.md 2026-08-14. Deliberately allows the house style of appending
a bare brand name after a part name without a preposition ("מצבר Bosch",
"רדיאטור NRF 550201") since that pattern is used throughout real posts and an
earlier draft of this judge false-positived on it.

Every NOA post already requires OWNER APPROVAL before publishing — this guard
is a pre-filter that reduces how often a broken draft reaches that review, not
the last line of defense. It can be imperfect without being unsafe.

Fails OPEN on infra failure (same principle as post_guard.py): an API error
must never silently block all posting. A skipped cycle is signalled via
notify_owner by the caller, never swallowed silently.
"""
from __future__ import annotations

import logging
import re
from typing import Optional, Tuple

logger = logging.getLogger("coherence_guard")

JUDGE_SYSTEM = (
    "את עורכת לשון קפדנית לעברית עם רקע במכונאות רכב. תפקידך: לבדוק אם טקסט "
    "שיווקי כתוב בעברית תקינה, ברורה והגיונית טכנית — לא לכתוב תוכן חדש.\n\n"
    "החזירי FAIL אם קיים אחד מאלה:\n"
    "1. פועל/דימוי שמייחס לחלק מכני תודעה, כוונה, או חוש שאין לו — למשל חלק "
    "'ממריא' (טיסה), 'הולך לישון', 'שר', 'מרגיש', 'מחליט'. פעלים "
    "שמתארים בלאי/הידרדרות הדרגתית כן תקינים ('שוקע', 'נחלש', 'מתעייף', 'נשחק'). "
    "ניבים סטנדרטיים ונפוצים בעברית מדוברת על רכב תמיד עוברים גם אם הם 'לא "
    "מילולית מדויקים' — למשל 'הבוקר לא עולה' (הרכב לא מתניע), 'המנוע שורף שמן', "
    "'הרכב מושך שמאלה' — אלה ניבים אמיתיים, לא באגים. אל תפסלי ניב עברי מוכר.\n"
    "2. מילה בסוגריים אחרי שם חלק/יצרן שאינה שם מותג רכב אמיתי ומוכר\n"
    "3. שגיאת סמיכות (למשל 'המוט ייצוב' במקום 'מוט הייצוב') — שימו לב: צירוף "
    "ישיר של שם מותג לועזי אחרי שם חלק בלי מילת יחס (כמו 'מצבר Bosch', "
    "'רדיאטור NRF 550201', 'מוט העברה SEAT') הוא סגנון שיווקי תקין ומקובל, לא שגיאה\n"
    "4. מילה שלא קיימת בעברית תקנית או מדוברת\n\n"
    "כשיש ספק בין 'ניב עברי מוכר' לבין 'המצאה' — הטיה לכיוון PASS. תפסלי רק "
    "מקרים ברורים וחד-משמעיים, לא כל ביטוי יצירתי.\n\n"
    "פורמט תשובה חובה — שורה ראשונה PASS או FAIL בלבד, שורה שנייה סיבה קצרה אחת "
    "(אם FAIL, ציטטי את הביטוי הבעייתי המדויק)."
)


def manufacturer_preserved(caption: str, manufacturer: str) -> bool:
    """Deterministic check: if a real manufacturer name was given as grounding,
    it must appear verbatim in the final caption. Zero ambiguity, catches the
    שלדג class of error with certainty — no LLM judgment needed for this part."""
    manufacturer = (manufacturer or "").strip()
    if not manufacturer:
        return True  # nothing to check
    return manufacturer.lower() in (caption or "").lower()


# ---------------------------------------------------------------------------
# Commercial-claim truth guard (added 2026-08-16 — TikTok BREMBO/free-shipping
# incident). generate_post()/execute_campaign() (the SHIRA Campaign content
# path) have NO mechanism today to ground a price, shipping, guarantee, or
# discount claim in real catalog/policy data — unlike _noa_marketing_loop's
# organic posts, which DO inject a verified real_fact and check
# manufacturer_preserved() against it. Any such claim from THIS path is
# therefore a genuine fabrication risk, not a judgment call, so this is
# deterministic keyword/pattern matching (same zero-ambiguity spirit as
# manufacturer_preserved() above) — conservative by design: prefer
# REJECT/FLAG over ASSUME TRUE, per the platform's existing SHIPPING TRUTH /
# VERIFY-DON'T-ASSUME policy (BACKEND_AI_AGENTS.py, TELEGRAM_BOT_POLICY).
# Deliberately scoped to the Campaign content-generation path only — do NOT
# wire this into _noa_marketing_loop, whose posts legitimately cite real
# catalog prices via a real_fact it verifies separately.
# ---------------------------------------------------------------------------
_FREE_SHIPPING_RE = re.compile(
    r"(משלוח\s*חינם|משלוח\s*ללא\s*עלות|free\s*shipping|shipping(?:'s|\s+is)\s*free)",
    re.IGNORECASE,
)
_GUARANTEE_OR_DISCOUNT_RE = re.compile(
    r"(מובטח|guaranteed\s*delivery|מבצע\s*בלעדי|exclusive\s*(deal|offer|partnership)|"
    r"\d+\s*%\s*הנחה|הנחה\s*של\s*\d+\s*%|\d+\s*%\s*off\b)",
    re.IGNORECASE,
)
_PRICE_CLAIM_RE = re.compile(r"(₪\s*\d|\d+\s*₪|\d+\s*ש[\"'׳]?ח|\bILS\b\s*\d|\d+\s*ILS\b)")


def no_fabricated_commercial_claims(text: str) -> Tuple[bool, Optional[str]]:
    """Deterministic, zero-LLM-call guard against the two proven fabrication
    classes: an unverified free-shipping/guarantee/discount claim, or a
    specific price/currency figure the generation path has no way to ground
    in real data. Returns (ok, reason) — reason quotes the exact matched
    phrase so a human reviewer can see precisely what triggered it."""
    t = text or ""
    m = _FREE_SHIPPING_RE.search(t)
    if m:
        return False, f"unverified free-shipping claim: {m.group(0)!r}"
    m = _GUARANTEE_OR_DISCOUNT_RE.search(t)
    if m:
        return False, f"unverified guarantee/discount claim: {m.group(0)!r}"
    m = _PRICE_CLAIM_RE.search(t)
    if m:
        return False, f"unverified price/currency claim: {m.group(0)!r}"
    return True, None


async def check(caption: str) -> Tuple[bool, Optional[str]]:
    """(ok, reason). ok=False only when the judge ran and returned FAIL.
    Fails OPEN on any infra error — never block posting because a judge call
    failed; the caller decides what to do with an unscored post."""
    caption = (caption or "").strip()
    if not caption:
        return True, None
    try:
        from hf_client import hf_text as _hf_text
        verdict = await _hf_text(
            prompt=f"בדקי את הטקסט הבא:\n\n{caption}",
            system=JUDGE_SYSTEM, timeout=30.0, max_tokens=150,
            temperature=0.0, reasoning_effort="low",
        )
        lines = [ln.strip() for ln in (verdict or "").splitlines() if ln.strip()]
        first = (lines[0] if lines else "").upper()
        reason = lines[1] if len(lines) > 1 else ""
        ok = first.startswith("PASS")
        logger.info("coherence_guard: verdict=%s reason=%s", "PASS" if ok else "FAIL", reason[:120])
        return ok, (None if ok else reason)
    except Exception as exc:
        logger.warning("coherence_guard: judge unavailable (%s) — failing OPEN", type(exc).__name__)
        return True, None


__all__ = ["check", "manufacturer_preserved", "no_fabricated_commercial_claims", "JUDGE_SYSTEM"]
