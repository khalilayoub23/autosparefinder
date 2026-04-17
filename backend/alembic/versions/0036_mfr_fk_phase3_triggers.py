"""add dual-write triggers for manufacturer text and manufacturer_id columns

Revision ID: 0036_mfr_fk_phase3_triggers
Revises: 0035_mfr_fk_phase2_bfill
Create Date: 2026-04-16
"""

from alembic import op


revision = "0036_mfr_fk_phase3_triggers"
down_revision = "0035_mfr_fk_phase2_bfill"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.resolve_car_brand_id(p_text text)
        RETURNS uuid
        LANGUAGE sql
        STABLE
        AS $$
          WITH raw_map AS (
            SELECT lower(btrim(name)) AS k, id
            FROM public.car_brands
            WHERE name IS NOT NULL AND btrim(name) <> ''

            UNION ALL

            SELECT lower(btrim(name_he)) AS k, id
            FROM public.car_brands
            WHERE name_he IS NOT NULL AND btrim(name_he) <> ''

            UNION ALL

            SELECT lower(btrim(a.alias_txt)) AS k, c.id
            FROM public.car_brands c,
                 LATERAL unnest(COALESCE(c.aliases, ARRAY[]::text[])) AS a(alias_txt)
            WHERE a.alias_txt IS NOT NULL AND btrim(a.alias_txt) <> ''
          ), key_to_id AS (
            SELECT k, (array_agg(id))[1] AS id
            FROM raw_map
            GROUP BY k
            HAVING count(DISTINCT id) = 1
          )
          SELECT id
          FROM key_to_id
          WHERE k = lower(btrim(COALESCE(p_text, '')))
          LIMIT 1;
        $$;
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.canonical_car_brand_name(p_id uuid)
        RETURNS text
        LANGUAGE sql
        STABLE
        AS $$
          SELECT name
          FROM public.car_brands
          WHERE id = p_id
          LIMIT 1;
        $$;
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.trg_sync_mfr_common()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
          IF NEW.manufacturer_id IS NULL
             AND NEW.manufacturer IS NOT NULL
             AND btrim(NEW.manufacturer) <> '' THEN
            NEW.manufacturer_id := public.resolve_car_brand_id(NEW.manufacturer);
          END IF;

          IF (NEW.manufacturer IS NULL OR btrim(NEW.manufacturer) = '')
             AND NEW.manufacturer_id IS NOT NULL THEN
            NEW.manufacturer := public.canonical_car_brand_name(NEW.manufacturer_id);
          END IF;

          RETURN NEW;
        END;
        $$;
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.trg_sync_search_misses_mfr()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
          IF NEW.vehicle_manufacturer_id IS NULL
             AND NEW.vehicle_manufacturer IS NOT NULL
             AND btrim(NEW.vehicle_manufacturer) <> '' THEN
            NEW.vehicle_manufacturer_id := public.resolve_car_brand_id(NEW.vehicle_manufacturer);
          END IF;

          IF (NEW.vehicle_manufacturer IS NULL OR btrim(NEW.vehicle_manufacturer) = '')
             AND NEW.vehicle_manufacturer_id IS NOT NULL THEN
            NEW.vehicle_manufacturer := public.canonical_car_brand_name(NEW.vehicle_manufacturer_id);
          END IF;

          RETURN NEW;
        END;
        $$;
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.trg_sync_suppliers_mfr()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
          IF NEW.manufacturer_id IS NULL
             AND NEW.manufacturer_name IS NOT NULL
             AND btrim(NEW.manufacturer_name) <> '' THEN
            NEW.manufacturer_id := public.resolve_car_brand_id(NEW.manufacturer_name);
          END IF;

          IF (NEW.manufacturer_name IS NULL OR btrim(NEW.manufacturer_name) = '')
             AND NEW.manufacturer_id IS NOT NULL THEN
            NEW.manufacturer_name := public.canonical_car_brand_name(NEW.manufacturer_id);
          END IF;

          RETURN NEW;
        END;
        $$;
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.trg_sync_vehicle_market_il_mfrs()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
          IF NEW.manufacturer_id IS NULL
             AND NEW.manufacturer IS NOT NULL
             AND btrim(NEW.manufacturer) <> '' THEN
            NEW.manufacturer_id := public.resolve_car_brand_id(NEW.manufacturer);
          END IF;

          IF (NEW.manufacturer IS NULL OR btrim(NEW.manufacturer) = '')
             AND NEW.manufacturer_id IS NOT NULL THEN
            NEW.manufacturer := public.canonical_car_brand_name(NEW.manufacturer_id);
          END IF;

          IF NEW.manufacturer_nm_id IS NULL
             AND NEW.manufacturer_nm IS NOT NULL
             AND btrim(NEW.manufacturer_nm) <> '' THEN
            NEW.manufacturer_nm_id := public.resolve_car_brand_id(NEW.manufacturer_nm);
          END IF;

          IF (NEW.manufacturer_nm IS NULL OR btrim(NEW.manufacturer_nm) = '')
             AND NEW.manufacturer_nm_id IS NOT NULL THEN
            NEW.manufacturer_nm := public.canonical_car_brand_name(NEW.manufacturer_nm_id);
          END IF;

          RETURN NEW;
        END;
        $$;
        """
    )

    op.execute(
        """
        DO $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_trigger
            WHERE tgname = 'trg_sync_part_cross_reference_mfr'
          ) THEN
            CREATE TRIGGER trg_sync_part_cross_reference_mfr
            BEFORE INSERT OR UPDATE ON public.part_cross_reference
            FOR EACH ROW
            EXECUTE FUNCTION public.trg_sync_mfr_common();
          END IF;
        END $$;
        """
    )

    op.execute(
        """
        DO $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_trigger
            WHERE tgname = 'trg_sync_part_variants_mfr'
          ) THEN
            CREATE TRIGGER trg_sync_part_variants_mfr
            BEFORE INSERT OR UPDATE ON public.part_variants
            FOR EACH ROW
            EXECUTE FUNCTION public.trg_sync_mfr_common();
          END IF;
        END $$;
        """
    )

    op.execute(
        """
        DO $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_trigger
            WHERE tgname = 'trg_sync_part_vehicle_fitment_mfr'
          ) THEN
            CREATE TRIGGER trg_sync_part_vehicle_fitment_mfr
            BEFORE INSERT OR UPDATE ON public.part_vehicle_fitment
            FOR EACH ROW
            EXECUTE FUNCTION public.trg_sync_mfr_common();
          END IF;
        END $$;
        """
    )

    op.execute(
        """
        DO $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_trigger
            WHERE tgname = 'trg_sync_parts_catalog_mfr'
          ) THEN
            CREATE TRIGGER trg_sync_parts_catalog_mfr
            BEFORE INSERT OR UPDATE ON public.parts_catalog
            FOR EACH ROW
            EXECUTE FUNCTION public.trg_sync_mfr_common();
          END IF;
        END $$;
        """
    )

    op.execute(
        """
        DO $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_trigger
            WHERE tgname = 'trg_sync_vehicle_hierarchy_xls_mfr'
          ) THEN
            CREATE TRIGGER trg_sync_vehicle_hierarchy_xls_mfr
            BEFORE INSERT OR UPDATE ON public.vehicle_hierarchy_xls
            FOR EACH ROW
            EXECUTE FUNCTION public.trg_sync_mfr_common();
          END IF;
        END $$;
        """
    )

    op.execute(
        """
        DO $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_trigger
            WHERE tgname = 'trg_sync_vehicles_mfr'
          ) THEN
            CREATE TRIGGER trg_sync_vehicles_mfr
            BEFORE INSERT OR UPDATE ON public.vehicles
            FOR EACH ROW
            EXECUTE FUNCTION public.trg_sync_mfr_common();
          END IF;
        END $$;
        """
    )

    op.execute(
        """
        DO $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_trigger
            WHERE tgname = 'trg_sync_search_misses_mfr'
          ) THEN
            CREATE TRIGGER trg_sync_search_misses_mfr
            BEFORE INSERT OR UPDATE ON public.search_misses
            FOR EACH ROW
            EXECUTE FUNCTION public.trg_sync_search_misses_mfr();
          END IF;
        END $$;
        """
    )

    op.execute(
        """
        DO $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_trigger
            WHERE tgname = 'trg_sync_suppliers_mfr'
          ) THEN
            CREATE TRIGGER trg_sync_suppliers_mfr
            BEFORE INSERT OR UPDATE ON public.suppliers
            FOR EACH ROW
            EXECUTE FUNCTION public.trg_sync_suppliers_mfr();
          END IF;
        END $$;
        """
    )

    op.execute(
        """
        DO $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_trigger
            WHERE tgname = 'trg_sync_vehicle_market_il_mfrs'
          ) THEN
            CREATE TRIGGER trg_sync_vehicle_market_il_mfrs
            BEFORE INSERT OR UPDATE ON public.vehicle_market_il
            FOR EACH ROW
            EXECUTE FUNCTION public.trg_sync_vehicle_market_il_mfrs();
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_sync_vehicle_market_il_mfrs ON public.vehicle_market_il")
    op.execute("DROP TRIGGER IF EXISTS trg_sync_suppliers_mfr ON public.suppliers")
    op.execute("DROP TRIGGER IF EXISTS trg_sync_search_misses_mfr ON public.search_misses")
    op.execute("DROP TRIGGER IF EXISTS trg_sync_vehicles_mfr ON public.vehicles")
    op.execute("DROP TRIGGER IF EXISTS trg_sync_vehicle_hierarchy_xls_mfr ON public.vehicle_hierarchy_xls")
    op.execute("DROP TRIGGER IF EXISTS trg_sync_parts_catalog_mfr ON public.parts_catalog")
    op.execute("DROP TRIGGER IF EXISTS trg_sync_part_vehicle_fitment_mfr ON public.part_vehicle_fitment")
    op.execute("DROP TRIGGER IF EXISTS trg_sync_part_variants_mfr ON public.part_variants")
    op.execute("DROP TRIGGER IF EXISTS trg_sync_part_cross_reference_mfr ON public.part_cross_reference")

    op.execute("DROP FUNCTION IF EXISTS public.trg_sync_vehicle_market_il_mfrs()")
    op.execute("DROP FUNCTION IF EXISTS public.trg_sync_suppliers_mfr()")
    op.execute("DROP FUNCTION IF EXISTS public.trg_sync_search_misses_mfr()")
    op.execute("DROP FUNCTION IF EXISTS public.trg_sync_mfr_common()")
    op.execute("DROP FUNCTION IF EXISTS public.canonical_car_brand_name(uuid)")
    op.execute("DROP FUNCTION IF EXISTS public.resolve_car_brand_id(text)")
