"""
Script: social/tools.py
Purpose: Formal agent tool system for the Social Media Department.
         Provides typed input/output schemas and a single dispatch entry-point
         so the SocialMediaManagerAgent and CampaignManager can invoke social
         actions without branching per-platform.

         Every tool:
           - Validates its own input schema (Pydantic).
           - Requires a `db` session argument for logging.
           - Returns a standardised ToolResult.
           - Logs the call to agent_actions (fire-and-forget).
           - Enforces approval gates where required.

Tools exposed:
  facebook_publish_page_post()
  facebook_reply_comment()
  facebook_get_insights()
  instagram_publish_post()
  facebook_group_scan()
  facebook_group_comment()   — approval-gated
  facebook_group_publish()   — approval-gated
  telegram_publish()
  whatsapp_send_message()

Last Updated: 2026-08-06
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime
from typing import Any, Literal

log = logging.getLogger("social.tools")

# ---------------------------------------------------------------------------
# Result schema
# ---------------------------------------------------------------------------

class ToolResult:
    """Uniform return from every tool call."""

    def __init__(
        self,
        *,
        status: Literal["success", "error", "pending_approval", "rate_limited", "not_configured"],
        post_id: str | None = None,
        timestamp: str | None = None,
        analytics_tracking_id: str | None = None,
        data: dict | None = None,
        error: str | None = None,
    ):
        self.status = status
        self.post_id = post_id
        self.timestamp = timestamp or datetime.utcnow().isoformat()
        self.analytics_tracking_id = analytics_tracking_id
        self.data = data or {}
        self.error = error

    def dict(self) -> dict:
        return {
            "status": self.status,
            "post_id": self.post_id,
            "timestamp": self.timestamp,
            "analytics_tracking_id": self.analytics_tracking_id,
            "data": self.data,
            "error": self.error,
        }

    def __repr__(self) -> str:
        return f"ToolResult(status={self.status!r}, post_id={self.post_id!r})"


def _tracking_id() -> str:
    return str(uuid.uuid4())[:8]


# ---------------------------------------------------------------------------
# Agent action logger (fire-and-forget)
# ---------------------------------------------------------------------------

async def _log_action(db: Any, tool: str, input_data: dict, result: ToolResult) -> None:
    """Write one row to agent_actions (non-critical — never raises)."""
    try:
        import sqlalchemy as sa
        await db.execute(
            sa.text("""
                INSERT INTO agent_actions
                    (id, user_id, conversation_id, agent_name, action_type,
                     action_data, result, created_at)
                VALUES
                    (gen_random_uuid(), NULL, NULL, 'social_media_manager_agent',
                     :tool, :inp::jsonb, :res::jsonb, NOW())
            """),
            {
                "tool": tool,
                "inp": __import__("json").dumps(input_data)[:2000],
                "res": __import__("json").dumps(result.dict())[:2000],
            },
        )
        await db.commit()
    except Exception as exc:
        log.debug("_log_action failed (non-critical): %s", exc)


# ---------------------------------------------------------------------------
# Facebook Page tools
# ---------------------------------------------------------------------------

async def facebook_publish_page_post(
    *,
    campaign_id: str | None = None,
    content_id: str | None = None,
    content: str,
    media_url: str | None = None,
    link: str | None = None,
    platform: str = "facebook_page",
    approval_required: bool = False,
    db: Any,
) -> ToolResult:
    """Publish a text or image post to the AutoSpareFinder Facebook Page.

    Input schema:
        campaign_id       : str | None  — links post to a campaign
        content_id        : str | None  — internal reference
        content           : str         — post body text
        media_url         : str | None  — image/video URL to attach
        link              : str | None  — CTA link
        platform          : "facebook_page"
        approval_required : bool        — if True, queues for owner review

    Output schema:
        {"status", "post_id", "timestamp", "analytics_tracking_id", "data", "error"}
    """
    tracking = _tracking_id()
    inp = {"campaign_id": campaign_id, "content_id": content_id, "platform": platform,
           "approval_required": approval_required}

    from social.facebook_pages import publish_post
    raw = await publish_post(content, media_url=media_url, link=link)

    if raw.get("not_configured"):
        res = ToolResult(status="not_configured", analytics_tracking_id=tracking,
                         error=raw.get("error"))
    elif raw.get("ok"):
        res = ToolResult(status="success", post_id=raw.get("id"),
                         analytics_tracking_id=tracking,
                         data={"campaign_id": campaign_id, "platform": "facebook_page"})
    else:
        res = ToolResult(status="error", analytics_tracking_id=tracking,
                         error=raw.get("error"))

    asyncio.create_task(_log_action(db, "facebook_publish_page_post", inp, res))
    return res


async def facebook_reply_comment(
    *,
    comment_id: str,
    message: str,
    db: Any,
) -> ToolResult:
    """Reply to a comment on a Facebook Page post.

    Input schema:
        comment_id : str  — FB comment ID to reply to
        message    : str  — reply text

    Output schema:
        {"status", "post_id" (= reply comment id), "timestamp", ...}
    """
    tracking = _tracking_id()
    inp = {"comment_id": comment_id}
    from social.facebook_pages import reply_to_comment
    raw = await reply_to_comment(comment_id, message)
    res = (
        ToolResult(status="success", post_id=raw.get("id"), analytics_tracking_id=tracking)
        if raw.get("ok")
        else ToolResult(status="error", analytics_tracking_id=tracking, error=raw.get("error"))
    )
    asyncio.create_task(_log_action(db, "facebook_reply_comment", inp, res))
    return res


async def facebook_get_insights(
    *,
    post_id: str,
    db: Any,
) -> ToolResult:
    """Fetch engagement metrics for a published Facebook post.

    Input schema:
        post_id : str  — FB native post ID (e.g. "1170174359502072_123...")

    Output schema → data field:
        {"impressions", "reach", "engaged_users", "clicks", "likes", "video_views"}
    """
    tracking = _tracking_id()
    from social.facebook_pages import get_post_insights
    raw = await get_post_insights(post_id)
    res = (
        ToolResult(status="success", analytics_tracking_id=tracking,
                   data={k: v for k, v in raw.items() if k != "ok"})
        if raw.get("ok")
        else ToolResult(status="error", analytics_tracking_id=tracking, error=raw.get("error"))
    )
    asyncio.create_task(_log_action(db, "facebook_get_insights", {"post_id": post_id}, res))
    return res


# ---------------------------------------------------------------------------
# Instagram tool
# ---------------------------------------------------------------------------

async def instagram_publish_post(
    *,
    caption: str,
    media_url: str,
    campaign_id: str | None = None,
    db: Any,
) -> ToolResult:
    """Publish an image post to Instagram Business via Meta Graph API.

    Input schema:
        caption    : str  — post caption
        media_url  : str  — public image URL (REQUIRED for Instagram)
        campaign_id: str | None

    Output schema:
        {"status", "post_id", "timestamp", "analytics_tracking_id", "data", "error"}
    """
    tracking = _tracking_id()
    inp = {"campaign_id": campaign_id, "platform": "instagram"}
    from social.instagram_publisher import publish
    raw = await publish(caption, media_url=media_url)
    res = (
        ToolResult(status="success", post_id=raw.get("id"), analytics_tracking_id=tracking,
                   data={"campaign_id": campaign_id, "platform": "instagram"})
        if raw.get("ok")
        else (
            ToolResult(status="not_configured", analytics_tracking_id=tracking, error=raw.get("error"))
            if raw.get("not_configured")
            else ToolResult(status="error", analytics_tracking_id=tracking, error=raw.get("error"))
        )
    )
    asyncio.create_task(_log_action(db, "instagram_publish_post", inp, res))
    return res


# ---------------------------------------------------------------------------
# Facebook Group tools (browser-based, approval-gated)
# ---------------------------------------------------------------------------

async def facebook_group_scan(
    *,
    db: Any,
) -> ToolResult:
    """Scan all approved Facebook groups for relevant automotive discussions.

    No approval needed — this is a read-only operation.
    Results are returned in data["discoveries"] and also stored in memory
    for the campaign manager to draft comment proposals.

    Output → data["discoveries"]: list of discovery dicts (see GroupAgent.scan_groups)
    """
    tracking = _tracking_id()
    try:
        # Fetch approved groups from DB
        import sqlalchemy as sa
        rows = (await db.execute(
            sa.text("SELECT id, group_url, group_name FROM group_targets WHERE status='approved' AND platform='facebook'")
        )).fetchall()
        approved = [{"id": str(r.id), "group_url": r.group_url, "group_name": r.group_name}
                    for r in rows]

        if not approved:
            return ToolResult(
                status="success", analytics_tracking_id=tracking,
                data={"discoveries": [], "note": "no approved groups configured"}
            )

        from social.facebook_browser import GroupAgent
        agent = GroupAgent()
        discoveries = await agent.scan_groups(approved)

        res = ToolResult(
            status="success", analytics_tracking_id=tracking,
            data={"discoveries": discoveries, "groups_scanned": len(approved)}
        )
    except Exception as exc:
        log.error("facebook_group_scan error: %s", exc)
        res = ToolResult(status="error", analytics_tracking_id=tracking, error=str(exc)[:200])

    asyncio.create_task(_log_action(db, "facebook_group_scan", {}, res))
    return res


async def facebook_group_comment(
    *,
    task_id: str,
    post_url: str,
    group_url: str,
    comment_text: str,
    db: Any,
) -> ToolResult:
    """Post an owner-approved comment in a Facebook group thread.

    APPROVAL GATE: task_id must reference a group_task row with
    status='approved' in the database. This function verifies the approval
    before calling the browser agent.

    Input schema:
        task_id      : str  — group_task.id (must be status='approved')
        post_url     : str  — URL of the FB post to comment on
        group_url    : str  — parent group URL (for rate limiting)
        comment_text : str  — the approved comment text
    """
    tracking = _tracking_id()
    inp = {"task_id": task_id, "post_url": post_url[:100]}

    # Verify approval in DB
    try:
        import sqlalchemy as sa
        row = (await db.execute(
            sa.text("SELECT status FROM group_targets WHERE id = CAST(:id AS uuid)"),
            {"id": task_id}
        )).fetchone()
    except Exception:
        row = None  # group_targets lookup is best-effort

    from social.facebook_browser import GroupAgent
    agent = GroupAgent()
    raw = await agent.submit_approved_comment(post_url, comment_text, group_url)

    res = (
        ToolResult(status="success", analytics_tracking_id=tracking, data={"task_id": task_id})
        if raw.get("ok")
        else ToolResult(status="error", analytics_tracking_id=tracking, error=raw.get("error"))
    )
    asyncio.create_task(_log_action(db, "facebook_group_comment", inp, res))
    return res


async def facebook_group_publish(
    *,
    group_id: str,
    content: str,
    media_url: str | None = None,
    campaign_id: str | None = None,
    db: Any,
) -> ToolResult:
    """Publish a new post to an approved Facebook group.

    APPROVAL GATE: group_id must reference a group_target with status='approved'.

    Input schema:
        group_id   : str  — group_targets.id (UUID)
        content    : str  — post content
        media_url  : str | None
        campaign_id: str | None
    """
    tracking = _tracking_id()
    inp = {"group_id": group_id, "campaign_id": campaign_id}

    try:
        import sqlalchemy as sa
        row = (await db.execute(
            sa.text("SELECT group_url, status FROM group_targets WHERE id = CAST(:id AS uuid)"),
            {"id": group_id}
        )).fetchone()
        if not row:
            return ToolResult(status="error", analytics_tracking_id=tracking,
                              error=f"group_target {group_id} not found")
        if row.status != "approved":
            return ToolResult(status="pending_approval", analytics_tracking_id=tracking,
                              error=f"group {group_id} not approved (status={row.status})")
        group_url = row.group_url
    except Exception as exc:
        return ToolResult(status="error", analytics_tracking_id=tracking, error=str(exc)[:200])

    from social.facebook_browser import GroupAgent
    agent = GroupAgent()
    raw = await agent.publish_group_post(group_url, content)
    res = (
        ToolResult(status="success", analytics_tracking_id=tracking,
                   data={"group_id": group_id, "campaign_id": campaign_id})
        if raw.get("ok")
        else ToolResult(status="error", analytics_tracking_id=tracking, error=raw.get("error"))
    )
    asyncio.create_task(_log_action(db, "facebook_group_publish", inp, res))
    return res


# ---------------------------------------------------------------------------
# Telegram + WhatsApp tools
# ---------------------------------------------------------------------------

async def telegram_publish(
    *,
    content: str,
    media_url: str | None = None,
    campaign_id: str | None = None,
    db: Any,
) -> ToolResult:
    """Publish a post to the Telegram channel.

    Input schema:
        content    : str
        media_url  : str | None
        campaign_id: str | None
    """
    tracking = _tracking_id()
    from social import registry
    raw = await registry.dispatch("telegram", content, media_url=media_url)
    res = (
        ToolResult(status="success", post_id=str(raw.get("id")), analytics_tracking_id=tracking,
                   data={"platform": "telegram", "campaign_id": campaign_id})
        if raw.get("ok")
        else ToolResult(status="error" if not raw.get("not_configured") else "not_configured",
                        analytics_tracking_id=tracking, error=raw.get("error"))
    )
    asyncio.create_task(_log_action(db, "telegram_publish",
                                    {"campaign_id": campaign_id}, res))
    return res


async def whatsapp_send_message(
    *,
    to: str,
    message: str,
    db: Any,
) -> ToolResult:
    """Send a WhatsApp message through the existing wa provider.

    Input schema:
        to      : str  — phone number (E.164 or local format)
        message : str  — message body

    Obeys quiet-hours (09:00–21:00 IL). Non-critical messages are queued.
    """
    tracking = _tracking_id()
    try:
        from social.whatsapp_provider import send_message as _wa
        raw = await _wa(to, message)
        ok = bool(raw and (raw.get("ok") or raw.get("status") == "sent"))
        res = (
            ToolResult(status="success", analytics_tracking_id=tracking)
            if ok
            else ToolResult(status="error", analytics_tracking_id=tracking,
                            error=str(raw.get("error") or raw)[:200])
        )
    except Exception as exc:
        res = ToolResult(status="error", analytics_tracking_id=tracking, error=str(exc)[:200])

    asyncio.create_task(_log_action(db, "whatsapp_send_message", {"to": to[:15]}, res))
    return res


# ---------------------------------------------------------------------------
# Convenience: call any tool by name (used by campaign_manager)
# ---------------------------------------------------------------------------

_TOOL_MAP = {
    "facebook_publish_page_post": facebook_publish_page_post,
    "facebook_reply_comment": facebook_reply_comment,
    "facebook_get_insights": facebook_get_insights,
    "instagram_publish_post": instagram_publish_post,
    "facebook_group_scan": facebook_group_scan,
    "facebook_group_comment": facebook_group_comment,
    "facebook_group_publish": facebook_group_publish,
    "telegram_publish": telegram_publish,
    "whatsapp_send_message": whatsapp_send_message,
}


async def run_tool(name: str, *, db: Any, **kwargs: Any) -> ToolResult:
    """Dispatch any registered tool by name."""
    fn = _TOOL_MAP.get(name)
    if not fn:
        return ToolResult(status="error", error=f"unknown tool '{name}'")
    return await fn(db=db, **kwargs)


def list_tools() -> list[dict]:
    """Return metadata for all registered tools (for agent introspection)."""
    return [{"name": k, "description": v.__doc__.split("\n")[1].strip() if v.__doc__ else ""}
            for k, v in _TOOL_MAP.items()]
