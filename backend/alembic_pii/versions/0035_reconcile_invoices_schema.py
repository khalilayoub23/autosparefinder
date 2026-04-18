"""reconcile invoices schema with runtime ORM

Revision ID: 0035_reconcile_invoices
Revises: 0034_supplier_payments
Create Date: 2026-04-17
"""

from alembic import op
import sqlalchemy as sa


revision = "0035_reconcile_invoices"
down_revision = "0034_supplier_payments"
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
    if not _table_exists("invoices"):
        return

    # ORM expects these columns on Invoice.
    if not _col_exists("invoices", "business_number"):
        op.add_column(
            "invoices",
            sa.Column("business_number", sa.String(length=50), nullable=True, server_default="060633880"),
        )

    if not _col_exists("invoices", "pdf_path"):
        op.add_column("invoices", sa.Column("pdf_path", sa.String(length=500), nullable=True))

    if not _col_exists("invoices", "pdf_url"):
        op.add_column("invoices", sa.Column("pdf_url", sa.String(length=500), nullable=True))

    if not _col_exists("invoices", "issued_at"):
        op.add_column(
            "invoices",
            sa.Column("issued_at", sa.DateTime(), nullable=True, server_default=sa.text("now()")),
        )

    # Legacy schema may require total_ils/vat_ils NOT NULL; ensure inserts that
    # don't explicitly send them still work by setting safe defaults.
    if _col_exists("invoices", "total_ils"):
        op.execute(sa.text("ALTER TABLE invoices ALTER COLUMN total_ils SET DEFAULT 0"))
    if _col_exists("invoices", "vat_ils"):
        op.execute(sa.text("ALTER TABLE invoices ALTER COLUMN vat_ils SET DEFAULT 0"))


def downgrade() -> None:
    # Non-destructive downgrade for production safety.
    pass
