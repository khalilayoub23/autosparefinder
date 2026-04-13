"""add vehicle_market_il table for gov.il market data

Revision ID: 0031_vehicle_market_il
Revises: 0030_add_aftermarket_brands
Create Date: 2026-04-06 00:00:01

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0031_vehicle_market_il"
down_revision = "0030_add_aftermarket_brands"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "vehicle_market_il",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tozeret_cd", sa.Integer(), nullable=True),
        sa.Column("manufacturer", sa.String(length=150), nullable=True),
        sa.Column("manufacturer_nm", sa.String(length=150), nullable=True),
        sa.Column("country", sa.String(length=100), nullable=True),
        sa.Column("degem_cd", sa.Integer(), nullable=True),
        sa.Column("degem_nm", sa.String(length=100), nullable=True),
        sa.Column("kinuy_mishari", sa.String(length=150), nullable=True),
        sa.Column("shnat_yitzur", sa.Integer(), nullable=True),
        sa.Column("sug_degem", sa.String(length=10), nullable=True),
        sa.Column("mispar_rechavim_pailim", sa.Integer(), nullable=True),
        sa.Column("mispar_rechavim_le_pailim", sa.Integer(), nullable=True),
        sa.Column("nefah_manoa", sa.Integer(), nullable=True),
        sa.Column("koah_sus", sa.Integer(), nullable=True),
        sa.Column("delek_nm", sa.String(length=50), nullable=True),
        sa.Column("technologiat_hanaa_nm", sa.String(length=100), nullable=True),
        sa.Column("sug_tkina_nm", sa.String(length=50), nullable=True),
        sa.Column("ramat_gimur", sa.String(length=50), nullable=True),
        sa.Column("kvutzat_zihum", sa.Integer(), nullable=True),
        sa.Column("madad_yarok", sa.Integer(), nullable=True),
        sa.Column("automatic_ind", sa.SmallInteger(), nullable=True),
        sa.Column("source_tag", sa.String(length=50), nullable=True, server_default="gov_il"),
        sa.Column("updated_at", sa.DateTime(), nullable=True, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        "uix_vehicle_market_il",
        "vehicle_market_il",
        ["tozeret_cd", "degem_cd", "shnat_yitzur"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uix_vehicle_market_il", table_name="vehicle_market_il")
    op.drop_table("vehicle_market_il")
