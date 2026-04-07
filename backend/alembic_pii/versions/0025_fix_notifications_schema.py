"""fix notifications schema: rename body->message, metadata->data, add channel/sent_at/is_read

Revision ID: 0025
Revises: 0024
Create Date: 2025-01-01 00:00:00

"""
from alembic import op
import sqlalchemy as sa

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Rename body -> message (ORM uses 'message')
    op.execute(sa.text("ALTER TABLE notifications RENAME COLUMN body TO message"))
    # Rename metadata -> data (ORM uses 'data')
    op.execute(sa.text("ALTER TABLE notifications RENAME COLUMN metadata TO data"))
    # Add missing columns that ORM defines
    op.add_column("notifications", sa.Column("channel", sa.String(20), nullable=True, server_default="push"))
    op.add_column("notifications", sa.Column("sent_at", sa.DateTime(), nullable=True))
    # is_read exists in DB but not in ORM — keep it for now, just ensure ORM has it
    # (no schema change needed for is_read, it already exists)


def downgrade() -> None:
    op.execute(sa.text("ALTER TABLE notifications RENAME COLUMN message TO body"))
    op.execute(sa.text("ALTER TABLE notifications RENAME COLUMN data TO metadata"))
    op.drop_column("notifications", "sent_at")
    op.drop_column("notifications", "channel")
