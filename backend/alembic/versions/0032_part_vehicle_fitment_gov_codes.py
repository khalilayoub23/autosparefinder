"""add gov code columns to part_vehicle_fitment

Revision ID: 0032_part_vehicle_fitment_gov_codes
Revises: 0031_vehicle_market_il
Create Date: 2026-04-06 00:05:00

"""

from alembic import op
import sqlalchemy as sa


revision = "0032"
down_revision = "0031_vehicle_market_il"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("part_vehicle_fitment", sa.Column("tozeret_cd", sa.Integer(), nullable=True))
    op.add_column("part_vehicle_fitment", sa.Column("degem_cd", sa.Integer(), nullable=True))
    op.add_column("part_vehicle_fitment", sa.Column("shnat_yitzur", sa.Integer(), nullable=True))
    op.add_column(
        "part_vehicle_fitment",
        sa.Column("updated_at", sa.DateTime(), nullable=True, server_default=sa.func.now()),
    )

    op.create_index(
        "idx_pvf_tozeret_degem",
        "part_vehicle_fitment",
        ["tozeret_cd", "degem_cd", "shnat_yitzur"],
        unique=False,
    )
    op.create_index(
        "idx_pvf_manufacturer_model",
        "part_vehicle_fitment",
        ["manufacturer", "model", "year_from", "year_to"],
        unique=False,
    )
    op.create_index(
        "uix_pvf_part_mfr_model_year_from",
        "part_vehicle_fitment",
        ["part_id", "manufacturer", "model", "year_from"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uix_pvf_part_mfr_model_year_from", table_name="part_vehicle_fitment")
    op.drop_index("idx_pvf_manufacturer_model", table_name="part_vehicle_fitment")
    op.drop_index("idx_pvf_tozeret_degem", table_name="part_vehicle_fitment")

    op.drop_column("part_vehicle_fitment", "updated_at")
    op.drop_column("part_vehicle_fitment", "shnat_yitzur")
    op.drop_column("part_vehicle_fitment", "degem_cd")
    op.drop_column("part_vehicle_fitment", "tozeret_cd")
