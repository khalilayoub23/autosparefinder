"""
Script: alembic/versions/0061_noa_ops_audit.py
Purpose: Audit + observability schema for NOA's two-phase operational readiness
         (2026-09-20). group_comment_drafts / social_inbox recorded neither WHO
         approved a reply, nor when it was actually posted, nor how many times it
         failed and why - so failed publications, approval latency and duplicate
         risk were unmeasurable, and a submitted-but-unverified comment silently
         reverted to 'pending_approval' (re-approvable => duplicate public comment).
         Adds the audit columns, a terminal 'failed' state, and noa_scan_runs (one
         row per scan cycle) because scan volume / session failures / duplicate
         prevention events had NO persisted source anywhere (logs only).

Revision ID: 0061_noa_ops_audit
Revises: 0060_drafts_dedup_active
Create Date: 2026-09-20
"""

from alembic import op

revision = "0061_noa_ops_audit"
down_revision = "0060_drafts_dedup_active"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE group_comment_drafts
            ADD COLUMN IF NOT EXISTS posted_at        TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS skipped_at       TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS approved_by      TEXT,
            ADD COLUMN IF NOT EXISTS attempts         INTEGER NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS last_error       TEXT,
            ADD COLUMN IF NOT EXISTS last_attempt_at  TIMESTAMPTZ
    """)
    op.execute("ALTER TABLE group_comment_drafts DROP CONSTRAINT IF EXISTS group_comment_drafts_status_check")
    op.execute("""
        ALTER TABLE group_comment_drafts ADD CONSTRAINT group_comment_drafts_status_check
        CHECK (status IN ('pending_approval','approved','posted','skipped','failed'))
    """)
    # 'failed' keeps its post_url reserved: a comment that was submitted but could not be
    # verified may already be public, so a rescan must never re-draft that post.
    op.execute("DROP INDEX IF EXISTS uq_group_comment_drafts_active_post")
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_group_comment_drafts_active_post
        ON group_comment_drafts (post_url)
        WHERE status IN ('pending_approval', 'approved', 'posted', 'failed')
    """)

    op.execute("""
        ALTER TABLE social_inbox
            ADD COLUMN IF NOT EXISTS approved_by  VARCHAR,
            ADD COLUMN IF NOT EXISTS attempts     INTEGER NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS last_error   TEXT
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS noa_scan_runs (
            id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            source         TEXT NOT NULL,
            started_at     TIMESTAMPTZ NOT NULL,
            finished_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            items_scanned  INTEGER NOT NULL DEFAULT 0,
            relevant       INTEGER NOT NULL DEFAULT 0,
            rejected       INTEGER NOT NULL DEFAULT 0,
            drafted        INTEGER NOT NULL DEFAULT 0,
            duplicates     INTEGER NOT NULL DEFAULT 0,
            failures       INTEGER NOT NULL DEFAULT 0,
            session_failed BOOLEAN NOT NULL DEFAULT FALSE,
            error          TEXT,
            detail         JSONB
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_noa_scan_runs_source_time ON noa_scan_runs (source, finished_at DESC)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS noa_scan_runs")
    op.execute("ALTER TABLE social_inbox DROP COLUMN IF EXISTS approved_by, DROP COLUMN IF EXISTS attempts, DROP COLUMN IF EXISTS last_error")
    op.execute("DROP INDEX IF EXISTS uq_group_comment_drafts_active_post")
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_group_comment_drafts_active_post
        ON group_comment_drafts (post_url) WHERE status IN ('pending_approval', 'approved', 'posted')
    """)
    op.execute("UPDATE group_comment_drafts SET status='skipped' WHERE status='failed'")
    op.execute("ALTER TABLE group_comment_drafts DROP CONSTRAINT IF EXISTS group_comment_drafts_status_check")
    op.execute("""
        ALTER TABLE group_comment_drafts ADD CONSTRAINT group_comment_drafts_status_check
        CHECK (status IN ('pending_approval','approved','posted','skipped'))
    """)
    op.execute("""
        ALTER TABLE group_comment_drafts
            DROP COLUMN IF EXISTS posted_at, DROP COLUMN IF EXISTS skipped_at, DROP COLUMN IF EXISTS approved_by,
            DROP COLUMN IF EXISTS attempts, DROP COLUMN IF EXISTS last_error, DROP COLUMN IF EXISTS last_attempt_at
    """)
