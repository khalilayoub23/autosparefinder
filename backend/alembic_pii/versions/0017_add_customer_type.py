"""Add customer_type, total_orders, total_spent_ils, is_vip, vip_since to user_profiles.

Revision ID: 0017_add_customer_type
Revises: 0016_missing_indexes
Create Date: 2026-03-20
"""
from alembic import op
import sqlalchemy as sa

revision = "0017_add_customer_type"
down_revision = "0016_missing_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE user_profiles
            ADD COLUMN IF NOT EXISTS customer_type VARCHAR(20) NOT NULL DEFAULT 'individual'
                CONSTRAINT user_profiles_customer_type_check
                    CHECK (customer_type IN ('individual', 'mechanic', 'garage', 'retailer', 'fleet')),
            ADD COLUMN IF NOT EXISTS total_orders    INTEGER       NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS total_spent_ils NUMERIC(12,2) NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS is_vip          BOOLEAN       NOT NULL DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS vip_since       TIMESTAMP     NULL
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_user_profiles_customer_type ON user_profiles(customer_type)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_user_profiles_is_vip ON user_profiles(is_vip)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_user_profiles_is_vip")
    op.execute("DROP INDEX IF EXISTS ix_user_profiles_customer_type")
    op.execute("""
        ALTER TABLE user_profiles
            DROP COLUMN IF EXISTS vip_since,
            DROP COLUMN IF EXISTS is_vip,
            DROP COLUMN IF EXISTS total_spent_ils,
            DROP COLUMN IF EXISTS total_orders,
            DROP COLUMN IF EXISTS customer_type
    """)
