"""
noa_group_draft_handoff_test.py — regression for the scanner→NOA draft
persistence handoff (2026-09-19, /goal "close NOA Facebook post handling").

Root cause: the automatic scheduled path (_group_scan_loop() ->
facebook_group_scan() -> GroupAgent.scan_groups()) returns discovery dicts
that NEVER include a "draft_comment" key, and nothing in that call chain
ever called _save_draft() to persist a row into group_comment_drafts. The
owner-facing WhatsApp message nonetheless told the owner to approve via
"תגובות-גרופ"/"אשרתגובה <id>" — commands that operate on
group_comment_drafts, which had zero rows from this path. A bridge function
clearly written for exactly this purpose,
social/campaign_manager.py::create_group_discovery_tasks(), was confirmed
(via repo-wide grep) to have ZERO callers anywhere — dead code.

Fix: _group_scan_loop() now drafts (draft_group_comment()) and persists
(_save_draft(), the same function already used by
group_scanner.run_group_scanner() and routes/system.py's
ingest_group_posts endpoint — no duplicate logic) each discovery whose
suggested_action is "comment" or "post" (score >= 0.4), populating
discovery["draft_comment"] in place so the pre-existing notification code
(which already read d.get("draft_comment", "(אין)")) starts working
correctly with no changes needed there.

As with group_scan_reporting_test.py, importing BACKEND_API_ROUTES.py
directly hangs in this environment on unrelated startup side effects, so
this suite verifies the fix at the source-text level.
"""

import re
import sys
import unittest

sys.path.insert(0, "/app")

_ROUTES_PATH = "/app/BACKEND_API_ROUTES.py"


def _extract_function_source(func_name: str, path: str = _ROUTES_PATH) -> str:
    with open(path, encoding="utf-8") as f:
        lines = f.readlines()
    start = None
    for i, line in enumerate(lines):
        if re.match(rf"^async def {re.escape(func_name)}\(", line):
            start = i
            break
    if start is None:
        raise AssertionError(f"Could not find function '{func_name}' in {path}")
    end = len(lines)
    for j in range(start + 1, len(lines)):
        line = lines[j]
        if line.strip() == "":
            continue
        if not line.startswith((" ", "\t")):
            end = j
            break
    return "".join(lines[start:end])


class TestDraftPersistHandoffPresent(unittest.TestCase):
    """The handoff must exist at all -- the confirmed root defect was that
    it never ran."""

    @classmethod
    def setUpClass(cls):
        cls.source = _extract_function_source("_group_scan_loop")

    def test_calls_draft_group_comment(self):
        self.assertIn("draft_group_comment(d)", self.source)

    def test_calls_save_draft(self):
        self.assertIn("_save_group_draft(", self.source)

    def test_reuses_existing_save_draft_not_a_reimplementation(self):
        # Must import the SAME _save_draft already used by
        # group_scanner.run_group_scanner() and routes/system.py's
        # ingest_group_posts -- not a duplicated INSERT statement.
        self.assertIn(
            "from social.facebook_browser.group_scanner import _save_draft as _save_group_draft",
            self.source,
        )

    def test_populates_draft_comment_key_in_place(self):
        # The pre-existing notification code already reads
        # d.get("draft_comment", "(אין)") -- the fix must populate exactly
        # that key on the discovery dict, not introduce a new one.
        self.assertIn('d["draft_comment"] = draft_text', self.source)

    def test_only_drafts_high_confidence_suggested_actions(self):
        # suggested_action is "comment" only for score >= 0.4 (vs "monitor"
        # for 0.2-0.4) -- the fix must respect that existing distinction
        # rather than draft+persist every discovery indiscriminately.
        self.assertIn('d.get("suggested_action") not in ("comment", "post")', self.source)

    def test_handoff_runs_before_notification_branching(self):
        # The draft/persist step must run before the if/elif/else that
        # builds the WhatsApp message, so draft_comment is already
        # populated by the time that code reads it.
        handoff_pos = self.source.find("_save_group_draft(")
        notify_branch_pos = self.source.find("if session_failed:")
        self.assertGreater(handoff_pos, 0)
        self.assertGreater(notify_branch_pos, 0)
        self.assertLess(handoff_pos, notify_branch_pos)

    def test_handoff_failure_does_not_crash_the_loop(self):
        # A drafting/persistence failure must not prevent the scan cycle
        # from still reporting discoveries to the owner.
        handoff_start = self.source.find("if discoveries:")
        handoff_end = self.source.find("groups_scanned = result.data.get")
        handoff_block = self.source[handoff_start:handoff_end]
        self.assertIn("except Exception", handoff_block)


