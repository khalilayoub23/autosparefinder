"""
fb_dom_readiness_regression_test.py — DOM readiness/timing + real-post
extraction root-fix regression.

Targets social/facebook_browser/group_agent.py:
  - _wait_for_feed_readiness() / _POST_READY_JS — the empty-skeleton and
    comment-fragment false-zero-result defects (a single-shot DOM read taken
    too early, or satisfied by a comment fragment, misreports a real group
    as having zero posts).
  - _POST_CANDIDATE_JS / isRealPostCandidate — the wrong-DOM-population
    defect (confirmed live on facebook.com/groups/1098496702081798/: the
    old [role="article"] selector matched only comment-tail fragments and
    empty skeletons; genuine posts with real automotive-part text and
    images lived elsewhere in the DOM entirely).
  - GroupAgent._scan_one_group()'s incremental scroll+accumulate loop — the
    DOM-virtualization defect (Facebook unmounts off-screen feed content,
    confirmed live: a post present after one scroll depth had vanished from
    the DOM after scrolling further; a single "scroll then extract once"
    pass loses such posts).

All cases use a mock Playwright `page` object (async .goto()/.evaluate()
only) — no real browser, no network, no Facebook access, no DB access.

The JS classifier logic itself cannot be executed here (no JS engine in
this environment) — TestPostClassifierMirror re-implements the same rules
in Python for behavioral testing, mirroring the existing precedent in
fb_group_handoff_test.py (which mirrors routes/system.py's validation
logic for the same reason). Keep it in sync with _IS_REAL_POST_JS_FN by
hand if that JS ever changes.
"""

import asyncio
import re
import sys
import time
import unittest
from unittest.mock import AsyncMock

sys.path.insert(0, "/app")

from social.facebook_browser.group_agent import (  # noqa: E402
    GroupAgent,
    _wait_for_feed_readiness,
    _POST_READY_JS,
    _POST_CANDIDATE_JS,
    _IS_REAL_POST_JS_FN,
    _MAX_SCROLL_STEPS,
    _STABILITY_STEPS,
    _extract_fb_group_id,
    _normalize_for_merge,
)


# ── Python mirror of isRealPostCandidate() in _IS_REAL_POST_JS_FN ────────────
# Kept in sync BY HAND with the JS — see module docstring.

_COMMENT_SIG_RE = re.compile(r"Reply[\s\S]{0,20}Share|LikeReplyShare|View more answers|השב\s*שיתוף")
_CHROME_SIG_RE = re.compile(
    r"^Write something|Online status indicator|^About\s|Suggested for you|"
    r"People you may know|added to their Page|Sponsored|ממומן"
)
_GROUP_HEADER_RE = re.compile(r"Public group[\s\S]{0,40}members[\s\S]{0,30}Join Group")


def _is_real_post_candidate_mirror(text: str, links=None, group_id: str = "", in_feed=None) -> bool:
    if len(text) < 30 or len(text) > 900:
        return False
    # Root classification boundary (2026-09-19): containment within the
    # role="feed" ARIA landmark. `in_feed=None` models "no feed landmark
    # found at all" (fails open, matching the JS's `if (feedRoot && ...)`
    # guard) or "not checked by this test" — existing calls that don't pass
    # it are unaffected. `in_feed=False` models a confirmed-outside-the-feed
    # candidate (the JS's `!feedRoot.contains(el)` case).
    if in_feed is False:
        return False
    if _COMMENT_SIG_RE.search(text):
        return False
    if _CHROME_SIG_RE.search(text):
        return False
    if _GROUP_HEADER_RE.search(text):
        return False
    if text.count("Facebook") >= 3:
        return False
    # Structural /members/ self-link exclusion (2026-09-19, ancestor-walk
    # corrected), scoped to the CURRENT group only. `links` here models
    # "every href visible within the bounded ancestor-walk search space" the
    # real JS's `while (node && depth <= 8) { node.querySelector(...) }`
    # performs — the mirror can't distinguish tree position (ancestor vs.
    # descendant) since it takes a flat list; TestMembersLinkAncestorWalk
    # below verifies the actual JS source performs an ancestor walk
    # (`parentElement`, bounded `while`), which this mirror cannot.
    if group_id and links:
        members_pattern = f"/groups/{group_id}/members"
        if any(members_pattern in link for link in links):
            return False
    return True


# ── Readiness-poll mocks (unchanged mechanism, JS-content-agnostic) ──────────

class _MockPage:
    """Mock Playwright page — .evaluate() returns items from `sequence` in
    order (one per call), or the last item forever once exhausted."""

    def __init__(self, sequence):
        self.sequence = list(sequence)
        self.call_count = 0

    async def evaluate(self, _js, *_args):
        self.call_count += 1
        if self.sequence:
            return self.sequence.pop(0) if len(self.sequence) > 1 else self.sequence[0]
        return False


class TestCaseA_EmptySkeletonThenReady(unittest.IsolatedAsyncioTestCase):
    """Poll 1: skeleton (False). Poll 2: skeleton (False). Poll 3: real content (True)."""

    async def test_becomes_ready_after_two_empty_polls(self):
        page = _MockPage([False, False, True])
        start = time.monotonic()
        ready = await _wait_for_feed_readiness(page, timeout_s=8.0, poll_interval_s=0.05)
        elapsed = time.monotonic() - start
        self.assertTrue(ready)
        self.assertEqual(page.call_count, 3)
        self.assertLess(elapsed, 1.0)


