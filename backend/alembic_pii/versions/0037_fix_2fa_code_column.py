"""Widen two_factor_codes.code column from VARCHAR(6) to TEXT, and add wa_message_key.

The code column stores HMAC-SHA256 hex digests (64 chars) since the auth security
refactor — VARCHAR(6) truncates and breaks registration/2FA for new users.
wa_message_key is in the ORM model but was never added to the schema.

Both operations use IF EXISTS / type-change that is safe to run on production even
if the column was already widened or added manually.

Revision ID: 0037_fix_2fa_code_column
Revises: 0036_agent_memory_usage_logs
Create Date: 2026-08-05
"""

from alembic import op
import sqlalchemy as sa


revision = "0037_fix_2fa_code_column"
down_revision = "0036_agent_memory_usage_logs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Widen code column: was VARCHAR(6), must hold 64-char HMAC-SHA256 hex digests.
    op.execute("ALTER TABLE two_factor_codes ALTER COLUMN code TYPE TEXT")

    # Add wa_message_key: in ORM since WhatsApp 2FA refactor, never migrated.
    op.execute(
        "ALTER TABLE two_factor_codes ADD COLUMN IF NOT EXISTS wa_message_key TEXT"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE two_factor_codes DROP COLUMN IF EXISTS wa_message_key")
    # Cannot safely narrow TEXT back to VARCHAR(6) without data loss — no-op.