class TestDeadCodeBridgeDocumented(unittest.TestCase):
    """Confirms the root-cause evidence: the bridge function that should
    have done this already exists but is never called. This test exists so
    a future change that starts calling create_group_discovery_tasks()
    elsewhere doesn't silently create a SECOND, redundant draft path --
    if this test ever fails, that's a signal to reconcile the two."""

    def test_create_group_discovery_tasks_still_has_zero_callers(self):
        import subprocess
        result = subprocess.run(
            ["grep", "-rn", "create_group_discovery_tasks", "/app"],
            capture_output=True, text=True,
        )
        call_sites = [
            line for line in result.stdout.splitlines()
            if "def create_group_discovery_tasks" not in line
            and "__pycache__" not in line
            and "noa_group_draft_handoff_test.py" not in line
            # Exclude comment lines -- this fix's own docstring/inline comments
            # reference the function BY NAME as root-cause evidence, which is
            # not a call site. A real call is code, not a "#"-prefixed line.
            and re.match(r"^[^:]+:\d+:\s*#", line) is None
        ]
        self.assertEqual(
            call_sites, [],
            "create_group_discovery_tasks() now has a caller -- if this is "
            "intentional, reconcile it with _group_scan_loop()'s own "
            "draft+persist handoff (this fix) to avoid double-drafting the "
            "same discovery.",
        )


class TestSaveDraftDedupContract(unittest.TestCase):
    """Confirms the DB-level guarantee this fix relies on for dedup: the
    same post_url can never produce two draft rows while a comment for it
    is pending/approved/already posted, regardless of how many times the
    post is rescanned across daily cycles.

    2026-09-19 follow-up: the original 0059 index only covered post_url
    WHILE status='pending_approval'. Once a draft moved to 'approved' or
    'posted' its post_url became free again, so a later rescan of the same
    still-visible post could insert a SECOND 'pending_approval' draft
    asking the owner to approve another comment on a post NOA had already
    commented on -- a real (if narrow) duplicate-action gap, since the
    approval gate does not, by itself, know the post was already handled.
    0060_drafts_dedup_active widens the index to 'pending_approval',
    'approved' AND 'posted' (leaving 'skipped' out on purpose -- the owner
    explicitly declined that draft and NOA may legitimately try again)."""

    @classmethod
    def setUpClass(cls):
        with open("/app/social/facebook_browser/group_scanner.py", encoding="utf-8") as f:
            cls.scanner_source = f.read()
        with open("/app/alembic/versions/0060_drafts_dedup_active.py", encoding="utf-8") as f:
            cls.migration_source = f.read()

    def test_save_draft_uses_on_conflict_do_nothing(self):
        self.assertIn("ON CONFLICT DO NOTHING", self.scanner_source)

    def test_widened_dedup_migration_covers_pending_approved_posted(self):
        self.assertIn("uq_group_comment_drafts_active_post", self.migration_source)
        self.assertIn("ON group_comment_drafts (post_url)", self.migration_source)
        for state in ("pending_approval", "approved", "posted"):
            self.assertIn(state, self.migration_source)

    def test_live_index_matches_widened_dedup_contract(self):
        import subprocess
        result = subprocess.run(
            [
                "python3", "-c",
                "import asyncio, asyncpg, os\n"
                "DB = os.environ['DATABASE_URL'].replace('postgresql+asyncpg://','postgresql://')\n"
                "async def main():\n"
                "    conn = await asyncpg.connect(DB)\n"
                "    row = await conn.fetchrow(\"SELECT indexdef FROM pg_indexes WHERE indexname='uq_group_comment_drafts_active_post'\")\n"
                "    print(row['indexdef'] if row else '')\n"
                "    await conn.close()\n"
                "asyncio.run(main())\n",
            ],
            capture_output=True, text=True,
        )
        indexdef = result.stdout.strip()
        self.assertTrue(indexdef, f"live index not found (stderr: {result.stderr[-300:]})")
        self.assertIn("post_url", indexdef)
        for state in ("pending_approval", "approved", "posted"):
            self.assertIn(state, indexdef)
        # 'skipped' must stay excluded -- a declined draft must remain re-draftable
        self.assertNotIn("skipped", indexdef)


if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for cls in (
        TestDraftPersistHandoffPresent,
        TestDeadCodeBridgeDocumented,
        TestSaveDraftDedupContract,
    ):
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    total = result.testsRun
    failures = len(result.failures) + len(result.errors)
    passed = total - failures

    print()
    print("=" * 70)
    print(f"noa_group_draft_handoff_test.py: {passed}/{total} PASS")
    print("=" * 70)

    sys.exit(0 if failures == 0 else 1)
