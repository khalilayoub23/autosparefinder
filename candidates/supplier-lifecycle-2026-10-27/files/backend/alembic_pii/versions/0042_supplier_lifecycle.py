"""Supplier lifecycle: separate authorizations, NIR supplier outreach, procurement, spam-folder routing.

Revision ID: 0042_supplier_lifecycle
Revises: 0041_email_agent_owner_flow
Create Date: 2026-10-27

Owner task 2026-10-27 ("MASTER IMPLEMENTATION TASK - NIR, BOAZ & GMAIL AGENT"):

agent_authorizations    ONE row per explicit permission. `domain` keeps the independent permissions apart:
                        supplier_message, supplier_activation, commercial_commitment, purchase_order,
                        supplier_payment, supplier_integration_change. A grant is bound to one entity and,
                        for purchase_order / supplier_payment, to a terms_hash (supplier, items, quantities,
                        prices, shipping). A changed term = a different hash = no authorization.
supplier_outreach       NIR's routine, non-binding supplier inquiries. State machine
                        READY_TO_CONTACT -> SENT -> AWAITING_REPLY -> REPLY_RECEIVED -> INFORMATION_REVIEWED
                        -> QUALIFIED | MORE_INFORMATION_REQUIRED | NOT_QUALIFIED (+ FAILED / CANCELLED).
                        idempotency_key UNIQUE: a repeated cycle cannot send the same inquiry twice.
                        CHECK: a sent inquiry always names the authorization it was sent under.
procurement_orders      one supplier purchase per (customer order, supplier). A customer payment creates a
                        PREPARED row only. CHECKs: submitted needs a purchase_order authorization; paid needs
                        a supplier_payment authorization.
procurement_events      append-only audit of every procurement state transition and permission check.
email_agent_messages    + source_folder (inbox | spam), supplier_routed_at, supplier_route (who owns the
                        supplier reply: NIR for a reply to NIR outreach, exception when ambiguous).

Downgrade drops the new tables and columns only - IRREVERSIBLY: back them up first with
candidates/supplier-lifecycle-2026-10-27/ops/backup_0042_data.sh (DEPLOY.md). It runs inside one transaction with a
15 s lock timeout, so a blocked or failed downgrade leaves the database at 0042, unchanged.
"""
from alembic import op

revision = "0042_supplier_lifecycle"
down_revision = "0041_email_agent_owner_flow"
branch_labels = None
depends_on = None

DOMAINS = ("supplier_message", "supplier_activation", "commercial_commitment", "purchase_order",
           "supplier_payment", "supplier_integration_change")
OUTREACH_STATES = ("READY_TO_CONTACT", "SENT", "AWAITING_REPLY", "REPLY_RECEIVED", "INFORMATION_REVIEWED",
                   "QUALIFIED", "MORE_INFORMATION_REQUIRED", "NOT_QUALIFIED", "FAILED", "CANCELLED")
PROCUREMENT_STATES = ("PREPARED", "AWAITING_PO_AUTHORIZATION", "PO_AUTHORIZED", "SUBMISSION_FAILED",
                      "SUBMITTED", "SUPPLIER_CONFIRMED", "SUPPLIER_REJECTED", "AWAITING_PAYMENT_AUTHORIZATION",
                      "PAYMENT_AUTHORIZED", "SUPPLIER_PAID", "SHIPPED", "DELIVERED", "CANCELLED", "EXCEPTION")


def _in(values):
    return "(" + ", ".join(f"'{v}'" for v in values) + ")"


