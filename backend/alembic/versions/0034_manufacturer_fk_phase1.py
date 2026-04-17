"""add manufacturer_id FK-ready columns across operational tables

Revision ID: 0034_manufacturer_fk_phase1
Revises: 0033_ensure_vehicle_market_il
Create Date: 2026-04-16
"""

from alembic import op


revision = "0034_manufacturer_fk_phase1"
down_revision = "0033_ensure_vehicle_market_il"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add nullable FK-ready columns (additive, no behavior break)
    op.execute("ALTER TABLE public.part_cross_reference ADD COLUMN IF NOT EXISTS manufacturer_id UUID")
    op.execute("ALTER TABLE public.part_variants ADD COLUMN IF NOT EXISTS manufacturer_id UUID")
    op.execute("ALTER TABLE public.part_vehicle_fitment ADD COLUMN IF NOT EXISTS manufacturer_id UUID")
    op.execute("ALTER TABLE public.parts_catalog ADD COLUMN IF NOT EXISTS manufacturer_id UUID")
    op.execute("ALTER TABLE public.search_misses ADD COLUMN IF NOT EXISTS vehicle_manufacturer_id UUID")
    op.execute("ALTER TABLE public.suppliers ADD COLUMN IF NOT EXISTS manufacturer_id UUID")
    op.execute("ALTER TABLE public.vehicle_hierarchy_xls ADD COLUMN IF NOT EXISTS manufacturer_id UUID")
    op.execute("ALTER TABLE public.vehicle_market_il ADD COLUMN IF NOT EXISTS manufacturer_id UUID")
    op.execute("ALTER TABLE public.vehicle_market_il ADD COLUMN IF NOT EXISTS manufacturer_nm_id UUID")
    op.execute("ALTER TABLE public.vehicles ADD COLUMN IF NOT EXISTS manufacturer_id UUID")

    # Add indexes for join/filter performance
    op.execute("CREATE INDEX IF NOT EXISTS idx_part_cross_reference_manufacturer_id ON public.part_cross_reference (manufacturer_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_part_variants_manufacturer_id ON public.part_variants (manufacturer_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_part_vehicle_fitment_manufacturer_id ON public.part_vehicle_fitment (manufacturer_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_parts_catalog_manufacturer_id ON public.parts_catalog (manufacturer_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_search_misses_vehicle_manufacturer_id ON public.search_misses (vehicle_manufacturer_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_suppliers_manufacturer_id ON public.suppliers (manufacturer_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_vehicle_hierarchy_xls_manufacturer_id ON public.vehicle_hierarchy_xls (manufacturer_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_vehicle_market_il_manufacturer_id ON public.vehicle_market_il (manufacturer_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_vehicle_market_il_manufacturer_nm_id ON public.vehicle_market_il (manufacturer_nm_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_vehicles_manufacturer_id ON public.vehicles (manufacturer_id)")

    # Add NOT VALID foreign keys to avoid large-table validation lock during rollout
    op.execute(
        """
        DO $$
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_part_cross_reference_manufacturer_id_car_brands') THEN
            ALTER TABLE public.part_cross_reference
              ADD CONSTRAINT fk_part_cross_reference_manufacturer_id_car_brands
              FOREIGN KEY (manufacturer_id) REFERENCES public.car_brands(id) NOT VALID;
          END IF;
        END $$;
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_part_variants_manufacturer_id_car_brands') THEN
            ALTER TABLE public.part_variants
              ADD CONSTRAINT fk_part_variants_manufacturer_id_car_brands
              FOREIGN KEY (manufacturer_id) REFERENCES public.car_brands(id) NOT VALID;
          END IF;
        END $$;
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_part_vehicle_fitment_manufacturer_id_car_brands') THEN
            ALTER TABLE public.part_vehicle_fitment
              ADD CONSTRAINT fk_part_vehicle_fitment_manufacturer_id_car_brands
              FOREIGN KEY (manufacturer_id) REFERENCES public.car_brands(id) NOT VALID;
          END IF;
        END $$;
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_parts_catalog_manufacturer_id_car_brands') THEN
            ALTER TABLE public.parts_catalog
              ADD CONSTRAINT fk_parts_catalog_manufacturer_id_car_brands
              FOREIGN KEY (manufacturer_id) REFERENCES public.car_brands(id) NOT VALID;
          END IF;
        END $$;
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_search_misses_vehicle_manufacturer_id_car_brands') THEN
            ALTER TABLE public.search_misses
              ADD CONSTRAINT fk_search_misses_vehicle_manufacturer_id_car_brands
              FOREIGN KEY (vehicle_manufacturer_id) REFERENCES public.car_brands(id) NOT VALID;
          END IF;
        END $$;
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_suppliers_manufacturer_id_car_brands') THEN
            ALTER TABLE public.suppliers
              ADD CONSTRAINT fk_suppliers_manufacturer_id_car_brands
              FOREIGN KEY (manufacturer_id) REFERENCES public.car_brands(id) NOT VALID;
          END IF;
        END $$;
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_vehicle_hierarchy_xls_manufacturer_id_car_brands') THEN
            ALTER TABLE public.vehicle_hierarchy_xls
              ADD CONSTRAINT fk_vehicle_hierarchy_xls_manufacturer_id_car_brands
              FOREIGN KEY (manufacturer_id) REFERENCES public.car_brands(id) NOT VALID;
          END IF;
        END $$;
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_vehicle_market_il_manufacturer_id_car_brands') THEN
            ALTER TABLE public.vehicle_market_il
              ADD CONSTRAINT fk_vehicle_market_il_manufacturer_id_car_brands
              FOREIGN KEY (manufacturer_id) REFERENCES public.car_brands(id) NOT VALID;
          END IF;
        END $$;
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_vehicle_market_il_manufacturer_nm_id_car_brands') THEN
            ALTER TABLE public.vehicle_market_il
              ADD CONSTRAINT fk_vehicle_market_il_manufacturer_nm_id_car_brands
              FOREIGN KEY (manufacturer_nm_id) REFERENCES public.car_brands(id) NOT VALID;
          END IF;
        END $$;
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_vehicles_manufacturer_id_car_brands') THEN
            ALTER TABLE public.vehicles
              ADD CONSTRAINT fk_vehicles_manufacturer_id_car_brands
              FOREIGN KEY (manufacturer_id) REFERENCES public.car_brands(id) NOT VALID;
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE public.vehicles DROP CONSTRAINT IF EXISTS fk_vehicles_manufacturer_id_car_brands")
    op.execute("ALTER TABLE public.vehicle_market_il DROP CONSTRAINT IF EXISTS fk_vehicle_market_il_manufacturer_nm_id_car_brands")
    op.execute("ALTER TABLE public.vehicle_market_il DROP CONSTRAINT IF EXISTS fk_vehicle_market_il_manufacturer_id_car_brands")
    op.execute("ALTER TABLE public.vehicle_hierarchy_xls DROP CONSTRAINT IF EXISTS fk_vehicle_hierarchy_xls_manufacturer_id_car_brands")
    op.execute("ALTER TABLE public.suppliers DROP CONSTRAINT IF EXISTS fk_suppliers_manufacturer_id_car_brands")
    op.execute("ALTER TABLE public.search_misses DROP CONSTRAINT IF EXISTS fk_search_misses_vehicle_manufacturer_id_car_brands")
    op.execute("ALTER TABLE public.parts_catalog DROP CONSTRAINT IF EXISTS fk_parts_catalog_manufacturer_id_car_brands")
    op.execute("ALTER TABLE public.part_vehicle_fitment DROP CONSTRAINT IF EXISTS fk_part_vehicle_fitment_manufacturer_id_car_brands")
    op.execute("ALTER TABLE public.part_variants DROP CONSTRAINT IF EXISTS fk_part_variants_manufacturer_id_car_brands")
    op.execute("ALTER TABLE public.part_cross_reference DROP CONSTRAINT IF EXISTS fk_part_cross_reference_manufacturer_id_car_brands")

    op.execute("DROP INDEX IF EXISTS public.idx_vehicles_manufacturer_id")
    op.execute("DROP INDEX IF EXISTS public.idx_vehicle_market_il_manufacturer_nm_id")
    op.execute("DROP INDEX IF EXISTS public.idx_vehicle_market_il_manufacturer_id")
    op.execute("DROP INDEX IF EXISTS public.idx_vehicle_hierarchy_xls_manufacturer_id")
    op.execute("DROP INDEX IF EXISTS public.idx_suppliers_manufacturer_id")
    op.execute("DROP INDEX IF EXISTS public.idx_search_misses_vehicle_manufacturer_id")
    op.execute("DROP INDEX IF EXISTS public.idx_parts_catalog_manufacturer_id")
    op.execute("DROP INDEX IF EXISTS public.idx_part_vehicle_fitment_manufacturer_id")
    op.execute("DROP INDEX IF EXISTS public.idx_part_variants_manufacturer_id")
    op.execute("DROP INDEX IF EXISTS public.idx_part_cross_reference_manufacturer_id")

    op.execute("ALTER TABLE public.vehicles DROP COLUMN IF EXISTS manufacturer_id")
    op.execute("ALTER TABLE public.vehicle_market_il DROP COLUMN IF EXISTS manufacturer_nm_id")
    op.execute("ALTER TABLE public.vehicle_market_il DROP COLUMN IF EXISTS manufacturer_id")
    op.execute("ALTER TABLE public.vehicle_hierarchy_xls DROP COLUMN IF EXISTS manufacturer_id")
    op.execute("ALTER TABLE public.suppliers DROP COLUMN IF EXISTS manufacturer_id")
    op.execute("ALTER TABLE public.search_misses DROP COLUMN IF EXISTS vehicle_manufacturer_id")
    op.execute("ALTER TABLE public.parts_catalog DROP COLUMN IF EXISTS manufacturer_id")
    op.execute("ALTER TABLE public.part_vehicle_fitment DROP COLUMN IF EXISTS manufacturer_id")
    op.execute("ALTER TABLE public.part_variants DROP COLUMN IF EXISTS manufacturer_id")
    op.execute("ALTER TABLE public.part_cross_reference DROP COLUMN IF EXISTS manufacturer_id")
