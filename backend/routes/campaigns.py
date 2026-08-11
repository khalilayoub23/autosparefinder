"""
routes/campaigns.py — Campaign management API.

Endpoints:
  POST   /api/v1/campaigns                           create a campaign
  GET    /api/v1/campaigns                           list campaigns
  GET    /api/v1/campaigns/{id}                      get one campaign
  PATCH  /api/v1/campaigns/{id}/status               change status
  POST   /api/v1/campaigns/{id}/execute              execute (approval-gated)
  GET    /api/v1/campaigns/{id}/posts                list social_posts for campaign
  GET    /api/v1/campaigns/{id}/analytics            analytics for one campaign

  Group targets:
  POST   /api/v1/campaigns/groups                    add a group target
  GET    /api/v1/campaigns/groups                    list group targets
  POST   /api/v1/campaigns/groups/{id}/approve       approve a group target
  POST   /api/v1/campaigns/groups/scan               trigger a group scan

  Social post content approval (human-in-the-loop, Phase 5-7):
  PATCH  /api/v1/social-posts/{id}/approve           approve a pending post
  PATCH  /api/v1/social-posts/{id}/reject            reject a post
  PATCH  /api/v1/social-posts/{id}/content           edit content (bumps version, resets to pending)

  Tool dispatcher:
  POST   /api/v1/campaigns/tools/run                 call any social tool by name

Approval invariant:
  execute_campaign() generates content → stores as pending_approval → returns early.
  Owner calls PATCH /approve for each post.
  A second execute_campaign() call publishes only the approved posts.
  An edit (PATCH /content) bumps content_version and resets to pending_approval,
  preventing a stale-approved post from being published after the owner edits it.

All endpoints require admin auth. Integrates with:
  - social/campaign_manager.py  (persistence + approval workflow)
  - social/tools.py             (social actions)
  - social/feedback_analyzer.py (analytics)
  - BACKEND_AI_AGENTS.SocialMediaManagerAgent (LLM plan generation + execute)

Author: AutoSpareFinder — 2026-08-09
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


class ExecuteCampaignRequest(BaseModel):
    dry_run: bool = False


class ApprovePostRequest(BaseModel):
    approved_version: Optional[int] = None   # if set, must match current content_version


class RejectPostRequest(BaseModel):
    reason: str = ""


class UpdatePostContentRequest(BaseModel):
    content: str = Field(..., min_length=10)
    scheduled_at: Optional[datetime] = None


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
# Campaign execution endpoint (Phase 3: SHIRA → NOA → Tools)
# ---------------------------------------------------------------------------

@router.post("/api/v1/campaigns/{campaign_id}/execute")
async def execute_campaign(
    campaign_id: str,
    data: ExecuteCampaignRequest = ExecuteCampaignRequest(),
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Execute a campaign: generate content for each platform and publish via NOA.

    Architecture: this endpoint → SocialMediaManagerAgent.execute_campaign()
                                                    ↓
                                          social/tools.run_tool()

    Pass dry_run=true to preview generated content without publishing.
    """
    from BACKEND_AI_AGENTS import get_agent
    noa = get_agent("social_media_manager_agent")
    return await noa.execute_campaign(campaign_id, db, dry_run=data.dry_run)


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
# Campaign post listing (Phase 6 — human-in-the-loop review)
# ---------------------------------------------------------------------------

@router.get("/api/v1/campaigns/{campaign_id}/posts")
async def list_campaign_posts(
    campaign_id: str,
    status: Optional[str] = None,
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """List social_posts associated with a campaign.

    Use status filter to see e.g. posts awaiting approval (status=pending_approval)
    or already published (status=published).
    """
    from social.campaign_manager import get_campaign_posts
    posts = await get_campaign_posts(db, campaign_id=campaign_id, status=status)
    return {"campaign_id": campaign_id, "posts": posts, "total": len(posts)}


# ---------------------------------------------------------------------------
# Social post content approval (Phase 5-7 — human-in-the-loop)
# ---------------------------------------------------------------------------

@router.patch("/api/v1/social-posts/{post_id}/approve")
async def approve_social_post(
    post_id: str,
    data: ApprovePostRequest = ApprovePostRequest(),
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Approve a pending social post for publishing.

    Records: approver UUID, timestamp, and optionally validates content_version
    to prevent approving a stale version (if the post was edited since you read it).

    After approval, call POST /api/v1/campaigns/{id}/execute again to publish.
    """
    from social.campaign_manager import approve_post_content
    approved = await approve_post_content(
        db,
        post_id=post_id,
        approved_by=str(current_user.id),
        approved_version=data.approved_version,
    )
    if not approved:
        raise HTTPException(
            status_code=409,
            detail=(
                "Post not found, not in pending_approval status, "
                "or content_version mismatch (post was edited after you read it)."
            )
        )
    return {
        "post_id": post_id,
        "status": "approved",
        "approved_by": str(current_user.id),
    }


@router.patch("/api/v1/social-posts/{post_id}/reject")
async def reject_social_post(
    post_id: str,
    data: RejectPostRequest = RejectPostRequest(),
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Reject a pending or approved social post.

    Sets status='rejected' and records the rejection reason.
    To regenerate content, call POST /api/v1/campaigns/{id}/execute again.
    """
    from social.campaign_manager import reject_post_content
    rejected = await reject_post_content(
        db,
        post_id=post_id,
        reason=data.reason,
        rejected_by=str(current_user.id),
    )
    if not rejected:
        raise HTTPException(
            status_code=404,
            detail="Post not found or already published (cannot reject published posts)."
        )
    return {"post_id": post_id, "status": "rejected", "reason": data.reason}


@router.patch("/api/v1/social-posts/{post_id}/content")
async def update_post_content(
    post_id: str,
    data: UpdatePostContentRequest,
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Edit a post's content.

    Content version is bumped and status is reset to pending_approval, so a
    prior approval cannot publish the old version. The owner must re-approve
    after editing.
    """
    from social.campaign_manager import update_post_content as _update
    result = await _update(
        db,
        post_id=post_id,
        new_content=data.content,
        scheduled_at=data.scheduled_at,
    )
    if not result:
        raise HTTPException(
            status_code=404,
            detail="Post not found or already published (cannot edit published posts)."
        )
    return result


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
