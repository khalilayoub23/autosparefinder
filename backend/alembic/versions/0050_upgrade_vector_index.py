"""upgrade vector index and dimension for sandbox compatibility

Upgrade parts_catalog.embedding to 1536 dimensions and use HNSW index.
This ensures compatibility with sandbox resource limits while maintaining high search quality.

Revision ID: 0050
Revises: 0049_filter_hierarchy_indexes
Create Date: 2026-04-18
"""

from alembic import op

revision = "0050"
down_revision = "0049_filter_hierarchy_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Drop existing vector index
    op.execute("DROP INDEX IF EXISTS idx_parts_catalog_embedding")

    # 2. Re-create column with 1536 dimensions
    op.execute("ALTER TABLE parts_catalog DROP COLUMN IF EXISTS embedding")
    op.execute("ALTER TABLE parts_catalog ADD COLUMN embedding vector(1536)")

    # 3. Create HNSW index (more robust for sandbox environments)
    op.execute("""
        CREATE INDEX idx_parts_catalog_embedding
        ON parts_catalog
        USING hnsw (embedding vector_cosine_ops)
        WHERE embedding IS NOT NULL
    """)


def downgrade() -> None:
    # 1. Drop HNSW index
    op.execute("DROP INDEX IF EXISTS idx_parts_catalog_embedding")

    # 2. Revert to 3072 dimensions (original state from migration 0027)
    op.execute("ALTER TABLE parts_catalog DROP COLUMN IF EXISTS embedding")
    op.execute("ALTER TABLE parts_catalog ADD COLUMN embedding vector(3072)")

    # 3. Restore HNSW index for 3072 dims (as per 0027 original state)
    op.execute("""
        CREATE INDEX idx_parts_catalog_embedding
        ON parts_catalog
        USING hnsw (embedding vector_cosine_ops)
        WHERE embedding IS NOT NULL
    """)
