"""
devtests/noa_group_summary_test.py — regression for the group-draft owner handoff fix
(FIXES_TRACKER #31).

Defect: the scan-cycle WhatsApp summary titled itself with len(discoveries) (posts FOUND)
as if it were replies awaiting approval — a 490-discovery scan with 18 new drafts and 40
pending reported "490 תגובות ממתינות" — and `תגובות-גרופ` exposed only 10 drafts.

Fix under test: summary built from persisted group_comment_drafts
(social/noa_ops.group_draft_summary + format_group_summary), actionable draft IDs, and
`תגובות-גרופ [עמוד]` pagination. Approval semantics are unchanged.

Safe by construction: synthetic rows only (removed afterwards), the Facebook publish step
is mocked, notify_owner/WhatsApp is never called (only the pure formatter is exercised).

Run: docker exec autospare_backend python3 /app/devtests/noa_group_summary_test.py
"""
import asyncio
import re
import sys
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import sqlalchemy as sa

sys.path.insert(0, "/app")

from social import noa_ops  # noqa: E402

_ROUTES = "/app/BACKEND_API_ROUTES.py"


def _fn_source(path: str, name: str) -> str:
    lines = open(path, encoding="utf-8").read().splitlines(keepends=True)
    start = next(i for i, l in enumerate(lines) if re.match(rf"^(async )?def {re.escape(name)}\(", l))
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if lines[j].strip() and not lines[j].startswith((" ", "\t")):
            end = j
            break
    return "".join(lines[start:end])


class TestFormatterCounts(unittest.TestCase):
    def setUp(self):
        top = [{"id": str(uuid.uuid4()), "group": f"קבוצה {i}", "score": 1.0, "draft": f"טיוטה {i}"} for i in range(10)]
        self.summary = {"new": 18, "total_pending": 40, "top": top}
        self.title, self.body = noa_ops.format_group_summary(490, self.summary)

    def test_three_distinct_numbers(self):
        self.assertIn("*490* פוסטים רלוונטיים", self.body)
        self.assertIn("*18* תגובות חדשות", self.body)
        self.assertIn("*40* תגובות בסך הכול ממתינות", self.body)

    def test_title_uses_new_drafts_not_discoveries(self):
        self.assertIn("18", self.title)
        self.assertNotIn("490", self.title)

    def test_no_longer_claims_490_pending_responses(self):
        self.assertNotIn("490 תגובות ממתינות", self.title + self.body)
        self.assertNotIn("*490* תגובות", self.body)

    def test_ids_are_shown_and_command_preserved(self):
        for it in self.summary["top"]:
            self.assertIn(it["id"][:8], self.body)
        self.assertIn("אשרתגובה <מזהה>", self.body)
        self.assertIn("תגובות-גרופ", self.body)

    def test_no_new_drafts_case_is_honest(self):
        t, b = noa_ops.format_group_summary(490, {"new": 0, "total_pending": 40, "top": []})
        self.assertIn("לא נוצרו תגובות חדשות", t)
        self.assertIn("*0* תגובות חדשות", b)
        self.assertIn("*40*", b)

    def test_fallback_never_claims_a_pending_count(self):
        t, b = noa_ops.format_group_summary_fallback(490)
        self.assertNotIn("ממתינות לאישורך", t + b.split("לצפייה")[0])
        self.assertIn("*490* פוסטים רלוונטיים", b)


