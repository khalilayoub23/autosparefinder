"""Add pgvector extension and embedding columns to parts_catalog

Revision ID: 0009_add_pgvector
Revises: 0008_add_master_enriched
Create Date: 2026-03-18
"""
from alembic import op

revision = "0009_add_pgvector"
down_revision = "0008_add_master_enriched"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Install pgvector extension (idempotent)
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # 2. Embedding columns — nullable so existing rows are unaffected
    op.execute(
        "ALTER TABLE parts_catalog "
        "ADD COLUMN IF NOT EXISTS embedding vector(768)"
    )
    op.execute(
        "ALTER TABLE parts_catalog "
        "ADD COLUMN IF NOT EXISTS image_embedding vector(512)"
    )

    # 3. IVFFlat index on text embedding (cosine ops)
    #    lists=100 ≈ sqrt(278K rows); partial — skip nulls at launch
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_parts_catalog_embedding
        ON parts_catalog
        USING ivfflat (embedding vector_cosine_ops)
        WITH (lists = 100)
        WHERE embedding IS NOT NULL
    """)

    # 4. IVFFlat index on image embedding (cosine ops)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_parts_catalog_image_embedding
        ON parts_catalog
        USING ivfflat (image_embedding vector_cosine_ops)
        WITH (lists = 100)
        WHERE image_embedding IS NOT NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_parts_catalog_image_embedding")
    op.execute("DROP INDEX IF EXISTS idx_parts_catalog_embedding")
    op.execute(
        "ALTER TABLE parts_catalog DROP COLUMN IF EXISTS image_embedding"
    )
    op.execute(
        "ALTER TABLE parts_catalog DROP COLUMN IF EXISTS embedding"
    )
    # Intentionally NOT dropping the vector extension —
    # other objects may depend on it and reinstalling is cheap.
