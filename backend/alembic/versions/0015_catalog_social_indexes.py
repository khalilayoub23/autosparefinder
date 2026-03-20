"""Add missing indexes for parts_catalog and social_posts tables.

Revision ID: 0015_catalog_social_indexes
Revises: 0014_drop_dupe_index
Create Date: 2026-03-20
"""
from alembic import op

revision = "0015_catalog_social_indexes"
down_revision = "0014_drop_dupe_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE INDEX IF NOT EXISTS ix_parts_catalog_is_active ON parts_catalog(is_active)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_parts_catalog_base_price ON parts_catalog(base_price)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_parts_catalog_created_at ON parts_catalog(created_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_social_posts_created_by ON social_posts(created_by)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_social_posts_created_by")
    op.execute("DROP INDEX IF EXISTS ix_parts_catalog_created_at")
    op.execute("DROP INDEX IF EXISTS ix_parts_catalog_base_price")
    op.execute("DROP INDEX IF EXISTS ix_parts_catalog_is_active")
