"""create part_diagram_cache table for vision identification cache

Revision ID: 0048_part_diagram_cache
Revises: 0047_mfr_fk_p5_domain_xor
Create Date: 2026-04-16
"""

from alembic import op


revision = "0048_part_diagram_cache"
down_revision = "0047_mfr_fk_p5_domain_xor"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS public.part_diagram_cache (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          image_hash VARCHAR(64) NOT NULL,
          vehicle_make VARCHAR(100),
          vehicle_model VARCHAR(100),
          vehicle_year VARCHAR(10),
          part_name_he VARCHAR(200) NOT NULL,
          part_name_en VARCHAR(200),
          possible_names TEXT[],
          confidence NUMERIC(4,3),
          catalog_part_id UUID,
          times_seen INTEGER NOT NULL DEFAULT 1,
          created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT now(),
          updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT now()
        )
        """
    )

    op.execute("ALTER TABLE public.part_diagram_cache ADD COLUMN IF NOT EXISTS id UUID DEFAULT gen_random_uuid()")
    op.execute("ALTER TABLE public.part_diagram_cache ADD COLUMN IF NOT EXISTS image_hash VARCHAR(64)")
    op.execute("ALTER TABLE public.part_diagram_cache ADD COLUMN IF NOT EXISTS vehicle_make VARCHAR(100)")
    op.execute("ALTER TABLE public.part_diagram_cache ADD COLUMN IF NOT EXISTS vehicle_model VARCHAR(100)")
    op.execute("ALTER TABLE public.part_diagram_cache ADD COLUMN IF NOT EXISTS vehicle_year VARCHAR(10)")
    op.execute("ALTER TABLE public.part_diagram_cache ADD COLUMN IF NOT EXISTS part_name_he VARCHAR(200)")
    op.execute("ALTER TABLE public.part_diagram_cache ADD COLUMN IF NOT EXISTS part_name_en VARCHAR(200)")
    op.execute("ALTER TABLE public.part_diagram_cache ADD COLUMN IF NOT EXISTS possible_names TEXT[]")
    op.execute("ALTER TABLE public.part_diagram_cache ADD COLUMN IF NOT EXISTS confidence NUMERIC(4,3)")
    op.execute("ALTER TABLE public.part_diagram_cache ADD COLUMN IF NOT EXISTS catalog_part_id UUID")
    op.execute("ALTER TABLE public.part_diagram_cache ADD COLUMN IF NOT EXISTS times_seen INTEGER")
    op.execute("ALTER TABLE public.part_diagram_cache ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITHOUT TIME ZONE")
    op.execute("ALTER TABLE public.part_diagram_cache ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITHOUT TIME ZONE")

    op.execute("UPDATE public.part_diagram_cache SET times_seen = 1 WHERE times_seen IS NULL")
    op.execute("UPDATE public.part_diagram_cache SET created_at = now() WHERE created_at IS NULL")
    op.execute("UPDATE public.part_diagram_cache SET updated_at = now() WHERE updated_at IS NULL")

    op.execute("ALTER TABLE public.part_diagram_cache ALTER COLUMN image_hash SET NOT NULL")
    op.execute("ALTER TABLE public.part_diagram_cache ALTER COLUMN part_name_he SET NOT NULL")
    op.execute("ALTER TABLE public.part_diagram_cache ALTER COLUMN times_seen SET DEFAULT 1")
    op.execute("ALTER TABLE public.part_diagram_cache ALTER COLUMN times_seen SET NOT NULL")
    op.execute("ALTER TABLE public.part_diagram_cache ALTER COLUMN created_at SET DEFAULT now()")
    op.execute("ALTER TABLE public.part_diagram_cache ALTER COLUMN created_at SET NOT NULL")
    op.execute("ALTER TABLE public.part_diagram_cache ALTER COLUMN updated_at SET DEFAULT now()")
    op.execute("ALTER TABLE public.part_diagram_cache ALTER COLUMN updated_at SET NOT NULL")

    op.execute(
        """
        DO $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conname = 'part_diagram_cache_pkey'
          ) THEN
            ALTER TABLE public.part_diagram_cache
            ADD CONSTRAINT part_diagram_cache_pkey PRIMARY KEY (id);
          END IF;
        END $$;
        """
    )

    op.execute(
        """
        DO $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conname = 'uq_diagram_cache'
          ) THEN
            ALTER TABLE public.part_diagram_cache
            ADD CONSTRAINT uq_diagram_cache
            UNIQUE (image_hash, vehicle_make, vehicle_model);
          END IF;
        END $$;
        """
    )

    op.execute(
        """
        DO $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conname = 'fk_part_diagram_cache_catalog_part_id'
          ) THEN
            ALTER TABLE public.part_diagram_cache
              ADD CONSTRAINT fk_part_diagram_cache_catalog_part_id
              FOREIGN KEY (catalog_part_id)
              REFERENCES public.parts_catalog(id)
              ON DELETE SET NULL;
          END IF;
        END $$;
        """
    )

    op.execute("CREATE INDEX IF NOT EXISTS ix_part_diagram_cache_image_hash ON public.part_diagram_cache (image_hash)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_part_diagram_cache_vehicle_make ON public.part_diagram_cache (vehicle_make)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_part_diagram_cache_catalog_part_id ON public.part_diagram_cache (catalog_part_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_diagram_cache_make_part ON public.part_diagram_cache (vehicle_make, part_name_he)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS public.part_diagram_cache")