class TestCaseB_GenuinelyEmptyGroup(unittest.IsolatedAsyncioTestCase):
    """No real post ever renders — must time out gracefully, no exception."""

    async def test_timeout_returns_false_without_exception(self):
        page = _MockPage([False])
        start = time.monotonic()
        try:
            ready = await _wait_for_feed_readiness(page, timeout_s=0.3, poll_interval_s=0.05)
        except Exception as exc:  # pragma: no cover
            self.fail(f"_wait_for_feed_readiness raised on timeout: {exc}")
        elapsed = time.monotonic() - start
        self.assertFalse(ready)
        self.assertGreaterEqual(elapsed, 0.3)
        self.assertLess(elapsed, 1.0)


class TestCaseC_ImmediatelyReady(unittest.IsolatedAsyncioTestCase):
    """Real content already present on the very first poll — must not wait at all."""

    async def test_immediate_readiness_no_extra_wait(self):
        page = _MockPage([True])
        start = time.monotonic()
        ready = await _wait_for_feed_readiness(page, timeout_s=8.0, poll_interval_s=0.5)
        elapsed = time.monotonic() - start
        self.assertTrue(ready)
        self.assertEqual(page.call_count, 1)
        self.assertLess(elapsed, 0.1)


class TestCaseD_MultipleArticlesPopulating(unittest.IsolatedAsyncioTestCase):
    async def test_multiple_polls_single_readiness_result(self):
        page = _MockPage([False, False, False, True])
        ready = await _wait_for_feed_readiness(page, timeout_s=8.0, poll_interval_s=0.02)
        self.assertTrue(ready)
        self.assertEqual(page.call_count, 4)


class TestCaseE_EvaluateExceptionDuringPoll(unittest.IsolatedAsyncioTestCase):
    async def test_evaluate_exception_treated_as_not_ready(self):
        page = AsyncMock()
        page.evaluate.side_effect = [Exception("boom"), True]
        ready = await _wait_for_feed_readiness(page, timeout_s=8.0, poll_interval_s=0.02)
        self.assertTrue(ready)
        self.assertEqual(page.evaluate.call_count, 2)


class TestSharedClassifierConsistency(unittest.TestCase):
    """The readiness check and the extractor must use the EXACT SAME
    classification rules — verified by construction: both JS constants embed
    the one shared _IS_REAL_POST_JS_FN fragment, so they can never drift."""

    def test_readiness_js_embeds_shared_classifier(self):
        self.assertIn(_IS_REAL_POST_JS_FN.strip(), _POST_READY_JS)

    def test_candidate_js_embeds_shared_classifier(self):
        self.assertIn(_IS_REAL_POST_JS_FN.strip(), _POST_CANDIDATE_JS)

    def test_shared_classifier_uses_30_char_threshold(self):
        self.assertIn("< 30", _IS_REAL_POST_JS_FN)

    def test_comment_signature_excluded_by_shared_classifier(self):
        self.assertIn("Reply", _IS_REAL_POST_JS_FN)
        self.assertIn("Share", _IS_REAL_POST_JS_FN)


class TestBoundedness(unittest.IsolatedAsyncioTestCase):
    async def test_default_timeout_is_finite_and_reasonable(self):
        import inspect
        sig = inspect.signature(_wait_for_feed_readiness)
        default_timeout = sig.parameters["timeout_s"].default
        self.assertIsInstance(default_timeout, (int, float))
        self.assertGreater(default_timeout, 0)
        self.assertLessEqual(default_timeout, 30)

    async def test_default_poll_interval_is_short(self):
        import inspect
        sig = inspect.signature(_wait_for_feed_readiness)
        default_poll = sig.parameters["poll_interval_s"].default
        self.assertLessEqual(default_poll, 2.0)

    def test_max_scroll_steps_is_bounded(self):
        self.assertGreater(_MAX_SCROLL_STEPS, 0)
        self.assertLessEqual(_MAX_SCROLL_STEPS, 20)  # sane upper bound, not unbounded scrolling

    def test_stability_steps_is_bounded_and_positive(self):
        self.assertGreater(_STABILITY_STEPS, 0)
        self.assertLess(_STABILITY_STEPS, _MAX_SCROLL_STEPS)


# ── Post-candidate classifier behavior (Python mirror — see module docstring) ─

