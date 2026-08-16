"""
Script: devtests/e2e_campaign_test.py
Purpose: Phase 9 end-to-end campaign test.

Tests the complete workflow:
  1. Master Agent (AVI) routes to Digital Manager (SHIRA)
  2. Digital Manager (SHIRA) creates campaign via delegate_to_social_campaign()
  3. Social Manager (NOA) generates content via generate_post()
  4. Campaign executor selects tools via social/tools.py
  5. Tools execute (dry-run: no real FB post made)
  6. Feedback loop schema is verified
  7. Analytics report generated from engagement data
  8. All DB records created and verifiable

Run: docker exec autospare_backend python3 /app/devtests/e2e_campaign_test.py
Last Updated: 2026-08-07
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import uuid
from datetime import datetime

import sqlalchemy as sa

logging.basicConfig(level=logging.WARNING)
log = logging.getLogger("e2e_test")

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"
results: list[dict] = []


def record(name: str, status: str, detail: str = "") -> None:
    icon = "✅" if status == PASS else ("⚠️" if status == SKIP else "❌")
    print(f"  {icon}  [{status}] {name}" + (f"\n         {detail}" if detail else ""))
    results.append({"name": name, "status": status, "detail": detail})


# ---------------------------------------------------------------------------
# Step 1: Agent hierarchy imports
# ---------------------------------------------------------------------------

def test_agent_hierarchy() -> None:
    print("\n=== Step 1: Agent Hierarchy Verification ===")
    try:
        from BACKEND_AI_AGENTS import get_agent, RouterAgent, MarketingAgent, SocialMediaManagerAgent
        avi   = get_agent("router_agent")
        shira = get_agent("marketing_agent")
        noa   = get_agent("social_media_manager_agent")
        assert isinstance(avi,   RouterAgent)
        assert isinstance(shira, MarketingAgent)
        assert isinstance(noa,   SocialMediaManagerAgent)
        record("AVI (RouterAgent) is the Master Agent", PASS)
        record("SHIRA (MarketingAgent) is the Digital Manager", PASS)
        record("NOA (SocialMediaManagerAgent) is the Social Media Manager", PASS)
    except Exception as exc:
        record("agent hierarchy", FAIL, str(exc)[:150])
        return

    # Verify method chain
    try:
        assert hasattr(shira, "delegate_to_social_campaign"), "SHIRA missing delegate_to_social_campaign"
        assert hasattr(noa,   "execute_campaign"),            "NOA missing execute_campaign"
        assert hasattr(noa,   "generate_campaign_plan"),      "NOA missing generate_campaign_plan"
        assert hasattr(noa,   "generate_post"),               "NOA missing generate_post"
        record("Full method chain SHIRA→NOA→tools is wired", PASS)
    except AssertionError as exc:
        record("method chain", FAIL, str(exc))


# ---------------------------------------------------------------------------
# Step 2: Tool registry
# ---------------------------------------------------------------------------

def test_tool_registry_sync() -> None:
    print("\n=== Step 2: Tool Registry ===")
    from social.tools import list_tools
    tools = list_tools()
    names = {t["name"] for t in tools}
    expected = {
        "facebook_publish_page_post", "facebook_reply_comment", "facebook_get_insights",
        "instagram_publish_post", "facebook_group_scan", "facebook_group_comment",
        "facebook_group_publish", "telegram_publish", "whatsapp_send_message",
    }
    missing = expected - names
    if missing:
        record("tool registry completeness", FAIL, f"missing: {missing}")
    else:
        record(f"tool registry has all {len(tools)} required tools", PASS)

    for t in tools:
        assert t.get("description"), f"tool '{t['name']}' has no description"
    record("all tools have descriptions", PASS)


async def test_tool_registry_async() -> None:
    from social.tools import run_tool
    tr = await run_tool("nonexistent_tool", db=None)
    assert tr.status == "error"
    record("run_tool(unknown) returns ToolResult(error) gracefully", PASS)


# ---------------------------------------------------------------------------
# Step 3: Campaign plan generation (NOA, LLM call)
# ---------------------------------------------------------------------------

async def test_campaign_plan_generation(noa) -> dict:
    print("\n=== Step 3: Campaign Plan Generation (NOA + LLM) ===")
    try:
        plan = await noa.generate_campaign_plan(
            topic="Toyota brake pads — fitment verified, fast delivery in Israel",
            platforms=["facebook", "instagram"],
            tone="professional",
            duration_days=7,
            proposed_budget_ils=800.0,
        )
        assert isinstance(plan, dict), "plan must be a dict"
        assert plan.get("platforms"), "plan must include platforms"
        record("NOA.generate_campaign_plan() returns structured plan", PASS,
               f"summary={plan.get('summary','(none)')[:60]}")
        return plan
    except Exception as exc:
        record("generate_campaign_plan", FAIL, str(exc)[:200])
        return {}


# ---------------------------------------------------------------------------
# Step 4: Campaign record creation
# ---------------------------------------------------------------------------

async def test_campaign_creation(db, plan: dict) -> str:
    print("\n=== Step 4: Campaign DB Record Creation ===")
    from social.campaign_manager import create_campaign, get_campaign

    try:
        c = await create_campaign(
            db,
            name="Toyota Brake Parts Israel — E2E Test",
            goal="Promote Toyota OEM brake pads with fitment verification to Israeli car owners",
            platforms=["facebook", "instagram"],
            tone="professional",
            duration_days=7,
            budget_ils=800.0,
            plan=plan,
            created_by="e2e_test_runner",
        )
        cid = c["id"]
        assert c["status"] == "draft"
        assert "facebook" in c["platforms"]
        record(f"campaign created: id={cid[:8]}… status={c['status']}", PASS)

        # Verify it's in the DB
        fetched = await get_campaign(db, campaign_id=cid)
        assert fetched is not None
        assert fetched["goal"].startswith("Promote Toyota")
        record("campaign DB persistence confirmed (SELECT after INSERT)", PASS)
        return cid

    except Exception as exc:
        record("campaign creation", FAIL, str(exc)[:200])
        return ""


# ---------------------------------------------------------------------------
# Step 5: Content generation for each platform
# ---------------------------------------------------------------------------

async def test_content_generation(noa) -> dict[str, str]:
    print("\n=== Step 5: Content Generation per Platform (NOA) ===")
    contents: dict[str, str] = {}

    for platform in ["facebook", "instagram"]:
        try:
            content = await noa.generate_post(
                topic="Toyota OEM brake pads — fit your car perfectly, delivered to your door",
                platform=platform,
                tone="professional",
            )
            if content and len(content.strip()) >= 20:
                record(f"NOA generated {platform} content ({len(content)} chars)", PASS)
                contents[platform] = content
            else:
                record(f"NOA generated {platform} content", FAIL, f"too short: {content!r:.50}")
        except Exception as exc:
            record(f"content generation {platform}", FAIL, str(exc)[:150])

    return contents


# ---------------------------------------------------------------------------
# Step 6: Campaign execution (dry-run — no real posts)
# ---------------------------------------------------------------------------

async def test_campaign_execution(db, cid: str, noa) -> None:
    print("\n=== Step 6: Campaign Execution — dry_run=True ===")
    if not cid:
        record("campaign execution", SKIP, "no campaign_id from step 4")
        return

    try:
        result = await noa.execute_campaign(cid, db, dry_run=True)
        record(
            f"execute_campaign dry_run: queued={result.get('posts_queued')} platforms={result.get('platforms_attempted')}",
            PASS,
        )
        assert result.get("dry_run") is True
        assert result.get("posts_published") == 0
        record("dry_run produces no actual posts (posts_published=0)", PASS)

        dry_results = [r for r in result.get("results", []) if r.get("status") == "dry_run"]
        if dry_results:
            record(f"dry_run results contain platform content previews ({len(dry_results)} entries)", PASS)
        else:
            record("dry_run results", FAIL, f"no dry_run entries: {result.get('results')}")

    except Exception as exc:
        record("campaign execution", FAIL, str(exc)[:200])


# ---------------------------------------------------------------------------
# Step 7: Feedback loop schema verification
# ---------------------------------------------------------------------------

async def test_feedback_loop(db, cid: str) -> None:
    print("\n=== Step 7: Feedback Loop — engagement_events + analytics ===")
    from social.feedback_analyzer import generate_analytics_report, get_top_performers

    # Insert a synthetic engagement event linked to our campaign
    try:
        eid = str(uuid.uuid4())
        await db.execute(
            sa.text("""
                INSERT INTO engagement_events
                    (id, post_id, campaign_id, platform, external_post_id,
                     likes, comments, shares, reach, impressions, clicks,
                     data_source, collected_at, created_at)
                VALUES
                    (:id, gen_random_uuid(), CAST(:cid AS uuid), 'facebook', :ext,
                     150, 23, 8, 4200, 11000, 320,
                     'e2e_test', :now, :now)
            """),
            {"id": eid, "cid": cid, "ext": f"e2e_{uuid.uuid4().hex[:8]}", "now": datetime.utcnow()},
        )
        await db.commit()
        record("engagement_event inserted and linked to campaign", PASS)
    except Exception as exc:
        record("engagement_event insert", FAIL, str(exc)[:200])
        return

    # Verify campaign performance counters were NOT updated yet
    # (they update via feedback_analyzer._insert_engagement_event, not direct insert)
    # Analytics report should still see the row
    try:
        report = await generate_analytics_report(db, period_days=1, campaign_id=cid)
        rd = report["raw_data"]
        assert rd["total_reach"] >= 4200, f"expected ≥4200 reach, got {rd['total_reach']}"
        record(
            f"analytics report: posts={rd['total_posts']} reach={rd['total_reach']} engagement={rd['total_engagement']}",
            PASS,
        )
        record(f"analytics_reports row created: id={report['report_id'][:8]}…", PASS)
    except Exception as exc:
        record("analytics report generation", FAIL, str(exc)[:200])

    # Top performers
    try:
        tops = await get_top_performers(db, days=1, limit=5)
        assert isinstance(tops, list)
        record(f"get_top_performers returns {len(tops)} rows for NOA's weekly brief", PASS)
    except Exception as exc:
        record("get_top_performers", FAIL, str(exc)[:150])


# ---------------------------------------------------------------------------
# Step 8: Integration layer verification
# ---------------------------------------------------------------------------

def test_integration_layer() -> None:
    print("\n=== Step 8: Integration Layer ===")
    checks = [
        ("integrations.meta", ["auth_manager", "graph_client", "facebook_pages",
                                "instagram", "webhook_handler", "rate_limiter"]),
        ("integrations.facebook_browser", ["task_queue"]),
    ]
    for pkg, submodules in checks:
        try:
            __import__(pkg)
            record(f"{pkg} package imports", PASS)
        except Exception as exc:
            record(f"{pkg} import", FAIL, str(exc)[:100])

        for sub in submodules:
            try:
                __import__(f"{pkg}.{sub}")
                record(f"  {pkg}.{sub}", PASS)
            except Exception as exc:
                record(f"  {pkg}.{sub}", FAIL, str(exc)[:100])

    # Cerebras gate
    try:
        from integrations.meta.cerebras_gate import needs_llm
        assert needs_llm("generate_campaign_plan") is True
        assert needs_llm("generate_post") is True
        assert needs_llm("unknown_action") is False
        try:
            needs_llm("db_write")
            record("cerebras_gate blocks db_write", FAIL, "should have raised ValueError")
        except ValueError:
            pass
        record("cerebras_gate: LLM required=True for planning/generation, blocked for db/api ops", PASS)
    except Exception as exc:
        record("cerebras_gate", FAIL, str(exc)[:150])


# ---------------------------------------------------------------------------
# Step 9: DB records audit
# ---------------------------------------------------------------------------

async def test_db_audit(db, cid: str) -> None:
    print("\n=== Step 9: DB Records Audit ===")
    checks = [
        ("campaigns",          f"id = CAST('{cid}' AS uuid)" if cid else "1=0"),
        ("engagement_events",  f"campaign_id = CAST('{cid}' AS uuid)" if cid else "1=0"),
        ("analytics_reports",  f"campaign_id = CAST('{cid}' AS uuid)" if cid else "1=0"),
    ]
    for table, where in checks:
        try:
            cnt = (await db.execute(sa.text(f"SELECT COUNT(*) FROM {table} WHERE {where}"))).scalar()
            if cnt and cnt > 0:
                record(f"{table}: {cnt} row(s) for our campaign", PASS)
            else:
                record(f"{table}: 0 rows for campaign", FAIL, "campaign_id not found in table")
        except Exception as exc:
            record(f"{table} audit", FAIL, str(exc)[:120])


# ---------------------------------------------------------------------------
# Phase 10 (2026-08-15e) — creative_angle handoff completion
#
# Prior audit found: generate_campaign_plan() produces a real per-platform
# Campaign.plan["platform_mix"][].creative_angle, but execute_campaign() never
# read it — actual post content was regenerated generically from goal+tone.
# Fixed in BACKEND_AI_AGENTS.py: execute_campaign() now builds a
# platform->creative_angle map from the PERSISTED plan and passes it into
# generate_post(), which folds it into the real generation prompt.
#
# These tests self-clean (delete their own campaign/posts) — unlike the
# pre-existing tests above in this file, which intentionally leave their
# campaign in place. Both conventions are correct for what each test needs.
# ---------------------------------------------------------------------------

async def _cleanup_campaign(db, cid: str) -> None:
    """Deletes everything a test campaign can leave behind. Fixed 2026-08-16
    (operational-loop audit): previously deleted only social_posts+campaigns
    — test_feedback_loop's engagement_events/analytics_reports rows (and the
    original Phase 9 campaign itself, which never called this helper at all)
    were never cleaned up, silently leaking one campaign+event+report per
    suite run since 2026-08-09 (22 found and purged this pass)."""
    await db.execute(sa.text("DELETE FROM engagement_events WHERE campaign_id = CAST(:id AS uuid)"), {"id": cid})
    await db.execute(sa.text("DELETE FROM analytics_reports WHERE campaign_id = CAST(:id AS uuid)"), {"id": cid})
    await db.execute(sa.text("DELETE FROM social_posts WHERE campaign_id = CAST(:id AS uuid)"), {"id": cid})
    await db.execute(sa.text("DELETE FROM campaigns WHERE id = CAST(:id AS uuid)"), {"id": cid})
    await db.commit()


async def test_creative_angle_reaches_generation_input(db, noa) -> None:
    """TEST 1: a distinctive, impossible-to-confuse creative_angle must appear
    in the ACTUAL prompt passed to the LLM boundary (hf_text) — not merely be
    present in Campaign.plan. Spies at the real generation boundary rather
    than asserting on generate_post's return value, per the requirement that
    variable-name evidence alone is not proof."""
    from unittest.mock import AsyncMock, patch
    from social.campaign_manager import create_campaign

    DISTINCT = "CREATIVE_ANGLE_PROOF_92841"
    cid = None
    try:
        campaign = await create_campaign(
            db, name="TEST_HANDOFF_1", goal="TEST_HANDOFF_1 goal", platforms=["facebook"],
            tone="professional",
            plan={"platform_mix": [{"platform": "facebook", "goal": "x", "daily_budget_ils": 10, "creative_angle": DISTINCT}]},
            created_by="00000000-0000-0000-0000-000000000000",
        )
        cid = campaign["id"]

        captured_prompts = []

        async def _fake_hf_text(*, prompt, **kw):
            captured_prompts.append(prompt)
            return "פוסט טסט קצר לצורך הבדיקה."

        with patch("BACKEND_AI_AGENTS.hf_text", new=AsyncMock(side_effect=_fake_hf_text)):
            result = await noa.execute_campaign(cid, db)

        assert result.get("posts_queued", 0) >= 1, f"expected at least one queued post, got {result}"
        assert captured_prompts, "hf_text (the real LLM boundary) was never called"
        joined = "\n---\n".join(captured_prompts)
        assert DISTINCT in joined, (
            f"the distinctive creative_angle never crossed into the actual generation "
            f"prompt — captured prompts: {joined[:400]}"
        )
        record("TEST 1: distinctive creative_angle reaches the real hf_text prompt boundary", PASS)
    except AssertionError as exc:
        record("TEST 1: creative_angle reaches generation input", FAIL, str(exc)[:200])
    except Exception as exc:
        record("TEST 1: creative_angle reaches generation input", FAIL, f"unexpected: {exc!s:.200}")
    finally:
        if cid:
            await _cleanup_campaign(db, cid)


async def test_creative_angle_platform_mapping(db, noa) -> None:
    """TEST 2: two platforms with two DIFFERENT distinctive angles — each
    platform's captured prompt must contain ONLY its own angle. Proves
    keying is by platform name, not array position or cross-contamination."""
    from unittest.mock import AsyncMock, patch
    from social.campaign_manager import create_campaign

    FB_ANGLE = "FACEBOOK_ANGLE_PROOF"
    TG_ANGLE = "TELEGRAM_ANGLE_PROOF"
    cid = None
    try:
        campaign = await create_campaign(
            db, name="TEST_HANDOFF_2", goal="TEST_HANDOFF_2 goal", platforms=["facebook", "telegram"],
            tone="professional",
            plan={"platform_mix": [
                {"platform": "telegram", "goal": "x", "daily_budget_ils": 10, "creative_angle": TG_ANGLE},
                {"platform": "facebook", "goal": "x", "daily_budget_ils": 10, "creative_angle": FB_ANGLE},
            ]},  # deliberately out of order vs. campaign.platforms — proves no positional assumption
            created_by="00000000-0000-0000-0000-000000000000",
        )
        cid = campaign["id"]

        captured: dict[str, str] = {}

        async def _fake_hf_text(*, prompt, **kw):
            if FB_ANGLE in prompt:
                captured["facebook"] = prompt
            elif TG_ANGLE in prompt:
                captured["telegram"] = prompt
            else:
                captured.setdefault("unmatched", prompt)
            return "פוסט טסט קצר."

        with patch("BACKEND_AI_AGENTS.hf_text", new=AsyncMock(side_effect=_fake_hf_text)):
            await noa.execute_campaign(cid, db)

        assert "facebook" in captured, f"facebook never received its angle: {captured.keys()}"
        assert "telegram" in captured, f"telegram never received its angle: {captured.keys()}"
        assert FB_ANGLE in captured["facebook"] and TG_ANGLE not in captured["facebook"], (
            "CROSS-CONTAMINATION: facebook's prompt contains telegram's angle"
        )
        assert TG_ANGLE in captured["telegram"] and FB_ANGLE not in captured["telegram"], (
            "CROSS-CONTAMINATION: telegram's prompt contains facebook's angle"
        )
        record("TEST 2: platform-specific mapping correct, no cross-contamination", PASS)
    except AssertionError as exc:
        record("TEST 2: platform-specific mapping", FAIL, str(exc)[:200])
    except Exception as exc:
        record("TEST 2: platform-specific mapping", FAIL, f"unexpected: {exc!s:.200}")
    finally:
        if cid:
            await _cleanup_campaign(db, cid)


async def test_creative_angle_persisted_plan_consumption(db, noa) -> None:
    """TEST 3: execute_campaign() must read creative_angle from the PERSISTED
    Campaign row (via a fresh get_campaign() DB read inside execute_campaign),
    not from any in-memory object — proving the real architecture (SHIRA ->
    Campaign -> LATER execution) is what's actually exercised. This test
    never holds a reference to the plan dict after create_campaign() returns;
    only campaign_id crosses into execute_campaign()."""
    from unittest.mock import AsyncMock, patch
    from social.campaign_manager import create_campaign

    DISTINCT = "PERSISTED_PLAN_PROOF_55219"
    cid = None
    try:
        campaign = await create_campaign(
            db, name="TEST_HANDOFF_3", goal="TEST_HANDOFF_3 goal", platforms=["facebook"],
            tone="professional",
            plan={"platform_mix": [{"platform": "facebook", "goal": "x", "daily_budget_ils": 10, "creative_angle": DISTINCT}]},
            created_by="00000000-0000-0000-0000-000000000000",
        )
        cid = str(campaign["id"])
        del campaign  # only campaign_id (a plain string) crosses the boundary below

        captured = []

        async def _fake_hf_text(*, prompt, **kw):
            captured.append(prompt)
            return "פוסט טסט קצר."

        with patch("BACKEND_AI_AGENTS.hf_text", new=AsyncMock(side_effect=_fake_hf_text)):
            await noa.execute_campaign(cid, db)  # cid only — forces a fresh DB read internally

        assert captured and DISTINCT in captured[0], (
            "creative_angle did not survive a real persisted-Campaign round trip"
        )
        record("TEST 3: execute_campaign reads creative_angle from the PERSISTED Campaign.plan", PASS)
    except AssertionError as exc:
        record("TEST 3: persisted-plan consumption", FAIL, str(exc)[:200])
    except Exception as exc:
        record("TEST 3: persisted-plan consumption", FAIL, f"unexpected: {exc!s:.200}")
    finally:
        if cid:
            await _cleanup_campaign(db, cid)


async def test_creative_angle_backward_compatible(db, noa) -> None:
    """TEST 4: a campaign with NO plan, or a platform_mix missing an entry for
    one of its platforms, must execute exactly as before — no exception
    caused solely by the missing optional field."""
    from unittest.mock import AsyncMock, patch
    from social.campaign_manager import create_campaign

    cases = [
        ("no plan at all", None),
        ("plan without platform_mix", {"summary": "x"}),
        ("platform_mix missing this platform", {"platform_mix": [{"platform": "telegram", "creative_angle": "irrelevant"}]}),
    ]
    for label, plan in cases:
        cid = None
        try:
            campaign = await create_campaign(
                db, name=f"TEST_HANDOFF_4_{label[:10]}", goal="TEST_HANDOFF_4 goal", platforms=["facebook"],
                tone="professional", plan=plan,
                created_by="00000000-0000-0000-0000-000000000000",
            )
            cid = campaign["id"]

            async def _fake_hf_text(*, prompt, **kw):
                return "פוסט טסט קצר."

            with patch("BACKEND_AI_AGENTS.hf_text", new=AsyncMock(side_effect=_fake_hf_text)):
                result = await noa.execute_campaign(cid, db)

            assert "error" not in result or not result["error"], f"{label}: unexpected error {result.get('error')}"
            assert result.get("posts_queued", 0) >= 1, f"{label}: expected normal queueing, got {result}"
            record(f"TEST 4: backward-compatible ({label})", PASS)
        except AssertionError as exc:
            record(f"TEST 4: backward-compatible ({label})", FAIL, str(exc)[:200])
        except Exception as exc:
            record(f"TEST 4: backward-compatible ({label})", FAIL, f"unexpected exception: {exc!s:.200}")
        finally:
            if cid:
                await _cleanup_campaign(db, cid)


async def _post_count(db, cid: str) -> int:
    return (await db.execute(sa.text(
        "SELECT COUNT(*) FROM social_posts WHERE campaign_id = CAST(:id AS uuid)"
    ), {"id": cid})).scalar()


async def _post_statuses(db, cid: str) -> set:
    return {r.status for r in (await db.execute(sa.text(
        "SELECT status FROM social_posts WHERE campaign_id = CAST(:id AS uuid)"
    ), {"id": cid})).fetchall()}


async def test_execute_campaign_triple_idempotent_and_approval_gated(db, noa) -> None:
    """TESTS 1/2/3/6 (2026-08-15f fix verification): first execution creates
    N posts; second and third executions create ZERO additional rows — the
    database row count is the source of truth, never the returned Python
    list. All posts remain pending_approval throughout (TEST 6).

    This directly re-verifies the fix for the defect first surfaced in the
    2026-08-15e handoff-completion pass: execute_campaign()'s "no approved
    posts yet" branch previously had no check for already-pending posts
    before calling prepare_campaign_content() (an unconditional INSERT,
    social/campaign_manager.py:289-323) — confirmed there via both an
    isolated test and a real live owner-console call (4 -> 8 rows). Fixed by
    querying existing non-terminal (pending_approval/approved/published)
    posts per platform before generating, BACKEND_AI_AGENTS.py.
    """
    from unittest.mock import AsyncMock, patch
    from social.campaign_manager import create_campaign

    cid = None
    try:
        campaign = await create_campaign(
            db, name="TEST_HANDOFF_TRIPLE", goal="TEST_HANDOFF_TRIPLE goal", platforms=["facebook"],
            tone="professional",
            plan={"platform_mix": [{"platform": "facebook", "creative_angle": "TRIPLE_IDEMPOTENCY_PROOF"}]},
            created_by="00000000-0000-0000-0000-000000000000",
        )
        cid = campaign["id"]

        async def _fake_hf_text(*, prompt, **kw):
            return "פוסט טסט קצר."

        with patch("BACKEND_AI_AGENTS.hf_text", new=AsyncMock(side_effect=_fake_hf_text)):
            first = await noa.execute_campaign(cid, db)
            count_1 = await _post_count(db, cid)

            second = await noa.execute_campaign(cid, db)
            count_2 = await _post_count(db, cid)

            third = await noa.execute_campaign(cid, db)
            count_3 = await _post_count(db, cid)

        assert count_1 == 1, f"TEST 1: expected 1 post after first execution, got {count_1}"
        record(f"TEST 1: first execution created {count_1} post(s) (db row count, not return value)", PASS)

        assert count_2 == count_1, f"TEST 2 FAIL: second execution changed row count {count_1} -> {count_2}"
        record("TEST 2: second execution created zero additional rows (db-verified)", PASS)

        assert count_3 == count_1, f"TEST 3 FAIL: third execution changed row count {count_1} -> {count_3}"
        record("TEST 3: third execution created zero additional rows (db-verified)", PASS)

        statuses = await _post_statuses(db, cid)
        assert statuses and statuses <= {"pending_approval"}, f"TEST 6 FAIL: non-pending_approval status found: {statuses}"
        record("TEST 6: all posts remain pending_approval across 3 executions, none published", PASS)

        # response shape sanity: second/third calls must still accurately
        # report the campaign's TOTAL queued state, not zero/empty
        assert second.get("posts_queued") == 1 and third.get("posts_queued") == 1, (
            f"response under-reports existing queued posts: second={second.get('posts_queued')}, "
            f"third={third.get('posts_queued')}"
        )
        record("Response shape: repeated calls still report the true total queued count", PASS)
    except AssertionError as exc:
        record("TEST 1/2/3/6: triple execution idempotency", FAIL, str(exc)[:300])
    except Exception as exc:
        record("TEST 1/2/3/6: triple execution idempotency", FAIL, f"unexpected: {exc!s:.200}")
    finally:
        if cid:
            await _cleanup_campaign(db, cid)


async def test_execute_campaign_state_specific_behavior(db, noa) -> None:
    """TEST 8: state-aware behavior, not a blind "if posts_exist: return".

    pending_approval / approved / published posts must all BLOCK
    regeneration for that platform (no duplicate). A REJECTED post must NOT
    block regeneration — the owner explicitly declined that specific
    content (reject_post_content sets status='rejected', a real, distinct,
    non-terminal-in-the-blocking-sense state; see
    social/campaign_manager.py:383-402), so a fresh attempt is the correct,
    intended behavior, not a duplicate.
    """
    from unittest.mock import AsyncMock, patch
    from social.campaign_manager import create_campaign

    async def _fake_hf_text(*, prompt, **kw):
        return "פוסט טסט קצר."

    cases = [
        ("pending_approval blocks regeneration", "pending_approval", False),
        ("approved blocks regeneration", "approved", False),
        ("published blocks regeneration", "published", False),
        ("rejected does NOT block regeneration", "rejected", True),
    ]
    for label, seed_status, expect_new_post in cases:
        cid = None
        try:
            campaign = await create_campaign(
                db, name=f"TEST_HANDOFF_STATE_{seed_status}", goal="x", platforms=["facebook"],
                tone="professional",
                plan={"platform_mix": [{"platform": "facebook", "creative_angle": "STATE_TEST"}]},
                created_by="00000000-0000-0000-0000-000000000000",
            )
            cid = campaign["id"]
            # Seed one existing post in the target state directly (bypassing
            # execute_campaign, so this test exercises ONLY the pre-generation
            # check, not a second full execution).
            seed_id = str(__import__("uuid").uuid4())
            await db.execute(sa.text("""
                INSERT INTO social_posts (id, content, platforms, status, external_post_ids,
                                           created_by, campaign_id, created_at, updated_at)
                VALUES (CAST(:id AS uuid), 'seed content', ARRAY['facebook']::text[], :status,
                        '{}'::jsonb, '00000000-0000-0000-0000-000000000000', CAST(:cid AS uuid), NOW(), NOW())
            """), {"id": seed_id, "status": seed_status, "cid": cid})
            await db.commit()

            with patch("BACKEND_AI_AGENTS.hf_text", new=AsyncMock(side_effect=_fake_hf_text)):
                await noa.execute_campaign(cid, db)

            count = await _post_count(db, cid)
            if expect_new_post:
                assert count == 2, f"{label}: expected seed(1) + new(1) = 2 rows, got {count}"
            else:
                assert count == 1, f"{label}: expected only the seed row (no regeneration), got {count}"
            record(f"TEST 8: {label} — {count} row(s) as expected", PASS)
        except AssertionError as exc:
            record(f"TEST 8: {label}", FAIL, str(exc)[:200])
        except Exception as exc:
            record(f"TEST 8: {label}", FAIL, f"unexpected: {exc!s:.200}")
        finally:
            if cid:
                await _cleanup_campaign(db, cid)


async def test_execute_campaign_dry_run_never_persists(db, noa) -> None:
    """TEST 9: dry_run must never write to social_posts, regardless of how
    many times it's called — it was already documented as safe to call
    repeatedly; this proves it, at the database level, post-fix."""
    from unittest.mock import AsyncMock, patch
    from social.campaign_manager import create_campaign

    cid = None
    try:
        campaign = await create_campaign(
            db, name="TEST_HANDOFF_DRYRUN", goal="x", platforms=["facebook"],
            tone="professional", created_by="00000000-0000-0000-0000-000000000000",
        )
        cid = campaign["id"]

        async def _fake_hf_text(*, prompt, **kw):
            return "פוסט טסט קצר."

        with patch("BACKEND_AI_AGENTS.hf_text", new=AsyncMock(side_effect=_fake_hf_text)):
            await noa.execute_campaign(cid, db, dry_run=True)
            await noa.execute_campaign(cid, db, dry_run=True)

        count = await _post_count(db, cid)
        assert count == 0, f"dry_run must never persist rows, found {count}"
        record("TEST 9: dry_run creates zero persisted SocialPost rows, even called twice", PASS)
    except AssertionError as exc:
        record("TEST 9: dry_run never persists", FAIL, str(exc)[:200])
    except Exception as exc:
        record("TEST 9: dry_run never persists", FAIL, f"unexpected: {exc!s:.200}")
    finally:
        if cid:
            await _cleanup_campaign(db, cid)


# ---------------------------------------------------------------------------
# Phase 12 — P2 fix #1: analytics_reports.insights reaches SHIRA's
# performance-evaluation context as advisory input (2026-08-16 closure pass)
# ---------------------------------------------------------------------------

async def test_insights_reach_shira_evaluation_prompt(db, shira) -> None:
    """TEST 1: a distinctive insights payload — returned by
    feedback_analyzer.generate_analytics_report() but previously never read
    by evaluate_campaign_performance() — must now appear in the ACTUAL
    prompt passed to the real hf_text boundary. Same discipline as the
    creative_angle proof: spy at the LLM call boundary, not the return value."""
    from unittest.mock import AsyncMock, patch
    from social.campaign_manager import create_campaign

    DISTINCT = "INSIGHT_PROOF_77213_best_times_sunday_9am"
    cid = None
    try:
        campaign = await create_campaign(
            db, name="TEST_P2_INSIGHTS_1", goal="TEST_P2_INSIGHTS_1 goal", platforms=["facebook"],
            tone="professional", created_by="00000000-0000-0000-0000-000000000000",
        )
        cid = campaign["id"]

        fake_report = {
            "report_id": "test-report-1",
            "raw_data": {
                "period_days": 7, "total_posts": 3, "total_reach": 5000,
                "total_impressions": 5000, "total_engagement": 400, "total_clicks": 50,
                "total_leads": 4, "avg_sentiment": 0.2, "engagement_rate": 0.08,
                "click_through_rate": 0.01,
            },
            "insights": {
                "summary": DISTINCT,
                "recommendations": ["post more on weekends"],
                "top_content_types": ["how-to"],
            },
        }

        captured_prompts = []

        async def _fake_hf_text(*, prompt, **kw):
            captured_prompts.append(prompt)
            return '{"decision": "continue", "reasoning": "test"}'

        with patch("social.feedback_analyzer.generate_analytics_report", new=AsyncMock(return_value=fake_report)), \
             patch("BACKEND_AI_AGENTS.hf_text", new=AsyncMock(side_effect=_fake_hf_text)):
            result = await shira.evaluate_campaign_performance(cid, db)

        assert captured_prompts, "hf_text (the real LLM boundary) was never called"
        joined = "\n---\n".join(captured_prompts)
        assert DISTINCT in joined, (
            f"the distinctive insights.summary never reached SHIRA's evaluation prompt — "
            f"captured: {joined[:400]}"
        )
        assert result.get("insights", {}).get("summary") == DISTINCT, "insights must also be in the return value"
        record("TEST 1: distinctive analytics insights reach SHIRA's real evaluation prompt", PASS)
    except AssertionError as exc:
        record("TEST 1: insights reach SHIRA prompt", FAIL, str(exc)[:200])
    except Exception as exc:
        record("TEST 1: insights reach SHIRA prompt", FAIL, f"unexpected: {exc!s:.200}")
    finally:
        if cid:
            await _cleanup_campaign(db, cid)


async def test_insights_structurally_distinct_from_metrics(db, shira) -> None:
    """TEST 2: the prompt must clearly separate the authoritative numeric
    metrics from the advisory insights section — SHIRA must be able to tell
    them apart, not receive one ambiguous blob. Checks both section labels
    and that the raw metrics JSON is still present when insights are added."""
    from unittest.mock import AsyncMock, patch
    from social.campaign_manager import create_campaign

    cid = None
    try:
        campaign = await create_campaign(
            db, name="TEST_P2_INSIGHTS_2", goal="x", platforms=["facebook"],
            tone="professional", created_by="00000000-0000-0000-0000-000000000000",
        )
        cid = campaign["id"]
        fake_report = {
            "report_id": "test-report-2",
            "raw_data": {"period_days": 7, "total_posts": 1, "total_reach": 12345,
                         "total_impressions": 1, "total_engagement": 1, "total_clicks": 1,
                         "total_leads": 1, "avg_sentiment": 0.0, "engagement_rate": 0.0,
                         "click_through_rate": 0.0},
            "insights": {"summary": "ADVISORY_MARKER_XYZ"},
        }
        captured = []

        async def _fake_hf_text(*, prompt, **kw):
            captured.append(prompt)
            return '{"decision": "continue", "reasoning": "test"}'

        with patch("social.feedback_analyzer.generate_analytics_report", new=AsyncMock(return_value=fake_report)), \
             patch("BACKEND_AI_AGENTS.hf_text", new=AsyncMock(side_effect=_fake_hf_text)):
            await shira.evaluate_campaign_performance(cid, db)

        prompt = captured[0]
        assert "12345" in prompt, "the real numeric metric must still be present verbatim"
        assert "ADVISORY_MARKER_XYZ" in prompt, "the insight must be present"
        assert "המקור היחיד הסמכותי" in prompt or "נתוני ביצועים אמיתיים" in prompt, \
            "the metrics section must be explicitly labeled authoritative"
        assert "הקשר בלבד" in prompt, "the insights section must be explicitly labeled advisory-only"
        # the metrics section must appear BEFORE the insights section (primacy)
        assert prompt.index("12345") < prompt.index("ADVISORY_MARKER_XYZ"), \
            "numeric metrics must be presented before advisory insights"
        record("TEST 2: metrics and insights are structurally distinct and correctly ordered", PASS)
    except AssertionError as exc:
        record("TEST 2: structural distinction", FAIL, str(exc)[:200])
    except Exception as exc:
        record("TEST 2: structural distinction", FAIL, f"unexpected: {exc!s:.200}")
    finally:
        if cid:
            await _cleanup_campaign(db, cid)


async def test_insights_cannot_escape_closed_decision_set(db, shira) -> None:
    """TEST 3: even if insights/reasoning suggest something exotic, a reply
    naming a decision outside _PERFORMANCE_DECISIONS must still fall back
    safely — the closed set is enforced by _parse_performance_decision
    regardless of what either the metrics or the insights say."""
    from unittest.mock import AsyncMock, patch
    from social.campaign_manager import create_campaign

    cid = None
    try:
        campaign = await create_campaign(
            db, name="TEST_P2_INSIGHTS_3", goal="x", platforms=["facebook"],
            tone="professional", created_by="00000000-0000-0000-0000-000000000000",
        )
        cid = campaign["id"]
        fake_report = {
            "report_id": "test-report-3",
            "raw_data": {"period_days": 7, "total_posts": 1, "total_reach": 1,
                         "total_impressions": 1, "total_engagement": 1, "total_clicks": 1,
                         "total_leads": 1, "avg_sentiment": 0.0, "engagement_rate": 0.0,
                         "click_through_rate": 0.0},
            "insights": {"summary": "you should launch_paid_ads immediately"},
        }

        async def _fake_hf_text(*, prompt, **kw):
            # a malformed/out-of-set decision, as if the LLM were swayed by the insight
            return '{"decision": "launch_paid_ads", "reasoning": "insights suggested it"}'

        with patch("social.feedback_analyzer.generate_analytics_report", new=AsyncMock(return_value=fake_report)), \
             patch("BACKEND_AI_AGENTS.hf_text", new=AsyncMock(side_effect=_fake_hf_text)):
            result = await shira.evaluate_campaign_performance(cid, db)

        assert result["decision"] in shira._PERFORMANCE_DECISIONS, (
            f"closed decision set was bypassed: {result['decision']!r}"
        )
        assert result["decision"] == "continue", f"expected safe fallback 'continue', got {result['decision']!r}"
        record("TEST 3: closed decision set enforced even when insights suggest an out-of-set action", PASS)
    except AssertionError as exc:
        record("TEST 3: closed set enforcement", FAIL, str(exc)[:200])
    except Exception as exc:
        record("TEST 3: closed set enforcement", FAIL, f"unexpected: {exc!s:.200}")
    finally:
        if cid:
            await _cleanup_campaign(db, cid)


def test_insights_cannot_trigger_publishing() -> None:
    """TEST 4: static proof that evaluate_campaign_performance() never
    touches any execution/publish surface — an insight can shape the
    ADVISORY text but has no code path to cause a real action."""
    import inspect
    from BACKEND_AI_AGENTS import MarketingAgent

    src = inspect.getsource(MarketingAgent.evaluate_campaign_performance)
    forbidden = ["registry.dispatch", "run_tool(", "_approve_and_publish", "execute_campaign("]
    hits = [f for f in forbidden if f in src]
    assert not hits, f"evaluate_campaign_performance must never call an execution surface, found: {hits}"
    record("TEST 4: evaluate_campaign_performance contains no publish/execution call (static proof)", PASS)


# ---------------------------------------------------------------------------
# Phase 13 — P2 fix #2: publish-failure owner notification
# (2026-08-16 closure pass)
# ---------------------------------------------------------------------------

async def _seed_approved_post(db, cid: str, platform: str = "facebook") -> str:
    pid = str(uuid.uuid4())
    await db.execute(sa.text("""
        INSERT INTO social_posts (id, content, platforms, status, external_post_ids,
                                   created_by, campaign_id, created_at, updated_at)
        VALUES (CAST(:id AS uuid), 'seed content', ARRAY[:plat]::text[], 'approved',
                '{}'::jsonb, '00000000-0000-0000-0000-000000000000', CAST(:cid AS uuid), NOW(), NOW())
    """), {"id": pid, "plat": platform, "cid": cid})
    await db.commit()
    return pid


async def test_publish_success_unchanged(db, noa) -> None:
    """TEST 5: successful publication behaves exactly as before the fix —
    status becomes published, and no failure notification fires."""
    from unittest.mock import AsyncMock, patch
    from social.campaign_manager import create_campaign
    from social.tools import ToolResult

    cid = None
    try:
        campaign = await create_campaign(
            db, name="TEST_P2_PUBLISH_OK", goal="x", platforms=["facebook"],
            tone="professional", created_by="00000000-0000-0000-0000-000000000000",
        )
        cid = campaign["id"]
        await _seed_approved_post(db, cid, "facebook")

        ok_result = ToolResult(status="success", post_id="fb_123", analytics_tracking_id="trk_1")
        notify_mock = AsyncMock()
        with patch("social.tools.run_tool", new=AsyncMock(return_value=ok_result)), \
             patch("BACKEND_API_ROUTES.notify_owner", new=notify_mock):
            result = await noa.execute_campaign(cid, db)

        row = (await db.execute(sa.text(
            "SELECT status FROM social_posts WHERE campaign_id = CAST(:id AS uuid)"
        ), {"id": cid})).fetchone()
        assert row.status == "published", f"expected published, got {row.status}"
        assert result.get("posts_published") == 1
        notify_mock.assert_not_called()
        record("TEST 5: successful publication unchanged (published, no failure notification)", PASS)
    except AssertionError as exc:
        record("TEST 5: publish success unchanged", FAIL, str(exc)[:200])
    except Exception as exc:
        record("TEST 5: publish success unchanged", FAIL, f"unexpected: {exc!s:.200}")
    finally:
        if cid:
            await _cleanup_campaign(db, cid)


async def test_publish_failure_stays_approved_and_notifies_owner(db, noa) -> None:
    """TESTS 2/3/4/5 of the fix#2 spec: a failed publish must (a) leave the
    post in 'approved' (never marked published, never bypasses approval),
    (b) generate exactly one owner notification via the EXISTING
    notify_owner channel, (c) identify campaign/post/platform in that
    notification, (d) never touch any customer-facing send function."""
    from unittest.mock import AsyncMock, patch
    from social.campaign_manager import create_campaign
    from social.tools import ToolResult

    cid = None
    try:
        campaign = await create_campaign(
            db, name="TEST_P2_PUBLISH_FAIL", goal="x", platforms=["facebook"],
            tone="professional", created_by="00000000-0000-0000-0000-000000000000",
        )
        cid = campaign["id"]
        pid = await _seed_approved_post(db, cid, "facebook")

        fail_result = ToolResult(status="error", error="DISTINCTIVE_TOOL_FAILURE_998")
        notify_mock = AsyncMock()
        with patch("social.tools.run_tool", new=AsyncMock(return_value=fail_result)), \
             patch("BACKEND_API_ROUTES.notify_owner", new=notify_mock):
            result = await noa.execute_campaign(cid, db)
            await asyncio.sleep(0.05)  # let the fire-and-forget notify task run

        row = (await db.execute(sa.text(
            "SELECT status FROM social_posts WHERE campaign_id = CAST(:id AS uuid)"
        ), {"id": cid})).fetchone()
        assert row.status == "approved", f"failed publish must stay 'approved', got {row.status}"
        assert result.get("posts_published") == 0
        record("TEST 5b: failed publication stays 'approved' — never marked published, never bypasses approval", PASS)

        assert notify_mock.await_count == 1, f"expected exactly one owner notification, got {notify_mock.await_count}"
        call = notify_mock.await_args
        args, kwargs = call.args, call.kwargs
        full_text = " ".join(str(a) for a in args) + " " + " ".join(f"{k}={v}" for k, v in kwargs.items())
        assert cid[:8] in full_text, "notification must identify the campaign"
        assert pid[:8] in full_text, "notification must identify the post"
        assert "facebook" in full_text, "notification must identify the platform"
        assert "DISTINCTIVE_TOOL_FAILURE_998" in full_text, "notification must include the actual failure reason"
        assert "לא פורסם" in full_text, "notification must state the content was NOT published"
        record("TEST 6: owner notification generated, identifies campaign/post/platform/reason", PASS)

        # Confirms this reuses the EXISTING owner-only channel: the fix calls
        # ONLY notify_owner (never any customer-facing send function) — and
        # notify_owner's own implementation routes exclusively through
        # _wa_send_update (the owner's WhatsApp), never a customer path.
        import inspect as _inspect
        helper_src = _inspect.getsource(noa.execute_campaign).split("def _notify_publish_failure")[1].split("\n\n        tool_map")[0]
        assert "notify_owner" in helper_src, "failure handler must call the existing notify_owner"
        customer_send_markers = ["send_whatsapp_message", "send_customer", "_customer_send", "process_user_message"]
        hits = [m for m in customer_send_markers if m in helper_src]
        assert not hits, f"failure handler must never touch a customer-facing send path, found: {hits}"
        record("TEST 7: failure notification uses the existing owner-only notify_owner channel (no customer send)", PASS)
    except AssertionError as exc:
        record("TEST publish failure notification", FAIL, str(exc)[:300])
    except Exception as exc:
        record("TEST publish failure notification", FAIL, f"unexpected: {exc!s:.200}")
    finally:
        if cid:
            await _cleanup_campaign(db, cid)


async def test_publish_failure_repeat_no_duplicate_posts(db, noa) -> None:
    """TEST 8: calling execute_campaign again after a failure must retry the
    SAME approved row (dedup via Redis alert_key handles alert-spam
    separately) — never insert a duplicate SocialPost."""
    from unittest.mock import AsyncMock, patch
    from social.campaign_manager import create_campaign
    from social.tools import ToolResult

    cid = None
    try:
        campaign = await create_campaign(
            db, name="TEST_P2_PUBLISH_RETRY", goal="x", platforms=["facebook"],
            tone="professional", created_by="00000000-0000-0000-0000-000000000000",
        )
        cid = campaign["id"]
        await _seed_approved_post(db, cid, "facebook")

        fail_result = ToolResult(status="error", error="still failing")
        with patch("social.tools.run_tool", new=AsyncMock(return_value=fail_result)), \
             patch("BACKEND_API_ROUTES.notify_owner", new=AsyncMock()):
            await noa.execute_campaign(cid, db)
            await noa.execute_campaign(cid, db)
            await noa.execute_campaign(cid, db)

        count = await _post_count(db, cid)
        assert count == 1, f"repeated failed-publish retries must not create duplicates, got {count} rows"
        record("TEST 8: repeated publish-failure handling creates zero duplicate SocialPosts", PASS)
    except AssertionError as exc:
        record("TEST 8: no duplicate posts on retry", FAIL, str(exc)[:200])
    except Exception as exc:
        record("TEST 8: no duplicate posts on retry", FAIL, f"unexpected: {exc!s:.200}")
    finally:
        if cid:
            await _cleanup_campaign(db, cid)


# ---------------------------------------------------------------------------
# Phase 14 — pre-publish truth guard (2026-08-16, organic-channel closure)
# ---------------------------------------------------------------------------

def test_truth_guard_unit() -> None:
    """TEST 1: deterministic unit proof against the exact TikTok fabrication
    (BREMBO 299₪ + free shipping) that triggered this fix, plus a clean-text
    negative control."""
    from social import coherence_guard as cg

    bad = "‏המחיר הממוצע לרפידות BREMBO הוא 299 ₪, והמשלוח חינם לכל הארץ."
    ok, reason = cg.no_fabricated_commercial_claims(bad)
    assert ok is False and reason, f"must flag the real fabricated TikTok text, got ok={ok}"
    record("TEST 1: truth guard flags the exact real fabricated TikTok content", PASS)

    clean = ("‏ב AutoSpareFinder מזינים את מספר הלוחית, המערכת מוצאת את החלק "
             "המתאים, ומשווה מחירים בין כמה ספקים.")
    ok2, reason2 = cg.no_fabricated_commercial_claims(clean)
    assert ok2 is True and reason2 is None, f"must NOT flag clean content, got ok={ok2} reason={reason2}"
    record("TEST 1: truth guard does not flag clean, unverified-claim-free content", PASS)


async def test_truth_guard_wired_into_execute_campaign(db, noa) -> None:
    """TEST 2: the guard must actually intercept fabricated content INSIDE
    the real execute_campaign() flow — routing it to status='rejected'
    (never offered as approvable, per _resolve_post/_list_pending_posts only
    surfacing pending_approval) — while a clean platform in the SAME call is
    completely unaffected (guards fail independently per platform, matching
    the collector-isolation principle applied elsewhere this pass)."""
    from unittest.mock import AsyncMock, patch
    from social.campaign_manager import create_campaign
    from agents.owner_console import _list_pending_posts

    FABRICATED = "המחיר הממוצע הוא 299 ₪ והמשלוח חינם לכל הארץ!"
    CLEAN = "פוסט תקין לגמרי בלי שום טענה מסחרית לא מאומתת."
    calls = {"n": 0}

    async def _fake_hf_text(*, prompt, **kw):
        calls["n"] += 1
        return FABRICATED if calls["n"] == 1 else CLEAN

    cid = None
    try:
        campaign = await create_campaign(
            db, name="TEST_TRUTH_GUARD_WIRING", goal="x", platforms=["tiktok", "facebook"],
            tone="professional", created_by="00000000-0000-0000-0000-000000000000",
        )
        cid = campaign["id"]

        with patch("BACKEND_AI_AGENTS.hf_text", new=AsyncMock(side_effect=_fake_hf_text)):
            await noa.execute_campaign(cid, db)

        rows = (await db.execute(sa.text(
            "SELECT platforms, content, status, rejection_reason FROM social_posts "
            "WHERE campaign_id = CAST(:id AS uuid)"
        ), {"id": cid})).fetchall()
        assert len(rows) == 2, f"expected 2 posts (1 per platform), got {len(rows)}"
        flagged = [r for r in rows if r.status == "rejected"]
        clean_rows = [r for r in rows if r.status == "pending_approval"]
        assert len(flagged) == 1, f"expected exactly 1 rejected post, got {len(flagged)}: {rows}"
        assert len(clean_rows) == 1, f"expected exactly 1 pending_approval post, got {len(clean_rows)}"
        assert "בדיקת אמינות" in (flagged[0].rejection_reason or ""), \
            f"rejection_reason must reference the truth guard: {flagged[0].rejection_reason!r}"
        record("TEST 2: fabricated content -> rejected; clean content on the other "
               "platform -> pending_approval, unaffected", PASS)

        pending_content = [p["content"] for p in await _list_pending_posts(db, limit=50)]
        assert FABRICATED not in pending_content, "fabricated content must never reach the approvable list"
        record("TEST 2: flagged content never appears in the owner's approvable post list", PASS)
    except AssertionError as exc:
        record("TEST 2: truth guard wired into execute_campaign", FAIL, str(exc)[:300])
    except Exception as exc:
        record("TEST 2: truth guard wired into execute_campaign", FAIL, f"unexpected: {exc!s:.200}")
    finally:
        if cid:
            await _cleanup_campaign(db, cid)


# ---------------------------------------------------------------------------
# Phase 15 — Discord engagement collector (2026-08-16, organic-channel closure)
# ---------------------------------------------------------------------------

async def test_discord_engagement_collector(db) -> None:
    """Real internal parsing + real campaign attribution + real DB insert,
    only the external Discord REST call mocked. Proves the row, not just the
    return value."""
    from unittest.mock import patch
    from social import feedback_analyzer as fa
    from social.campaign_manager import create_campaign, prepare_campaign_content

    cid = None
    try:
        campaign = await create_campaign(
            db, name="TEST_DISCORD_COLLECTOR", goal="x", platforms=["discord"],
            tone="professional", created_by="00000000-0000-0000-0000-000000000000",
        )
        cid = campaign["id"]
        created = await prepare_campaign_content(
            db, campaign_id=cid, content_by_platform={"discord": "discord test post"}
        )
        pid = created[0]["post_id"]
        await db.execute(sa.text(
            "UPDATE social_posts SET status='published', published_at=NOW(), "
            "external_post_ids = external_post_ids || '{\"discord\": \"999888777\"}'::jsonb "
            "WHERE id=CAST(:id AS uuid)"
        ), {"id": pid})
        await db.commit()

        fake_message = {"id": "999888777", "reactions": [
            {"emoji": {"name": "👍"}, "count": 7}, {"emoji": {"name": "❤️"}, "count": 3},
        ]}
        with patch("social.engagement._http_json", return_value=fake_message), \
             patch("social.engagement.discord_configured", return_value=True), \
             patch("social.engagement._discord_channels", return_value=["1528455754787328122"]):
            collected = await fa._collect_discord(db)
        assert collected == 1, f"expected 1 collected, got {collected}"

        ev = (await db.execute(sa.text(
            "SELECT post_id, campaign_id, platform, likes FROM engagement_events WHERE post_id=CAST(:id AS uuid)"
        ), {"id": pid})).fetchone()
        assert ev is not None, "no engagement_events row was created by the real collector"
        assert str(ev.campaign_id) == cid, f"attribution must use SocialPost.campaign_id, got {ev.campaign_id}"
        assert ev.likes == 10, f"reaction sum must be 7+3=10, got {ev.likes}"
        record("Discord collector: real row inserted, correct campaign_id (FK, not JSONB), correct reaction sum", PASS)
    except AssertionError as exc:
        record("Discord collector", FAIL, str(exc)[:300])
    except Exception as exc:
        record("Discord collector", FAIL, f"unexpected: {exc!s:.200}")
    finally:
        if cid:
            await _cleanup_campaign(db, cid)


# ---------------------------------------------------------------------------
# Phase 16 — TikTok engagement collector (2026-08-16, deep TikTok verification)
# ---------------------------------------------------------------------------

async def test_tiktok_collector_no_token_graceful_stub(db) -> None:
    """TEST 1: the real, current production state — no owner consent yet —
    must return 0 gracefully (same contract as _collect_instagram), never
    raise, never fabricate a metric."""
    from social import feedback_analyzer as fa
    from social.tiktok_publisher import get_stored_user_token

    try:
        tok = await get_stored_user_token(db)
        assert tok is None, f"expected no stored token in real production state, found: {bool(tok)}"
        collected = await fa._collect_tiktok(db)
        assert collected == 0, f"expected 0 with no token, got {collected}"
        record("TEST 1: TikTok collector returns 0 gracefully with no owner-authorized token (real current state)", PASS)
    except AssertionError as exc:
        record("TEST 1: TikTok no-token stub", FAIL, str(exc)[:200])
    except Exception as exc:
        record("TEST 1: TikTok no-token stub", FAIL, f"unexpected: {exc!s:.200}")


async def test_tiktok_collector_with_token(db) -> None:
    """TEST 2: once a token exists (simulated — never a real owner
    credential), the collector must correlate publish_id -> video_id ->
    metrics and insert a real, correctly-attributed engagement_events row.
    Only the external TikTok API boundary is mocked."""
    from unittest.mock import patch
    from social import feedback_analyzer as fa
    from social.tiktok_publisher import store_user_token
    from social.campaign_manager import create_campaign, prepare_campaign_content

    cid = None
    try:
        await store_user_token(
            db, access_token="TEST_FAKE_ACCESS", refresh_token="TEST_FAKE_REFRESH",
            open_id="test_fake_open_id", expires_in=3600, scope="user.info.basic,video.list",
        )
        campaign = await create_campaign(
            db, name="TEST_TIKTOK_COLLECTOR_2", goal="x", platforms=["tiktok"],
            tone="professional", created_by="00000000-0000-0000-0000-000000000000",
        )
        cid = campaign["id"]
        created = await prepare_campaign_content(
            db, campaign_id=cid, content_by_platform={"tiktok": "tiktok test post"}
        )
        pid = created[0]["post_id"]
        await db.execute(sa.text(
            "UPDATE social_posts SET status='published', published_at=NOW(), "
            "external_post_ids = external_post_ids || '{\"tiktok\": \"PUBLISH_ID_777\"}'::jsonb "
            "WHERE id=CAST(:id AS uuid)"
        ), {"id": pid})
        await db.commit()

        fake_status = {"publicly_available_post_id": ["VIDEO_ID_999"], "status": "PUBLISH_COMPLETE"}
        fake_metrics = {"VIDEO_ID_999": {"id": "VIDEO_ID_999", "view_count": 5000,
                                          "like_count": 300, "comment_count": 20, "share_count": 15}}
        with patch("social.tiktok_publisher.fetch_publish_status", return_value=fake_status), \
             patch("social.tiktok_publisher.fetch_video_metrics", return_value=fake_metrics):
            collected = await fa._collect_tiktok(db)
        assert collected == 1, f"expected 1 collected, got {collected}"

        ev = (await db.execute(sa.text(
            "SELECT post_id, campaign_id, platform, likes, comments, shares, reach "
            "FROM engagement_events WHERE post_id=CAST(:id AS uuid)"
        ), {"id": pid})).fetchone()
        assert ev is not None, "no engagement_events row was created"
        assert str(ev.campaign_id) == cid, f"attribution must use SocialPost.campaign_id, got {ev.campaign_id}"
        assert ev.likes == 300 and ev.comments == 20 and ev.shares == 15 and ev.reach == 5000
        record("TEST 2: TikTok collector correlates publish_id->video_id->metrics and inserts correctly (token simulated)", PASS)
    except AssertionError as exc:
        record("TEST 2: TikTok collector with token", FAIL, str(exc)[:300])
    except Exception as exc:
        record("TEST 2: TikTok collector with token", FAIL, f"unexpected: {exc!s:.200}")
    finally:
        if cid:
            await _cleanup_campaign(db, cid)
        await db.execute(sa.text("DELETE FROM system_settings WHERE key='tiktok_oauth_user_token'"))
        await db.commit()


def test_tiktok_authorize_url_well_formed() -> None:
    """TEST 3: the owner-facing consent URL must be correctly constructed —
    right host, right redirect_uri (must exactly match the callback), right
    scopes, response_type=code. Never a fabricated/placeholder URL."""
    from social.tiktok_publisher import get_authorize_url, REDIRECT_URI

    url = get_authorize_url(state="test")
    assert url.startswith("https://www.tiktok.com/v2/auth/authorize/"), url
    assert "response_type=code" in url
    assert "video.list" in url
    from urllib.parse import unquote
    assert REDIRECT_URI in unquote(url), "redirect_uri in the URL must exactly match the callback's own redirect_uri"
    record("TEST 3: TikTok authorize URL well-formed with matching redirect_uri and analytics scope", PASS)


# ---------------------------------------------------------------------------
# Phase 17 — Telegram reaction-count correlation (2026-08-16, deep verification)
# ---------------------------------------------------------------------------

async def test_telegram_reaction_correlation(db) -> None:
    """Verifies the correlation+attribution+insert logic used by
    routes/webhooks.py's new message_reaction_count branch (tested at the
    logic level here since the live route requires a running server /
    real webhook secret; the DB-facing logic is identical and is what
    actually determines correctness)."""
    from social.feedback_analyzer import _insert_engagement_event
    from social.campaign_manager import create_campaign, prepare_campaign_content

    cid = None
    try:
        campaign = await create_campaign(
            db, name="TEST_TG_REACTION_2", goal="x", platforms=["telegram"],
            tone="professional", created_by="00000000-0000-0000-0000-000000000000",
        )
        cid = campaign["id"]
        created = await prepare_campaign_content(
            db, campaign_id=cid, content_by_platform={"telegram": "telegram test post"}
        )
        pid = created[0]["post_id"]
        await db.execute(sa.text(
            "UPDATE social_posts SET status='published', published_at=NOW(), "
            "external_post_ids = external_post_ids || '{\"telegram\": \"777333\"}'::jsonb "
            "WHERE id=CAST(:id AS uuid)"
        ), {"id": pid})
        await db.commit()

        reaction_update = {
            "chat": {"id": -100999888777}, "message_id": 777333,
            "reactions": [{"total_count": 12}, {"total_count": 5}],
        }
        r_message_id = reaction_update.get("message_id")
        total = sum(int(r.get("total_count") or 0) for r in reaction_update.get("reactions") or [])
        row = (await db.execute(sa.text("""
            SELECT id, campaign_id FROM social_posts
            WHERE external_post_ids->>'telegram' = :mid AND 'telegram' = ANY(platforms)
            LIMIT 1
        """), {"mid": str(r_message_id)})).fetchone()
        assert row is not None, "correlation query must find the published post by its telegram message_id"
        await _insert_engagement_event(
            db, post_id=str(row.id), platform="telegram", external_post_id=str(r_message_id),
            campaign_id=str(row.campaign_id) if row.campaign_id else None, likes=total,
        )
        await db.commit()

        ev = (await db.execute(sa.text(
            "SELECT post_id, campaign_id, platform, likes FROM engagement_events WHERE external_post_id=:mid"
        ), {"mid": str(r_message_id)})).fetchone()
        assert ev is not None
        assert str(ev.campaign_id) == cid
        assert ev.likes == 17
        record("Telegram reaction correlation: real message_id lookup, real campaign_id attribution, correct sum", PASS)
    except AssertionError as exc:
        record("Telegram reaction correlation", FAIL, str(exc)[:300])
    except Exception as exc:
        record("Telegram reaction correlation", FAIL, f"unexpected: {exc!s:.200}")
    finally:
        if cid:
            await _cleanup_campaign(db, cid)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def _get_db():
    from BACKEND_DATABASE_MODELS import async_session_factory
    return async_session_factory()


async def run() -> int:
    # Phase 1: sync tests
    test_agent_hierarchy()
    test_tool_registry_sync()
    await test_tool_registry_async()
    test_integration_layer()

    # Phase 2: async tests need a DB session
    from BACKEND_AI_AGENTS import get_agent
    noa = get_agent("social_media_manager_agent")
    shira = get_agent("marketing_agent")

    # LLM + DB tests
    plan = await test_campaign_plan_generation(noa)
    contents = await test_content_generation(noa)

    async with await _get_db() as db:
        cid = await test_campaign_creation(db, plan)
        await test_campaign_execution(db, cid, noa)
        await test_feedback_loop(db, cid)
        await test_db_audit(db, cid)
        if cid:
            await _cleanup_campaign(db, cid)  # 2026-08-16 fix: Phase 9's own campaign was never cleaned up

        # Phase 10 — creative_angle handoff completion (2026-08-15e)
        await test_creative_angle_reaches_generation_input(db, noa)
        await test_creative_angle_platform_mapping(db, noa)
        await test_creative_angle_persisted_plan_consumption(db, noa)
        await test_creative_angle_backward_compatible(db, noa)

        # Phase 11 — idempotency defect fix (2026-08-15f)
        await test_execute_campaign_triple_idempotent_and_approval_gated(db, noa)
        await test_execute_campaign_state_specific_behavior(db, noa)
        await test_execute_campaign_dry_run_never_persists(db, noa)

        # Phase 12 — P2 fix #1: analytics insights reach SHIRA (2026-08-16)
        await test_insights_reach_shira_evaluation_prompt(db, shira)
        await test_insights_structurally_distinct_from_metrics(db, shira)
        await test_insights_cannot_escape_closed_decision_set(db, shira)
        test_insights_cannot_trigger_publishing()

        # Phase 13 — P2 fix #2: publish-failure owner notification (2026-08-16)
        await test_publish_success_unchanged(db, noa)
        await test_publish_failure_stays_approved_and_notifies_owner(db, noa)
        await test_publish_failure_repeat_no_duplicate_posts(db, noa)

        # Phase 14 — pre-publish truth guard (2026-08-16, organic-channel closure)
        test_truth_guard_unit()
        await test_truth_guard_wired_into_execute_campaign(db, noa)

        # Phase 15 — Discord engagement collector (2026-08-16, organic-channel closure)
        await test_discord_engagement_collector(db)

        # Phase 16 — TikTok engagement collector (2026-08-16, deep TikTok verification)
        await test_tiktok_collector_no_token_graceful_stub(db)
        await test_tiktok_collector_with_token(db)
        test_tiktok_authorize_url_well_formed()

        # Phase 17 — Telegram reaction correlation (2026-08-16, deep verification)
        await test_telegram_reaction_correlation(db)

    return sum(1 for r in results if r["status"] == FAIL)


def print_report() -> int:
    print("\n" + "=" * 60)
    print("END-TO-END CAMPAIGN TEST REPORT")
    print("=" * 60)
    passed  = sum(1 for r in results if r["status"] == PASS)
    failed  = sum(1 for r in results if r["status"] == FAIL)
    skipped = sum(1 for r in results if r["status"] == SKIP)
    print(f"\nTotal: {len(results)}  |  ✅ Passed: {passed}  |  ❌ Failed: {failed}  |  ⚠️  Skipped: {skipped}")
    if failed:
        print("\nFailed:")
        for r in results:
            if r["status"] == FAIL:
                print(f"  - {r['name']}")
                if r["detail"]:
                    print(f"    {r['detail'][:120]}")
    print()
    return failed


if __name__ == "__main__":
    print("AutoSpareFinder — End-to-End Campaign Test (Phase 9)")
    print("Campaign: Toyota Brake Parts Israel")
    failed_count = asyncio.run(run())
    exit_code = print_report()
    sys.exit(1 if exit_code else 0)
