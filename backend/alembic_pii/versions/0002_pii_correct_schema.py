"""Schema corrections applied to autospare_pii (stub — already applied to DB).

Revision ID: 0002_pii_correct_schema
Revises: 0001_pii_initial
Create Date: 2026-03-16
"""
from alembic import op

revision = "0002_pii_correct_schema"
down_revision = "0001_pii_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass  # Already applied — stub for migration chain tracking only.


def downgrade() -> None:
    pass
