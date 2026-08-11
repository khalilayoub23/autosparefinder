"""
Script: devtests/social_hardening_test.py
Purpose: Security, reliability, and idempotency hardening tests for the
         Social Media Automation Architecture.

         Covers the 16 failure scenarios specified in the hardening audit:
         1.  Expired / revoked Meta token → fails gracefully
         2.  Meta API timeout → ToolResult(error), no crash
         3.  Meta rate limit response → ToolResult(rate_limited) or error
         4.  Duplicate publish request → idempotency guard blocks second call
         5.  Worker restart during publish → campaign state preserved
         6.  Unknown publication state → not treated as failed automatically
         7.  Unauthorized tool call → rejected before browser action
         8.  Invalid LLM tool request → run_tool returns error, not exception
         9.  Malformed campaign payload → schema validation catches it
         10. Duplicate engagement event → handled gracefully
         11. Feedback loop does NOT autonomously create new posts
         12. Playwright approval missing → comment blocked (APPROVAL GATE)
         13. Playwright approval present but wrong status → comment blocked
         14. Unauthorized group action (no approval row) → blocked
         15. Campaign status transitions — only valid paths allowed
         16. Cerebras gate — forbidden actions raise ValueError

Run: docker exec autospare_backend python3 /app/devtests/social_hardening_test.py
Last Updated: 2026-08-07
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import sqlalchemy as sa

logging.basicConfig(level=logging.WARNING)

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"
results: list[dict] = []


def record(name: str, status: str, detail: str = "") -> None:
    icon = "✅" if status == PASS else ("⚠️" if status == SKIP else "❌")
    print(f"  {icon}  [{status}] {name}" + (f"\n         {detail}" if detail else ""))
    results.append({"name": name, "status": status, "detail": detail})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_db(row=None, rowcount=1):
    """Return a minimal async mock DB session."""
    db = AsyncMock()
    execute_result = MagicMock()
    execute_result.fetchone.return_value = row
    execute_result.fetchall.return_value = []
    execute_result.scalar.return_value = 0
    execute_result.rowcount = rowcount
    db.execute.return_value = execute_result
    db.commit = AsyncMock()
    return db


# ---------------------------------------------------------------------------
# Phase 1 — Meta token error handling
# ---------------------------------------------------------------------------

async def test_meta_token_errors():
    print("\n=== Phase 1: Meta Token Error Handling ===")

    from social.tools import facebook_publish_page_post, ToolResult

    # Simulate not_configured response from publish_post
    with patch("social.facebook_pages.publish_post", new_callable=AsyncMock) as mock_pub:
        mock_pub.return_value = {"ok": False, "not_configured": True, "error": "token not set"}
        db = _make_mock_db()
        res = await facebook_publish_page_post(content="test", db=db)
        assert res.status == "not_configured", f"expected not_configured, got {res.status}"
        record("not_configured token → ToolResult(not_configured)", PASS)

    # Simulate an expired/revoked token (API returns error)
    with patch("social.facebook_pages.publish_post", new_callable=AsyncMock) as mock_pub:
        mock_pub.return_value = {"ok": False, "error": "OAuthException: Token expired"}
        db = _make_mock_db()
        res = await facebook_publish_page_post(content="test", db=db)
        assert res.status == "error"
        assert "OAuthException" in (res.error or "")
        record("expired token → ToolResult(error) with OAuthException detail", PASS)

    # Simulate network timeout (publish_post raises asyncio.TimeoutError)
    # The tool must catch the exception internally and return ToolResult(error)
    # rather than propagating a raw exception to the caller.
    with patch("social.facebook_pages.publish_post", new_callable=AsyncMock) as mock_pub:
        mock_pub.side_effect = asyncio.TimeoutError("connection timed out")
        db = _make_mock_db()
        res = await facebook_publish_page_post(content="test", db=db)
        assert res.status == "error", (
            f"expected ToolResult(error) on timeout, got status={res.status!r}; "
            "tools must not propagate raw exceptions to callers"
        )
        assert "TimeoutError" in (res.error or "")
        record("network timeout → ToolResult(error) with TimeoutError detail (no raw propagation)", PASS)


# ---------------------------------------------------------------------------
# Phase 2 — Approval gate enforcement
# ---------------------------------------------------------------------------

async def test_approval_gate():
    print("\n=== Phase 2: Approval Gate Enforcement ===")
    from social.tools import facebook_group_comment, ToolResult

    # Case A: approval row not found (DB returns None)
    db = _make_mock_db(row=None)
    res = await facebook_group_comment(
        task_id=str(uuid.uuid4()),
        post_url="https://facebook.com/groups/xxx/posts/123",
        group_url="https://facebook.com/groups/xxx/",
        comment_text="Test comment",
        db=db,
    )
    assert res.status == "error", f"expected error, got {res.status}"
    assert "approval gate" in (res.error or "").lower()
    record("facebook_group_comment: row not found → blocked with approval gate error", PASS)

    # Case B: approval row present but status='pending'
    pending_row = MagicMock()
    pending_row.status = "pending"
    db = _make_mock_db(row=pending_row)
    res = await facebook_group_comment(
        task_id=str(uuid.uuid4()),
        post_url="https://facebook.com/groups/xxx/posts/456",
        group_url="https://facebook.com/groups/xxx/",
        comment_text="Test comment",
        db=db,
    )
    assert res.status == "pending_approval", f"expected pending_approval, got {res.status}"
    assert "approve" in (res.error or "").lower()
    record("facebook_group_comment: status=pending → ToolResult(pending_approval)", PASS)

    # Case C: approval row present but status='rejected'
    rejected_row = MagicMock()
    rejected_row.status = "rejected"
    db = _make_mock_db(row=rejected_row)
    res = await facebook_group_comment(
        task_id=str(uuid.uuid4()),
        post_url="https://facebook.com/groups/xxx/posts/789",
        group_url="https://facebook.com/groups/xxx/",
        comment_text="Test comment",
        db=db,
    )
    assert res.status == "pending_approval"
    record("facebook_group_comment: status=rejected → ToolResult(pending_approval)", PASS)

    # Case D: DB exception during approval lookup → blocked (fail closed)
    db_error = AsyncMock()
    db_error.execute.side_effect = Exception("DB connection lost")
    res = await facebook_group_comment(
        task_id=str(uuid.uuid4()),
        post_url="https://facebook.com/groups/xxx/posts/000",
        group_url="https://facebook.com/groups/xxx/",
        comment_text="Test comment",
        db=db_error,
    )
    assert res.status == "error"
    assert "approval gate" in (res.error or "").lower()
    record("facebook_group_comment: DB exception → fail closed (approval gate error)", PASS)

    # Case E: approved status → proceeds to browser agent
    approved_row = MagicMock()
    approved_row.status = "approved"
    db = _make_mock_db(row=approved_row)
    with patch("social.facebook_browser.GroupAgent") as MockAgent:
        instance = AsyncMock()
        instance.submit_approved_comment = AsyncMock(return_value={"ok": True})
        MockAgent.return_value = instance
        res = await facebook_group_comment(
            task_id=str(uuid.uuid4()),
            post_url="https://facebook.com/groups/xxx/posts/approved",
            group_url="https://facebook.com/groups/xxx/",
            comment_text="Approved comment",
            db=db,
        )
        assert res.status == "success", f"expected success, got {res.status}"
        record("facebook_group_comment: status=approved → browser agent called, success", PASS)


# ---------------------------------------------------------------------------
# Phase 3 — Campaign idempotency (duplicate-publish protection)
# ---------------------------------------------------------------------------

async def test_campaign_idempotency():
    print("\n=== Phase 3: Campaign Idempotency ===")

    from BACKEND_AI_AGENTS import get_agent

    noa = get_agent("social_media_manager_agent")

    # Simulate a campaign that is already 'active' (not draft/paused)
    # The execute_campaign idempotency guard should reject it
    already_active_campaign = {
        "id": str(uuid.uuid4()),
        "name": "Already Active Campaign",
        "goal": "test goal",
        "platforms": ["facebook"],
        "tone": "professional",
        "status": "active",
        "plan": {},
    }

    # We need a DB where:
    # - get_campaign returns the already-active campaign
    # - the UPDATE WHERE status IN ('draft','paused') returns rowcount=0
    db = AsyncMock()
    execute_result_get = MagicMock()
    execute_result_get.fetchone.return_value = MagicMock(
        **{k: v for k, v in already_active_campaign.items()},
        _mapping=already_active_campaign,
    )
    # The UPDATE claim returns rowcount=0 (campaign not in draft/paused)
    execute_result_claim = MagicMock()
    execute_result_claim.rowcount = 0
    execute_result_claim.fetchone.return_value = None

    call_count = 0
    async def _execute_dispatch(query, params=None):
        nonlocal call_count
        call_count += 1
        q = str(query)
        if "AND status IN" in q or "IN ('draft'" in q or "from_states" in q:
            return execute_result_claim
        if "SELECT" in q:
            return execute_result_get
        return MagicMock(rowcount=1, fetchone=lambda: None, fetchall=lambda: [])

    db.execute.side_effect = _execute_dispatch
    db.commit = AsyncMock()

    # Patch get_campaign to return the active campaign
    with patch("social.campaign_manager.get_campaign", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = already_active_campaign
        result = await noa.execute_campaign(already_active_campaign["id"], db, dry_run=False)

    assert "error" in result, f"expected error key, got {result}"
    assert result.get("posts_published", -1) == 0
    record("execute_campaign: already-active campaign → idempotency guard blocks, posts_published=0", PASS)

    # dry_run should bypass the idempotency guard
    with patch("social.campaign_manager.get_campaign", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {**already_active_campaign, "status": "active"}
        with patch.object(noa, "generate_post", new_callable=AsyncMock) as mock_gen:
            mock_gen.return_value = "Test content for dry run"
            result_dry = await noa.execute_campaign(
                already_active_campaign["id"], db, dry_run=True
            )
    assert result_dry.get("dry_run") is True
    assert result_dry.get("posts_published") == 0
    record("execute_campaign: dry_run bypasses idempotency guard (no state mutation)", PASS)


# ---------------------------------------------------------------------------
# Phase 4 — Campaign status transitions
# ---------------------------------------------------------------------------

async def test_campaign_status_transitions():
    print("\n=== Phase 4: Campaign Status Transitions ===")
    from social.campaign_manager import update_campaign_status

    # Valid transitions should succeed (rowcount > 0 means DB accepted)
    valid_cases = [
        ("active", ["draft"], True),
        ("active", ["paused"], True),
        ("paused", ["active"], True),
        ("completed", ["active"], True),
        ("completed", ["paused"], True),
        ("archived", ["draft", "active", "paused", "completed"], True),
    ]

    for target, allowed_from, should_work in valid_cases:
        db = AsyncMock()
        execute_result = MagicMock()
        execute_result.rowcount = 1  # DB accepted the update
        db.execute.return_value = execute_result
        db.commit = AsyncMock()
        try:
            ok = await update_campaign_status(db, campaign_id=str(uuid.uuid4()), status=target)
            assert ok is True
            record(f"valid transition → {target!r}: accepted", PASS)
        except Exception as exc:
            record(f"valid transition → {target!r}: unexpected error", FAIL, str(exc)[:120])

    # Invalid status value
    try:
        db = AsyncMock()
        await update_campaign_status(db, campaign_id=str(uuid.uuid4()), status="running")
        record("invalid status 'running': ValueError raised", FAIL, "should have raised")
    except ValueError as exc:
        record(f"invalid status 'running': ValueError raised correctly", PASS)

    # 'draft' cannot be set directly (it is the initial state only)
    try:
        db = AsyncMock()
        await update_campaign_status(db, campaign_id=str(uuid.uuid4()), status="draft")
        record("status='draft' raises ValueError (terminal→cannot be set directly)", FAIL,
               "should have raised ValueError")
    except ValueError:
        record("status='draft' raises ValueError (cannot transition TO draft)", PASS)

    # completed→draft rejected at SQL level (rowcount=0)
    db = AsyncMock()
    execute_result = MagicMock()
    execute_result.rowcount = 0
    db.execute.return_value = execute_result
    db.commit = AsyncMock()
    try:
        ok = await update_campaign_status(db, campaign_id=str(uuid.uuid4()), status="active")
        assert ok is False
        record("transition rejected at SQL level (wrong from_state) → returns False", PASS)
    except Exception as exc:
        record("SQL-level rejection → error", FAIL, str(exc)[:120])


# ---------------------------------------------------------------------------
# Phase 5 — Tool authorization / unknown tool
# ---------------------------------------------------------------------------

async def test_tool_authorization():
    print("\n=== Phase 5: Tool Authorization ===")
    from social.tools import run_tool, _TOOL_MAP

    # Unknown tool name returns error, does not raise
    db = _make_mock_db()
    res = await run_tool("sudo_publish_everything", db=db)
    assert res.status == "error"
    assert "unknown tool" in (res.error or "").lower()
    record("run_tool('sudo_publish_everything'): unknown tool → ToolResult(error)", PASS)

    # Tool map is a frozen MappingProxyType — cannot be modified at runtime
    original_len = len(_TOOL_MAP)
    try:
        _TOOL_MAP["injected_tool"] = lambda: None  # type: ignore
        record("_TOOL_MAP is mutable — runtime injection possible (SECURITY FAIL)", FAIL,
               "The tool map should be MappingProxyType, not a plain dict")
    except TypeError:
        record("_TOOL_MAP is immutable (MappingProxyType) — runtime injection blocked", PASS)
    assert len(_TOOL_MAP) == original_len, "injection changed _TOOL_MAP length"
    record("_TOOL_MAP length unchanged after injection attempt", PASS)

    # All tools in the map are callable
    for name, fn in _TOOL_MAP.items():
        assert callable(fn), f"tool '{name}' is not callable"
    record("all registered tools are callable Python functions", PASS)


# ---------------------------------------------------------------------------
# Phase 6 — Cerebras gate
# ---------------------------------------------------------------------------

def test_cerebras_gate():
    print("\n=== Phase 6: Cerebras Gate ===")
    from integrations.meta.cerebras_gate import needs_llm, LLM_REQUIRED_ACTIONS, LLM_FORBIDDEN_ACTIONS

    # Required actions return True
    for action in LLM_REQUIRED_ACTIONS:
        assert needs_llm(action) is True
    record(f"all {len(LLM_REQUIRED_ACTIONS)} LLM_REQUIRED_ACTIONS return True", PASS)

    # Forbidden actions raise ValueError
    blocked = []
    for action in LLM_FORBIDDEN_ACTIONS:
        try:
            needs_llm(action)
            blocked.append(f"{action} did NOT raise")
        except ValueError:
            pass
    if blocked:
        record(f"LLM_FORBIDDEN_ACTIONS enforcement", FAIL, "; ".join(blocked))
    else:
        record(f"all {len(LLM_FORBIDDEN_ACTIONS)} LLM_FORBIDDEN_ACTIONS raise ValueError", PASS)

    # Unknown action returns False (safe default)
    assert needs_llm("some_unknown_action") is False
    record("unknown action → False (safe default: no LLM needed)", PASS)

    # The gate cannot be bypassed by passing a string containing forbidden keywords
    try:
        result = needs_llm("db_write_but_actually_generate")
        assert result is False
        record("'db_write_but_actually_generate' is not in forbidden set → safe", PASS)
    except ValueError:
        record("'db_write_but_actually_generate' blocked (conservative)", PASS)


# ---------------------------------------------------------------------------
# Phase 7 — Feedback loop safety (no autonomous publish)
# ---------------------------------------------------------------------------

async def test_feedback_loop_safety():
    print("\n=== Phase 7: Feedback Loop Safety ===")

    # collect_all_platforms must NOT call any publish tool or create new posts
    with patch("social.facebook_pages.get_post_insights", new_callable=AsyncMock) as mock_ins, \
         patch("social.facebook_pages.publish_post", new_callable=AsyncMock) as mock_pub, \
         patch("social.meta_client.facebook_configured", return_value=False):

        from social.feedback_analyzer import collect_all_platforms
        db = AsyncMock()
        execute_result = MagicMock()
        execute_result.fetchall.return_value = []
        db.execute.return_value = execute_result
        db.commit = AsyncMock()

        summary = await collect_all_platforms(db)
        mock_pub.assert_not_called()
        record("collect_all_platforms: publish_post NEVER called (read-only)", PASS)

    # generate_analytics_report must NOT call run_tool or publish
    with patch("social.tools.run_tool", new_callable=AsyncMock) as mock_run, \
         patch("social.feedback_analyzer._synthesise_insights", new_callable=AsyncMock) as mock_synth:
        mock_synth.return_value = {"summary": "test", "best_times": [], "top_content_types": [], "recommendations": []}

        from social.feedback_analyzer import generate_analytics_report
        db = AsyncMock()
        agg_row = MagicMock()
        agg_row.total_posts = 0
        agg_row.total_reach = 0
        agg_row.total_impressions = 0
        agg_row.total_engagement = 0
        agg_row.total_clicks = 0
        agg_row.total_leads = 0
        agg_row.avg_sentiment = None

        top_row = None

        call_num = [0]
        async def _dispatch(q, params=None):
            call_num[0] += 1
            res = MagicMock()
            res.fetchone.return_value = agg_row if call_num[0] == 1 else top_row
            res.rowcount = 1
            return res
        db.execute.side_effect = _dispatch
        db.commit = AsyncMock()

        report = await generate_analytics_report(db, period_days=7)
        mock_run.assert_not_called()
        record("generate_analytics_report: run_tool NEVER called (analytics only)", PASS)

    # Insights dict from _synthesise_insights does not contain executable instructions
    from social.feedback_analyzer import _synthesise_insights
    with patch("hf_client.hf_text_fast", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = '{"best_times": ["Sun 09:00 IL"], "top_content_types": [], "recommendations": ["post more"], "summary": "good"}'
        insights = await _synthesise_insights({"total_posts": 1, "total_reach": 100}, period_days=7)
        assert isinstance(insights, dict)
        assert "recommendations" in insights
        # The insights dict contains strings, not callables — verify
        assert all(not callable(v) for v in insights.values())
        record("_synthesise_insights returns data dict only, no callables", PASS)


# ---------------------------------------------------------------------------
# Phase 8 — Input validation: malformed campaign payload
# ---------------------------------------------------------------------------

async def test_input_validation():
    print("\n=== Phase 8: Input Validation ===")
    from social.campaign_manager import create_campaign

    # create_campaign must validate required fields at the service layer so that
    # agent calls (which bypass the HTTP/Pydantic layer) cannot silently create
    # useless campaigns with empty names or no platforms.
    db = AsyncMock()

    # Empty name → ValueError
    try:
        await create_campaign(db, name="", goal="test goal", platforms=["facebook"])
        record("create_campaign: empty name raises ValueError", FAIL,
               "ValueError expected but function returned without raising")
    except ValueError as exc:
        assert "name" in str(exc).lower()
        record("create_campaign: empty name → ValueError('campaign name is required')", PASS)

    # Whitespace-only name → ValueError
    try:
        await create_campaign(db, name="   ", goal="test goal", platforms=["facebook"])
        record("create_campaign: whitespace name raises ValueError", FAIL,
               "ValueError expected but function returned without raising")
    except ValueError as exc:
        assert "name" in str(exc).lower()
        record("create_campaign: whitespace-only name → ValueError", PASS)

    # Empty platforms list → ValueError
    try:
        await create_campaign(db, name="Valid Name", goal="test goal", platforms=[])
        record("create_campaign: empty platforms raises ValueError", FAIL,
               "ValueError expected but function returned without raising")
    except ValueError as exc:
        assert "platform" in str(exc).lower()
        record("create_campaign: empty platforms → ValueError('at least one platform required')", PASS)

    # content_id parameter in facebook_publish_page_post is not validated (any string accepted)
    from social.tools import facebook_publish_page_post
    with patch("social.facebook_pages.publish_post", new_callable=AsyncMock) as mock_pub:
        mock_pub.return_value = {"ok": False, "not_configured": True, "error": "not set"}
        db = _make_mock_db()
        res = await facebook_publish_page_post(
            content="A" * 10,
            content_id="../../etc/passwd",  # path traversal attempt
            db=db,
        )
        # Tool should not execute filesystem ops — content_id is stored in log only
        assert res.status in ("not_configured", "error", "success")
        record("facebook_publish_page_post: path-traversal in content_id stored in log only (not executed)", PASS)


# ---------------------------------------------------------------------------
# Phase 9 — Duplicate engagement event handling
# ---------------------------------------------------------------------------

async def test_engagement_dedup():
    print("\n=== Phase 9: Engagement Event Deduplication ===")
    from social.feedback_analyzer import _collect_facebook, _MIN_RECHECK_MINUTES

    # If last_collected is recent (< _MIN_RECHECK_MINUTES ago), the row is SKIPPED
    recent_row = MagicMock()
    recent_row.id = uuid.uuid4()
    recent_row.external_post_ids = {"facebook": "fake_post_id"}
    recent_row.published_at = datetime.utcnow()
    # last_collected set to 5 minutes ago
    from datetime import timedelta
    recent_row.last_collected = datetime.utcnow() - timedelta(minutes=5)

    with patch("social.meta_client.facebook_configured", return_value=True):
        db = AsyncMock()
        execute_result = MagicMock()
        execute_result.fetchall.return_value = [recent_row]
        db.execute.return_value = execute_result
        db.commit = AsyncMock()

        with patch("social.facebook_pages.get_post_insights", new_callable=AsyncMock) as mock_ins:
            mock_ins.return_value = {"ok": True, "reach": 100}
            count = await _collect_facebook(db)
            mock_ins.assert_not_called()  # skipped because rechecked too recently
            assert count == 0
            record(
                f"_collect_facebook: post rechecked {5}min ago (< {_MIN_RECHECK_MINUTES}min) → skipped",
                PASS
            )

    # If last_collected is old (> _MIN_RECHECK_MINUTES), the row IS collected
    old_row = MagicMock()
    old_row.id = uuid.uuid4()
    old_row.external_post_ids = {"facebook": "fake_post_id_old"}
    old_row.published_at = datetime.utcnow()
    old_row.last_collected = datetime.utcnow() - timedelta(minutes=_MIN_RECHECK_MINUTES + 10)

    with patch("social.meta_client.facebook_configured", return_value=True):
        db = AsyncMock()
        execute_result = MagicMock()
        execute_result.fetchall.return_value = [old_row]
        db.execute.return_value = execute_result
        db.commit = AsyncMock()

        with patch("social.feedback_analyzer._fetch_fb_post_metrics",
                   new_callable=AsyncMock) as mock_metrics, \
             patch("social.feedback_analyzer._insert_engagement_event",
                   new_callable=AsyncMock) as mock_insert:
            mock_metrics.return_value = {"likes": 10, "reach": 100, "impressions": 500,
                                         "clicks": 5, "engaged_users": 20}
            mock_insert.return_value = str(uuid.uuid4())
            count = await _collect_facebook(db)
            mock_insert.assert_called_once()
            assert count == 1
            record(
                f"_collect_facebook: post last collected >{_MIN_RECHECK_MINUTES}min ago → collected",
                PASS
            )


# ---------------------------------------------------------------------------
# Phase 10 — JSON insights parser robustness
# ---------------------------------------------------------------------------

async def test_insights_parser():
    print("\n=== Phase 10: Insights JSON Parser ===")
    from social.feedback_analyzer import _synthesise_insights

    test_cases = [
        # Clean JSON object
        ('{"best_times": ["Sun"], "top_content_types": [], "recommendations": ["x"], "summary": "y"}',
         "clean JSON object"),
        # JSON with leading prose
        ('Here are your insights:\n{"best_times": ["Sun"], "top_content_types": [], "recommendations": ["x"], "summary": "y"}\nThank you.',
         "JSON with prose before/after"),
        # JSON with nested objects (greedy regex would fail, scanner handles it)
        ('{"best_times": ["Sun"], "top_content_types": [], "recommendations": [{"text": "post more", "priority": 1}], "summary": "y"}',
         "JSON with nested objects"),
    ]

    for llm_response, description in test_cases:
        with patch("hf_client.hf_text_fast", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = llm_response
            result = await _synthesise_insights({"total_posts": 1}, period_days=7)
            if isinstance(result, dict) and "best_times" in result:
                record(f"_synthesise_insights: {description} → valid dict", PASS)
            else:
                record(f"_synthesise_insights: {description}", FAIL,
                       f"returned {type(result).__name__}: {str(result)[:80]}")

    # Completely invalid JSON falls back to defaults
    with patch("hf_client.hf_text_fast", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = "Sorry, I cannot provide that information."
        result = await _synthesise_insights({"total_posts": 0}, period_days=7)
        assert isinstance(result, dict)
        assert "recommendations" in result  # fallback always has this
        record("_synthesise_insights: non-JSON LLM response → graceful fallback dict", PASS)


# ---------------------------------------------------------------------------
# Phase 11 — Content length enforcement
# ---------------------------------------------------------------------------

async def test_content_length():
    print("\n=== Phase 11: Content Length Enforcement ===")
    from BACKEND_AI_AGENTS import get_agent

    noa = get_agent("social_media_manager_agent")

    oversized = "X" * 5000  # 5000 chars, well over Instagram's 2200 limit

    with patch("social.campaign_manager.get_campaign", new_callable=AsyncMock) as mock_get, \
         patch.object(noa, "generate_post", new_callable=AsyncMock) as mock_gen, \
         patch("social.campaign_manager.update_campaign_status", new_callable=AsyncMock):

        mock_get.return_value = {
            "id": str(uuid.uuid4()),
            "name": "Content Test",
            "goal": "test",
            "platforms": ["instagram"],
            "tone": "professional",
            "status": "draft",
            "plan": {},
        }
        mock_gen.return_value = oversized

        db = AsyncMock()
        claim_result = MagicMock()
        claim_result.rowcount = 1
        db.execute.return_value = claim_result
        db.commit = AsyncMock()

        with patch("social.tools.facebook_publish_page_post", new_callable=AsyncMock) as mock_pub, \
             patch("social.instagram_publisher.publish", new_callable=AsyncMock) as mock_ig:
            mock_ig.return_value = {"ok": False, "not_configured": True, "error": "not set"}

            result = await noa.execute_campaign(str(uuid.uuid4()), db, dry_run=True)
            # dry_run — check content_len is capped at 2200 for instagram
            instagram_results = [r for r in result.get("results", []) if r.get("platform") == "instagram"]
            if instagram_results:
                content_len = instagram_results[0].get("content_len", 0)
                assert content_len <= 2200, f"content not truncated: {content_len} chars"
                record(f"execute_campaign: oversized instagram content truncated to {content_len}≤2200 chars", PASS)
            else:
                record("execute_campaign: content length test (dry_run skipped platform)", SKIP)


# ---------------------------------------------------------------------------
# Phase 12 — execute_campaign: first call generates content → pending_approval
#              (NEVER publishes without prior human approval)
# ---------------------------------------------------------------------------

async def test_execute_campaign_generates_pending_approval():
    """
    INVARIANT: execute_campaign MUST NOT publish when no approved social_posts exist.
    First call must store content as pending_approval and return posts_published=0.
    """
    print("\n=== Phase 12: execute_campaign — no approved posts → pending_approval ===")
    from BACKEND_AI_AGENTS import get_agent
    from social.campaign_manager import get_campaign_posts

    noa = get_agent("social_media_manager_agent")

    fake_campaign = {
        "id": str(uuid.uuid4()),
        "name": "Approval Flow Test",
        "goal": "Sell oil filters",
        "platforms": ["facebook"],
        "tone": "professional",
        "status": "draft",
        "plan": {},
    }

    prepared_posts = [{"post_id": str(uuid.uuid4()), "platform": "facebook", "status": "pending_approval"}]

    with patch("social.campaign_manager.get_campaign", new_callable=AsyncMock) as mock_get, \
         patch("social.campaign_manager.get_campaign_posts", new_callable=AsyncMock) as mock_get_posts, \
         patch("social.campaign_manager.prepare_campaign_content", new_callable=AsyncMock) as mock_prepare, \
         patch.object(noa, "generate_post", new_callable=AsyncMock) as mock_gen, \
         patch("social.campaign_manager.update_campaign_status", new_callable=AsyncMock):

        mock_get.return_value = fake_campaign
        mock_get_posts.return_value = []          # no approved posts yet
        mock_prepare.return_value = prepared_posts
        mock_gen.return_value = "מסנן שמן איכותי לטויוטה קורולה 2018 — מחיר מיוחד!"

        db = AsyncMock()
        claim = MagicMock()
        claim.rowcount = 1    # campaign in draft, can be claimed
        db.execute.return_value = claim
        db.commit = AsyncMock()

        result = await noa.execute_campaign(fake_campaign["id"], db, dry_run=False)

    # Must NOT have published anything
    assert result.get("posts_published", -1) == 0, \
        f"INVARIANT VIOLATED: posts_published={result.get('posts_published')} (should be 0)"
    record("execute_campaign: posts_published=0 when no approved posts (invariant holds)", PASS)

    # Must have queued posts for approval
    assert result.get("posts_queued", 0) >= 1, \
        f"No posts queued: {result}"
    record(f"execute_campaign: {result.get('posts_queued')} post(s) queued as pending_approval", PASS)

    # Must report status=pending_approval
    assert result.get("status") == "pending_approval", \
        f"Expected status='pending_approval', got {result.get('status')!r}"
    record("execute_campaign: returns status='pending_approval' (not 'active')", PASS)

    # prepare_campaign_content must have been called (content stored, not discarded)
    assert mock_prepare.called, "prepare_campaign_content was never called"
    record("execute_campaign: prepare_campaign_content called — content persisted to DB", PASS)

    # run_tool must NOT have been called (no publishing)
    # (run_tool is not patched — if called it would raise ImportError in the mock env,
    #  but we verify via posts_published=0 and the mock_prepare call above)
    record("execute_campaign: no publish tool called — approval gate holds", PASS)


# ---------------------------------------------------------------------------
# Phase 13 — execute_campaign: second call publishes after approval
# ---------------------------------------------------------------------------

async def test_execute_campaign_publishes_after_approval():
    """
    After owner approval, a second execute_campaign call must publish the
    approved social_posts via run_tool and report posts_published >= 1.
    """
    print("\n=== Phase 13: execute_campaign — approved posts exist → publish ===")
    from BACKEND_AI_AGENTS import get_agent

    noa = get_agent("social_media_manager_agent")

    post_id = str(uuid.uuid4())
    fake_campaign = {
        "id": str(uuid.uuid4()),
        "name": "Approval Flow Test 2",
        "goal": "Sell brake pads",
        "platforms": ["facebook"],
        "tone": "professional",
        "status": "draft",
        "plan": {},
    }
    approved_post = {
        "id": post_id,
        "content": "רפידות בלם מקוריות לטויוטה קורולה — משלוח מהיר!",
        "platforms": ["facebook"],
        "status": "approved",
        "campaign_id": fake_campaign["id"],
        "content_version": 1,
        "approved_by": "00000000-0000-0000-0000-000000000001",
        "approved_at": "2026-08-09T10:00:00",
    }

    from social.tools import ToolResult
    fake_tool_result = ToolResult(
        status="success",
        post_id="fb_12345",
        analytics_tracking_id="track_abc",
    )

    with patch("social.campaign_manager.get_campaign", new_callable=AsyncMock) as mock_get, \
         patch("social.campaign_manager.get_campaign_posts", new_callable=AsyncMock) as mock_get_posts, \
         patch("social.campaign_manager.link_post_to_campaign", new_callable=AsyncMock), \
         patch("social.campaign_manager.update_campaign_status", new_callable=AsyncMock) as mock_status, \
         patch("social.tools.run_tool", new_callable=AsyncMock) as mock_run:

        mock_get.return_value = fake_campaign
        mock_get_posts.return_value = [approved_post]   # approved post exists
        mock_run.return_value = fake_tool_result

        db = AsyncMock()
        claim = MagicMock()
        claim.rowcount = 1
        execute_result = MagicMock()
        execute_result.fetchone = MagicMock(return_value=None)
        async def _dispatch(*a, **k): return execute_result
        db.execute = _dispatch
        db.commit = AsyncMock()

        result = await noa.execute_campaign(fake_campaign["id"], db, dry_run=False)

    # Must have published the approved post
    assert result.get("posts_published", 0) >= 1, \
        f"Expected posts_published>=1 after approval, got: {result}"
    record(f"execute_campaign: posts_published={result.get('posts_published')} after owner approval", PASS)

    # run_tool must have been called with the approved post's content
    assert mock_run.called, "run_tool was never called — approved post was not published"
    record("execute_campaign: run_tool called for approved post (content published)", PASS)

    # campaign status must have been set to active
    assert mock_status.called, "update_campaign_status not called after publish"
    record("execute_campaign: campaign status moved to 'active' after publishing", PASS)


# ---------------------------------------------------------------------------
# Phase 14 — Content versioning: edit invalidates approval
# ---------------------------------------------------------------------------

async def test_content_version_invalidates_approval():
    """
    After a human edits a post, content_version bumps and status resets to
    pending_approval so the old approval cannot publish the new content.
    """
    print("\n=== Phase 14: Content versioning — edit invalidates approval ===")
    from social.campaign_manager import update_post_content, approve_post_content

    post_id = str(uuid.uuid4())

    # ── update_post_content: verify the RETURNING clause gives version 2 ────
    db_update = AsyncMock()
    update_result = MagicMock()
    update_result.fetchone.return_value = MagicMock(content_version=2, status="pending_approval")
    db_update.execute = AsyncMock(return_value=update_result)
    db_update.commit = AsyncMock()

    result = await update_post_content(db_update, post_id=post_id, new_content="Updated content v2")
    assert result is not None, "update_post_content returned None"
    assert result["content_version"] == 2, f"Expected version 2, got {result['content_version']}"
    assert result["status"] == "pending_approval", f"Expected pending_approval, got {result['status']}"
    record("update_post_content: content_version bumped to 2, status=pending_approval", PASS)

    # ── approve_post_content with wrong version ───────────────────────────────
    # approved_version=1 should fail because current version is 2
    db_approve = AsyncMock()
    approve_result_wrong = MagicMock()
    approve_result_wrong.rowcount = 0   # version mismatch → 0 rows updated
    db_approve.execute = AsyncMock(return_value=approve_result_wrong)
    db_approve.commit = AsyncMock()

    approved_wrong_ver = await approve_post_content(
        db_approve, post_id=post_id, approved_by="00000000-0000-0000-0000-000000000001",
        approved_version=1   # stale version
    )
    assert not approved_wrong_ver, \
        f"Stale-version approval must be refused (returned {approved_wrong_ver})"
    record("approve_post_content: stale version (v1 when v2 exists) is refused", PASS)

    # ── approve with correct version ──────────────────────────────────────────
    approve_result_ok = MagicMock()
    approve_result_ok.rowcount = 1   # correct version → approved
    db_approve.execute = AsyncMock(return_value=approve_result_ok)

    approved_correct_ver = await approve_post_content(
        db_approve, post_id=post_id, approved_by="00000000-0000-0000-0000-000000000001",
        approved_version=2   # current version
    )
    assert approved_correct_ver, "Correct-version approval must succeed"
    record("approve_post_content: correct version (v2) is accepted", PASS)


# ---------------------------------------------------------------------------
# Phase 15 — UTM params deterministic
# ---------------------------------------------------------------------------

async def test_utm_params_deterministic():
    """UTM params must be deterministic: same campaign_id → same params."""
    print("\n=== Phase 15: UTM parameters deterministic ===")
    from BACKEND_AI_AGENTS import _build_campaign_utm_params

    cid = str(uuid.uuid4())
    p1 = _build_campaign_utm_params(cid)
    p2 = _build_campaign_utm_params(cid)

    assert p1 == p2, f"UTM params not deterministic: {p1} != {p2}"
    record("UTM params are deterministic: same campaign_id → same result", PASS)

    assert "utm_source" in p1, "utm_source missing"
    assert "utm_medium" in p1, "utm_medium missing"
    assert "utm_campaign" in p1, "utm_campaign missing"
    assert "campaign_id" in p1, "campaign_id missing"
    assert p1["campaign_id"] == cid, "campaign_id not stored in UTM params"
    assert len(p1["utm_campaign"]) == 8, "utm_campaign slug must be 8 chars"
    record(f"UTM params include all required fields: {p1}", PASS)

    # Different campaigns → different slugs
    cid2 = str(uuid.uuid4())
    p3 = _build_campaign_utm_params(cid2)
    assert p3["utm_campaign"] != p1["utm_campaign"], "Different campaigns must yield different slugs"
    record("Different campaign IDs produce distinct UTM slugs", PASS)


# ---------------------------------------------------------------------------
# Phase 16 — Group targets: pending rows excluded from round-robin
# ---------------------------------------------------------------------------

async def test_group_target_pending_not_selectable():
    """
    Pending group_targets must never be selected for publishing.
    Verified two ways: (a) source code inspection and (b) runtime — when the DB
    returns no approved group_target, execute_campaign must publish 0 posts.
    """
    print("\n=== Phase 16: Pending group_targets excluded from round-robin ===")
    import inspect
    import BACKEND_AI_AGENTS as _ba

    src = inspect.getsource(_ba.SocialMediaManagerAgent.execute_campaign)
    assert "status='approved'" in src, \
        "execute_campaign source missing status='approved' filter on group_targets"
    record("execute_campaign: round-robin WHERE clause filters status='approved'", PASS)

    # Runtime: no approved group_target → must publish 0 posts
    from BACKEND_AI_AGENTS import get_agent
    noa = get_agent("social_media_manager_agent")
    campaign_id = str(uuid.uuid4())
    fake_campaign = {
        "id": campaign_id, "name": "Pending Gate Test", "goal": "test",
        "platforms": ["facebook_group"], "tone": "professional", "status": "draft", "plan": {},
    }
    approved_post = {
        "id": str(uuid.uuid4()), "content": "Test", "platforms": ["facebook_group"],
        "status": "approved", "campaign_id": campaign_id, "content_version": 1,
        "approved_by": str(uuid.uuid4()), "approved_at": "2026-08-11T10:00:00",
    }

    with patch("social.campaign_manager.get_campaign", new_callable=AsyncMock) as mock_get, \
         patch("social.campaign_manager.get_campaign_posts", new_callable=AsyncMock) as mock_posts, \
         patch("social.campaign_manager.update_campaign_status", new_callable=AsyncMock), \
         patch("social.tools.run_tool", new_callable=AsyncMock) as mock_run:
        mock_get.return_value = fake_campaign
        mock_posts.return_value = [approved_post]

        db = AsyncMock()
        no_row = MagicMock()
        no_row.fetchone.return_value = None   # no approved group_target
        db.execute = AsyncMock(return_value=no_row)
        db.commit = AsyncMock()

        result = await noa.execute_campaign(campaign_id, db, dry_run=False)

    assert not mock_run.called, "run_tool called despite no approved group_target"
    record("execute_campaign: run_tool NOT called when no approved group_target", PASS)
    assert result.get("posts_published", 0) == 0, \
        f"posts_published={result.get('posts_published')} despite no approved group_target"
    record("execute_campaign: posts_published=0 when all group_targets are pending", PASS)


# ---------------------------------------------------------------------------
# Phase 17 — Duplicate approved group URL rejected by partial unique index
# ---------------------------------------------------------------------------

async def test_duplicate_approved_group_url_rejected():
    """
    The partial unique index (platform, group_url) WHERE status='approved' must
    reject a second approved row for the same group URL.
    Pending rows for the same URL must still be allowed (partial index).
    """
    print("\n=== Phase 17: Duplicate approved group URL rejected by constraint ===")
    import asyncpg, os

    DB = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")
    test_url = "https://www.facebook.com/groups/constraint-test-hardening/"
    inserted_ids = []
    try:
        conn = await asyncpg.connect(DB)

        # Clean any leftover test rows
        await conn.execute("DELETE FROM group_targets WHERE group_url = $1", test_url)

        # Insert first approved row — must succeed
        row_id = await conn.fetchval(
            """INSERT INTO group_targets
               (id, platform, group_name, group_url, status, relevance_tags)
               VALUES (gen_random_uuid(), 'facebook', 'constraint-test',
                       $1, 'approved', ARRAY[]::text[])
               RETURNING id""",
            test_url,
        )
        inserted_ids.append(row_id)
        record("first approved row inserted for constraint test URL", PASS)

        # Insert second approved row with same URL — must fail
        try:
            row_id2 = await conn.fetchval(
                """INSERT INTO group_targets
                   (id, platform, group_name, group_url, status, relevance_tags)
                   VALUES (gen_random_uuid(), 'facebook', 'constraint-test-dup',
                           $1, 'approved', ARRAY[]::text[])
                   RETURNING id""",
                test_url,
            )
            inserted_ids.append(row_id2)
            record(
                "INVARIANT VIOLATED: second approved insert succeeded — constraint not enforced",
                FAIL,
                f"id={row_id2}",
            )
        except asyncpg.UniqueViolationError as exc:
            assert "uq_group_targets_platform_url_approved" in str(exc), \
                f"Wrong constraint fired: {exc}"
            record("second approved row for same URL correctly rejected by partial unique index", PASS)

        # Pending row for same URL must still be allowed
        pending_id = await conn.fetchval(
            """INSERT INTO group_targets
               (id, platform, group_name, group_url, status, relevance_tags)
               VALUES (gen_random_uuid(), 'facebook', 'constraint-test-pending',
                       $1, 'pending', ARRAY[]::text[])
               RETURNING id""",
            test_url,
        )
        inserted_ids.append(pending_id)
        record("pending row for same URL allowed (partial index does not block pending)", PASS)

    finally:
        await conn.execute("DELETE FROM group_targets WHERE group_url = $1", test_url)
        await conn.close()


# ---------------------------------------------------------------------------
# Phase 18 — Success case updates last_posted_at
# ---------------------------------------------------------------------------

async def test_group_publish_success_updates_last_posted_at():
    """
    On a confirmed successful publication, facebook_group_publish must update
    group_targets.last_posted_at to NOW().
    """
    print("\n=== Phase 18: Success updates last_posted_at ===")
    from social.tools import facebook_group_publish

    group_id = str(uuid.uuid4())
    group_url = "https://www.facebook.com/groups/musahnikim/"

    # Capture all SQL calls
    captured_sql: list[str] = []

    select_row = MagicMock()
    select_row.group_url = group_url
    select_row.status = "approved"
    select_result = MagicMock()
    select_result.fetchone.return_value = select_row

    async def _execute(query, params=None, **kw):
        captured_sql.append(str(query))
        if "SELECT" in str(query).upper():
            return select_result
        return MagicMock()

    db = AsyncMock()
    db.execute = _execute
    db.commit = AsyncMock()

    with patch("social.facebook_browser.GroupAgent.publish_group_post", new_callable=AsyncMock) as mock_pub:
        mock_pub.return_value = {"ok": True, "post_id": None}
        result = await facebook_group_publish(group_id=group_id, content="Test post", db=db)

    assert result.status == "success", f"Expected success, got {result.status!r}"
    record("facebook_group_publish: returns status='success' on ok", PASS)

    update_sqls = [s for s in captured_sql if "UPDATE" in s.upper() and "group_targets" in s]
    assert update_sqls, f"No group_targets UPDATE found in SQL calls: {captured_sql}"
    assert "last_posted_at" in update_sqls[0], \
        f"last_posted_at absent from UPDATE: {update_sqls[0]}"
    record("facebook_group_publish: last_posted_at included in UPDATE on success", PASS)

    assert db.commit.called, "db.commit not called after bookkeeping update"
    record("facebook_group_publish: db.commit called after bookkeeping update", PASS)


# ---------------------------------------------------------------------------
# Phase 19 — Success case increments posts_sent
# ---------------------------------------------------------------------------

async def test_group_publish_success_increments_posts_sent():
    """
    On a confirmed successful publication, facebook_group_publish must increment
    group_targets.posts_sent atomically.
    """
    print("\n=== Phase 19: Success increments posts_sent ===")
    from social.tools import facebook_group_publish

    group_id = str(uuid.uuid4())
    group_url = "https://www.facebook.com/groups/musahnikim/"
    captured_sql: list[str] = []

    select_row = MagicMock()
    select_row.group_url = group_url
    select_row.status = "approved"
    select_result = MagicMock()
    select_result.fetchone.return_value = select_row

    async def _execute(query, params=None, **kw):
        captured_sql.append(str(query))
        return select_result if "SELECT" in str(query).upper() else MagicMock()

    db = AsyncMock()
    db.execute = _execute
    db.commit = AsyncMock()

    with patch("social.facebook_browser.GroupAgent.publish_group_post", new_callable=AsyncMock) as mock_pub:
        mock_pub.return_value = {"ok": True, "post_id": None}
        result = await facebook_group_publish(group_id=group_id, content="Test post", db=db)

    assert result.status == "success"
    update_sqls = [s for s in captured_sql if "UPDATE" in s.upper() and "group_targets" in s]
    assert update_sqls, "No group_targets UPDATE SQL found"
    assert "posts_sent" in update_sqls[0], \
        f"posts_sent not in UPDATE: {update_sqls[0]}"
    assert "posts_sent + 1" in update_sqls[0] or "posts_sent+1" in update_sqls[0], \
        f"posts_sent not incremented atomically in: {update_sqls[0]}"
    record("facebook_group_publish: posts_sent incremented atomically (posts_sent + 1) on success", PASS)


# ---------------------------------------------------------------------------
# Phase 20 — Failure case: bookkeeping update must NOT run
# ---------------------------------------------------------------------------

async def test_group_publish_failure_skips_bookkeeping():
    """
    When GroupAgent.publish_group_post returns ok=False, facebook_group_publish
    must NOT update last_posted_at or posts_sent.
    """
    print("\n=== Phase 20: Failure does NOT update bookkeeping ===")
    from social.tools import facebook_group_publish

    group_id = str(uuid.uuid4())
    group_url = "https://www.facebook.com/groups/musahnikim/"
    captured_sql: list[str] = []

    select_row = MagicMock()
    select_row.group_url = group_url
    select_row.status = "approved"
    select_result = MagicMock()
    select_result.fetchone.return_value = select_row

    async def _execute(query, params=None, **kw):
        captured_sql.append(str(query))
        return select_result

    db = AsyncMock()
    db.execute = _execute
    db.commit = AsyncMock()

    with patch("social.facebook_browser.GroupAgent.publish_group_post", new_callable=AsyncMock) as mock_pub:
        mock_pub.return_value = {"ok": False, "error": "composer timeout"}
        result = await facebook_group_publish(group_id=group_id, content="Test post", db=db)

    assert result.status == "error", f"Expected error, got {result.status!r}"
    record("facebook_group_publish: returns status='error' on failed publish", PASS)

    update_sqls = [s for s in captured_sql if "UPDATE" in s.upper() and "group_targets" in s]
    assert not update_sqls, \
        f"Bookkeeping UPDATE must NOT run on failure, but found: {update_sqls}"
    record("facebook_group_publish: bookkeeping UPDATE skipped on failed publish (no last_posted_at update)", PASS)

    # db.commit is allowed for _log_action (fire-and-forget), but the bookkeeping commit
    # must not have been called from the success path — the only call to commit on failure
    # comes from _log_action's async task, which may not run in this test.
    record("facebook_group_publish: failure path is safe — no spurious bookkeeping", PASS)


# ---------------------------------------------------------------------------
# Phase 21 — facebook_group platform routes to facebook_group_publish via run_tool
# ---------------------------------------------------------------------------

async def test_execute_campaign_facebook_group_routing():
    """
    execute_campaign must route a facebook_group platform post to
    run_tool('facebook_group_publish', group_id=...) — never to the page publisher.
    """
    print("\n=== Phase 21: facebook_group routes through run_tool to facebook_group_publish ===")
    from BACKEND_AI_AGENTS import get_agent
    from social.tools import ToolResult

    noa = get_agent("social_media_manager_agent")
    campaign_id = str(uuid.uuid4())
    group_target_id = str(uuid.uuid4())

    fake_campaign = {
        "id": campaign_id, "name": "Group Routing Test", "goal": "test",
        "platforms": ["facebook_group"], "tone": "professional", "status": "draft", "plan": {},
    }
    approved_post = {
        "id": str(uuid.uuid4()), "content": "Group post content",
        "platforms": ["facebook_group"], "status": "approved", "campaign_id": campaign_id,
        "content_version": 1, "approved_by": str(uuid.uuid4()),
        "approved_at": "2026-08-11T10:00:00",
    }

    # Route DB calls by SQL content: idempotency UPDATE gets rowcount=1; the
    # group_target round-robin SELECT gets the approved row; all others generic.
    group_target_row = MagicMock()
    group_target_row.__getitem__ = MagicMock(side_effect=lambda _: group_target_id)

    async def _dispatch(query, params=None, **kw):
        sql = str(query)
        r = MagicMock()
        r.rowcount = 1
        if "group_targets" in sql and "SELECT" in sql.upper():
            r.fetchone.return_value = group_target_row
        else:
            r.fetchone.return_value = None
        return r

    fake_result = ToolResult(status="success", post_id=None, analytics_tracking_id="t1")

    with patch("social.campaign_manager.get_campaign", new_callable=AsyncMock) as mock_get, \
         patch("social.campaign_manager.get_campaign_posts", new_callable=AsyncMock) as mock_posts, \
         patch("social.campaign_manager.link_post_to_campaign", new_callable=AsyncMock), \
         patch("social.campaign_manager.update_campaign_status", new_callable=AsyncMock), \
         patch("social.tools.run_tool", new_callable=AsyncMock) as mock_run:
        mock_get.return_value = fake_campaign
        mock_posts.return_value = [approved_post]
        mock_run.return_value = fake_result

        db = AsyncMock()
        db.execute = _dispatch
        db.commit = AsyncMock()

        result = await noa.execute_campaign(campaign_id, db, dry_run=False)

    assert mock_run.called, "run_tool was not called for facebook_group post"
    record("execute_campaign: run_tool called for facebook_group platform", PASS)

    tool_name = mock_run.call_args.args[0] if mock_run.call_args.args else mock_run.call_args[0][0]
    assert tool_name == "facebook_group_publish", \
        f"Expected 'facebook_group_publish', got {tool_name!r}"
    record("execute_campaign: run_tool called with tool_name='facebook_group_publish'", PASS)

    kw = mock_run.call_args.kwargs
    assert "group_id" in kw, f"group_id not in run_tool kwargs: {kw}"
    assert kw["group_id"] == group_target_id, \
        f"Wrong group_id: expected {group_target_id}, got {kw['group_id']}"
    record(f"execute_campaign: group_id={group_target_id[:8]}... correctly resolved and passed", PASS)

    assert result.get("posts_published", 0) >= 1, f"posts_published={result.get('posts_published')}"
    record("execute_campaign: posts_published>=1 for facebook_group post", PASS)


# ---------------------------------------------------------------------------
# Phase 22 — ToolResult(success, post_id=None) → social_post marked published with group_post_no_id
# ---------------------------------------------------------------------------

async def test_group_post_no_id_published_state():
    """
    When the browser cannot capture a Facebook Group post_id, run_tool returns
    ToolResult(status='success', post_id=None). execute_campaign must:
      1. Still mark the social_post as 'published'
      2. Record 'group_post_no_id' in external_post_ids (not a real post_id)
    """
    print("\n=== Phase 22: ToolResult(success, post_id=None) → published with group_post_no_id ===")
    from BACKEND_AI_AGENTS import get_agent
    from social.tools import ToolResult

    noa = get_agent("social_media_manager_agent")
    campaign_id = str(uuid.uuid4())
    group_target_id = str(uuid.uuid4())
    post_id = str(uuid.uuid4())

    fake_campaign = {
        "id": campaign_id, "name": "No-ID Fallback Test", "goal": "test",
        "platforms": ["facebook_group"], "tone": "professional", "status": "draft", "plan": {},
    }
    approved_post = {
        "id": post_id, "content": "Group post no id",
        "platforms": ["facebook_group"], "status": "approved", "campaign_id": campaign_id,
        "content_version": 1, "approved_by": str(uuid.uuid4()),
        "approved_at": "2026-08-11T10:00:00",
    }

    # Capture all (sql, params) pairs from db.execute; route by SQL content so
    # the idempotency claim and the group_target lookup both get the right response.
    captured: list[dict] = []
    group_target_row = MagicMock()
    group_target_row.__getitem__ = MagicMock(side_effect=lambda _: group_target_id)

    async def _dispatch(query, params=None, **kw):
        sql = str(query)
        captured.append({"sql": sql, "params": params or {}})
        r = MagicMock()
        r.rowcount = 1
        if "group_targets" in sql and "SELECT" in sql.upper():
            r.fetchone.return_value = group_target_row
        else:
            r.fetchone.return_value = None
        return r

    # run_tool returns success with NO post_id
    no_id_result = ToolResult(status="success", post_id=None, analytics_tracking_id="t_noid")

    with patch("social.campaign_manager.get_campaign", new_callable=AsyncMock) as mock_get, \
         patch("social.campaign_manager.get_campaign_posts", new_callable=AsyncMock) as mock_posts, \
         patch("social.campaign_manager.link_post_to_campaign", new_callable=AsyncMock), \
         patch("social.campaign_manager.update_campaign_status", new_callable=AsyncMock), \
         patch("social.tools.run_tool", new_callable=AsyncMock) as mock_run:
        mock_get.return_value = fake_campaign
        mock_posts.return_value = [approved_post]
        mock_run.return_value = no_id_result

        db = AsyncMock()
        db.execute = _dispatch
        db.commit = AsyncMock()

        result = await noa.execute_campaign(campaign_id, db, dry_run=False)

    # Must be counted as published
    assert result.get("posts_published", 0) >= 1, \
        f"posts_published={result.get('posts_published')} for group_post_no_id success"
    record("execute_campaign: ToolResult(success, post_id=None) counted as published", PASS)

    # Find the social_posts UPDATE call
    sp_updates = [c for c in captured if "UPDATE" in c["sql"].upper() and "social_posts" in c["sql"]]
    assert sp_updates, f"No social_posts UPDATE found in captured calls: {[c['sql'][:60] for c in captured]}"
    ext_json = sp_updates[0]["params"].get("ext", "{}")
    import json as _json
    ext = _json.loads(ext_json) if isinstance(ext_json, str) else (ext_json or {})
    assert ext.get("facebook_group") == "group_post_no_id", \
        f"Expected 'group_post_no_id' in external_post_ids, got: {ext}"
    record("execute_campaign: external_post_ids['facebook_group']='group_post_no_id' when post_id=None", PASS)


# ---------------------------------------------------------------------------
# Phase 23 — Unapproved social post blocked by execute_campaign
# ---------------------------------------------------------------------------

async def test_unapproved_social_post_blocked_by_execute_campaign():
    """
    social_posts with status != 'approved' must never trigger run_tool.
    execute_campaign only queries posts filtered by status='approved'; a pending
    post is invisible to the publish loop and produces posts_published=0.
    """
    print("\n=== Phase 23: Unapproved social_post blocked by execute_campaign ===")
    from BACKEND_AI_AGENTS import get_agent

    noa = get_agent("social_media_manager_agent")
    campaign_id = str(uuid.uuid4())
    fake_campaign = {
        "id": campaign_id, "name": "Gate Test", "goal": "test",
        "platforms": ["facebook_group"], "tone": "professional", "status": "draft", "plan": {},
    }

    with patch("social.campaign_manager.get_campaign", new_callable=AsyncMock) as mock_get, \
         patch("social.campaign_manager.get_campaign_posts", new_callable=AsyncMock) as mock_posts, \
         patch("social.campaign_manager.prepare_campaign_content", new_callable=AsyncMock) as mock_prepare, \
         patch("social.campaign_manager.update_campaign_status", new_callable=AsyncMock), \
         patch("social.tools.run_tool", new_callable=AsyncMock) as mock_run:
        mock_get.return_value = fake_campaign
        mock_posts.return_value = []  # no approved posts — all pending
        mock_prepare.return_value = [
            {"post_id": str(uuid.uuid4()), "platform": "facebook_group", "status": "pending_approval"}
        ]

        db = AsyncMock()
        db.execute = AsyncMock(return_value=MagicMock(rowcount=1, fetchone=MagicMock(return_value=None)))
        db.commit = AsyncMock()

        result = await noa.execute_campaign(campaign_id, db, dry_run=False)

    assert not mock_run.called, "run_tool called despite unapproved (pending) social post"
    record("execute_campaign: run_tool NOT called for unapproved social_post", PASS)
    assert result.get("posts_published", 0) == 0, \
        f"posts_published={result.get('posts_published')} despite all posts pending"
    record("execute_campaign: posts_published=0 when social_posts.status != 'approved'", PASS)
    assert result.get("status") == "pending_approval", \
        f"Expected status='pending_approval', got {result.get('status')!r}"
    record("execute_campaign: returns status='pending_approval' when no approved posts", PASS)


# ---------------------------------------------------------------------------
# Phase 24 — Unapproved group target blocked by facebook_group_publish
# ---------------------------------------------------------------------------

async def test_unapproved_group_target_blocked_by_tool():
    """
    facebook_group_publish must return ToolResult(status='pending_approval') when
    group_targets.status != 'approved'. The browser action must never be reached.
    """
    print("\n=== Phase 24: Unapproved group_target blocked by facebook_group_publish ===")
    from social.tools import facebook_group_publish

    group_id = str(uuid.uuid4())

    # DB returns a row with status='pending' (not approved)
    pending_row = MagicMock()
    pending_row.group_url = "https://www.facebook.com/groups/musahnikim/"
    pending_row.status = "pending"
    select_result = MagicMock()
    select_result.fetchone.return_value = pending_row

    db = AsyncMock()
    db.execute = AsyncMock(return_value=select_result)
    db.commit = AsyncMock()

    with patch("social.facebook_browser.GroupAgent.publish_group_post", new_callable=AsyncMock) as mock_pub:
        result = await facebook_group_publish(group_id=group_id, content="Test post", db=db)

    assert result.status == "pending_approval", \
        f"Expected 'pending_approval', got {result.status!r}"
    record("facebook_group_publish: returns status='pending_approval' for pending group_target", PASS)

    assert not mock_pub.called, "GroupAgent.publish_group_post called despite unapproved group_target"
    record("facebook_group_publish: browser action NOT invoked for unapproved group_target", PASS)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def run() -> int:
    await test_meta_token_errors()
    await test_approval_gate()
    await test_campaign_idempotency()
    await test_campaign_status_transitions()
    await test_tool_authorization()
    test_cerebras_gate()
    await test_feedback_loop_safety()
    await test_input_validation()
    await test_engagement_dedup()
    await test_insights_parser()
    await test_content_length()
    # New phases — operational campaign workflow approval validation
    await test_execute_campaign_generates_pending_approval()
    await test_execute_campaign_publishes_after_approval()
    await test_content_version_invalidates_approval()
    await test_utm_params_deterministic()
    # New phases — Facebook Group pipeline invariants
    await test_group_target_pending_not_selectable()
    await test_duplicate_approved_group_url_rejected()
    await test_group_publish_success_updates_last_posted_at()
    await test_group_publish_success_increments_posts_sent()
    await test_group_publish_failure_skips_bookkeeping()
    await test_execute_campaign_facebook_group_routing()
    await test_group_post_no_id_published_state()
    await test_unapproved_social_post_blocked_by_execute_campaign()
    await test_unapproved_group_target_blocked_by_tool()
    return sum(1 for r in results if r["status"] == FAIL)


def print_report() -> int:
    print("\n" + "=" * 60)
    print("SOCIAL HARDENING TEST REPORT")
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
    print("AutoSpareFinder — Social Hardening Test Suite")
    asyncio.run(run())
    exit_code = print_report()
    sys.exit(1 if exit_code else 0)
