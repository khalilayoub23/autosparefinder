"""add supplier_parts.updated_at column

Revision ID: 0028
Revises: 0027
Create Date: 2025-01-01 00:00:00

"""
from alembic import op
import sqlalchemy as sa

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "supplier_parts",
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=True,
            server_default=sa.text("NOW()"),
        ),
    )
    # Backfill: set updated_at = created_at for existing rows
    op.execute(sa.text("UPDATE supplier_parts SET updated_at = created_at WHERE updated_at IS NULL"))


def downgrade() -> None:
    op.drop_column("supplier_parts", "updated_at")
