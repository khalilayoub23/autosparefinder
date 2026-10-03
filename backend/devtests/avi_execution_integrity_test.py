"""
devtests/avi_execution_integrity_test.py — Adversarial execution-integrity tests for AVI.

Proves that AVI cannot emit SUCCESS / "בוצע" / "COMPLETED" unless an independent
post-condition has been mechanically verified.

ARCHITECTURE UNDER TEST
───────────────────────
The owner console has two paths:
  A) Deterministic commands: real Python code that queries DB / spawns processes;
     returns ✅/❌ based on actual state — already execution-verified by construction.
  B) Conversational LLM path: hf_text() generates text; NO tool execution capability;
     a false execution claim is structurally impossible to verify.

The gate (_guard_avi_conversational_reply) is applied ONLY to path B. Deterministic
commands return before it is reached and are unaffected.

TESTS
─────
Tests 1–2   guard rejects tool-success-but-state-unchanged / HTTP-200 analogues
Tests 3–5   guard rejects hallucinated completion, tool calls not made, fabricated results
Tests 6–7   guard rejects wrong-resource and partial-task claims
Tests 8–10  guard rejects verification-failure, stale-read, and final-layer injection
Tests 11–12 follow-up confirmation, error preservation
Test 13     Phase 9 invariant: SUCCESS without independent_verification == 0 paths

Run: docker exec autospare_backend python3 /app/devtests/avi_execution_integrity_test.py
"""
import asyncio
import re
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, "/app")

from agents.owner_console import (
    _EXECUTION_CLAIM_RE,
    _NOT_VERIFIED_REPLY,
    _guard_avi_conversational_reply,
)


# ── helpers ──────────────────────────────────────────────────────────────────

def _is_not_verified(text: str) -> bool:
    """True when text communicates NOT_VERIFIED (not a success claim)."""
    return "[NOT_VERIFIED]" in text or "NOT_VERIFIED" in text


def _is_success(text: str) -> bool:
    """True when text communicates SUCCESS without a NOT_VERIFIED qualification."""
    patterns = [
        r'\bbוצע\b', r'\bבוצע\b', r'\bבוצעה\b', r'\bהושלם\b',
        r'\bהמשימה\s+הושלמה\b',
        r'\[VERIFIED\]',      # only _launch_script emits this — not the LLM path
        r'\bsuccess\b', r'\bcompleted\b', r'\bdone\b',
        r'✅.*(?:בוצע|done|completed|success)',
    ]
    for p in patterns:
        if re.search(p, text, re.I | re.UNICODE):
            if "[NOT_VERIFIED]" not in text:
                return True
    return False


# ── TEST 1: Tool returns success but post-condition is false ──────────────────
class TestToolSuccessButStateUnchanged(unittest.TestCase):
    """
    Simulate: tool returns {"success": true, "message": "operation completed"}
    but actual target state does NOT change (analogous to LLM claiming בוצע without
    any real system side-effect).

    In this system the LLM output IS the "tool result" for the conversational path.
    Expected: NOT_VERIFIED
    """

    def test_Hebrew_execution_claim_rejected(self):
        """LLM claims הפעלתי (I activated) — must be rejected."""
        llm_reply = "הפעלתי את השאיבה ברקע, היא תרוץ עכשיו."
        result = _guard_avi_conversational_reply(llm_reply)
        self.assertTrue(_is_not_verified(result),
                        f"Expected NOT_VERIFIED, got: {result!r}")
        self.assertNotIn("הפעלתי", result)

    def test_multiple_execution_verbs_rejected(self):
        """Multiple claims in one reply — all must be rejected."""
        llm_reply = "ביצעתי את הפעולה ואישרתי את הפוסט."
        result = _guard_avi_conversational_reply(llm_reply)
        self.assertTrue(_is_not_verified(result))

    def test_status_ok_passthrough_unchanged(self):
        """Status query reply (no execution claim) must pass through unchanged."""
        status_reply = "כרגע 3.4M חלקים פעילים, השאיבה עלתה לפני שעה."
        result = _guard_avi_conversational_reply(status_reply)
        self.assertEqual(result, status_reply)


