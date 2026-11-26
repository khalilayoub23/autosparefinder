"""Owner interactive decisions (WhatsApp buttons): interaction state + append-only audit.

Revision ID: 0043_owner_interactions
Revises: 0042_supplier_lifecycle
Create Date: 2026-11-25

owner_interactions        one row per decision request shown to the owner: allowed actions (with the exact typed
                          command each runs), state open|awaiting_input|consumed|completed|expired, expiry, idempotency key,
                          and the persisted continuation {verb, ref, agent} of a Change/Edit/More-info follow-up.
                          Consumption is a conditional UPDATE, so a repeated or concurrent tap executes once.
owner_interaction_events  append-only audit: created / response / every rejection reason / follow-up steps.

Amended 2026-11-26 (#157, before any production apply): continuation columns + 'completed' state.
Additive only; downgrade drops just these two tables (data in them is audit/state).
"""
from alembic import op

revision = "0043_owner_interactions"
down_revision = "0042_supplier_lifecycle"
branch_labels = None
depends_on = None

UPGRADE_SQL = (
    """
    CREATE TABLE IF NOT EXISTS owner_interactions (
        id               varchar(48) PRIMARY KEY,
        agent            varchar(32) NOT NULL,
        task_ref         varchar(80) NOT NULL DEFAULT '',
        entity_id        varchar(80) NOT NULL DEFAULT '',
        actions          jsonb       NOT NULL,
        state            varchar(16) NOT NULL DEFAULT 'open'
                         CHECK (state IN ('open','awaiting_input','consumed','completed','expired')),
        pending_action   varchar(32),
        consumed_action  varchar(32),
        consumed_at      timestamptz,
        wa_message_id    varchar(64),
        idempotency_key  varchar(64) NOT NULL,
        created_at       timestamptz NOT NULL DEFAULT now(),
        expires_at       timestamptz NOT NULL,
        continuation        jsonb,
        prompt_message_id   varchar(64),
        followup_expires_at timestamptz
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_owner_interactions_idem ON owner_interactions (idempotency_key, state)",
    "CREATE INDEX IF NOT EXISTS ix_owner_interactions_entity ON owner_interactions (agent, entity_id) WHERE state IN ('open','awaiting_input')",
    "CREATE INDEX IF NOT EXISTS ix_owner_interactions_pending ON owner_interactions (state, created_at) WHERE state = 'awaiting_input'",
    """
    CREATE TABLE IF NOT EXISTS owner_interaction_events (
        id             bigserial PRIMARY KEY,
        interaction_id varchar(48) NOT NULL,
        at             timestamptz NOT NULL DEFAULT now(),
        kind           varchar(40) NOT NULL,
        detail         jsonb NOT NULL DEFAULT '{}'::jsonb
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_owner_interaction_events_ix ON owner_interaction_events (interaction_id, at)",
)

DOWNGRADE_SQL = (
    "SET LOCAL lock_timeout = '15s'",
    "DROP TABLE IF EXISTS owner_interaction_events",
    "DROP TABLE IF EXISTS owner_interactions",
)


def upgrade() -> None:
    for stmt in UPGRADE_SQL:
        op.execute(stmt)


def downgrade() -> None:
    for stmt in DOWNGRADE_SQL:
        op.execute(stmt)
