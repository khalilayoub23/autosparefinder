"""Initial PII database schema.

Revision ID: 0001_pii_initial
Revises: 
Create Date: 2026-03-03
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "0001_pii_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # Enable pgcrypto for gen_random_uuid()
    # ------------------------------------------------------------------
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    # ------------------------------------------------------------------
    # users
    # ------------------------------------------------------------------
    op.create_table(
        "users",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("phone", sa.String(20), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(255), nullable=False),
        sa.Column("role", sa.String(50), nullable=False, server_default="customer"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("is_verified", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("is_admin", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("failed_login_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("locked_until", sa.DateTime, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime, nullable=True),
    )
    op.create_index("idx_users_email", "users", ["email"])

    # ------------------------------------------------------------------
    # vehicles  (encrypted VIN / license_plate — PII)
    # ------------------------------------------------------------------
    op.create_table(
        "vehicles",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("license_plate", sa.String(20), unique=True, nullable=True),
        sa.Column("manufacturer", sa.String(100), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("year", sa.Integer, nullable=False),
        sa.Column("vin", sa.String(17), nullable=True),
        sa.Column("engine_type", sa.String(50), nullable=True),
        sa.Column("transmission", sa.String(50), nullable=True),
        sa.Column("fuel_type", sa.String(50), nullable=True),
        sa.Column("gov_api_data", JSONB, nullable=True),
        sa.Column("cached_at", sa.DateTime, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("idx_vehicles_manufacturer_model", "vehicles", ["manufacturer", "model"])

    # ------------------------------------------------------------------
    # user_profiles
    # ------------------------------------------------------------------
    op.create_table(
        "user_profiles",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False),
        sa.Column("address_line1", sa.String(255), nullable=True),
        sa.Column("address_line2", sa.String(255), nullable=True),
        sa.Column("city", sa.String(100), nullable=True),
        sa.Column("postal_code", sa.String(20), nullable=True),
        sa.Column("default_vehicle_id", UUID(as_uuid=True), sa.ForeignKey("vehicles.id"), nullable=True),
        sa.Column("marketing_consent", sa.Boolean, server_default="false"),
        sa.Column("newsletter_subscribed", sa.Boolean, server_default="false"),
        sa.Column("terms_accepted_at", sa.DateTime, nullable=True),
        sa.Column("marketing_preferences", JSONB, nullable=True),
        sa.Column("preferred_language", sa.String(10), server_default="he"),
        sa.Column("avatar_url", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime, nullable=True),
    )

    # ------------------------------------------------------------------
    # user_sessions
    # ------------------------------------------------------------------
    op.create_table(
        "user_sessions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token", sa.String(500), unique=True, nullable=False),
        sa.Column("refresh_token", sa.String(500), unique=True, nullable=True),
        sa.Column("device_fingerprint", sa.String(255), nullable=True),
        sa.Column("device_name", sa.String(255), nullable=True),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("user_agent", sa.Text, nullable=True),
        sa.Column("is_trusted_device", sa.Boolean, server_default="false"),
        sa.Column("trusted_until", sa.DateTime, nullable=True),
        sa.Column("expires_at", sa.DateTime, nullable=False),
        sa.Column("last_used_at", sa.DateTime, server_default=sa.text("now()")),
        sa.Column("revoked_at", sa.DateTime, nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.text("now()")),
    )
    op.create_index("idx_user_sessions_user_id", "user_sessions", ["user_id"])

    # ------------------------------------------------------------------
    # two_factor_codes
    # ------------------------------------------------------------------
    op.create_table(
        "two_factor_codes",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("code", sa.String(6), nullable=False),
        sa.Column("phone", sa.String(20), nullable=True),
        sa.Column("attempts", sa.Integer, server_default="0"),
        sa.Column("expires_at", sa.DateTime, nullable=False),
        sa.Column("verified_at", sa.DateTime, nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.text("now()")),
    )
    op.create_index("idx_two_factor_codes_user_id", "two_factor_codes", ["user_id"])

    # ------------------------------------------------------------------
    # login_attempts
    # ------------------------------------------------------------------
    op.create_table(
        "login_attempts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("ip_address", sa.String(45), nullable=False),
        sa.Column("success", sa.Boolean, nullable=False),
        sa.Column("failure_reason", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.text("now()")),
    )
    op.create_index("idx_login_attempts_ip", "login_attempts", ["ip_address"])
    op.create_index("idx_login_attempts_created_at", "login_attempts", ["created_at"])

    # ------------------------------------------------------------------
    # password_resets
    # ------------------------------------------------------------------
    op.create_table(
        "password_resets",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token", sa.String(255), unique=True, nullable=False),
        sa.Column("expires_at", sa.DateTime, nullable=False),
        sa.Column("used_at", sa.DateTime, nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.text("now()")),
    )

    # ------------------------------------------------------------------
    # user_vehicles  (link table — PII because it links user to vehicle)
    # ------------------------------------------------------------------
    op.create_table(
        "user_vehicles",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("vehicle_id", UUID(as_uuid=True), sa.ForeignKey("vehicles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("nickname", sa.String(100), nullable=True),
        sa.Column("is_primary", sa.Boolean, server_default="false"),
        sa.Column("created_at", sa.DateTime, server_default=sa.text("now()")),
        sa.UniqueConstraint("user_id", "vehicle_id"),
    )
    op.create_index("idx_user_vehicles_user_id", "user_vehicles", ["user_id"])

    # ------------------------------------------------------------------
    # orders
    # ------------------------------------------------------------------
    op.create_table(
        "orders",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        # Part/supplier IDs are stored without FK (cross-DB reference to catalog)
        sa.Column("vehicle_id", UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(50), nullable=False, server_default="pending"),
        sa.Column("subtotal_ils", sa.Numeric(12, 2), nullable=False),
        sa.Column("vat_ils", sa.Numeric(12, 2), nullable=False),
        sa.Column("shipping_ils", sa.Numeric(12, 2), server_default="0"),
        sa.Column("total_ils", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(3), server_default="ILS"),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("shipping_address", JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime, nullable=True),
    )
    op.create_index("idx_orders_user_id", "orders", ["user_id"])
    op.create_index("idx_orders_status", "orders", ["status"])

    # ------------------------------------------------------------------
    # order_items
    # ------------------------------------------------------------------
    op.create_table(
        "order_items",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("order_id", UUID(as_uuid=True), sa.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False),
        # catalog DB references — no FK constraint cross-DB
        sa.Column("part_id", UUID(as_uuid=True), nullable=False),
        sa.Column("supplier_id", UUID(as_uuid=True), nullable=True),
        sa.Column("sku", sa.String(100), nullable=False),
        sa.Column("name_he", sa.String(255), nullable=True),
        sa.Column("name_en", sa.String(255), nullable=True),
        sa.Column("quantity", sa.Integer, nullable=False),
        sa.Column("unit_price_ils", sa.Numeric(12, 2), nullable=False),
        sa.Column("total_price_ils", sa.Numeric(12, 2), nullable=False),
        sa.Column("is_express", sa.Boolean, server_default="false"),
        sa.Column("created_at", sa.DateTime, server_default=sa.text("now()")),
    )
    op.create_index("idx_order_items_order_id", "order_items", ["order_id"])

    # ------------------------------------------------------------------
    # payments
    # ------------------------------------------------------------------
    op.create_table(
        "payments",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("order_id", UUID(as_uuid=True), sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("amount_ils", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(3), server_default="ILS"),
        sa.Column("status", sa.String(50), nullable=False, server_default="pending"),
        sa.Column("provider", sa.String(50), nullable=True),
        sa.Column("provider_transaction_id", sa.String(255), nullable=True),
        sa.Column("last_four", sa.String(4), nullable=True),
        sa.Column("card_brand", sa.String(30), nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime, nullable=True),
    )
    op.create_index("idx_payments_order_id", "payments", ["order_id"])
    op.create_index("idx_payments_user_id", "payments", ["user_id"])

    # ------------------------------------------------------------------
    # invoices
    # ------------------------------------------------------------------
    op.create_table(
        "invoices",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("order_id", UUID(as_uuid=True), sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("invoice_number", sa.String(50), unique=True, nullable=False),
        sa.Column("total_ils", sa.Numeric(12, 2), nullable=False),
        sa.Column("vat_ils", sa.Numeric(12, 2), nullable=False),
        sa.Column("issued_at", sa.DateTime, server_default=sa.text("now()")),
        sa.Column("due_at", sa.DateTime, nullable=True),
        sa.Column("pdf_url", sa.String(500), nullable=True),
        sa.Column("status", sa.String(50), server_default="issued"),
        sa.Column("created_at", sa.DateTime, server_default=sa.text("now()")),
    )

    # ------------------------------------------------------------------
    # returns
    # ------------------------------------------------------------------
    op.create_table(
        "returns",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("order_id", UUID(as_uuid=True), sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("status", sa.String(50), server_default="pending"),
        sa.Column("refund_amount_ils", sa.Numeric(12, 2), nullable=True),
        sa.Column("approved_at", sa.DateTime, nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime, nullable=True),
    )
    op.create_index("idx_returns_user_id", "returns", ["user_id"])

    # ------------------------------------------------------------------
    # conversations
    # ------------------------------------------------------------------
    op.create_table(
        "conversations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("session_id", sa.String(100), nullable=True),
        sa.Column("context", JSONB, nullable=True),
        sa.Column("status", sa.String(50), server_default="active"),
        sa.Column("created_at", sa.DateTime, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime, nullable=True),
    )
    op.create_index("idx_conversations_user_id", "conversations", ["user_id"])

    # ------------------------------------------------------------------
    # messages
    # ------------------------------------------------------------------
    op.create_table(
        "messages",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("conversation_id", UUID(as_uuid=True), sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("metadata", JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.text("now()")),
    )
    op.create_index("idx_messages_conversation_id", "messages", ["conversation_id"])

    # ------------------------------------------------------------------
    # notifications
    # ------------------------------------------------------------------
    op.create_table(
        "notifications",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("body", sa.Text, nullable=True),
        sa.Column("type", sa.String(50), server_default="info"),
        sa.Column("is_read", sa.Boolean, server_default="false"),
        sa.Column("metadata", JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.text("now()")),
        sa.Column("read_at", sa.DateTime, nullable=True),
    )
    op.create_index("idx_notifications_user_id", "notifications", ["user_id"])
    op.create_index("idx_notifications_unread", "notifications", ["user_id", "is_read"])


def downgrade() -> None:
    for table in [
        "notifications", "messages", "conversations",
        "returns", "invoices", "payments", "order_items", "orders",
        "user_vehicles", "password_resets", "login_attempts",
        "two_factor_codes", "user_sessions", "user_profiles", "users", "vehicles",
    ]:
        op.drop_table(table)
