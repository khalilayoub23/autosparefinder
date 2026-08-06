"""social campaign tables: campaigns, group_targets, engagement_events, platform_accounts, analytics_reports

Revision ID: 0055_social_campaign_tables
Revises: 0054_alias_review_queue
Create Date: 2026-08-06

Purpose:
  Adds the missing Social Media Campaign Infrastructure tables that support
  structured campaign lifecycle, Facebook group targeting, per-post engagement
  tracking, platform credential health, and periodic analytics snapshots.
  All tables live in the catalog (autospare_catalog) DB.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY

revision = "0055_social_campaign_tables"
down_revision = "0054_alias_review_queue"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── campaigns ────────────────────────────────────────────────────────────
    op.create_table(
        "campaigns",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("goal", sa.Text, nullable=True),
        sa.Column("platforms", ARRAY(sa.String), nullable=False, server_default="{}"),
        sa.Column("target_audience", sa.Text, nullable=True),
        sa.Column("tone", sa.String(50), nullable=True, server_default="professional"),
        sa.Column("duration_days", sa.Integer, nullable=True),
        sa.Column("budget_ils", sa.Float, nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("plan", JSONB, nullable=False, server_default="{}"),
        sa.Column("linked_post_ids", ARRAY(sa.String), nullable=False, server_default="{}"),
        sa.Column("total_reach", sa.Integer, nullable=False, server_default="0"),
        sa.Column("total_impressions", sa.Integer, nullable=False, server_default="0"),
        sa.Column("total_engagement", sa.Integer, nullable=False, server_default="0"),
        sa.Column("total_clicks", sa.Integer, nullable=False, server_default="0"),
        sa.Column("total_leads", sa.Integer, nullable=False, server_default="0"),
        sa.Column("performance_score", sa.Float, nullable=True),
        sa.Column("created_by", UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime, nullable=True),
        sa.CheckConstraint(
            "status IN ('draft','active','paused','completed','archived')",
            name="ck_campaigns_status",
        ),
    )
    op.create_index("ix_campaigns_status_created", "campaigns", ["status", "created_at"])

    # ── group_targets ─────────────────────────────────────────────────────────
    op.create_table(
        "group_targets",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("platform", sa.String(30), nullable=False, server_default="facebook"),
        sa.Column("group_name", sa.String(255), nullable=False),
        sa.Column("group_url", sa.String(500), nullable=False),
        sa.Column("group_id", sa.String(100), nullable=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("member_count_estimate", sa.Integer, nullable=True),
        sa.Column("relevance_tags", ARRAY(sa.String), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("posts_sent", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_posted_at", sa.DateTime, nullable=True),
        sa.Column("avg_leads_per_post", sa.Float, nullable=True),
        sa.Column("approved_by", UUID(as_uuid=True), nullable=True),
        sa.Column("approved_at", sa.DateTime, nullable=True),
        sa.Column("rejection_reason", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "status IN ('pending','approved','rejected','paused')",
            name="ck_group_targets_status",
        ),
    )
    op.create_index("ix_group_targets_platform_status", "group_targets", ["platform", "status"])

    # ── engagement_events ─────────────────────────────────────────────────────
    op.create_table(
        "engagement_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("post_id", UUID(as_uuid=True), nullable=False),
        sa.Column("campaign_id", UUID(as_uuid=True), nullable=True),
        sa.Column("platform", sa.String(30), nullable=False),
        sa.Column("external_post_id", sa.String(200), nullable=True),
        sa.Column("likes", sa.Integer, nullable=True),
        sa.Column("comments", sa.Integer, nullable=True),
        sa.Column("shares", sa.Integer, nullable=True),
        sa.Column("reach", sa.Integer, nullable=True),
        sa.Column("impressions", sa.Integer, nullable=True),
        sa.Column("clicks", sa.Integer, nullable=True),
        sa.Column("saves", sa.Integer, nullable=True),
        sa.Column("leads", sa.Integer, nullable=True),
        sa.Column("sentiment_score", sa.Float, nullable=True),
        sa.Column("sentiment_summary", sa.Text, nullable=True),
        sa.Column("data_source", sa.String(50), nullable=True),
        sa.Column("collected_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_engagement_events_post_id", "engagement_events", ["post_id"])
    op.create_index("ix_engagement_events_platform_ts", "engagement_events", ["platform", "collected_at"])
    op.create_index("ix_engagement_events_campaign_id", "engagement_events", ["campaign_id"])

    # ── platform_accounts ─────────────────────────────────────────────────────
    op.create_table(
        "platform_accounts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("platform", sa.String(30), nullable=False, unique=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="disconnected"),
        sa.Column("account_name", sa.String(255), nullable=True),
        sa.Column("account_id", sa.String(100), nullable=True),
        sa.Column("scopes", ARRAY(sa.String), nullable=False, server_default="{}"),
        sa.Column("token_expires_at", sa.DateTime, nullable=True),
        sa.Column("last_api_call_at", sa.DateTime, nullable=True),
        sa.Column("last_error", sa.Text, nullable=True),
        sa.Column("api_calls_today", sa.Integer, nullable=False, server_default="0"),
        sa.Column("api_calls_reset_at", sa.DateTime, nullable=True),
        sa.Column("capabilities", JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "status IN ('connected','disconnected','token_expired','error')",
            name="ck_platform_accounts_status",
        ),
    )

    # ── analytics_reports ─────────────────────────────────────────────────────
    op.create_table(
        "analytics_reports",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("campaign_id", UUID(as_uuid=True), nullable=True),
        sa.Column("report_type", sa.String(50), nullable=False, server_default="campaign"),
        sa.Column("period_start", sa.DateTime, nullable=False),
        sa.Column("period_end", sa.DateTime, nullable=False),
        sa.Column("platform", sa.String(30), nullable=True),
        sa.Column("total_posts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("total_reach", sa.Integer, nullable=False, server_default="0"),
        sa.Column("total_impressions", sa.Integer, nullable=False, server_default="0"),
        sa.Column("total_engagement", sa.Integer, nullable=False, server_default="0"),
        sa.Column("total_clicks", sa.Integer, nullable=False, server_default="0"),
        sa.Column("total_leads", sa.Integer, nullable=False, server_default="0"),
        sa.Column("engagement_rate", sa.Float, nullable=True),
        sa.Column("click_through_rate", sa.Float, nullable=True),
        sa.Column("cost_per_lead_ils", sa.Float, nullable=True),
        sa.Column("top_performing_post_id", sa.String(200), nullable=True),
        sa.Column("top_performing_platform", sa.String(30), nullable=True),
        sa.Column("top_performing_tone", sa.String(50), nullable=True),
        sa.Column("insights", JSONB, nullable=False, server_default="{}"),
        sa.Column("raw_data", JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_analytics_reports_campaign_period", "analytics_reports",
                    ["campaign_id", "period_start"])
    op.create_index("ix_analytics_reports_created", "analytics_reports", ["created_at"])


def downgrade() -> None:
    op.drop_table("analytics_reports")
    op.drop_table("platform_accounts")
    op.drop_table("engagement_events")
    op.drop_table("group_targets")
    op.drop_table("campaigns")