class TestSafetyAndDedupSource(unittest.TestCase):
    def test_summary_helpers_contain_no_publish_path(self):
        src = "".join(_fn_source("/app/social/noa_ops.py", n) for n in
                     ("group_draft_summary", "format_group_summary", "format_group_summary_fallback"))
        for banned in ("post_claimed_group_draft", "submit_approved_comment", "publish", "dispatch", "claim_group_draft"):
            self.assertNotIn(banned, src)

    def test_list_command_is_read_only(self):
        src = _fn_source("/app/agents/owner_console.py", "_fb_comment_drafts_list")
        self.assertNotIn("UPDATE", src.upper().replace("OFFSET", ""))
        for banned in ("claim_group_draft", "post_claimed_group_draft", "submit_approved_comment"):
            self.assertNotIn(banned, src)

    def test_loop_summary_keeps_dedup_and_transport(self):
        src = _fn_source(_ROUTES, "_group_scan_loop")
        self.assertIn('alert_key=f"group_scan_discoveries_{_disc_fp}"', src)
        self.assertIn("cooldown_s=86400", src)
        self.assertIn("await notify_owner(", src)
        self.assertNotIn("{len(discoveries)} תגובות ממתינות", src)
        self.assertIn("_noa_sum.group_draft_summary(_sum_db, _cycle_started)", src)

    def test_autonomous_engagement_still_off_and_approval_required(self):
        import os
        from unittest.mock import patch as _p
        self.assertEqual(noa_ops.AUTONOMOUS_FLAG, "NOA_ENGAGEMENT_AUTOREPLY")
        with _p.dict(os.environ, {}, clear=False):
            os.environ.pop("NOA_ENGAGEMENT_AUTOREPLY", None)
            self.assertFalse(noa_ops.autonomous_flag_on(), "autonomy must default OFF")
        self.assertTrue("APPROVAL_REQUIRED = True" in open("/app/social/facebook_browser/group_agent.py", encoding="utf-8").read())


