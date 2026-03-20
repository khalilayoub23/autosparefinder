"""Add cart and cart_items tables to autospare_pii.

Revision ID: 0014_add_cart
Revises: 0004_add_approval_queue
Create Date: 2026-03-20
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0014_add_cart"
down_revision = "0004_add_approval_queue"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # carts — one row per user (enforced by uq_carts_user)
    # ------------------------------------------------------------------
    op.create_table(
        "carts",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime,
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime,
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_unique_constraint("uq_carts_user", "carts", ["user_id"])
    op.create_index("ix_carts_updated_at", "carts", ["updated_at"])

    # ------------------------------------------------------------------
    # cart_items — line items inside a cart
    # ------------------------------------------------------------------
    op.create_table(
        "cart_items",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "cart_id",
            UUID(as_uuid=True),
            sa.ForeignKey("carts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("part_id",          UUID(as_uuid=True), nullable=False),
        sa.Column("supplier_part_id", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "quantity",
            sa.Integer,
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column("unit_price", sa.Numeric(10, 2), nullable=False),
        sa.Column(
            "added_at",
            sa.DateTime,
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime,
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_cart_items_quantity",
        "cart_items",
        "quantity > 0",
    )
    op.create_unique_constraint(
        "uq_cart_item",
        "cart_items",
        ["cart_id", "supplier_part_id"],
    )
    op.create_index("ix_cart_items_cart_id", "cart_items", ["cart_id"])


def downgrade() -> None:
    op.drop_index("ix_cart_items_cart_id",  table_name="cart_items")
    op.drop_constraint("uq_cart_item",          "cart_items", type_="unique")
    op.drop_constraint("ck_cart_items_quantity", "cart_items", type_="check")
    op.drop_table("cart_items")

    op.drop_index("ix_carts_updated_at",    table_name="carts")
    op.drop_constraint("uq_carts_user",         "carts",      type_="unique")
    op.drop_table("carts")
