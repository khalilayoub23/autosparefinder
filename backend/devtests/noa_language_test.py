# -*- coding: utf-8 -*-
"""
devtests/noa_language_test.py — proves the NOA language root-fix (2026-08-13).

Three things are asserted, in this order, because the order is the point:

  PART A — CALIBRATION FIRST. The linter is run over hand-written, CORRECT Hebrew
           posts (including the forms that actually broke it before: the maqaf
           price form `מ-198`, the gershayim abbreviations `מק"ט` / `ק"מ` / `ש"ח`,
           Latin brand names, and legitimate pain copy about garages). A checker
           that has not been proven silent on good input is not allowed to reject
           anything — Mistake Log, 2026-07-20.

  PART B — DETECTION. Each broken construct the owner has been seeing is caught,
           including the three defects our OWN post-processor used to create.

  PART C — NON-MUTATION. The real publishing chain (`_finalize_noa_post`) is run
           over those same correct posts and must return the body byte-for-byte
           unchanged. This is the actual root fix: the pipeline no longer edits
           sentences, so it can no longer break them.

Run: python3 /app/devtests/noa_language_test.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from social import hebrew_style as HS  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        FAILURES.append(name)


# ---------------------------------------------------------------- PART A ----
# Correct, human Hebrew. The linter must find ZERO blocking issues in all of it.
GOOD_POSTS = {
    "price + maqaf form": (
        "הרעש הזה בבלמים? הוא לא הולך לעבור לבד.\n"
        "רפידות קדמיות לקורולה 2017 מתחילות אצלנו מ-198 ₪, אחרי השוואה בין כמה ספקים.\n"
        "מזינים מספר רישוי באתר ורואים בדיוק מה מתאים לרכב שלך.\n"
        "מתי הסתכלת אחרונה על עובי הרפידות? 🚗\n"
        "#חלקיחילוף #רכב"
    ),
    "gershayim abbreviations": (
        "המק\"ט השתנה ב-2020 וכולם מתבלבלים.\n"
        "פילטר שמן מקורי ה-Bosch לגולף 2019 — 12 אלף ק\"מ ומחליפים, לא יותר.\n"
        "החל מ-89 ש\"ח, ורואים את ההתאמה לפי מספר רישוי.\n"
        "איזה חלק אתם הכי מפחדים להזמין לבד?\n"
        "#חלפים"
    ),
    "pain copy about garages": (
        "בלי לרוץ בין מוסכים ובלי לשמוע שלוש דעות על אותה תקלה.\n"
        "בולם זעזועים אחורי למאזדה 3 — מזינים לוחית ורואים מה מתאים, מ-340 ₪.\n"
        "מה הרעש הכי מוזר ששמעת מהרכב שלך?\n"
        "#רכב"
    ),
    "short punchy post": (
        "מצתים ישנים שורפים לך דלק בשקט.\n"
        "לקיה ספורטאז' 2018 זה החלק שהכי משתלם להקדים. מ-64 ₪ למצת.\n"
        "מספר רישוי באתר, ואתם רואים מה מתאים.\n"
        "#רכב"
    ),
    "with parentheses and slash": (
        "דיסק בלם קדמי (סט של שניים) לאוקטביה 2016.\n"
        "מזינים לוחית באתר ומשווים בין הספקים במקום אחד — מ-410 ₪.\n"
        "מתי החלפתם דיסקים בפעם האחרונה?\n"
        "#בלמים"
    ),
}

print("PART A — calibration on correct Hebrew (must be silent)")
for name, post in GOOD_POSTS.items():
    errs = HS.errors(HS.lint(post))
    check(f"no false positive: {name}", not errs, f"-> {[str(e) for e in errs]}")


# ---------------------------------------------------------------- PART B ----
BROKEN = {
    "stranded_letter": "הפילטר ה מקורי לגולף עולה 89 ₪ באתר שלנו היום.",
    "broken_maqaf": "פילטר שמן מקורי ה Bosch לגולף 2019 עולה 89 ₪ באתר.",
    "repeated_word": "רפידות קדמיות לקורולה עולות את את המחיר הזה באתר שלנו.",
    "glued_sentence": "איזה חלק אתם מפחדים להזמין לבד?אנחנו מוכרים חלקי חילוף בלבד.",
    "dangling_connective": "רפידות קדמיות לקורולה 2017 מתחילות מ-198 ₪ אחרי השוואה בין\nמזינים לוחית באתר.",
    "boilerplate": (
        "הרעש בבלמים לא עובר לבד.\n"
        "הפלטפורמה שלנו מאתרת חלקים לפי מספר רישוי, מאפשרת להשוות אפשרויות ומחירים במקום אחד, "
        "וחוסכת חיפוש מיותר והתעסקות טכנית עד הרכישה."
    ),
    "duplicate_sentence": (
        "מזינים מספר רישוי באתר ורואים מה מתאים. רפידות לקורולה מ-198 ₪. "
        "מזינים מספר רישוי באתר ורואים מה מתאים."
    ),
}

print("\nPART B — detection of the broken constructs")
for code, post in BROKEN.items():
    codes = [i.code for i in HS.errors(HS.lint(post))]
    check(f"caught {code}", code in codes, f"-> got {codes}")

# The exact string the owner saw published (the real 2026-07-28 leak) must be rejected.
leak = "5 letters? Hebrew letters: ה (1), ר (2), ע (3), ד (4) maybe 4 letters?"
check("rejects the published reasoning leak", not HS.is_publishable(leak))

# The reasoning stripper DELETES text, so it needs the same calibration as the linter.
# Its "Hebrew letter followed by (n)" tell used to match an ordinary quantity, so
# "רפידות קדמיות (2)" made it delete the whole priced sentence.
print("\nPART B2 — the reasoning stripper deletes only reasoning")
try:
    from BACKEND_AI_AGENTS import SocialMediaManagerAgent as _NOA_B2

    quantity = ("רפידות קדמיות (2) לקורולה 2017 — מ-198 ₪ באתר.\n"
                "מזינים מספר רישוי ורואים מה מתאים.")
    check("keeps a sentence with a parenthesised quantity",
          _NOA_B2._extract_post_from_reasoning(quantity) == quantity,
          f"-> {_NOA_B2._extract_post_from_reasoning(quantity)!r}")

    counting = "ה (1), ר (2), ע (3) — counting letters. מזינים מספר רישוי ורואים מה מתאים."
    check("still strips real character-counting",
          _NOA_B2._extract_post_from_reasoning(counting) != counting)
except ImportError:
    print("  SKIP  (agents module unavailable)")


# ---------------------------------------------------------------- PART C ----
print("\nPART C — the publishing chain must not modify correct copy")
try:
    from BACKEND_AI_AGENTS import SocialMediaManagerAgent as NOA

    def body_only(t):
        return re.sub(r"\s+", " ", HS.body_of(t)).strip()

    for name, post in GOOD_POSTS.items():
        for platforms in (["facebook", "instagram"], ["tiktok"], ["x", "telegram"]):
            out = NOA._finalize_noa_post(post, platforms=platforms)
            check(
                f"body preserved: {name} / {platforms[0]}",
                body_only(out) == body_only(post),
                f"\n      in : {body_only(post)}\n      out: {body_only(out)}",
            )

    # A draft the model got wrong must come back EMPTY (caller regenerates),
    # never as canned filler text.
    for code, post in BROKEN.items():
        out = NOA._finalize_noa_post(post, platforms=["facebook"])
        check(f"unpublishable draft is not faked up: {code}", out == "", f"-> {out!r}")

except ImportError as exc:      # deps missing outside the container
    print(f"  SKIP  chain tests (cannot import agents module: {exc})")



# ---------------------------------------------------------------- PART D ----
# The rewrite loop is the actual root fix, so it is exercised on its real code
# path with a stubbed model: a bad first draft must be REWRITTEN, not patched.
def _part_d():
    import asyncio
    import BACKEND_AI_AGENTS as AG

    NOA = AG.SocialMediaManagerAgent
    calls = {"n": 0, "prompts": []}

    drafts = [
        # 1: our own historical damage — stranded letter + glued disclosure
        "פילטר שמן מקורי ה Bosch לגולף 2019 עולה 89 ₪.אנחנו מוכרים חלקי חילוף בלבד.",
        # 2: fixes the grammar but reaches for a banned superlative
        "פילטר שמן מקורי ה-Bosch לגולף 2019 — הכי זול בארץ, רק 89 ₪.\nמזינים מספר רישוי באתר.",
        # 3: clean
        "פילטר שמן מקורי ה-Bosch לגולף 2019, והמק\"ט השתנה ב-2020.\n"
        "החל מ-89 ₪, ורואים את ההתאמה לפי מספר רישוי באתר.\n#חלפים",
    ]

    async def fake_hf_text(prompt="", system="", **kw):
        calls["prompts"].append(prompt)
        calls["n"] += 1
        return drafts[min(calls["n"] - 1, len(drafts) - 1)]

    real = AG.hf_text
    AG.hf_text = fake_hf_text
    try:
        post, problems = asyncio.get_event_loop().run_until_complete(
            NOA.write_post(prompt="כתבי פוסט", system="s", platforms=["facebook"]))
    finally:
        AG.hf_text = real

    check("rewrite loop used all needed attempts", calls["n"] == 3, f"-> {calls['n']}")
    check("final post is publishable", bool(post) and not problems, f"-> {post!r} {problems}")
    check("final post keeps the maqaf", "ה-Bosch" in post, f"-> {post!r}")
    check("draft 1's defect was named back to the writer",
          any("מקף" in p or "אות" in p for p in calls["prompts"][1:]))
    check("draft 2's policy breach was named back to the writer",
          any("סופרלטיב" in p for p in calls["prompts"][2:]))
    check("no canned filler anywhere in the result",
          not any(b in post for b in HS.BOILERPLATE), f"-> {post!r}")

    # And when the model never recovers, we must publish NOTHING.
    calls["n"] = 0
    bad = ["הפילטר ה מקורי לגולף עולה 89 ₪ באתר שלנו היום."] * 5

    async def always_bad(prompt="", system="", **kw):
        calls["n"] += 1
        return bad[0]

    AG.hf_text = always_bad
    try:
        post2, problems2 = asyncio.get_event_loop().run_until_complete(
            NOA.write_post(prompt="כתבי פוסט", system="s", platforms=["facebook"]))
    finally:
        AG.hf_text = real
    check("gives up instead of publishing filler", post2 == "" and bool(problems2),
          f"-> {post2!r}")
    check("gave up after the configured budget", calls["n"] == NOA.NOA_MAX_WRITE_ATTEMPTS,
          f"-> {calls['n']}")


print("\nPART D — write -> check -> rewrite loop")
try:
    _part_d()
except ImportError as exc:
    print(f"  SKIP  ({exc})")

print("\n" + ("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAILURE(S): {FAILURES}"))
sys.exit(1 if FAILURES else 0)
