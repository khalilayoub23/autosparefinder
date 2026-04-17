"""enforce domain xor for part_cross_reference manufacturer ids

Revision ID: 0047_mfr_fk_p5_domain_xor
Revises: 0046_mfr_fk_p5_suppliers_guard
Create Date: 2026-04-16
"""

from alembic import op


revision = "0047_mfr_fk_p5_domain_xor"
down_revision = "0046_mfr_fk_p5_suppliers_guard"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Keep one domain owner for part_cross_reference rows.
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
             AND NEW.manufacturer_id IS NULL
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
        ALTER TABLE public.part_cross_reference
        ADD CONSTRAINT ck_part_cross_reference_single_domain_id
        CHECK (
          NOT (manufacturer_id IS NOT NULL AND aftermarket_brand_id IS NOT NULL)
        ) NOT VALID
        """
    )
    op.execute(
        "ALTER TABLE public.part_cross_reference VALIDATE CONSTRAINT ck_part_cross_reference_single_domain_id"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE public.part_cross_reference DROP CONSTRAINT IF EXISTS ck_part_cross_reference_single_domain_id"
    )

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
