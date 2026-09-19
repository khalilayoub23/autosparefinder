"""
devtests/noa_page_publish_approval_test.py — regression + controlled proof for the
Facebook-Page-publishing root fix (2026-09-19, /goal "close NOA Facebook Page + EOD
reporting end-to-end").

BACKGROUND / ROOT CAUSE (FIXES_TRACKER #28): a post approved for
platforms=[discord, facebook, telegram, tiktok] via the Telegram-mirror inline
button (routes/webhooks.py's telegram_admin_webhook "approve_post" callback) was
marked status='published' in the DB, yet the owner never saw it on the Facebook
Page. Confirmed via a live production row (social_posts 8a6e91a7-...,
created/published 2026-09-17): external_post_ids carried ONLY {"telegram": 565,
"published_platforms": ["telegram"]} and approved_at was NULL. Root cause: the
Telegram-callback handler was a SECOND, divergent approve+publish implementation
that (a) unconditionally re-published to Telegram regardless of which button was
tapped, (b) only ever attempted ONE additional platform and explicitly excluded
"telegram"/"post"/"" from that single slot, and (c) marked the WHOLE row
'published' the instant that lone Telegram send succeeded -- so Facebook (and
Discord/TikTok) could be silently skipped while the row still read "published".
It also never called campaign_manager.approve_post_content(), so approved_at was
never set -- unlike the canonical WhatsApp "אשר <id>" path
(agents.owner_console._approve_and_publish), which correctly loops the FULL
platforms list.

FIX: the Telegram-callback handler now delegates to the SAME canonical
_approve_and_publish() the WhatsApp path already uses, so both approval channels
share one implementation, one approval-gate call, and one per-platform loop over
the row's real platforms list. A second, smaller defect was found and fixed while
proving this: _approve_and_publish() itself did not carry the
"tiktok never needs pre-supplied media" exception the old webhook code had, which
would have been a regression the moment webhooks.py started delegating to it —
ported into _approve_and_publish() so both channels keep TikTok's existing
no-media auto-video-generation behavior.

This suite:
  1. Verifies (source-text) that webhooks.py's approve_post callback no longer
     contains the old divergent logic and now imports/calls
     agents.owner_console._approve_and_publish.
  2. Runs a CONTROLLED functional test of _approve_and_publish itself (the
     function BOTH channels now share) against a real, synthetic social_posts
     row in the live DB, with social.registry.dispatch PATCHED to a mock (no
     real Facebook/Telegram/Discord/TikTok call is ever made), proving that a
     multi-platform approval now attempts EVERY eligible platform on the row --
     not just one -- and that approved_at is correctly recorded.

Following this project's established pattern (see noa_pipeline_controlled_e2e_test.py,
engagement_lifecycle_test.py): the DB-touching functional test runs as one linear
async script rather than unittest.IsolatedAsyncioTestCase, which hits an
asyncpg-driver-bound-to-a-stale-event-loop error across multiple test methods in
this environment.

Run:
    docker exec autospare_backend python3 /app/devtests/noa_page_publish_approval_test.py
"""
import asyncio
import sys
import unittest
import uuid
from unittest.mock import AsyncMock, patch

import sqlalchemy as sa

sys.path.insert(0, "/app")

_WEBHOOKS_PATH = "/app/routes/webhooks.py"


class TestApprovePostCallbackDelegatesToCanonicalPublisher(unittest.TestCase):
    """Source-text checks: the Telegram-callback handler must no longer carry
    its own divergent publish logic."""

    @classmethod
    def setUpClass(cls):
        with open(_WEBHOOKS_PATH, encoding="utf-8") as f:
            cls.source = f.read()
        # Isolate just the approve_post callback block for precise assertions.
        start = cls.source.index('if callback_data == "approve_post":')
        end = cls.source.index('elif callback_data == "edit_post":')
        cls.block = cls.source[start:end]

    def test_delegates_to_canonical_approve_and_publish(self):
        self.assertIn("from agents.owner_console import _approve_and_publish", self.block)
        self.assertIn("await _approve_and_publish(db, post)", self.block)

    def test_no_longer_unconditionally_publishes_telegram_first(self):
        # The old defect: telegram was dispatched BEFORE any approval-gate call,
        # regardless of which platform button was tapped.
        self.assertNotIn('await registry.dispatch("telegram", caption)', self.block)

    def test_no_longer_excludes_telegram_post_empty_from_the_platform_loop(self):
        # The old single-platform loop that silently skipped Facebook/Discord/
        # TikTok whenever the tapped platform resolved to "telegram"/"post"/"".
        self.assertNotIn('plat not in ("post", "", "telegram")', self.block)

    def test_no_longer_marks_published_on_partial_success_without_the_approval_gate(self):
        # The old code set sp.status='published' directly with no
        # approve_post_content() call anywhere in this block.
        self.assertNotIn('sp.status = "published" if published else "approved"', self.block)

    def test_post_dict_uses_the_real_platforms_column(self):
        self.assertIn("sp_row.platforms or []", self.block)


class TestApproveAndPublishTiktokExemption(unittest.TestCase):
    """Source-text check: the media-required skip in the canonical function
    must still exempt tiktok, or consolidating onto it regresses TikTok."""

    def test_tiktok_exempted_from_media_required_skip(self):
        with open("/app/agents/owner_console.py", encoding="utf-8") as f:
            source = f.read()
        start = source.index("async def _approve_and_publish")
        end = source.index("async def _reject_post")
        block = source[start:end]
        self.assertIn('and p != "tiktok"', block)


