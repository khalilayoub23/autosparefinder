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
  create_campaign()           — create a campaigns row from NOA's plan
  link_post_to_campaign()     — associate a social_posts row with a campaign
  get_campaign()              — fetch one campaign by id
  list_campaigns()            — paginated list with performance summaries
  update_campaign_status()    — change lifecycle state (forward-only)
  prepare_campaign_content()  — generate+store posts as pending_approval (NO publish)
  approve_post_content()      — record human approval with approver identity + timestamp
  reject_post_content()       — record rejection with reason; post returns to draft
  update_post_content()       — edit content → content_version++, approval invalidated
  get_campaign_posts()        — list social_posts for a campaign (optionally by status)
  create_group_task()         — draft a group comment/post for owner approval
  approve_group_task()        — mark approved → trigger browser agent execution
  list_pending_group_tasks()  — what awaits owner review

Approval invariant (Phase 5 & 6 requirement):
  Generated → PendingApproval → Approved → ReadyForScheduling → Published
  The system NEVER allows Generated → Published without the Approved state.
  This is enforced in execute_campaign (BACKEND_AI_AGENTS.py) by checking
  for approved social_posts rows BEFORE calling any publish tool.

  An edit (update_post_content) bumps content_version and resets status to
  pending_approval so a stale-approval cannot publish a newer version of a post.

Data Imported/Modified:
  campaigns, group_targets, social_posts, engagement_events (read)
Data Sources: internal DB only
Last Updated: 2026-08-09
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
    if not name or not name.strip():
        raise ValueError("campaign name is required")
    if not platforms:
        raise ValueError("at least one platform is required")
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
                 :days, :budget, 'draft', CAST(:plan AS jsonb), '{}',
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
    """Transition campaign lifecycle state. Returns True if found+updated.

    Enforces valid forward-only transitions:
      draft   → active | archived
      active  → paused | completed | archived
      paused  → active | completed | archived
      completed → archived   (terminal except for archiving)
      archived  → (none)     (fully terminal)
    """
    # Valid values (DB CHECK constraint also enforces this)
    _VALID = {"draft", "active", "paused", "completed", "archived"}
    # Forward-only transition table
    _ALLOWED_FROM: dict[str, set[str]] = {
        "active":    {"draft", "paused"},
        "paused":    {"active"},
        "completed": {"active", "paused"},
        "archived":  {"draft", "active", "paused", "completed"},
    }
    if status not in _VALID:
        raise ValueError(f"invalid status '{status}'; must be one of {_VALID}")

    allowed_from = _ALLOWED_FROM.get(status)
    if allowed_from is None:
        raise ValueError(f"status '{status}' cannot be set directly; it is a terminal state")

    now = datetime.utcnow()
    completed_at = now if status == "completed" else None

    result = await db.execute(
        sa.text("""
            UPDATE campaigns
            SET status=:s, updated_at=:now, completed_at=:completed_at
            WHERE id = CAST(:id AS uuid)
              AND status = ANY(:from_states)
        """),
        {
            "s": status,
            "now": now,
            "completed_at": completed_at,
            "id": campaign_id,
            "from_states": list(allowed_from),
        },
    )
    await db.commit()
    updated = (result.rowcount or 0) > 0
    if not updated:
        log.warning(
            "campaign_manager: update_campaign_status %s → %r rejected "
            "(not found or invalid transition from current status)",
            campaign_id, status
        )
    return updated


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
# Social post content management — approval workflow (Phase 5-8)
# ---------------------------------------------------------------------------

async def prepare_campaign_content(
    db: Any,
    *,
    campaign_id: str,
    content_by_platform: dict[str, str],
    created_by: str | None = None,
    scheduled_at: datetime | None = None,
    utm_params: dict | None = None,
) -> list[dict]:
    """Store LLM-generated content as social_posts with status='pending_approval'.

    DOES NOT publish anything. Returns list of created post summaries.
    Each platform gets its own row so it can be approved/rejected independently.

    content_by_platform: {"facebook": "...", "instagram": "...", ...}
    utm_params: stored in external_post_ids.__utm as metadata for the publisher.
    """
    now = datetime.utcnow()
    created = []
    _system_uuid = "00000000-0000-0000-0000-000000000000"
    actor = created_by or _system_uuid
    if not _is_valid_uuid(actor):
        actor = _system_uuid

    for platform, content in content_by_platform.items():
        if not content or not content.strip():
            continue
        pid = uuid.uuid4()
        ext_ids: dict = {"__campaign_id__": campaign_id}
        if utm_params:
            ext_ids["__utm"] = utm_params
        if scheduled_at:
            ext_ids["__scheduled_at__"] = scheduled_at.isoformat()

        await db.execute(
            sa.text("""
                INSERT INTO social_posts
                    (id, content, platforms, status, scheduled_at, published_at,
                     external_post_ids, created_by, approved_by, approved_at,
                     rejection_reason, campaign_id, content_version,
                     created_at, updated_at)
                VALUES
                    (:id, :content, ARRAY[:platform]::text[], 'pending_approval',
                     :sched, NULL,
                     CAST(:ext AS jsonb), CAST(:created_by AS uuid), NULL, NULL,
                     NULL, CAST(:campaign_id AS uuid), 1,
                     :now, :now)
            """),
            {
                "id": str(pid),
                "content": content.strip(),
                "platform": platform,
                "sched": scheduled_at,
                "ext": __import__("json").dumps(ext_ids),
                "created_by": actor,
                "campaign_id": campaign_id,
                "now": now,
            },
        )
        created.append({"post_id": str(pid), "platform": platform, "status": "pending_approval"})

    await db.commit()
    log.info(
        "campaign_manager: prepared %d pending_approval posts for campaign %s",
        len(created), campaign_id
    )
    return created


