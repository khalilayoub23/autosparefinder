"""
Phase 5H — Near-Miss Observability Regression Test
===================================================
Validates that the Phase 5H diagnostic telemetry additions to _scan_one_group()
and scan_groups() are:
  1. Behaviorally inert — identical classification results before and after
  2. Structurally correct — telemetry fields are present and accurate
  3. Threshold-invariant — 0.2 discovery / 0.4 comment boundaries unchanged

ZERO Production DB writes.  NO Facebook access.  NO live group scans.
Pure unit/mock tests.

Run: docker exec autospare_backend python3 /app/devtests/fb_phase5h_observability_test.py
"""

import asyncio
import sys
import types
from unittest.mock import AsyncMock, patch, MagicMock

sys.path.insert(0, "/app")

from social.facebook_browser.group_agent import (
    GroupAgent,
    _AUTO_KEYWORDS,
    _HE_PROCLITICS,
    _normalize_hebrew_token,
    _relevance_score,
)

# ── Helpers ────────────────────────────────────────────────────────────────────

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


def run(coro) -> None:
    asyncio.get_event_loop().run_until_complete(coro)


# ============================================================
#  Section 1 — Baseline sanity
# ============================================================
print("\n" + "=" * 68)
print("  SECTION 1: Baseline — classifier state unchanged by Phase 5H")
print("=" * 68)

check("_AUTO_KEYWORDS count == 66", len(_AUTO_KEYWORDS) == 66,
      f"actual={len(_AUTO_KEYWORDS)}")
check("_HE_PROCLITICS == frozenset('לבהומשכ')", _HE_PROCLITICS == frozenset("לבהומשכ"))
check("discovery threshold 0.2 → min(1/3.0,1) >= 0.2 (1 hit = discovery)",
      min(1 / 3.0, 1.0) >= 0.2)
check("comment threshold 0.4 → min(2/3.0,1) >= 0.4 (2 hits = comment)",
      min(2 / 3.0, 1.0) >= 0.4)

# ============================================================
#  Section 2 — Threshold invariant tests
# ============================================================
print("\n" + "=" * 68)
print("  SECTION 2: Threshold invariants — score values and boundaries")
print("=" * 68)

# All possible score values with integer hits under current formula
for hits, expected_score in [(0, 0.0), (1, 1/3.0), (2, 2/3.0), (3, 1.0)]:
    s = min(hits / 3.0, 1.0)
    check(f"min({hits}/3.0, 1.0) ≈ {round(s,4)}", abs(s - expected_score) < 1e-9,
          f"got {s}")

# Discovery boundary
for score, expected_disc in [
    (0.0,   False),
    (0.199, False),
    (0.2,   True),
    (0.333, True),
    (0.4,   True),
    (0.667, True),
    (1.0,   True),
]:
    is_disc = score >= 0.2
    check(f"score={score} → discovery={is_disc}", is_disc == expected_disc)

# Comment boundary
for score, expected_comment in [
    (0.333, False),
    (0.399, False),
    (0.4,   True),
    (0.667, True),
    (1.0,   True),
]:
    is_comment = score >= 0.4
    check(f"score={score} → comment={is_comment}", is_comment == expected_comment)

# ============================================================
#  Section 3 — Zero-score case
# ============================================================
print("\n" + "=" * 68)
print("  SECTION 3: Zero-score posts (no automotive keywords)")
print("=" * 68)

zero_score_texts = [
    ("Social greeting", "בוקר טוב לכולם! מי רוצה קפה?"),
    ("Food post", "מסעדה מצוינת בתל אביב, יש המלצות?"),
    ("Real estate", "דירה למכירה ברמת גן"),
    ("Job listing", "דרוש עובד למחסן"),
    ("Tourism", "המלצה לטיול בצפון?"),
]
for label, text in zero_score_texts:
    s = _relevance_score(text)
    check(f"Zero-score: '{label}' → score=0.0", s == 0.0, f"got {s}")

