"""social campaign audit columns: created_by and approved_by as TEXT

Revision ID: 0056_social_campaign_audit_columns
Revises: 0055_social_campaign_tables
Create Date: 2026-08-07

Purpose:
  campaigns.created_by and group_targets.approved_by were created as UUID columns
  in 0055, but they store audit trail labels (agent names, user IDs, "test-runner",
  etc.) that are not always valid UUIDs. Changing to TEXT allows both UUID strings
  and plain string identifiers without a type error.

  Existing NULL values (no rows written yet) are unaffected.
"""

from alembic import op
import sqlalchemy as sa

revision = "0056_social_audit_cols"
down_revision = "0055_social_campaign_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # campaigns.created_by: UUID → TEXT
    op.alter_column(
        "campaigns",
        "created_by",
        type_=sa.Text,
        existing_type=sa.dialects.postgresql.UUID(as_uuid=True),
        postgresql_using="created_by::text",
        existing_nullable=True,
    )
    # group_targets.approved_by: UUID → TEXT
    op.alter_column(
        "group_targets",
        "approved_by",
        type_=sa.Text,
        existing_type=sa.dialects.postgresql.UUID(as_uuid=True),
        postgresql_using="approved_by::text",
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "campaigns",
        "created_by",
        type_=sa.dialects.postgresql.UUID(as_uuid=True),
        existing_type=sa.Text,
        postgresql_using="created_by::uuid",
        existing_nullable=True,
    )
    op.alter_column(
        "group_targets",
        "approved_by",
        type_=sa.dialects.postgresql.UUID(as_uuid=True),
        existing_type=sa.Text,
        postgresql_using="approved_by::uuid",
        existing_nullable=True,
    )
