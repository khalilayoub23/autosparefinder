"""reconcile chat schema for conversations/messages/agent tables

Revision ID: 0027
Revises: 0026
Create Date: 2026-04-07 17:10:00

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "0027"
down_revision = "0026"
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


def _index_exists(index_name: str) -> bool:
    conn = op.get_bind()
    result = conn.execute(
        sa.text(
            "SELECT 1 FROM pg_indexes "
            "WHERE schemaname='public' AND indexname=:i"
        ),
        {"i": index_name},
    )
    return result.fetchone() is not None


def upgrade() -> None:
    # conversations columns expected by ORM
    if not _col_exists("conversations", "title"):
        op.add_column("conversations", sa.Column("title", sa.String(length=255), nullable=True))
    if not _col_exists("conversations", "current_agent"):
        op.add_column("conversations", sa.Column("current_agent", sa.String(length=50), nullable=True))
    if not _col_exists("conversations", "is_active"):
        op.add_column("conversations", sa.Column("is_active", sa.Boolean(), nullable=True, server_default=sa.text("true")))
    if not _col_exists("conversations", "started_at"):
        op.add_column("conversations", sa.Column("started_at", sa.DateTime(), nullable=True, server_default=sa.text("now()")))
    if not _col_exists("conversations", "last_message_at"):
        op.add_column("conversations", sa.Column("last_message_at", sa.DateTime(), nullable=True))
    if not _col_exists("conversations", "ended_at"):
        op.add_column("conversations", sa.Column("ended_at", sa.DateTime(), nullable=True))

    # messages columns expected by ORM
    if not _col_exists("messages", "agent_name"):
        op.add_column("messages", sa.Column("agent_name", sa.String(length=50), nullable=True))
    if not _col_exists("messages", "content_type"):
        op.add_column("messages", sa.Column("content_type", sa.String(length=20), nullable=True, server_default=sa.text("'text'")))
    if not _col_exists("messages", "file_id"):
        op.add_column("messages", sa.Column("file_id", sa.UUID(), nullable=True))
    if not _col_exists("messages", "transcription"):
        op.add_column("messages", sa.Column("transcription", sa.Text(), nullable=True))
    if not _col_exists("messages", "analysis"):
        op.add_column("messages", sa.Column("analysis", JSONB(), nullable=True))
    if not _col_exists("messages", "model_used"):
        op.add_column("messages", sa.Column("model_used", sa.String(length=100), nullable=True))
    if not _col_exists("messages", "tokens_used"):
        op.add_column("messages", sa.Column("tokens_used", sa.Integer(), nullable=True))

    if not _index_exists("idx_messages_created_at"):
        op.create_index("idx_messages_created_at", "messages", ["created_at"])

    # missing chat tables referenced by ORM
    if not _table_exists("agent_actions"):
        op.execute(
            sa.text(
                """
                CREATE TABLE agent_actions (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    message_id UUID NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
                    agent_name VARCHAR(50),
                    action_type VARCHAR(50),
                    action_data JSONB DEFAULT '{}'::jsonb,
                    result JSONB,
                    success BOOLEAN DEFAULT TRUE,
                    error_message TEXT,
                    execution_time_ms INTEGER,
                    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now()
                )
                """
            )
        )
    if not _index_exists("idx_agent_actions_message_id"):
        op.create_index("idx_agent_actions_message_id", "agent_actions", ["message_id"])

    if not _table_exists("agent_ratings"):
        op.execute(
            sa.text(
                """
                CREATE TABLE agent_ratings (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    user_id UUID NOT NULL REFERENCES users(id),
                    agent_name VARCHAR(50),
                    rating INTEGER NOT NULL,
                    feedback TEXT,
                    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now(),
                    CONSTRAINT ck_agent_rating_range CHECK (rating >= 1 AND rating <= 5)
                )
                """
            )
        )
    if not _index_exists("idx_agent_ratings_conversation_id"):
        op.create_index("idx_agent_ratings_conversation_id", "agent_ratings", ["conversation_id"])
    if not _index_exists("idx_agent_ratings_user_id"):
        op.create_index("idx_agent_ratings_user_id", "agent_ratings", ["user_id"])


def downgrade() -> None:
    # Intentionally minimal and non-destructive for live data safety.
    pass