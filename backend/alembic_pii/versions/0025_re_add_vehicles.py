"""Re-add vehicles table to autospare_pii DB.

Vehicle contains PII (license_plate, VIN) and belongs in the PII database.
This migration re-creates the table and restores FK constraints that were
removed in 0003_remove_vehicles.

Revision ID: 0025_re_add_vehicles
Revises: 0024_return_supplier_confirm
Create Date: 2026-06-01
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
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
    # Clear stale cross-DB vehicle references before re-instating FK constraints
    op.execute("UPDATE user_profiles SET default_vehicle_id = NULL "
               "WHERE default_vehicle_id IS NOT NULL")
    op.execute("DELETE FROM user_vehicles")
    op.create_foreign_key(
        "user_profiles_default_vehicle_id_fkey",
        "user_profiles", "vehicles", ["default_vehicle_id"], ["id"],
    )
    op.create_foreign_key(
        "user_vehicles_vehicle_id_fkey",
        "user_vehicles", "vehicles", ["vehicle_id"], ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint("user_profiles_default_vehicle_id_fkey",
                       "user_profiles", type_="foreignkey")
    op.drop_constraint("user_vehicles_vehicle_id_fkey",
                       "user_vehicles", type_="foreignkey")
    op.drop_index("ix_vehicles_manufacturer",        table_name="vehicles")
    op.drop_index("idx_vehicles_manufacturer_model", table_name="vehicles")
    op.drop_table("vehicles")
