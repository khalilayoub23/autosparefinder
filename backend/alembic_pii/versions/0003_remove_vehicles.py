"""Remove vehicles table from autospare_pii (moved to autospare catalog DB).

Revision ID: 0003_remove_vehicles
Revises: 0002_pii_correct_schema
Create Date: 2026-03-16
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "0003_remove_vehicles"
down_revision = "0002_pii_correct_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop FK constraints (columns remain as plain UUIDs — cross-DB refs no longer enforced)
    # Use IF EXISTS — the table may already have been manually dropped (idempotent)
    op.execute(sa.text("ALTER TABLE user_profiles DROP CONSTRAINT IF EXISTS user_profiles_default_vehicle_id_fkey"))
    op.execute(sa.text("ALTER TABLE user_vehicles DROP CONSTRAINT IF EXISTS user_vehicles_vehicle_id_fkey"))
    # Drop indexes then the table
    op.execute(sa.text("DROP INDEX IF EXISTS ix_vehicles_manufacturer"))
    op.execute(sa.text("DROP INDEX IF EXISTS idx_vehicles_manufacturer_model"))
    op.execute(sa.text("DROP TABLE IF EXISTS vehicles"))


def downgrade() -> None:
    op.create_table(
        "vehicles",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("license_plate", sa.String(20), unique=True, nullable=True),
        sa.Column("manufacturer",  sa.String(100), nullable=False),
        sa.Column("model",         sa.String(100), nullable=False),
        sa.Column("year",          sa.Integer,     nullable=False),
        sa.Column("vin",           sa.String(17),  nullable=True),
        sa.Column("engine_type",   sa.String(50),  nullable=True),
        sa.Column("transmission",  sa.String(50),  nullable=True),
        sa.Column("fuel_type",     sa.String(50),  nullable=True),
        sa.Column("gov_api_data",  JSONB,          nullable=True),
        sa.Column("cached_at",     sa.DateTime,    nullable=True),
        sa.Column("created_at",    sa.DateTime,    nullable=True,
                  server_default=sa.text("now()")),
    )
    op.create_index("idx_vehicles_manufacturer_model", "vehicles",
                    ["manufacturer", "model"])
    op.create_index("ix_vehicles_manufacturer", "vehicles", ["manufacturer"])
    op.create_foreign_key(
        "user_profiles_default_vehicle_id_fkey",
        "user_profiles", "vehicles", ["default_vehicle_id"], ["id"],
    )
    op.create_foreign_key(
        "user_vehicles_vehicle_id_fkey",
        "user_vehicles", "vehicles", ["vehicle_id"], ["id"],
        ondelete="CASCADE",
    )
