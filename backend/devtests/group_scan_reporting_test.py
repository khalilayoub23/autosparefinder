"""
group_scan_reporting_test.py — regression for the Facebook group-scan
owner-facing reporting contract (2026-09-19).

Root cause: `_group_scan_loop()` (BACKEND_API_ROUTES.py) read
`result.data.get("groups_scanned", 0)` — a key `facebook_group_scan()`
(social/tools.py) never sets (it returns groups_selected/groups_attempted/
groups_fetched instead) — so the owner-facing "N groups scanned" message
always silently showed 0. The loop also never checked
`result.data["session_failed"]`, so an authentication failure fell through
to the same "no discoveries found" branch and was reported as an ordinary
empty scan, masking a real outage as a quiet day.

Importing BACKEND_API_ROUTES.py directly (the full production FastAPI
entrypoint) hangs in this environment on module-level startup side effects
unrelated to this fix — the same reason every other regression suite in
this project tests narrower, focused modules rather than that one. This
suite instead verifies the fix at the SOURCE level: it reads the actual
function text from disk and asserts the specific structural properties
that constitute the fix, without executing the module. This mirrors the
precedent already used successfully in fb_dom_readiness_regression_test.py
for JS content that likewise cannot be executed in this environment
(TestSharedClassifierConsistency, TestMembersLinkAncestorWalk).
"""

import re
import sys
import unittest

sys.path.insert(0, "/app")

_SOURCE_PATH = "/app/BACKEND_API_ROUTES.py"


def _extract_function_source(func_name: str) -> str:
    """Extract one top-level `async def <func_name>(...):` function's full
    body from the file by tracking indentation, without importing the
    module. Returns the function source including its signature line."""
    with open(_SOURCE_PATH, encoding="utf-8") as f:
        lines = f.readlines()

    start = None
    for i, line in enumerate(lines):
        if re.match(rf"^async def {re.escape(func_name)}\(", line):
            start = i
            break
    if start is None:
        raise AssertionError(f"Could not find function '{func_name}' in {_SOURCE_PATH}")

    end = len(lines)
    for j in range(start + 1, len(lines)):
        line = lines[j]
        if line.strip() == "":
            continue
        # A line starting at column 0 (no leading whitespace) that isn't
        # part of a decorator/blank run marks the next top-level statement.
        if not line.startswith((" ", "\t")):
            end = j
            break
    return "".join(lines[start:end])


class TestGroupsScannedKeyFix(unittest.TestCase):
    """The old code always read a non-existent key and silently showed 0."""

    @classmethod
    def setUpClass(cls):
        cls.source = _extract_function_source("_group_scan_loop")

    def test_no_longer_reads_nonexistent_groups_scanned_key(self):
        # The bug: reading a key facebook_group_scan() never sets.
        self.assertNotIn('data.get("groups_scanned"', self.source)

    def test_reads_groups_attempted_instead(self):
        # groups_attempted is the correct semantic: incremented per-group
        # before the scan attempt, regardless of later success/failure —
        # the true count of scanning work performed. Not groups_selected
        # (eligible input size, before any attempt) or groups_fetched
        # (stricter — excludes attempted-but-errored groups).
        self.assertIn('groups_scanned = result.data.get("groups_attempted", 0)', self.source)


class TestSessionFailedNoLongerMasked(unittest.TestCase):
    """The old code never checked session_failed -- an auth failure was
    silently reported as an ordinary empty scan."""

    @classmethod
    def setUpClass(cls):
        cls.source = _extract_function_source("_group_scan_loop")

    def test_session_failed_is_read_from_result_data(self):
        self.assertIn('session_failed = bool(result.data.get("session_failed"))', self.source)

    def test_session_failed_branch_exists_before_empty_discoveries_branch(self):
        # The auth-failure check must be an `if` that is evaluated before
        # (or as an elif chain ahead of) the "not discoveries" branch, so
        # it takes priority -- an auth failure also has empty discoveries.
        if_failed = self.source.find("if session_failed:")
        elif_empty = self.source.find("elif not discoveries:")
        self.assertNotEqual(if_failed, -1, "session_failed check not found")
        self.assertNotEqual(elif_empty, -1, "'elif not discoveries' branch not found")
        self.assertLess(if_failed, elif_empty, "session_failed branch must come before the empty-discoveries branch")

    def test_session_failed_uses_a_distinct_alert_key(self):
        self.assertIn('alert_key="group_scan_auth_failed"', self.source)
        # Must not reuse the ordinary empty-scan alert key -- that would
        # collapse back into the old masking behavior via cooldown collision.
        auth_block_start = self.source.find("if session_failed:")
        empty_block_start = self.source.find("elif not discoveries:")
        auth_block = self.source[auth_block_start:empty_block_start]
        self.assertNotIn('alert_key="group_scan_empty"', auth_block)

    def test_session_failed_uses_error_severity(self):
        auth_block_start = self.source.find("if session_failed:")
        empty_block_start = self.source.find("elif not discoveries:")
        auth_block = self.source[auth_block_start:empty_block_start]
        self.assertIn('severity="error"', auth_block)


class TestDiscoveriesPathUnaffected(unittest.TestCase):
    """The existing discoveries-found reporting path must be untouched."""

    @classmethod
    def setUpClass(cls):
        cls.source = _extract_function_source("_group_scan_loop")

    def test_discoveries_branch_still_present_and_unchanged_in_shape(self):
        self.assertIn('alert_key=f"group_scan_discoveries_{_disc_fp}"', self.source)
        self.assertIn("תגובות ממתינות", self.source)

    def test_empty_scan_message_still_uses_groups_scanned_variable(self):
        # The display variable name is preserved (only its source key
        # changed) -- the Hebrew message text and alert_key are untouched.
        self.assertIn("סרקנו *{groups_scanned}*", self.source)
        self.assertIn('alert_key="group_scan_empty"', self.source)


class TestFacebookGroupScanContract(unittest.TestCase):
    """Confirms the actual contract facebook_group_scan() exposes, so this
    test suite itself stays honest about what the fix reads from."""

    @classmethod
    def setUpClass(cls):
        with open("/app/social/tools.py", encoding="utf-8") as f:
            cls.tools_source = f.read()

    def test_facebook_group_scan_never_sets_groups_scanned(self):
        # Confirms the root cause: this key genuinely does not exist in the
        # tool's output, so reading it can never work regardless of value.
        # (A loose substring check — deliberately not scoped to one function
        # — so this fails loudly if a future change ever introduces it
        # elsewhere in the file too, which would make groups_scanned viable
        # again and this whole fix worth revisiting.)
        self.assertNotIn('"groups_scanned"', self.tools_source)

    def test_facebook_group_scan_sets_groups_attempted(self):
        self.assertIn('"groups_attempted": groups_attempted', self.tools_source)

    def test_facebook_group_scan_sets_session_failed_on_auth_failure(self):
        self.assertIn('"session_failed": True', self.tools_source)


if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for cls in (
        TestGroupsScannedKeyFix,
        TestSessionFailedNoLongerMasked,
        TestDiscoveriesPathUnaffected,
        TestFacebookGroupScanContract,
    ):
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    total = result.testsRun
    failures = len(result.failures) + len(result.errors)
    passed = total - failures

    print()
    print("=" * 70)
    print(f"group_scan_reporting_test.py: {passed}/{total} PASS")
    print("=" * 70)

    sys.exit(0 if failures == 0 else 1)
