"""Add supplier-confirmation columns to returns table for two-phase refund flow.

New columns:
  item_shipped_at       — when customer confirmed return shipment
  supplier_confirmed_at — when admin marked supplier as having confirmed the return
  refund_issued_at      — when we actually issued the refund to the customer
  supplier_notes        — free-text internal note from supplier / admin on the return

Revision ID: 0024
Revises: 0023
Create Date: 2026-03-28
"""

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.add_column("returns", sa.Column("item_shipped_at",       sa.DateTime(), nullable=True))
    op.add_column("returns", sa.Column("supplier_confirmed_at", sa.DateTime(), nullable=True))
    op.add_column("returns", sa.Column("refund_issued_at",      sa.DateTime(), nullable=True))
    op.add_column("returns", sa.Column("supplier_notes",        sa.Text(),     nullable=True))


def downgrade():
    op.drop_column("returns", "supplier_notes")
    op.drop_column("returns", "refund_issued_at")
    op.drop_column("returns", "supplier_confirmed_at")
    op.drop_column("returns", "item_shipped_at")
