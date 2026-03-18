"""Add search_misses table for self-healing catalog

Revision ID: 0010_add_search_misses
Revises: 0009_add_pgvector
Create Date: 2026-03-18
"""
from alembic import op

revision = "0010_add_search_misses"
down_revision = "0009_add_pgvector"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS search_misses (
            id                   UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            query                TEXT        NOT NULL,
            normalized_query     TEXT        NOT NULL,
            category             VARCHAR(100),
            vehicle_manufacturer VARCHAR(100),
            miss_count           INTEGER     NOT NULL DEFAULT 1,
            last_seen_at         TIMESTAMP   NOT NULL DEFAULT NOW(),
            first_seen_at        TIMESTAMP   NOT NULL DEFAULT NOW(),
            triggered_scrape     BOOLEAN     NOT NULL DEFAULT FALSE,
            created_at           TIMESTAMP   NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_search_misses_normalized_query UNIQUE (normalized_query)
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_search_misses_miss_count_triggered
        ON search_misses (miss_count, triggered_scrape)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_search_misses_miss_count_triggered")
    op.execute("DROP TABLE IF EXISTS search_misses")
