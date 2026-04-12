"""Reconcile legacy chat schema with current ORM expectations.

Adds missing columns to conversations/messages and creates missing
agent_actions/agent_ratings tables in drifted deployments.

Revision ID: 0031_reconcile_chat_schema
Revises: 0030_reconcile_returns_schema
Create Date: 2026-04-06
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB


revision = "0031_reconcile_chat_schema"
down_revision = "0030_reconcile_returns_schema"
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


def _idx_exists(table: str, index_name: str) -> bool:
    conn = op.get_bind()
    result = conn.execute(
        sa.text(
            "SELECT 1 FROM pg_indexes "
            "WHERE schemaname='public' AND tablename=:t AND indexname=:i"
        ),
        {"t": table, "i": index_name},
    )
    return result.fetchone() is not None


def upgrade() -> None:
    # conversations
    if _table_exists("conversations"):
        if not _col_exists("conversations", "title"):
            op.add_column("conversations", sa.Column("title", sa.String(length=255), nullable=True))
        if not _col_exists("conversations", "current_agent"):
            op.add_column("conversations", sa.Column("current_agent", sa.String(length=50), nullable=True))
        if not _col_exists("conversations", "is_active"):
            op.add_column(
                "conversations",
                sa.Column("is_active", sa.Boolean(), nullable=True, server_default=sa.text("true")),
            )
        if not _col_exists("conversations", "started_at"):
            op.add_column(
                "conversations",
                sa.Column("started_at", sa.DateTime(), nullable=True, server_default=sa.text("now()")),
            )
        if not _col_exists("conversations", "last_message_at"):
            op.add_column(
                "conversations",
                sa.Column("last_message_at", sa.DateTime(), nullable=True, server_default=sa.text("now()")),
            )
        if not _col_exists("conversations", "ended_at"):
            op.add_column("conversations", sa.Column("ended_at", sa.DateTime(), nullable=True))
        if not _col_exists("conversations", "deleted_at"):
            op.add_column("conversations", sa.Column("deleted_at", sa.DateTime(), nullable=True))

        # Backfill from legacy columns where available.
        if _col_exists("conversations", "created_at") and _col_exists("conversations", "started_at"):
            op.execute(
                sa.text(
                    "UPDATE conversations "
                    "SET started_at = COALESCE(started_at, created_at, now()) "
                    "WHERE started_at IS NULL"
                )
            )
        if _col_exists("conversations", "last_message_at"):
            if _col_exists("conversations", "updated_at"):
                op.execute(
                    sa.text(
                        "UPDATE conversations "
                        "SET last_message_at = COALESCE(last_message_at, updated_at, created_at, now()) "
                        "WHERE last_message_at IS NULL"
                    )
                )
            else:
                op.execute(
                    sa.text(
                        "UPDATE conversations "
                        "SET last_message_at = COALESCE(last_message_at, created_at, now()) "
                        "WHERE last_message_at IS NULL"
                    )
                )

        if _col_exists("conversations", "is_active"):
            if _col_exists("conversations", "status"):
                op.execute(
                    sa.text(
                        "UPDATE conversations "
                        "SET is_active = CASE "
                        "    WHEN lower(COALESCE(status, 'active')) IN ('ended', 'closed', 'archived', 'inactive', 'deleted') THEN false "
                        "    ELSE true "
                        "END "
                        "WHERE is_active IS NULL"
                    )
                )
            else:
                op.execute(sa.text("UPDATE conversations SET is_active = true WHERE is_active IS NULL"))

        if not _idx_exists("conversations", "ix_conversations_deleted_at"):
            op.create_index("ix_conversations_deleted_at", "conversations", ["deleted_at"], unique=False)

    # messages
    if _table_exists("messages"):
        if not _col_exists("messages", "agent_name"):
            op.add_column("messages", sa.Column("agent_name", sa.String(length=50), nullable=True))
        if not _col_exists("messages", "content_type"):
            op.add_column(
                "messages",
                sa.Column("content_type", sa.String(length=20), nullable=True, server_default=sa.text("'text'")),
            )
        if not _col_exists("messages", "file_id"):
            op.add_column("messages", sa.Column("file_id", UUID(as_uuid=True), nullable=True))
        if not _col_exists("messages", "transcription"):
            op.add_column("messages", sa.Column("transcription", sa.Text(), nullable=True))
        if not _col_exists("messages", "analysis"):
            op.add_column("messages", sa.Column("analysis", JSONB(), nullable=True))
        if not _col_exists("messages", "model_used"):
            op.add_column("messages", sa.Column("model_used", sa.String(length=100), nullable=True))
        if not _col_exists("messages", "tokens_used"):
            op.add_column("messages", sa.Column("tokens_used", sa.Integer(), nullable=True))
        if not _col_exists("messages", "deleted_at"):
            op.add_column("messages", sa.Column("deleted_at", sa.DateTime(), nullable=True))

        if _col_exists("messages", "content_type"):
            op.execute(sa.text("UPDATE messages SET content_type = 'text' WHERE content_type IS NULL"))

        if not _idx_exists("messages", "ix_messages_deleted_at"):
            op.create_index("ix_messages_deleted_at", "messages", ["deleted_at"], unique=False)

    # agent_actions
    if not _table_exists("agent_actions"):
        op.create_table(
            "agent_actions",
            sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
            sa.Column("message_id", UUID(as_uuid=True), sa.ForeignKey("messages.id", ondelete="CASCADE"), nullable=False),
            sa.Column("agent_name", sa.String(length=50), nullable=True),
            sa.Column("action_type", sa.String(length=50), nullable=True),
            sa.Column("action_data", JSONB(), nullable=True),
            sa.Column("result", JSONB(), nullable=True),
            sa.Column("success", sa.Boolean(), nullable=True, server_default=sa.text("true")),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("execution_time_ms", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True, server_default=sa.text("now()")),
        )
    if _table_exists("agent_actions") and not _idx_exists("agent_actions", "ix_agent_actions_message_id"):
        op.create_index("ix_agent_actions_message_id", "agent_actions", ["message_id"], unique=False)

    # agent_ratings
    if not _table_exists("agent_ratings"):
        op.create_table(
            "agent_ratings",
            sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
            sa.Column("conversation_id", UUID(as_uuid=True), sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False),
            sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("agent_name", sa.String(length=50), nullable=True),
            sa.Column("rating", sa.Integer(), nullable=False),
            sa.Column("feedback", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True, server_default=sa.text("now()")),
            sa.CheckConstraint("rating >= 1 AND rating <= 5", name="ck_agent_rating_range"),
        )
    if _table_exists("agent_ratings") and not _idx_exists("agent_ratings", "ix_agent_ratings_user_id"):
        op.create_index("ix_agent_ratings_user_id", "agent_ratings", ["user_id"], unique=False)


def downgrade() -> None:
    # Non-destructive on purpose.
    pass
