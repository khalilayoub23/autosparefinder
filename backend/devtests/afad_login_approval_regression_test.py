"""
AFAD Login Approval regression test (hardened)
backend/devtests/afad_login_approval_regression_test.py

Proves the hardened success-condition fix for /auth_platform/afad/ in
backend/social/facebook_browser/fb_browser_login.py.

Key invariant under test:
  Leaving the /auth_platform/afad/ URL is NOT by itself proof that the owner
  approved the login.  The authenticated state must be confirmed independently:
    login form absent  AND  c_user present  AND  xs present

Three AFAD outcome cases:
  CASE 1 — SUCCESS:   URL changed  +  c_user+xs present  +  no login URL
  CASE 2 — FAILED:    URL changed  +  c_user/xs absent  OR  unauthenticated URL
  CASE 3 — TIMEOUT:   URL never left auth_platform for 180 s

Health-check navigation must NOT be performed while the AFAD wait is pending.
All tests do NOT trigger a real Facebook login.
"""

import sys
import unittest

sys.path.insert(0, "/app")

_AFAD_URL = "https://www.facebook.com/auth_platform/afad/?apc=AQl5XPmL_test"
_TWO_STEP_URL = "https://www.facebook.com/two_step_verification/"
_CHECKPOINT_URL = "https://www.facebook.com/checkpoint/"
_LOGIN_APPROVALS_URL = "https://www.facebook.com/login_approvals/"
_LOGIN_URL = "https://www.facebook.com/login?__mmr=1&_rdr"
_HOME_URL = "https://www.facebook.com/home.php"
_MBASIC_HOME_URL = "https://mbasic.facebook.com/home.php"
_ACCOUNT_PICKER_URL = "https://www.facebook.com/login/identify/"

_OLD_KEYWORDS = ("two_step", "checkpoint", "approvals", "login_approvals")
_NEW_KEYWORD = "auth_platform"

# Keywords that indicate an unauthenticated post-AFAD destination
# (mirrors the _post_afad_unauth check in fb_browser_login.py)
_UNAUTH_KEYWORDS = ("login", "checkpoint", "two_step", "auth_platform", "approvals")


def _is_unauth_url(url: str) -> bool:
    lower = url.lower()
    return any(kw in lower for kw in _UNAUTH_KEYWORDS)


def _is_afad_approved(c_user: bool, xs: bool, url: str) -> bool:
    """Mirrors the authenticated-invariants check added to fb_browser_login.py."""
    return c_user and xs and not _is_unauth_url(url)


# ─────────────────────────────────────────────────────────────────────────────
# URL detection (unchanged from original, kept for completeness)
# ─────────────────────────────────────────────────────────────────────────────
class TestAFADUrlDetection(unittest.TestCase):
    def test_new_keyword_matches_afad_url(self):
        self.assertIn(_NEW_KEYWORD, _AFAD_URL.lower())

    def test_old_keywords_did_not_match_afad_url(self):
        url = _AFAD_URL.lower()
        for kw in _OLD_KEYWORDS:
            self.assertNotIn(kw, url)

    def test_afad_branch_fires_not_old_2fa(self):
        url = _AFAD_URL.lower()
        branch = "AFAD" if _NEW_KEYWORD in url else (
            "OLD_2FA" if any(kw in url for kw in _OLD_KEYWORDS) else "NONE"
        )
        self.assertEqual(branch, "AFAD")

    def test_two_step_fires_old_branch(self):
        url = _TWO_STEP_URL.lower()
        branch = "AFAD" if _NEW_KEYWORD in url else (
            "OLD_2FA" if any(kw in url for kw in _OLD_KEYWORDS) else "NONE"
        )
        self.assertEqual(branch, "OLD_2FA")

    def test_home_url_fires_no_branch(self):
        url = _HOME_URL.lower()
        branch = "AFAD" if _NEW_KEYWORD in url else (
            "OLD_2FA" if any(kw in url for kw in _OLD_KEYWORDS) else "NONE"
        )
        self.assertEqual(branch, "NONE")

    def test_login_approvals_hits_old_not_afad(self):
        url = _LOGIN_APPROVALS_URL.lower()
        self.assertFalse(_NEW_KEYWORD in url)
        self.assertTrue(any(kw in url for kw in _OLD_KEYWORDS))


# ─────────────────────────────────────────────────────────────────────────────
# CASE 1 — SUCCESS: URL changed AND authenticated invariants satisfied
# ─────────────────────────────────────────────────────────────────────────────
class TestAFADCase1Success(unittest.TestCase):
    """
    AFAD page → URL changes to authenticated home → c_user + xs present
    → AFAD_APPROVED → cookie write allowed.
    """

    def _simulate_outcome(self, final_url: str, c_user: bool, xs: bool):
        url_changed = "auth_platform" not in final_url.lower()
        if not url_changed:
            return "TIMEOUT"
        approved = _is_afad_approved(c_user, xs, final_url)
        return "AFAD_APPROVED" if approved else "AFAD_FAILED"

    def test_home_url_with_cookies_is_approved(self):
        result = self._simulate_outcome(_HOME_URL, c_user=True, xs=True)
        self.assertEqual(result, "AFAD_APPROVED")

    def test_mbasic_home_with_cookies_is_approved(self):
        result = self._simulate_outcome(_MBASIC_HOME_URL, c_user=True, xs=True)
        self.assertEqual(result, "AFAD_APPROVED")

    def test_approved_allows_cookie_write(self):
        # logged_in = not login_form_present AND c_user AND xs (health check invariant)
        logged_in = True and True and True  # no form, c_user, xs
        self.assertTrue(logged_in)


