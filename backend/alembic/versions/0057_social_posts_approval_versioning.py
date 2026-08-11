"""
Script: alembic/versions/0057_social_posts_approval_versioning.py
Purpose: Add approval tracking and content versioning columns to social_posts,
         and add a campaign_id FK so campaign posts can be queried directly.

Columns added to social_posts:
  campaign_id     UUID nullable  — links post to its originating campaign
  approved_at     TIMESTAMP      — when the post was approved (NULL = not yet)
  content_version INTEGER        — increments on every edit; invalidates prior approval

Index added:
  ix_social_posts_campaign_id — fast lookup of all posts for a campaign

Revision ID: 0057
Revises: 0056_social_campaign_audit_columns
Create Date: 2026-08-09
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0057_sp_approval_vers"
down_revision = "0056_social_audit_cols"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # campaign_id: nullable UUID, direct FK reference to campaigns table
    op.add_column(
        "social_posts",
        sa.Column("campaign_id", UUID(as_uuid=True), nullable=True),
    )
    # approved_at: timestamp when a human approved this post
    op.add_column(
        "social_posts",
        sa.Column("approved_at", sa.DateTime(), nullable=True),
    )
    # content_version: starts at 1, increments on each edit; approval is invalidated
    # when this bumps, so the publisher always publishes the version the human read
    op.add_column(
        "social_posts",
        sa.Column(
            "content_version",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
    )
    # Index for efficient per-campaign post queries
    op.create_index(
        "ix_social_posts_campaign_id",
        "social_posts",
        ["campaign_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_social_posts_campaign_id", table_name="social_posts")
    op.drop_column("social_posts", "content_version")
    op.drop_column("social_posts", "approved_at")
    op.drop_column("social_posts", "campaign_id")
