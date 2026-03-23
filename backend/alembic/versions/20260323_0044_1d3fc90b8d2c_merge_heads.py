"""merge_heads

Revision ID: 1d3fc90b8d2c
Revises: 4223ace7ec49
Create Date: 2026-03-23 00:44:37.235758
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic
revision: str = '1d3fc90b8d2c'
down_revision: Union[str, None] = '4223ace7ec49'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
