"""Move vehicles table from autospare_pii to autospare (catalog DB).

Revision ID: 0004_add_vehicles
Revises: 0003_drop_pii_tables
Create Date: 2026-03-16
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "0004_add_vehicles"
down_revision = "0003_drop_pii_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
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


def downgrade() -> None:
    op.drop_index("ix_vehicles_manufacturer",        table_name="vehicles")
    op.drop_index("idx_vehicles_manufacturer_model", table_name="vehicles")
    op.drop_table("vehicles")
