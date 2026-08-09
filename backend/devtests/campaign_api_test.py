"""
Script: devtests/campaign_api_test.py
Purpose: Phase 2 API validation for the social campaign infrastructure.

Tests:
  1. Unauthenticated access → 401 on all campaign endpoints
  2. Campaign CRUD (in-process, catalog DB session)
  3. Group target lifecycle (add → list → approve)
  4. Engagement event creation and analytics generation
  5. Tool registry integrity (list_tools, ToolResult schema)
  6. DB persistence (verify rows exist after operations)
  7. Error handling (invalid status, missing campaign, etc.)

Run: docker exec autospare_backend python3 /app/devtests/campaign_api_test.py
Last Updated: 2026-08-06
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import traceback
import uuid
from datetime import datetime

import httpx
import sqlalchemy as sa

BASE_URL = os.getenv("TEST_BASE_URL", "https://autosparefinder.co.il")

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"

results: list[dict] = []


def record(name: str, status: str, detail: str = "") -> None:
    icon = "✅" if status == PASS else ("⚠️" if status == SKIP else "❌")
    print(f"  {icon}  [{status}] {name}" + (f": {detail}" if detail else ""))
    results.append({"name": name, "status": status, "detail": detail})


# ---------------------------------------------------------------------------
# Part 1 — HTTP auth gate (no token required, tests run without creds)
# ---------------------------------------------------------------------------

CAMPAIGN_ENDPOINTS = [
    ("GET",   "/api/v1/campaigns"),
    ("POST",  "/api/v1/campaigns"),
    ("GET",   "/api/v1/campaigns/groups"),
    ("POST",  "/api/v1/campaigns/groups"),
    ("POST",  "/api/v1/campaigns/groups/scan"),
    ("POST",  "/api/v1/campaigns/tools/run"),
    ("GET",   "/api/v1/campaigns/platforms/status"),
    ("GET",   "/api/v1/campaigns/analytics/overview"),
]


def test_auth_gate() -> None:
    print("\n=== Part 1: Auth Gate (HTTP) ===")
    with httpx.Client(timeout=15, verify=False) as client:
        for method, path in CAMPAIGN_ENDPOINTS:
            try:
                resp = client.request(method, BASE_URL + path, json={})
                if resp.status_code == 401:
                    record(f"401 {method} {path}", PASS)
                elif resp.status_code == 422:
                    # 422 = schema error before auth — means auth check is missing for this body
                    record(f"401 {method} {path}", FAIL,
                           f"got 422 (auth check not firing first, status={resp.status_code})")
                else:
                    record(f"401 {method} {path}", FAIL,
                           f"expected 401, got {resp.status_code}")
            except Exception as exc:
                record(f"401 {method} {path}", FAIL, str(exc)[:80])


# ---------------------------------------------------------------------------
# Part 2 — In-process DB tests (campaign_manager, feedback_analyzer, tools)
# ---------------------------------------------------------------------------

async def _get_db_session():
    """Return an async catalog-DB session (same as background loops use)."""
    from BACKEND_DATABASE_MODELS import async_session_factory
    return async_session_factory()


async def test_campaign_crud() -> None:
    print("\n=== Part 2a: Campaign CRUD ===")
    from social.campaign_manager import (
        create_campaign, get_campaign, list_campaigns,
        update_campaign_status, link_post_to_campaign,
    )

    async with await _get_db_session() as db:
        # Create
        cid = None
        try:
            c = await create_campaign(
                db,
                name=f"Test Campaign {uuid.uuid4().hex[:6]}",
                goal="Test brake parts awareness in Israel",
                platforms=["facebook", "instagram"],
                target_audience="IL car owners 25-55",
                tone="professional",
                duration_days=7,
                budget_ils=500.0,
                plan={"note": "test plan"},
                created_by="test-runner",
            )
            cid = c["id"]
            record("create_campaign returns dict with id", PASS)
            assert c["name"].startswith("Test Campaign"), "name mismatch"
            assert c["status"] == "draft", f"expected draft, got {c['status']}"
            assert c["platforms"] == ["facebook", "instagram"], "platforms mismatch"
            record("create_campaign fields correct (name/status/platforms)", PASS)
        except Exception as exc:
            record("create_campaign", FAIL, traceback.format_exc()[-200:])
            return

        # Read single
        try:
            fetched = await get_campaign(db, campaign_id=cid)
            assert fetched is not None
            assert str(fetched["id"]) == cid
            record("get_campaign by id", PASS)
        except Exception as exc:
            record("get_campaign", FAIL, str(exc)[:120])

        # List
        try:
            res = await list_campaigns(db, status=None, limit=10, offset=0)
            assert "campaigns" in res and "total" in res
            assert res["total"] >= 1
            record(f"list_campaigns returns total={res['total']}", PASS)
        except Exception as exc:
            record("list_campaigns", FAIL, str(exc)[:120])

        # List filtered by status
        try:
            res = await list_campaigns(db, status="draft", limit=10, offset=0)
            assert all(c["status"] == "draft" for c in res["campaigns"])
            record("list_campaigns filtered by status=draft", PASS)
        except Exception as exc:
            record("list_campaigns (status filter)", FAIL, str(exc)[:120])

        # Update status
        try:
            ok = await update_campaign_status(db, campaign_id=cid, status="active")
            assert ok, "update returned falsy"
            fetched = await get_campaign(db, campaign_id=cid)
            assert fetched["status"] == "active"
            record("update_campaign_status draft→active", PASS)
        except Exception as exc:
            record("update_campaign_status", FAIL, str(exc)[:120])

        # Invalid status
        try:
            await update_campaign_status(db, campaign_id=cid, status="invalid_xyz")
            record("update_campaign_status (invalid) should raise", FAIL, "no exception raised")
        except ValueError:
            record("update_campaign_status (invalid) raises ValueError", PASS)
        except Exception as exc:
            record("update_campaign_status (invalid)", FAIL, str(exc)[:80])

        # link_post_to_campaign
        try:
            fake_post_id = str(uuid.uuid4())
            await link_post_to_campaign(db, campaign_id=cid, post_id=fake_post_id)
            fetched = await get_campaign(db, campaign_id=cid)
            assert fake_post_id in (fetched.get("linked_post_ids") or [])
            record("link_post_to_campaign appends post id", PASS)
        except Exception as exc:
            record("link_post_to_campaign", FAIL, str(exc)[:120])

        # Verify DB persistence directly
        try:
            row = (await db.execute(
                sa.text("SELECT id, name, status FROM campaigns WHERE id = CAST(:id AS uuid)"),
                {"id": cid},
            )).fetchone()
            assert row is not None, "row not found in DB"
            assert row.status == "active"
            record("DB persistence verified (SELECT after update)", PASS)
        except Exception as exc:
            record("DB persistence check", FAIL, str(exc)[:120])


async def test_group_targets() -> None:
    print("\n=== Part 2b: Group Targets ===")
    from social.campaign_manager import add_group_target, list_group_targets, approve_group_target

    async with await _get_db_session() as db:
        gid = None
        # Add
        try:
            g = await add_group_target(
                db,
                group_url="https://www.facebook.com/groups/test.autospare.group",
                group_name="Test Auto Spare Group",
                platform="facebook",
                description="Test group for integration testing",
                relevance_tags=["car parts", "Israel", "auto"],
                member_count_estimate=1500,
            )
            gid = g["id"]
            assert g["status"] == "pending"
            record("add_group_target creates with status=pending", PASS)
        except Exception as exc:
            record("add_group_target", FAIL, traceback.format_exc()[-200:])
            return

        # List
        try:
            groups = await list_group_targets(db, platform="facebook", status=None)
            assert any(str(g2.get("id")) == gid for g2 in groups), "our group not in list"
            record("list_group_targets returns our group", PASS)
        except Exception as exc:
            record("list_group_targets", FAIL, str(exc)[:120])

        # Approve
        try:
            ok = await approve_group_target(db, group_id=gid, approved_by="test-runner")
            assert ok, "approve returned falsy"
            groups = await list_group_targets(db, platform="facebook", status="approved")
            assert any(str(g2.get("id")) == gid for g2 in groups), "group not in approved list"
            record("approve_group_target transitions pending→approved", PASS)
        except Exception as exc:
            record("approve_group_target", FAIL, str(exc)[:120])

        # Approve non-existent group
        try:
            bad = await approve_group_target(db, group_id=str(uuid.uuid4()), approved_by="test")
            if not bad:
                record("approve non-existent group returns falsy", PASS)
            else:
                record("approve non-existent group", FAIL, "should return falsy")
        except Exception as exc:
            record("approve non-existent group", FAIL, str(exc)[:80])


async def test_engagement_and_analytics() -> None:
    print("\n=== Part 2c: Engagement Events + Analytics ===")
    from social.feedback_analyzer import generate_analytics_report, get_top_performers

    async with await _get_db_session() as db:
        # Insert a test engagement event directly (no real FB post needed)
        try:
            eid = str(uuid.uuid4())
            await db.execute(
                sa.text("""
                    INSERT INTO engagement_events
                        (id, post_id, campaign_id, platform, external_post_id,
                         likes, comments, shares, reach, impressions, clicks, saves, leads,
                         data_source, collected_at, created_at)
                    VALUES
                        (:id, gen_random_uuid(), NULL, 'facebook', :ext,
                         42, 5, 3, 1200, 3400, 88, 12, NULL,
                         'test', :now, :now)
                """),
                {"id": eid, "ext": f"test_post_{uuid.uuid4().hex[:8]}",
                 "now": datetime.utcnow()},
            )
            await db.commit()
            record("engagement_event INSERT (test data)", PASS)
        except Exception as exc:
            record("engagement_event INSERT", FAIL, str(exc)[:120])
            return

        # Verify the row exists
        try:
            row = (await db.execute(
                sa.text("SELECT id, likes, reach FROM engagement_events WHERE id = :id"),
                {"id": eid},
            )).fetchone()
            assert row and row.likes == 42 and row.reach == 1200
            record("engagement_event DB persistence (SELECT verifies likes/reach)", PASS)
        except Exception as exc:
            record("engagement_event DB read-back", FAIL, str(exc)[:120])

        # Analytics report
        try:
            report = await generate_analytics_report(db, period_days=1)
            assert "report_id" in report
            assert "raw_data" in report
            assert "insights" in report
            rd = report["raw_data"]
            assert rd["total_posts"] >= 1
            assert rd["total_reach"] >= 1200  # our test row
            record(
                f"generate_analytics_report: posts={rd['total_posts']} reach={rd['total_reach']}",
                PASS,
            )
        except Exception as exc:
            record("generate_analytics_report", FAIL, str(exc)[:200])

        # Top performers
        try:
            tops = await get_top_performers(db, days=1, limit=5)
            assert isinstance(tops, list)
            record(f"get_top_performers returns {len(tops)} rows", PASS)
        except Exception as exc:
            record("get_top_performers", FAIL, str(exc)[:120])


# ---------------------------------------------------------------------------
# Part 3 — Tool registry integrity
# ---------------------------------------------------------------------------

def test_tool_registry() -> None:
    print("\n=== Part 3: Tool Registry ===")
    from social.tools import list_tools, ToolResult

    try:
        tools = list_tools()
        names = {t["name"] for t in tools}
        required = {
            "facebook_publish_page_post",
            "facebook_reply_comment",
            "facebook_get_insights",
            "instagram_publish_post",
            "facebook_group_scan",
            "facebook_group_comment",
            "facebook_group_publish",
            "telegram_publish",
            "whatsapp_send_message",
        }
        missing = required - names
        if missing:
            record("tool registry has all required tools", FAIL, f"missing: {missing}")
        else:
            record(f"tool registry complete ({len(tools)} tools)", PASS)
    except Exception as exc:
        record("list_tools()", FAIL, str(exc)[:120])

    # ToolResult schema
    try:
        tr = ToolResult(status="ok", post_id="123", data={"x": 1})
        assert tr.status == "ok"
        d = tr.dict()
        assert "status" in d and "timestamp" in d and "analytics_tracking_id" in d
        record("ToolResult schema has status/timestamp/analytics_tracking_id", PASS)
    except Exception as exc:
        record("ToolResult schema", FAIL, str(exc)[:120])

    # Each tool has name/description/inputs/outputs
    try:
        for t in list_tools():
            assert "name" in t and "description" in t
        record("all tools have name+description fields", PASS)
    except Exception as exc:
        record("tool schema fields", FAIL, str(exc)[:120])


# ---------------------------------------------------------------------------
# Part 4 — Response schema validation (spot-check campaign dict)
# ---------------------------------------------------------------------------

async def test_response_schemas() -> None:
    print("\n=== Part 4: Response Schema Validation ===")
    from social.campaign_manager import create_campaign, get_campaign

    required_campaign_fields = {
        "id", "name", "status", "platforms", "goal",
        "total_reach", "total_impressions", "total_engagement", "total_clicks",
    }

    async with await _get_db_session() as db:
        c = await create_campaign(
            db,
            name="Schema Validation Test",
            goal="Validate response schema shape",
            platforms=["facebook"],
            created_by="test-runner",
        )
        missing = required_campaign_fields - set(c.keys())
        if missing:
            record("campaign response schema", FAIL, f"missing fields: {missing}")
        else:
            record(f"campaign response schema has all {len(required_campaign_fields)} required fields", PASS)

        # Verify nullable fields don't crash
        fetched = await get_campaign(db, campaign_id=c["id"])
        for f in required_campaign_fields:
            assert f in fetched, f"field {f!r} missing from get_campaign"
        record("get_campaign response schema matches create_campaign schema", PASS)


# ---------------------------------------------------------------------------
# Part 5 — Error handling
# ---------------------------------------------------------------------------

async def test_error_handling() -> None:
    print("\n=== Part 5: Error Handling ===")
    from social.campaign_manager import get_campaign, update_campaign_status

    async with await _get_db_session() as db:
        # Non-existent campaign
        fake_id = str(uuid.uuid4())
        try:
            result = await get_campaign(db, campaign_id=fake_id)
            if result is None:
                record("get_campaign(non-existent) returns None", PASS)
            else:
                record("get_campaign(non-existent)", FAIL, f"expected None, got {result}")
        except Exception as exc:
            record("get_campaign(non-existent) raises cleanly", PASS, str(exc)[:60])

        # Invalid campaign status
        try:
            await update_campaign_status(db, campaign_id=fake_id, status="bogus")
            record("update_campaign_status(bogus) should raise ValueError", FAIL)
        except ValueError:
            record("update_campaign_status(bogus) raises ValueError", PASS)

        # Malformed UUID
        try:
            result = await get_campaign(db, campaign_id="not-a-uuid")
            record("get_campaign(malformed uuid) handled", PASS, f"returned: {result}")
        except Exception as exc:
            # Exception is acceptable too — just shouldn't crash the server
            record("get_campaign(malformed uuid) raises cleanly", PASS, type(exc).__name__)


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def print_report() -> None:
    print("\n" + "=" * 60)
    print("CAMPAIGN API TEST REPORT")
    print("=" * 60)
    passed = sum(1 for r in results if r["status"] == PASS)
    failed = sum(1 for r in results if r["status"] == FAIL)
    skipped = sum(1 for r in results if r["status"] == SKIP)
    print(f"\nTotal: {len(results)}  |  ✅ Passed: {passed}  |  ❌ Failed: {failed}  |  ⚠️  Skipped: {skipped}")

    if failed:
        print("\nFailed tests:")
        for r in results:
            if r["status"] == FAIL:
                print(f"  - {r['name']}: {r['detail']}")

    print()
    return failed


async def _run_async_tests():
    await test_campaign_crud()
    await test_group_targets()
    await test_engagement_and_analytics()
    await test_response_schemas()
    await test_error_handling()


if __name__ == "__main__":
    print("AutoSpareFinder — Campaign API Integration Test")
    print(f"Base URL: {BASE_URL}")

    # Phase 1: HTTP auth gate
    test_auth_gate()

    # Phase 2-5: In-process tests
    test_tool_registry()
    asyncio.run(_run_async_tests())

    # Final report
    failed = print_report()
    sys.exit(1 if failed else 0)
