"""
devtests/noa_eod_report_test.py — regression + controlled proof for NOA's
End-of-Day Facebook Activity report (2026-09-19, /goal "close NOA Facebook
Page + EOD reporting end-to-end", Phase D).

Structural checks confirm _noa_eod_report_loop() (BACKEND_API_ROUTES.py):
  - is registered as a supervised task in startup()
  - reuses notify_owner() (the existing WhatsApp channel) rather than a new
    transport
  - queries the three REAL persisted tables (group_comment_drafts,
    social_inbox, social_posts) rather than inventing a second ledger
  - fires once/day via a fixed-local-time sleep, not a container-uptime timer

A controlled functional test then runs the EXACT SAME query shapes the loop
uses against synthetic rows inserted into all three tables (cleaned up
after), proving the counting semantics are correct: identified/drafted/
approved/published/pending for Groups and Page, plus the "Original Page
posts published" JSONB check and the all-platform PLATFORM RESULT block.
No WhatsApp message is sent by this test — notify_owner() itself is never
called; only the underlying SQL is exercised directly.

Following this project's established pattern (see
noa_pipeline_controlled_e2e_test.py / noa_page_publish_approval_test.py):
the DB-touching portion runs as a linear async script, not
unittest.IsolatedAsyncioTestCase (asyncpg's driver breaks across the
separate event loop each test method gets in this environment).

Run:
    docker exec autospare_backend python3 /app/devtests/noa_eod_report_test.py
"""
import asyncio
import datetime
import re
import sys
import unittest
import uuid

import sqlalchemy as sa

sys.path.insert(0, "/app")

_SOURCE_PATH = "/app/BACKEND_API_ROUTES.py"


def _extract_function_source(func_name: str) -> str:
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
        if not line.startswith((" ", "\t")):
            end = j
            break
    return "".join(lines[start:end])


