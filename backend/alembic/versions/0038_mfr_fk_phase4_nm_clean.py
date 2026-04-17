"""normalize vehicle_market_il.manufacturer_nm and refresh exception snapshot

Revision ID: 0038_mfr_fk_phase4_nm_clean
Revises: 0037_mfr_fk_phase4_exc
Create Date: 2026-04-16
"""

from alembic import op


revision = "0038_mfr_fk_phase4_nm_clean"
down_revision = "0037_mfr_fk_phase4_exc"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Step A: normalize trailing country suffixes and backfill manufacturer_nm_id when resolvable.
    op.execute(
        """
        WITH candidates AS (
          SELECT
            id,
            btrim(
              regexp_replace(
                manufacturer_nm,
                '\\s+(גרמניה|צ''כיה|ארה"ב|ארהב|אנגליה|יפן|קוריאה|דרום\\s+קוריאה|צרפת|סין|בלגיה|ספרד|איטליה|הודו|הולנד|שוודיה|שבדיה|רומניה|פולין|הונגריה|טורקיה|תאילנד|פורטוגל|מקסיקו|סלובקיה)$',
                '',
                'i'
              )
            ) AS cleaned
          FROM public.vehicle_market_il
          WHERE manufacturer_nm_id IS NULL
            AND manufacturer_nm IS NOT NULL
            AND btrim(manufacturer_nm) <> ''
        ), resolved AS (
          SELECT
            id,
            cleaned,
            public.resolve_car_brand_id(cleaned) AS resolved_id
          FROM candidates
        )
        UPDATE public.vehicle_market_il v
        SET manufacturer_nm = r.cleaned,
            manufacturer_nm_id = r.resolved_id
        FROM resolved r
        WHERE v.id = r.id
          AND r.resolved_id IS NOT NULL;
        """
    )

    # Step B: second pass direct resolution on possibly normalized values.
    op.execute(
        """
        UPDATE public.vehicle_market_il v
        SET manufacturer_nm_id = public.resolve_car_brand_id(v.manufacturer_nm)
        WHERE v.manufacturer_nm_id IS NULL
          AND v.manufacturer_nm IS NOT NULL
          AND btrim(v.manufacturer_nm) <> '';
        """
    )

    # Step C: refresh exception status for vehicle_market_il.manufacturer_nm.
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
          END AS recommended_dictionary,
          'open'::text AS resolution_status
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
    # Data normalization is intentionally non-reversible.
    pass
