"""
Script: social/feedback_analyzer.py
Purpose: Post-publish engagement collection and feedback loop.

         After NOA publishes content, this module:
         1. Polls the Facebook Insights API for reach/engagement on recent posts.
         2. Writes per-poll snapshots to engagement_events.
         3. Updates aggregate counters on the parent campaign.
         4. Generates a weekly analytics_report with LLM-synthesised insights.
         5. Returns a learning digest so NOA's next weekly brief is informed
            by real performance data.

         Called by _social_feedback_loop() in BACKEND_API_ROUTES.py every
         SOCIAL_FEEDBACK_INTERVAL_S seconds (default 21600 = 6h).

Process:
  collect_post_engagement()   — fetch insights for all recent FB page posts
  collect_all_platforms()     — orchestrate across configured platforms
  generate_analytics_report() — aggregate + LLM insight summary for a period
  get_top_performers()        — surface best posts/tones for NOA's brief

Data Imported/Modified:
  engagement_events (INSERT), campaigns (UPDATE perf counters),
  analytics_reports (INSERT), social_posts (READ platform IDs)
Data Sources: Facebook Graph API via social/facebook_pages.py
Last Updated: 2026-08-06
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timedelta
from typing import Any

import sqlalchemy as sa

log = logging.getLogger("feedback_analyzer")

# How many recent social_posts to look back at per collection cycle.
_MAX_POSTS_PER_CYCLE = 50
# Minimum minutes between collecting insights for the same post.
_MIN_RECHECK_MINUTES = 60


# ---------------------------------------------------------------------------
# Main collection entry-point
# ---------------------------------------------------------------------------

async def collect_all_platforms(db: Any) -> dict:
    """Collect engagement from all configured platforms for recent posts.

    Returns a summary dict for logging.
    """
    summary = {"facebook": 0, "instagram": 0, "errors": 0}
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

    log.info(
        "feedback_analyzer: collect_all_platforms done | fb=%d ig=%d errors=%d",
        summary["facebook"], summary["instagram"], summary["errors"]
    )
    return summary


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
            SELECT sp.id, sp.external_post_ids, sp.published_at,
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
            await _insert_engagement_event(
                db,
                post_id=str(row.id),
                platform="facebook",
                external_post_id=fb_post_id,
                campaign_id=_extract_campaign_id(external),
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
# Internal helpers
# ---------------------------------------------------------------------------

def _extract_campaign_id(external_post_ids: dict) -> str | None:
    cid = external_post_ids.get("campaign_id") or external_post_ids.get("__campaign_id__")
    return str(cid) if cid else None
