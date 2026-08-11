"""
Script: alembic/versions/0058_group_targets_unique_approved.py
Purpose: Add a partial unique index on group_targets (platform, group_url)
         filtered to status = 'approved', so only one approved entry can
         exist per real group. Pending/test rows are unaffected.

Why partial (not full unique):
  A full unique constraint would block having a pending test record alongside
  an approved production record for the same URL, which breaks test workflows.
  The invariant we enforce is: a group can only be in the approved production
  pool once. Multiple pending rows for the same URL are harmless.

Revision ID: 0058
Revises: 0057_sp_approval_vers
Create Date: 2026-08-11
"""

from alembic import op

revision = "0058_group_targets_uniq_approved"
down_revision = "0057_sp_approval_vers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Partial unique index: enforces at most one approved row per (platform, group_url).
    # postgresql-specific WHERE clause — acceptable since the stack is Postgres-only.
    op.execute("""
        CREATE UNIQUE INDEX uq_group_targets_platform_url_approved
        ON group_targets (platform, group_url)
        WHERE status = 'approved'
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_group_targets_platform_url_approved")
