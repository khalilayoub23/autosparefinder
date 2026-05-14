"""align scraper_api_calls schema with runtime writer

Revision ID: 0051_scraper_api_calls_schema_alignment
Revises: 0050_search_latency_indexes
Create Date: 2026-05-12
"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "0051_scraper_api_calls_align"
down_revision = "0050_search_latency_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE public.scraper_api_calls
            ADD COLUMN IF NOT EXISTS url character varying(500),
            ADD COLUMN IF NOT EXISTS part_id uuid,
            ADD COLUMN IF NOT EXISTS called_at timestamp without time zone;
        """
    )

    op.execute(
        """
        UPDATE public.scraper_api_calls
        SET called_at = COALESCE(called_at, created_at),
            created_at = COALESCE(created_at, called_at)
        WHERE called_at IS NULL OR created_at IS NULL;
        """
    )

    op.execute(
        """
        ALTER TABLE public.scraper_api_calls
            ALTER COLUMN created_at SET DEFAULT NOW(),
            ALTER COLUMN called_at SET DEFAULT NOW();
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_api_calls_called
        ON public.scraper_api_calls (called_at);
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_api_calls_part_id
        ON public.scraper_api_calls (part_id);
        """
    )

    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM information_schema.table_constraints
                WHERE table_schema = 'public'
                  AND table_name = 'scraper_api_calls'
                  AND constraint_name = 'fk_scraper_api_calls_part_id'
            ) THEN
                ALTER TABLE public.scraper_api_calls
                    ADD CONSTRAINT fk_scraper_api_calls_part_id
                    FOREIGN KEY (part_id) REFERENCES public.parts_catalog(id)
                    ON DELETE SET NULL;
            END IF;
        END$$;
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE public.scraper_api_calls DROP CONSTRAINT IF EXISTS fk_scraper_api_calls_part_id;")
    op.execute("DROP INDEX IF EXISTS public.idx_api_calls_part_id;")
    op.execute("DROP INDEX IF EXISTS public.idx_api_calls_called;")
    op.execute("ALTER TABLE public.scraper_api_calls DROP COLUMN IF EXISTS part_id;")
    op.execute("ALTER TABLE public.scraper_api_calls DROP COLUMN IF EXISTS url;")
    op.execute("ALTER TABLE public.scraper_api_calls DROP COLUMN IF EXISTS called_at;")