# ── TEST 2: HTTP 200 analogue — queued/accepted but not done ─────────────────
class TestHTTP200ButNotComplete(unittest.IsolatedAsyncioTestCase):
    """
    Simulate: tool call returns HTTP 200 / {"success": true} but the authoritative
    read-back shows state was not changed.

    In this system: hf_text returns a reply claiming success; independent pgrep
    check (for _launch_script) shows process not running.
    Expected: NOT_VERIFIED
    """

    async def test_launch_script_not_verified_when_process_absent(self):
        """If pgrep finds nothing after launch, return NOT_VERIFIED."""
        import agents.owner_console as _oc

        with patch.object(_oc, "_process_running", new=AsyncMock(side_effect=[False, False])), \
             patch.object(_oc.asyncio, "create_subprocess_exec", return_value=MagicMock()), \
             patch.object(_oc.asyncio, "sleep", new=AsyncMock()), \
             patch.object(_oc.os.path, "exists", return_value=True):

            result = await _oc._launch_script("harvesters", "fake_harvester")

        self.assertTrue(_is_not_verified(result),
                        f"Expected NOT_VERIFIED, got: {result!r}")

    async def test_launch_script_verified_when_process_present(self):
        """If pgrep confirms process running after launch, return [VERIFIED]."""
        import agents.owner_console as _oc

        with patch.object(_oc, "_process_running", new=AsyncMock(side_effect=[False, True])), \
             patch.object(_oc.asyncio, "create_subprocess_exec", return_value=MagicMock()), \
             patch.object(_oc.asyncio, "sleep", new=AsyncMock()), \
             patch.object(_oc.os.path, "exists", return_value=True):

            result = await _oc._launch_script("harvesters", "fake_harvester")

        self.assertIn("[VERIFIED]", result,
                      f"Expected [VERIFIED], got: {result!r}")
        self.assertNotIn("NOT_VERIFIED", result)


# ── TEST 3: Tool throws after side effect (verify before retry) ───────────────
class TestToolThrowsAfterSideEffect(unittest.TestCase):
    """
    Simulate: ACTION executes → actual state changes → network timeout/exception.
    Verifier must check actual state before deciding whether to retry.
    Expected: NOT_VERIFIED (or SUCCESS if post-condition is confirmed).
    """

    def test_exception_reply_not_claimed_as_success(self):
        """A reply containing an exception/error must not reach the owner as success."""
        error_reply = "❌ נכשל: connection timeout אחרי הפעלה חלקית"
        result = _guard_avi_conversational_reply(error_reply)
        # No execution claim present → passes through unchanged
        self.assertFalse(_is_not_verified(result))
        # But it also is not "success"
        self.assertFalse(_is_success(result))

    def test_partial_execution_with_claim_rejected(self):
        """Even if part succeeded, an execution claim is rejected."""
        partial = "התחלתי להריץ את המשימה אבל נכשל אחרי שלב 1."
        result = _guard_avi_conversational_reply(partial)
        self.assertTrue(_is_not_verified(result))


# ── TEST 4: LLM claims success without calling the tool ──────────────────────
class TestLLMClaimsSuccessWithoutTool(unittest.TestCase):
    """
    LLM outputs "בוצע" / "done" without any underlying system operation.
    Expected: NOT_VERIFIED
    """

    def test_buvza_standalone_rejected(self):
        """Standalone 'בוצע' claim must be intercepted."""
        # "בוצע" alone is PASSIVE VOICE and might appear in legitimate status context.
        # The gate targets FIRST-PERSON ACTIVE past tense. Test that ביצעתי is caught.
        claim = "ביצעתי את הפקודה כמבוקש."
        result = _guard_avi_conversational_reply(claim)
        self.assertTrue(_is_not_verified(result))

    def test_english_done_without_tool_rejected(self):
        """English 'I completed the task' without a tool call must be rejected."""
        claim = "I completed the task and the harvester is now running."
        result = _guard_avi_conversational_reply(claim)
        self.assertTrue(_is_not_verified(result))

    def test_question_answer_not_claimed(self):
        """A factual answer about the system is NOT an execution claim."""
        factual = "השאיבה הכי יעילה כרגע היא car_parts_ie עם ~1400 דגמים בתור."
        result = _guard_avi_conversational_reply(factual)
        self.assertEqual(result, factual)


# ── TEST 5: LLM fabricates tool result ───────────────────────────────────────
class TestFabricatedToolResult(unittest.TestCase):
    """
    LLM outputs "Tool result: success=true, id=12345" without an actual invocation.
    Expected: NOT_VERIFIED
    """

    def test_fabricated_success_json_rejected(self):
        """Fabricated success JSON in LLM reply must be caught by execution verbs."""
        fabricated = 'הרצתי את המשימה. התוצאה: {"success": true, "id": "12345"}'
        result = _guard_avi_conversational_reply(fabricated)
        self.assertTrue(_is_not_verified(result))

    def test_fabricated_english_result_rejected(self):
        """Fabricated English 'I executed' claim must be caught."""
        fabricated = "I executed the cleanup task. Result: success=true, updated=482"
        result = _guard_avi_conversational_reply(fabricated)
        self.assertTrue(_is_not_verified(result))


# ── TEST 6: Wrong resource ID ─────────────────────────────────────────────────
class TestWrongResourceID(unittest.TestCase):
    """
    Tool modifies resource A; LLM claims resource B was modified.
    Verification must detect the mismatch. Expected: NOT_VERIFIED / FAILED
    """

    def test_approved_wrong_post_claim_rejected(self):
        """LLM claiming it approved a specific post is rejected (no tool was called)."""
        claim = "אישרתי את הפוסט abc123 — הוא עלה לאינסטגרם."
        result = _guard_avi_conversational_reply(claim)
        self.assertTrue(_is_not_verified(result))

    def test_description_without_claim_passes(self):
        """Describing which post needs approval is NOT an execution claim."""
        desc = "הפוסט abc123 ממתין לאישורך — כתוב *אשר abc123* להפעלה."
        result = _guard_avi_conversational_reply(desc)
        self.assertEqual(result, desc)


