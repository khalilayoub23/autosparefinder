"""Email Agent owner flow: notification, digest, tasks and OWNER-APPROVED sending.

Revision ID: 0041_email_agent_owner_flow
Revises: 0040_messages_chat_req_uniq
Create Date: 2026-10-27

Owner decision 2026-10-27: the agent must tell the owner about mail (important mail at once,
everything else in a 09:00 digest), file tasks for the agent that owns each matter, and send a
reply ONLY after the owner approves that specific reply in the WhatsApp console.

Columns added to email_agent_messages:
  owner_notified_at      the immediate WhatsApp alert for this email went out (important mail)
  digest_at              this email was included in a daily digest (all other mail)
  owner_closed_at        the owner marked the email handled without sending
  task_ids               agent_todos ids filed for this email (catalog DB)
  send_approved_at / send_approved_by   the owner's explicit approval to send this reply
  sent_at / gmail_sent_message_id       the approved draft was sent (Gmail message id)
  send_error             the last failed send attempt

`sendable` keeps CHECK (sendable = false): it means "the system may send on its own", which stays
impossible. A NEW check ties every send to a recorded owner approval:
    CHECK (sent_at IS NULL OR send_approved_at IS NOT NULL)

Existing rows are marked notified + digested at their creation time so the first run does not
replay 130 historic emails; open items still appear in the digest's "waiting for you" section.

Downgrade drops the columns and the check (no data outside them is touched).
"""
from alembic import op

revision = "0041_email_agent_owner_flow"
down_revision = "0040_messages_chat_req_uniq"
branch_labels = None
depends_on = None

UPGRADE_SQL = (
    "SET LOCAL lock_timeout = '15s'",
    """
    ALTER TABLE email_agent_messages
        ADD COLUMN IF NOT EXISTS owner_notified_at     timestamptz,
        ADD COLUMN IF NOT EXISTS digest_at             timestamptz,
        ADD COLUMN IF NOT EXISTS owner_closed_at       timestamptz,
        ADD COLUMN IF NOT EXISTS task_ids              jsonb NOT NULL DEFAULT '[]'::jsonb,
        ADD COLUMN IF NOT EXISTS send_approved_at      timestamptz,
        ADD COLUMN IF NOT EXISTS send_approved_by      varchar(40),
        ADD COLUMN IF NOT EXISTS sent_at               timestamptz,
        ADD COLUMN IF NOT EXISTS gmail_sent_message_id varchar(128),
        ADD COLUMN IF NOT EXISTS send_error            text
    """,
    """
    DO $$ BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'email_agent_messages_sent_requires_approval') THEN
            ALTER TABLE email_agent_messages ADD CONSTRAINT email_agent_messages_sent_requires_approval
                CHECK (sent_at IS NULL OR send_approved_at IS NOT NULL);
        END IF;
    END $$
    """,
    "UPDATE email_agent_messages SET owner_notified_at = created_at, digest_at = created_at "
    "WHERE owner_notified_at IS NULL AND digest_at IS NULL",
    "CREATE INDEX IF NOT EXISTS ix_email_agent_messages_owner_pending ON email_agent_messages (created_at) "
    "WHERE owner_notified_at IS NULL OR digest_at IS NULL",
)

DOWNGRADE_SQL = (
    "DROP INDEX IF EXISTS ix_email_agent_messages_owner_pending",
    "ALTER TABLE email_agent_messages DROP CONSTRAINT IF EXISTS email_agent_messages_sent_requires_approval",
    """
    ALTER TABLE email_agent_messages
        DROP COLUMN IF EXISTS owner_notified_at, DROP COLUMN IF EXISTS digest_at,
        DROP COLUMN IF EXISTS owner_closed_at, DROP COLUMN IF EXISTS task_ids,
        DROP COLUMN IF EXISTS send_approved_at, DROP COLUMN IF EXISTS send_approved_by,
        DROP COLUMN IF EXISTS sent_at, DROP COLUMN IF EXISTS gmail_sent_message_id,
        DROP COLUMN IF EXISTS send_error
    """,
)


def upgrade() -> None:
    for stmt in UPGRADE_SQL:
        op.execute(stmt)


def downgrade() -> None:
    for stmt in DOWNGRADE_SQL:
        op.execute(stmt)
