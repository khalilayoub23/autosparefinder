"""
Phase 5E — Facebook Relevance Hardening Regression Test
========================================================
Validates:
  1. Hebrew proclitic normalization — prefixed forms now match base-form keywords
  2. Base-form preservation — existing base-form matches still work
  3. Positive cases — real-world posts from Phase 5 groups score >= 0.2
  4. False-positive guard — irrelevant social posts do NOT score >= 0.2

ZERO Production DB writes.  NO Facebook access.  Pure unit tests.
Run: docker exec autospare_backend python3 /app/devtests/fb_relevance_hardening_regression_test.py
"""

import sys
import types

# ── Bootstrap: make _AUTO_KEYWORDS and helpers importable without the full app ──
# We import group_agent directly; it only needs `re` (stdlib) at module level.
sys.path.insert(0, "/app")

from social.facebook_browser.group_agent import (
    _AUTO_KEYWORDS,
    _HE_PROCLITICS,
    _normalize_hebrew_token,
    _relevance_score,
)

# ============================================================
#  Helpers
# ============================================================

PASS_COUNT = 0
FAIL_COUNT = 0
RESULTS: list[tuple[str, bool, str]] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    global PASS_COUNT, FAIL_COUNT
    if condition:
        PASS_COUNT += 1
        status = "PASS"
    else:
        FAIL_COUNT += 1
        status = "FAIL"
    RESULTS.append((label, condition, detail))
    mark = "✅" if condition else "❌"
    print(f"  {mark}  [{status}] {label}")
    if detail:
        print(f"         {detail}")


# ============================================================
#  Section 1 — Keyword set sanity
# ============================================================
print("\n" + "=" * 68)
print("  SECTION 1: _AUTO_KEYWORDS set composition")
print("=" * 68)

check("keyword count >= 60",
      len(_AUTO_KEYWORDS) >= 60,
      f"actual count = {len(_AUTO_KEYWORDS)}")

# Only unambiguously automotive terms added (generic request/commerce terms EXCLUDED
# to keep false-positive rate low — see Phase 5E forensic report Section 8)
for kw in ["חלקים", "חלקי", "פנס", "פנסים", "בלם",
           "סקודה", "ג'יפ", "פיג'ו", "סיטרואן", "רנו", "טויוטה", "יונדאי", "קיה",
           "skoda", "jeep", "peugeot", "citroen", "renault"]:
    check(f"'{kw}' in _AUTO_KEYWORDS", kw in _AUTO_KEYWORDS)

# Confirm generic terms NOT added (false-positive guard)
for kw_excluded in ["מחפש", "מחפשת", "צריך", "צריכה", "דרוש", "דרושה",
                    "מחיר", "למכירה", "מוכר", "מוכרת"]:
    check(f"generic '{kw_excluded}' NOT in _AUTO_KEYWORDS (false-positive guard)",
          kw_excluded not in _AUTO_KEYWORDS)

# ============================================================
#  Section 2 — Hebrew proclitic normalization
# ============================================================
print("\n" + "=" * 68)
print("  SECTION 2: _normalize_hebrew_token — prefixed forms")
print("=" * 68)

# All 10 prefixed forms from Phase 5D morphology matrix must now resolve to base keyword
morphology_matrix = [
    # (prefixed_token, expected_stem)
    ("לרכב",    "רכב"),
    ("ברכב",    "רכב"),
    ("הרכב",    "רכב"),
    ("ורכב",    "רכב"),
    ("למנוע",   "מנוע"),
    ("המנוע",   "מנוע"),
    ("לבלמים",  "בלמים"),
    ("הבלמים",  "בלמים"),
    ("לגיר",    "גיר"),
    ("לשמן",    "שמן"),
]
for token, expected_stem in morphology_matrix:
    result = _normalize_hebrew_token(token)
    check(f"normalize('{token}') == '{expected_stem}'",
          result == expected_stem,
          f"got '{result}'")

