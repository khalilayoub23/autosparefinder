"""Add embedding_generated flag to parts_images

Revision ID: 0012_parts_images_embedding_generated
Revises: 0011_search_misses_user_notified
Create Date: 2026-03-18

"""
from alembic import op

revision = '0012_parts_images_emb_generated'
down_revision = '0011_search_misses_user_notified'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        ALTER TABLE parts_images
            ADD COLUMN IF NOT EXISTS embedding_generated BOOLEAN NOT NULL DEFAULT FALSE
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_parts_images_embedding_pending
            ON parts_images (part_id)
            WHERE embedding_generated = FALSE
    """)


def downgrade():
    op.execute("DROP INDEX IF EXISTS idx_parts_images_embedding_pending")
    op.execute("ALTER TABLE parts_images DROP COLUMN IF EXISTS embedding_generated")
