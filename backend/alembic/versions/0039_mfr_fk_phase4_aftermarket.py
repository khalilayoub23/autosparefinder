"""add aftermarket brand mapping lane for part_cross_reference

Revision ID: 0039_mfr_fk_phase4_aftermarket
Revises: 0038_mfr_fk_phase4_nm_clean
Create Date: 2026-04-16
"""

from alembic import op


revision = "0039_mfr_fk_phase4_aftermarket"
down_revision = "0038_mfr_fk_phase4_nm_clean"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE public.part_cross_reference ADD COLUMN IF NOT EXISTS aftermarket_brand_id UUID")
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_part_cross_reference_aftermarket_brand_id ON public.part_cross_reference (aftermarket_brand_id)"
    )

    op.execute(
        """
        DO $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conname = 'fk_part_cross_reference_aftermarket_brand_id_aftermarket_brands'
          ) THEN
            ALTER TABLE public.part_cross_reference
              ADD CONSTRAINT fk_part_cross_reference_aftermarket_brand_id_aftermarket_brands
              FOREIGN KEY (aftermarket_brand_id)
              REFERENCES public.aftermarket_brands(id)
              NOT VALID;
          END IF;
        END $$;
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.resolve_aftermarket_brand_id(p_text text)
        RETURNS uuid
        LANGUAGE sql
        STABLE
        AS $$
          WITH candidates AS (
            SELECT id
            FROM public.aftermarket_brands
            WHERE name IS NOT NULL
              AND btrim(name) <> ''
              AND lower(btrim(name)) = lower(btrim(COALESCE(p_text, '')))
          )
          SELECT (array_agg(id))[1]
          FROM candidates
          HAVING count(*) = 1;
        $$;
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.canonical_aftermarket_brand_name(p_id uuid)
        RETURNS text
        LANGUAGE sql
        STABLE
        AS $$
          SELECT name
          FROM public.aftermarket_brands
          WHERE id = p_id
          LIMIT 1;
        $$;
        """
    )

    # Replace part_cross_reference trigger with domain-aware version.
    op.execute("DROP TRIGGER IF EXISTS trg_sync_part_cross_reference_mfr ON public.part_cross_reference")

    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.trg_sync_part_cross_reference_mfr_domains()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
          IF NEW.manufacturer_id IS NULL
             AND NEW.manufacturer IS NOT NULL
             AND btrim(NEW.manufacturer) <> '' THEN
            NEW.manufacturer_id := public.resolve_car_brand_id(NEW.manufacturer);
          END IF;

          IF NEW.aftermarket_brand_id IS NULL
             AND NEW.manufacturer IS NOT NULL
             AND btrim(NEW.manufacturer) <> '' THEN
            NEW.aftermarket_brand_id := public.resolve_aftermarket_brand_id(NEW.manufacturer);
          END IF;

          IF (NEW.manufacturer IS NULL OR btrim(NEW.manufacturer) = '') THEN
            IF NEW.manufacturer_id IS NOT NULL THEN
              NEW.manufacturer := public.canonical_car_brand_name(NEW.manufacturer_id);
            ELSIF NEW.aftermarket_brand_id IS NOT NULL THEN
              NEW.manufacturer := public.canonical_aftermarket_brand_name(NEW.aftermarket_brand_id);
            END IF;
          END IF;

          RETURN NEW;
        END;
        $$;
        """
    )

    op.execute(
        """
        CREATE TRIGGER trg_sync_part_cross_reference_mfr
        BEFORE INSERT OR UPDATE ON public.part_cross_reference
        FOR EACH ROW
        EXECUTE FUNCTION public.trg_sync_part_cross_reference_mfr_domains();
        """
    )

    # Backfill aftermarket IDs for existing rows.
    op.execute(
        """
        UPDATE public.part_cross_reference p
        SET aftermarket_brand_id = public.resolve_aftermarket_brand_id(p.manufacturer)
        WHERE p.aftermarket_brand_id IS NULL
          AND p.manufacturer IS NOT NULL
          AND btrim(p.manufacturer) <> '';
        """
    )

    # Refresh exception status for part_cross_reference.manufacturer.
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

    op.execute(
        """
        WITH unresolved AS (
          SELECT
            'part_cross_reference'::text AS source_table,
            'manufacturer'::text AS source_column,
            manufacturer AS source_value,
            lower(btrim(manufacturer)) AS normalized_value,
            count(*)::int AS row_count
          FROM public.part_cross_reference
          WHERE manufacturer IS NOT NULL
            AND btrim(manufacturer) <> ''
            AND manufacturer_id IS NULL
            AND aftermarket_brand_id IS NULL
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
    op.execute("DROP TRIGGER IF EXISTS trg_sync_part_cross_reference_mfr ON public.part_cross_reference")

    op.execute(
        """
        CREATE TRIGGER trg_sync_part_cross_reference_mfr
        BEFORE INSERT OR UPDATE ON public.part_cross_reference
        FOR EACH ROW
        EXECUTE FUNCTION public.trg_sync_mfr_common();
        """
    )

    op.execute("DROP FUNCTION IF EXISTS public.trg_sync_part_cross_reference_mfr_domains()")
    op.execute("DROP FUNCTION IF EXISTS public.canonical_aftermarket_brand_name(uuid)")
    op.execute("DROP FUNCTION IF EXISTS public.resolve_aftermarket_brand_id(text)")

    op.execute(
        "ALTER TABLE public.part_cross_reference DROP CONSTRAINT IF EXISTS fk_part_cross_reference_aftermarket_brand_id_aftermarket_brands"
    )
    op.execute("DROP INDEX IF EXISTS public.idx_part_cross_reference_aftermarket_brand_id")
    op.execute("ALTER TABLE public.part_cross_reference DROP COLUMN IF EXISTS aftermarket_brand_id")