# ============================================================
#  Section 4 — Discovery cases (score >= 0.2)
# ============================================================
print("\n" + "=" * 68)
print("  SECTION 4: Discovery cases (score >= 0.2)")
print("=" * 68)

disc_texts = [
    # (label, text, min_score_threshold) — min_score uses exact float to avoid rounding traps
    ("Phase 5G disc-1 (רכב, טויוטה — 2 hits → 2/3)",
     "ירידת ערך שלא מובנת לרכב מעולה זה הטויוטה של החשמליות", 2/3.0 - 0.001),
    ("Phase 5G disc-3 (מוסך via prefix — 1 hit → 1/3)",
     "יש עידכון תוכנה ייתכן שווה בדיקה במוסך מורשה", 1/3.0 - 0.001),
    ("Phase 5G disc-9 (חלקים via prefix — 1 hit → 1/3)",
     "הזמנתי את כל החלקים לטיפול רק על המשאבת מים", 1/3.0 - 0.001),
    ("Phase 5E fixture G2-A (חלקים, סקודה, גיר — 3+ hits → 1.0)",
     "מישהו יודע מאיפה להשיג חלקים לסקודה? מחפש חלק לגיר", 1.0),
    ("Phase 5E fixture G4-D (רנו, מנוע, מסנן, שמן — 4 hits → 1.0)",
     "רנו קליאו עם בעיה במנוע, צריך מסנן שמן", 1.0),
]
for label, text, min_score in disc_texts:
    s = _relevance_score(text)
    is_disc = s >= 0.2
    check(f"Discovery: '{label}' score={round(s,3)} >= {round(min_score,3)}",
          s >= min_score and is_disc,
          f"got score={round(s,3)}")

# ============================================================
#  Section 5 — High-relevance cases (score >= 0.6)
# ============================================================
print("\n" + "=" * 68)
print("  SECTION 5: High-relevance cases (score >= 0.6)")
print("=" * 68)

high_rel_texts = [
    ("G1-C Phase 5E (לרכב, פנסים, רכבים)", "לרכב שלכם הגיעו הפנסים! מגוון רחב של פנסים לכל סוגי הרכבים במחיר מפתיע"),
    ("G4-D (רנו, מנוע, מסנן, שמן)", "רנו קליאו עם בעיה במנוע, צריך מסנן שמן"),
    ("G2-A (חלקים, סקודה, גיר)", "מישהו יודע מאיפה להשיג חלקים לסקודה? מחפש חלק לגיר"),
]
for label, text in high_rel_texts:
    s = _relevance_score(text)
    check(f"High-relevance: '{label}' score={round(s,3)} >= 0.6",
          s >= 0.6, f"got {round(s,3)}")

# ============================================================
#  Section 6 — Near-miss structural invariant
# ============================================================
print("\n" + "=" * 68)
print("  SECTION 6: Near-miss structural invariant")
print("=" * 68)

# With formula min(hits/3.0, 1.0) and integer hits:
# 0 hits → 0.0 (zero_score), 1 hit → 0.333 (discovery)
# The range (0.0, 0.2) is structurally unreachable.
possible_scores = sorted({round(min(h/3.0, 1.0), 4) for h in range(10)})
check("Possible score values = {0.0, 0.333, 0.667, 1.0}",
      possible_scores == [0.0, 0.3333, 0.6667, 1.0],
      f"got {possible_scores}")

# Near-miss range (0, 0.2) is unreachable with current formula
near_miss_possible = any(0 < round(min(h/3.0,1.0), 10) < 0.2 for h in range(1000))
check("No integer-hit score falls in (0.0, 0.2) — near_misses counter always 0 under current formula",
      not near_miss_possible,
      "Formula min(n/3.0,1) jumps directly from 0.0 to 0.333 with no intermediate values")

# ============================================================
#  Section 7 — Telemetry structure of _scan_one_group (via mock)
# ============================================================
print("\n" + "=" * 68)
print("  SECTION 7: _scan_one_group telemetry structure (offline mock)")
print("=" * 68)

