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

        # poll_once must not raise with nothing configured
        summ = await eng.poll_once(db)
        print("poll_once (no creds):", summ)

        await db.execute(_t("DELETE FROM social_inbox WHERE external_id='TEST_LIFECYCLE_1'"))
        await db.commit()
        print("cleanup: OK")

    print("\nALL LIFECYCLE TESTS PASSED" if ok else "FAILED")


if __name__ == "__main__":
    asyncio.run(main())