async def _run_controlled_functional_tests() -> bool:
    """Proves, against the real DB with a mocked registry.dispatch (no real
    Facebook/Telegram/Discord/TikTok call ever made), that _approve_and_publish
    now attempts every eligible platform on a post and records approval state
    correctly. Returns True if all assertions pass."""
    from BACKEND_DATABASE_MODELS import async_session_factory, SocialPost
    from agents.owner_console import _approve_and_publish

    post_id = uuid.uuid4()
    system_user = uuid.UUID("00000000-0000-0000-0000-000000000001")

    async def _cleanup():
        async with async_session_factory() as db:
            await db.execute(sa.text("DELETE FROM social_posts WHERE id = :id"), {"id": str(post_id)})
            await db.commit()

    await _cleanup()  # in case a prior failed run left a row behind
    async with async_session_factory() as db:
        sp = SocialPost(
            id=post_id,
            content="TEST controlled post — do not publish for real",
            platforms=["discord", "facebook", "telegram", "tiktok"],
            status="pending_approval",
            external_post_ids={"source": "controlled_test", "topic": "TEST"},
            created_by=system_user,
        )
        db.add(sp)
        await db.commit()
    print("STEP 0 — synthetic pending_approval social_posts row seeded: OK")

    async def fake_dispatch(platform, content, *, media_url=None, hashtags=None, link=None):
        # Deterministic, distinct-per-platform mock outcomes -- proves each
        # platform is individually reached, not just the first one. tiktok
        # succeeds WITHOUT a media_url, proving the exemption fix works.
        if platform == "discord":
            return {"ok": True, "id": "disc_123", "error": None, "not_configured": False}
        if platform == "facebook":
            return {"ok": True, "id": "fb_456", "error": None, "not_configured": False}
        if platform == "telegram":
            return {"ok": True, "id": 999, "error": None, "not_configured": False}
        if platform == "tiktok":
            return {"ok": True, "id": "tt_789", "error": None, "not_configured": False}
        raise AssertionError(f"unexpected platform dispatched: {platform!r}")

    try:
        with patch("social.registry.is_configured", return_value=True), \
             patch("social.registry.dispatch", new=AsyncMock(side_effect=fake_dispatch)) as mocked_dispatch:
            async with async_session_factory() as db:
                post = {"id": str(post_id), "content": "TEST controlled post",
                        "platforms": ["discord", "facebook", "telegram", "tiktok"]}
                reply = await _approve_and_publish(db, post)

        dispatched_platforms = {call.args[0] for call in mocked_dispatch.await_args_list}
        assert dispatched_platforms == {"discord", "facebook", "telegram", "tiktok"}, \
            f"expected all 4 platforms dispatched, got {dispatched_platforms} (the root defect)"
        print("STEP 1 — every platform on the row was attempted (discord+facebook+telegram+tiktok): OK")

        assert "facebook" in reply.lower() and "✅" in reply
        print(f"STEP 2 — reply confirms facebook publish: {reply.splitlines()[0]!r}")

        async with async_session_factory() as db:
            row = (await db.execute(sa.text(
                "SELECT status, approved_at, external_post_ids FROM social_posts WHERE id = :id"
            ), {"id": str(post_id)})).fetchone()

        assert row.status == "published", f"expected status='published', got {row.status!r}"
        assert row.approved_at is not None, "approved_at must be set — the old defect left it NULL"
        ext = row.external_post_ids
        assert ext.get("discord") == "disc_123"
        assert ext.get("facebook") == "fb_456"
        assert ext.get("telegram") == 999
        assert ext.get("tiktok") == "tt_789"
        assert ext.get("source") == "controlled_test", "original metadata must survive the JSONB merge"
        print("STEP 3 — status='published', approved_at set, all 4 platform ids persisted, prior metadata preserved: OK")

        # Idempotency: a second approval attempt on the now-published row must
        # be a safe no-op (no double dispatch, no double publish).
        with patch("social.registry.is_configured", return_value=True), \
             patch("social.registry.dispatch", new=AsyncMock(side_effect=fake_dispatch)) as mocked_dispatch2:
            async with async_session_factory() as db:
                second_reply = await _approve_and_publish(db, post)
        assert "כבר במצב" in second_reply, f"expected a safe no-op message, got: {second_reply!r}"
        assert mocked_dispatch2.await_count == 0, "a second approval must not re-dispatch to any platform"
        print("STEP 4 — duplicate approval attempt is a safe no-op, zero re-dispatch: OK")

        return True
    finally:
        await _cleanup()
        print("cleanup: OK (synthetic social_posts row removed)")


def _run_unittest_suite() -> bool:
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for cls in (
        TestApprovePostCallbackDelegatesToCanonicalPublisher,
        TestApproveAndPublishTiktokExemption,
    ):
        suite.addTests(loader.loadTestsFromTestCase(cls))
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return not (result.failures or result.errors)


if __name__ == "__main__":
    source_ok = _run_unittest_suite()
    functional_ok = asyncio.run(_run_controlled_functional_tests())

    print()
    print("=" * 70)
    total_ok = source_ok and functional_ok
    print(f"noa_page_publish_approval_test.py: {'ALL PASS' if total_ok else 'FAILURES PRESENT'}")
    print("No real Facebook/Telegram/Discord/TikTok call was made.")
    print("=" * 70)

    sys.exit(0 if total_ok else 1)
