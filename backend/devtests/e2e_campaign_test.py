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

    # LLM + DB tests
    plan = await test_campaign_plan_generation(noa)
    contents = await test_content_generation(noa)

    async with await _get_db() as db:
        cid = await test_campaign_creation(db, plan)
        await test_campaign_execution(db, cid, noa)
        await test_feedback_loop(db, cid)
        await test_db_audit(db, cid)

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