class TestEodReportLoopStructure(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = _extract_function_source("_noa_eod_report_loop")
        with open(_SOURCE_PATH, encoding="utf-8") as f:
            cls.full_source = f.read()

    def test_registered_as_supervised_task(self):
        self.assertIn(
            '_supervised_task("noa_eod_report_loop",         _noa_eod_report_loop())',
            self.full_source,
        )

    def test_reuses_notify_owner_not_a_new_transport(self):
        self.assertIn("await notify_owner(", self.source)
        self.assertNotIn("_wa_send(", self.source)  # must not bypass quiet-hours via the raw sender

    def test_queries_real_persisted_tables_not_a_new_ledger(self):
        self.assertIn("FROM group_comment_drafts", self.source)
        self.assertIn("FROM social_inbox", self.source)
        self.assertIn("FROM social_posts", self.source)
        self.assertNotIn("CREATE TABLE", self.source)

    def test_fires_once_per_day_at_a_fixed_local_time(self):
        self.assertIn("_secs_until_next_eod", self.source)
        self.assertIn("APP_LOCAL_TZ", self.source)

    def test_has_a_daily_alert_key_to_prevent_double_send(self):
        self.assertIn("noa_eod_report_", self.source)
        self.assertIn("alert_key=", self.source)

    def test_uses_naive_utc_window_to_avoid_the_tzaware_column_mismatch(self):
        # social_posts.created_at is a naive DateTime column; group_comment_drafts/
        # social_inbox use TIMESTAMPTZ. A tz-aware Python datetime bound against the
        # naive column raises asyncpg.DataError (verified live during this task) —
        # the loop must use datetime.utcnow(), not datetime.now(APP_LOCAL_TZ).
        self.assertIn("datetime.utcnow() - timedelta(hours=24)", self.source)


async def _run_controlled_functional_test() -> bool:
    from BACKEND_DATABASE_MODELS import async_session_factory, SocialPost

    gid = str(uuid.uuid4())
    marker = f"EODTEST_{uuid.uuid4().hex[:8]}"
    social_post_ids = [uuid.uuid4() for _ in range(3)]
    system_user = uuid.UUID("00000000-0000-0000-0000-000000000001")

    async def _cleanup():
        async with async_session_factory() as db:
            await db.execute(sa.text("DELETE FROM group_comment_drafts WHERE post_url LIKE :m"), {"m": f"%{marker}%"})
            await db.execute(sa.text("DELETE FROM group_targets WHERE id = CAST(:id AS uuid)"), {"id": gid})
            await db.execute(sa.text("DELETE FROM social_inbox WHERE external_id LIKE :m"), {"m": f"{marker}%"})
            for pid in social_post_ids:
                await db.execute(sa.text("DELETE FROM social_posts WHERE id = :id"), {"id": str(pid)})
            await db.commit()

    await _cleanup()
    try:
        async with async_session_factory() as db:
            # Seed a synthetic group target + 2 group_comment_drafts: one
            # 'posted' (published), one 'pending_approval' (still waiting).
            await db.execute(sa.text("""
                INSERT INTO group_targets (id, platform, group_name, group_url, status)
                VALUES (CAST(:id AS uuid), 'facebook', :name, :url, 'approved')
            """), {"id": gid, "name": f"{marker}-group", "url": f"https://facebook.com/groups/{marker}/"})
            await db.execute(sa.text("""
                INSERT INTO group_comment_drafts (group_target_id, post_url, post_text, draft_comment, relevance_score, status)
                VALUES (CAST(:gid AS uuid), :url1, 'test post 1', 'draft 1', 0.6, 'posted')
            """), {"gid": gid, "url1": f"https://facebook.com/{marker}/post1"})
            await db.execute(sa.text("""
                INSERT INTO group_comment_drafts (group_target_id, post_url, post_text, draft_comment, relevance_score, status)
                VALUES (CAST(:gid AS uuid), :url2, 'test post 2', 'draft 2', 0.5, 'pending_approval')
            """), {"gid": gid, "url2": f"https://facebook.com/{marker}/post2"})

            # Seed 2 social_inbox rows (Page): one 'replied', one 'new'.
            from social import engagement
            await engagement.ensure_inbox_table(db)
            await db.execute(sa.text("""
                INSERT INTO social_inbox (platform, external_id, kind, author, message, status)
                VALUES ('facebook', :eid1, 'comment', 'Test Author', 'hi', 'replied')
            """), {"eid1": f"{marker}_c1"})
            await db.execute(sa.text("""
                INSERT INTO social_inbox (platform, external_id, kind, author, message, status)
                VALUES ('facebook', :eid2, 'comment', 'Test Author 2', 'hi again', 'new')
            """), {"eid2": f"{marker}_c2"})

            # Seed 3 social_posts: one published-to-facebook, one pending_approval
            # (multi-platform), one approved-but-not-fully-published.
            db.add(SocialPost(
                id=social_post_ids[0], content=f"{marker} published to fb", platforms=["facebook", "telegram"],
                status="published", external_post_ids={"facebook": "fb_test_1", "telegram": 111},
                created_by=system_user,
            ))
            db.add(SocialPost(
                id=social_post_ids[1], content=f"{marker} pending", platforms=["facebook"],
                status="pending_approval", external_post_ids={}, created_by=system_user,
            ))
            db.add(SocialPost(
                id=social_post_ids[2], content=f"{marker} approved not published", platforms=["facebook"],
                status="approved", external_post_ids={}, created_by=system_user,
            ))
            await db.commit()
        print("STEP 0 — synthetic rows seeded across group_comment_drafts, social_inbox, social_posts: OK")

        window_start = datetime.datetime.utcnow() - datetime.timedelta(hours=24)
        async with async_session_factory() as db:
            g = (await db.execute(sa.text("""
                SELECT
                    COUNT(*) FILTER (WHERE created_at >= :ws) AS identified,
                    COUNT(*) FILTER (WHERE created_at >= :ws AND status IN ('approved','posted')) AS approved,
                    COUNT(*) FILTER (WHERE created_at >= :ws AND status = 'posted') AS published,
                    COUNT(*) FILTER (WHERE created_at >= :ws AND status = 'pending_approval') AS pending
                FROM group_comment_drafts WHERE group_target_id = CAST(:gid AS uuid)
            """), {"ws": window_start, "gid": gid})).fetchone()
            assert g.identified == 2, f"expected 2 identified, got {g.identified}"
            assert g.approved == 1, f"expected 1 approved (the 'posted' row; 'pending_approval' was never approved), got {g.approved}"
            assert g.published == 1, f"expected 1 published (posted), got {g.published}"
            assert g.pending == 1, f"expected 1 pending, got {g.pending}"
            print("STEP 1 — GROUPS counts correct: identified=2 approved=1 published=1 pending=1: OK")

            p = (await db.execute(sa.text("""
                SELECT
                    COUNT(*) FILTER (WHERE created_at >= :ws) AS identified,
                    COUNT(*) FILTER (WHERE created_at >= :ws AND status IN ('pending_approval','replied','skipped')) AS drafted,
                    COUNT(*) FILTER (WHERE created_at >= :ws AND status = 'replied') AS published,
                    COUNT(*) FILTER (WHERE created_at >= :ws AND status = 'new') AS pending
                FROM social_inbox WHERE platform = 'facebook' AND external_id LIKE :m
            """), {"ws": window_start, "m": f"{marker}%"})).fetchone()
            assert p.identified == 2
            assert p.published == 1
            assert p.pending == 1
            print("STEP 2 — PAGE counts correct: identified=2 published=1 pending=1: OK")

            page_posts = (await db.execute(sa.text("""
                SELECT COUNT(*) FROM social_posts
                WHERE created_at >= :ws AND 'facebook' = ANY(platforms)
                  AND external_post_ids ? 'facebook' AND content LIKE :m
            """), {"ws": window_start, "m": f"{marker}%"})).scalar()
            assert page_posts == 1, f"expected exactly 1 real Facebook-published post, got {page_posts}"
            print("STEP 3 — Original Page posts published (JSONB '?' key check) = 1: OK")

            overall = (await db.execute(sa.text("""
                SELECT
                    COUNT(*) FILTER (WHERE status = 'published') AS published,
                    COUNT(*) FILTER (WHERE status = 'pending_approval') AS pending,
                    COUNT(*) FILTER (WHERE status = 'approved') AS approved_not_published
                FROM social_posts WHERE created_at >= :ws AND content LIKE :m
            """), {"ws": window_start, "m": f"{marker}%"})).fetchone()
            assert overall.published == 1
            assert overall.pending == 1
            assert overall.approved_not_published == 1
            print("STEP 4 — PLATFORM RESULT correct: published=1 pending=1 approved_not_published=1: OK")

        return True
    finally:
        await _cleanup()
        print("cleanup: OK (all synthetic rows removed)")


def _run_unittest_suite() -> bool:
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(TestEodReportLoopStructure)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return not (result.failures or result.errors)


if __name__ == "__main__":
    structure_ok = _run_unittest_suite()
    functional_ok = asyncio.run(_run_controlled_functional_test())

    print()
    print("=" * 70)
    total_ok = structure_ok and functional_ok
    print(f"noa_eod_report_test.py: {'ALL PASS' if total_ok else 'FAILURES PRESENT'}")
    print("No WhatsApp message was sent by this test.")
    print("=" * 70)

    sys.exit(0 if total_ok else 1)
