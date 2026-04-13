"""Reconcile legacy-required payments columns used by runtime inserts.

Ensures `payments.user_id` and `payments.amount_ils` exist and are backfilled,
so checkout inserts remain compatible across old/new schema variants.

Revision ID: 0029
Revises: 0028
Create Date: 2026-04-06
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0029"
down_revision = "0028b"
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


def _idx_exists(table: str, index_name: str) -> bool:
    conn = op.get_bind()
    result = conn.execute(
        sa.text(
            "SELECT 1 FROM pg_indexes "
            "WHERE schemaname='public' AND tablename=:t AND indexname=:i"
        ),
        {"t": table, "i": index_name},
    )
    return result.fetchone() is not None


def _fk_on_column_exists(table: str, column: str) -> bool:
    conn = op.get_bind()
    result = conn.execute(
        sa.text(
            "SELECT 1 "
            "FROM information_schema.table_constraints tc "
            "JOIN information_schema.key_column_usage kcu "
            "  ON tc.constraint_name = kcu.constraint_name "
            " AND tc.table_schema = kcu.table_schema "
            "WHERE tc.table_schema = 'public' "
            "  AND tc.table_name = :t "
            "  AND tc.constraint_type = 'FOREIGN KEY' "
            "  AND kcu.column_name = :c "
            "LIMIT 1"
        ),
        {"t": table, "c": column},
    )
    return result.fetchone() is not None


def _null_count(table: str, column: str) -> int:
    conn = op.get_bind()
    result = conn.execute(
        sa.text(f"SELECT COUNT(*) FROM {table} WHERE {column} IS NULL")
    )
    return int(result.scalar() or 0)


def upgrade() -> None:
    # Ensure legacy-required columns exist.
    if not _col_exists("payments", "user_id"):
        op.add_column("payments", sa.Column("user_id", UUID(as_uuid=True), nullable=True))

    if not _col_exists("payments", "amount_ils"):
        op.add_column("payments", sa.Column("amount_ils", sa.Numeric(12, 2), nullable=True))

    # Backfill user_id from order ownership when missing.
    if _col_exists("payments", "user_id") and _col_exists("payments", "order_id") and _col_exists("orders", "user_id"):
        op.execute(
            sa.text(
                "UPDATE payments p "
                "SET user_id = o.user_id "
                "FROM orders o "
                "WHERE p.order_id = o.id "
                "  AND p.user_id IS NULL"
            )
        )

    # Backfill legacy amount_ils from canonical amount.
    if _col_exists("payments", "amount_ils") and _col_exists("payments", "amount"):
        op.execute(
            sa.text(
                "UPDATE payments "
                "SET amount_ils = COALESCE(amount_ils, amount) "
                "WHERE amount_ils IS NULL"
            )
        )

    # Keep inserts safe even when old rows lacked amount.
    if _col_exists("payments", "amount_ils"):
        op.execute(sa.text("UPDATE payments SET amount_ils = 0 WHERE amount_ils IS NULL"))
        op.alter_column(
            "payments",
            "amount_ils",
            existing_type=sa.Numeric(12, 2),
            nullable=False,
        )

    # Only enforce NOT NULL when fully backfilled.
    if _col_exists("payments", "user_id") and _null_count("payments", "user_id") == 0:
        op.alter_column(
            "payments",
            "user_id",
            existing_type=UUID(as_uuid=True),
            nullable=False,
        )

    # Recreate expected FK/index if missing.
    if _col_exists("payments", "user_id") and not _fk_on_column_exists("payments", "user_id"):
        op.create_foreign_key(
            "payments_user_id_fkey",
            "payments",
            "users",
            ["user_id"],
            ["id"],
        )

    if _col_exists("payments", "user_id") and not _idx_exists("payments", "idx_payments_user_id"):
        op.create_index("idx_payments_user_id", "payments", ["user_id"], unique=False)


def downgrade() -> None:
    # Intentionally non-destructive.
    pass