async def _functional() -> bool:
    from BACKEND_DATABASE_MODELS import async_session_factory
    from agents import owner_console

    marker = f"SUMTEST_{uuid.uuid4().hex[:8]}"
    gid = str(uuid.uuid4())
    started = datetime.now(timezone.utc)
    old_ts = started - timedelta(days=2)
    ids_old, ids_new = [], []

    async def cleanup():
        async with async_session_factory() as db:
            await db.execute(sa.text("DELETE FROM group_comment_drafts WHERE post_url LIKE :m"), {"m": f"%{marker}%"})
            await db.execute(sa.text("DELETE FROM group_targets WHERE id = CAST(:i AS uuid)"), {"i": gid})
            await db.commit()

    await cleanup()
    try:
        async with async_session_factory() as db:
            baseline_total = (await db.execute(sa.text(
                "SELECT COUNT(*) FROM group_comment_drafts WHERE status='pending_approval'"))).scalar()
            await db.execute(sa.text("""INSERT INTO group_targets (id, platform, group_name, group_url, status)
                VALUES (CAST(:i AS uuid), 'facebook', :n, :u, 'approved')"""),
                {"i": gid, "n": f"{marker}-group", "u": f"https://facebook.com/groups/{marker}/"})
            for i in range(22):  # pre-existing backlog (must NOT count as new)
                did = str(uuid.uuid4()); ids_old.append(did)
                await db.execute(sa.text("""INSERT INTO group_comment_drafts
                    (id, group_target_id, post_url, post_text, draft_comment, relevance_score, status, created_at)
                    VALUES (CAST(:id AS uuid), CAST(:g AS uuid), :u, 'old post', 'old draft', 0.5, 'pending_approval', :t)"""),
                    {"id": did, "g": gid, "u": f"https://facebook.com/{marker}/old{i}", "t": old_ts})
            for i in range(18):  # created by "this scan"
                did = str(uuid.uuid4()); ids_new.append(did)
                await db.execute(sa.text("""INSERT INTO group_comment_drafts
                    (id, group_target_id, post_url, post_text, draft_comment, relevance_score, status, created_at)
                    VALUES (CAST(:id AS uuid), CAST(:g AS uuid), :u, 'new post', 'new draft', 1.0, 'pending_approval', NOW())"""),
                    {"id": did, "g": gid, "u": f"https://facebook.com/{marker}/new{i}"})
            await db.commit()
        print("STEP 0 — seeded 22 old + 18 new pending drafts: OK")

        # 1+2: accurate counts; old drafts not counted as new
        async with async_session_factory() as db:
            summ = await noa_ops.group_draft_summary(db, started)
        assert summ["new"] == 18, f"new={summ['new']}"
        assert summ["total_pending"] == baseline_total + 40, (summ["total_pending"], baseline_total)
        assert len(summ["top"]) == 10
        assert all(t["id"] in ids_new for t in summ["top"]), "old drafts leaked into the 'new' list"
        title, body = noa_ops.format_group_summary(490, summ)
        assert "490 תגובות ממתינות" not in title + body and "18" in title
        print(f"STEP 1/2/3 — new=18, total_pending={summ['total_pending']} (baseline {baseline_total}+40), 490 not shown as pending: OK")

        # 4: IDs in the summary are real persisted drafts
        async with async_session_factory() as db:
            for t in summ["top"]:
                assert t["id"][:8] in body
                n = (await db.execute(sa.text("SELECT COUNT(*) FROM group_comment_drafts WHERE id = CAST(:i AS uuid) AND status='pending_approval'"),
                                      {"i": t["id"]})).scalar()
                assert n == 1
        print("STEP 4 — every ID shown maps to a real pending draft: OK")

        # 5: pagination reaches ALL pending drafts
        async with async_session_factory() as db:
            all_pending = {r[0][:8] for r in (await db.execute(sa.text(
                "SELECT id::text FROM group_comment_drafts WHERE status='pending_approval'"))).fetchall()}
            pages = (len(all_pending) + 9) // 10
            seen = set()
            for p in range(1, pages + 1):
                out = await owner_console._fb_comment_drafts_list(db, p)
                found = set(re.findall(r"🆔 ([0-9a-f]{8}) ", out))
                assert 0 < len(found) <= 10, (p, len(found))
                assert f"עמוד {p}/{pages}" in out
                seen |= found
            assert seen == all_pending, f"missing {len(all_pending - seen)} of {len(all_pending)}"
            last = await owner_console._fb_comment_drafts_list(db, pages + 5)
            assert f"עמוד {pages}/{pages}" in last and "לעמוד הבא" not in last
            assert f"עמוד 1/{pages}" in await owner_console._fb_comment_drafts_list(db, 0)
            assert "לעמוד הבא: *תגובות-גרופ 2*" in await owner_console._fb_comment_drafts_list(db, 1)
        print(f"STEP 5 — pagination reaches all {len(all_pending)} pending drafts across {pages} pages (clamps out-of-range): OK")

        # 5b: the command dispatcher routes "תגובות-גרופ 2"
        with patch.object(owner_console, "_fb_comment_drafts_list", new=AsyncMock(return_value="x")) as m:
            async with async_session_factory() as db:
                await owner_console._process_owner_message("תגובות-גרופ 2", db)
                await owner_console._process_owner_message("תגובות-גרופ", db)
        assert [c.args[1] for c in m.await_args_list] == [2, 1]
        print("STEP 5b — dispatcher parses 'תגובות-גרופ 2' and bare 'תגובות-גרופ': OK")

        # 6+7: אשרתגובה approves only the selected draft; publish step is MOCKED
        target = ids_new[3]
        with patch.object(noa_ops, "post_claimed_group_draft", new=AsyncMock(return_value={"ok": True})) as pub:
            async with async_session_factory() as db:
                reply = await owner_console._fb_comment_approve(db, target[:8])
            await asyncio.sleep(0.3)
        assert "אישרת" in reply
        assert pub.await_count == 1 and pub.await_args.args[0]["id"] == target
        async with async_session_factory() as db:
            st = dict((await db.execute(sa.text("SELECT id::text, status FROM group_comment_drafts WHERE post_url LIKE :m"),
                                        {"m": f"%{marker}%"})).fetchall())
        assert st[target] == "approved"
        assert sum(1 for k, v in st.items() if v == "approved") == 1, "approval leaked to other drafts"
        assert sum(1 for v in st.values() if v == "pending_approval") == 39
        print("STEP 6/7 — אשרתגובה approved exactly one draft via the owner gate (publish mocked, 39 others untouched): OK")
        return True
    finally:
        await cleanup()
        print("cleanup: OK (synthetic rows removed)")


def _unit() -> bool:
    suite = unittest.TestSuite()
    for c in (TestFormatterCounts, TestSafetyAndDedupSource):
        suite.addTests(unittest.TestLoader().loadTestsFromTestCase(c))
    r = unittest.TextTestRunner(verbosity=2).run(suite)
    return not (r.failures or r.errors)


if __name__ == "__main__":
    u = _unit()
    f = asyncio.run(_functional())
    ok = u and f
    print("\n" + "=" * 70)
    print(f"noa_group_summary_test.py: {'ALL PASS' if ok else 'FAILURES PRESENT'}")
    print("No WhatsApp message and no Facebook post/comment was made by this test.")
    print("=" * 70)
    sys.exit(0 if ok else 1)
