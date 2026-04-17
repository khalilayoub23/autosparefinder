"""backfill manufacturer_id columns from canonical brand dictionary

Revision ID: 0035_mfr_fk_phase2_bfill
Revises: 0034_manufacturer_fk_phase1
Create Date: 2026-04-16
"""

from alembic import op


revision = "0035_mfr_fk_phase2_bfill"
down_revision = "0034_manufacturer_fk_phase1"
branch_labels = None
depends_on = None


def _mapping_cte() -> str:
    # Match by canonical English name, Hebrew name, and unique aliases only.
    return """
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
    """


def upgrade() -> None:
    cte = _mapping_cte()

    op.execute(
        cte
        + """
        UPDATE public.part_cross_reference t
        SET manufacturer_id = m.id
        FROM key_to_id m
        WHERE t.manufacturer_id IS NULL
          AND t.manufacturer IS NOT NULL
          AND btrim(t.manufacturer) <> ''
          AND lower(btrim(t.manufacturer)) = m.k;
        """
    )

    op.execute(
        cte
        + """
        UPDATE public.part_variants t
        SET manufacturer_id = m.id
        FROM key_to_id m
        WHERE t.manufacturer_id IS NULL
          AND t.manufacturer IS NOT NULL
          AND btrim(t.manufacturer) <> ''
          AND lower(btrim(t.manufacturer)) = m.k;
        """
    )

    op.execute(
        cte
        + """
        UPDATE public.part_vehicle_fitment t
        SET manufacturer_id = m.id
        FROM key_to_id m
        WHERE t.manufacturer_id IS NULL
          AND t.manufacturer IS NOT NULL
          AND btrim(t.manufacturer) <> ''
          AND lower(btrim(t.manufacturer)) = m.k;
        """
    )

    op.execute(
        cte
        + """
        UPDATE public.parts_catalog t
        SET manufacturer_id = m.id
        FROM key_to_id m
        WHERE t.manufacturer_id IS NULL
          AND t.manufacturer IS NOT NULL
          AND btrim(t.manufacturer) <> ''
          AND lower(btrim(t.manufacturer)) = m.k;
        """
    )

    op.execute(
        cte
        + """
        UPDATE public.search_misses t
        SET vehicle_manufacturer_id = m.id
        FROM key_to_id m
        WHERE t.vehicle_manufacturer_id IS NULL
          AND t.vehicle_manufacturer IS NOT NULL
          AND btrim(t.vehicle_manufacturer) <> ''
          AND lower(btrim(t.vehicle_manufacturer)) = m.k;
        """
    )

    op.execute(
        cte
        + """
        UPDATE public.suppliers t
        SET manufacturer_id = m.id
        FROM key_to_id m
        WHERE t.manufacturer_id IS NULL
          AND t.manufacturer_name IS NOT NULL
          AND btrim(t.manufacturer_name) <> ''
          AND lower(btrim(t.manufacturer_name)) = m.k;
        """
    )

    op.execute(
        cte
        + """
        UPDATE public.vehicle_hierarchy_xls t
        SET manufacturer_id = m.id
        FROM key_to_id m
        WHERE t.manufacturer_id IS NULL
          AND t.manufacturer IS NOT NULL
          AND btrim(t.manufacturer) <> ''
          AND lower(btrim(t.manufacturer)) = m.k;
        """
    )

    op.execute(
        cte
        + """
        UPDATE public.vehicle_market_il t
        SET manufacturer_id = m.id
        FROM key_to_id m
        WHERE t.manufacturer_id IS NULL
          AND t.manufacturer IS NOT NULL
          AND btrim(t.manufacturer) <> ''
          AND lower(btrim(t.manufacturer)) = m.k;
        """
    )

    op.execute(
        cte
        + """
        UPDATE public.vehicle_market_il t
        SET manufacturer_nm_id = m.id
        FROM key_to_id m
        WHERE t.manufacturer_nm_id IS NULL
          AND t.manufacturer_nm IS NOT NULL
          AND btrim(t.manufacturer_nm) <> ''
          AND lower(btrim(t.manufacturer_nm)) = m.k;
        """
    )

    op.execute(
        cte
        + """
        UPDATE public.vehicles t
        SET manufacturer_id = m.id
        FROM key_to_id m
        WHERE t.manufacturer_id IS NULL
          AND t.manufacturer IS NOT NULL
          AND btrim(t.manufacturer) <> ''
          AND lower(btrim(t.manufacturer)) = m.k;
        """
    )


def downgrade() -> None:
    op.execute("UPDATE public.part_cross_reference SET manufacturer_id = NULL")
    op.execute("UPDATE public.part_variants SET manufacturer_id = NULL")
    op.execute("UPDATE public.part_vehicle_fitment SET manufacturer_id = NULL")
    op.execute("UPDATE public.parts_catalog SET manufacturer_id = NULL")
    op.execute("UPDATE public.search_misses SET vehicle_manufacturer_id = NULL")
    op.execute("UPDATE public.suppliers SET manufacturer_id = NULL")
    op.execute("UPDATE public.vehicle_hierarchy_xls SET manufacturer_id = NULL")
    op.execute("UPDATE public.vehicle_market_il SET manufacturer_id = NULL")
    op.execute("UPDATE public.vehicle_market_il SET manufacturer_nm_id = NULL")
    op.execute("UPDATE public.vehicles SET manufacturer_id = NULL")
