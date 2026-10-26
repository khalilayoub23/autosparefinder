"""One logical conversation turn per chat request — database-enforced (FIXES_TRACKER #75).

Additive only: one partial UNIQUE index on messages, no data touched. The chat lifecycle
(routes/webhooks.py, #73-#75) stamps every row it causes with analysis.chat_request_id and a role
key (chat_turn = user|assistant for the agent's turn, chat_kind = failure for a failure message).
This index makes a second live row for the same (request, role key) impossible, whatever any
process does — a concurrent duplicate agent run or failure write fails its INSERT and the
application reconciles to the row that exists. Rows soft-deleted (deleted_at) are outside the
index; rows without chat_request_id (all pre-existing rows) are outside it too, so it starts empty.

messages had ~1.5k rows at authoring time (2026-10-21; read-only check: 1,488 rows / 4.3 MB, 0 rows with
chat_request_id, 0 would-conflict groups, the index expression evaluates on every row): a plain CREATE INDEX inside
the migration transaction builds in milliseconds; no CONCURRENTLY needed. CREATE INDEX takes a SHARE lock (reads
continue, writes to messages wait). The PII DB has lock_timeout = 0, so behind an open write transaction on messages
the migration — which runs before uvicorn starts — would wait FOREVER; SET LOCAL lock_timeout makes it fail fast
and visibly instead (the backend start then fails and can simply be retried).

Rollback: `alembic -c alembic_pii.ini downgrade 0039_email_agent_messages` (drops the index) MUST run, with this
code still in place, BEFORE the code is reverted — an older tree has no revision 0040 and its start-up
`alembic upgrade head` would fail on a database stamped 0040. Dropping the index loses only the database-level
uniqueness guarantee; no data.

Revision ID: 0040_messages_chat_req_uniq   (<= 32 chars)
Revises: 0039_email_agent_messages
Create Date: 2026-10-21
"""

from alembic import op


revision = "0040_messages_chat_req_uniq"
down_revision = "0039_email_agent_messages"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '15s'")
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_messages_chat_request_role
        ON messages ((analysis->>'chat_request_id'),
                     (COALESCE(analysis->>'chat_kind', analysis->>'chat_turn', role)))
        WHERE (analysis ? 'chat_request_id') AND deleted_at IS NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_messages_chat_request_role")