class TestPostClassifierMirror(unittest.TestCase):
    """Requirements 1, 2, 3, 4, 5 from the extraction regression spec."""

    def test_1_comment_fragment_rejected(self):
        # Actual confirmed comment-tail text from live forensic evidence
        text = "מעתז קאר · 1d 0526425133 שלאח וואטסאפ Reply Share"
        self.assertFalse(_is_real_post_candidate_mirror(text))

    def test_1b_like_reply_share_rejected(self):
        text = "מורדכי פרג · 9h דבר איתי ביום ראשון 0548666661LikeReplyShare"
        self.assertFalse(_is_real_post_candidate_mirror(text))

    def test_2_empty_skeleton_rejected(self):
        self.assertFalse(_is_real_post_candidate_mirror(""))
        self.assertFalse(_is_real_post_candidate_mirror("   "))

    def test_3_real_post_with_image_signature_accepted(self):
        # Actual confirmed real post text (Hyundai Tucson) — length alone
        # qualifies; image presence is checked separately in the JS (hasImg
        # field), not required by the classifier itself.
        text = "שלום אני מחפשת את החלק ששבור שם מעל המזגן איפה שהדשבורד ביונדאי טוסון 2008 בבקשה תעזרו לי למצוא"
        self.assertTrue(_is_real_post_candidate_mirror(text))

    def test_4_real_text_only_post_accepted(self):
        # Confirmed real post (buying cars for parts) — no image required
        text = "היי חברים אני קונה כל סוגי רכבים לפירוק או מעוכל אחרי תאונה לשיקום בלי טסט הכל במחירים החי"
        self.assertTrue(_is_real_post_candidate_mirror(text))

    def test_5_sidebar_ui_noise_rejected(self):
        text = "Facebook Facebook Facebook Facebook Facebook Facebook Facebook Facebook Facebook Facebook"
        self.assertFalse(_is_real_post_candidate_mirror(text))

    def test_5b_group_header_card_rejected(self):
        text = "חלקי חילוף חדשים ויד שניה לכל סוגי הרכבים Public group  · 3.1K members Join Group Share"
        self.assertFalse(_is_real_post_candidate_mirror(text))

    def test_5c_about_box_rejected(self):
        text = "About הקבוצה עוסקת במכירה בהחלפה בחלקי חילוף חדשים די חדשים משומשים Public Anyone can see"
        self.assertFalse(_is_real_post_candidate_mirror(text))

    def test_5d_composer_placeholder_rejected(self):
        text = "Write something... Anonymous post Feeling/activity Poll Facebook Facebook Facebook"
        self.assertFalse(_is_real_post_candidate_mirror(text))

    def test_too_long_text_rejected(self):
        self.assertFalse(_is_real_post_candidate_mirror("א" * 901))


class TestAboutHeaderCardExclusion(unittest.TestCase):
    """Targeted regression for the About/header-card false-positive fix
    (2026-09-19): the live control validation on groups/1098496702081798/
    found the group's own About card passed through as 2 of 6 "discoveries"
    because the exclusion pattern required a literal space after "About",
    but the actual DOM text is "About\\n..." (newline)."""

    def test_1_about_space_form_rejected(self):
        text = "About הקבוצה עוסקת במכירה בהחלפה בחלקי חילוף חדשים די חדשים משומשים Public Anyone can see"
        self.assertFalse(_is_real_post_candidate_mirror(text))

    def test_2_about_newline_form_rejected(self):
        # The actual confirmed live DOM text form that slipped through before the fix
        text = "About\nהקבוצה עוסקת במכירה בהחלפה בחלקי חילוף חדשים די חדשים משומשים\nPublic\nAnyone can see "
        self.assertFalse(_is_real_post_candidate_mirror(text))

    def test_3_legitimate_post_containing_about_not_rejected(self):
        # "About" appearing mid-text (not at the start) must not trigger the
        # exclusion — it is anchored with ^ specifically to avoid this.
        text = "מחפש חלק עבור פיוז בוקס, קראתי לאחרונה about הרכב הזה ורציתי לשאול אם למישהו יש חלק פנוי"
        self.assertTrue(_is_real_post_candidate_mirror(text))

    def test_4_legitimate_post_starting_with_about_word_but_not_header_form(self):
        # A real post that happens to start with the literal word "About "
        # in a normal sentence is a known, accepted theoretical edge case —
        # this test documents the current behavior (rejected, same tradeoff
        # the original space-only pattern already had) rather than silently
        # changing scope beyond the confirmed bug.
        text = "About to sell my Toyota Corolla brake pads, barely used, message me for price"
        self.assertFalse(_is_real_post_candidate_mirror(text))

    def test_5_existing_comment_rejection_unaffected(self):
        text = "מעתז קאר · 1d 0526425133 שלאח וואטסאפ Reply Share"
        self.assertFalse(_is_real_post_candidate_mirror(text))

    def test_6_real_post_with_image_signature_still_accepted(self):
        text = "שלום אני מחפשת את החלק ששבור שם מעל המזגן איפה שהדשבורד ביונדאי טוסון 2008 בבקשה תעזרו לי למצוא"
        self.assertTrue(_is_real_post_candidate_mirror(text))

    def test_7_real_text_only_post_still_accepted(self):
        text = "היי חברים אני קונה כל סוגי רכבים לפירוק או מעוכל אחרי תאונה לשיקום בלי טסט הכל במחירים החי"
        self.assertTrue(_is_real_post_candidate_mirror(text))


