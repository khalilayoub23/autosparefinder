"""Add approval_queue table to autospare_pii.

Revision ID: 0004_add_approval_queue
Revises: 0003_remove_vehicles
Create Date: 2026-03-17
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "0004_add_approval_queue"
down_revision = "0003_remove_vehicles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "approval_queue",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("entity_type", sa.String(50), nullable=False),
        sa.Column(
            "entity_id",
            UUID(as_uuid=True),
            nullable=False,
            comment="UUID reference — target table determined by entity_type; no FK (may be cross-DB)",
        ),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("payload", JSONB, nullable=False, server_default=sa.text("'{}'")),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column(
            "requested_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "resolved_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("resolution_note", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime,
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("resolved_at", sa.DateTime, nullable=True),
    )
    op.create_check_constraint(
        "ck_approval_queue_status",
        "approval_queue",
        "status IN ('pending', 'approved', 'rejected')",
    )
    op.create_index("ix_approval_queue_status", "approval_queue", ["status"])
    op.create_index("ix_approval_queue_entity_type", "approval_queue", ["entity_type"])
    op.create_index("ix_approval_queue_requested_by", "approval_queue", ["requested_by"])


def downgrade() -> None:
    op.drop_index("ix_approval_queue_requested_by", table_name="approval_queue")
    op.drop_index("ix_approval_queue_entity_type", table_name="approval_queue")
    op.drop_index("ix_approval_queue_status", table_name="approval_queue")
    op.drop_table("approval_queue")