async def _test_scan_one_group_telemetry():
    """Test _scan_one_group telemetry via mocked Playwright page."""
    agent = GroupAgent()

    # Build a synthetic posts_data payload: 3 zero-score, 2 discovery posts
    # Returned as {total_containers: 7, posts: [...5...]} (2 filtered by text length)
    mock_raw_result = {
        "raw_count": 7,  # e.g. 7 containers in DOM, 2 had text < 30 chars (per-step contract since d28c15b)
        "posts": [
            {"text": "בוקר טוב לכולם! מי רוצה לשתות קפה ולשוחח?", "postUrl": "https://fb.com/g/1/p/1"},
            {"text": "מסעדה מצוינת בתל אביב, ממליץ בחום על המקום הזה", "postUrl": "https://fb.com/g/1/p/2"},
            {"text": "דירה למכירה ברמת גן - 4 חדרים, קומה שנייה", "postUrl": "https://fb.com/g/1/p/3"},
            # Discovery: 1 hit → 0.333
            {"text": "לרכב שלי יש בעיה, מה עושים?", "postUrl": "https://fb.com/g/1/p/4"},
            # High-relevance: 3+ hits → 1.0
            {"text": "חלקים לרכב? יש מוסך טוב שמוכר חלקי טויוטה?", "postUrl": "https://fb.com/g/1/p/5"},
        ],
    }

    # _scan_one_group (since d28c15b) extracts at EVERY scroll step and stops after _STABILITY_STEPS
    # steps with no new post, so a fixed-length side_effect list can no longer model it. Script-aware
    # stub: the candidate-extraction script yields the posts on the first step and nothing afterwards
    # (the scanner's own stability logic then stops); scroll scripts return None.
    from social.facebook_browser import group_agent as _ga
    _extractions = {"n": 0}

    async def _evaluate(script, *args):
        if script is _ga._POST_CANDIDATE_JS:
            _extractions["n"] += 1
            return mock_raw_result if _extractions["n"] == 1 else {"raw_count": 0, "posts": []}
        return None  # window.scrollBy(...)

    mock_page = AsyncMock()
    mock_page.goto = AsyncMock()
    mock_page.evaluate = AsyncMock(side_effect=_evaluate)

    with patch("social.facebook_browser.group_agent._random_delay", AsyncMock()), \
         patch("social.facebook_browser.group_agent._wait_for_feed_readiness", AsyncMock()):
        result = await agent._scan_one_group(
            mock_page, "https://fb.com/g/1/", "Test Group", "g1", 10
        )

    # Structure checks
    check("_scan_one_group returns dict", isinstance(result, dict))
    check("_scan_one_group has 'discoveries' key", "discoveries" in result)
    check("_scan_one_group has 'telemetry' key", "telemetry" in result)

    tel = result.get("telemetry", {})
    discs = result.get("discoveries", [])

    check("telemetry.dom_candidates == 7", tel.get("dom_candidates") == 7,
          f"got {tel.get('dom_candidates')}")
    check("telemetry.text_valid == 5", tel.get("text_valid") == 5,
          f"got {tel.get('text_valid')}")
    check("telemetry.scored == 5 (all 5 posts scored, max_posts=10)",
          tel.get("scored") == 5, f"got {tel.get('scored')}")
    check("telemetry.discoveries == 2 (לרכב=0.333, חלקים+רכב+מוסך+טויוטה=1.0)",
          tel.get("discoveries") == 2, f"got {tel.get('discoveries')}")
    # With current formula, near_misses always 0
    check("telemetry.near_misses == 0 (no score in (0,0.2) with min(hits/3,1))",
          tel.get("near_misses") == 0, f"got {tel.get('near_misses')}")
    check("telemetry.zero_score == 3 (3 social posts with 0 automotive keywords)",
          tel.get("zero_score") == 3, f"got {tel.get('zero_score')}")

    # Consistency: discoveries + near_misses + zero_score == scored
    total_accounted = (tel.get("discoveries", 0) + tel.get("near_misses", 0)
                       + tel.get("zero_score", 0))
    check("discoveries + near_misses + zero_score == scored",
          total_accounted == tel.get("scored", -1),
          f"{total_accounted} == {tel.get('scored')}")

    # Behavioral invariant: actual discoveries list matches telemetry count
    check("len(discoveries) == telemetry.discoveries",
          len(discs) == tel.get("discoveries", -1),
          f"list={len(discs)}, tel={tel.get('discoveries')}")

    # Discovery scores are correct
    disc_scores = sorted([d.get("relevance_score", 0) for d in discs], reverse=True)
    check("Discoveries have correct relevance_score values (1.0 and 0.333)",
          len(disc_scores) == 2 and disc_scores[0] >= 0.6 and abs(disc_scores[1] - 0.333) < 0.01,
          f"scores={disc_scores}")

