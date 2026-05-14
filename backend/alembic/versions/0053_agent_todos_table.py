"""Add agent_todos table for shared task tracking

Revision ID: 0053_agent_todos
Revises: 0052_il_mkt_priority
Create Date: 2026-05-12
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0053_agent_todos"
down_revision = "0052_il_mkt_priority"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_todos",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(50), nullable=False, server_default="not_started"),
        sa.Column("priority", sa.String(20), nullable=False, server_default="medium"),
        sa.Column("assigned_to_agent", sa.String(100), nullable=True),
        sa.Column("assigned_to_user", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("progress_pct", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("progress_notes", sa.Text(), nullable=True),
        sa.Column("category", sa.String(50), nullable=False, server_default="general"),
        sa.Column("depends_on_todo_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("blocking_todo_ids", postgresql.ARRAY(postgresql.UUID(as_uuid=True)), nullable=True),
        sa.Column("target_date", sa.DateTime(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("tags", postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column("artifacts", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["depends_on_todo_id"], ["agent_todos.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_todos_status", "agent_todos", ["status"])
    op.create_index("ix_agent_todos_assigned_to_agent", "agent_todos", ["assigned_to_agent"])
    op.create_index("ix_agent_todos_created_at", "agent_todos", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_agent_todos_created_at", table_name="agent_todos")
    op.drop_index("ix_agent_todos_assigned_to_agent", table_name="agent_todos")
    op.drop_index("ix_agent_todos_status", table_name="agent_todos")
    op.drop_table("agent_todos")