# Prefixed forms of Phase 5E new keywords
for token, expected_stem in [("לחלפים", "חלפים"), ("הפנסים", "פנסים"), ("לבלם", "בלם")]:
    result = _normalize_hebrew_token(token)
    check(f"normalize('{token}') == '{expected_stem}' (new keyword with prefix)",
          result == expected_stem,
          f"got '{result}'")

# A word starting with a proclitic whose stem is NOT a keyword — must not be stripped
r = _normalize_hebrew_token("בית")
check("normalize('בית'): stem 'ית' not in keywords → no strip",
      r == "בית",
      f"got '{r}'")

# ============================================================
#  Section 3 — Base-form preservation (existing keywords unchanged)
# ============================================================
print("\n" + "=" * 68)
print("  SECTION 3: Base-form keywords still match after normalization")
print("=" * 68)

base_forms = [
    "רכב", "מנוע", "בלמים", "חלפים", "גיר", "שמן",
    "מכונית", "רפידות", "מסנן", "צמיג", "גלגל",
    "toyota", "kia", "hyundai", "engine", "brake", "filter",
]
for kw in base_forms:
    result = _normalize_hebrew_token(kw)
    check(f"base '{kw}' → unchanged",
          result == kw,
          f"got '{result}'")

# ============================================================
#  Section 4 — False-positive guard on _normalize_hebrew_token
# ============================================================
print("\n" + "=" * 68)
print("  SECTION 4: Non-automotive tokens NOT incorrectly normalized")
print("=" * 68)

non_automotive_prefixed = [
    # "בית" (house) — ב prefix, stem "ית" is not a keyword
    ("בית",  "בית"),
    # "להוריד" (to download) — ל prefix, stem "הוריד" not a keyword
    ("להוריד", "להוריד"),
    # "מדינה" (country) — מ prefix, stem "דינה" not a keyword
    ("מדינה", "מדינה"),
    # "שלום" (hello) — ש prefix, stem "לום" not a keyword
    ("שלום", "שלום"),
]
for token, expected in non_automotive_prefixed:
    result = _normalize_hebrew_token(token)
    check(f"non-automotive '{token}' NOT stripped",
          result == expected,
          f"got '{result}'")

# ============================================================
#  Section 5 — _relevance_score positive cases (Phase 5 group posts)
# ============================================================
print("\n" + "=" * 68)
print("  SECTION 5: Positive cases — real automotive posts score >= 0.2")
print("=" * 68)

# G1-B: פנסים group — headlight request post (heavily prefixed Hebrew)
G1_B = "מישהו מכיר מקום שמוכר פנסים לרכב? צריך פנס קדמי לסקודה אוקטביה"
s = _relevance_score(G1_B)
check("G1-B (פנסים request with לרכב prefix) >= 0.333",
      s >= 0.333, f"score={s:.3f}")

# G1-C: פנסים group — competitor vendor ad (the Phase 5 actual discovery)
G1_C = "לרכב שלכם הגיעו הפנסים! מגוון רחב של פנסים לכל סוגי הרכבים במחיר מפתיע"
s = _relevance_score(G1_C)
check("G1-C (vendor ad: לרכב פנסים מחיר) >= 0.333",
      s >= 0.333, f"score={s:.3f}")
check("G1-C score improved vs old tokenizer (should hit לרכב + פנסים + מחיר)",
      s >= 0.5, f"score={s:.3f} (expected >= 0.5 with normalization)")

# G2-A: SKODA group — part request
G2_A = "מישהו יודע מאיפה להשיג חלקים לסקודה? מחפש חלק לגיר"
s = _relevance_score(G2_A)
check("G2-A (חלקים סקודה מחפש גיר) >= 0.667",
      s >= 0.667, f"score={s:.3f}")

# G2-B: SKODA group — repair discussion
G2_B = "הביאו את הסקודה למוסך ואמרו לי שצריך להחליף את הבלמים"
s = _relevance_score(G2_B)
check("G2-B (סקודה מוסך בלמים — prefixed) >= 0.333",
      s >= 0.333, f"score={s:.3f}")

