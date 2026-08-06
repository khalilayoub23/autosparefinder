"""
routes/campaigns.py — Campaign management API.

Endpoints:
  POST   /api/v1/campaigns               create a campaign
  GET    /api/v1/campaigns               list campaigns
  GET    /api/v1/campaigns/{id}          get one campaign
  PATCH  /api/v1/campaigns/{id}/status   change status
  GET    /api/v1/campaigns/{id}/analytics analytics for one campaign

  POST   /api/v1/campaigns/groups        add a group target
  GET    /api/v1/campaigns/groups        list group targets
  POST   /api/v1/campaigns/groups/{id}/approve  approve a group target
  POST   /api/v1/campaigns/groups/scan   trigger a group scan

  POST   /api/v1/campaigns/tools/run     call any social tool by name

All endpoints require admin auth. Integrates with:
  - social/campaign_manager.py  (persistence)
  - social/tools.py             (social actions)
  - social/feedback_analyzer.py (analytics)
  - BACKEND_AI_AGENTS.SocialMediaManagerAgent (LLM plan generation)

Author: AutoSpareFinder — 2026-08-06
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from BACKEND_AUTH_SECURITY import get_current_admin_user
from BACKEND_DATABASE_MODELS import get_db, User

log = logging.getLogger("routes.campaigns")
router = APIRouter()


# ---------------------------------------------------------------------------
# Request/response schemas
# ---------------------------------------------------------------------------

class CreateCampaignRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=255)
    goal: str = Field(..., min_length=5)
    platforms: list[str] = Field(..., min_items=1)
    target_audience: str = ""
    tone: str = "professional"
    duration_days: Optional[int] = None
    budget_ils: Optional[float] = None
    # If True, call SocialMediaManagerAgent to generate the plan automatically
    generate_plan: bool = True


class UpdateCampaignStatusRequest(BaseModel):
    status: str  # draft | active | paused | completed | archived


class AddGroupTargetRequest(BaseModel):
    group_url: str = Field(..., min_length=10)
    group_name: str = Field(..., min_length=2, max_length=255)
    platform: str = "facebook"
    description: str = ""
    relevance_tags: list[str] = []
    member_count_estimate: Optional[int] = None


class RunToolRequest(BaseModel):
    tool: str
    kwargs: dict = {}


# ---------------------------------------------------------------------------
# Campaign endpoints
# ---------------------------------------------------------------------------

@router.post("/api/v1/campaigns", status_code=201)
async def create_campaign(
    data: CreateCampaignRequest,
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new marketing campaign.

    Optionally calls NOA to auto-generate a structured campaign plan.
    """
    plan: dict = {}
    if data.generate_plan:
        try:
            from BACKEND_AI_AGENTS import get_agent
            noa = get_agent("social_media_manager_agent")
            plan = await noa.generate_campaign_plan(
                topic=data.goal,
                platforms=data.platforms,
                tone=data.tone,
                duration_days=data.duration_days,
                proposed_budget_ils=data.budget_ils,
            )
        except Exception as exc:
            log.warning("create_campaign: NOA plan generation failed: %s", exc)
            plan = {"error": str(exc)[:200], "note": "plan generation failed; campaign created without plan"}

    from social.campaign_manager import create_campaign as _create
    campaign = await _create(
        db,
        name=data.name,
        goal=data.goal,
        platforms=data.platforms,
        target_audience=data.target_audience,
        tone=data.tone,
        duration_days=data.duration_days,
        budget_ils=data.budget_ils,
        plan=plan,
        created_by=str(current_user.id),
    )
    return {"campaign": campaign, "plan_generated": data.generate_plan}


