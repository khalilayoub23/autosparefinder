"""Drop all PII tables from the catalog (autospare) database.

All personal data now lives in autospare_pii.
Data was migrated via migrate_pii_data.py before this migration ran.

Revision ID: 0003_drop_pii_tables
Revises: 0002_catalog_enhancements
Create Date: 2026-03-03
"""
from alembic import op

revision = "0003_drop_pii_tables"
down_revision = "0002_catalog_enhancements"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # Step 1: Drop cross-DB FK constraints that span catalog ↔ PII
    # (catalog tables reference PII tables that are about to be removed)
    # ------------------------------------------------------------------
    op.drop_constraint(
        "purchase_orders_order_id_fkey",
        "purchase_orders",
        type_="foreignkey",
    )
    op.drop_constraint(
        "parts_images_file_id_fkey",
        "parts_images",
        type_="foreignkey",
    )

    # ------------------------------------------------------------------
    # Step 2: Drop PII tables in dependency order (children before parents)
    # ------------------------------------------------------------------
    # AI / chat
    op.drop_table("file_metadata")
    op.drop_table("agent_actions")
    op.drop_table("agent_ratings")
    op.drop_table("messages")
    op.drop_table("conversations")

    # Notifications
    op.drop_table("notifications")

    # Orders & payments
    op.drop_table("returns")
    op.drop_table("invoices")
    op.drop_table("payments")
    op.drop_table("order_items")
    op.drop_table("orders")

    # Files (after file_metadata + messages that reference files)
    op.drop_table("files")

    # Users & vehicles (leaf auth tables first, then users, then vehicles)
    op.drop_table("user_vehicles")
    op.drop_table("user_profiles")
    op.drop_table("user_sessions")
    op.drop_table("two_factor_codes")
    op.drop_table("login_attempts")
    op.drop_table("password_resets")
    op.drop_table("users")
    op.drop_table("vehicles")


def downgrade() -> None:
    # Downgrade is intentionally not implemented.
    # To restore PII tables, re-run migrate_pii_data.py in reverse
    # and then run the data migration back.
    raise NotImplementedError(
        "Downgrade not supported — PII data lives in autospare_pii. "
        "Use migrate_pii_data.py to restore if needed."
    )