# G3-B: JEEP group — oil/engine
G3_B = "חיפשתי שמן מנוע לג'יפ, מישהו ממליץ?"
s = _relevance_score(G3_B)
check("G3-B (שמן מנוע ג'יפ) >= 0.333",
      s >= 0.333, f"score={s:.3f}")

# G4-A: פיג'ו סיטרואן רנו — parts group (hits: חלקי + פיג'ו)
G4_A = "מחפשת חלקי פיג'ו 207, יש מישהו שמוכר?"
s = _relevance_score(G4_A)
check("G4-A (חלקי פיג'ו) >= 0.333",
      s >= 0.333, f"score={s:.3f}")

# G4-D: פיג'ו סיטרואן רנו — brand + part
G4_D = "רנו קליאו עם בעיה במנוע, צריך מסנן שמן"
s = _relevance_score(G4_D)
check("G4-D (רנו מנוע מסנן שמן) >= 0.667",
      s >= 0.667, f"score={s:.3f}")

# G5-A: מוסכניק — mechanic group
G5_A = "שאלה למוסכניק: הרכב עושה רעש מהגיר, מה הסיבה?"
s = _relevance_score(G5_A)
check("G5-A (מוסך הרכב הגיר — prefixed) >= 0.333",
      s >= 0.333, f"score={s:.3f}")

# G5-B: מוסכניק — brake fade question
G5_B = "לרכב שלי יש בעיה בבלמים, הרגשה שהם נשרפים בירידות"
s = _relevance_score(G5_B)
check("G5-B (לרכב בלמים — prefixed) >= 0.333",
      s >= 0.333, f"score={s:.3f}")

# Phase 5 actual discovery (the found post)
PHASE5_DISCOVERY = "לרכב שלכם הגיעו הפנסים"
s = _relevance_score(PHASE5_DISCOVERY)
check("Phase5 discovery ('לרכב הפנסים') >= 0.333",
      s >= 0.333, f"score={s:.3f}")

# ============================================================
#  Section 6 — False-positive guard on _relevance_score
# ============================================================
print("\n" + "=" * 68)
print("  SECTION 6: False-positive guard — irrelevant posts score < 0.2")
print("=" * 68)

irrelevant_posts = [
    ("Generic social: good morning",
     "בוקר טוב לכולם! מי רוצה קפה?"),
    ("Food post (no automotive keywords)",
     "מסעדה מצוינת בתל אביב למשפחה, יש המלצות?"),
    ("Real estate (no automotive keywords)",
     "דירה באזור, כמה זה שווה היום?"),
    ("Job listing (no automotive keywords)",
     "עובד לחנות מזון, שלחו קורות חיים"),
    ("Tourism (no automotive keywords)",
     "המלצה לטיול בצפון? יש שם מקומות נפלאים"),
    ("Generic shopping (no automotive keywords)",
     "ספה משומשת במצב מצוין, כמה תשלמו?"),
]
for label, text in irrelevant_posts:
    s = _relevance_score(text)
    check(f"Irrelevant: {label[:45]} < 0.2",
          s < 0.2, f"score={s:.3f}")

# ============================================================
#  Summary
# ============================================================
print("\n" + "=" * 68)
print("  REGRESSION CASE SUMMARY")
print("=" * 68)
total = PASS_COUNT + FAIL_COUNT
print(f"  Total: {total}  |  PASS: {PASS_COUNT}  |  FAIL: {FAIL_COUNT}")
print()
if FAIL_COUNT == 0:
    print("  ✅  ALL TESTS PASSED")
    print()
    print("  Phase 5E offline regression: PASS")
else:
    print("  ❌  SOME TESTS FAILED")
    print()
    for label, ok, detail in RESULTS:
        if not ok:
            print(f"     FAIL: {label}")
            if detail:
                print(f"           {detail}")
print("=" * 68)

sys.exit(1 if FAIL_COUNT else 0)
