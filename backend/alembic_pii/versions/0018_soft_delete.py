"""Add deleted_at to orders, conversations, messages

Revision ID: 0018
Revises: 0017
Create Date: 2026-03-21
"""
from alembic import op
import sqlalchemy as sa

revision = "0018"
down_revision = "0017_add_customer_type"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("orders",        sa.Column("deleted_at", sa.DateTime(), nullable=True))
    op.add_column("conversations", sa.Column("deleted_at", sa.DateTime(), nullable=True))
    op.add_column("messages",      sa.Column("deleted_at", sa.DateTime(), nullable=True))

    op.create_index("ix_orders_deleted_at",        "orders",        ["deleted_at"])
    op.create_index("ix_conversations_deleted_at", "conversations", ["deleted_at"])
    op.create_index("ix_messages_deleted_at",      "messages",      ["deleted_at"])


def downgrade():
    op.drop_index("ix_orders_deleted_at",        table_name="orders")
    op.drop_index("ix_conversations_deleted_at", table_name="conversations")
    op.drop_index("ix_messages_deleted_at",      table_name="messages")
    op.drop_column("orders",        "deleted_at")
    op.drop_column("conversations", "deleted_at")
    op.drop_column("messages",      "deleted_at")