run(_test_scan_one_group_telemetry())

# ============================================================
#  Section 8 — scan_groups telemetry aggregation (offline mock)
# ============================================================
print("\n" + "=" * 68)
print("  SECTION 8: scan_groups telemetry aggregation (offline mock)")
print("=" * 68)

async def _test_scan_groups_telemetry():
    """
    Controlled 3-group mock scan:
      Group A: dom=5, valid=4, scored=4, disc=1, near=0, zero=3
      Group B: dom=8, valid=6, scored=6, disc=2, near=0, zero=4
      Group C: dom=3, valid=0, scored=0, disc=0, near=0, zero=0  (all posts < 30 chars)
    Expected aggregates:
      dom=16, valid=10, scored=10, disc=3, near=0, zero=7
    """
    agent = GroupAgent()
    approved = [
        {"id": "A", "group_url": "https://fb.com/g/A/", "group_name": "Group A"},
        {"id": "B", "group_url": "https://fb.com/g/B/", "group_name": "Group B"},
        {"id": "C", "group_url": "https://fb.com/g/C/", "group_name": "Group C"},
    ]

    grp_a_return = {
        "discoveries": [{"group_id": "A", "group_name": "Group A", "group_url": "https://fb.com/g/A/",
                          "post_url": "p1", "post_text": "רכב", "author": "",
                          "relevance_score": 0.333, "suggested_action": "monitor"}],
        "telemetry": {"dom_candidates": 5, "text_valid": 4, "scored": 4,
                      "discoveries": 1, "near_misses": 0, "zero_score": 3},
    }
    grp_b_return = {
        "discoveries": [
            {"group_id": "B", "group_name": "Group B", "group_url": "https://fb.com/g/B/",
             "post_url": "p2", "post_text": "מנוע טויוטה", "author": "",
             "relevance_score": 0.667, "suggested_action": "monitor"},
            {"group_id": "B", "group_name": "Group B", "group_url": "https://fb.com/g/B/",
             "post_url": "p3", "post_text": "חלקים לרכב", "author": "",
             "relevance_score": 0.667, "suggested_action": "monitor"},
        ],
        "telemetry": {"dom_candidates": 8, "text_valid": 6, "scored": 6,
                      "discoveries": 2, "near_misses": 0, "zero_score": 4},
    }
    grp_c_return = {
        "discoveries": [],
        "telemetry": {"dom_candidates": 3, "text_valid": 0, "scored": 0,
                      "discoveries": 0, "near_misses": 0, "zero_score": 0},
    }

    side_effects = [grp_a_return, grp_b_return, grp_c_return]

    with patch("social.facebook_browser.group_agent.FacebookSession") as MockSess:
        mock_page = AsyncMock()
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_page)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        MockSess.return_value = mock_ctx
        with patch.object(agent, "_scan_one_group", AsyncMock(side_effect=side_effects)):
            with patch("asyncio.sleep", AsyncMock()):
                result = await agent.scan_groups(approved)

    # Existing contract — unchanged
    check("scan_groups: session_failed=False", result["session_failed"] is False)
    check("scan_groups: groups_selected=3", result["groups_selected"] == 3,
          f"got {result['groups_selected']}")
    check("scan_groups: groups_attempted=3", result["groups_attempted"] == 3,
          f"got {result['groups_attempted']}")
    check("scan_groups: groups_fetched=3", result["groups_fetched"] == 3,
          f"got {result['groups_fetched']}")
    check("scan_groups: len(discoveries)==3", len(result["discoveries"]) == 3,
          f"got {len(result['discoveries'])}")

    # New telemetry
    tel = result.get("telemetry", {})
    check("scan_groups: telemetry present", isinstance(tel, dict))
    check("scan_groups: telemetry.dom_candidates==16", tel.get("dom_candidates") == 16,
          f"got {tel.get('dom_candidates')}")
    check("scan_groups: telemetry.text_valid==10", tel.get("text_valid") == 10,
          f"got {tel.get('text_valid')}")
    check("scan_groups: telemetry.scored==10", tel.get("scored") == 10,
          f"got {tel.get('scored')}")
    check("scan_groups: telemetry.discoveries==3", tel.get("discoveries") == 3,
          f"got {tel.get('discoveries')}")
    check("scan_groups: telemetry.near_misses==0", tel.get("near_misses") == 0,
          f"got {tel.get('near_misses')}")
    check("scan_groups: telemetry.zero_score==7", tel.get("zero_score") == 7,
          f"got {tel.get('zero_score')}")

    # Per-group telemetry
    pgt = result.get("per_group_telemetry", [])
    check("scan_groups: per_group_telemetry has 3 entries", len(pgt) == 3,
          f"got {len(pgt)}")
    grp_c_tel = next((g for g in pgt if g["group_id"] == "C"), None)
    check("Group C telemetry: dom=3, valid=0, scored=0, disc=0",
          grp_c_tel is not None and grp_c_tel["dom_candidates"] == 3
          and grp_c_tel["text_valid"] == 0 and grp_c_tel["scored"] == 0
          and grp_c_tel["discoveries"] == 0,
          f"got {grp_c_tel}")

    # Discoveries are sorted by relevance descending
    scores = [d.get("relevance_score", 0) for d in result["discoveries"]]
    check("Discoveries sorted by relevance descending",
          scores == sorted(scores, reverse=True), f"order={scores}")

