"""
Script: reply_language.py
Purpose: The single rule for WHICH language a customer-facing reply must be written in, and
         the check that a generated reply actually is in that language.

Why this exists (2026-10-27): customers writing English or Arabic got Hebrew replies. The
language requirement was lost on several independent paths, none of them the model alone:
  * agent prompts disagreed ("Default language is Hebrew", "ALWAYS respond in Hebrew",
    "NEVER insert English words") with the shared channel policy ("match the customer");
  * the shared policy keyed the language on the customer's FIRST message, and the brain's
    detected language (context_data["preferred_lang"]) never reached the agents;
  * the customer-reply editor (_format_response_for_customer) had no English rule and read the
    PREVIOUS customer message, so it rewrote correct English replies into Hebrew;
  * fixed fallback texts (timeouts, offline replies, canned "frustrated" answers) were Hebrew.
Every customer-facing path now asks THIS module for the target language and validates against it.

Process:
  1. detect_language(text)        -> "he" | "ar" | "en" | None (None = no language signal:
                                     only digits / part numbers / codes / emoji / links)
  2. resolve_reply_language(...)  -> the current message's language; if it has no signal, the
                                     conversation's preferred language; else the platform default.
  3. language_directive(lang)     -> the authoritative instruction appended LAST to the system prompt.
  4. reply_matches_language(...)  -> validation of the full reply (not the first sentence).
  5. REPLY_LANGUAGE context var   -> set once per customer turn by the brain so every agent call
                                     in that turn (including nested ones) uses the same target.

Data Imported/Modified: none (pure functions + a context variable).
Data Sources: none.
Missing Data Delegation: n/a.
Last Updated: 2026-10-27
"""
from __future__ import annotations

import contextvars
import re
from typing import Dict, Optional

SUPPORTED = ("he", "ar", "en")
DEFAULT_LANGUAGE = "he"          # Israeli platform; used ONLY when nothing else gives a signal

# Set by the brain for the duration of one customer turn. None = not a customer turn
# (background generation, owner console, NOA posts) -> no enforcement.
REPLY_LANGUAGE: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("reply_language", default=None)

_RE_AR = re.compile(r"[؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿]")
_RE_HE = re.compile(r"[֐-׿יִ-ﭏ]")
_RE_LATIN = re.compile(r"[A-Za-z]")
# Tokens that carry no language: anything with a digit (OEM numbers, plates, prices, years),
# links, e-mail addresses, @handles, #tags.
_RE_NEUTRAL_TOKEN = re.compile(r"\d|https?://|www\.|@|^#")

NAMES = {"he": "Hebrew (עברית)", "ar": "Arabic (العربية)", "en": "English"}


def _letter_counts(text: str) -> Dict[str, int]:
    counts = {"he": 0, "ar": 0, "en": 0}
    for tok in re.findall(r"\S+", text or ""):
        if _RE_NEUTRAL_TOKEN.search(tok):
            continue
        counts["ar"] += len(_RE_AR.findall(tok))
        counts["he"] += len(_RE_HE.findall(tok))
        counts["en"] += len(_RE_LATIN.findall(tok))
    return counts


def detect_language(text: str) -> Optional[str]:
    """Language of a customer message, or None when it carries no language signal.

    Policy for mixed messages:
      * Arabic or Hebrew letters present -> the right-to-left script with more letters wins.
        Latin words inside a Hebrew/Arabic message are brand names / part terms
        ("יש לכם brake pads ל-Mazda?") and do not make it English.
      * Only Latin letters -> English, but a lone short Latin token (an OEM-like code such as
        "OK", "VW", "ABS") is too weak a signal -> None.
    """
    c = _letter_counts(text)
    if c["ar"] or c["he"]:
        return "ar" if c["ar"] >= c["he"] else "he"
    if c["en"] >= 3 and len(re.findall(r"[A-Za-z]{2,}", text or "")) >= 1:
        words = [w for w in re.findall(r"[A-Za-z]+", text or "") if len(w) >= 2]
        # one short all-caps token (VW / ABS / OEM) is a code, not a sentence
        if len(words) == 1 and words[0].isupper() and len(words[0]) <= 4:
            return None
        return "en"
    return None


def normalize(lang: Optional[str]) -> Optional[str]:
    l = (lang or "").strip().lower()
    return l if l in SUPPORTED else None


