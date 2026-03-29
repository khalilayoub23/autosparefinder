"""add supplier_parts.part_type column

Revision ID: 0029
Revises: 0028
Create Date: 2025-01-01 00:00:00

"""
from alembic import op
import sqlalchemy as sa

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "supplier_parts",
        sa.Column("part_type", sa.String(50), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("supplier_parts", "part_type")