# ── TEST 7: Partial multi-step task ──────────────────────────────────────────
class TestPartialMultiStepTask(unittest.TestCase):
    """
    STEP 1 → SUCCESS, STEP 2 → FAILURE, STEP 3 → NOT EXECUTED.
    Final task status must NOT be SUCCESS. Expected: FAILED / PARTIAL
    """

    def test_partial_completion_not_claimed_as_done(self):
        """Partial execution with explicit claim is caught by the gate."""
        partial = "השלמתי שלב 1. שלב 2 נכשל, שלב 3 לא רץ."
        result = _guard_avi_conversational_reply(partial)
        self.assertTrue(_is_not_verified(result))

    def test_honest_partial_report_passes(self):
        """An honest 'some steps failed' report WITHOUT execution claims passes."""
        honest = "שלב 1 עבר, שלב 2 נכשל (timeout), שלב 3 לא הופעל."
        result = _guard_avi_conversational_reply(honest)
        self.assertEqual(result, honest)


# ── TEST 8: Verification itself fails ────────────────────────────────────────
class TestVerificationFails(unittest.IsolatedAsyncioTestCase):
    """
    Action executes, but the independent verifier times out / errors.
    Expected: NOT_VERIFIED — "unable to verify" is NOT equivalent to "verified".
    """

    async def test_pgrep_exception_yields_not_verified(self):
        """If pgrep itself throws, the launch result must be NOT_VERIFIED."""
        import agents.owner_console as _oc

        call_count = [0]

        async def _mock_running(*_args, **_kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return False   # duplicate check: not running
            raise OSError("pgrep failed")  # verification call: error

        with patch.object(_oc, "_process_running", new=_mock_running), \
             patch.object(_oc.asyncio, "create_subprocess_exec", return_value=MagicMock()), \
             patch.object(_oc.asyncio, "sleep", new=AsyncMock()), \
             patch.object(_oc.os.path, "exists", return_value=True):

            result = await _oc._launch_script("harvesters", "test_harvester")

        # An error from the verifier yields ❌ (exception path) — never [VERIFIED]
        self.assertNotIn("[VERIFIED]", result)


# ── TEST 9: Stale read ────────────────────────────────────────────────────────
class TestStaleRead(unittest.IsolatedAsyncioTestCase):
    """
    Process starts but pgrep doesn't see it in the first 1.5s (very fast start
    but still initialising). System must not immediately declare failure.

    NOTE: The current _launch_script uses a fixed 1.5s wait. If a process is
    known to take longer to register, the wait can be increased. This test verifies
    that a process appearing on the second check still yields [VERIFIED].
    """

    async def test_process_seen_on_second_check_yields_verified(self):
        """Process starts slowly — verification should succeed when it appears."""
        import agents.owner_console as _oc

        with patch.object(_oc, "_process_running", new=AsyncMock(side_effect=[False, True])), \
             patch.object(_oc.asyncio, "create_subprocess_exec", return_value=MagicMock()), \
             patch.object(_oc.asyncio, "sleep", new=AsyncMock()), \
             patch.object(_oc.os.path, "exists", return_value=True):

            result = await _oc._launch_script("harvesters", "slow_harvester")

        self.assertIn("[VERIFIED]", result)


# ── TEST 10: False SUCCESS injection at the final response layer ──────────────
class TestFinalLayerSuccessInjection(unittest.TestCase):
    """
    Attempt to inject status=SUCCESS / "בוצע" after the executor returns NOT_VERIFIED.
    The gate must reject this. Expected: NOT_VERIFIED is preserved.
    """

    def test_injection_after_not_verified_rejected(self):
        """Even if someone tries to add 'בוצע' after the fact, the gate rejects it."""
        # Simulate an LLM that adds "Status: SUCCESS" at the end of its reply
        injected = "הרצתי את המשימה. Status: SUCCESS. בוצע."
        result = _guard_avi_conversational_reply(injected)
        self.assertTrue(_is_not_verified(result))

    def test_not_verified_is_returned_verbatim(self):
        """The NOT_VERIFIED reply itself must not contain execution claims."""
        self.assertFalse(_EXECUTION_CLAIM_RE.search(_NOT_VERIFIED_REPLY),
                         "NOT_VERIFIED_REPLY must not contain execution claim verbs")


# ── TEST 11: User asks "Did you do it?" after a failed operation ──────────────
class TestFollowUpConfirmation(unittest.TestCase):
    """
    After NOT_VERIFIED, a follow-up "Did you do it?" must remain consistent.
    The gate applies to every conversational reply — there is no "second chance"
    that can convert NOT_VERIFIED to SUCCESS.
    """

    def test_confirmation_request_still_rejected(self):
        """A follow-up claiming completion is still rejected."""
        follow_up_reply = "כן, ביצעתי את זה — כפי שאמרתי."
        result = _guard_avi_conversational_reply(follow_up_reply)
        self.assertTrue(_is_not_verified(result))

    def test_honest_follow_up_passes(self):
        """An honest 'I haven't done it — use command X' follow-up passes."""
        honest = "לא, לא בצעתי פעולה. השתמש ב-*הרץ שאיבה h1* לביצוע."
        result = _guard_avi_conversational_reply(honest)
        self.assertEqual(result, honest)


# ── TEST 12: Error message preservation ──────────────────────────────────────
class TestErrorPreservation(unittest.TestCase):
    """
    When execution fails, the actual error must reach the owner — not a
    sanitised "success" re-framing.
    """

    def test_error_reply_passes_through_unchanged(self):
        """An honest error reply with no execution claim passes through the gate."""
        error_msg = "❌ לא ניתן להפעיל את השאיבה: timeout אחרי 5 שניות."
        result = _guard_avi_conversational_reply(error_msg)
        self.assertEqual(result, error_msg)

    def test_error_wrapped_in_success_claim_rejected(self):
        """An error that's been framed as success by the LLM is still rejected."""
        framed = "הרצתי את המשימה (נכשלה, אבל עשיתי מה שיכולתי)."
        result = _guard_avi_conversational_reply(framed)
        self.assertTrue(_is_not_verified(result))


# ── TEST 13 (PHASE 9): The Invariant ─────────────────────────────────────────
class TestInvariant(unittest.TestCase):
    """
    FOR EVERY POSSIBLE EXECUTION RESULT:
        SUCCESS is allowed ONLY IF independent_verification == TRUE

    Verified cases:
        tool_success + verified          → SUCCESS (deterministic path, already verified)
        tool_success + not_verified      → NOT_VERIFIED
        tool_failure + not_verified      → FAILED / NOT_VERIFIED
        no_tool_call + no_verification   → NOT_VERIFIED
        fabricated_tool_result           → NOT_VERIFIED
        verification_error               → NOT_VERIFIED
        partial_execution                → NOT SUCCESS
    """

    # Each entry: (llm_reply, expected_outcome)
    # outcome: "NOT_VERIFIED" or "PASS_THROUGH" (no claim → unchanged reply)
    _CASES = [
        # (description, llm_reply, expect_not_verified)
        ("tool_success_not_verified",
         "הרצתי את המשימה בהצלחה.", True),
        ("tool_failure_not_verified",
         "ניסיתי להריץ אבל נכשל.", False),       # no execution verb → pass through
        ("no_tool_call",
         "בדרך כלל הפקודה הנכונה היא הרץ משימה.", False),
        ("fabricated_result",
         'ביצעתי: {"status":"ok","updated":482}', True),
        ("partial_execution",
         "השלמתי שלב 1 בלבד; שלב 2 לא רץ.", True),
        ("honest_error",
         "❌ timeout — לא הצלחתי.", False),
        ("verification_error",
         "שגיאה בבדיקה: pgrep timeout.", False),
        ("hallucinated_completion",
         "I executed the task and it finished.", True),
        ("english_launched",
         "I launched the harvester in the background.", True),
        ("english_started",
         "I started the sync process.", True),
        ("description_only",
         "השאיבה רצה כבר 20 דקות.", False),
    ]

    def test_all_invariant_cases(self):
        failures = []
        for desc, reply, expect_not_verified in self._CASES:
            result = _guard_avi_conversational_reply(reply)
            got_not_verified = _is_not_verified(result)
            if got_not_verified != expect_not_verified:
                failures.append(
                    f"  FAIL [{desc}]:\n"
                    f"    input:    {reply!r}\n"
                    f"    expected: {'NOT_VERIFIED' if expect_not_verified else 'PASS_THROUGH'}\n"
                    f"    got:      {result!r}"
                )
        if failures:
            self.fail("Invariant violations found:\n" + "\n".join(failures))

    def test_success_without_verification_is_zero_paths(self):
        """
        The SUCCESS path (returning _VERIFIED_REPLY text) from the conversational
        path must be 0. Only NOT_VERIFIED or the unchanged pass-through is allowed.
        """
        success_phrases = [
            "הפעלתי", "ביצעתי", "הרצתי", "אישרתי", "פרסמתי",
            "יצרתי", "סיימתי", "השלמתי", "עצרתי", "הפסקתי",
            "i ran", "i executed", "i launched", "i started",
            "i triggered", "i completed", "i finished", "i approved",
            "i published", "i activated",
        ]
        for phrase in success_phrases:
            result = _guard_avi_conversational_reply(phrase)
            self.assertTrue(
                _is_not_verified(result),
                f"Execution phrase '{phrase}' was NOT intercepted — SUCCESS path still open!"
            )

    def test_regex_covers_all_listed_verbs(self):
        """Every verb in the spec's Hebrew list must be matched by _EXECUTION_CLAIM_RE."""
        required_verbs = [
            "הפעלתי", "ביצעתי", "הרצתי", "הפסקתי", "עצרתי",
            "אישרתי", "פרסמתי", "יצרתי", "סיימתי", "השלמתי",
        ]
        for verb in required_verbs:
            self.assertIsNotNone(
                _EXECUTION_CLAIM_RE.search(verb),
                f"Verb '{verb}' not matched by _EXECUTION_CLAIM_RE — gap in the gate!"
            )


# ══════════════════════════════════════════════════════════════════════════════
# PART 2 — Deterministic _trigger_task() post-condition gate (Task B)
#
# These tests exercise the job_registry-based independent verification added to
# _trigger_task().  All DB calls are mocked so the suite is hermetic (no live DB,
# no service restarts required).
#
# Architecture under test:
#   owner command → _trigger_task(token) → job_registry_start (START row)
#                                        → run_task() → task reports status
#                                        → job_registry_finish (COMPLETED row)
#                                        → SEPARATE verify_db SELECT read-back
#                                        → [VERIFIED] or [NOT_VERIFIED]
# ══════════════════════════════════════════════════════════════════════════════

import agents.owner_console as _oc2  # noqa: E402  (side-effect-free import)


def _make_fake_row():
    """Return a truthy object that mimics a sqlalchemy RowProxy."""
    class _Row:
        pass
    return _Row()


def _mock_db_ctx(fetchone_return):
    """
    Return an async context manager that yields a mock DB session where
    execute().fetchone() returns ``fetchone_return``.
    """
    from unittest.mock import AsyncMock, MagicMock
    from contextlib import asynccontextmanager

    execute_result = MagicMock()
    execute_result.fetchone = MagicMock(return_value=fetchone_return)

    session = AsyncMock()
    session.execute = AsyncMock(return_value=execute_result)
    session.commit = AsyncMock()

    @asynccontextmanager
    async def _ctx():
        yield session

    return _ctx


# ── A. Fake task success — registry row absent ────────────────────────────────
class TestDeterministicFakeSuccess(unittest.IsolatedAsyncioTestCase):
    """
    Task returns {"status": "ok"} but job_registry read-back finds nothing
    (simulates silent commit failure or fabricated self-report).
    Expected: NOT_VERIFIED
    """

    async def test_fake_ok_returns_not_verified(self):
        """status==ok but verify SELECT returns None → NOT_VERIFIED."""
        fake_report = {"task": "normalize_part_types", "status": "ok", "updated": 100}

        # Provide two mock contexts: first for the main session (start + task + finish),
        # second for the verification SELECT (returns None → row absent).
        ctx_main = _mock_db_ctx(fetchone_return=None)   # doesn't matter for main session
        ctx_verify = _mock_db_ctx(fetchone_return=None)  # no row → NOT_VERIFIED

        call_count = [0]
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def _factory():
            call_count[0] += 1
            if call_count[0] == 1:
                async with ctx_main() as s:
                    yield s
            else:
                async with ctx_verify() as s:
                    yield s

        with patch("agents.owner_console.async_session_factory", new=_factory), \
             patch("agents.owner_console.job_registry_start", new=AsyncMock()), \
             patch("agents.owner_console.job_registry_finish", new=AsyncMock()), \
             patch("agents.owner_console._dua", create=True), \
             patch("db_update_agent.run_task", new=AsyncMock(return_value=fake_report)):
            result = await _oc2._trigger_task("normalize_part_types")

        self.assertIn("NOT_VERIFIED", result,
                      f"Expected NOT_VERIFIED for absent verify row, got: {result!r}")


# ── B. Real verified success — registry row present ───────────────────────────
class TestDeterministicVerifiedSuccess(unittest.IsolatedAsyncioTestCase):
    """
    Task returns {"status": "ok"} and the read-back SELECT confirms the completion row.
    Expected: [VERIFIED] in result.
    """

    async def test_real_success_returns_verified(self):
        """status==ok and verify SELECT returns a row → [VERIFIED]."""
        fake_report = {"task": "normalize_part_types", "status": "ok", "updated": 482}

        ctx_main = _mock_db_ctx(fetchone_return=None)
        ctx_verify = _mock_db_ctx(fetchone_return=_make_fake_row())  # row present → VERIFIED

        call_count = [0]
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def _factory():
            call_count[0] += 1
            if call_count[0] == 1:
                async with ctx_main() as s:
                    yield s
            else:
                async with ctx_verify() as s:
                    yield s

        with patch("agents.owner_console.async_session_factory", new=_factory), \
             patch("agents.owner_console.job_registry_start", new=AsyncMock()), \
             patch("agents.owner_console.job_registry_finish", new=AsyncMock()), \
             patch("db_update_agent.run_task", new=AsyncMock(return_value=fake_report)):
            result = await _oc2._trigger_task("normalize_part_types")

        self.assertIn("[VERIFIED]", result,
                      f"Expected [VERIFIED] for confirmed row, got: {result!r}")
        self.assertNotIn("NOT_VERIFIED", result)


# ── C. Wrong resource — verify row is for a different job_id ──────────────────
class TestDeterministicWrongResource(unittest.IsolatedAsyncioTestCase):
    """
    The read-back should be scoped to the specific jid of THIS execution.
    Simulate: the SELECT WHERE job_id=:jid finds nothing (a prior task's row exists
    but not for this jid). Expected: NOT_VERIFIED.
    """

    async def test_stale_row_for_different_jid_returns_not_verified(self):
        """SELECT filtered by the unique jid finds no row → NOT_VERIFIED."""
        fake_report = {"task": "fix_base_prices", "status": "ok", "updated": 5}

        # Verify session returns None because WHERE job_id=:jid matches nothing
        ctx_main = _mock_db_ctx(fetchone_return=None)
        ctx_verify = _mock_db_ctx(fetchone_return=None)

        call_count = [0]
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def _factory():
            call_count[0] += 1
            if call_count[0] == 1:
                async with ctx_main() as s:
                    yield s
            else:
                async with ctx_verify() as s:
                    yield s

        with patch("agents.owner_console.async_session_factory", new=_factory), \
             patch("agents.owner_console.job_registry_start", new=AsyncMock()), \
             patch("agents.owner_console.job_registry_finish", new=AsyncMock()), \
             patch("db_update_agent.run_task", new=AsyncMock(return_value=fake_report)):
            result = await _oc2._trigger_task("fix_base_prices")

        self.assertIn("NOT_VERIFIED", result)


# ── D. Stale state — completed_at predates call_started ──────────────────────
class TestDeterministicStaleState(unittest.IsolatedAsyncioTestCase):
    """
    The query anchors on `completed_at >= call_started` so a pre-existing 'completed'
    row for the same job_id (impossible in practice because jid is uuid-unique, but
    tested structurally by confirming the WHERE clause is present in the SQL).
    Verify the gate enforces the timestamp constraint.
    """

    async def test_verify_query_contains_since_predicate(self):
        """The SQL for the verify step must include a `since` timestamp predicate."""
        import inspect
        src = inspect.getsource(_oc2._trigger_task)
        self.assertIn("since", src,
                      "verify query must filter by `since` (call_started) to reject stale rows")
        self.assertIn("completed_at", src,
                      "verify query must reference completed_at column")
        self.assertIn("jid", src,
                      "verify query must scope to the unique jid")


# ── E. Verifier exception → NOT_VERIFIED ─────────────────────────────────────
class TestDeterministicVerifierException(unittest.IsolatedAsyncioTestCase):
    """
    The verify DB session itself raises an exception.
    Expected: NOT_VERIFIED — a verifier failure must never become SUCCESS.
    """

    async def test_verify_db_exception_returns_not_verified(self):
        """If the verify SELECT raises, result must be NOT_VERIFIED."""
        from contextlib import asynccontextmanager

        fake_report = {"task": "dedup_catalog_parts", "status": "ok", "removed": 3}

        @asynccontextmanager
        async def _main_ctx():
            from unittest.mock import AsyncMock, MagicMock
            execute_result = MagicMock()
            execute_result.fetchone = MagicMock(return_value=None)
            s = AsyncMock()
            s.execute = AsyncMock(return_value=execute_result)
            s.commit = AsyncMock()
            yield s

        @asynccontextmanager
        async def _verify_ctx():
            raise OSError("DB verify connection refused")
            yield  # never reached

        call_count = [0]

        @asynccontextmanager
        async def _factory():
            call_count[0] += 1
            if call_count[0] == 1:
                async with _main_ctx() as s:
                    yield s
            else:
                async with _verify_ctx() as s:
                    yield s

        with patch("agents.owner_console.async_session_factory", new=_factory), \
             patch("agents.owner_console.job_registry_start", new=AsyncMock()), \
             patch("agents.owner_console.job_registry_finish", new=AsyncMock()), \
             patch("db_update_agent.run_task", new=AsyncMock(return_value=fake_report)):
            result = await _oc2._trigger_task("dedup_catalog_parts")

        self.assertIn("NOT_VERIFIED", result,
                      f"Verifier exception must yield NOT_VERIFIED, got: {result!r}")


# ── F. Task exception → FAILURE never SUCCESS ─────────────────────────────────
class TestDeterministicTaskException(unittest.IsolatedAsyncioTestCase):
    """
    run_task() raises an exception.
    Expected: the result contains ❌ and no execution success claim.
    """

    async def test_task_exception_returns_failure_not_success(self):
        """Exception from run_task → ❌ message, never [VERIFIED]."""
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def _factory():
            from unittest.mock import AsyncMock, MagicMock
            s = AsyncMock()
            s.execute = AsyncMock(return_value=MagicMock(fetchone=MagicMock(return_value=None)))
            s.commit = AsyncMock()
            yield s

        with patch("agents.owner_console.async_session_factory", new=_factory), \
             patch("agents.owner_console.job_registry_start", new=AsyncMock()), \
             patch("db_update_agent.run_task", new=AsyncMock(side_effect=RuntimeError("DB exploded"))):
            result = await _oc2._trigger_task("normalize_part_types")

        self.assertIn("❌", result)
        self.assertNotIn("[VERIFIED]", result)
        self.assertNotIn("NOT_VERIFIED", result)   # a clear failure, not ambiguous


# ── G. Partial execution — status ok but job_finish not written ───────────────
class TestDeterministicPartialExecution(unittest.IsolatedAsyncioTestCase):
    """
    Simulate: task succeeds (status=ok) but job_registry_finish raises so no
    completion row is ever written. Read-back finds nothing. Expected: NOT_VERIFIED.
    """

    async def test_finish_exception_returns_not_verified(self):
        """If job_registry_finish raises, the verify step finds no row → NOT_VERIFIED."""
        from contextlib import asynccontextmanager

        fake_report = {"task": "normalize_categories", "status": "ok", "updated": 200}

        @asynccontextmanager
        async def _main_ctx():
            from unittest.mock import AsyncMock, MagicMock
            s = AsyncMock()
            s.execute = AsyncMock(return_value=MagicMock(fetchone=MagicMock(return_value=None)))
            s.commit = AsyncMock()
            yield s

        @asynccontextmanager
        async def _verify_ctx():
            from unittest.mock import AsyncMock, MagicMock
            s = AsyncMock()
            s.execute = AsyncMock(return_value=MagicMock(fetchone=MagicMock(return_value=None)))
            s.commit = AsyncMock()
            yield s

        call_count = [0]

        @asynccontextmanager
        async def _factory():
            call_count[0] += 1
            if call_count[0] == 1:
                async with _main_ctx() as s:
                    yield s
            else:
                async with _verify_ctx() as s:
                    yield s

        with patch("agents.owner_console.async_session_factory", new=_factory), \
             patch("agents.owner_console.job_registry_start", new=AsyncMock()), \
             patch("agents.owner_console.job_registry_finish",
                   new=AsyncMock(side_effect=RuntimeError("write failed"))), \
             patch("db_update_agent.run_task", new=AsyncMock(return_value=fake_report)):
            result = await _oc2._trigger_task("normalize_categories")

        self.assertIn("NOT_VERIFIED", result)


# ── H. Fabricated task result — job_registry_start also failed ───────────────
class TestDeterministicFabricatedResult(unittest.IsolatedAsyncioTestCase):
    """
    Task returns a fabricated success dict with invented counts.
    job_registry_start failed silently, so no start row, no finish row.
    Read-back finds nothing. Expected: NOT_VERIFIED.
    """

    async def test_fabricated_result_with_no_registry_row_not_verified(self):
        """start fails + fabricated ok → read-back empty → NOT_VERIFIED."""
        from contextlib import asynccontextmanager

        fabricated = {"task": "fill_car_brands", "status": "ok",
                      "inserted": 99999, "note": "fabricated by attacker"}

        @asynccontextmanager
        async def _factory():
            from unittest.mock import AsyncMock, MagicMock
            s = AsyncMock()
            s.execute = AsyncMock(return_value=MagicMock(fetchone=MagicMock(return_value=None)))
            s.commit = AsyncMock()
            yield s

        with patch("agents.owner_console.async_session_factory", new=_factory), \
             patch("agents.owner_console.job_registry_start",
                   new=AsyncMock(side_effect=Exception("start write failed"))), \
             patch("agents.owner_console.job_registry_finish", new=AsyncMock()), \
             patch("db_update_agent.run_task", new=AsyncMock(return_value=fabricated)):
            result = await _oc2._trigger_task("fill_car_brands")

        self.assertIn("NOT_VERIFIED", result)


# ── I. Repeated execution — second run uses its own jid ──────────────────────
class TestDeterministicRepeatedExecution(unittest.IsolatedAsyncioTestCase):
    """
    Two consecutive calls to _trigger_task with the same task name.
    Each run generates its own unique jid. The second run's verification must
    check its own jid, not accidentally validate against the first run's stale row.
    """

    async def test_each_run_uses_unique_jid(self):
        """Two calls must produce two distinct jids (uuid-backed suffix)."""
        captured_jids = []

        original_start = _oc2.job_registry_start

        async def _capture_start(db, name, ttl_seconds, *, job_id=None, **kw):
            captured_jids.append(job_id)

        from contextlib import asynccontextmanager
        call_count = [0]
        fake_report = {"task": "normalize_part_types", "status": "ok", "updated": 1}

        @asynccontextmanager
        async def _factory():
            call_count[0] += 1
            from unittest.mock import AsyncMock, MagicMock
            s = AsyncMock()
            # alternating: main sessions return None, verify sessions return a row
            if call_count[0] % 2 == 1:  # main session
                s.execute = AsyncMock(return_value=MagicMock(fetchone=MagicMock(return_value=None)))
            else:  # verify session
                s.execute = AsyncMock(return_value=MagicMock(fetchone=MagicMock(return_value=_make_fake_row())))
            s.commit = AsyncMock()
            yield s

        with patch("agents.owner_console.async_session_factory", new=_factory), \
             patch("agents.owner_console.job_registry_start", new=_capture_start), \
             patch("agents.owner_console.job_registry_finish", new=AsyncMock()), \
             patch("db_update_agent.run_task", new=AsyncMock(return_value=fake_report)):
            await _oc2._trigger_task("normalize_part_types")
            await _oc2._trigger_task("normalize_part_types")

        self.assertEqual(len(captured_jids), 2)
        self.assertNotEqual(captured_jids[0], captured_jids[1],
                            "Each _trigger_task call must produce a unique jid")
        self.assertTrue(all(j.startswith("owner_trigger:normalize_part_types:") for j in captured_jids))


# ── J. Conversational regression — existing 27/27 remain green ───────────────
class TestConversationalRegression(unittest.TestCase):
    """
    Deterministic _trigger_task changes must not break the conversational gate.
    These tests re-verify the guard function that was proven in Part 1.
    """

    def test_hf_text_execution_claim_still_rejected(self):
        """הפעלתי in a conversational reply is still rejected."""
        result = _guard_avi_conversational_reply("הפעלתי את המשימה.")
        self.assertTrue(_is_not_verified(result))

    def test_factual_status_reply_still_passes(self):
        """Factual status reply (no verb) still passes through unchanged."""
        reply = "השאיבה כרגע פעילה, 3.5M חלקים בקטלוג."
        result = _guard_avi_conversational_reply(reply)
        self.assertEqual(result, reply)

    def test_not_verified_reply_still_clean(self):
        """_NOT_VERIFIED_REPLY still contains no execution claim verbs."""
        self.assertFalse(_EXECUTION_CLAIM_RE.search(_NOT_VERIFIED_REPLY))

    def test_english_launched_still_rejected(self):
        """'I launched' in English still intercepted."""
        result = _guard_avi_conversational_reply("I launched the scraper.")
        self.assertTrue(_is_not_verified(result))


# ── Deterministic invariant ───────────────────────────────────────────────────
class TestDeterministicInvariant(unittest.IsolatedAsyncioTestCase):
    """
    FOR THE DETERMINISTIC PATH:
        SUCCESS/[VERIFIED] is allowed ONLY IF the job_registry read-back row is present.
    Prove that status==ok + row absent → NOT_VERIFIED (0 allowed paths to SUCCESS).
    Prove that status==ok + row present → [VERIFIED].
    """

    async def _run_with_verify_result(self, row):
        """Helper: run _trigger_task with controlled verify SELECT return value."""
        from contextlib import asynccontextmanager
        fake_report = {"task": "normalize_part_types", "status": "ok", "updated": 1}
        call_count = [0]

        @asynccontextmanager
        async def _factory():
            call_count[0] += 1
            from unittest.mock import AsyncMock, MagicMock
            s = AsyncMock()
            s.execute = AsyncMock(return_value=MagicMock(fetchone=MagicMock(return_value=row)))
            s.commit = AsyncMock()
            yield s

        with patch("agents.owner_console.async_session_factory", new=_factory), \
             patch("agents.owner_console.job_registry_start", new=AsyncMock()), \
             patch("agents.owner_console.job_registry_finish", new=AsyncMock()), \
             patch("db_update_agent.run_task", new=AsyncMock(return_value=fake_report)):
            return await _oc2._trigger_task("normalize_part_types")

    async def test_ok_without_row_is_not_verified(self):
        """status==ok + verify returns None → NOT_VERIFIED."""
        result = await self._run_with_verify_result(None)
        self.assertIn("NOT_VERIFIED", result)
        self.assertNotIn("[VERIFIED]", result)

    async def test_ok_with_row_is_verified(self):
        """status==ok + verify returns row → [VERIFIED]."""
        result = await self._run_with_verify_result(_make_fake_row())
        self.assertIn("[VERIFIED]", result)
        self.assertNotIn("NOT_VERIFIED", result)

    async def test_success_without_verification_is_zero_paths(self):
        """No path through _trigger_task can emit [VERIFIED] without the row."""
        # Exhaustive: None → must never reach [VERIFIED]
        result = await self._run_with_verify_result(None)
        self.assertNotIn("[VERIFIED]", result,
                         "SUCCESS without verification: 0 allowed paths — invariant violated!")


# ── SUMMARY ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for cls in [
        # Part 1 — conversational gate (original 27 tests)
        TestToolSuccessButStateUnchanged,
        TestHTTP200ButNotComplete,
        TestToolThrowsAfterSideEffect,
        TestLLMClaimsSuccessWithoutTool,
        TestFabricatedToolResult,
        TestWrongResourceID,
        TestPartialMultiStepTask,
        TestVerificationFails,
        TestStaleRead,
        TestFinalLayerSuccessInjection,
        TestFollowUpConfirmation,
        TestErrorPreservation,
        TestInvariant,
        # Part 2 — deterministic _trigger_task post-condition gate (new tests A–J)
        TestDeterministicFakeSuccess,
        TestDeterministicVerifiedSuccess,
        TestDeterministicWrongResource,
        TestDeterministicStaleState,
        TestDeterministicVerifierException,
        TestDeterministicTaskException,
        TestDeterministicPartialExecution,
        TestDeterministicFabricatedResult,
        TestDeterministicRepeatedExecution,
        TestConversationalRegression,
        TestDeterministicInvariant,
    ]:
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