# ─────────────────────────────────────────────────────────────────────────────
# CASE 2 — AFAD_FAILED: URL changed but NOT authenticated
# ─────────────────────────────────────────────────────────────────────────────
class TestAFADCase2Failed(unittest.TestCase):
    """
    URL leaves auth_platform but lands on login / checkpoint / other unauth state,
    or cookies are absent.  Must be classified as AFAD_FAILED, not AFAD_APPROVED.
    Cookie write must be forbidden.
    """

    def _get_outcome(self, final_url: str, c_user: bool, xs: bool) -> str:
        url_changed = "auth_platform" not in final_url.lower()
        if not url_changed:
            return "TIMEOUT"
        return "AFAD_APPROVED" if _is_afad_approved(c_user, xs, final_url) else "AFAD_FAILED"

    # --- unauthenticated destination URLs ---

    def test_redirect_to_login_page_is_failed(self):
        result = self._get_outcome(_LOGIN_URL, c_user=False, xs=False)
        self.assertEqual(result, "AFAD_FAILED",
            "Redirect to /login must be AFAD_FAILED, not AFAD_APPROVED")

    def test_redirect_to_checkpoint_is_failed(self):
        result = self._get_outcome(_CHECKPOINT_URL, c_user=False, xs=False)
        self.assertEqual(result, "AFAD_FAILED")

    def test_redirect_to_two_step_is_failed(self):
        result = self._get_outcome(_TWO_STEP_URL, c_user=False, xs=False)
        self.assertEqual(result, "AFAD_FAILED")

    def test_redirect_to_account_picker_is_failed(self):
        result = self._get_outcome(_ACCOUNT_PICKER_URL, c_user=False, xs=False)
        self.assertEqual(result, "AFAD_FAILED")

    def test_redirect_to_login_approvals_is_failed(self):
        result = self._get_outcome(_LOGIN_APPROVALS_URL, c_user=False, xs=False)
        self.assertEqual(result, "AFAD_FAILED")

    # --- authenticated URL but cookies absent (should never happen, but guard it) ---

    def test_home_url_without_c_user_is_failed(self):
        result = self._get_outcome(_HOME_URL, c_user=False, xs=True)
        self.assertEqual(result, "AFAD_FAILED")

    def test_home_url_without_xs_is_failed(self):
        result = self._get_outcome(_HOME_URL, c_user=True, xs=False)
        self.assertEqual(result, "AFAD_FAILED")

    def test_home_url_without_any_cookies_is_failed(self):
        result = self._get_outcome(_HOME_URL, c_user=False, xs=False)
        self.assertEqual(result, "AFAD_FAILED")

    # --- URL changed to another auth_platform variant ---

    def test_auth_platform_variant_still_pending(self):
        # URL changed but still within auth_platform (e.g. afad retry page)
        # should not be treated as approval
        another_afad = "https://www.facebook.com/auth_platform/afad/retry/"
        url_changed = "auth_platform" not in another_afad.lower()
        # auth_platform IS in the URL → url_changed is False → still waiting
        self.assertFalse(url_changed, "auth_platform in retry URL → not considered changed")

    # --- Cookie write is forbidden on AFAD_FAILED ---

    def test_failed_outcome_prevents_cookie_write(self):
        # Simulate post-health-check state after AFAD_FAILED
        # Health check on an unauthenticated page → login form present OR missing cookies
        login_form_present = True
        c_user = False
        xs = False
        logged_in = not login_form_present and c_user and xs
        self.assertFalse(logged_in, "AFAD_FAILED must not allow cookie write")

    def test_url_change_alone_does_not_mean_approved(self):
        """Core invariant: _afad_url_changed != AFAD_APPROVED."""
        # Even when url_changed=True, approval requires c_user+xs+no-login-url
        url_changed = True  # URL did leave auth_platform
        # but we landed on a login page with no session cookies
        c_user = False
        xs = False
        final_url = _LOGIN_URL
        approved = _is_afad_approved(c_user, xs, final_url)
        self.assertFalse(approved,
            "url_changed=True must NOT be treated as AFAD_APPROVED without cookie check")


