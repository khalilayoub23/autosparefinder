"""initial empty migration

Revision ID: 0001_initial
Revises: 
Create Date: 2026-02-08 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '0001_initial'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # initial repository scaffold — add real table creation via `alembic revision --autogenerate`
    pass


def downgrade() -> None:
    pass
