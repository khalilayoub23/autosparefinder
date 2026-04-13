"""Add oauth_provider and oauth_id to users; make phone/password_hash nullable

Revision ID: 0027
Revises: 0026
Create Date: 2026-04-01
"""
from alembic import op
import sqlalchemy as sa

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("oauth_provider", sa.String(32), nullable=True))
    op.add_column("users", sa.Column("oauth_id", sa.String(255), nullable=True))
    op.create_index("ix_users_oauth_id", "users", ["oauth_id"])
    # Make phone and password_hash nullable so OAuth users don't need them
    op.alter_column("users", "phone", nullable=True)
    op.alter_column("users", "password_hash", nullable=True)


def downgrade() -> None:
    op.drop_index("ix_users_oauth_id", table_name="users")
    op.drop_column("users", "oauth_id")
    op.drop_column("users", "oauth_provider")
    op.alter_column("users", "phone", nullable=False)
    op.alter_column("users", "password_hash", nullable=False)
