"""Enable pg_trgm and add trigram GIN indexes for fuzzy part-name search

Revision ID: 0016
Revises: 0015
Create Date: 2026-03-21
"""
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_parts_catalog_name_trgm
        ON parts_catalog USING gin(name gin_trgm_ops)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_parts_catalog_name_he_trgm
        ON parts_catalog USING gin(name_he gin_trgm_ops)
        """
    )


def downgrade():
    op.execute("DROP INDEX IF EXISTS idx_parts_catalog_name_trgm")
    op.execute("DROP INDEX IF EXISTS idx_parts_catalog_name_he_trgm")
