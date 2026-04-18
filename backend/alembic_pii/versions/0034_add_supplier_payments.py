"""Add supplier_payments table for supplier payout lifecycle tracking.

Revision ID: 0034_supplier_payments
Revises: 0033_pay_legacy_cols
Create Date: 2026-04-17
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0034_supplier_payments"
down_revision = "0033_pay_legacy_cols"
branch_labels = None
depends_on = None


def _table_exists(table: str) -> bool:
    conn = op.get_bind()
    result = conn.execute(
        sa.text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema='public' AND table_name=:t"
        ),
        {"t": table},
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
    if not _table_exists("supplier_payments"):
        op.create_table(
            "supplier_payments",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
            sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("supplier_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("supplier_name", sa.String(length=255), nullable=False),
            sa.Column("amount_ils", sa.Numeric(12, 2), nullable=False),
            sa.Column("currency", sa.String(length=3), nullable=False, server_default="ILS"),
            sa.Column("status", sa.String(length=30), nullable=False, server_default="pending"),
            sa.Column("provider", sa.String(length=50), nullable=False, server_default="stripe"),
            sa.Column("provider_payment_id", sa.String(length=255), nullable=True),
            sa.Column("provider_reference", sa.String(length=255), nullable=True),
            sa.Column("payment_method", sa.String(length=50), nullable=True),
            sa.Column("tracking_number", sa.String(length=100), nullable=True),
            sa.Column("tracking_url", sa.String(length=500), nullable=True),
            sa.Column("failure_reason", sa.Text(), nullable=True),
            sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
            sa.Column("paid_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
            sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.UniqueConstraint("order_id", "supplier_id", name="uq_supplier_payments_order_supplier"),
        )

    if not _index_exists("ix_supplier_payments_order_id"):
        op.create_index("ix_supplier_payments_order_id", "supplier_payments", ["order_id"])
    if not _index_exists("ix_supplier_payments_user_id"):
        op.create_index("ix_supplier_payments_user_id", "supplier_payments", ["user_id"])
    if not _index_exists("ix_supplier_payments_status"):
        op.create_index("ix_supplier_payments_status", "supplier_payments", ["status"])
    if not _index_exists("ix_supplier_payments_paid_at"):
        op.create_index("ix_supplier_payments_paid_at", "supplier_payments", ["paid_at"])


def downgrade() -> None:
    # Keep downgrade non-destructive in production.
    pass
