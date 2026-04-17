"""validate manufacturer foreign keys stage 1

Revision ID: 0041_mfr_fk_phase5_validate1
Revises: 0040_mfr_fk_phase4_pass2
Create Date: 2026-04-16
"""

from alembic import op


revision = "0041_mfr_fk_phase5_validate1"
down_revision = "0040_mfr_fk_phase4_pass2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Validate previously added NOT VALID constraints now that backfill/dual-write is active.
    op.execute(
        "ALTER TABLE public.part_cross_reference VALIDATE CONSTRAINT fk_part_cross_reference_manufacturer_id_car_brands"
    )
    op.execute(
        "ALTER TABLE public.part_variants VALIDATE CONSTRAINT fk_part_variants_manufacturer_id_car_brands"
    )
    op.execute(
        "ALTER TABLE public.part_vehicle_fitment VALIDATE CONSTRAINT fk_part_vehicle_fitment_manufacturer_id_car_brands"
    )
    op.execute(
        "ALTER TABLE public.parts_catalog VALIDATE CONSTRAINT fk_parts_catalog_manufacturer_id_car_brands"
    )
    op.execute(
        "ALTER TABLE public.search_misses VALIDATE CONSTRAINT fk_search_misses_vehicle_manufacturer_id_car_brands"
    )
    op.execute(
        "ALTER TABLE public.suppliers VALIDATE CONSTRAINT fk_suppliers_manufacturer_id_car_brands"
    )
    op.execute(
        "ALTER TABLE public.vehicle_hierarchy_xls VALIDATE CONSTRAINT fk_vehicle_hierarchy_xls_manufacturer_id_car_brands"
    )
    op.execute(
        "ALTER TABLE public.vehicle_market_il VALIDATE CONSTRAINT fk_vehicle_market_il_manufacturer_id_car_brands"
    )
    op.execute(
        "ALTER TABLE public.vehicle_market_il VALIDATE CONSTRAINT fk_vehicle_market_il_manufacturer_nm_id_car_brands"
    )
    op.execute(
        "ALTER TABLE public.vehicles VALIDATE CONSTRAINT fk_vehicles_manufacturer_id_car_brands"
    )

    op.execute(
        "ALTER TABLE public.part_cross_reference VALIDATE CONSTRAINT fk_part_cross_reference_aftermarket_brand_id_aftermarket_brands"
    )


def downgrade() -> None:
    # Constraint validation is metadata state only; keep downgrade as no-op.
    pass