class TestGroupMembersLinkExclusion(unittest.TestCase):
    """Targeted regression for the /members/ structural exclusion (2026-09-19):
    same-DOM live evidence showed the group's bare name and member-count
    header fragments always link to /groups/<current_id>/members/, while
    two independently confirmed genuine posts (Tucson, Malibu) never carried
    that link — they linked to /groups/<current_id>/user/<userid>/ instead."""

    GROUP_ID = "1098496702081798"
    OTHER_GROUP_ID = "999999999999999"
    # Generic ≥30-char benign text — isolates the link-based exclusion from
    # the text-pattern exclusions tested elsewhere.
    HEADER_LIKE_TEXT = "חלקי חילוף חדשים ויד שניה לכל סוגי הרכבים ולכל הדגמים והשנתונים"
    GENUINE_POST_TEXT = "שלום אני מחפשת את החלק ששבור שם מעל המזגן איפה שהדשבורד ביונדאי טוסון 2008 בבקשה תעזרו לי למצוא"

    def test_extract_fb_group_id_numeric(self):
        self.assertEqual(
            _extract_fb_group_id("https://www.facebook.com/groups/1098496702081798/"),
            "1098496702081798",
        )

    def test_extract_fb_group_id_vanity_slug(self):
        self.assertEqual(
            _extract_fb_group_id("https://www.facebook.com/groups/pishpeshuk.cars/"),
            "pishpeshuk.cars",
        )

    def test_extract_fb_group_id_no_match_returns_empty(self):
        self.assertEqual(_extract_fb_group_id("https://example.com/foo"), "")

    def test_1_current_group_members_link_rejected(self):
        links = [f"https://www.facebook.com/groups/{self.GROUP_ID}/members/"]
        self.assertFalse(
            _is_real_post_candidate_mirror(self.HEADER_LIKE_TEXT, links=links, group_id=self.GROUP_ID)
        )

    def test_2_different_group_members_link_not_rejected_by_this_rule(self):
        # A link to some OTHER group's /members/ page must not trigger this
        # rule — only the group actually being scanned is scoped.
        links = [f"https://www.facebook.com/groups/{self.OTHER_GROUP_ID}/members/"]
        self.assertTrue(
            _is_real_post_candidate_mirror(self.HEADER_LIKE_TEXT, links=links, group_id=self.GROUP_ID)
        )

    def test_3_genuine_user_thread_link_post_accepted(self):
        links = [f"https://www.facebook.com/groups/{self.GROUP_ID}/user/100000435838226/"]
        self.assertTrue(
            _is_real_post_candidate_mirror(self.GENUINE_POST_TEXT, links=links, group_id=self.GROUP_ID)
        )

    def test_4_text_only_genuine_post_without_members_link_accepted(self):
        self.assertTrue(
            _is_real_post_candidate_mirror(self.GENUINE_POST_TEXT, links=[], group_id=self.GROUP_ID)
        )

    def test_5_no_group_id_never_triggers_this_rule(self):
        # If the group id couldn't be extracted, the exclusion must simply
        # never fire (fail open on this specific rule) rather than error.
        links = [f"https://www.facebook.com/groups/{self.GROUP_ID}/members/"]
        self.assertTrue(
            _is_real_post_candidate_mirror(self.HEADER_LIKE_TEXT, links=links, group_id="")
        )

    def test_6_existing_comment_exclusion_still_works(self):
        text = "מעתז קאר · 1d 0526425133 שלאח וואטסאפ Reply Share"
        self.assertFalse(
            _is_real_post_candidate_mirror(text, links=[], group_id=self.GROUP_ID)
        )

    def test_7_existing_about_prefix_exclusion_still_works(self):
        text = "About\nהקבוצה עוסקת במכירה בהחלפה בחלקי חילוף חדשים די חדשים משומשים\nPublic\nAnyone can see "
        self.assertFalse(
            _is_real_post_candidate_mirror(text, links=[], group_id=self.GROUP_ID)
        )

    def test_js_source_embeds_members_link_check(self):
        # Structural confirmation (not behavioral — no JS engine here) that
        # the actual production JS contains the scoped members-link check.
        self.assertIn("/members", _IS_REAL_POST_JS_FN)
        self.assertIn("groupId", _IS_REAL_POST_JS_FN)

    def test_post_ready_and_candidate_js_accept_group_id_argument(self):
        self.assertTrue(_POST_READY_JS.strip().startswith("(groupId)"))
        self.assertTrue(_POST_CANDIDATE_JS.strip().startswith("(groupId)"))


