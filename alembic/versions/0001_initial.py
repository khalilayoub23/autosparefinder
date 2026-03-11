"""empty scaffold placeholder — superseded by 0001_initial_schema

Revision ID: 0000_scaffold
Revises:
Create Date: 2026-02-08 00:00:00.000000

NOTE: This file was a duplicate of 0001_initial_schema.py (both had
revision='0001_initial'). Renamed to 0000_scaffold to resolve the
Alembic MultipleHeads error. Real schema is in 0001_initial_schema.py.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '0000_scaffold'
down_revision = None
branch_labels = ('scaffold',)
depends_on = None


def upgrade() -> None:
    # initial repository scaffold — add real table creation via `alembic revision --autogenerate`
    pass


def downgrade() -> None:
    pass