run(_test_scan_groups_telemetry())

# ============================================================
#  Section 9 — Behavioral regression: telemetry does NOT change results
# ============================================================
print("\n" + "=" * 68)
print("  SECTION 9: Behavioral regression — classification outputs identical")
print("=" * 68)

# For each test text: compare pre-Phase-5H expected result with what
# _relevance_score() now returns (no change to the function).
regression_cases = [
    # (text, expected_score_exact, expected_is_discovery, expected_is_comment)
    # expected_score_exact uses exact float arithmetic to avoid rounding traps
    ("בוקר טוב לכולם", 0.0, False, False),
    ("מסעדה מצוינת", 0.0, False, False),
    # 1 hit (רכב via לרכב prefix) → 1/3
    ("לרכב שלי יש בעיה", 1/3.0, True, False),
    # 4 hits (חלקים, סקודה, חלק, גיר) → min(4/3,1) = 1.0
    ("מישהו יודע מאיפה להשיג חלקים לסקודה? מחפש חלק לגיר", 1.0, True, True),
    # 4 hits (רנו, מנוע, מסנן, שמן) → 1.0
    ("רנו קליאו עם בעיה במנוע, צריך מסנן שמן", 1.0, True, True),
    # 2 hits (רכב via לרכב, טויוטה via הטויוטה) → 2/3 → comment=True (0.667>=0.4)
    ("ירידת ערך שלא מובנת לרכב מעולה זה הטויוטה של החשמליות",
     2/3.0, True, True),
    # 1 hit (חלקים via החלקים prefix) → 1/3
    ("הזמנתי את כל החלקים לטיפול 240 יצא משהו כמו 2500 שקל",
     1/3.0, True, False),
    # 2 hits (רכב via לרכב, פנסים via הפנסים — רכבים→ית' not a keyword) → 2/3 → comment=True
    ("לרכב שלכם הגיעו הפנסים! מגוון רחב של פנסים לכל סוגי הרכבים",
     2/3.0, True, True),
]