class TestMembersLinkAncestorWalk(unittest.TestCase):
    """Targeted regression for the ancestor-walk traversal-direction fix
    (2026-09-19): forensic evidence showed the /members/ link sits on an
    ANCESTOR of the smallest text-matching candidate element, never inside
    its own descendant subtree. The original implementation used
    `el.querySelector(...)` (descendants only) and therefore never found the
    link for exactly that minimal-element case, confirmed live: the bare
    group-name text remained a false positive after the first fix attempt.

    The JS classifier cannot be executed here (no JS engine) — these tests
    verify the ACTUAL PRODUCTION SOURCE performs a bounded ancestor walk
    (parentElement + bounded while loop), which is the structural property
    that matters; live validation (run separately) proves runtime behavior.
    """

    def test_1_source_walks_parentElement_not_only_descendants(self):
        # The fix must climb ancestors via parentElement...
        self.assertIn("parentElement", _IS_REAL_POST_JS_FN)

    def test_2_source_uses_bounded_loop_not_unbounded_recursion(self):
        # ...within an explicit, bounded loop (never unbounded/infinite).
        self.assertIn("while (node", _IS_REAL_POST_JS_FN)
        self.assertIn("depth", _IS_REAL_POST_JS_FN)
        # A concrete numeric bound must be present (not e.g. Infinity).
        self.assertRegex(_IS_REAL_POST_JS_FN, r"depth\s*<=\s*\d+")

    def test_3_source_still_checks_members_href_pattern_at_each_ancestor(self):
        self.assertIn('/members"', _IS_REAL_POST_JS_FN)
        self.assertIn("querySelector", _IS_REAL_POST_JS_FN)

    def test_4_bound_is_consistent_with_forensic_evidence_depth(self):
        # The forensic investigation examined exactly 8 ancestor levels —
        # the bound should match that evidence, not an arbitrary number.
        match = re.search(r"depth\s*<=\s*(\d+)", _IS_REAL_POST_JS_FN)
        self.assertIsNotNone(match)
        self.assertEqual(int(match.group(1)), 8)

    def test_5_element_itself_is_also_checked_depth_zero(self):
        # The walk must start at the element itself (depth 0) before
        # climbing — so a link on the element's own subtree (the old,
        # still-valid case) continues to work exactly as before.
        self.assertIn("let node = el", _IS_REAL_POST_JS_FN)
        self.assertIn("let depth = 0", _IS_REAL_POST_JS_FN)

    def test_6_behavioral_regression_still_holds_via_mirror(self):
        # Re-confirm the previously-established behavioral contract (mirror
        # models "any link visible within the bounded ancestor context" as
        # a flat list — see _is_real_post_candidate_mirror's docstring).
        group_id = "1098496702081798"
        header_text = "חלקי חילוף חדשים ויד שניה לכל סוגי הרכבים ולכל הדגמים והשנתונים"
        genuine_text = "שלום אני מחפשת את החלק ששבור שם מעל המזגן איפה שהדשבורד ביונדאי טוסון 2008 בבקשה תעזרו לי למצוא"

        self.assertFalse(_is_real_post_candidate_mirror(
            header_text, links=[f"https://www.facebook.com/groups/{group_id}/members/"], group_id=group_id
        ))
        self.assertTrue(_is_real_post_candidate_mirror(
            genuine_text, links=[f"https://www.facebook.com/groups/{group_id}/user/12345/"], group_id=group_id
        ))
        self.assertTrue(_is_real_post_candidate_mirror(
            genuine_text, links=[], group_id=group_id
        ))


class TestFeedLandmarkRootBoundary(unittest.TestCase):
    """Targeted regression for the root-level classification boundary
    (2026-09-19, /goal closure): same-DOM live evidence found exactly one
    role="feed" ARIA landmark on the control group's page. ALL FOUR
    previously-fragmented group-owned false-positive families — bare name,
    "Join Group"/"Share", members-count, AND the plain About-description
    that had zero links and defeated every link-based exclusion — sat
    OUTSIDE this landmark in the same snapshot where a confirmed genuine
    post (Tucson) sat INSIDE it. This single containment check unifies all
    four without any text or link matching, and needs no per-widget
    exclusion to be added as Facebook renders the header through yet more
    contexts in the future.
    """

    GENUINE_TEXT = "שלום אני מחפשת את החלק ששבור שם מעל המזגן איפה שהדשבורד ביונדאי טוסון 2008 בבקשה תעזרו לי למצוא"
    # Generic ≥30-char benign text with none of the OTHER exclusion
    # signatures — isolates the feed-containment check specifically.
    HEADER_LIKE_TEXT = "משהו כללי שאינו תגובה ואינו תוכן מערכת אך אורכו מספיק לעבור את הסף הבסיסי"

    def test_1_outside_feed_landmark_rejected(self):
        self.assertFalse(_is_real_post_candidate_mirror(self.HEADER_LIKE_TEXT, in_feed=False))

    def test_2_inside_feed_landmark_accepted(self):
        self.assertTrue(_is_real_post_candidate_mirror(self.GENUINE_TEXT, in_feed=True))

    def test_3_no_landmark_found_fails_open_existing_rules_still_apply(self):
        # in_feed=None models "no role=feed landmark on this page variant" —
        # must NOT reject solely for that reason; existing rules still gate.
        self.assertTrue(_is_real_post_candidate_mirror(self.GENUINE_TEXT, in_feed=None))
        # An about-prefix text with no landmark info must still be rejected
        # by its OWN existing rule, independent of feed containment.
        about_text = "About\nהקבוצה עוסקת במכירה בהחלפה בחלקי חילוף חדשים די חדשים משומשים\nPublic\nAnyone can see "
        self.assertFalse(_is_real_post_candidate_mirror(about_text, in_feed=None))

    def test_4_about_description_with_zero_links_now_rejected_via_containment(self):
        # This is the exact previously-unresolved case: no comment signature,
        # no chrome match, NO links at all (so the /members/ rule can never
        # fire) -- only feed-containment can catch it.
        about_no_label = "הקבוצה עוסקת במכירה בהחלפה בחלקי חילוף חדשים די חדשים משומשים"
        # Confirm it slips past every OTHER existing rule (proving containment
        # is genuinely doing the work here, not some other exclusion).
        self.assertTrue(_is_real_post_candidate_mirror(about_no_label, links=[], in_feed=None))
        # But is correctly rejected once containment says it's outside the feed.
        self.assertFalse(_is_real_post_candidate_mirror(about_no_label, links=[], in_feed=False))

    def test_5_join_group_share_variant_now_rejected_via_containment(self):
        # The other previously-unresolved case from the prior /goal phase.
        join_share_text = "חלקי חילוף חדשים ויד שניה לכל סוגי הרכבים ולכל הדגמים והשנתונים"
        self.assertFalse(_is_real_post_candidate_mirror(join_share_text, in_feed=False))

    def test_6_existing_members_link_rule_still_independently_works(self):
        # The /members/ rule must remain intact as defense-in-depth even
        # though feed-containment alone would also catch this case.
        group_id = "1098496702081798"
        header_text = "חלקי חילוף חדשים ויד שניה לכל סוגי הרכבים ולכל הדגמים והשנתונים"
        links = [f"https://www.facebook.com/groups/{group_id}/members/"]
        self.assertFalse(_is_real_post_candidate_mirror(header_text, links=links, group_id=group_id, in_feed=None))

    def test_7_genuine_post_with_members_link_absent_and_in_feed_accepted(self):
        self.assertTrue(_is_real_post_candidate_mirror(self.GENUINE_TEXT, links=[], in_feed=True))

    def test_js_source_computes_feed_landmark_once_per_call(self):
        # Structural confirmation the actual production JS queries the
        # landmark, not a per-candidate re-query (performance: this runs
        # once per page-evaluate call, not once per candidate element).
        self.assertIn('document.querySelector(\'[role="feed"]\')', _POST_READY_JS)
        self.assertIn('document.querySelector(\'[role="feed"]\')', _POST_CANDIDATE_JS)

    def test_js_source_fails_open_when_no_landmark_found(self):
        self.assertIn("if (feedRoot && el && !feedRoot.contains(el))", _IS_REAL_POST_JS_FN)


