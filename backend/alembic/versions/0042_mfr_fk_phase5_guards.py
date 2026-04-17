"""add manufacturer text-to-id consistency guards

Revision ID: 0042_mfr_fk_phase5_guards
Revises: 0041_mfr_fk_phase5_validate1
Create Date: 2026-04-16
"""

from alembic import op


revision = "0042_mfr_fk_phase5_guards"
down_revision = "0041_mfr_fk_phase5_validate1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # For fully remediated columns, enforce text->id consistency on future writes.
    op.execute(
        """
        ALTER TABLE public.parts_catalog
        ADD CONSTRAINT ck_parts_catalog_mfr_text_requires_id
        CHECK (
          manufacturer IS NULL OR btrim(manufacturer) = '' OR manufacturer_id IS NOT NULL
        ) NOT VALID
        """
    )
    op.execute(
        "ALTER TABLE public.parts_catalog VALIDATE CONSTRAINT ck_parts_catalog_mfr_text_requires_id"
    )

    op.execute(
        """
        ALTER TABLE public.part_variants
        ADD CONSTRAINT ck_part_variants_mfr_text_requires_id
        CHECK (
          manufacturer IS NULL OR btrim(manufacturer) = '' OR manufacturer_id IS NOT NULL
        ) NOT VALID
        """
    )
    op.execute(
        "ALTER TABLE public.part_variants VALIDATE CONSTRAINT ck_part_variants_mfr_text_requires_id"
    )

    op.execute(
        """
        ALTER TABLE public.part_vehicle_fitment
        ADD CONSTRAINT ck_pvf_mfr_text_requires_id
        CHECK (
          manufacturer IS NULL OR btrim(manufacturer) = '' OR manufacturer_id IS NOT NULL
        ) NOT VALID
        """
    )
    op.execute(
        "ALTER TABLE public.part_vehicle_fitment VALIDATE CONSTRAINT ck_pvf_mfr_text_requires_id"
    )

    op.execute(
        """
        ALTER TABLE public.search_misses
        ADD CONSTRAINT ck_search_misses_mfr_text_requires_id
        CHECK (
          vehicle_manufacturer IS NULL OR btrim(vehicle_manufacturer) = '' OR vehicle_manufacturer_id IS NOT NULL
        ) NOT VALID
        """
    )
    op.execute(
        "ALTER TABLE public.search_misses VALIDATE CONSTRAINT ck_search_misses_mfr_text_requires_id"
    )

    op.execute(
        """
        ALTER TABLE public.vehicle_hierarchy_xls
        ADD CONSTRAINT ck_vehicle_hierarchy_xls_mfr_text_requires_id
        CHECK (
          manufacturer IS NULL OR btrim(manufacturer) = '' OR manufacturer_id IS NOT NULL
        ) NOT VALID
        """
    )
    op.execute(
        "ALTER TABLE public.vehicle_hierarchy_xls VALIDATE CONSTRAINT ck_vehicle_hierarchy_xls_mfr_text_requires_id"
    )

    op.execute(
        """
        ALTER TABLE public.vehicles
        ADD CONSTRAINT ck_vehicles_mfr_text_requires_id
        CHECK (
          manufacturer IS NULL OR btrim(manufacturer) = '' OR manufacturer_id IS NOT NULL
        ) NOT VALID
        """
    )
    op.execute(
        "ALTER TABLE public.vehicles VALIDATE CONSTRAINT ck_vehicles_mfr_text_requires_id"
    )

    op.execute(
        """
        ALTER TABLE public.vehicle_market_il
        ADD CONSTRAINT ck_vehicle_market_il_mfr_text_requires_id
        CHECK (
          manufacturer IS NULL OR btrim(manufacturer) = '' OR manufacturer_id IS NOT NULL
        ) NOT VALID
        """
    )
    op.execute(
        "ALTER TABLE public.vehicle_market_il VALIDATE CONSTRAINT ck_vehicle_market_il_mfr_text_requires_id"
    )

    op.execute(
        """
        ALTER TABLE public.part_cross_reference
        ADD CONSTRAINT ck_part_cross_reference_mfr_text_requires_any_id
        CHECK (
          manufacturer IS NULL OR btrim(manufacturer) = '' OR manufacturer_id IS NOT NULL OR aftermarket_brand_id IS NOT NULL
        ) NOT VALID
        """
    )
    op.execute(
        "ALTER TABLE public.part_cross_reference VALIDATE CONSTRAINT ck_part_cross_reference_mfr_text_requires_any_id"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE public.part_cross_reference DROP CONSTRAINT IF EXISTS ck_part_cross_reference_mfr_text_requires_any_id")
    op.execute("ALTER TABLE public.vehicle_market_il DROP CONSTRAINT IF EXISTS ck_vehicle_market_il_mfr_text_requires_id")
    op.execute("ALTER TABLE public.vehicles DROP CONSTRAINT IF EXISTS ck_vehicles_mfr_text_requires_id")
    op.execute("ALTER TABLE public.vehicle_hierarchy_xls DROP CONSTRAINT IF EXISTS ck_vehicle_hierarchy_xls_mfr_text_requires_id")
    op.execute("ALTER TABLE public.search_misses DROP CONSTRAINT IF EXISTS ck_search_misses_mfr_text_requires_id")
    op.execute("ALTER TABLE public.part_vehicle_fitment DROP CONSTRAINT IF EXISTS ck_pvf_mfr_text_requires_id")
    op.execute("ALTER TABLE public.part_variants DROP CONSTRAINT IF EXISTS ck_part_variants_mfr_text_requires_id")
    op.execute("ALTER TABLE public.parts_catalog DROP CONSTRAINT IF EXISTS ck_parts_catalog_mfr_text_requires_id")
