"""Add StripeWebhookLog table for webhook deduplication

Revision ID: 0022
Revises: 0021_job_failures
Create Date: 2026-03-21
"""
from alembic import op
import sqlalchemy as sa

revision = "0022"
down_revision = "0021_job_failures"
branch_labels = None
depends_on = None


def upgrade():
    # Create StripeWebhookLog table (PII DB) for webhook deduplication
    op.create_table(
        'stripe_webhook_logs',
        sa.Column('id', sa.UUID(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column('event_id', sa.String(255), nullable=False, unique=True, comment="Stripe event_id for deduplication"),
        sa.Column('event_type', sa.String(100), nullable=False, comment="Stripe event type (e.g., charge.succeeded)"),
        sa.Column('processed', sa.Boolean(), nullable=False, server_default=sa.text("FALSE"), comment="Whether event was successfully processed"),
        sa.Column('payload', sa.JSON(), nullable=True, comment="Full Stripe event payload"),
        sa.Column('result', sa.JSON(), nullable=True, comment="Processing result or error details"),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column('processed_at', sa.DateTime(timezone=True), nullable=True, comment="When event was processed"),
        sa.PrimaryKeyConstraint('id'),
    )

    # Indexes for efficient webhook lookups
    op.create_index('ix_stripe_webhook_logs_event_id', 'stripe_webhook_logs', ['event_id'])
    op.create_index('ix_stripe_webhook_logs_event_type', 'stripe_webhook_logs', ['event_type'])
    op.create_index('ix_stripe_webhook_logs_created_at', 'stripe_webhook_logs', ['created_at'])
    op.create_index('ix_stripe_webhook_logs_processed', 'stripe_webhook_logs', ['processed'])


def downgrade():
    op.drop_index('ix_stripe_webhook_logs_processed', table_name='stripe_webhook_logs')
    op.drop_index('ix_stripe_webhook_logs_created_at', table_name='stripe_webhook_logs')
    op.drop_index('ix_stripe_webhook_logs_event_type', table_name='stripe_webhook_logs')
    op.drop_index('ix_stripe_webhook_logs_event_id', table_name='stripe_webhook_logs')
    op.drop_table('stripe_webhook_logs')