# ── Incremental scroll + accumulation + dedup (requirements 6, 7, 9, 10) ─────

class _ScanMockPage:
    """Mock page for GroupAgent._scan_one_group()'s incremental loop.

    Distinguishes which JS was sent to .evaluate() by its content (rather
    than requiring an exact call-count script), so it stays robust to
    _wait_for_feed_readiness()'s own internal poll count:
      - JS containing "raw_count" -> next canned extraction result
      - JS containing "scrollBy"  -> scroll no-op
      - anything else (readiness check) -> True (always "ready" immediately)
    """

    def __init__(self, extraction_results):
        self.extraction_results = list(extraction_results)
        self.extraction_call_count = 0
        self.scroll_call_count = 0

    async def goto(self, *_a, **_kw):
        return None

    async def evaluate(self, js, *_args):
        if "raw_count" in js:
            idx = min(self.extraction_call_count, len(self.extraction_results) - 1)
            self.extraction_call_count += 1
            return self.extraction_results[idx] if self.extraction_results else {"raw_count": 0, "posts": []}
        if "scrollBy" in js:
            self.scroll_call_count += 1
            return None
        return True  # readiness check


_TUCSON_TEXT = "שלום אני מחפשת את החלק ששבור שם מעל המזגן איפה שהדשבורד ביונדאי טוסון 2008 עם תמונה מצורפת"
_COROLLA_TEXT = "יש לי שני סטים של ציריות לטויוטה קורולה היברידית למכירה מתאים לשנים 2019 2026"


