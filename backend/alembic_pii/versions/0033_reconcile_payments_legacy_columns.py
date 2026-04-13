"""Reconcile legacy payment columns expected by ORM.

Some deployments have a modernized payments table without legacy columns
(provider/provider_transaction_id/amount_ils/last_four), while the ORM still
maps them. Ensure these columns exist so ORM queries do not fail.

Revision ID: 0033_pay_legacy_cols
Revises: 0032_add_users_oauth_columns
Create Date: 2026-04-13
"""

from alembic import op
import sqlalchemy as sa


revision = "0033_pay_legacy_cols"
down_revision = "0032_add_users_oauth_columns"
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


def _null_count(table: str, column: str) -> int:
    conn = op.get_bind()
    result = conn.execute(sa.text(f"SELECT COUNT(*) FROM {table} WHERE {column} IS NULL"))
    return int(result.scalar() or 0)


def upgrade() -> None:
    if not _col_exists("payments", "provider"):
        op.add_column("payments", sa.Column("provider", sa.String(length=50), nullable=True))

    if not _col_exists("payments", "provider_transaction_id"):
        op.add_column("payments", sa.Column("provider_transaction_id", sa.String(length=255), nullable=True))

    if not _col_exists("payments", "last_four"):
        op.add_column("payments", sa.Column("last_four", sa.String(length=4), nullable=True))

    if not _col_exists("payments", "amount_ils"):
        op.add_column("payments", sa.Column("amount_ils", sa.Numeric(12, 2), nullable=True))

    # Backfill legacy columns from canonical columns where possible.
    if _col_exists("payments", "provider") and _col_exists("payments", "payment_method"):
        op.execute(sa.text(
            "UPDATE payments SET provider = payment_method "
            "WHERE provider IS NULL AND payment_method IS NOT NULL"
        ))

    if _col_exists("payments", "provider_transaction_id") and _col_exists("payments", "payment_intent_id"):
        op.execute(sa.text(
            "UPDATE payments SET provider_transaction_id = payment_intent_id "
            "WHERE provider_transaction_id IS NULL AND payment_intent_id IS NOT NULL"
        ))

    if _col_exists("payments", "last_four") and _col_exists("payments", "last_4_digits"):
        op.execute(sa.text(
            "UPDATE payments SET last_four = last_4_digits "
            "WHERE last_four IS NULL AND last_4_digits IS NOT NULL"
        ))

    if _col_exists("payments", "amount_ils") and _col_exists("payments", "amount"):
        op.execute(sa.text(
            "UPDATE payments SET amount_ils = amount "
            "WHERE amount_ils IS NULL AND amount IS NOT NULL"
        ))
        op.execute(sa.text("UPDATE payments SET amount_ils = 0 WHERE amount_ils IS NULL"))
        if _null_count("payments", "amount_ils") == 0:
            op.alter_column("payments", "amount_ils", nullable=False)


def downgrade() -> None:
    # Keep downgrade non-destructive in production.
    pass
