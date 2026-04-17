"""add unresolved manufacturer mapping exception registry

Revision ID: 0037_mfr_fk_phase4_exc
Revises: 0036_mfr_fk_phase3_triggers
Create Date: 2026-04-16
"""

from alembic import op


revision = "0037_mfr_fk_phase4_exc"
down_revision = "0036_mfr_fk_phase3_triggers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS public.manufacturer_mapping_exceptions (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          source_table VARCHAR(64) NOT NULL,
          source_column VARCHAR(64) NOT NULL,
          source_value VARCHAR(200) NOT NULL,
          normalized_value VARCHAR(200) NOT NULL,
          row_count INTEGER NOT NULL DEFAULT 0,
          domain_guess VARCHAR(32) NOT NULL DEFAULT 'unknown',
          recommended_dictionary VARCHAR(64),
          resolved_brand_id UUID,
          resolution_status VARCHAR(20) NOT NULL DEFAULT 'open',
          notes TEXT,
          created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT now(),
          updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT now(),
          CONSTRAINT uq_mfr_map_exc_source_norm UNIQUE (source_table, source_column, normalized_value),
          CONSTRAINT fk_mfr_map_exc_resolved_brand
            FOREIGN KEY (resolved_brand_id) REFERENCES public.car_brands(id)
        );
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_mfr_map_exc_resolution_status
          ON public.manufacturer_mapping_exceptions (resolution_status);
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_mfr_map_exc_domain_guess
          ON public.manufacturer_mapping_exceptions (domain_guess);
        """
    )

    # Snapshot unresolved rows after phase2 backfill + phase3 dual-write
    op.execute(
        """
        WITH unresolved AS (
          SELECT 'part_cross_reference'::text AS source_table, 'manufacturer'::text AS source_column,
                 manufacturer AS source_value, lower(btrim(manufacturer)) AS normalized_value,
                 count(*)::int AS row_count
          FROM public.part_cross_reference
          WHERE manufacturer_id IS NULL
            AND manufacturer IS NOT NULL
            AND btrim(manufacturer) <> ''
          GROUP BY manufacturer

          UNION ALL

          SELECT 'part_variants', 'manufacturer',
                 manufacturer, lower(btrim(manufacturer)), count(*)::int
          FROM public.part_variants
          WHERE manufacturer_id IS NULL
            AND manufacturer IS NOT NULL
            AND btrim(manufacturer) <> ''
          GROUP BY manufacturer

          UNION ALL

          SELECT 'part_vehicle_fitment', 'manufacturer',
                 manufacturer, lower(btrim(manufacturer)), count(*)::int
          FROM public.part_vehicle_fitment
          WHERE manufacturer_id IS NULL
            AND manufacturer IS NOT NULL
            AND btrim(manufacturer) <> ''
          GROUP BY manufacturer

          UNION ALL

          SELECT 'parts_catalog', 'manufacturer',
                 manufacturer, lower(btrim(manufacturer)), count(*)::int
          FROM public.parts_catalog
          WHERE manufacturer_id IS NULL
            AND manufacturer IS NOT NULL
            AND btrim(manufacturer) <> ''
          GROUP BY manufacturer

          UNION ALL

          SELECT 'search_misses', 'vehicle_manufacturer',
                 vehicle_manufacturer, lower(btrim(vehicle_manufacturer)), count(*)::int
          FROM public.search_misses
          WHERE vehicle_manufacturer_id IS NULL
            AND vehicle_manufacturer IS NOT NULL
            AND btrim(vehicle_manufacturer) <> ''
          GROUP BY vehicle_manufacturer

          UNION ALL

          SELECT 'suppliers', 'manufacturer_name',
                 manufacturer_name, lower(btrim(manufacturer_name)), count(*)::int
          FROM public.suppliers
          WHERE manufacturer_id IS NULL
            AND manufacturer_name IS NOT NULL
            AND btrim(manufacturer_name) <> ''
          GROUP BY manufacturer_name

          UNION ALL

          SELECT 'vehicle_hierarchy_xls', 'manufacturer',
                 manufacturer, lower(btrim(manufacturer)), count(*)::int
          FROM public.vehicle_hierarchy_xls
          WHERE manufacturer_id IS NULL
            AND manufacturer IS NOT NULL
            AND btrim(manufacturer) <> ''
          GROUP BY manufacturer

          UNION ALL

          SELECT 'vehicle_market_il', 'manufacturer',
                 manufacturer, lower(btrim(manufacturer)), count(*)::int
          FROM public.vehicle_market_il
          WHERE manufacturer_id IS NULL
            AND manufacturer IS NOT NULL
            AND btrim(manufacturer) <> ''
          GROUP BY manufacturer

          UNION ALL

          SELECT 'vehicle_market_il', 'manufacturer_nm',
                 manufacturer_nm, lower(btrim(manufacturer_nm)), count(*)::int
          FROM public.vehicle_market_il
          WHERE manufacturer_nm_id IS NULL
            AND manufacturer_nm IS NOT NULL
            AND btrim(manufacturer_nm) <> ''
          GROUP BY manufacturer_nm

          UNION ALL

          SELECT 'vehicles', 'manufacturer',
                 manufacturer, lower(btrim(manufacturer)), count(*)::int
          FROM public.vehicles
          WHERE manufacturer_id IS NULL
            AND manufacturer IS NOT NULL
            AND btrim(manufacturer) <> ''
          GROUP BY manufacturer
        ), classified AS (
          SELECT
            u.*,
            CASE
              WHEN EXISTS (
                SELECT 1
                FROM public.truck_brands tb
                WHERE lower(btrim(tb.name)) = u.normalized_value
                   OR (tb.name_he IS NOT NULL AND lower(btrim(tb.name_he)) = u.normalized_value)
              )
              OR EXISTS (
                SELECT 1
                FROM public.truck_brand_aliases tba
                WHERE tba.normalized = u.normalized_value
              ) THEN 'truck_brand'

              WHEN EXISTS (
                SELECT 1
                FROM public.aftermarket_brands ab
                WHERE lower(btrim(ab.name)) = u.normalized_value
              ) THEN 'aftermarket_brand'

              ELSE 'unknown'
            END AS domain_guess
          FROM unresolved u
        )
        INSERT INTO public.manufacturer_mapping_exceptions (
          source_table, source_column, source_value, normalized_value, row_count,
          domain_guess, recommended_dictionary, resolution_status
        )
        SELECT
          c.source_table,
          c.source_column,
          c.source_value,
          c.normalized_value,
          c.row_count,
          c.domain_guess,
          CASE
            WHEN c.domain_guess = 'truck_brand' THEN 'truck_brands'
            WHEN c.domain_guess = 'aftermarket_brand' THEN 'aftermarket_brands'
            ELSE 'car_brands_or_new'
          END AS recommended_dictionary,
          'open'::text AS resolution_status
        FROM classified c
        ON CONFLICT (source_table, source_column, normalized_value)
        DO UPDATE
          SET row_count = EXCLUDED.row_count,
              domain_guess = EXCLUDED.domain_guess,
              recommended_dictionary = EXCLUDED.recommended_dictionary,
              updated_at = now();
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS public.manufacturer_mapping_exceptions")
