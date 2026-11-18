"""partial index for task_heal_importer_price (built CONCURRENTLY in production 2026-11-18)

Revision ID: 0063_importer_gap_partial_index
Revises: 0062_version_oob_schema
"""
from alembic import op

revision = "0063_importer_gap_partial_index"
down_revision = "0062_version_oob_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("SET lock_timeout = 0")
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_parts_catalog_importer_gap ON public.parts_catalog (id) "
            "WHERE is_active AND max_price_ils > 0 AND (importer_price_ils IS NULL OR importer_price_ils = 0) "
            "AND (online_price_ils IS NULL OR online_price_ils = 0)")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS public.idx_parts_catalog_importer_gap")