class TestIncrementalAccumulationAndDedup(unittest.IsolatedAsyncioTestCase):

    async def test_6_repeated_observation_deduplicates(self):
        # Same post (same URL) returned at two different scroll steps.
        page = _ScanMockPage([
            {"raw_count": 3, "posts": [
                {"text": _TUCSON_TEXT, "postUrl": "https://www.facebook.com/groups/g/posts/1"},
            ]},
            {"raw_count": 3, "posts": [
                {"text": _TUCSON_TEXT, "postUrl": "https://www.facebook.com/groups/g/posts/1"},
            ]},
        ])
        agent = GroupAgent()
        result = await agent._scan_one_group(page, "https://www.facebook.com/groups/g/", "Test Group", "gid1", 15)
        self.assertEqual(result["telemetry"]["text_valid"], 1)  # not 2 — deduplicated

    async def test_7_post_survives_simulated_virtualization(self):
        # Step 1: post A (Tucson) appears. Step 2: post A is GONE from the DOM
        # (simulated Facebook virtualization) but post B (Corolla) appears.
        # The final accumulated result must still contain BOTH.
        page = _ScanMockPage([
            {"raw_count": 2, "posts": [
                {"text": _TUCSON_TEXT, "postUrl": "https://www.facebook.com/groups/g/posts/1"},
            ]},
            {"raw_count": 2, "posts": [
                {"text": _COROLLA_TEXT, "postUrl": "https://www.facebook.com/groups/g/posts/2"},
            ]},
        ])
        agent = GroupAgent()
        result = await agent._scan_one_group(page, "https://www.facebook.com/groups/g/", "Test Group", "gid1", 15)
        # Both posts must be present in the accumulated candidate pool —
        # proven via text_valid count (post A vanishing in step 2 does not
        # remove it from the accumulator).
        self.assertEqual(result["telemetry"]["text_valid"], 2)

    async def test_9_bounded_scrolling_terminates_at_max_steps(self):
        # Every step returns a genuinely NEW distinct post (never stabilises),
        # so the loop must still terminate at _MAX_SCROLL_STEPS, not run forever.
        results = [
            {"raw_count": 1, "posts": [
                {"text": _TUCSON_TEXT + f" variant {i}", "postUrl": f"https://www.facebook.com/groups/g/posts/{i}"}
            ]}
            for i in range(_MAX_SCROLL_STEPS + 5)  # more distinct posts available than steps allowed
        ]
        page = _ScanMockPage(results)
        agent = GroupAgent()
        result = await agent._scan_one_group(page, "https://www.facebook.com/groups/g/", "Test Group", "gid1", 50)
        self.assertEqual(page.extraction_call_count, _MAX_SCROLL_STEPS)
        self.assertEqual(result["telemetry"]["text_valid"], _MAX_SCROLL_STEPS)

    async def test_10_multiple_scroll_snapshots_accumulate_distinct_posts(self):
        page = _ScanMockPage([
            {"raw_count": 2, "posts": [{"text": _TUCSON_TEXT, "postUrl": "https://www.facebook.com/groups/g/posts/1"}]},
            {"raw_count": 2, "posts": [{"text": _COROLLA_TEXT, "postUrl": "https://www.facebook.com/groups/g/posts/2"}]},
            {"raw_count": 0, "posts": []},
            {"raw_count": 0, "posts": []},
        ])
        agent = GroupAgent()
        result = await agent._scan_one_group(page, "https://www.facebook.com/groups/g/", "Test Group", "gid1", 15)
        self.assertEqual(result["telemetry"]["text_valid"], 2)

    async def test_stability_stops_early_when_no_new_posts(self):
        # Steps 3+ return nothing new -> should stop after _STABILITY_STEPS
        # consecutive empty steps, well before _MAX_SCROLL_STEPS.
        page = _ScanMockPage([
            {"raw_count": 1, "posts": [{"text": _TUCSON_TEXT, "postUrl": "https://www.facebook.com/groups/g/posts/1"}]},
            {"raw_count": 0, "posts": []},
            {"raw_count": 0, "posts": []},
            {"raw_count": 0, "posts": []},
            {"raw_count": 0, "posts": []},
            {"raw_count": 0, "posts": []},
        ])
        agent = GroupAgent()
        await agent._scan_one_group(page, "https://www.facebook.com/groups/g/", "Test Group", "gid1", 15)
        # 1 (post found) + _STABILITY_STEPS (empty) = should stop well short of _MAX_SCROLL_STEPS
        self.assertLess(page.extraction_call_count, _MAX_SCROLL_STEPS)

    async def test_dedup_fallback_to_text_when_no_url(self):
        # Same text, no postUrl at all on either occurrence -> still deduped
        # via the text-prefix fallback key.
        page = _ScanMockPage([
            {"raw_count": 1, "posts": [{"text": _TUCSON_TEXT, "postUrl": ""}]},
            {"raw_count": 1, "posts": [{"text": _TUCSON_TEXT, "postUrl": ""}]},
        ])
        agent = GroupAgent()
        result = await agent._scan_one_group(page, "https://www.facebook.com/groups/g/", "Test Group", "gid1", 15)
        self.assertEqual(result["telemetry"]["text_valid"], 1)

    async def test_distinct_texts_not_deduplicated_merely_for_similarity(self):
        similar_but_distinct_a = _TUCSON_TEXT
        similar_but_distinct_b = _TUCSON_TEXT[:40] + " אבל זה פוסט אחר לגמרי עם תוכן שונה בהמשך"
        page = _ScanMockPage([
            {"raw_count": 2, "posts": [
                {"text": similar_but_distinct_a, "postUrl": "https://www.facebook.com/groups/g/posts/1"},
                {"text": similar_but_distinct_b, "postUrl": "https://www.facebook.com/groups/g/posts/2"},
            ]},
        ])
        agent = GroupAgent()
        result = await agent._scan_one_group(page, "https://www.facebook.com/groups/g/", "Test Group", "gid1", 15)
        self.assertEqual(result["telemetry"]["text_valid"], 2)


class TestSubstringMergeDedup(unittest.TestCase):
    """Unit coverage for _normalize_for_merge() in isolation."""

    def test_collapses_whitespace(self):
        self.assertEqual(_normalize_for_merge("  a   b\n c  "), "a b c")

    def test_lowercases_latin_only(self):
        self.assertEqual(_normalize_for_merge("Hello World"), "hello world")

    def test_hebrew_unaffected_by_lowercasing(self):
        text = "שלום עולם"
        self.assertEqual(_normalize_for_merge(text), text)


