"""Add additive Eurosender shipping columns to orders and supplier_payments.

Sandbox-only implementation (see docs/EUROSENDER_SANDBOX.md / FIXES_TRACKER entry
2026-09-08). No existing column is modified or dropped; every new column is
nullable so existing rows and existing code paths are unaffected until the
Eurosender adapter is explicitly wired in and enabled via EUROSENDER_ENABLED.

orders.eurosender_order_code : Eurosender's orderCode, stored immediately after
                                a successful POST /v1/orders response.
orders.eurosender_status     : local state machine — pending_creation, created,
                                timeout_pending_reconciliation, reconciled,
                                label_ready, awaiting_customs, failed, cancelled.
orders.shipping_label_url    : label PDF/ZPL link (from order_label_ready or the
                                order-creation response).
orders.shipping_provider     : 'eurosender' | null (null = existing synthetic
                                tracking path, unchanged).
supplier_payments.shipping_provider_ref : Eurosender orderCode scoped to the
                                per-supplier payment row (an order can span
                                multiple suppliers/shipments).

Revision ID: 0038_eurosender_shipping
Revises: 0037_fix_2fa_code_column
Create Date: 2026-09-08

Root-fix 2026-09-09: the revision id was originally the full 36-character
slug "0038_add_eurosender_shipping_columns", which alembic writes verbatim
into alembic_version.version_num — a column defined as character varying(32)
(same as every other revision id in this repo, all of which are <=29 chars by
convention). Every upgrade attempt executed the additive DDL below correctly,
then failed on the final `UPDATE alembic_version ...` with
StringDataRightTruncationError, and Postgres's transactional DDL rolled the
whole migration back — so the columns were silently never actually created,
even though the app's ORM (BACKEND_DATABASE_MODELS.py) already declared them,
producing UndefinedColumnError in two live background loops. Shortened to fit;
the DDL body is unchanged.
"""

from alembic import op
import sqlalchemy as sa


revision = "0038_eurosender_shipping"
down_revision = "0037_fix_2fa_code_column"
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
    if _table_exists("orders"):
        if not _col_exists("orders", "eurosender_order_code"):
            op.add_column(
                "orders",
                sa.Column("eurosender_order_code", sa.String(length=100), nullable=True),
            )
        if not _col_exists("orders", "eurosender_status"):
            op.add_column(
                "orders",
                sa.Column("eurosender_status", sa.String(length=50), nullable=True),
            )
        if not _col_exists("orders", "shipping_label_url"):
            op.add_column(
                "orders",
                sa.Column("shipping_label_url", sa.String(length=500), nullable=True),
            )
        if not _col_exists("orders", "shipping_provider"):
            op.add_column(
                "orders",
                sa.Column("shipping_provider", sa.String(length=50), nullable=True),
            )
        # Read-heavy filter added in the stuck-orders monitor (Pass 2 exclusion of
        # Eurosender-managed orders) — index only if the column landed just now or
        # already exists without one.
        if _col_exists("orders", "shipping_provider"):
            op.execute(
                sa.text(
                    "CREATE INDEX IF NOT EXISTS ix_orders_shipping_provider "
                    "ON orders (shipping_provider)"
                )
            )
        # ORM declares eurosender_status with index=True (BACKEND_DATABASE_MODELS.py) —
        # match it here so the live schema matches the model's own metadata.
        if _col_exists("orders", "eurosender_status"):
            op.execute(
                sa.text(
                    "CREATE INDEX IF NOT EXISTS ix_orders_eurosender_status "
                    "ON orders (eurosender_status)"
                )
            )

    if _table_exists("supplier_payments"):
        if not _col_exists("supplier_payments", "shipping_provider_ref"):
            op.add_column(
                "supplier_payments",
                sa.Column("shipping_provider_ref", sa.String(length=100), nullable=True),
            )


def downgrade() -> None:
    # Non-destructive downgrade for production safety (matches repo convention —
    # see 0035_reconcile_invoices_schema.py). Columns are additive/nullable and
    # unused unless EUROSENDER_ENABLED=1, so leaving them in place on downgrade
    # is safe and avoids a destructive DROP COLUMN on a live table.
    pass
