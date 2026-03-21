"""Add job_registry table for queue monitoring

Revision ID: 0020
Revises: 0015_catalog_social_indexes
Create Date: 2026-03-21
"""
from alembic import op
import sqlalchemy as sa

revision = "0020"
down_revision = "0015_catalog_social_indexes"
branch_labels = None
depends_on = None


def upgrade():
    # Create job_registry table (queue monitoring in main catalog DB)
    op.create_table(
        'job_registry',
        sa.Column('id', sa.UUID(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column('job_id', sa.String(255), nullable=False, unique=True, doc="Unique job identifier (e.g., 'sync_prices-2026-03-21T10:00:00')"),
        sa.Column('job_name', sa.String(255), nullable=False, doc="Name of job (e.g., 'sync_prices', 'run_scraper_cycle')"),
        sa.Column('worker_host', sa.String(255), nullable=True, doc="Hostname/pod name where job runs"),
        sa.Column('status', sa.String(50), nullable=False, server_default=sa.text("'running'"), doc="running | completed | failed"),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True, doc="When job finished (success or failure)"),
        sa.Column('ttl_seconds', sa.Integer(), nullable=True, doc="Expected job duration in seconds (used to detect stuck jobs)"),
        sa.Column('error_message', sa.Text(), nullable=True, doc="If failed, exception message"),
        sa.Column('last_heartbeat_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()"), doc="Timestamp of last heartbeat update (for stuck detection)"),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint('id'),
    )

    # Indexes for monitoring queries
    op.create_index('ix_job_registry_job_id', 'job_registry', ['job_id'])
    op.create_index('ix_job_registry_job_name', 'job_registry', ['job_name'])
    op.create_index('ix_job_registry_status', 'job_registry', ['status'])
    op.create_index('ix_job_registry_started_at', 'job_registry', ['started_at'])
    op.create_index('ix_job_registry_status_heartbeat', 'job_registry', ['status', 'last_heartbeat_at'], 
                    postgresql_where=sa.text("status = 'running'"))


def downgrade():
    op.drop_index('ix_job_registry_status_heartbeat', table_name='job_registry')
    op.drop_index('ix_job_registry_started_at', table_name='job_registry')
    op.drop_index('ix_job_registry_status', table_name='job_registry')
    op.drop_index('ix_job_registry_job_name', table_name='job_registry')
    op.drop_index('ix_job_registry_job_id', table_name='job_registry')
    op.drop_table('job_registry')