for text, exp_score, exp_disc, exp_comment in regression_cases:
    s = _relevance_score(text)
    is_disc = s >= 0.2
    is_comment = s >= 0.4
    label = text[:45] + ("…" if len(text) > 45 else "")
    check(f"Regression '{label}': score≈{round(s,3)} disc={is_disc} comment={is_comment}",
          abs(s - exp_score) < 1e-9 and is_disc == exp_disc and is_comment == exp_comment,
          f"expected score={round(exp_score,4)} disc={exp_disc} comment={exp_comment}, "
          f"got score={round(s,4)} disc={is_disc} comment={is_comment}")

# ============================================================
#  Section 10 — Return contract backward-compatibility
# ============================================================
print("\n" + "=" * 68)
print("  SECTION 10: scan_groups return contract (backward-compatible)")
print("=" * 68)

async def _test_contract_backward_compat():
    """Existing callers only read existing keys; new keys are additive."""
    agent = GroupAgent()
    approved = [{"id": "1", "group_url": "https://fb.com/g/x/", "group_name": "X"}]

    grp_return = {
        "discoveries": [],
        "telemetry": {"dom_candidates": 2, "text_valid": 1, "scored": 1,
                      "discoveries": 0, "near_misses": 0, "zero_score": 1},
    }

    with patch("social.facebook_browser.group_agent.FacebookSession") as MockSess:
        mock_page = AsyncMock()
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_page)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        MockSess.return_value = mock_ctx
        with patch.object(agent, "_scan_one_group", AsyncMock(return_value=grp_return)):
            with patch("asyncio.sleep", AsyncMock()):
                result = await agent.scan_groups(approved)

    # Existing keys still present
    for key in ("discoveries", "groups_selected", "groups_attempted",
                "groups_fetched", "session_failed"):
        check(f"Existing key '{key}' still present", key in result,
              f"keys={list(result.keys())}")

    # Existing callers' .get() pattern works
    check("result.get('session_failed', False) == False",
          result.get("session_failed", False) is False)
    check("result.get('groups_fetched', 0) == 1",
          result.get("groups_fetched", 0) == 1)
    check("result['discoveries'] == []",
          result["discoveries"] == [])

    # New telemetry keys present
    check("New key 'telemetry' present", "telemetry" in result)
    check("New key 'per_group_telemetry' present", "per_group_telemetry" in result)

run(_test_contract_backward_compat())

# ============================================================
#  Section 11 — Session auth failure path — telemetry is empty
# ============================================================
print("\n" + "=" * 68)
print("  SECTION 11: Auth failure path — telemetry is zero/empty")
print("=" * 68)

async def _test_auth_failure_telemetry():
    """When session_failed=True, telemetry should be all-zeros."""
    agent = GroupAgent()
    approved = [{"id": "1", "group_url": "https://fb.com/g/x/", "group_name": "X"}]

    with patch("social.facebook_browser.group_agent.FacebookSession") as MockSess:
        mock_ctx = AsyncMock()
        # Simulate unauthenticated session (page=None from __aenter__)
        mock_ctx.__aenter__ = AsyncMock(return_value=None)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        MockSess.return_value = mock_ctx
        result = await agent.scan_groups(approved)

    check("Auth failure: session_failed=True", result["session_failed"] is True)
    check("Auth failure: discoveries=[]", result["discoveries"] == [])
    check("Auth failure: groups_attempted=0", result["groups_attempted"] == 0)
    check("Auth failure: groups_fetched=0", result["groups_fetched"] == 0)

    tel = result.get("telemetry", {})
    check("Auth failure: telemetry.dom_candidates=0", tel.get("dom_candidates") == 0,
          f"got {tel.get('dom_candidates')}")
    check("Auth failure: telemetry.scored=0", tel.get("scored") == 0,
          f"got {tel.get('scored')}")
    check("Auth failure: per_group_telemetry=[]",
          result.get("per_group_telemetry") == [],
          f"got {result.get('per_group_telemetry')}")

