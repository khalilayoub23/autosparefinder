"""Add user_id and notified columns to search_misses

Revision ID: 0011_search_misses_user_notified
Revises: 0010_add_search_misses
Create Date: 2026-03-18
"""
from alembic import op

revision = "0011_search_misses_user_notified"
down_revision = "0010_add_search_misses"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE search_misses
            ADD COLUMN IF NOT EXISTS user_id  UUID,
            ADD COLUMN IF NOT EXISTS notified BOOLEAN NOT NULL DEFAULT FALSE
    """)

    # Partial index — only covers rows the notification loop actually queries
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_search_misses_triggered_notified
        ON search_misses (triggered_scrape, notified)
        WHERE triggered_scrape = TRUE AND notified = FALSE
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_search_misses_triggered_notified")
    op.execute("ALTER TABLE search_misses DROP COLUMN IF EXISTS notified")
    op.execute("ALTER TABLE search_misses DROP COLUMN IF EXISTS user_id")
