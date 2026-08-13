"""
social/hebrew_style.py — deterministic Hebrew LANGUAGE gate for generated copy.

WHY THIS EXISTS (2026-08-13, owner asked for the 4th time: "recheck the NOA
language style and the wrong sentence build — I want a root fix, I want the
agents to act like humans").

The previous three attempts all kept the same architecture:

    generate  ->  REWRITE the text with regexes  ->  publish

and each round narrowed one regex. That architecture IS the root cause. A regex
that swaps a phrase inside a sentence cannot know the grammar around it, so every
"compliance fix" is a coin flip on whether the sentence survives. Measured on
hand-written, CORRECT Hebrew before this change:

    "מצאנו את הרפידות הכי זול בארץ"  -> "מצאנו את הרפידות מחירים תחרותיים"
    "פילטר מקורי ה-Bosch"            -> "פילטר מקורי ה Bosch"      (stranded letter)
    "...להזמין לבד?"                 -> "...להזמין לבד? אנחנו מוכרים חלקי חילוף בלבד."

None of those were model errors. We wrote them.

THE RULE THIS MODULE ENFORCES
    A post-processor may VALIDATE, and it may add or drop a WHOLE LINE.
    It may never rewrite words inside a sentence.
    A draft that violates policy is REGENERATED (or held for the owner) —
    never patched.

So this module only ever reports. It is the language counterpart to
social/post_guard.py: post_guard judges whether the copy is about the right part
(MEANING), this judges whether it reads like a person wrote it (FORM). Neither
one edits anything.

CALIBRATION RULE (Mistake Log, 2026-07-20 — a quality heuristic that has never
been run against real CORRECT input is not allowed to reject anything). Every
check here is proven in devtests/noa_language_test.py against hand-written
correct Hebrew posts BEFORE it is allowed to block. That test is what caught the
`מק"ט` case: the pre-existing lone-Hebrew-letter check treated the gershayim in
`מק"ט` / `ק"מ` / `ש"ח` as a word boundary, so the single most common term in an
auto-parts post made EVERY such post "low quality" and sent it down the repair
path that stapled boilerplate onto it.

Data Modified: none (pure evaluation)
Last Updated:  2026-08-13
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional

HE = r"֐-׿"
AR = r"؀-ۿ"

# One-letter proclitics that Hebrew glues onto the next word (ה ו ב ל מ ש כ ד).
# They are the ONLY letters allowed to stand next to a maqaf before a Latin token
# or a number — everywhere else a one-letter Hebrew word is a garble.
PROCLITICS = "מבלהושכד"

# Sentence-final punctuation, used to split a body into sentences.
_SENT_SPLIT = re.compile(r"(?<=[.!?׃])\s+")

# A Hebrew abbreviation written with gershayim/geresh: מק"ט, ק"מ, ש"ח, מ"מ, ד"ר,
# ג'קט. The quote is NOT a word boundary here — treating it as one is what made
# every post mentioning a catalogue number look garbled.
_ABBREV = re.compile(rf"[{HE}]{{1,4}}[\"״'׳][{HE}]{{1,3}}")

# A proclitic bound by maqaf to a number or a Latin token: מ-198, ב-2020, ה-Bosch.
# Correct Hebrew, and the exact form a selling post is built on.
_MAQAF_BOUND = re.compile(rf"[{PROCLITICS}][-‐-―](?=[0-9A-Za-z])")

# The artifact our own normalizer used to create by deleting that maqaf.
_BROKEN_MAQAF = re.compile(rf"(?<![{HE}])([{PROCLITICS}])\s+(?=[A-Za-z0-9])")

_REPEATED_WORD = re.compile(rf"\b([{HE}A-Za-z]{{2,}})\s+\1\b")

# A sentence that ends on a conjunction/preposition is a truncated sentence.
_DANGLING = re.compile(
    rf"(?:^|\s)(ו|ב|ל|מ|ה|ש|כ|של|את|עם|על|כי|אבל|או|גם|כדי|אחרי|לפני|בין)\s*$"
)

# Sentence punctuation with NO space before the next word — the signature of text
# that was concatenated by a machine rather than written by a person.
_GLUED = re.compile(rf"[.!?](?=[{HE}]{{2,}})")

# Openers the NOA system prompt already bans as worn out. Detected, never rewritten.
CLICHE_OPENERS = (
    "מחפשים חלקי חילוף",
    "מחפשים חלק לרכב",
    "ידעת ש",
    "ידעתם ש",
    "למה לשלם יותר",
    "האם ידעת",
)

# Our OWN canned sentences. If one of these appears in a draft it means a
# fallback/repair path wrote copy instead of NOA — which is precisely what the
# owner keeps recognising as "a bot". Their presence is an error, not a warning.
BOILERPLATE = (
    "הפלטפורמה שלנו מאתרת חלקים לפי מספר רישוי, מאפשרת להשוות אפשרויות ומחירים במקום אחד",
    "וחוסכת חיפוש מיותר והתעסקות טכנית",
    "אנחנו משווקים חלקי חילוף בעזרת AI בלבד",
    "מחפשים חלק לרכב בלי לרוץ בין מוסכים? מזינים מספר רישוי",
    "אנחנו מאתרים חלקים מהר, עוזרים בהתאמה לפי רכב, ומרכזים אפשרויות במקום אחד",
    "שלחו דגם, שנה ומנוע ונחזיר התאמה מהירה ומדויקת",
)

MAX_SENTENCE_WORDS = 32

# Whole lines the SYSTEM appends for compliance (never written by NOA, never spliced
# into one of her sentences). They are formatting, like the hashtag and link lines, so
# they are excluded from the prose that gets linted — otherwise the language gate would
# be judging our own legal boilerplate as if it were the copy.
COMPLIANCE_LINES = (
    "המחירים, המבצעים והזמינות כפופים לתנאי האתר.",
)


@dataclass(frozen=True)
class Issue:
    code: str
    severity: str          # "error" blocks publishing; "warn" only feeds the rewrite prompt
    message: str           # Hebrew — goes straight into the repair prompt
    snippet: str = ""

    def __str__(self) -> str:
        return f"{self.message}" + (f" — «{self.snippet}»" if self.snippet else "")


def _mask_protected(text: str) -> str:
    """Blank out the spans that are legitimately allowed to contain a lone Hebrew
    letter, so the stranded-letter check cannot fire on them. Length is preserved
    so reported offsets stay meaningful."""
    def blank(m: re.Match) -> str:
        return "·" * len(m.group(0))

    masked = _ABBREV.sub(blank, text)
    masked = _MAQAF_BOUND.sub(blank, masked)
    return masked


def body_of(text: str) -> str:
    """The prose part of a post: hashtag lines, link lines and QR/emoji CTA lines
    are formatting, not sentences, and must not be linted as such."""
    lines = []
    for ln in (text or "").splitlines():
        s = ln.strip()
        if not s:
            continue
        if s.startswith("#"):
            continue
        if s.startswith(("🌐", "📲", "✈", "💬", "📘", "📸")):
            continue
        if re.fullmatch(r"https?://\S+", s):
            continue
        if s in COMPLIANCE_LINES:
            continue
        lines.append(s)
    return "\n".join(lines).strip()


def lint(text: str, *, platform: str = "") -> List[Issue]:
    """Report everything wrong with the LANGUAGE of a draft. Never modifies it."""
    issues: List[Issue] = []
    raw = (text or "").strip()
    if not raw:
        return [Issue("empty", "error", "הפוסט ריק")]

    body = body_of(raw)
    if not body:
        return [Issue("empty_body", "error", "אין גוף טקסט — רק האשטאגים או לינק")]

    masked = _mask_protected(body)

    # 1. A one-letter Hebrew word that is not a maqaf-bound proclitic and not part
    #    of an abbreviation. This is a genuine garble.
    for m in re.finditer(rf"(?<![{HE}\w])([{HE}])(?![{HE}\w])", masked):
        start = max(0, m.start() - 18)
        issues.append(Issue(
            "stranded_letter", "error",
            "יש אות עברית בודדת שעומדת כמילה — משפט שבור",
            body[start:m.end() + 18].strip(),
        ))
        break

    # 2. Proclitic separated from a Latin/number token by a space instead of a maqaf
    #    ("ה Bosch" instead of "ה-Bosch").
    m = _BROKEN_MAQAF.search(body)
    if m:
        issues.append(Issue(
            "broken_maqaf", "error",
            "חסר מקף אחרי אות שימוש לפני מילה לועזית או מספר (צריך ה-Bosch, לא ה Bosch)",
            body[max(0, m.start() - 10):m.end() + 14].strip(),
        ))

    # 3. Doubled word.
    m = _REPEATED_WORD.search(body)
    if m:
        issues.append(Issue("repeated_word", "error", "מילה כפולה ברצף", m.group(0)))

    # 4. Sentence punctuation glued to the next word.
    m = _GLUED.search(body)
    if m:
        issues.append(Issue(
            "glued_sentence", "error", "משפטים מחוברים בלי רווח אחרי סימן פיסוק",
            body[max(0, m.start() - 12):m.end() + 12].strip(),
        ))

    # 5. A line that ends on a conjunction/preposition — a cut-off sentence.
    for ln in body.splitlines():
        if _DANGLING.search(ln):
            issues.append(Issue("dangling_connective", "error",
                                "שורה נגמרת במילת חיבור — המשפט קטוע", ln.strip()[-40:]))
            break

    # 6. Our own canned copy leaked into the draft.
    for phrase in BOILERPLATE:
        if phrase in raw:
            issues.append(Issue(
                "boilerplate", "error",
                "יש משפט תבניתי של המערכת בתוך הפוסט — צריך ניסוח מקורי", phrase[:48]))
            break

    # 7. The same sentence twice.
    sentences = [s.strip() for s in _SENT_SPLIT.split(body.replace("\n", " ")) if len(s.strip()) > 12]
    seen = set()
    for s in sentences:
        key = re.sub(r"\s+", " ", s).strip(" .!?")
        if key in seen:
            issues.append(Issue("duplicate_sentence", "error", "אותו משפט מופיע פעמיים", s[:48]))
            break
        seen.add(key)

    # 8. Unbalanced brackets / quotes (excluding gershayim inside abbreviations).
    if body.count("(") != body.count(")"):
        issues.append(Issue("unbalanced_parens", "error", "סוגריים לא סגורים"))

    # ---- advisory (never blocks; feeds the rewrite prompt) -------------------

    for s in sentences:
        if len(s.split()) > MAX_SENTENCE_WORDS:
            issues.append(Issue("run_on", "warn",
                                f"משפט ארוך מדי ({len(s.split())} מילים) — לפצל לשניים", s[:48]))
            break

    low = body.lower()
    for opener in CLICHE_OPENERS:
        if low.lstrip().startswith(opener.lower()):
            issues.append(Issue("cliche_opener", "warn",
                                "פתיחה שחוקה — צריך פתיח מקורי", opener))
            break

    he_chars = len(re.findall(rf"[{HE}]", body))
    latin_chars = len(re.findall(r"[A-Za-z]", body))
    if he_chars and latin_chars > he_chars * 0.6:
        issues.append(Issue("latin_heavy", "warn", "יותר מדי אנגלית בפוסט עברי"))

    if not re.search(r"[.!?]", body):
        issues.append(Issue("no_sentence_end", "warn", "אין סימני סוף משפט — הטקסט נקרא כרשימה"))

    return issues


def errors(issues: List[Issue]) -> List[Issue]:
    return [i for i in issues if i.severity == "error"]


def is_publishable(text: str, *, platform: str = "") -> bool:
    """True when nothing BLOCKING is wrong with the language."""
    return not errors(lint(text, platform=platform))


def feedback_prompt(issues: List[Issue]) -> str:
    """Turn the findings into instructions the model can act on. This is how a
    problem gets FIXED now — we tell the writer what is wrong and let her rewrite
    the sentence, instead of us splicing words into it."""
    if not issues:
        return ""
    lines = ["הטקסט הקודם נפסל מסיבות ניסוח. תקני וכתבי מחדש את הפוסט כולו:"]
    for i in issues:
        lines.append(f"• {i}")
    lines.append("שמרי על אותם עובדות ומחיר, אל תוסיפי משפטי גילוי נאות משלך, "
                 "וכתבי עברית תקנית וזורמת כמו בן אדם.")
    return "\n".join(lines)