def resolve_reply_language(current_message: str, preferred: Optional[str] = None,
                           default: str = DEFAULT_LANGUAGE) -> str:
    """The language the reply MUST be in.

    1. the customer's CURRENT message (switching language mid-conversation is honoured);
    2. if it has no language signal (a plate, an OEM number, "1"), the conversation's
       preferred language (the last language the customer actually wrote in);
    3. the platform default.
    Assistant replies, tool results and catalog data are never inputs here.
    """
    return detect_language(current_message) or normalize(preferred) or default


def reply_language_shares(reply: str) -> Dict[str, float]:
    c = _letter_counts(reply)
    total = sum(c.values()) or 1
    return {k: round(v / total, 3) for k, v in c.items()}


def detected_reply_language(reply: str) -> Optional[str]:
    c = _letter_counts(reply)
    if not any(c.values()):
        return None
    return max(c, key=lambda k: c[k])


# The exact header of the directive block. hf_client keys its truncation on it.
DIRECTIVE_HEADER = "[REPLY LANGUAGE - FINAL AND OVERRIDING]"


def reply_matches_language(reply: str, lang: str) -> bool:
    """True when the WHOLE reply is in `lang`.

    Always wrong: a token mixing Hebrew and Arabic letters ("ובمناسبة").
    he / ar : the target script is present and at most ONE word of the other right-to-left script
              (a quoted catalog name). Latin words are allowed (Toyota, OEM, Bosch).
    en      : Latin letters present and at most ONE Hebrew/Arabic word.
    A reply with no letters at all (only numbers/links) is accepted.
    """
    lang = normalize(lang) or DEFAULT_LANGUAGE
    if not (reply or "").strip():
        return False                 # an empty reply is never a valid customer answer
    c = _letter_counts(reply)
    if not any(c.values()):
        return True
    words = [t for t in re.findall(r"\S+", reply or "") if not _RE_NEUTRAL_TOKEN.search(t)]
    # A token mixing Hebrew and Arabic letters ("ובمناسبة") is always a broken reply.
    if any(_RE_HE.search(w) and _RE_AR.search(w) for w in words):
        return False
    if lang == "en":
        rtl = c["he"] + c["ar"]
        rtl_words = sum(1 for w in words if _RE_HE.search(w) or _RE_AR.search(w))
        return c["en"] > 0 and rtl_words < 2
    other = "ar" if lang == "he" else "he"
    other_re = _RE_AR if other == "ar" else _RE_HE
    if c[lang] == 0:
        return False
    # One foreign word (a quoted catalog name) is tolerated; two or more is a mixed reply.
    other_words = sum(1 for w in words if other_re.search(w))
    return other_words < 2


def language_directive(lang: str) -> str:
    """Authoritative reply-language instruction. Appended LAST to the system prompt so it
    overrides any older/conflicting wording earlier in a prompt."""
    lang = normalize(lang) or DEFAULT_LANGUAGE
    name = NAMES[lang]
    return (
        f"\n\n{DIRECTIVE_HEADER}\n"
        f"Write your ENTIRE reply in {name}. This is the customer's language for this message.\n"
        "- This rule overrides any other language instruction above (including any 'default "
        "language' or 'always respond in Hebrew' wording).\n"
        "- The language of earlier messages in the conversation, of catalog data, tool results "
        "or internal notes does NOT change the reply language.\n"
        "- Do not mix languages. Part numbers, OEM codes, brand and model names, prices and "
        "links stay exactly as written.\n"
        "- Translate any fixed labels or status names into the reply language."
    )


def correction_note(lang: str) -> str:
    """Appended to the prompt for the single bounded retry after a wrong-language reply."""
    name = NAMES[normalize(lang) or DEFAULT_LANGUAGE]
    return (
        f"\n\n[SYSTEM NOTE] Your previous draft was not written in {name}. "
        f"Answer the customer's last message again, fully in {name}, with the same facts."
    )


def pick(texts: Dict[str, str], lang: Optional[str]) -> str:
    """Choose the localized variant of a fixed text (falls back to the default language)."""
    lang = normalize(lang) or DEFAULT_LANGUAGE
    return texts.get(lang) or texts[DEFAULT_LANGUAGE]


def current_target(fallback_message: str = "") -> Optional[str]:
    """The turn's target language if the brain set one, else derived from the message."""
    return normalize(REPLY_LANGUAGE.get()) or (detect_language(fallback_message) if fallback_message else None)
