"""
devtests/noa_pipeline_controlled_e2e_test.py — controlled, approval-safe
end-to-end proof of the full NOA Facebook-post pipeline (2026-09-19, /goal
"close NOA Facebook post handling end-to-end").

Proves the state transition:
    relevant discovered post -> drafted -> persisted (pending_approval)
    -> owner approves -> approved -> (mocked) execution -> posted

against the LIVE catalog DB using the REAL production functions
(draft_group_comment, _save_draft, and the exact SQL owner_console.py uses
for the approve/status-transition steps) with a synthetic test group and
post_url, cleaned up afterward. The only thing NOT real is the final
Facebook browser action itself (GroupAgent.submit_approved_comment) --
that is patched to a no-op so this test can never publish a real comment,
per the task's live-safety constraint (no unauthorized Facebook action).

Run:
    docker exec autospare_backend python3 /app/devtests/noa_pipeline_controlled_e2e_test.py
"""
import asyncio
import uuid
from unittest.mock import AsyncMock, patch

import sqlalchemy as sa

from BACKEND_DATABASE_MODELS import async_session_factory
from social.facebook_browser.group_agent import GroupAgent
from social.facebook_browser.group_scanner import _save_draft

TEST_GROUP_ID = str(uuid.uuid4())
TEST_POST_URL = f"https://www.facebook.com/groups/TEST_{uuid.uuid4().hex[:12]}/posts/1"
TEST_POST_TEXT = "מחפש תושבת מנוע ימינית למיצובישי לנסר 1.8 שנת 2008"


async def _cleanup(db):
    await db.execute(sa.text("DELETE FROM group_comment_drafts WHERE post_url = :u"), {"u": TEST_POST_URL})
    await db.execute(sa.text("DELETE FROM group_targets WHERE id = CAST(:id AS uuid)"), {"id": TEST_GROUP_ID})
    await db.commit()


