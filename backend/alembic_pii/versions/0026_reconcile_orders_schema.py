"""reconcile orders and order_items schema to match ORM

The initial schema created 'old' column names (subtotal_ils, vat_ils, etc.).
The ORM was redesigned but no migration was ever written to bridge the gap.
This migration renames columns and adds missing ones.

Revision ID: 0026
Revises: 0025
Create Date: 2026-03-29 00:00:00

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def _col_exists(table: str, column: str) -> bool:
    conn = op.get_bind()
    result = conn.execute(sa.text(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name=:t AND column_name=:c"
    ), {"t": table, "c": column})
    return result.fetchone() is not None


def upgrade() -> None:
    # ------------------------------------------------------------------ orders
    # rename old column names → ORM names
    if _col_exists("orders", "subtotal_ils") and not _col_exists("orders", "subtotal"):
        op.execute(sa.text("ALTER TABLE orders RENAME COLUMN subtotal_ils TO subtotal"))
    if _col_exists("orders", "vat_ils") and not _col_exists("orders", "vat_amount"):
        op.execute(sa.text("ALTER TABLE orders RENAME COLUMN vat_ils TO vat_amount"))
    if _col_exists("orders", "shipping_ils") and not _col_exists("orders", "shipping_cost"):
        op.execute(sa.text("ALTER TABLE orders RENAME COLUMN shipping_ils TO shipping_cost"))
    if _col_exists("orders", "total_ils") and not _col_exists("orders", "total_amount"):
        op.execute(sa.text("ALTER TABLE orders RENAME COLUMN total_ils TO total_amount"))

    # add missing columns
    if not _col_exists("orders", "order_number"):
        op.add_column("orders", sa.Column("order_number", sa.String(20), nullable=True))
        # backfill with a short unique value derived from id
        op.execute(sa.text(
            "UPDATE orders SET order_number = 'ORD-' || UPPER(SUBSTRING(id::text, 1, 8)) "
            "WHERE order_number IS NULL"
        ))
        op.execute(sa.text(
            "ALTER TABLE orders ALTER COLUMN order_number SET NOT NULL"
        ))
        op.create_unique_constraint("uq_orders_order_number", "orders", ["order_number"])
        op.create_index("ix_orders_order_number", "orders", ["order_number"])

    if not _col_exists("orders", "discount_amount"):
        op.add_column("orders", sa.Column("discount_amount", sa.Numeric(10, 2), nullable=True, server_default="0"))
    if not _col_exists("orders", "tracking_number"):
        op.add_column("orders", sa.Column("tracking_number", sa.String(100), nullable=True))
    if not _col_exists("orders", "tracking_url"):
        op.add_column("orders", sa.Column("tracking_url", sa.String(500), nullable=True))
    if not _col_exists("orders", "estimated_delivery"):
        op.add_column("orders", sa.Column("estimated_delivery", sa.DateTime(), nullable=True))
    if not _col_exists("orders", "coupon_code"):
        op.add_column("orders", sa.Column("coupon_code", sa.String(50), nullable=True))
    if not _col_exists("orders", "shipping_type"):
        op.add_column("orders", sa.Column("shipping_type", sa.String(20), nullable=True, server_default="standard"))
    if not _col_exists("orders", "shipped_at"):
        op.add_column("orders", sa.Column("shipped_at", sa.DateTime(), nullable=True))
    if not _col_exists("orders", "delivered_at"):
        op.add_column("orders", sa.Column("delivered_at", sa.DateTime(), nullable=True))
    if not _col_exists("orders", "cancelled_at"):
        op.add_column("orders", sa.Column("cancelled_at", sa.DateTime(), nullable=True))

    # Ensure shipping_address default is not null (ORM requires it)
    op.execute(sa.text(
        "UPDATE orders SET shipping_address = '{}' WHERE shipping_address IS NULL"
    ))

    # ---------------------------------------------------------- order_items
    # rename old column names → ORM names
    if _col_exists("order_items", "unit_price_ils") and not _col_exists("order_items", "unit_price"):
        op.execute(sa.text("ALTER TABLE order_items RENAME COLUMN unit_price_ils TO unit_price"))
    if _col_exists("order_items", "total_price_ils") and not _col_exists("order_items", "total_price"):
        op.execute(sa.text("ALTER TABLE order_items RENAME COLUMN total_price_ils TO total_price"))
    if _col_exists("order_items", "sku") and not _col_exists("order_items", "part_sku"):
        op.execute(sa.text("ALTER TABLE order_items RENAME COLUMN sku TO part_sku"))
    if _col_exists("order_items", "supplier_id") and not _col_exists("order_items", "supplier_part_id"):
        op.execute(sa.text("ALTER TABLE order_items RENAME COLUMN supplier_id TO supplier_part_id"))

    # add missing columns
    if not _col_exists("order_items", "part_name"):
        op.add_column("order_items", sa.Column("part_name", sa.String(255), nullable=True))
        # backfill from name_he if it still exists
        if _col_exists("order_items", "name_he"):
            op.execute(sa.text(
                "UPDATE order_items SET part_name = COALESCE(name_he, name_en, 'Unknown') "
                "WHERE part_name IS NULL"
            ))
        else:
            op.execute(sa.text("UPDATE order_items SET part_name = 'Unknown' WHERE part_name IS NULL"))
        op.execute(sa.text("ALTER TABLE order_items ALTER COLUMN part_name SET NOT NULL"))

    if not _col_exists("order_items", "manufacturer"):
        op.add_column("order_items", sa.Column("manufacturer", sa.String(100), nullable=True))
    if not _col_exists("order_items", "part_type"):
        op.add_column("order_items", sa.Column("part_type", sa.String(50), nullable=True))
    if not _col_exists("order_items", "supplier_name"):
        op.add_column("order_items", sa.Column("supplier_name", sa.String(255), nullable=True))
    if not _col_exists("order_items", "supplier_order_id"):
        op.add_column("order_items", sa.Column("supplier_order_id", sa.String(100), nullable=True))
    if not _col_exists("order_items", "vat_amount"):
        op.add_column("order_items", sa.Column("vat_amount", sa.Numeric(10, 2), nullable=True, server_default="0"))
        op.execute(sa.text("UPDATE order_items SET vat_amount = 0 WHERE vat_amount IS NULL"))
        op.execute(sa.text("ALTER TABLE order_items ALTER COLUMN vat_amount SET NOT NULL"))
    if not _col_exists("order_items", "warranty_months"):
        op.add_column("order_items", sa.Column("warranty_months", sa.Integer(), nullable=True, server_default="12"))

    # make part_id nullable (ORM has nullable=True for cross-DB ref)
    op.execute(sa.text("ALTER TABLE order_items ALTER COLUMN part_id DROP NOT NULL"))
    # make part_sku nullable (ORM has no nullable=False for it)
    if _col_exists("order_items", "part_sku"):
        op.execute(sa.text("ALTER TABLE order_items ALTER COLUMN part_sku DROP NOT NULL"))


def downgrade() -> None:
    # Intentionally left minimal — reversing renames would lose backfilled data
    pass
