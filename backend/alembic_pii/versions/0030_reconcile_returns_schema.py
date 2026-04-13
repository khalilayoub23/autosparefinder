"""Reconcile legacy returns schema with runtime ORM expectations.

Ensures missing returns columns exist across drifted deployments where the
initial PII schema lacked return_number/original_amount/refund fields.

Revision ID: 0030_reconcile_returns_schema
Revises: 0029
Create Date: 2026-04-06
"""

from alembic import op
import sqlalchemy as sa


revision = "0030_reconcile_returns_schema"
down_revision = "0029b"
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


def _unique_on_col_exists(table: str, column: str) -> bool:
    conn = op.get_bind()
    result = conn.execute(
        sa.text(
            "SELECT 1 "
            "FROM pg_constraint c "
            "JOIN pg_class t ON t.oid = c.conrelid "
            "JOIN pg_namespace n ON n.oid = t.relnamespace "
            "JOIN unnest(c.conkey) AS k(attnum) ON TRUE "
            "JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = k.attnum "
            "WHERE n.nspname='public' "
            "  AND t.relname = :t "
            "  AND c.contype IN ('u', 'p') "
            "  AND a.attname = :c "
            "LIMIT 1"
        ),
        {"t": table, "c": column},
    )
    return result.fetchone() is not None


def _null_count(table: str, column: str) -> int:
    conn = op.get_bind()
    result = conn.execute(sa.text(f"SELECT COUNT(*) FROM {table} WHERE {column} IS NULL"))
    return int(result.scalar() or 0)


def upgrade() -> None:
    if not _col_exists("returns", "return_number"):
        op.add_column("returns", sa.Column("return_number", sa.String(length=20), nullable=True))
    if not _col_exists("returns", "description"):
        op.add_column("returns", sa.Column("description", sa.Text(), nullable=True))
    if not _col_exists("returns", "original_amount"):
        op.add_column("returns", sa.Column("original_amount", sa.Numeric(10, 2), nullable=True))
    if not _col_exists("returns", "refund_amount"):
        op.add_column("returns", sa.Column("refund_amount", sa.Numeric(10, 2), nullable=True))
    if not _col_exists("returns", "refund_percentage"):
        op.add_column("returns", sa.Column("refund_percentage", sa.Numeric(5, 2), nullable=True))
    if not _col_exists("returns", "handling_fee"):
        op.add_column("returns", sa.Column("handling_fee", sa.Numeric(10, 2), nullable=True))
    if not _col_exists("returns", "tracking_number"):
        op.add_column("returns", sa.Column("tracking_number", sa.String(length=100), nullable=True))
    if not _col_exists("returns", "tracking_url"):
        op.add_column("returns", sa.Column("tracking_url", sa.String(length=500), nullable=True))
    if not _col_exists("returns", "rejection_reason"):
        op.add_column("returns", sa.Column("rejection_reason", sa.String(length=255), nullable=True))
    if not _col_exists("returns", "requested_at"):
        op.add_column(
            "returns",
            sa.Column("requested_at", sa.DateTime(), nullable=True, server_default=sa.text("now()")),
        )
    if not _col_exists("returns", "rejected_at"):
        op.add_column("returns", sa.Column("rejected_at", sa.DateTime(), nullable=True))
    if not _col_exists("returns", "completed_at"):
        op.add_column("returns", sa.Column("completed_at", sa.DateTime(), nullable=True))

    if _col_exists("returns", "return_number"):
        op.execute(
            sa.text(
                "UPDATE returns "
                "SET return_number = 'RET-' || upper(substr(md5(id::text), 1, 12)) "
                "WHERE return_number IS NULL OR btrim(return_number) = ''"
            )
        )

    if _col_exists("returns", "original_amount") and _col_exists("orders", "total_amount"):
        op.execute(
            sa.text(
                "UPDATE returns r "
                "SET original_amount = COALESCE(r.original_amount, o.total_amount, 0) "
                "FROM orders o "
                "WHERE r.order_id = o.id"
            )
        )
    if _col_exists("returns", "original_amount"):
        op.execute(sa.text("UPDATE returns SET original_amount = 0 WHERE original_amount IS NULL"))

    if _col_exists("returns", "refund_amount") and _col_exists("returns", "refund_amount_ils"):
        op.execute(
            sa.text(
                "UPDATE returns "
                "SET refund_amount = COALESCE(refund_amount, refund_amount_ils) "
                "WHERE refund_amount IS NULL"
            )
        )

    if _col_exists("returns", "requested_at") and _col_exists("returns", "created_at"):
        op.execute(
            sa.text(
                "UPDATE returns "
                "SET requested_at = COALESCE(requested_at, created_at, now()) "
                "WHERE requested_at IS NULL"
            )
        )

    if _col_exists("returns", "return_number") and _null_count("returns", "return_number") == 0:
        op.alter_column("returns", "return_number", existing_type=sa.String(length=20), nullable=False)
    if _col_exists("returns", "original_amount") and _null_count("returns", "original_amount") == 0:
        op.alter_column("returns", "original_amount", existing_type=sa.Numeric(10, 2), nullable=False)
    if _col_exists("returns", "requested_at") and _null_count("returns", "requested_at") == 0:
        op.alter_column("returns", "requested_at", existing_type=sa.DateTime(), nullable=False)

    if _col_exists("returns", "return_number"):
        if not _unique_on_col_exists("returns", "return_number") and not _idx_exists("returns", "uq_returns_return_number"):
            op.create_index("uq_returns_return_number", "returns", ["return_number"], unique=True)


def downgrade() -> None:
    # Non-destructive on purpose.
    pass
