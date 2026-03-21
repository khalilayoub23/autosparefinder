"""Add job_failures table for Dead Letter Queue (DLQ)

Revision ID: 0019
Revises: 0018
Create Date: 2026-03-21
"""
from alembic import op
import sqlalchemy as sa

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade():
    # Create job_failures table (Dead Letter Queue for background job failures)
    op.create_table(
        'job_failures',
        sa.Column('id', sa.UUID(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column('job_name', sa.String(255), nullable=False, doc="Name of failed job (e.g., 'sync_prices', 'run_scraper_cycle')"),
        sa.Column('payload', sa.JSON(), nullable=True, doc="Original job parameters (dict)"),
        sa.Column('error', sa.Text(), nullable=True, doc="Exception message / traceback"),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default=sa.text("1"), doc="Number of retry attempts so far"),
        sa.Column('next_retry_at', sa.DateTime(timezone=True), nullable=True, doc="Scheduled time for next retry (NULL = don't retry)"),
        sa.Column('status', sa.String(50), nullable=False, server_default=sa.text("'pending'"), doc="pending | retrying | resolved"),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True, doc="When job was manually resolved or deleted"),
        sa.Column('resolved_by', sa.String(255), nullable=True, doc="Admin user ID who resolved the failure"),
        sa.PrimaryKeyConstraint('id'),
    )

    # Indexes for efficient querying
    op.create_index('ix_job_failures_status', 'job_failures', ['status'])
    op.create_index('ix_job_failures_job_name', 'job_failures', ['job_name'])
    op.create_index('ix_job_failures_created_at', 'job_failures', ['created_at'])
    op.create_index('ix_job_failures_next_retry_at', 'job_failures', ['next_retry_at'])
    op.create_index('ix_job_failures_status_next_retry', 'job_failures', ['status', 'next_retry_at'])


def downgrade():
    op.drop_index('ix_job_failures_status_next_retry', table_name='job_failures')
    op.drop_index('ix_job_failures_next_retry_at', table_name='job_failures')
    op.drop_index('ix_job_failures_created_at', table_name='job_failures')
    op.drop_index('ix_job_failures_job_name', table_name='job_failures')
    op.drop_index('ix_job_failures_status', table_name='job_failures')
    op.drop_table('job_failures')