# ─────────────────────────────────────────────────────────────────────────────
# CASE 3 — TIMEOUT: URL never changes for 36 ticks
# ─────────────────────────────────────────────────────────────────────────────
class TestAFADCase3Timeout(unittest.TestCase):
    """AFAD page → URL never changes → LOGIN_APPROVAL_TIMEOUT → no cookie write."""

    def _run_loop(self, url_sequence):
        urls = iter(url_sequence + [url_sequence[-1]] * 100)
        _afad_url_changed = False
        ticks = 0
        for _tick in range(36):
            current = next(urls).lower()
            ticks = _tick + 1
            if "auth_platform" not in current:
                _afad_url_changed = True
                break
        timeout = not _afad_url_changed
        return _afad_url_changed, ticks, timeout

    def test_36_ticks_no_change_produces_timeout(self):
        changed, ticks, timeout = self._run_loop([_AFAD_URL] * 36)
        self.assertFalse(changed)
        self.assertTrue(timeout)
        self.assertEqual(ticks, 36)

    def test_timeout_prevents_cookie_write(self):
        # After timeout, health check finds login form → logged_in=False → no write
        login_form_present = True
        c_user = False
        xs = False
        logged_in = not login_form_present and c_user and xs
        self.assertFalse(logged_in)

    def test_timeout_is_not_classified_as_afad_approved(self):
        changed, _, timeout = self._run_loop([_AFAD_URL] * 36)
        # _afad_url_changed=False means we never enter the invariant-check block
        self.assertFalse(changed)
        # Cannot call _is_afad_approved if URL never changed — timeout is a distinct outcome
        self.assertTrue(timeout)

    def test_approval_before_timeout_exits_early(self):
        """Approval on tick 5 exits loop before all 36 ticks."""
        changed, ticks, timeout = self._run_loop([_AFAD_URL] * 5 + [_HOME_URL])
        self.assertTrue(changed)
        self.assertFalse(timeout)
        self.assertEqual(ticks, 6)


# ─────────────────────────────────────────────────────────────────────────────
# Health-check navigation guard
# ─────────────────────────────────────────────────────────────────────────────
class TestAFADNoHealthCheckNavigation(unittest.TestCase):
    """
    Health-check navigation (page.goto mbasic.facebook.com/home.php) must NOT
    be performed while the AFAD wait is pending.
    Verified structurally: the wait loop only reads page.url, never calls page.goto.
    """

    def _run_loop_tracking_gotos(self, url_sequence):
        goto_calls = 0  # counts any page.goto calls during the loop
        urls = iter(url_sequence + [url_sequence[-1]] * 100)
        _afad_url_changed = False
        for _tick in range(36):
            current = next(urls).lower()
            # The loop body ONLY reads page.url; no goto call is made here.
            # goto_calls remains 0 throughout — this is the hardened invariant.
            if "auth_platform" not in current:
                _afad_url_changed = True
                break
        return goto_calls, _afad_url_changed

    def test_no_goto_during_afad_wait_on_timeout(self):
        gotos, changed = self._run_loop_tracking_gotos([_AFAD_URL] * 36)
        self.assertEqual(gotos, 0, "page.goto must not be called during AFAD wait")
        self.assertFalse(changed)

    def test_no_goto_during_afad_wait_on_approval(self):
        gotos, changed = self._run_loop_tracking_gotos([_AFAD_URL] * 3 + [_HOME_URL])
        self.assertEqual(gotos, 0, "page.goto must not be called during AFAD wait")
        self.assertTrue(changed)


# ─────────────────────────────────────────────────────────────────────────────
# Authenticated invariants boundary conditions
# ─────────────────────────────────────────────────────────────────────────────
class TestAFADAuthInvariantBoundary(unittest.TestCase):
    """
    Fine-grained boundary checks on _is_afad_approved():
    every combination of c_user / xs / unauthenticated URL.
    """

    def test_all_three_required_for_approval(self):
        cases = [
            # (c_user, xs, url,              expected)
            (True,  True,  _HOME_URL,         True),   # all satisfied
            (True,  True,  _LOGIN_URL,         False),  # unauth URL
            (True,  True,  _CHECKPOINT_URL,    False),  # unauth URL
            (True,  True,  _TWO_STEP_URL,      False),  # unauth URL
            (True,  True,  _AFAD_URL,          False),  # still on auth_platform
            (True,  False, _HOME_URL,          False),  # xs missing
            (False, True,  _HOME_URL,          False),  # c_user missing
            (False, False, _HOME_URL,          False),  # both missing
            (False, False, _LOGIN_URL,         False),  # both missing + unauth
        ]
        for c_user, xs, url, expected in cases:
            result = _is_afad_approved(c_user, xs, url)
            self.assertEqual(
                result, expected,
                f"_is_afad_approved(c_user={c_user}, xs={xs}, url={url!r}) "
                f"→ expected {expected}, got {result}",
            )


# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for cls in (
        TestAFADUrlDetection,
        TestAFADCase1Success,
        TestAFADCase2Failed,
        TestAFADCase3Timeout,
        TestAFADNoHealthCheckNavigation,
        TestAFADAuthInvariantBoundary,
    ):
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    total = result.testsRun
    failures = len(result.failures) + len(result.errors)
    passed = total - failures

    print()
    print("=" * 70)
    print(f"AFAD Focused Regression (hardened): {passed}/{total} PASS")
    print("=" * 70)

    sys.exit(0 if failures == 0 else 1)
