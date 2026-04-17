"""apply pass-2 unresolved manufacturer remediation

Revision ID: 0040_mfr_fk_phase4_pass2
Revises: 0039_mfr_fk_phase4_aftermarket
Create Date: 2026-04-16
"""

from alembic import op


revision = "0040_mfr_fk_phase4_pass2"
down_revision = "0039_mfr_fk_phase4_aftermarket"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Pass 2A: resolve uniquely mappable manufacturer_nm values using normalized-prefix matching.
    op.execute(
        """
        WITH brand_keys AS (
          SELECT id, lower(btrim(name)) AS key_txt
          FROM public.car_brands
          WHERE name IS NOT NULL AND btrim(name) <> ''

          UNION ALL

          SELECT id, lower(btrim(name_he))
          FROM public.car_brands
          WHERE name_he IS NOT NULL AND btrim(name_he) <> ''

          UNION ALL

          SELECT c.id, lower(btrim(a.alias_txt))
          FROM public.car_brands c,
               LATERAL unnest(COALESCE(c.aliases, ARRAY[]::text[])) AS a(alias_txt)
          WHERE a.alias_txt IS NOT NULL AND btrim(a.alias_txt) <> ''
        ), keys AS (
          SELECT id, regexp_replace(key_txt, '[^[:alnum:]א-ת]+', '', 'g') AS key_norm
          FROM brand_keys
        ), unresolved AS (
          SELECT id,
                 manufacturer_nm,
                 regexp_replace(lower(btrim(manufacturer_nm)), '[^[:alnum:]א-ת]+', '', 'g') AS val_norm
          FROM public.vehicle_market_il
          WHERE manufacturer_nm_id IS NULL
            AND manufacturer_nm IS NOT NULL
            AND btrim(manufacturer_nm) <> ''
        ), candidate AS (
          SELECT u.id AS row_id, k.id AS brand_id
          FROM unresolved u
          JOIN keys k
            ON length(k.key_norm) >= 3
           AND (u.val_norm = k.key_norm OR u.val_norm LIKE k.key_norm || '%')
        ), uniq_rows AS (
          SELECT row_id, (array_agg(brand_id))[1] AS brand_id
          FROM candidate
          GROUP BY row_id
          HAVING count(DISTINCT brand_id) = 1
        )
        UPDATE public.vehicle_market_il v
        SET manufacturer_nm_id = u.brand_id
        FROM uniq_rows u
        WHERE v.id = u.row_id
          AND v.manufacturer_nm_id IS NULL;
        """
    )

    # Pass 2B: resolve remaining part_cross_reference unknown value into aftermarket domain.
    op.execute(
        """
        INSERT INTO public.aftermarket_brands(name, is_active)
        VALUES ('Motorstore IL', true)
        ON CONFLICT (name) DO NOTHING;
        """
    )

    op.execute(
        """
        UPDATE public.part_cross_reference p
        SET aftermarket_brand_id = ab.id
        FROM public.aftermarket_brands ab
        WHERE p.aftermarket_brand_id IS NULL
          AND p.manufacturer_id IS NULL
          AND p.manufacturer IS NOT NULL
          AND lower(btrim(p.manufacturer)) = lower(btrim(ab.name))
          AND lower(btrim(ab.name)) = 'motorstore il';
        """
    )

    # Mark resolved exceptions for the remediated sources.
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
            WHERE v.manufacturer_nm_id IS NULL
              AND v.manufacturer_nm IS NOT NULL
              AND btrim(v.manufacturer_nm) <> ''
              AND lower(btrim(v.manufacturer_nm)) = e.normalized_value
          );
        """
    )

    op.execute(
        """
        UPDATE public.manufacturer_mapping_exceptions e
        SET resolution_status = 'resolved',
            updated_at = now()
        WHERE e.source_table = 'part_cross_reference'
          AND e.source_column = 'manufacturer'
          AND e.resolution_status = 'open'
          AND NOT EXISTS (
            SELECT 1
            FROM public.part_cross_reference p
            WHERE p.manufacturer IS NOT NULL
              AND btrim(p.manufacturer) <> ''
              AND p.manufacturer_id IS NULL
              AND p.aftermarket_brand_id IS NULL
              AND lower(btrim(p.manufacturer)) = e.normalized_value
          );
        """
    )

    # Upsert remaining unresolved manufacturer_nm exceptions.
    op.execute(
        """
        WITH unresolved AS (
          SELECT
            'vehicle_market_il'::text AS source_table,
            'manufacturer_nm'::text AS source_column,
            manufacturer_nm AS source_value,
            lower(btrim(manufacturer_nm)) AS normalized_value,
            count(*)::int AS row_count
          FROM public.vehicle_market_il
          WHERE manufacturer_nm_id IS NULL
            AND manufacturer_nm IS NOT NULL
            AND btrim(manufacturer_nm) <> ''
          GROUP BY manufacturer_nm
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
          END,
          'open'::text
        FROM classified c
        ON CONFLICT (source_table, source_column, normalized_value)
        DO UPDATE
          SET row_count = EXCLUDED.row_count,
              domain_guess = EXCLUDED.domain_guess,
              recommended_dictionary = EXCLUDED.recommended_dictionary,
              source_value = EXCLUDED.source_value,
              resolution_status = 'open',
              updated_at = now();
        """
    )


def downgrade() -> None:
    # Data remediations are intentionally not reversed.
    pass