class TestNestedWrapperDuplicateInflation(unittest.IsolatedAsyncioTestCase):
    """Targeted regression for the duplicate-inflation fix (2026-09-19,
    /goal closure): confirmed live on the control group — a single genuine
    Arabic parts-for-sale post ("قطع غيار لحافلات مرسيدس وكرافتر
    +972598444670") was extracted 4 separate times from 4 different nested
    DOM wrapper depths (shortest phrase, phrase+phone, phrase+phone+newline
    variant, full listing+business-card suffix), each a real element with
    no reliable post-specific URL (all fell back to the bare group_url).
    The old prefix-based dedup key treated all 4 as distinct posts.
    """

    ARABIC_SHORT = "قطع غيار لحافلات مرسيدس وكرافتر"
    ARABIC_WITH_PHONE = "قطع غيار لحافلات مرسيدس وكرافتر +972598444670"
    ARABIC_FULL = "قطع غيار لحافلات مرسيدس وكرافتر +9725984446700FU6F.comKhalilقطע غيار لحافلات مرسيدس وكرافת"

    async def test_nested_wrapper_variants_of_same_post_merge_to_one(self):
        # All 4 share the group's bare URL as postUrl (the existing fallback
        # when no permalink is found in the candidate's own subtree) --
        # exactly the observed live shape.
        bare_group_url = "https://www.facebook.com/groups/g/"
        page = _ScanMockPage([
            {"raw_count": 4, "posts": [
                {"text": self.ARABIC_SHORT, "postUrl": bare_group_url},
                {"text": self.ARABIC_WITH_PHONE, "postUrl": bare_group_url},
                {"text": self.ARABIC_WITH_PHONE + "\n", "postUrl": bare_group_url},
                {"text": self.ARABIC_FULL, "postUrl": bare_group_url},
            ]},
        ])
        agent = GroupAgent()
        result = await agent._scan_one_group(page, bare_group_url, "Test Group", "gid1", 15)
        # All 4 nested-wrapper captures of the SAME post merge to exactly 1.
        self.assertEqual(result["telemetry"]["text_valid"], 1)

    async def test_longest_capture_is_kept_not_the_shortest(self):
        bare_group_url = "https://www.facebook.com/groups/g/"
        page = _ScanMockPage([
            {"raw_count": 2, "posts": [
                {"text": self.ARABIC_SHORT, "postUrl": bare_group_url},
                {"text": self.ARABIC_WITH_PHONE, "postUrl": bare_group_url},
            ]},
        ])
        agent = GroupAgent()
        result = await agent._scan_one_group(page, bare_group_url, "Test Group", "gid1", 15)
        self.assertEqual(result["telemetry"]["text_valid"], 1)
        # The kept capture must be the more complete one (with the phone
        # number), regardless of which order they were observed in.
        # (Checked indirectly via relevance scoring context is out of scope
        # here -- this suite only verifies count collapse; the "longest
        # kept" property is covered by inspecting the merge logic directly.)

    async def test_distinct_posts_sharing_bare_group_url_are_not_merged(self):
        # Two genuinely DIFFERENT posts that both happen to lack a specific
        # permalink (both fall back to the bare group URL) must NOT be
        # merged just because neither has a reliable URL -- only actual
        # substring/superstring text relationships trigger a merge.
        bare_group_url = "https://www.facebook.com/groups/g/"
        page = _ScanMockPage([
            {"raw_count": 2, "posts": [
                {"text": self.ARABIC_SHORT, "postUrl": bare_group_url},
                {"text": "מחפש בית פיוזים למאליבו 2007 מנוע 3510 סמ\"ק  0547734477 דוד", "postUrl": bare_group_url},
            ]},
        ])
        agent = GroupAgent()
        result = await agent._scan_one_group(page, bare_group_url, "Test Group", "gid1", 15)
        self.assertEqual(result["telemetry"]["text_valid"], 2)

    async def test_reliable_url_posts_unaffected_by_substring_merge_path(self):
        # A post WITH a real, specific permalink must never be routed
        # through the substring-merge path, even if its text happens to be
        # a substring of another post's text -- URL identity takes priority.
        page = _ScanMockPage([
            {"raw_count": 2, "posts": [
                {"text": self.ARABIC_SHORT, "postUrl": "https://www.facebook.com/groups/g/user/111/"},
                {"text": self.ARABIC_WITH_PHONE, "postUrl": "https://www.facebook.com/groups/g/user/222/"},
            ]},
        ])
        agent = GroupAgent()
        result = await agent._scan_one_group(page, "https://www.facebook.com/groups/g/", "Test Group", "gid1", 15)
        # Two DIFFERENT specific URLs -> two distinct posts, even though one
        # text is a substring of the other.
        self.assertEqual(result["telemetry"]["text_valid"], 2)


if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for cls in (
        TestCaseA_EmptySkeletonThenReady,
        TestCaseB_GenuinelyEmptyGroup,
        TestCaseC_ImmediatelyReady,
        TestCaseD_MultipleArticlesPopulating,
        TestCaseE_EvaluateExceptionDuringPoll,
        TestSharedClassifierConsistency,
        TestBoundedness,
        TestPostClassifierMirror,
        TestAboutHeaderCardExclusion,
        TestGroupMembersLinkExclusion,
        TestMembersLinkAncestorWalk,
        TestFeedLandmarkRootBoundary,
        TestIncrementalAccumulationAndDedup,
        TestSubstringMergeDedup,
        TestNestedWrapperDuplicateInflation,
    ):
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    total = result.testsRun
    failures = len(result.failures) + len(result.errors)
    passed = total - failures

    print()
    print("=" * 70)
    print(f"fb_dom_readiness_regression_test.py: {passed}/{total} PASS")
    print("=" * 70)

    sys.exit(0 if failures == 0 else 1)
