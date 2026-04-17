"""bridge manufacturer_nm_id from manufacturer_id for remaining unresolved rows

Revision ID: 0043_mfr_fk_p4_nm_bridge
Revises: 0042_mfr_fk_phase5_guards
Create Date: 2026-04-16
"""

from alembic import op


revision = "0043_mfr_fk_p4_nm_bridge"
down_revision = "0042_mfr_fk_phase5_guards"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE public.vehicle_market_il
        SET manufacturer_nm_id = manufacturer_id
        WHERE manufacturer_nm_id IS NULL
          AND manufacturer_id IS NOT NULL
          AND manufacturer_nm IS NOT NULL
          AND btrim(manufacturer_nm) <> '';
        """
    )

    op.execute(
        """
        UPDATE public.manufacturer_mapping_exceptions e
        SET resolution_status = 'resolved',
            updated_at = now()
        WHERE e.source_table = 'vehicle_market_il'
          AND e.source_column = 'manufacturer_nm'
          AND e.resolution_status = 'open'
          AND NOT EXISTS (
            SELECT 1
            FROM public.vehicle_market_il v
            WHERE v.manufacturer_nm IS NOT NULL
              AND btrim(v.manufacturer_nm) <> ''
              AND v.manufacturer_nm_id IS NULL
              AND lower(btrim(v.manufacturer_nm)) = e.normalized_value
          );
        """
    )


def downgrade() -> None:
    # Data bridge is intentionally non-reversible.
    pass
