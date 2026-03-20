"""Add social_posts table

Revision ID: 0013_add_social_posts
Revises: 0012_parts_images_emb_generated
Create Date: 2026-03-20

"""
from alembic import op

revision = "0013_add_social_posts"
down_revision = "0012_parts_images_emb_generated"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE IF NOT EXISTS social_posts (
            id                UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
            content           TEXT          NOT NULL,
            platforms         TEXT[]        NOT NULL,
            status            VARCHAR(20)   NOT NULL DEFAULT 'draft'
                CONSTRAINT ck_social_posts_status
                    CHECK (status IN ('draft','pending_approval','approved','published','rejected')),
            scheduled_at      TIMESTAMP,
            published_at      TIMESTAMP,
            external_post_ids JSONB         NOT NULL DEFAULT '{}',
            created_by        UUID          NOT NULL,
            approved_by       UUID,
            rejection_reason  TEXT,
            created_at        TIMESTAMP     NOT NULL DEFAULT NOW(),
            updated_at        TIMESTAMP     NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_social_posts_status_scheduled
            ON social_posts (status, scheduled_at)
    """)


def downgrade():
    op.execute("DROP INDEX IF EXISTS ix_social_posts_status_scheduled")
    op.execute("DROP TABLE IF EXISTS social_posts")
