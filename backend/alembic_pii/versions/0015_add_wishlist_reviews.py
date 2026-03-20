"""Add wishlist_items and part_reviews tables to autospare_pii.

Revision ID: 0015_add_wishlist_reviews
Revises: 0014_add_cart
Create Date: 2026-03-20
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0015_add_wishlist_reviews"
down_revision = "0014_add_cart"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # wishlist_items — one row per (user, part) pair
    # ------------------------------------------------------------------
    op.create_table(
        "wishlist_items",
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
            "part_id",
            UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "added_at",
            sa.DateTime,
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_unique_constraint("uq_wishlist_item", "wishlist_items", ["user_id", "part_id"])
    op.create_index("ix_wishlist_items_user_id", "wishlist_items", ["user_id"])

    # ------------------------------------------------------------------
    # part_reviews — one review per (user, part) pair
    # ------------------------------------------------------------------
    op.create_table(
        "part_reviews",
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
            "part_id",
            UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "order_id",
            UUID(as_uuid=True),
            sa.ForeignKey("orders.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "rating",
            sa.Integer,
            nullable=False,
        ),
        sa.Column(
            "title",
            sa.String(255),
            nullable=True,
        ),
        sa.Column(
            "body",
            sa.Text,
            nullable=True,
        ),
        sa.Column(
            "is_verified_purchase",
            sa.Boolean,
            server_default=sa.text("false"),
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
        sa.CheckConstraint("rating >= 1 AND rating <= 5", name="ck_part_review_rating"),
    )
    op.create_unique_constraint("uq_part_review", "part_reviews", ["user_id", "part_id"])
    op.create_index("ix_part_reviews_part_id", "part_reviews", ["part_id"])


def downgrade() -> None:
    op.drop_table("part_reviews")
    op.drop_table("wishlist_items")
