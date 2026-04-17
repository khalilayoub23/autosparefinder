"""add manufacturer_nm text-to-id guard on vehicle_market_il

Revision ID: 0044_mfr_fk_p5_nm_guard
Revises: 0043_mfr_fk_p4_nm_bridge
Create Date: 2026-04-16
"""

from alembic import op


revision = "0044_mfr_fk_p5_nm_guard"
down_revision = "0043_mfr_fk_p4_nm_bridge"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE public.vehicle_market_il
        ADD CONSTRAINT ck_vehicle_market_il_mfr_nm_text_requires_id
        CHECK (
          manufacturer_nm IS NULL OR btrim(manufacturer_nm) = '' OR manufacturer_nm_id IS NOT NULL
        ) NOT VALID
        """
    )
    op.execute(
        "ALTER TABLE public.vehicle_market_il VALIDATE CONSTRAINT ck_vehicle_market_il_mfr_nm_text_requires_id"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE public.vehicle_market_il DROP CONSTRAINT IF EXISTS ck_vehicle_market_il_mfr_nm_text_requires_id"
    )
