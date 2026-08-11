"""
Script: alembic/versions/0059_group_comment_drafts.py
Purpose: Add group_comment_drafts table to store NOA's draft comments on
         Facebook group posts, pending owner approval before any comment
         is actually posted.

Revision ID: 0059_group_comment_drafts
Revises: 0058_group_targets_uniq_approved
Create Date: 2026-08-11
"""

from alembic import op
import sqlalchemy as sa

revision = "0059_group_comment_drafts"
down_revision = "0058_group_targets_uniq_approved"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS group_comment_drafts (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            group_target_id UUID REFERENCES group_targets(id) ON DELETE CASCADE,
            post_url        TEXT NOT NULL,
            post_text       TEXT NOT NULL DEFAULT '',
            draft_comment   TEXT NOT NULL DEFAULT '',
            relevance_score FLOAT NOT NULL DEFAULT 0.0,
            status          TEXT NOT NULL DEFAULT 'pending_approval'
                            CHECK (status IN ('pending_approval','approved','posted','skipped')),
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            approved_at     TIMESTAMPTZ
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_group_comment_drafts_status
        ON group_comment_drafts (status, created_at DESC)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_group_comment_drafts_group
        ON group_comment_drafts (group_target_id, created_at DESC)
    """)
    # Dedupe: one pending draft per post URL (prevents scanning the same post twice)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_group_comment_drafts_pending_post
        ON group_comment_drafts (post_url)
        WHERE status = 'pending_approval'
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS group_comment_drafts CASCADE")