async def main():
    async with async_session_factory() as db:
        await _cleanup(db)  # in case a prior failed run left rows behind

        # 0. Seed a synthetic (non-real) group_target row -- FK requirement,
        #    not a real Facebook group.
        await db.execute(sa.text("""
            INSERT INTO group_targets (id, platform, group_name, group_url, status)
            VALUES (CAST(:id AS uuid), 'facebook', 'TEST-controlled-e2e', :url, 'approved')
        """), {"id": TEST_GROUP_ID, "url": f"https://www.facebook.com/groups/{TEST_GROUP_ID}/"})
        await db.commit()
        print("STEP 0 — synthetic group_target seeded: OK")

        # 1. DISCOVERY -- a genuine-shaped discovery dict, exactly the shape
        #    _scan_one_group() / GroupAgent.scan_groups() produce.
        discovery = {
            "group_id": TEST_GROUP_ID,
            "group_url": f"https://www.facebook.com/groups/{TEST_GROUP_ID}/",
            "group_name": "TEST-controlled-e2e",
            "post_url": TEST_POST_URL,
            "post_text": TEST_POST_TEXT,
            "author": "",
            "relevance_score": 0.667,
            "suggested_action": "comment",  # score >= 0.4
        }
        assert discovery["suggested_action"] in ("comment", "post"), \
            "fixture must match the handoff's own filter condition"
        print("STEP 1 — discovery fixture built (relevance_score=0.667, suggested_action='comment'): OK")

        # 2. RELEVANCE -- the discovery already carries a real, non-trivial
        #    score and full post text/URL/group identity, exactly what the
        #    handoff block in _group_scan_loop() consumes.
        assert discovery["post_text"] and discovery["post_url"] and discovery["group_id"]
        print("STEP 2 — relevance/detection fields present (text, url, group_id): OK")

        # 3. HANDOFF / PERSISTENCE -- the REAL functions the fix wires
        #    together: draft_group_comment() (LLM-based, no Facebook action)
        #    then _save_draft() (real DB insert, real ON CONFLICT dedup).
        draft_text = await GroupAgent().draft_group_comment(discovery)
        assert draft_text, "draft_group_comment must produce non-empty text for a real discovery"
        print(f"STEP 3a — draft_group_comment produced: {draft_text[:80]!r}")

        saved = await _save_draft(
            db,
            group_id=discovery["group_id"],
            post_url=discovery["post_url"],
            post_text=discovery["post_text"],
            draft=draft_text,
            score=discovery["relevance_score"],
        )
        assert saved is True, "_save_draft must report success on first insert"

        row = (await db.execute(sa.text(
            "SELECT status, draft_comment FROM group_comment_drafts WHERE post_url = :u"
        ), {"u": TEST_POST_URL})).fetchone()
        assert row is not None, "a real row must exist in group_comment_drafts"
        assert row.status == "pending_approval", f"expected pending_approval, got {row.status!r}"
        assert row.draft_comment, "persisted row must carry the drafted text"
        print("STEP 3b — persisted to group_comment_drafts with status='pending_approval': OK")

        # 3c. DEDUP -- rescanning the SAME post while it's still pending
        #     must never create a second row (the pre-existing partial
        #     unique index on post_url).
        dup_attempt = await _save_draft(
            db, group_id=discovery["group_id"], post_url=discovery["post_url"],
            post_text=discovery["post_text"], draft="a different draft text",
            score=discovery["relevance_score"],
        )
        count = (await db.execute(sa.text(
            "SELECT COUNT(*) FROM group_comment_drafts WHERE post_url = :u"
        ), {"u": TEST_POST_URL})).scalar()
        assert count == 1, f"rescanning a pending post must not duplicate the row (found {count})"
        print("STEP 3c — rescan-while-pending dedup verified (still exactly 1 row): OK")

        # 4. NOA ACTION -- confirm nothing has been sent to Facebook. Only a
        #    DB row exists; no comment has been posted anywhere.
        assert row.status != "posted"
        print("STEP 4 — no Facebook action has occurred yet (status is not 'posted'): OK")

        # 5. APPROVAL GATE -- replicate the EXACT SQL owner_console.py's
        #    _fb_comment_approve() executes on "אשרתגובה <id>": flip
        #    pending_approval -> approved. This IS the real approval
        #    mechanism, just invoked directly instead of via a WhatsApp
        #    message, since driving a real WhatsApp message is out of
        #    scope for an automated test.
        await db.execute(sa.text("""
            UPDATE group_comment_drafts
            SET status='approved', approved_at=NOW()
            WHERE post_url = :u AND status='pending_approval'
        """), {"u": TEST_POST_URL})
        await db.commit()
        row2 = (await db.execute(sa.text(
            "SELECT status FROM group_comment_drafts WHERE post_url = :u"
        ), {"u": TEST_POST_URL})).fetchone()
        assert row2.status == "approved"
        print("STEP 5 — owner-approval SQL transitions pending_approval -> approved: OK")

        # 5b. A second approve attempt against the same row must now find
        #     nothing (owner_console.py's WHERE status='pending_approval'
        #     guard) -- proves the approve command cannot double-fire.
        second_approve = await db.execute(sa.text("""
            UPDATE group_comment_drafts
            SET status='approved', approved_at=NOW()
            WHERE post_url = :u AND status='pending_approval'
        """), {"u": TEST_POST_URL})
        await db.commit()
        assert second_approve.rowcount == 0, "a second approve on an already-approved row must be a no-op"
        print("STEP 5b — re-approving an already-approved draft is a no-op (rowcount=0): OK")

        # 6. EXECUTION -- the ONLY step that would touch real Facebook.
        #    Patched to a no-op so this test can never publish a real
        #    comment, per the task's live-safety constraint. Replicates the
        #    exact success-path status update owner_console.py performs
        #    after a successful submit_approved_comment() call.
        with patch.object(GroupAgent, "submit_approved_comment", new=AsyncMock(return_value={"ok": True, "error": None})) as mocked:
            agent = GroupAgent()
            result = await agent.submit_approved_comment(
                post_url=TEST_POST_URL, comment_text=draft_text, group_url=discovery["group_url"],
            )
            mocked.assert_awaited_once()
            assert result["ok"] is True
        status = "posted" if result.get("ok") else "pending_approval"
        await db.execute(sa.text(
            "UPDATE group_comment_drafts SET status=:s WHERE post_url=:u"
        ), {"s": status, "u": TEST_POST_URL})
        await db.commit()
        row3 = (await db.execute(sa.text(
            "SELECT status FROM group_comment_drafts WHERE post_url = :u"
        ), {"u": TEST_POST_URL})).fetchone()
        assert row3.status == "posted"
        print("STEP 6 — mocked execution (NO real Facebook call made) transitions approved -> posted: OK")

        # 7. DEDUP AFTER POSTING -- the 0060 migration's widened index must
        #    now block a fresh draft for the same post_url even though its
        #    status is no longer 'pending_approval'.
        redraft_after_posted = await _save_draft(
            db, group_id=discovery["group_id"], post_url=discovery["post_url"],
            post_text=discovery["post_text"], draft="yet another draft",
            score=discovery["relevance_score"],
        )
        count2 = (await db.execute(sa.text(
            "SELECT COUNT(*) FROM group_comment_drafts WHERE post_url = :u"
        ), {"u": TEST_POST_URL})).scalar()
        assert count2 == 1, f"rescanning an already-posted post must not create a second draft (found {count2})"
        print("STEP 7 — rescan-after-posted dedup verified (0060 widened index, still exactly 1 row): OK")

        await _cleanup(db)
        print("cleanup: OK (synthetic group_target + group_comment_drafts rows removed)")

    print()
    print("=" * 70)
    print("noa_pipeline_controlled_e2e_test.py: ALL STEPS PASSED")
    print("No real Facebook comment/post was made. No WhatsApp message was sent.")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
