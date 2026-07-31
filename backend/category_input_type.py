"""
category_input_type.py — classify WHAT KIND of text a part name is.

Why this exists (owner insight, 2026-07-28):
    Evaluating the embedding model by LANGUAGE alone conflated two different
    failures. A part NUMBER or a SIZE CODE scoring badly is not a language
    weakness — it is a semantic limitation: embeddings encode meaning, and
    identifiers, dimensions and bare brand names carry little or none. Splitting
    by language made those look like "Hebrew is weak" and pointed at the wrong
    fix ("improve Hebrew coverage") instead of the right one ("route identifiers
    away from the model entirely").

MEASURED PRECISION BY TYPE (11,110 ground-truth parts, 2026-07-28):

    input type                      n     fire@.75  prec@.75   fire@.85  prec@.85
    english single-word           267        94%       99%        87%      100%
    english short descriptive   3,603        83%       94%        49%       96%
    english long descriptive    6,157        16%       85%         2%       93%
    hebrew descriptive            788        64%       79%        39%       85%
    size/spec format              217         3%      100%         0%        —
    part number / code             18        17%      100%         0%        —
    supersession/placeholder       59         5%       33%         0%        —

Three findings that language-splitting had hidden:
  1. SHORT names are the model's BEST case (99-100%), not its worst. Long
     descriptive names are the weak English case (85%) — more words means more
     competing concepts for a nearest-exemplar match.
  2. Codes and sizes were never the real danger: they barely fire at all (3-17%).
     The earlier "it confidently mis-files codes" observation came from a 0.45
     threshold; at 0.75 that failure is largely self-limiting.
  3. The genuinely dangerous type is SUPERSESSION/PLACEHOLDER — 33% precision and
     it DOES fire. It needs a hard pre-filter, not a threshold.

Data Modified: none (pure classification)
Last Updated:  2026-07-28
"""
from __future__ import annotations

import re
from typing import Optional

# ── Type constants ────────────────────────────────────────────────────────────
EN_SINGLE = "english single-word"
EN_SHORT = "english short descriptive"
EN_LONG = "english long descriptive"
HE_DESC = "hebrew descriptive"
HE_SINGLE = "hebrew single-word"
AR_DESC = "arabic descriptive"
AR_SINGLE = "arabic single-word"
SIZE_SPEC = "size/spec format"
PART_CODE = "part number / code"
SUPERSESSION = "supersession/placeholder"
BRAND_ONLY = "brand-only"
EMPTY = "empty"

_HE = re.compile(r"[֐-׿]")
_AR = re.compile(r"[؀-ۿ]")
_WORD = re.compile(r"[A-Za-z֐-ۿ]{2,}")
# Tyre sizes, metric threads, millimetre dims, fractional inches
_SIZE = re.compile(
    r"\b(p?\d{3}\s*[/ ]\s*\d{2}\s*[rz]{1,2}\s*\d{2}"
    r"|\d{2}x\d[.\d]*|\d+\s*mm\b|\d+/\d+\"|m\d{1,2}\b)", re.I)
# An identifier-ish token: contains a digit and is long, or is pure digits
_CODE_TOK = re.compile(r"^(?=.*\d)[A-Za-z0-9][A-Za-z0-9\-./]{4,}$")
# Rows that are references to another part, or a literal placeholder name
_SUPERSEDE = re.compile(r"^\s*(s/s to\b|supersed|replaced\s+by|oem\s+part\b)", re.I)


def classify_input_type(text: str, is_blocked=None) -> str:
    """
    Return the input TYPE of a part name.

    `is_blocked` is an optional predicate (category_learning.is_blocked) used to
    detect brand-only names. Passed in rather than imported so this module stays
    dependency-free and safe to import from anywhere.
    """
    s = (text or "").strip()
    if not s:
        return EMPTY
    low = s.lower()

    # Order matters: a placeholder that also contains a code is a placeholder.
    if _SUPERSEDE.match(low):
        return SUPERSESSION
    if _SIZE.search(low):
        return SIZE_SPEC

    words = _WORD.findall(s)
    toks = [t for t in re.split(r"[\s,]+", s) if t]
    if not words or all(_CODE_TOK.match(t) or t.isdigit() for t in toks):
        return PART_CODE

    if is_blocked is not None:
        try:
            if all(is_blocked(w.lower()) for w in words):
                return BRAND_ONLY
        except Exception:
            pass

    if _HE.search(s):
        return HE_DESC if len(words) >= 2 else HE_SINGLE
    if _AR.search(s):
        return AR_DESC if len(words) >= 2 else AR_SINGLE
    if len(words) == 1:
        return EN_SINGLE
    if len(words) >= 4:
        return EN_LONG
    return EN_SHORT


# ── PER-TYPE POLICY ───────────────────────────────────────────────────────────
# Derived from the measured table above, NOT from intuition.
#
#   may_propose  — may this type feed the keyword-learning suggestion path?
#   min_score    — cosine floor for that type (per-type, because a code at 0.82
#                  and a descriptive name at 0.82 are not equally trustworthy).
#   autowrite_ok — is this type ELIGIBLE for Phase-2 auto-write? Eligibility is
#                  necessary but NOT sufficient: the runtime gate additionally
#                  requires recorded approval history (see embed_policy.py).
#
# PHASE 1 (now): nothing auto-writes. Every type that may propose does so through
# blocklist -> consensus -> OWNER APPROVAL -> bulk apply.
# PHASE 2 (later): a type may be switched to auto-write only once its recorded
# owner-approval rate clears the bar. That evidence is collected from today.
POLICY = {
    EN_SINGLE:    {"may_propose": True,  "min_score": 0.85, "autowrite_ok": True},
    EN_SHORT:     {"may_propose": True,  "min_score": 0.85, "autowrite_ok": True},
    EN_LONG:      {"may_propose": True,  "min_score": 0.85, "autowrite_ok": True},
    HE_DESC:      {"may_propose": True,  "min_score": 0.90, "autowrite_ok": False},
    HE_SINGLE:    {"may_propose": True,  "min_score": 0.90, "autowrite_ok": False},
    AR_DESC:      {"may_propose": True,  "min_score": 0.90, "autowrite_ok": False},
    AR_SINGLE:    {"may_propose": True,  "min_score": 0.90, "autowrite_ok": False},
    # Handled by _TYRE_RE and dedicated parsers — a model has nothing to add and
    # these barely fire anyway (3-17%).
    SIZE_SPEC:    {"may_propose": False, "min_score": 1.01, "autowrite_ok": False},
    PART_CODE:    {"may_propose": False, "min_score": 1.01, "autowrite_ok": False},
    # 33% precision AND it fires. Hard-blocked, not threshold-gated.
    SUPERSESSION: {"may_propose": False, "min_score": 1.01, "autowrite_ok": False},
    BRAND_ONLY:   {"may_propose": False, "min_score": 1.01, "autowrite_ok": False},
    EMPTY:        {"may_propose": False, "min_score": 1.01, "autowrite_ok": False},
}


def policy_for(input_type: str) -> dict:
    return POLICY.get(input_type, POLICY[EMPTY])


def may_propose(input_type: str) -> bool:
    return bool(policy_for(input_type)["may_propose"])


def min_score_for(input_type: str) -> float:
    return float(policy_for(input_type)["min_score"])


def autowrite_eligible(input_type: str) -> bool:
    """Eligible for Phase 2. Does NOT mean enabled — see embed_policy.py."""
    return bool(policy_for(input_type)["autowrite_ok"])
