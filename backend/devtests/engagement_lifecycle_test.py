"""
devtests/engagement_lifecycle_test.py — offline lifecycle test for NOA social engagement.

Proves the social_inbox store + record→draft→list→resolve→reply/skip lifecycle works
against the LIVE DB, without needing any social credentials (uses a synthetic item and
cleans up after itself). Run:
    docker exec autospare_backend python3 /app/devtests/engagement_lifecycle_test.py
Last Updated: 2026-07-25
"""
import asyncio
from social import engagement as eng
from BACKEND_DATABASE_MODELS import async_session_factory


async def main():
    ok = True
    # language detection
    assert eng._detect_lang("רפידות בלם לקורולה") == "he"
    assert eng._detect_lang("قطع غيار") == "ar"
    assert eng._detect_lang("brake pads please") == "en"
    print("lang detection: OK")

    print("configured platforms:", eng.configured_platforms())

    async with async_session_factory() as db:
        await eng.ensure_inbox_table(db)
        print("ensure_inbox_table: OK")

        item = {
            "platform": "facebook", "kind": "comment",
            "external_id": "TEST_LIFECYCLE_1", "parent_id": "TEST_POST_1",
            "author": "Test Customer", "text": "יש לכם רפידות בלם לקורולה 2018?",
            "permalink": "https://example.com/c/1",
        }
        # clean any leftover from a prior run
        from sqlalchemy import text as _t
        await db.execute(_t("DELETE FROM social_inbox WHERE external_id='TEST_LIFECYCLE_1'"))
        await db.commit()

        iid = await eng.record_item(db, item); await db.commit()
        assert iid, "record_item should return a new id"
        print("record_item new id:", iid)

        dup = await eng.record_item(db, item); await db.commit()
        assert dup is None, "duplicate should be None"
        print("dedupe: OK")

        await eng.set_draft(db, iid, "כן! חפש/י לפי מספר רישוי באתר ונביא את החלק המתאים 🚗")
        pend = await eng.pending_for_owner(db)
        assert any(p["id"] == iid for p in pend), "drafted item must appear as pending"
        print("set_draft + pending_for_owner: OK (", len(pend), "pending )")

        r = await eng.resolve_inbox(db, iid[:8])
        assert r and r["id"] == iid, "resolve by prefix must find it"
        print("resolve_inbox by prefix: OK")

        # send_reply on an unconfigured platform must fail gracefully (no token)
        sr = await eng.send_reply("facebook", "TEST_LIFECYCLE_1", "hi")
        assert sr.get("ok") is False, "reply without token must fail gracefully"
        print("send_reply graceful-fail without token: OK ->", sr.get("error"))

        await eng.mark_skipped(db, iid)
        pend2 = await eng.pending_for_owner(db)
        assert not any(p["id"] == iid for p in pend2), "skipped item must leave pending"
        print("mark_skipped: OK")

        # --- Feedback loop: returning-engager priority (2026-08-15c) ---
        from sqlalchemy import text as _t2
        await db.execute(_t2(
            "DELETE FROM social_inbox WHERE external_id IN "
            "('TEST_LIFECYCLE_HIST','TEST_LIFECYCLE_RETURN','TEST_LIFECYCLE_FRESH')"
        ))
        await db.commit()

        # A prior REPLIED item from "Returning Customer" — real past execution.
        hist_id = await eng.record_item(db, {
            "platform": "facebook", "kind": "comment",
            "external_id": "TEST_LIFECYCLE_HIST", "parent_id": "TEST_POST_HIST",
            "author": "Returning Customer", "text": "תודה על העזרה הקודמת!",
            "permalink": "https://example.com/c/hist",
        })
        await db.commit()
        await eng.mark_replied(db, hist_id, "fake_reply_id")

        # A NEW pending item from the SAME returning author.
        return_id = await eng.record_item(db, {
            "platform": "facebook", "kind": "comment",
            "external_id": "TEST_LIFECYCLE_RETURN", "parent_id": "TEST_POST_RETURN",
            "author": "Returning Customer", "text": "שאלה נוספת בבקשה",
            "permalink": "https://example.com/c/return",
        })
        await db.commit()
        await eng.set_draft(db, return_id, "בטח, איך אפשר לעזור?")

        # A NEWER pending item (created after) from a first-time, non-returning author.
        await asyncio.sleep(0.05)
        fresh_id = await eng.record_item(db, {
            "platform": "facebook", "kind": "comment",
            "external_id": "TEST_LIFECYCLE_FRESH", "parent_id": "TEST_POST_FRESH",
            "author": "First Time Commenter", "text": "יש לכם מצבר לקורולה?",
            "permalink": "https://example.com/c/fresh",
        })
        await db.commit()
        await eng.set_draft(db, fresh_id, "כן! נבדוק זמינות עבורך")

        pend3 = await eng.pending_for_owner(db, limit=20)
        by_id = {p["id"]: p for p in pend3}
        assert return_id in by_id and fresh_id in by_id, "both new pending items must be listed"
        assert by_id[return_id]["is_returning_engager"] is True, (
            "an author with a prior REPLIED item must be flagged is_returning_engager"
        )
        assert by_id[fresh_id]["is_returning_engager"] is False, (
            "a first-time author must NOT be flagged is_returning_engager"
        )
        idx_return = next(i for i, p in enumerate(pend3) if p["id"] == return_id)
        idx_fresh = next(i for i, p in enumerate(pend3) if p["id"] == fresh_id)
        assert idx_return < idx_fresh, (
            "the RETURNING engager must be surfaced before the more-recent first-time "
            "commenter — real prior execution (a sent reply) must change ordering, "
            "not just recency"
        )
        print("pending_for_owner: returning-engager correctly prioritized over more-recent first-timer")

        await eng.mark_skipped(db, return_id)
        await eng.mark_skipped(db, fresh_id)
        await db.execute(_t2(
            "DELETE FROM social_inbox WHERE external_id IN "
            "('TEST_LIFECYCLE_HIST','TEST_LIFECYCLE_RETURN','TEST_LIFECYCLE_FRESH')"
        ))
        await db.commit()
        print("returning-engager test cleanup: OK")

        # poll_once must not raise with nothing configured
        summ = await eng.poll_once(db)
        print("poll_once (no creds):", summ)

        await db.execute(_t("DELETE FROM social_inbox WHERE external_id='TEST_LIFECYCLE_1'"))
        await db.commit()
        print("cleanup: OK")

    print("\nALL LIFECYCLE TESTS PASSED" if ok else "FAILED")


if __name__ == "__main__":
    asyncio.run(main())
