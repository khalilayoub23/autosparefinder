"""Add email_agent_messages - idempotency + audit table for the Email Agent (PII DB).

Additive only: one new table, no existing table touched. One row per Gmail message;
UNIQUE(gmail_message_id) is the idempotency key that stops a message from creating duplicate
internal work. sendable carries CHECK (sendable = false): in this phase no row can be marked
sendable, whatever the application code does.

status lifecycle: processing -> processed | retry -> failed
  (retry is re-claimed until attempts reaches the configured maximum).

Revision ID: 0039_email_agent_messages   (<= 32 chars: alembic_version.version_num is varchar(32))
Revises: 0038_eurosender_shipping
Create Date: 2026-10-04
"""

from alembic import op


revision = "0039_email_agent_messages"
down_revision = "0038_eurosender_shipping"
branch_labels = None
depends_on = None

UPGRADE_SQL = (
    """
    CREATE TABLE IF NOT EXISTS email_agent_messages (
        id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        gmail_message_id    varchar(128) NOT NULL UNIQUE,
        gmail_thread_id     varchar(128) NOT NULL,
        rfc822_message_id   text,
        sender_email        varchar(320),
        subject             text,
        received_at         timestamptz,
        classification      varchar(40),
        confidence          numeric(3,2),
        reason              text,
        risk_flags          jsonb NOT NULL DEFAULT '[]'::jsonb,
        policy_tier         varchar(40),
        recommended_action  varchar(40),
        requires_human      boolean NOT NULL DEFAULT true,
        context             jsonb NOT NULL DEFAULT '{}'::jsonb,
        attachments         jsonb NOT NULL DEFAULT '[]'::jsonb,
        draft_subject       text,
        draft_body          text,
        draft_reason        text,
        draft_attempted_at  timestamptz,
        gmail_draft_id      varchar(128),
        verification        jsonb NOT NULL DEFAULT '{}'::jsonb,
        sendable            boolean NOT NULL DEFAULT false CHECK (sendable = false),
        status              varchar(20) NOT NULL DEFAULT 'processing',
        attempts            integer NOT NULL DEFAULT 0,
        last_error          text,
        created_at          timestamptz NOT NULL DEFAULT NOW(),
        updated_at          timestamptz NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_email_agent_messages_thread ON email_agent_messages (gmail_thread_id)",
    "CREATE INDEX IF NOT EXISTS ix_email_agent_messages_status ON email_agent_messages (status, created_at)",
)


def upgrade() -> None:
    for stmt in UPGRADE_SQL:
        op.execute(stmt)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS email_agent_messages")
