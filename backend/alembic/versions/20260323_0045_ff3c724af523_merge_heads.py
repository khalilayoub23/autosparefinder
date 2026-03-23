"""merge_heads

Revision ID: ff3c724af523
Revises: 1d3fc90b8d2c
Create Date: 2026-03-23 00:45:07.844031
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic
revision: str = 'ff3c724af523'
down_revision: Union[str, None] = '1d3fc90b8d2c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