UPGRADE_SQL = (
    "SET LOCAL lock_timeout = '15s'",
    f"""
    CREATE TABLE IF NOT EXISTS agent_authorizations (
        id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        domain        varchar(40) NOT NULL CHECK (domain IN {_in(DOMAINS)}),
        scope         varchar(60) NOT NULL,
        entity_type   varchar(40) NOT NULL,
        entity_id     varchar(80) NOT NULL,
        terms_hash    varchar(64),
        terms         jsonb NOT NULL DEFAULT '{{}}'::jsonb,
        approved_by   varchar(60) NOT NULL,
        source        varchar(60) NOT NULL,
        standing      boolean NOT NULL DEFAULT false,
        created_at    timestamptz NOT NULL DEFAULT now(),
        expires_at    timestamptz,
        consumed_at   timestamptz,
        revoked_at    timestamptz,
        result        text
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_agent_auth_lookup ON agent_authorizations (domain, entity_type, entity_id) "
    "WHERE revoked_at IS NULL",
    f"""
    CREATE TABLE IF NOT EXISTS supplier_outreach (
        id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        supplier_id        varchar(64) NOT NULL,
        recipient          varchar(254) NOT NULL,
        purposes           jsonb NOT NULL DEFAULT '[]'::jsonb,
        template_version   varchar(20) NOT NULL,
        state              varchar(32) NOT NULL DEFAULT 'READY_TO_CONTACT' CHECK (state IN {_in(OUTREACH_STATES)}),
        idempotency_key    varchar(128) NOT NULL UNIQUE,
        attempt            integer NOT NULL DEFAULT 1,
        parent_id          uuid,
        authorization_id   uuid,
        source_task_id     varchar(64),
        gmail_draft_id     varchar(128),
        gmail_message_id   varchar(128),
        gmail_thread_id    varchar(128),
        reply_message_id   varchar(128),
        reply_at           timestamptz,
        last_error         text,
        created_at         timestamptz NOT NULL DEFAULT now(),
        sent_at            timestamptz,
        updated_at         timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT supplier_outreach_sent_requires_authorization
            CHECK (sent_at IS NULL OR authorization_id IS NOT NULL)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_supplier_outreach_thread ON supplier_outreach (gmail_thread_id) "
    "WHERE gmail_thread_id IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS ix_supplier_outreach_supplier ON supplier_outreach (supplier_id, created_at)",
    f"""
    CREATE TABLE IF NOT EXISTS procurement_orders (
        id                         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        order_id                   varchar(64) NOT NULL,
        order_number               varchar(64),
        supplier_id                varchar(64) NOT NULL,
        state                      varchar(40) NOT NULL DEFAULT 'PREPARED' CHECK (state IN {_in(PROCUREMENT_STATES)}),
        items                      jsonb NOT NULL DEFAULT '[]'::jsonb,
        terms                      jsonb NOT NULL DEFAULT '{{}}'::jsonb,
        terms_hash                 varchar(64) NOT NULL,
        idempotency_key            varchar(128) NOT NULL UNIQUE,
        po_authorization_id        uuid,
        payment_authorization_id   uuid,
        supplier_reference         varchar(128),
        submission_evidence        jsonb,
        tracking_number            varchar(128),
        last_error                 text,
        created_at                 timestamptz NOT NULL DEFAULT now(),
        updated_at                 timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT procurement_submitted_requires_po_authorization CHECK (
            state NOT IN ('SUBMITTED','SUPPLIER_CONFIRMED','SUPPLIER_PAID','SHIPPED','DELIVERED')
            OR po_authorization_id IS NOT NULL),
        CONSTRAINT procurement_paid_requires_payment_authorization CHECK (
            state NOT IN ('SUPPLIER_PAID','SHIPPED','DELIVERED') OR payment_authorization_id IS NOT NULL)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_procurement_orders_order ON procurement_orders (order_id)",
    """
    CREATE TABLE IF NOT EXISTS procurement_events (
        id              bigserial PRIMARY KEY,
        procurement_id  uuid NOT NULL REFERENCES procurement_orders(id) ON DELETE CASCADE,
        from_state      varchar(40),
        to_state        varchar(40) NOT NULL,
        actor           varchar(60) NOT NULL,
        detail          jsonb NOT NULL DEFAULT '{}'::jsonb,
        at              timestamptz NOT NULL DEFAULT now()
    )
    """,
    """
    ALTER TABLE email_agent_messages
        ADD COLUMN IF NOT EXISTS source_folder      varchar(16) NOT NULL DEFAULT 'inbox',
        ADD COLUMN IF NOT EXISTS supplier_routed_at timestamptz,
        ADD COLUMN IF NOT EXISTS supplier_route     jsonb
    """,
    # historic rows predate routing: mark them considered so the first run does not re-route old mail
    "UPDATE email_agent_messages SET supplier_routed_at = created_at WHERE supplier_routed_at IS NULL",
)

DOWNGRADE_SQL = (
    # never wait indefinitely behind a live transaction (e.g. the Email Agent mid-cycle): fail, stay at 0042
    "SET LOCAL lock_timeout = '15s'",
    "ALTER TABLE email_agent_messages DROP COLUMN IF EXISTS source_folder, "
    "DROP COLUMN IF EXISTS supplier_routed_at, DROP COLUMN IF EXISTS supplier_route",
    "DROP TABLE IF EXISTS procurement_events",
    "DROP TABLE IF EXISTS procurement_orders",
    "DROP TABLE IF EXISTS supplier_outreach",
    "DROP TABLE IF EXISTS agent_authorizations",
)


def upgrade() -> None:
    for stmt in UPGRADE_SQL:
        op.execute(stmt)


def downgrade() -> None:
    for stmt in DOWNGRADE_SQL:
        op.execute(stmt)
