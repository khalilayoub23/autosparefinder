"""
Script: social/campaign_manager.py
Purpose: Campaign lifecycle management for the Social Media Department.
         Bridges the SocialMediaManagerAgent (NOA) with the campaigns table,
         social_posts pipeline, group_targets, and the tool system.

         This is NOT an autonomous agent — it is a stateless service module
         that the NOA loop and the campaigns API route call directly.
         All LLM work stays in SocialMediaManagerAgent; this module handles
         persistence and orchestration only.

Process:
  create_campaign()       — create a campaigns row from NOA's plan
  link_post_to_campaign() — associate a social_posts row with a campaign
  get_campaign_status()   — aggregate status: posts published, engagement totals
  list_campaigns()        — paginated list with performance summaries
  update_campaign_status()— change lifecycle state
  create_group_task()     — draft a group comment/post for owner approval
  approve_group_task()    — mark approved → trigger browser agent execution
  list_pending_group_tasks() — what awaits owner review

Data Imported/Modified:
  campaigns, group_targets, social_posts (link only), engagement_events (read)
Data Sources: internal DB only
Last Updated: 2026-08-06
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa

log = logging.getLogger("campaign_manager")

# ---------------------------------------------------------------------------
# Campaign CRUD
# ---------------------------------------------------------------------------

async def create_campaign(
    db: Any,
    *,
    name: str,
    goal: str,
    platforms: list[str],
    target_audience: str = "",
    tone: str = "professional",
    duration_days: int | None = None,
    budget_ils: float | None = None,
    plan: dict | None = None,
    created_by: str | None = None,
) -> dict:
    """Insert a new campaign row. Returns the created campaign dict."""
    cid = uuid.uuid4()
    now = datetime.utcnow()
    await db.execute(
        sa.text("""
            INSERT INTO campaigns
                (id, name, goal, platforms, target_audience, tone,
                 duration_days, budget_ils, status, plan, linked_post_ids,
                 total_reach, total_impressions, total_engagement, total_clicks, total_leads,
                 created_by, created_at, updated_at)
            VALUES
                (:id, :name, :goal, :platforms, :target, :tone,
                 :days, :budget, 'active', :plan::jsonb, '{}',
                 0, 0, 0, 0, 0,
                 :created_by, :now, :now)
        """),
        {
            "id": str(cid),
            "name": name,
            "goal": goal,
            "platforms": platforms,
            "target": target_audience,
            "tone": tone,
            "days": duration_days,
            "budget": budget_ils,
            "plan": __import__("json").dumps(plan or {}),
            "created_by": created_by,
            "now": now,
        },
    )
    await db.commit()
    log.info("campaign_manager: created campaign %s '%s'", cid, name)
    return await get_campaign(db, campaign_id=str(cid))


async def get_campaign(db: Any, *, campaign_id: str) -> dict | None:
    """Fetch one campaign by UUID. Returns None if not found."""
    row = (await db.execute(
        sa.text("""
            SELECT id, name, goal, platforms, target_audience, tone,
                   duration_days, budget_ils, status, plan, linked_post_ids,
                   total_reach, total_impressions, total_engagement, total_clicks, total_leads,
                   performance_score, created_by, created_at, updated_at, completed_at
            FROM campaigns WHERE id = CAST(:id AS uuid)
        """),
        {"id": campaign_id},
    )).fetchone()
    if not row:
        return None
    return _row_to_dict(row)


async def list_campaigns(
    db: Any,
    *,
    status: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> dict:
    """Paginated campaign list with performance summaries."""
    where = "WHERE status = :status" if status else ""
    rows = (await db.execute(
        sa.text(f"""
            SELECT id, name, goal, platforms, status, tone, total_reach,
                   total_engagement, total_leads, performance_score, created_at, updated_at
            FROM campaigns {where}
            ORDER BY created_at DESC
            LIMIT :limit OFFSET :offset
        """),
        {"status": status, "limit": limit, "offset": offset},
    )).fetchall()
    total = (await db.execute(
        sa.text(f"SELECT COUNT(*) FROM campaigns {where}"),
        {"status": status},
    )).scalar()
    return {
        "campaigns": [_row_to_dict(r) for r in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


async def update_campaign_status(
    db: Any,
    *,
    campaign_id: str,
    status: str,
) -> bool:
    """Transition campaign lifecycle state. Returns True if found+updated."""
    valid = {"draft", "active", "paused", "completed", "archived"}
    if status not in valid:
        raise ValueError(f"invalid status '{status}'; must be one of {valid}")
    now = datetime.utcnow()
    completed_at = now if status == "completed" else None
    result = await db.execute(
        sa.text("""
            UPDATE campaigns SET status=:s, updated_at=:now,
                completed_at = CASE WHEN :s='completed' THEN :now ELSE completed_at END
            WHERE id = CAST(:id AS uuid)
        """),
        {"s": status, "now": now, "id": campaign_id},
    )
    await db.commit()
    return (result.rowcount or 0) > 0


async def link_post_to_campaign(db: Any, *, campaign_id: str, post_id: str) -> bool:
    """Add a social_posts.id to the campaign's linked_post_ids array."""
    result = await db.execute(
        sa.text("""
            UPDATE campaigns
            SET linked_post_ids = array_append(linked_post_ids, :pid),
                updated_at = NOW()
            WHERE id = CAST(:cid AS uuid)
              AND NOT (:pid = ANY(linked_post_ids))
        """),
        {"cid": campaign_id, "pid": post_id},
    )
    await db.commit()
    return (result.rowcount or 0) > 0


