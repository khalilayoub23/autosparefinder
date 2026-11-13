"""2026-11-13: persistent post revision history + hashtag relevance (needs the sandbox catalog DB)."""
import asyncio
import datetime
import os
import uuid

import pytest
import sqlalchemy as sa

pytestmark = pytest.mark.skipif(os.getenv("RUN_DB_MUTATION_TESTS") != "1", reason="DB mutation tests off")


def _run(coro):
    return asyncio.run(coro)


def test_revisions_original_edits_sources_and_order():
    from BACKEND_DATABASE_MODELS import async_session_factory
    from social import campaign_manager as cm
    from agents import owner_console as oc

    async def go():
        pid = str(uuid.uuid4())
        async with async_session_factory() as db:
            await db.execute(sa.text(
                "INSERT INTO social_posts (id, content, platforms, status, created_by) "
                "VALUES (CAST(:i AS uuid), 'draft v1', ARRAY['telegram'], 'pending_approval', CAST('00000000-0000-0000-0000-000000000001' AS uuid))"), {"i": pid})
            await db.commit()
            try:
                await oc.edit_post_text(db, pid, "edit two", source="whatsapp")
                await oc.edit_post_text(db, pid, "edit three", source="telegram")
                revs = await cm.get_revisions(db, pid)
                assert [(r["version"], r["content"], r["source"]) for r in revs] == [
                    (1, "draft v1", "noa_draft"), (2, "edit two", "whatsapp"), (3, "edit three", "telegram")]
                assert "גרסה 3" in await oc.post_history_text(db, pid[:8])
                row = (await db.execute(sa.text("SELECT content, content_version, status FROM social_posts WHERE id=CAST(:i AS uuid)"), {"i": pid})).fetchone()
                assert row.content == "edit three" and row.content_version == 3 and row.status == "pending_approval"
            finally:
                await db.execute(sa.text("DELETE FROM social_post_revisions WHERE post_id=CAST(:i AS uuid)"), {"i": pid})
                await db.execute(sa.text("DELETE FROM social_posts WHERE id=CAST(:i AS uuid)"), {"i": pid})
                await db.commit()
    _run(go())


def test_hashtags_drop_stale_year_and_unrelated_ev_tag():
    from BACKEND_AI_AGENTS import SocialMediaManagerAgent as N
    y = datetime.date.today().year
    out = N._filter_hashtags(f"צינור פליטה ל-Renault Kadjar\n#רכביחשמלי #חורף2024 #חורף{y} #צינורפליטה")
    assert "#רכביחשמלי" not in out and "2024" not in out
    assert f"#חורף{y}" in out and "#צינורפליטה" in out
    assert "#רכביחשמלי" in N._filter_hashtags("הטעינה של הרכב החשמלי\n#רכביחשמלי")
    assert "#רכביחשמלי" not in " ".join(N._NOA_TAG_POOL_HE)
