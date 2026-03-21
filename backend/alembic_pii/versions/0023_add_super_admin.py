"""Add is_super_admin to users

Revision ID: 0023
Revises: 0022
Create Date: 2026-03-21
"""
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        ALTER TABLE users
        ADD COLUMN IF NOT EXISTS is_super_admin BOOLEAN NOT NULL DEFAULT FALSE;
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_users_is_super_admin
        ON users(is_super_admin);
        """
    )


def downgrade():
    op.execute(
        """
        DROP INDEX IF EXISTS ix_users_is_super_admin;
        """
    )
    op.execute(
        """
        ALTER TABLE users
        DROP COLUMN IF EXISTS is_super_admin;
        """
    )
