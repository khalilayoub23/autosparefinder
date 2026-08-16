"""
Script: social/feedback_analyzer.py
Purpose: Post-publish engagement collection and feedback loop.

         After NOA publishes content, this module:
         1. Polls the Facebook Insights API for reach/engagement on recent posts.
         2. Writes per-poll snapshots to engagement_events.
         3. Updates aggregate counters on the parent campaign.
         4. Generates a weekly analytics_report with LLM-synthesised insights.
         5. Computes real topic-performance weights that actually bias
            _noa_marketing_loop's next topic choice (compute_topic_performance,
            2026-08-15 — closes the analytics→decision gap: this module used
            to collect and report real metrics but nothing downstream ever
            read them; generate_analytics_report/get_top_performers had zero
            callers outside this file and the admin dashboard routes).

         Called by _social_feedback_loop() in BACKEND_API_ROUTES.py every
         SOCIAL_FEEDBACK_INTERVAL_S seconds (default 21600 = 6h).

         Relationship to digital_department/departments/dept-analytics.md:
         that file is KNOWLEDGE/GUARDRAIL text injected into NOA's prompt for
         social_campaign (KPI framework, Truth-Only rule for reported numbers)
         — it never computes or touches a real number. THIS file is the real,
         deterministic data service: it collects real metrics and computes
         real aggregates. They intentionally stay separate — the skill is a
         "how to think" layer for the LLM; this module is "what the numbers
         actually are," never phrased as prose an LLM has to reason about
         correctly. compute_topic_performance() below is deliberately NOT an
         LLM call (see its own docstring) — the analytics-informed DECISION
         is made in Python before generation, not left for the model to
         infer from injected numbers.

Process:
  collect_post_engagement()    — fetch insights for all recent FB page posts
  collect_all_platforms()      — orchestrate across configured platforms
  generate_analytics_report()  — aggregate + LLM insight summary for a period
  get_top_performers()         — surface best posts/tones for NOA's brief
  compute_topic_performance()  — real, deterministic per-topic engagement
                                  weights consumed by _noa_marketing_loop's
                                  topic selection (BACKEND_API_ROUTES.py)

Data Imported/Modified:
  engagement_events (INSERT), campaigns (UPDATE perf counters),
  analytics_reports (INSERT), social_posts (READ platform IDs / topic)
Data Sources: Facebook Graph API via social/facebook_pages.py
Last Updated: 2026-08-15
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import sqlalchemy as sa

log = logging.getLogger("feedback_analyzer")

# Topic-performance weighting (compute_topic_performance) — tuning constants.
# Below _MIN_SAMPLES_FOR_SIGNAL measured posts, a topic's average engagement
# is noise, not signal — treat it as "no data" rather than a real weight.
_MIN_SAMPLES_FOR_SIGNAL = 3
# Weight is clamped to this range so one lucky/unlucky early post can never
# make a topic permanently dominant or permanently excluded — it can only
# nudge the random choice, never replace it.
_MAX_TOPIC_WEIGHT = 3.0
_MIN_TOPIC_WEIGHT = 0.4

# How many recent social_posts to look back at per collection cycle.
_MAX_POSTS_PER_CYCLE = 50
# Minimum minutes between collecting insights for the same post.
_MIN_RECHECK_MINUTES = 60


# ---------------------------------------------------------------------------
# Main collection entry-point
# ---------------------------------------------------------------------------

async def collect_all_platforms(db: Any) -> dict:
    """Collect engagement from all configured platforms for recent posts.

    Returns a summary dict for logging. Every platform collector is wrapped
    in its own try/except — one platform's failure (API down, not
    configured, credential issue) must never prevent the others from
    running (2026-08-16, organic-channel closure pass: added Discord on the
    same principle already established for Facebook/Instagram here).
    Telegram and TikTok have NO collector — see the module-level note below.
    """
    summary = {"facebook": 0, "instagram": 0, "discord": 0, "tiktok": 0, "errors": 0}
    try:
        fb_collected = await _collect_facebook(db)
        summary["facebook"] = fb_collected
    except Exception as exc:
        log.error("feedback_analyzer: FB collection failed: %s", exc)
        summary["errors"] += 1

    # Instagram shares the same token path as Facebook; same rhythm.
    # (Instagram Insights API is identical in structure to FB's — only the
    # endpoint differs. We collect it the same way once an IG token is live.)
    try:
        ig_collected = await _collect_instagram(db)
        summary["instagram"] = ig_collected
    except Exception as exc:
        log.warning("feedback_analyzer: IG collection skipped: %s", exc)

    try:
        discord_collected = await _collect_discord(db)
        summary["discord"] = discord_collected
    except Exception as exc:
        log.error("feedback_analyzer: Discord collection failed: %s", exc)
        summary["errors"] += 1

    try:
        tiktok_collected = await _collect_tiktok(db)
        summary["tiktok"] = tiktok_collected
    except Exception as exc:
        log.error("feedback_analyzer: TikTok collection failed: %s", exc)
        summary["errors"] += 1

    log.info(
        "feedback_analyzer: collect_all_platforms done | fb=%d ig=%d discord=%d tiktok=%d errors=%d",
        summary["facebook"], summary["instagram"], summary["discord"],
        summary.get("tiktok", 0), summary["errors"]
    )
    return summary


# ---------------------------------------------------------------------------
# Telegram engagement collection — NOT AVAILABLE (2026-08-16 audit)
#
# The Bot API has no read-only "get message stats" call — a channel post's
# view count is only visible via the MTProto client API (Telegram
# Premium/Business Stats), never exposed to bots. Reaction counts
# (message_reaction_count) require the bot's webhook to subscribe to that
# update type; the current LIVE registration (telegram_publisher.py's
# setWebhook, allowed_updates=["message","edited_message"]) does not, and
# changing a live webhook's allowed_updates is a production webhook-
# infrastructure change outside this pass's safe scope — flagged, not
# implemented. Publishing (telegram_publisher.py) is unaffected.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# TikTok engagement collection (2026-08-16 — deep capability verification)
#
# Gated on tiktok_publisher.get_valid_user_access_token(): returns None
# until the owner completes the real OAuth consent (see owner-console
# "טוקן טיקטוק" — the URL-generation + token-persistence infrastructure was
# fixed this pass; the human consent click itself cannot be automated).
# Live-confirmed against the real API: the app-level client_credentials
# token this app already uses for PUBLISHING is REJECTED with HTTP 401
# access_token_invalid on the analytics endpoint — this is not assumed,
# it was tested. Once a user token exists, this resolves each post's stored
# publish_id -> real video_id (fetch_publish_status) -> real metrics
# (fetch_video_metrics), exactly mirroring _collect_facebook's shape.
# ---------------------------------------------------------------------------

async def _collect_tiktok(db: Any) -> int:
    """Poll TikTok video metrics for recently published posts. Returns 0
    (not an error) until the owner completes the one-time OAuth consent —
    same graceful-stub contract as _collect_instagram above."""
    from social.tiktok_publisher import get_valid_user_access_token, fetch_publish_status, fetch_video_metrics

    access_token = await get_valid_user_access_token(db)
    if not access_token:
        return 0

    rows = (await db.execute(
        sa.text("""
            SELECT sp.id, sp.external_post_ids, sp.published_at, sp.campaign_id,
                   (SELECT MAX(ee.collected_at)
                    FROM engagement_events ee
                    WHERE ee.post_id = sp.id AND ee.platform = 'tiktok') AS last_collected
            FROM social_posts sp
            WHERE sp.status = 'published'
              AND sp.published_at > NOW() - INTERVAL '30 days'
              AND 'tiktok' = ANY(sp.platforms)
            LIMIT :lim
        """),
        {"lim": _MAX_POSTS_PER_CYCLE},
    )).fetchall()

    collected = 0
    for row in rows:
        if row.last_collected:
            age_min = (datetime.utcnow() - row.last_collected).total_seconds() / 60
            if age_min < _MIN_RECHECK_MINUTES:
                continue

        external = row.external_post_ids or {}
        publish_id = external.get("tiktok")
        if not publish_id:
            continue

        try:
            status = await fetch_publish_status(publish_id, access_token)
            # publicly_available_post_id is a LIST per TikTok's documented
            # response shape (a publish can map to >1 post id in rare cases);
            # take the first for a simple single-video campaign post.
            video_ids = (status or {}).get("publicly_available_post_id") or []
            video_id = video_ids[0] if isinstance(video_ids, list) and video_ids else (
                video_ids if isinstance(video_ids, str) else (status or {}).get("video_id")
            )
            if not video_id:
                continue  # still processing, or not published to a video_id yet
            metrics_by_id = await fetch_video_metrics([video_id], access_token)
            m = metrics_by_id.get(video_id)
            if not m:
                continue
            await _insert_engagement_event(
                db,
                post_id=str(row.id),
                platform="tiktok",
                external_post_id=str(publish_id),
                campaign_id=str(row.campaign_id) if row.campaign_id else None,
                likes=int(m.get("like_count") or 0),
                comments=int(m.get("comment_count") or 0),
                shares=int(m.get("share_count") or 0),
                reach=int(m.get("view_count") or 0),
                impressions=int(m.get("view_count") or 0),
            )
            collected += 1
        except Exception as exc:
            log.warning("feedback_analyzer: TikTok post %s metrics failed: %s", publish_id, exc)

    return collected


# ---------------------------------------------------------------------------
# Facebook engagement collection
# ---------------------------------------------------------------------------

async def _collect_facebook(db: Any) -> int:
    """Poll FB Page Insights for recently published posts. Returns count collected."""
    from social.meta_client import facebook_configured
    if not facebook_configured():
        return 0

    # Fetch social_posts published to facebook in the last 30 days
    rows = (await db.execute(
        sa.text("""
            SELECT sp.id, sp.external_post_ids, sp.published_at, sp.campaign_id,
                   (SELECT MAX(ee.collected_at)
                    FROM engagement_events ee
                    WHERE ee.post_id = sp.id AND ee.platform = 'facebook') AS last_collected
            FROM social_posts sp
            WHERE sp.status = 'published'
              AND sp.published_at > NOW() - INTERVAL '30 days'
              AND 'facebook' = ANY(sp.platforms)
            LIMIT :lim
        """),
        {"lim": _MAX_POSTS_PER_CYCLE},
    )).fetchall()

    collected = 0
    for row in rows:
        # Skip if we collected less than _MIN_RECHECK_MINUTES ago
        if row.last_collected:
            age_min = (datetime.utcnow() - row.last_collected).total_seconds() / 60
            if age_min < _MIN_RECHECK_MINUTES:
                continue

        external = row.external_post_ids or {}
        fb_post_id = external.get("facebook") or external.get("facebook_page")
        if not fb_post_id:
            continue

        try:
            metrics = await _fetch_fb_post_metrics(fb_post_id)
            if not metrics:
                continue
            # Campaign attribution fix (2026-08-16, operational-loop audit):
            # was _extract_campaign_id(external) — parsed a "__campaign_id__"
            # key out of external_post_ids, but the owner-console approve
            # path (_approve_and_publish, agents/owner_console.py) overwrites
            # external_post_ids wholesale on publish (no JSONB merge),
            # destroying that key for every post approved through the real
            # "אשר <id>" WhatsApp command — the actual primary approval path.
            # social_posts.campaign_id is the authoritative FK, set once at
            # creation (campaign_manager.prepare_campaign_content) and never
            # touched by any publish path; reading it directly is both the
            # root fix and strictly more robust than depending on a JSONB
            # key surviving an unrelated UPDATE.
            await _insert_engagement_event(
                db,
                post_id=str(row.id),
                platform="facebook",
                external_post_id=fb_post_id,
                campaign_id=str(row.campaign_id) if row.campaign_id else None,
                **metrics,
            )
            collected += 1
            await asyncio.sleep(0.5)  # gentle inter-request pause
        except Exception as exc:
            log.warning("feedback_analyzer: FB post %s metrics failed: %s", fb_post_id, exc)

    return collected


async def _fetch_fb_post_metrics(fb_post_id: str) -> dict | None:
    """Call the Graph Insights endpoint; return standardised metrics dict."""
    from social.facebook_pages import get_post_insights
    raw = await get_post_insights(fb_post_id)
    if not raw.get("ok"):
        return None
    return {
        "likes": raw.get("likes", 0),
        "reach": raw.get("reach", 0),
        "impressions": raw.get("impressions", 0),
        "clicks": raw.get("clicks", 0),
        "engaged_users": raw.get("engaged_users", 0),
    }


# ---------------------------------------------------------------------------
# Discord engagement collection (2026-08-16 — organic-channel closure pass)
#
# Reuses the EXISTING DISCORD_BOT_TOKEN + DISCORD_ENGAGE_CHANNELS (already
# live for Community Engagement's discord_fetch_new in social/engagement.py
# — same bot, same channel, same permission level already proven working).
# No new credential, scope, or webhook. Discord's REST API returns a
# `reactions` array on the message object for a plain
# GET /channels/{channel}/messages/{message_id} call — a standard read using
# the permission the bot already has (it already lists messages in this
# exact channel for the inbox). Reaction count is mapped to the existing
# `likes` field (closest semantic match, same convention used for FB likes).
# ---------------------------------------------------------------------------

async def _collect_discord(db: Any) -> int:
    """Poll Discord message reactions for recently published posts. Returns
    count collected. discord_publisher.py posts via a webhook into the same
    channel social/engagement.py's bot already reads (DISCORD_ENGAGE_CHANNELS)
    — verified 2026-08-16, not assumed."""
    from social.engagement import discord_configured, _discord_channels, _discord_headers, DISCORD_API, _http_json

    if not discord_configured():
        return 0

    rows = (await db.execute(
        sa.text("""
            SELECT sp.id, sp.external_post_ids, sp.published_at, sp.campaign_id,
                   (SELECT MAX(ee.collected_at)
                    FROM engagement_events ee
                    WHERE ee.post_id = sp.id AND ee.platform = 'discord') AS last_collected
            FROM social_posts sp
            WHERE sp.status = 'published'
              AND sp.published_at > NOW() - INTERVAL '30 days'
              AND 'discord' = ANY(sp.platforms)
            LIMIT :lim
        """),
        {"lim": _MAX_POSTS_PER_CYCLE},
    )).fetchall()

    collected = 0
    channels = _discord_channels()
    for row in rows:
        if row.last_collected:
            age_min = (datetime.utcnow() - row.last_collected).total_seconds() / 60
            if age_min < _MIN_RECHECK_MINUTES:
                continue

        external = row.external_post_ids or {}
        message_id = external.get("discord")
        if not message_id or message_id == "group_post_no_id":
            continue

        message = None
        for channel_id in channels:
            resp = _http_json(
                f"{DISCORD_API}/channels/{channel_id}/messages/{message_id}",
                headers=_discord_headers(),
            )
            if not resp.get("error"):
                message = resp
                break
        if message is None:
            continue

        try:
            reactions = message.get("reactions") or []
            reaction_total = sum(int(r.get("count") or 0) for r in reactions)
            await _insert_engagement_event(
                db,
                post_id=str(row.id),
                platform="discord",
                external_post_id=str(message_id),
                campaign_id=str(row.campaign_id) if row.campaign_id else None,
                likes=reaction_total,
            )
            collected += 1
        except Exception as exc:
            log.warning("feedback_analyzer: Discord post %s metrics failed: %s", message_id, exc)

    return collected


# ---------------------------------------------------------------------------
# Instagram engagement collection (stub — activates when token is live)
# ---------------------------------------------------------------------------

async def _collect_instagram(db: Any) -> int:
    """Collect IG engagement metrics. Returns 0 if not configured."""
    import os
    if not (os.getenv("INSTAGRAM_USER_ID", "").strip() and
            os.getenv("INSTAGRAM_ACCESS_TOKEN", "").strip()):
        return 0
    # Instagram Insights API: GET /{media_id}/insights?metric=...
    # Identical approach to Facebook; deferred until token lands.
    log.debug("feedback_analyzer: Instagram collection deferred (token not yet set)")
    return 0


# ---------------------------------------------------------------------------
# Engagement event writer
# ---------------------------------------------------------------------------

async def _insert_engagement_event(
    db: Any,
    *,
    post_id: str,
    platform: str,
    external_post_id: str,
    campaign_id: str | None = None,
    likes: int = 0,
    reach: int = 0,
    impressions: int = 0,
    clicks: int = 0,
    comments: int = 0,
    shares: int = 0,
    saves: int = 0,
    engaged_users: int = 0,
    sentiment_score: float | None = None,
    sentiment_summary: str | None = None,
) -> str:
    """Insert one engagement snapshot. Returns the new event UUID."""
    eid = str(uuid.uuid4())
    now = datetime.utcnow()
    engagement = likes + comments + shares + saves
    await db.execute(
        sa.text("""
            INSERT INTO engagement_events
                (id, post_id, campaign_id, platform, external_post_id,
                 likes, comments, shares, reach, impressions, clicks, saves, leads,
                 sentiment_score, sentiment_summary,
                 data_source, collected_at, created_at)
            VALUES
                (:id, CAST(:post_id AS uuid), :campaign_id, :platform, :ext_id,
                 :likes, :comments, :shares, :reach, :impr, :clicks, :saves, NULL,
                 :sentiment, :sent_sum,
                 'graph_api', :now, :now)
        """),
        {
            "id": eid,
            "post_id": post_id,
            "campaign_id": campaign_id,
            "platform": platform,
            "ext_id": external_post_id,
            "likes": likes,
            "comments": comments,
            "shares": shares,
            "reach": reach,
            "impr": impressions,
            "clicks": clicks,
            "saves": saves,
            "sentiment": sentiment_score,
            "sent_sum": sentiment_summary,
            "now": now,
        },
    )

    # Update campaign aggregate if linked
    if campaign_id:
        await db.execute(
            sa.text("""
                UPDATE campaigns SET
                    total_reach       = total_reach + :r,
                    total_impressions = total_impressions + :i,
                    total_engagement  = total_engagement + :e,
                    total_clicks      = total_clicks + :c,
                    updated_at        = :now
                WHERE id = CAST(:cid AS uuid)
            """),
            {"r": reach, "i": impressions, "e": engagement, "c": clicks,
             "cid": campaign_id, "now": now},
        )

    await db.commit()
    return eid


# ---------------------------------------------------------------------------
# Analytics report generation
# ---------------------------------------------------------------------------

async def generate_analytics_report(
    db: Any,
    *,
    period_days: int = 7,
    campaign_id: str | None = None,
    platform: str | None = None,
) -> dict:
    """Generate and persist an analytics report for a time window.

    Aggregates engagement_events and generates LLM insights via hf_text_fast.
    Returns the report dict (also written to analytics_reports).
    """
    now = datetime.utcnow()
    period_start = now - timedelta(days=period_days)

    # Build query
    where_parts = ["collected_at >= :since"]
    params: dict = {"since": period_start, "now": now}
    if campaign_id:
        where_parts.append("campaign_id = CAST(:cid AS uuid)")
        params["cid"] = campaign_id
    if platform:
        where_parts.append("platform = :platform")
        params["platform"] = platform
    where = "WHERE " + " AND ".join(where_parts)

    agg = (await db.execute(
        sa.text(f"""
            SELECT
                COUNT(DISTINCT post_id) AS total_posts,
                COALESCE(SUM(reach), 0) AS total_reach,
                COALESCE(SUM(impressions), 0) AS total_impressions,
                COALESCE(SUM(likes + COALESCE(comments,0) + COALESCE(shares,0)), 0) AS total_engagement,
                COALESCE(SUM(clicks), 0) AS total_clicks,
                COALESCE(SUM(COALESCE(leads,0)), 0) AS total_leads,
                AVG(sentiment_score) AS avg_sentiment
            FROM engagement_events {where}
        """),
        params,
    )).fetchone()

    # Top post
    top_row = (await db.execute(
        sa.text(f"""
            SELECT external_post_id, platform,
                   (COALESCE(likes,0) + COALESCE(comments,0) + COALESCE(shares,0)) AS eng
            FROM engagement_events {where}
            ORDER BY eng DESC
            LIMIT 1
        """),
        params,
    )).fetchone()

    total_reach = int(agg.total_reach or 0)
    total_engagement = int(agg.total_engagement or 0)
    engagement_rate = round(total_engagement / total_reach, 4) if total_reach else 0.0
    total_clicks = int(agg.total_clicks or 0)
    total_leads = int(agg.total_leads or 0)
    ctr = round(total_clicks / max(total_reach, 1), 4)

    raw_data = {
        "period_days": period_days,
        "total_posts": int(agg.total_posts or 0),
        "total_reach": total_reach,
        "total_impressions": int(agg.total_impressions or 0),
        "total_engagement": total_engagement,
        "total_clicks": total_clicks,
        "total_leads": total_leads,
        "avg_sentiment": round(float(agg.avg_sentiment or 0), 3),
        "engagement_rate": engagement_rate,
        "click_through_rate": ctr,
    }

    # LLM insight synthesis
    insights = await _synthesise_insights(raw_data, period_days=period_days)

    # Persist report
    rid = str(uuid.uuid4())
    await db.execute(
        sa.text("""
            INSERT INTO analytics_reports
                (id, campaign_id, report_type, period_start, period_end, platform,
                 total_posts, total_reach, total_impressions, total_engagement,
                 total_clicks, total_leads, engagement_rate, click_through_rate,
                 top_performing_post_id, top_performing_platform,
                 insights, raw_data, created_at)
            VALUES
                (:id, :cid, :type, :ps, :pe, :platform,
                 :tp, :tr, :ti, :te, :tc, :tl, :er, :ctr,
                 :top_post, :top_plat,
                 CAST(:insights AS jsonb), CAST(:raw AS jsonb), :now)
        """),
        {
            "id": rid,
            "cid": campaign_id,
            "type": "campaign" if campaign_id else "platform",
            "ps": period_start,
            "pe": now,
            "platform": platform,
            "tp": raw_data["total_posts"],
            "tr": total_reach,
            "ti": raw_data["total_impressions"],
            "te": total_engagement,
            "tc": total_clicks,
            "tl": total_leads,
            "er": engagement_rate,
            "ctr": ctr,
            "top_post": top_row.external_post_id if top_row else None,
            "top_plat": top_row.platform if top_row else None,
            "insights": json.dumps(insights, ensure_ascii=False),
            "raw": json.dumps(raw_data, ensure_ascii=False),
            "now": now,
        },
    )
    await db.commit()

    log.info(
        "feedback_analyzer: analytics_report %s | reach=%d eng=%d leads=%d",
        rid, total_reach, total_engagement, total_leads
    )
    return {"report_id": rid, "raw_data": raw_data, "insights": insights}


async def _synthesise_insights(raw_data: dict, *, period_days: int) -> dict:
    """Call hf_text_fast to generate human-readable insights from metrics."""
    from hf_client import hf_text_fast

    prompt = (
        f"You are AutoSpareFinder's marketing analyst. "
        f"Here are social media metrics for the past {period_days} days:\n\n"
        f"{json.dumps(raw_data, indent=2)}\n\n"
        f"In JSON, provide:\n"
        f"1. best_times: list of recommended posting times (e.g. ['Sun 09:00 IL', 'Wed 18:00 IL'])\n"
        f"2. top_content_types: list of content types that work (e.g. ['how-to', 'price comparison'])\n"
        f"3. recommendations: 3 specific, actionable improvements for next week\n"
        f"4. summary: one sentence plain-text summary for the owner\n"
        f"Respond with ONLY valid JSON. No prose."
    )
    try:
        raw = await hf_text_fast(prompt, timeout=45.0)
        # Parse the first valid JSON object in the response.
        # We scan from every '{' position so prose before/after the object is ignored.
        # This is more robust than a greedy regex which captures text between the
        # first '{' and the LAST '}' (often invalid JSON when prose follows the object).
        for i, ch in enumerate(raw):
            if ch == "{":
                try:
                    obj, _ = json.JSONDecoder().raw_decode(raw[i:])
                    if isinstance(obj, dict):
                        return obj
                except (json.JSONDecodeError, ValueError):
                    continue
    except Exception as exc:
        log.warning("feedback_analyzer: LLM insights failed: %s", exc)
    return {
        "best_times": ["Sun 09:00 IL", "Wed 18:00 IL"],
        "top_content_types": [],
        "recommendations": ["Collect more data before making recommendations."],
        "summary": f"No significant trends yet ({raw_data.get('total_posts', 0)} posts tracked).",
    }


# ---------------------------------------------------------------------------
# Learning digest for NOA's weekly brief
# ---------------------------------------------------------------------------

async def get_top_performers(db: Any, *, days: int = 14, limit: int = 5) -> list[dict]:
    """Return the top performing posts from the last N days for NOA's brief."""
    rows = (await db.execute(
        sa.text("""
            SELECT ee.post_id, ee.platform, ee.external_post_id,
                   ee.reach, ee.impressions,
                   (COALESCE(ee.likes,0) + COALESCE(ee.comments,0) + COALESCE(ee.shares,0)) AS eng,
                   sp.content
            FROM engagement_events ee
            LEFT JOIN social_posts sp ON sp.id = ee.post_id
            WHERE ee.collected_at > NOW() - INTERVAL ':d days'
            ORDER BY eng DESC
            LIMIT :lim
        """.replace(":d days", f"{days} days")),
        {"lim": limit},
    )).fetchall()
    return [
        {
            "post_id": str(r.post_id),
            "platform": r.platform,
            "external_post_id": r.external_post_id,
            "reach": r.reach,
            "impressions": r.impressions,
            "engagement": r.eng,
            "content_snippet": (r.content or "")[:100],
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Topic performance → generation weighting (the analytics→decision link)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TopicPerformance:
    """One topic's real historical engagement signal — a typed, machine-usable
    contract, not prose. Produced by compute_topic_performance(), consumed by
    _noa_marketing_loop's topic selection (BACKEND_API_ROUTES.py). Never
    rendered into an LLM prompt: the weighting decision happens in Python
    BEFORE generation — the model only ever sees the already-chosen topic,
    exactly as it did before this existed. This keeps the decision
    deterministic and auditable instead of asking an LLM to correctly weigh
    numbers inside a prompt.
    """
    topic: str            # the heb_part dictionary key this signal is about
    sample_size: int      # how many real measured posts this is based on
    avg_engagement: float # real avg(likes+comments+shares) per measured post
    weight: float          # relative multiplier, clamped to
                           # [_MIN_TOPIC_WEIGHT, _MAX_TOPIC_WEIGHT]


async def compute_topic_performance(db: Any, *, days: int = 30) -> dict[str, TopicPerformance]:
    """Real, deterministic (NO LLM call) aggregation of engagement by topic.

    Topic is the heb_part prefix of social_posts.external_post_ids->>'topic'
    (format "{heb_part} — {car}", set only by _noa_marketing_loop's
    _noa_enqueue_social_post call — BACKEND_API_ROUTES.py:3768-3772 — which is
    the only writer of that field shape; admin/campaign-created posts use a
    different insert path entirely and are correctly excluded via the
    source='noa_marketing_loop' filter below).

    Returns {} whenever there is not enough real data to say anything —
    callers MUST treat a missing topic as "no signal, use a neutral weight
    of 1.0", never as "this topic performs badly". This mirrors the existing
    fail-open pattern already used by _synthesise_insights() below.

    ONE ROW PER POST, NOT PER SNAPSHOT (2026-08-15b acceptance-verification
    fix): _fetch_fb_post_metrics() requests Facebook's period="lifetime"
    (cumulative) insights (facebook_pages.py:163), and _collect_facebook()
    re-collects the same post every ~6h for up to 30 days. Averaging raw
    engagement_events rows would let a post that happened to be re-measured
    many times dominate a topic's score purely by snapshot COUNT, unrelated
    to its actual engagement quality — a real measurement bias, not a
    theoretical one, since collection frequency varies per post (batch cap,
    publish age) and has nothing to do with how good the post is. Fixed via
    DISTINCT ON: each post contributes exactly ONE sample (its most recent
    cumulative snapshot in the window), so sample_size and avg_engagement
    both mean "N independent posts," never "N snapshots of fewer posts."
    """
    since = datetime.utcnow() - timedelta(days=days)
    try:
        rows = (await db.execute(
            sa.text("""
                WITH latest_snapshot AS (
                    SELECT DISTINCT ON (ee.post_id)
                        ee.post_id,
                        ee.likes, ee.comments, ee.shares,
                        sp.external_post_ids->>'topic' AS topic_full
                    FROM engagement_events ee
                    JOIN social_posts sp ON sp.id = ee.post_id
                    WHERE ee.collected_at >= :since
                      AND sp.external_post_ids->>'topic' IS NOT NULL
                      AND sp.external_post_ids->>'source' = 'noa_marketing_loop'
                    ORDER BY ee.post_id, ee.collected_at DESC
                )
                SELECT
                    split_part(topic_full, ' — ', 1) AS topic,
                    COUNT(*) AS sample_size,
                    AVG(COALESCE(likes,0) + COALESCE(comments,0) + COALESCE(shares,0))
                        AS avg_engagement
                FROM latest_snapshot
                GROUP BY topic
            """),
            {"since": since},
        )).fetchall()
    except Exception as exc:
        # Fail open — a broken query must never block topic selection.
        log.warning("feedback_analyzer: compute_topic_performance query failed: %s", exc)
        return {}

    scored = [r for r in rows if r.topic and r.sample_size >= _MIN_SAMPLES_FOR_SIGNAL]
    if not scored:
        return {}

    pool_avg = sum(float(r.avg_engagement or 0) for r in scored) / len(scored)
    if pool_avg <= 0:
        return {}

    out: dict[str, TopicPerformance] = {}
    for r in scored:
        eng = float(r.avg_engagement or 0)
        raw_weight = eng / pool_avg
        weight = max(_MIN_TOPIC_WEIGHT, min(_MAX_TOPIC_WEIGHT, raw_weight))
        out[r.topic] = TopicPerformance(
            topic=r.topic,
            sample_size=int(r.sample_size),
            avg_engagement=eng,
            weight=weight,
        )
    log.info(
        "feedback_analyzer: compute_topic_performance | %d topics with signal (>=%d samples), pool_avg=%.1f",
        len(out), _MIN_SAMPLES_FOR_SIGNAL, pool_avg,
    )
    return out
