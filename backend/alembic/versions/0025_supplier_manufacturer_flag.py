"""Add is_manufacturer and manufacturer_name to suppliers"""

revision = "0025_supplier_manufacturer_flag"
down_revision = "0024_add_bug_reports"
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.add_column(
        "suppliers",
        sa.Column("is_manufacturer", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "suppliers",
        sa.Column("manufacturer_name", sa.String(255), nullable=True),
    )
    op.create_index(
        "idx_suppliers_manufacturer_name", "suppliers", ["manufacturer_name"]
    )


def downgrade():
    op.drop_index("idx_suppliers_manufacturer_name", table_name="suppliers")
    op.drop_column("suppliers", "manufacturer_name")
    op.drop_column("suppliers", "is_manufacturer")
