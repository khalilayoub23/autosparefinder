"""Add brand_alias_review_queue table for low-confidence Hebrew alias moderation

Revision ID: 0054_alias_review_queue
Revises: 0053_agent_todos
Create Date: 2026-05-12
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0054_alias_review_queue"
down_revision = "0053_agent_todos"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "brand_alias_review_queue",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("brand_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("brand_name", sa.String(120), nullable=False),
        sa.Column("candidate_alias", sa.String(120), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("margin", sa.Float(), nullable=True),
        sa.Column("reason", sa.String(255), nullable=True),
        sa.Column("source", sa.String(40), nullable=False, server_default="auto_matcher"),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("reviewed_by", sa.String(120), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["brand_id"], ["car_brands.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("brand_name", "candidate_alias", name="uq_brand_alias_review_brand_candidate"),
    )
    op.create_index(
        "ix_brand_alias_review_queue_status",
        "brand_alias_review_queue",
        ["status"],
    )
    op.create_index(
        "ix_brand_alias_review_queue_brand_name",
        "brand_alias_review_queue",
        ["brand_name"],
    )
    op.create_index(
        "ix_brand_alias_review_queue_created_at",
        "brand_alias_review_queue",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_brand_alias_review_queue_created_at", table_name="brand_alias_review_queue")
    op.drop_index("ix_brand_alias_review_queue_brand_name", table_name="brand_alias_review_queue")
    op.drop_index("ix_brand_alias_review_queue_status", table_name="brand_alias_review_queue")
    op.drop_table("brand_alias_review_queue")
