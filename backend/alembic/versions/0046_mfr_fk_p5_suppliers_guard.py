"""add suppliers manufacturer text-to-id consistency guard

Revision ID: 0046_mfr_fk_p5_suppliers_guard
Revises: 0045_mfr_fk_p5_notnull_s1
Create Date: 2026-04-16
"""

from alembic import op


revision = "0046_mfr_fk_p5_suppliers_guard"
down_revision = "0045_mfr_fk_p5_notnull_s1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE public.suppliers
        ADD CONSTRAINT ck_suppliers_mfr_text_requires_id
        CHECK (
          manufacturer_name IS NULL OR btrim(manufacturer_name) = '' OR manufacturer_id IS NOT NULL
        ) NOT VALID
        """
    )
    op.execute(
        "ALTER TABLE public.suppliers VALIDATE CONSTRAINT ck_suppliers_mfr_text_requires_id"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE public.suppliers DROP CONSTRAINT IF EXISTS ck_suppliers_mfr_text_requires_id"
    )
