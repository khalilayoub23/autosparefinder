"""Drop duplicate index ix_part_cross_reference_ref_number from autospare catalog DB.

Revision ID: 0014_drop_dupe_index
Revises: 0013_add_social_posts
Create Date: 2026-03-20
"""
from alembic import op

revision = "0014_drop_dupe_index"
down_revision = "0013_add_social_posts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_part_cross_reference_ref_number")


def downgrade() -> None:
    op.execute("CREATE INDEX IF NOT EXISTS ix_part_cross_reference_ref_number ON part_cross_reference(ref_number)")
