"""drop_part_vehicle_fitment_table

Revision ID: c03ba8486bd9
Revises: 0016
Create Date: 2026-03-23 00:40:59.865743
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic
revision: str = 'c03ba8486bd9'
down_revision: Union[str, None] = '0016'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table("part_vehicle_fitment")


def downgrade() -> None:
    op.create_table(
        "part_vehicle_fitment",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("part_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("parts_catalog.id", ondelete="CASCADE"), nullable=False),
        sa.Column("manufacturer", sa.String(100), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("year_from", sa.Integer(), nullable=False),
        sa.Column("year_to", sa.Integer(), nullable=True),
        sa.Column("engine_type", sa.String(50), nullable=True),
        sa.Column("transmission", sa.String(50), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_fitment_part_id", "part_vehicle_fitment", ["part_id"])
    op.create_index("idx_fitment_mfr_model", "part_vehicle_fitment", ["manufacturer", "model"])
    op.create_index("idx_fitment_years", "part_vehicle_fitment", ["year_from", "year_to"])
