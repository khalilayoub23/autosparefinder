"""
Final Facebook Group E2E — complete production execution path.
Creates campaign + social_post, approves, calls execute_campaign(), verifies.
"""
import asyncio
import json
import logging
import os
import uuid
from datetime import datetime

logging.basicConfig(level=logging.WARNING)

OWNER_UUID      = "00000000-0000-0000-0000-000000000001"
GROUP_TARGET_ID = "3a9b463d-e8e9-4946-884d-34a0b3879ca5"
GROUP_URL       = "https://www.facebook.com/groups/musahnikim/"

POST_CONTENT = (
    "🔧 טיפ מקצועי לבעלי טויוטה קורולה: מסנן שמן איכותי = מנוע בריא יותר!\n\n"
    "ב-AutoSpareFinder תמצאו את כל חלקי החילוף המקוריים והחלופיים במחיר הטוב ביותר — "
    "עם חיפוש לפי לוחית רישוי ישירות לרכב שלכם ✅\n\n"
    "🚗 autosparefinder.co.il\n#חלקי_רכב #טויוטה #ישראל"
)


async def run_e2e():
    from BACKEND_DATABASE_MODELS import async_session_factory
    from BACKEND_AI_AGENTS import get_agent
    from social.campaign_manager import approve_post_content
    import sqlalchemy as sa

    campaign_id = str(uuid.uuid4())
    post_id     = str(uuid.uuid4())

    print("=" * 60)
    print("FACEBOOK GROUP E2E — PRODUCTION EXECUTION PATH")
    print(f"Campaign ID : {campaign_id}")
    print(f"Post ID     : {post_id}")
    print(f"Group target: {GROUP_TARGET_ID}")
    print(f"Group URL   : {GROUP_URL}")
    print("=" * 60)

    async with async_session_factory() as db:
        # ── Step 1: Create campaign ──────────────────────────────────────
        await db.execute(
            sa.text("""
                INSERT INTO campaigns
                    (id, name, goal, platforms, tone, status, plan,
                     linked_post_ids, created_at, updated_at)
                VALUES (
                    CAST(:id AS uuid), :name, :goal, ARRAY['facebook_group'],
                    'professional', 'draft', '{}'::jsonb,
                    ARRAY[]::uuid[], NOW(), NOW()
                )
            """),
            {"id": campaign_id,
             "name": "E2E Group Test 2026-08-11",
             "goal": "Final E2E verification of Facebook Group publication pipeline"},
        )
        await db.commit()
        print(f"\n[STEP 1] Campaign created: {campaign_id}")

        # ── Step 2: Create social_post (pending_approval) ────────────────
        await db.execute(
            sa.text("""
                INSERT INTO social_posts
                    (id, content, platforms, status, campaign_id, content_version,
                     external_post_ids, created_by, created_at, updated_at)
                VALUES (
                    CAST(:id AS uuid), :content, ARRAY['facebook_group'],
                    'pending_approval', CAST(:cid AS uuid), 1,
                    '{}'::jsonb, CAST(:owner AS uuid), NOW(), NOW()
                )
            """),
            {"id": post_id, "content": POST_CONTENT,
             "cid": campaign_id, "owner": OWNER_UUID},
        )
        await db.commit()
        print(f"[STEP 2] social_post created (status=pending_approval): {post_id}")

        # ── Step 3: Record BEFORE state ──────────────────────────────────
        before_sp = (await db.execute(
            sa.text("SELECT status, published_at, external_post_ids, content_version FROM social_posts WHERE id = CAST(:id AS uuid)"),
            {"id": post_id},
        )).fetchone()
        before_gt = (await db.execute(
            sa.text("SELECT posts_sent, last_posted_at FROM group_targets WHERE id = CAST(:id AS uuid)"),
            {"id": GROUP_TARGET_ID},
        )).fetchone()

        print(f"\n[BEFORE STATE]")
        print(f"  social_posts.status         : {before_sp.status}")
        print(f"  social_posts.published_at   : {before_sp.published_at}")
        print(f"  social_posts.external_ids   : {before_sp.external_post_ids}")
        print(f"  social_posts.content_version: {before_sp.content_version}")
        print(f"  group_targets.posts_sent    : {before_gt.posts_sent}")
        print(f"  group_targets.last_posted_at: {before_gt.last_posted_at}")

        # ── Step 4: Approve the social_post ─────────────────────────────
        approved = await approve_post_content(
            db, post_id=post_id, approved_by=OWNER_UUID, approved_version=1
        )
        if not approved:
            print("\n[FAIL] approve_post_content returned False — aborting E2E")
            return {"verdict": "FAIL", "reason": "approval failed",
                    "campaign_id": campaign_id, "post_id": post_id}
        print(f"\n[STEP 3] social_post approved → status='approved'")

        after_approve = (await db.execute(
            sa.text("SELECT status, approved_by, approved_at FROM social_posts WHERE id = CAST(:id AS uuid)"),
            {"id": post_id},
        )).fetchone()
        print(f"  Confirmed status  : {after_approve.status}")
        print(f"  approved_by       : {after_approve.approved_by}")
        print(f"  approved_at       : {after_approve.approved_at}")
        assert after_approve.status == "approved", f"status is not 'approved': {after_approve.status}"

    # ── Step 5: Execute via full production path ─────────────────────────
    print(f"\n[STEP 4] Calling execute_campaign() — REAL PRODUCTION PATH")
    print("  execute_campaign → run_tool('facebook_group_publish')")
    print("    → facebook_group_publish() → GroupAgent.publish_group_post()")
    print("      → FacebookSession → Playwright → real Facebook Group")
    noa = get_agent("social_media_manager_agent")

    async with async_session_factory() as db:
        exec_result = await noa.execute_campaign(campaign_id, db, dry_run=False)

    print(f"\n[STEP 5] execute_campaign() returned:")
    print(json.dumps(exec_result, indent=2, default=str))

    # ── Step 6: Verify AFTER state ───────────────────────────────────────
    async with async_session_factory() as db:
        after_sp = (await db.execute(
            sa.text("SELECT status, published_at, external_post_ids FROM social_posts WHERE id = CAST(:id AS uuid)"),
            {"id": post_id},
        )).fetchone()
        after_gt = (await db.execute(
            sa.text("SELECT posts_sent, last_posted_at FROM group_targets WHERE id = CAST(:id AS uuid)"),
            {"id": GROUP_TARGET_ID},
        )).fetchone()

    print(f"\n[AFTER STATE]")
    print(f"  social_posts.status         : {after_sp.status}")
    print(f"  social_posts.published_at   : {after_sp.published_at}")
    print(f"  social_posts.external_ids   : {after_sp.external_post_ids}")
    print(f"  group_targets.posts_sent    : {after_gt.posts_sent}")
    print(f"  group_targets.last_posted_at: {after_gt.last_posted_at}")

    # ── Step 7: Assertions ───────────────────────────────────────────────
    failures = []

    if exec_result.get("posts_published", 0) < 1:
        failures.append(f"posts_published={exec_result.get('posts_published')} (expected >=1)")

    if after_sp.status != "published":
        failures.append(f"social_posts.status={after_sp.status!r} (expected 'published')")

    if after_sp.published_at is None:
        failures.append("social_posts.published_at is NULL after publish")

    ext = after_sp.external_post_ids or {}
    fg_val = ext.get("facebook_group")
    if fg_val is None:
        failures.append(f"external_post_ids missing 'facebook_group' key: {ext}")

    before_sent = before_gt.posts_sent or 0
    after_sent  = after_gt.posts_sent  or 0
    if after_sent != before_sent + 1:
        failures.append(f"group_targets.posts_sent: {before_sent} → {after_sent} (expected {before_sent+1})")

    if after_gt.last_posted_at is None:
        failures.append("group_targets.last_posted_at is still NULL after publish")

    errors = exec_result.get("errors", [])
    if errors:
        failures.append(f"execute_campaign errors: {errors}")

    published_results = [r for r in exec_result.get("results", []) if r.get("status") == "published"]
    page_results      = [r for r in exec_result.get("results", []) if r.get("platform") in ("facebook", "instagram")]
    if page_results:
        failures.append(f"Unexpected Page publication: {page_results}")

    if len(published_results) > 1:
        failures.append(f"DUPLICATE PUBLISH: {len(published_results)} published results")

    return {
        "campaign_id"   : campaign_id,
        "post_id"       : post_id,
        "group_target"  : GROUP_TARGET_ID,
        "before_sp"     : {"status": before_sp.status, "published_at": str(before_sp.published_at), "external_ids": before_sp.external_post_ids, "content_version": before_sp.content_version},
        "before_gt"     : {"posts_sent": before_gt.posts_sent, "last_posted_at": str(before_gt.last_posted_at)},
        "after_sp"      : {"status": after_sp.status, "published_at": str(after_sp.published_at), "external_ids": after_sp.external_post_ids},
        "after_gt"      : {"posts_sent": after_gt.posts_sent, "last_posted_at": str(after_gt.last_posted_at)},
        "exec_result"   : exec_result,
        "external_post_ids_facebook_group": fg_val,
        "page_publications" : page_results,
        "published_results" : published_results,
        "failures"      : failures,
        "verdict"       : "PASS" if not failures else "FAIL",
    }


if __name__ == "__main__":
    result = asyncio.run(run_e2e())
    print("\n" + "=" * 60)
    print(f"FINAL VERDICT: {result.get('verdict', 'ERROR')}")
    if result.get("failures"):
        print("FAILURES:")
        for f in result["failures"]:
            print(f"  ✗ {f}")
    else:
        print("FINAL E2E: PASS — approved social_post → real Facebook Group publication → published DB state → bookkeeping reconciled.")
    print("=" * 60)
