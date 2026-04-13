"""reconcile payments schema to match ORM model

Revision ID: 0029
Revises: 0028
Create Date: 2026-04-08 00:10:00
"""

from alembic import op
import sqlalchemy as sa


revision = "0029b"
down_revision = "0029"
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


def upgrade() -> None:
    # Rename legacy columns
    if _col_exists("payments", "amount_ils") and not _col_exists("payments", "amount"):
        op.execute(sa.text("ALTER TABLE payments RENAME COLUMN amount_ils TO amount"))

    if _col_exists("payments", "provider_transaction_id") and not _col_exists("payments", "payment_intent_id"):
        op.execute(sa.text("ALTER TABLE payments RENAME COLUMN provider_transaction_id TO payment_intent_id"))

    if _col_exists("payments", "provider") and not _col_exists("payments", "payment_method"):
        op.execute(sa.text("ALTER TABLE payments RENAME COLUMN provider TO payment_method"))

    if _col_exists("payments", "last_four") and not _col_exists("payments", "last_4_digits"):
        op.execute(sa.text("ALTER TABLE payments RENAME COLUMN last_four TO last_4_digits"))

    # Add missing columns expected by ORM/routes
    if not _col_exists("payments", "stripe_customer_id"):
        op.add_column("payments", sa.Column("stripe_customer_id", sa.String(length=255), nullable=True))
    if not _col_exists("payments", "paid_at"):
        op.add_column("payments", sa.Column("paid_at", sa.DateTime(), nullable=True))
    if not _col_exists("payments", "refunded_at"):
        op.add_column("payments", sa.Column("refunded_at", sa.DateTime(), nullable=True))
    if not _col_exists("payments", "refund_amount"):
        op.add_column("payments", sa.Column("refund_amount", sa.Numeric(10, 2), nullable=True))
    if not _col_exists("payments", "refund_reason"):
        op.add_column("payments", sa.Column("refund_reason", sa.String(length=255), nullable=True))

    # Ensure updated_at exists for ORM onupdate
    if not _col_exists("payments", "updated_at"):
        op.add_column("payments", sa.Column("updated_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    # Intentionally minimal/non-destructive for live data safety.
    pass
