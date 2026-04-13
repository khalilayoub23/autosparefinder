"""Ensure OAuth columns exist on users table.

This is a safety reconciliation migration for environments where prior
OAuth-column migrations were skipped during branch rewires.

Revision ID: 0032_add_users_oauth_columns
Revises: 0031_reconcile_chat_schema
Create Date: 2026-04-13
"""

from alembic import op
import sqlalchemy as sa


revision = "0032_add_users_oauth_columns"
down_revision = "0031_reconcile_chat_schema"
branch_labels = None
depends_on = None


def _col_exists(table: str, column: str) -> bool:
    conn = op.get_bind()
    result = conn.execute(
        sa.text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name=:t AND column_name=:c"
        ),
        {"t": table, "c": column},
    )
    return result.fetchone() is not None


def _idx_exists(index_name: str) -> bool:
    conn = op.get_bind()
    result = conn.execute(
        sa.text(
            "SELECT 1 FROM pg_indexes "
            "WHERE schemaname='public' AND indexname=:i"
        ),
        {"i": index_name},
    )
    return result.fetchone() is not None


def upgrade() -> None:
    if not _col_exists("users", "oauth_provider"):
        op.add_column("users", sa.Column("oauth_provider", sa.String(length=32), nullable=True))

    if not _col_exists("users", "oauth_id"):
        op.add_column("users", sa.Column("oauth_id", sa.String(length=255), nullable=True))

    if not _idx_exists("ix_users_oauth_id"):
        op.create_index("ix_users_oauth_id", "users", ["oauth_id"])

    # OAuth accounts may not have phone/password set.
    op.alter_column("users", "phone", nullable=True)
    op.alter_column("users", "password_hash", nullable=True)


def downgrade() -> None:
    if _idx_exists("ix_users_oauth_id"):
        op.drop_index("ix_users_oauth_id", table_name="users")

    if _col_exists("users", "oauth_id"):
        op.drop_column("users", "oauth_id")

    if _col_exists("users", "oauth_provider"):
        op.drop_column("users", "oauth_provider")
