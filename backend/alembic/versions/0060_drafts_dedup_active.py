"""
Script: alembic/versions/0060_drafts_dedup_active.py
Purpose: Close a real duplicate-action gap found during the "close NOA
         Facebook post handling" investigation (2026-09-19). The original
         0059 unique index only covered post_url WHILE status='pending_approval',
         so once a draft moved to 'approved' or 'posted', its post_url became
         free again — a later rescan of the SAME still-visible Facebook post
         could insert a BRAND NEW 'pending_approval' draft asking the owner
         to approve a second comment on a post NOA had already commented on.
         The approval gate still requires an explicit owner action either
         way, so this was never an automatic duplicate post, but an owner
         who doesn't recognise the post could approve a genuine duplicate
         public comment. Root fix: widen the partial unique index to also
         cover 'approved' and 'posted' so a rescan can never re-draft a post
         that already has a live or in-flight comment. 'skipped' is
         deliberately left out — the owner explicitly declined that draft,
         and NOA may legitimately propose a different comment later.

Revision ID: 0060_drafts_dedup_active
Revises: 0059_group_comment_drafts
Create Date: 2026-09-19
"""

from alembic import op

revision = "0060_drafts_dedup_active"
down_revision = "0059_group_comment_drafts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_group_comment_drafts_pending_post")
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_group_comment_drafts_active_post
        ON group_comment_drafts (post_url)
        WHERE status IN ('pending_approval', 'approved', 'posted')
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_group_comment_drafts_active_post")
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_group_comment_drafts_pending_post
        ON group_comment_drafts (post_url)
        WHERE status = 'pending_approval'
    """)