async def update_campaign_performance(
    db: Any,
    *,
    campaign_id: str,
    reach_delta: int = 0,
    impressions_delta: int = 0,
    engagement_delta: int = 0,
    clicks_delta: int = 0,
    leads_delta: int = 0,
) -> None:
    """Increment aggregate performance counters on a campaign."""
    await db.execute(
        sa.text("""
            UPDATE campaigns SET
                total_reach       = total_reach + :r,
                total_impressions = total_impressions + :i,
                total_engagement  = total_engagement + :e,
                total_clicks      = total_clicks + :c,
                total_leads       = total_leads + :l,
                updated_at        = NOW()
            WHERE id = CAST(:id AS uuid)
        """),
        {"r": reach_delta, "i": impressions_delta, "e": engagement_delta,
         "c": clicks_delta, "l": leads_delta, "id": campaign_id},
    )
    await db.commit()


# ---------------------------------------------------------------------------
# Group target management
# ---------------------------------------------------------------------------

async def add_group_target(
    db: Any,
    *,
    group_url: str,
    group_name: str,
    platform: str = "facebook",
    description: str = "",
    relevance_tags: list[str] | None = None,
    member_count_estimate: int | None = None,
) -> dict:
    """Add a new group target in 'pending' state (requires owner approval)."""
    gid = uuid.uuid4()
    now = datetime.utcnow()
    await db.execute(
        sa.text("""
            INSERT INTO group_targets
                (id, platform, group_name, group_url, description,
                 relevance_tags, member_count_estimate,
                 status, posts_sent, created_at, updated_at)
            VALUES
                (:id, :platform, :name, :url, :desc,
                 :tags, :members,
                 'pending', 0, :now, :now)
        """),
        {
            "id": str(gid), "platform": platform,
            "name": group_name, "url": group_url,
            "desc": description, "tags": relevance_tags or [],
            "members": member_count_estimate, "now": now,
        },
    )
    await db.commit()
    log.info("campaign_manager: added group_target %s '%s' (pending approval)", gid, group_name)
    return {"id": str(gid), "group_name": group_name, "status": "pending"}


async def approve_group_target(
    db: Any,
    *,
    group_id: str,
    approved_by: str,
) -> bool:
    """Mark a group as approved for automated engagement."""
    now = datetime.utcnow()
    result = await db.execute(
        sa.text("""
            UPDATE group_targets
            SET status='approved', approved_by=:by, approved_at=:now, updated_at=:now
            WHERE id = CAST(:id AS uuid) AND status='pending'
        """),
        {"id": group_id, "by": approved_by, "now": now},
    )
    await db.commit()
    return (result.rowcount or 0) > 0


async def list_group_targets(
    db: Any,
    *,
    platform: str = "facebook",
    status: str | None = None,
) -> list[dict]:
    where_parts = ["platform = :platform"]
    params: dict = {"platform": platform}
    if status:
        where_parts.append("status = :status")
        params["status"] = status
    where = "WHERE " + " AND ".join(where_parts)
    rows = (await db.execute(
        sa.text(f"""
            SELECT id, group_name, group_url, status, relevance_tags,
                   posts_sent, last_posted_at, avg_leads_per_post, created_at
            FROM group_targets {where}
            ORDER BY created_at DESC
        """),
        params,
    )).fetchall()
    return [_row_to_dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Group task helpers (draft → owner approval → execute)
# ---------------------------------------------------------------------------

async def create_group_discovery_tasks(
    db: Any,
    *,
    discoveries: list[dict],
    campaign_id: str | None = None,
) -> list[dict]:
    """
    For each discovery from GroupAgent.scan_groups(), draft a comment
    via the browser agent and queue it as a pending WhatsApp approval.

    Returns list of pending task summaries to send to the owner.
    """
    from social.facebook_browser import GroupAgent
    agent = GroupAgent()
    tasks = []

    for d in discoveries[:5]:  # cap at 5 per cycle; quality > quantity
        if d.get("suggested_action") not in ("comment", "post"):
            continue
        draft = await agent.draft_group_comment(d)
        if not draft:
            continue
        tasks.append({
            "group_id": d.get("group_id"),
            "group_name": d.get("group_name"),
            "post_url": d.get("post_url"),
            "post_snippet": d.get("post_text", "")[:100],
            "draft_comment": draft,
            "relevance_score": d.get("relevance_score"),
            "campaign_id": campaign_id,
        })

    log.info("campaign_manager: drafted %d group comment tasks", len(tasks))
    return tasks


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _row_to_dict(row: Any) -> dict:
    """Convert a SQLAlchemy Row to a plain dict, serializing datetimes."""
    d = dict(row._mapping) if hasattr(row, "_mapping") else dict(row)
    for k, v in d.items():
        if isinstance(v, datetime):
            d[k] = v.isoformat()
        elif isinstance(v, uuid.UUID):
            d[k] = str(v)
    return d
