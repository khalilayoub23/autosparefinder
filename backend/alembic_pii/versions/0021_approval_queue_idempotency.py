"""Add idempotency_key column to ApprovalQueue

Revision ID: 0021
Revises: 0019_job_failures
Create Date: 2026-03-21
"""
from alembic import op
import sqlalchemy as sa

revision = "0021"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade():
    # Add idempotency_key column to ApprovalQueue (PII DB)
    op.add_column(
        'approval_queue',
        sa.Column(
            'idempotency_key',
            sa.String(255),
            nullable=True,
            unique=True,
            comment="Idempotency key for deduplication (sha256 of entity_type:entity_id:action)"
        ),
    )
    op.create_index('ix_approval_queue_idempotency_key', 'approval_queue', ['idempotency_key'])


def downgrade():
    op.drop_index('ix_approval_queue_idempotency_key', table_name='approval_queue')
    op.drop_column('approval_queue', 'idempotency_key')