async def approve_post_content(
    db: Any,
    *,
    post_id: str,
    approved_by: str,
    approved_version: int | None = None,
) -> bool:
    """Record human approval for a social post.

    Sets status='approved', approved_by, approved_at=NOW().
    If approved_version is supplied and differs from the current content_version,
    the approval is refused (prevents approving a stale version after an edit).
    Returns True if approved, False if not found / wrong version / wrong status.
    """
    now = datetime.utcnow()
    _system_uuid = "00000000-0000-0000-0000-000000000000"
    actor = approved_by or _system_uuid
    if not _is_valid_uuid(actor):
        actor = _system_uuid

    version_clause = ""
    params: dict = {"id": post_id, "by": actor, "now": now}
    if approved_version is not None:
        version_clause = " AND content_version = :ver"
        params["ver"] = approved_version

    result = await db.execute(
        sa.text(f"""
            UPDATE social_posts
            SET status='approved', approved_by=CAST(:by AS uuid), approved_at=:now,
                updated_at=:now
            WHERE id = CAST(:id AS uuid)
              AND status = 'pending_approval'
              {version_clause}
        """),
        params,
    )
    await db.commit()
    approved = (result.rowcount or 0) > 0
    if not approved:
        log.warning(
            "campaign_manager: approve_post_content %s — not found, wrong status, or version mismatch",
            post_id
        )
    else:
        log.info("campaign_manager: post %s approved by %s", post_id, approved_by)
    return approved


async def reject_post_content(
    db: Any,
    *,
    post_id: str,
    reason: str = "",
    rejected_by: str | None = None,
) -> bool:
    """Mark a social post as rejected. Returns True if updated."""
    now = datetime.utcnow()
    result = await db.execute(
        sa.text("""
            UPDATE social_posts
            SET status='rejected', rejection_reason=:reason, updated_at=:now
            WHERE id = CAST(:id AS uuid)
              AND status IN ('pending_approval', 'approved')
        """),
        {"id": post_id, "reason": reason or "", "now": now},
    )
    await db.commit()
    return (result.rowcount or 0) > 0


async def update_post_content(
    db: Any,
    *,
    post_id: str,
    new_content: str,
    scheduled_at: datetime | None = None,
) -> dict | None:
    """Edit post content and bump content_version.

    Bumping content_version INVALIDATES any prior approval (resets status to
    pending_approval). This prevents a stale-approved version being published
    after the owner edits it. Returns the updated post row or None if not found.
    """
    if not new_content or not new_content.strip():
        raise ValueError("new_content must not be empty")
    now = datetime.utcnow()
    sched_clause = ""
    params: dict = {"id": post_id, "content": new_content.strip(), "now": now}
    if scheduled_at is not None:
        sched_clause = ", scheduled_at=:sched"
        params["sched"] = scheduled_at

    result = await db.execute(
        sa.text(f"""
            UPDATE social_posts
            SET content=:content,
                content_version = content_version + 1,
                status = 'pending_approval',
                approved_by = NULL,
                approved_at = NULL,
                updated_at = :now
                {sched_clause}
            WHERE id = CAST(:id AS uuid)
              AND status NOT IN ('published')
            RETURNING id, content_version, status
        """),
        params,
    )
    await db.commit()
    row = result.fetchone()
    if not row:
        return None
    return {"post_id": post_id, "content_version": row.content_version, "status": row.status}


async def get_campaign_posts(
    db: Any,
    *,
    campaign_id: str,
    status: str | None = None,
) -> list[dict]:
    """Return social_posts rows that belong to a campaign.

    Optionally filtered by status ('pending_approval', 'approved', 'published', …).
    """
    where_parts = ["campaign_id = CAST(:cid AS uuid)"]
    params: dict = {"cid": campaign_id}
    if status:
        where_parts.append("status = :status")
        params["status"] = status
    where = "WHERE " + " AND ".join(where_parts)
    rows = (await db.execute(
        sa.text(f"""
            SELECT id, content, platforms, status, scheduled_at, published_at,
                   external_post_ids, created_by, approved_by, approved_at,
                   rejection_reason, campaign_id, content_version, created_at, updated_at
            FROM social_posts {where}
            ORDER BY created_at ASC
        """),
        params,
    )).fetchall()
    return [_row_to_dict(r) for r in rows]


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


def _is_valid_uuid(s: str) -> bool:
    try:
        uuid.UUID(str(s))
        return True
    except (ValueError, AttributeError):
        return False
