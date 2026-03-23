"""merge_heads

Revision ID: 4223ace7ec49
Revises: 0023_supplier_rate_limit, c03ba8486bd9
Create Date: 2026-03-23 00:44:08.575416
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic
revision: str = '4223ace7ec49'
down_revision: Union[str, None] = ('0023_supplier_rate_limit', 'c03ba8486bd9')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