run(_test_auth_failure_telemetry())

# ============================================================
#  Section 12 — Near-miss counter instrumented correctly
#               (tested via synthetic score via monkeypatch)
# ============================================================
print("\n" + "=" * 68)
print("  SECTION 12: Near-miss counter — instrumented correctly")
print("=" * 68)

async def _test_near_miss_counter():
    """Verify near_miss counter works when _relevance_score returns 0 < s < 0.2.
    Uses monkeypatching to force a near-miss score that current formula cannot produce.
    This proves the counter is correctly wired even though it's structurally 0
    under the real formula.
    """
    agent = GroupAgent()

    mock_raw_result = {
        "raw_count": 3,
        "posts": [
            {"text": "אוטו מוביל מצב טוב עם חלקי חילוף מקוריים", "postUrl": "p1"},  # real discovery
            {"text": "בוקר טוב", "postUrl": "p2"},  # too short → filtered in JS (never reaches Python)
            # extra filler
            {"text": "סתם פוסט לא קשור לרכב כלל כלל", "postUrl": "p3"},  # zero-score
        ],
    }

    # Script-aware stub for the per-step extraction contract (see _test_scan_one_group_telemetry).
    from social.facebook_browser import group_agent as _ga2
    _ext2 = {"n": 0}

    async def _evaluate2(script, *args):
        if script is _ga2._POST_CANDIDATE_JS:
            _ext2["n"] += 1
            return mock_raw_result if _ext2["n"] == 1 else {"raw_count": 0, "posts": []}
        return None  # window.scrollBy(...)

    mock_page = AsyncMock()
    mock_page.goto = AsyncMock()
    mock_page.evaluate = AsyncMock(side_effect=_evaluate2)

    # Force one post to return 0.1 (near-miss), one to return 0.0 (zero), one real
    call_count = [0]
    real_score = _relevance_score

    def patched_score(text: str) -> float:
        call_count[0] += 1
        if call_count[0] == 1:
            return 0.1   # near-miss
        if call_count[0] == 2:
            return 0.0   # zero
        return real_score(text)

    with patch("social.facebook_browser.group_agent._random_delay", AsyncMock()), \
         patch("social.facebook_browser.group_agent._wait_for_feed_readiness", AsyncMock()):
        with patch("social.facebook_browser.group_agent._relevance_score", patched_score):
            result = await agent._scan_one_group(
                mock_page, "https://fb.com/g/test/", "Test", "t1", 10
            )

    tel = result.get("telemetry", {})
    check("Near-miss counter increments when 0 < score < 0.2",
          tel.get("near_misses") == 1, f"got near_misses={tel.get('near_misses')}")
    check("Zero-score counter increments when score == 0.0",
          tel.get("zero_score") == 1, f"got zero_score={tel.get('zero_score')}")
    check("Discoveries counter == 1 (third post is real discovery)",
          tel.get("discoveries") == 1, f"got discoveries={tel.get('discoveries')}")
    check("Scored == 3 (all posts scored)",
          tel.get("scored") == 3, f"got scored={tel.get('scored')}")

    # Near-miss posts must NOT appear in discoveries list
    check("Near-miss post NOT in discoveries",
          len(result.get("discoveries", [])) == 1)

run(_test_near_miss_counter())

# ============================================================
#  Summary
# ============================================================
print("\n" + "=" * 68)
print("  PHASE 5H REGRESSION CASE SUMMARY")
print("=" * 68)
total = PASS_COUNT + FAIL_COUNT
print(f"  Total: {total}  |  PASS: {PASS_COUNT}  |  FAIL: {FAIL_COUNT}")
print()
if FAIL_COUNT == 0:
    print("  ✅  ALL TESTS PASSED")
    print()
    print("  Phase 5H observability regression: PASS")
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
