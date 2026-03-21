"""Add per-supplier rate limit column

Revision ID: 0023_supplier_rate_limit
Revises: 0020_job_registry
Create Date: 2026-03-21
"""
from alembic import op
import sqlalchemy as sa

revision = "0023_supplier_rate_limit"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "suppliers",
        sa.Column(
            "rate_limit_per_minute",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("30"),
            comment="Per-supplier request rate limit (requests per minute)",
        ),
    )


def downgrade():
    op.drop_column("suppliers", "rate_limit_per_minute")
