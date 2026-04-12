"""reconcile returns schema to match ORM model

Revision ID: 0028
Revises: 0027
Create Date: 2026-04-08 00:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "0028"
down_revision = "0027"
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


def _index_exists(index_name: str) -> bool:
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
    # rename legacy refund column if needed
    if _col_exists("returns", "refund_amount_ils") and not _col_exists("returns", "refund_amount"):
        op.execute(sa.text("ALTER TABLE returns RENAME COLUMN refund_amount_ils TO refund_amount"))

    # add missing columns expected by ORM / routes
    if not _col_exists("returns", "return_number"):
        op.add_column("returns", sa.Column("return_number", sa.String(length=20), nullable=True))
    if not _col_exists("returns", "description"):
        op.add_column("returns", sa.Column("description", sa.Text(), nullable=True))
    if not _col_exists("returns", "original_amount"):
        op.add_column("returns", sa.Column("original_amount", sa.Numeric(10, 2), nullable=True))
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
        op.add_column("returns", sa.Column("requested_at", sa.DateTime(), nullable=True))
    if not _col_exists("returns", "rejected_at"):
        op.add_column("returns", sa.Column("rejected_at", sa.DateTime(), nullable=True))
    if not _col_exists("returns", "completed_at"):
        op.add_column("returns", sa.Column("completed_at", sa.DateTime(), nullable=True))

    # backfill requested_at from created_at where available
    if _col_exists("returns", "requested_at") and _col_exists("returns", "created_at"):
        op.execute(sa.text("UPDATE returns SET requested_at = created_at WHERE requested_at IS NULL"))
    if _col_exists("returns", "requested_at"):
        op.execute(sa.text("UPDATE returns SET requested_at = NOW() WHERE requested_at IS NULL"))

    # backfill original_amount from order totals or refund amount
    if _col_exists("returns", "original_amount") and _col_exists("orders", "total_amount"):
        op.execute(
            sa.text(
                """
                UPDATE returns r
                SET original_amount = o.total_amount
                FROM orders o
                WHERE r.order_id = o.id AND r.original_amount IS NULL
                """
            )
        )
    if _col_exists("returns", "original_amount") and _col_exists("returns", "refund_amount"):
        op.execute(sa.text("UPDATE returns SET original_amount = refund_amount WHERE original_amount IS NULL AND refund_amount IS NOT NULL"))
    if _col_exists("returns", "original_amount"):
        op.execute(sa.text("UPDATE returns SET original_amount = 0 WHERE original_amount IS NULL"))

    # backfill return_number and enforce uniqueness
    if _col_exists("returns", "return_number"):
        op.execute(
            sa.text(
                """
                UPDATE returns
                SET return_number = 'RET-' || TO_CHAR(COALESCE(requested_at, NOW()), 'YYYY') || '-' ||
                                   UPPER(SUBSTRING(REPLACE(id::text, '-', ''), 1, 8))
                WHERE return_number IS NULL OR return_number = ''
                """
            )
        )
        if not _index_exists("uq_returns_return_number"):
            op.create_index("uq_returns_return_number", "returns", ["return_number"], unique=True)
        op.execute(sa.text("ALTER TABLE returns ALTER COLUMN return_number SET NOT NULL"))


def downgrade() -> None:
    # Intentionally minimal/non-destructive for live data safety.
    pass
