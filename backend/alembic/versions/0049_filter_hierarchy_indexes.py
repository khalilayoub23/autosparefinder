"""add hierarchy and filter latency indexes

Revision ID: 0049_filter_hierarchy_indexes
Revises: 0048_part_diagram_cache
Create Date: 2026-04-16
"""

from alembic import op


revision = "0049_filter_hierarchy_indexes"
down_revision = "0048_part_diagram_cache"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Match normalized manufacturer/model predicates used by hierarchy endpoints.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_vehicles_mfr_model_year_norm
        ON public.vehicles (
            LOWER(TRIM(manufacturer)),
            LOWER(TRIM(model)),
            year
        )
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_vehicle_hierarchy_xls_mfr_model_sub_norm
        ON public.vehicle_hierarchy_xls (
            LOWER(TRIM(manufacturer)),
            LOWER(TRIM(model)),
            LOWER(TRIM(COALESCE(sub_model, ''))),
            year_from,
            year_to,
            year_hint
        )
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_pvf_part_mfr_model_year_norm
        ON public.part_vehicle_fitment (
            part_id,
            LOWER(TRIM(manufacturer)),
            LOWER(TRIM(model)),
            year_from,
            COALESCE(year_to, year_from)
        )
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_supplier_parts_part_avail_price
        ON public.supplier_parts (part_id, is_available, price_ils)
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_supplier_parts_part_type_part_id
        ON public.supplier_parts (part_type, part_id)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS public.idx_supplier_parts_part_type_part_id")
    op.execute("DROP INDEX IF EXISTS public.idx_supplier_parts_part_avail_price")
    op.execute("DROP INDEX IF EXISTS public.idx_pvf_part_mfr_model_year_norm")
    op.execute("DROP INDEX IF EXISTS public.idx_vehicle_hierarchy_xls_mfr_model_sub_norm")
    op.execute("DROP INDEX IF EXISTS public.idx_vehicles_mfr_model_year_norm")