@router.get("/api/v1/campaigns")
async def list_campaigns(
    status: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """List campaigns with optional status filter and pagination."""
    if limit > 100:
        limit = 100
    from social.campaign_manager import list_campaigns as _list
    return await _list(db, status=status, limit=limit, offset=offset)


@router.get("/api/v1/campaigns/{campaign_id}")
async def get_campaign(
    campaign_id: str,
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Get a single campaign by UUID."""
    from social.campaign_manager import get_campaign as _get
    campaign = await _get(db, campaign_id=campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return campaign


@router.patch("/api/v1/campaigns/{campaign_id}/status")
async def update_campaign_status(
    campaign_id: str,
    data: UpdateCampaignStatusRequest,
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Transition a campaign's lifecycle status."""
    from social.campaign_manager import update_campaign_status as _update
    try:
        updated = await _update(db, campaign_id=campaign_id, status=data.status)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if not updated:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return {"campaign_id": campaign_id, "status": data.status}


@router.get("/api/v1/campaigns/{campaign_id}/analytics")
async def get_campaign_analytics(
    campaign_id: str,
    period_days: int = 7,
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate and return an analytics report for a campaign."""
    from social.feedback_analyzer import generate_analytics_report
    return await generate_analytics_report(db, period_days=period_days, campaign_id=campaign_id)


# ---------------------------------------------------------------------------
# Group target endpoints
# ---------------------------------------------------------------------------

@router.post("/api/v1/campaigns/groups", status_code=201)
async def add_group_target(
    data: AddGroupTargetRequest,
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Add a Facebook group as a target. Starts in 'pending' status pending owner approval."""
    from social.campaign_manager import add_group_target as _add
    result = await _add(
        db,
        group_url=data.group_url,
        group_name=data.group_name,
        platform=data.platform,
        description=data.description,
        relevance_tags=data.relevance_tags,
        member_count_estimate=data.member_count_estimate,
    )
    return result


@router.get("/api/v1/campaigns/groups")
async def list_group_targets(
    platform: str = "facebook",
    status: Optional[str] = None,
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """List group targets, optionally filtered by platform and status."""
    from social.campaign_manager import list_group_targets as _list
    return {"groups": await _list(db, platform=platform, status=status)}


@router.post("/api/v1/campaigns/groups/{group_id}/approve")
async def approve_group_target(
    group_id: str,
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Approve a pending group target so the browser agent may engage with it."""
    from social.campaign_manager import approve_group_target as _approve
    updated = await _approve(db, group_id=group_id, approved_by=str(current_user.id))
    if not updated:
        raise HTTPException(
            status_code=404,
            detail="Group not found or already approved/rejected"
        )
    return {"group_id": group_id, "status": "approved"}


@router.post("/api/v1/campaigns/groups/scan")
async def trigger_group_scan(
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Trigger an immediate Facebook group scan and return discoveries.

    Results are also sent to the owner WhatsApp for comment approval.
    This is a blocking call (Playwright runs sync); expect 30-120s latency.
    """
    from social.tools import facebook_group_scan
    result = await facebook_group_scan(db=db)
    if result.status == "error":
        raise HTTPException(status_code=502, detail=result.error or "scan failed")
    return result.dict()


# ---------------------------------------------------------------------------
# Tool dispatcher endpoint
# ---------------------------------------------------------------------------

@router.post("/api/v1/campaigns/tools/run")
async def run_tool(
    data: RunToolRequest,
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Run any registered social tool by name.

    For exploration and admin testing. Tools that require approval gates
    (group_comment, group_publish) will validate the approval before acting.

    Body: {"tool": "facebook_get_insights", "kwargs": {"post_id": "xxx"}}
    """
    from social.tools import run_tool as _run, list_tools
    valid = {t["name"] for t in list_tools()}
    if data.tool not in valid:
        raise HTTPException(
            status_code=422,
            detail=f"unknown tool '{data.tool}'. valid: {sorted(valid)}"
        )
    result = await _run(data.tool, db=db, **data.kwargs)
    return result.dict()


# ---------------------------------------------------------------------------
# Platform status endpoint
# ---------------------------------------------------------------------------

@router.get("/api/v1/campaigns/platforms/status")
async def get_platform_status(
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Return connectivity status for all social platforms.

    Reads platform_accounts table + live token validation for Facebook.
    """
    from social.registry import ALL_PLATFORMS, is_configured
    from social.meta_client import validate_facebook_token, facebook_configured

    platforms = []
    for p in ALL_PLATFORMS:
        configured = is_configured(p)
        row = {"platform": p, "configured": configured, "status": "disconnected"}
        if configured:
            row["status"] = "connected"
        platforms.append(row)

    # Live Facebook token check
    fb_token_info = None
    if facebook_configured():
        fb_token_info = await validate_facebook_token()
        for p in platforms:
            if p["platform"] == "facebook":
                p["token_valid"] = fb_token_info.get("valid")
                p["token_scopes"] = fb_token_info.get("scopes", [])
                p["token_expires_at"] = (
                    fb_token_info["expires_at"].isoformat()
                    if fb_token_info.get("expires_at") else None
                )

    return {"platforms": platforms, "facebook_token": fb_token_info}


# ---------------------------------------------------------------------------
# Analytics overview (all campaigns)
# ---------------------------------------------------------------------------

@router.get("/api/v1/campaigns/analytics/overview")
async def get_analytics_overview(
    period_days: int = 7,
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Platform-wide analytics overview for the last N days."""
    from social.feedback_analyzer import generate_analytics_report, get_top_performers
    report = await generate_analytics_report(db, period_days=period_days)
    top = await get_top_performers(db, days=period_days)
    return {**report, "top_performers": top}
