"""Reconcile legacy payments schema with current ORM fields.

This migration keeps backward-compatibility columns but adds/backfills the
columns used by the current API code (payment_intent_id, amount, paid_at, etc.).
It also normalizes legacy order/payment statuses.

Revision ID: 0028
Revises: 0027
Create Date: 2026-04-05
"""

from alembic import op
import sqlalchemy as sa

revision = "0028"
down_revision = "0027b"
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


def upgrade() -> None:
    # ---------------------------------------------------------------- orders
    # Legacy deployments used status='pending'. Align with API status names.
    if _col_exists("orders", "status"):
        op.execute(sa.text(
            "UPDATE orders SET status='pending_payment' WHERE status='pending'"
        ))
        op.execute(sa.text(
            "ALTER TABLE orders ALTER COLUMN status SET DEFAULT 'pending_payment'"
        ))

    # Reconcile stale orders: paid/refunded payment rows should not stay pending.
    if _col_exists("orders", "status") and _col_exists("payments", "status") and _col_exists("payments", "order_id"):
        op.execute(sa.text(
            "UPDATE orders o "
            "SET status='paid', updated_at=NOW() "
            "WHERE o.status IN ('pending', 'pending_payment') "
            "AND EXISTS ("
            "  SELECT 1 FROM payments p "
            "  WHERE p.order_id = o.id AND p.status IN ('paid', 'refunded', 'succeeded')"
            ")"
        ))

    # -------------------------------------------------------------- payments
    if not _col_exists("payments", "payment_intent_id"):
        op.add_column("payments", sa.Column("payment_intent_id", sa.String(255), nullable=True))
    if not _col_exists("payments", "amount"):
        op.add_column("payments", sa.Column("amount", sa.Numeric(10, 2), nullable=True))
    if not _col_exists("payments", "payment_method"):
        op.add_column("payments", sa.Column("payment_method", sa.String(50), nullable=True))
    if not _col_exists("payments", "stripe_customer_id"):
        op.add_column("payments", sa.Column("stripe_customer_id", sa.String(255), nullable=True))
    if not _col_exists("payments", "last_4_digits"):
        op.add_column("payments", sa.Column("last_4_digits", sa.String(4), nullable=True))
    if not _col_exists("payments", "paid_at"):
        op.add_column("payments", sa.Column("paid_at", sa.DateTime(), nullable=True))
    if not _col_exists("payments", "refunded_at"):
        op.add_column("payments", sa.Column("refunded_at", sa.DateTime(), nullable=True))
    if not _col_exists("payments", "refund_amount"):
        op.add_column("payments", sa.Column("refund_amount", sa.Numeric(10, 2), nullable=True))
    if not _col_exists("payments", "refund_reason"):
        op.add_column("payments", sa.Column("refund_reason", sa.String(255), nullable=True))

    # Data backfill from legacy columns.
    if _col_exists("payments", "amount") and _col_exists("payments", "amount_ils"):
        op.execute(sa.text(
            "UPDATE payments SET amount = COALESCE(amount, amount_ils)"
        ))

    if _col_exists("payments", "payment_intent_id") and _col_exists("payments", "provider_transaction_id"):
        op.execute(sa.text(
            "UPDATE payments SET payment_intent_id = COALESCE(payment_intent_id, provider_transaction_id)"
        ))

    if _col_exists("payments", "payment_method") and _col_exists("payments", "provider"):
        op.execute(sa.text(
            "UPDATE payments SET payment_method = COALESCE(payment_method, provider)"
        ))

    if _col_exists("payments", "last_4_digits") and _col_exists("payments", "last_four"):
        op.execute(sa.text(
            "UPDATE payments SET last_4_digits = COALESCE(last_4_digits, last_four)"
        ))

    # Normalize legacy payment status names.
    if _col_exists("payments", "status"):
        op.execute(sa.text(
            "UPDATE payments SET status='paid' WHERE status IN ('succeeded', 'success')"
        ))

    if _col_exists("payments", "paid_at") and _col_exists("payments", "created_at") and _col_exists("payments", "status"):
        op.execute(sa.text(
            "UPDATE payments "
            "SET paid_at = COALESCE(paid_at, created_at) "
            "WHERE paid_at IS NULL AND status IN ('paid', 'refunded')"
        ))

    if _col_exists("payments", "refunded_at") and _col_exists("payments", "updated_at") and _col_exists("payments", "status"):
        op.execute(sa.text(
            "UPDATE payments "
            "SET refunded_at = COALESCE(refunded_at, updated_at) "
            "WHERE refunded_at IS NULL AND status = 'refunded'"
        ))

    if _col_exists("payments", "refund_amount") and _col_exists("payments", "amount") and _col_exists("payments", "status"):
        op.execute(sa.text(
            "UPDATE payments "
            "SET refund_amount = COALESCE(refund_amount, amount) "
            "WHERE refund_amount IS NULL AND status = 'refunded'"
        ))

    # Enforce ORM-required non-null amount after backfill.
    if _col_exists("payments", "amount"):
        op.execute(sa.text("UPDATE payments SET amount = 0 WHERE amount IS NULL"))
        op.alter_column(
            "payments",
            "amount",
            existing_type=sa.Numeric(10, 2),
            nullable=False,
        )

    # Helpful indexes used by verify-session lookups and payment history.
    if _col_exists("payments", "payment_intent_id") and not _idx_exists("payments", "ix_payments_payment_intent_id"):
        op.create_index("ix_payments_payment_intent_id", "payments", ["payment_intent_id"], unique=False)

    if _col_exists("payments", "paid_at") and not _idx_exists("payments", "ix_payments_paid_at"):
        op.create_index("ix_payments_paid_at", "payments", ["paid_at"], unique=False)


def downgrade() -> None:
    # Intentionally non-destructive; legacy and new columns are kept.
    pass
