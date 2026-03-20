"""Add missing indexes to autospare_pii.

Revision ID: 0016_missing_indexes
Revises: 0015_add_wishlist_reviews
Create Date: 2026-03-20
"""
from alembic import op

revision = "0016_missing_indexes"
down_revision = "0015_add_wishlist_reviews"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE INDEX IF NOT EXISTS ix_order_items_part_id ON order_items(part_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_notifications_user_read_created ON notifications(user_id, read_at, created_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_login_attempts_user_id ON login_attempts(user_id)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_login_attempts_user_id")
    op.execute("DROP INDEX IF EXISTS ix_notifications_user_read_created")
    op.execute("DROP INDEX IF EXISTS ix_order_items_part_id")
