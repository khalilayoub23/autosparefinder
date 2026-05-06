"""add shared memory and per-agent usage logs

Revision ID: 0036_agent_memory_usage_logs
Revises: 0035_reconcile_invoices
Create Date: 2026-05-04
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0036_agent_memory_usage_logs"
down_revision = "0035_reconcile_invoices"
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


def upgrade() -> None:
    if not _table_exists("agent_shared_memory"):
        op.create_table(
            "agent_shared_memory",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("agent_name", sa.String(length=50), nullable=True),
            sa.Column("scope", sa.String(length=20), nullable=False, server_default="conversation"),
            sa.Column("memory_key", sa.String(length=120), nullable=False),
            sa.Column("memory_value", sa.Text(), nullable=False),
            sa.Column("importance", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("last_used_at", sa.DateTime(), nullable=True, server_default=sa.text("now()")),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
            sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_agent_shared_memory_user_id", "agent_shared_memory", ["user_id"], unique=False)
        op.create_index("ix_agent_shared_memory_conversation_id", "agent_shared_memory", ["conversation_id"], unique=False)
        op.create_index("ix_agent_shared_memory_last_used_at", "agent_shared_memory", ["last_used_at"], unique=False)
        op.create_index(
            "idx_agent_shared_memory_user_scope_key",
            "agent_shared_memory",
            ["user_id", "scope", "memory_key"],
            unique=False,
        )
        op.create_index(
            "idx_agent_shared_memory_conversation",
            "agent_shared_memory",
            ["conversation_id", "updated_at"],
            unique=False,
        )

    if not _table_exists("agent_usage_logs"):
        op.create_table(
            "agent_usage_logs",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("message_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("agent_name", sa.String(length=50), nullable=False),
            sa.Column("source", sa.String(length=30), nullable=True),
            sa.Column("intent", sa.String(length=120), nullable=True),
            sa.Column("model_used", sa.String(length=120), nullable=True),
            sa.Column("execution_time_ms", sa.Integer(), nullable=True),
            sa.Column("success", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("route_data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("memory_keys", postgresql.ARRAY(sa.String(length=120)), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
            sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["message_id"], ["messages.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_agent_usage_logs_user_id", "agent_usage_logs", ["user_id"], unique=False)
        op.create_index("ix_agent_usage_logs_conversation_id", "agent_usage_logs", ["conversation_id"], unique=False)
        op.create_index("ix_agent_usage_logs_message_id", "agent_usage_logs", ["message_id"], unique=False)
        op.create_index("ix_agent_usage_logs_agent_name", "agent_usage_logs", ["agent_name"], unique=False)
        op.create_index("ix_agent_usage_logs_created_at", "agent_usage_logs", ["created_at"], unique=False)
        op.create_index(
            "idx_agent_usage_logs_agent_created",
            "agent_usage_logs",
            ["agent_name", "created_at"],
            unique=False,
        )
        op.create_index(
            "idx_agent_usage_logs_source_created",
            "agent_usage_logs",
            ["source", "created_at"],
            unique=False,
        )


def downgrade() -> None:
    if _table_exists("agent_usage_logs"):
        op.drop_index("idx_agent_usage_logs_source_created", table_name="agent_usage_logs")
        op.drop_index("idx_agent_usage_logs_agent_created", table_name="agent_usage_logs")
        op.drop_index("ix_agent_usage_logs_created_at", table_name="agent_usage_logs")
        op.drop_index("ix_agent_usage_logs_agent_name", table_name="agent_usage_logs")
        op.drop_index("ix_agent_usage_logs_message_id", table_name="agent_usage_logs")
        op.drop_index("ix_agent_usage_logs_conversation_id", table_name="agent_usage_logs")
        op.drop_index("ix_agent_usage_logs_user_id", table_name="agent_usage_logs")
        op.drop_table("agent_usage_logs")

    if _table_exists("agent_shared_memory"):
        op.drop_index("idx_agent_shared_memory_conversation", table_name="agent_shared_memory")
        op.drop_index("idx_agent_shared_memory_user_scope_key", table_name="agent_shared_memory")
        op.drop_index("ix_agent_shared_memory_last_used_at", table_name="agent_shared_memory")
        op.drop_index("ix_agent_shared_memory_conversation_id", table_name="agent_shared_memory")
        op.drop_index("ix_agent_shared_memory_user_id", table_name="agent_shared_memory")
        op.drop_table("agent_shared_memory")
