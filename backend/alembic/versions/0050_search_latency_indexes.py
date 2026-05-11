"""add search latency indexes for fallback text and strict fitment

Revision ID: 0050_search_latency_indexes
Revises: 0049_filter_hierarchy_indexes
Create Date: 2026-05-11
"""

from alembic import op


revision = "0050_search_latency_indexes"
down_revision = "0049_filter_hierarchy_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Fallback text search in /api/v1/parts/search uses ILIKE across
    # sku/manufacturer/category/oem_number when Meilisearch is unavailable.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_parts_catalog_sku_trgm
        ON public.parts_catalog USING gin (sku gin_trgm_ops)
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_parts_catalog_manufacturer_trgm
        ON public.parts_catalog USING gin (manufacturer gin_trgm_ops)
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_parts_catalog_category_trgm
        ON public.parts_catalog USING gin (category gin_trgm_ops)
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_parts_catalog_oem_number_trgm
        ON public.parts_catalog USING gin (oem_number gin_trgm_ops)
        """
    )

    # Empty-text vehicle/category searches request candidates by part_type and id.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_parts_catalog_active_part_type_id
        ON public.parts_catalog (part_type, id)
        WHERE is_active = TRUE
        """
    )

    # Strict fitment path in /api/v1/parts/search filters by normalized
    # manufacturer/model and year range before joining by part_id.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_pvf_mfr_model_year_part_norm
        ON public.part_vehicle_fitment (
            LOWER(TRIM(manufacturer)),
            LOWER(TRIM(model)),
            year_from,
            COALESCE(year_to, year_from),
            part_id
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS public.idx_pvf_mfr_model_year_part_norm")
    op.execute("DROP INDEX IF EXISTS public.idx_parts_catalog_active_part_type_id")
    op.execute("DROP INDEX IF EXISTS public.idx_parts_catalog_oem_number_trgm")
    op.execute("DROP INDEX IF EXISTS public.idx_parts_catalog_category_trgm")
    op.execute("DROP INDEX IF EXISTS public.idx_parts_catalog_manufacturer_trgm")
    op.execute("DROP INDEX IF EXISTS public.idx_parts_catalog_sku_trgm")
