"""promote selected manufacturer FK columns to not null (stage 1)

Revision ID: 0045_mfr_fk_p5_notnull_s1
Revises: 0044_mfr_fk_p5_nm_guard
Create Date: 2026-04-16
"""

from alembic import op


revision = "0045_mfr_fk_p5_notnull_s1"
down_revision = "0044_mfr_fk_p5_nm_guard"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Stage 1: only columns with zero nulls and active dual-write protection.
    op.execute("ALTER TABLE public.parts_catalog ALTER COLUMN manufacturer_id SET NOT NULL")
    op.execute("ALTER TABLE public.part_variants ALTER COLUMN manufacturer_id SET NOT NULL")
    op.execute("ALTER TABLE public.part_vehicle_fitment ALTER COLUMN manufacturer_id SET NOT NULL")
    op.execute("ALTER TABLE public.vehicle_hierarchy_xls ALTER COLUMN manufacturer_id SET NOT NULL")
    op.execute("ALTER TABLE public.vehicles ALTER COLUMN manufacturer_id SET NOT NULL")
    op.execute("ALTER TABLE public.vehicle_market_il ALTER COLUMN manufacturer_id SET NOT NULL")
    op.execute("ALTER TABLE public.vehicle_market_il ALTER COLUMN manufacturer_nm_id SET NOT NULL")


def downgrade() -> None:
    op.execute("ALTER TABLE public.vehicle_market_il ALTER COLUMN manufacturer_nm_id DROP NOT NULL")
    op.execute("ALTER TABLE public.vehicle_market_il ALTER COLUMN manufacturer_id DROP NOT NULL")
    op.execute("ALTER TABLE public.vehicles ALTER COLUMN manufacturer_id DROP NOT NULL")
    op.execute("ALTER TABLE public.vehicle_hierarchy_xls ALTER COLUMN manufacturer_id DROP NOT NULL")
    op.execute("ALTER TABLE public.part_vehicle_fitment ALTER COLUMN manufacturer_id DROP NOT NULL")
    op.execute("ALTER TABLE public.part_variants ALTER COLUMN manufacturer_id DROP NOT NULL")
    op.execute("ALTER TABLE public.parts_catalog ALTER COLUMN manufacturer_id DROP NOT NULL")
