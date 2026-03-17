"""Add parts_master table (Phase 1.2).

Revision ID: 0005_add_parts_master
Revises: 0004_add_vehicles
Create Date: 2026-03-16
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0005_add_parts_master"
down_revision = "0004_add_vehicles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "parts_master",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("canonical_name",    sa.String(255), nullable=False),
        sa.Column("canonical_name_he", sa.String(255), nullable=True),
        sa.Column("category",          sa.String(100), nullable=False),
        sa.Column("part_type",         sa.String(50),  nullable=True),
        sa.Column("is_safety_critical", sa.Boolean(), nullable=False,
                  server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(), nullable=True,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(), nullable=True,
                  server_default=sa.text("now()")),
    )
    op.create_index("idx_parts_master_category",       "parts_master", ["category"])
    op.create_index("idx_parts_master_canonical_name", "parts_master", ["canonical_name"])


def downgrade() -> None:
    op.drop_index("idx_parts_master_canonical_name", table_name="parts_master")
    op.drop_index("idx_parts_master_category",       table_name="parts_master")
    op.drop_table("parts_master")
