"""Add part_variants table (Phase 1.3).

Revision ID: 0006_add_part_variants
Revises: 0005_add_parts_master
Create Date: 2026-03-16
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0006_add_part_variants"
down_revision = "0005_add_parts_master"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "part_variants",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("master_part_id", UUID(as_uuid=True),
                  sa.ForeignKey("parts_master.id",  ondelete="CASCADE"), nullable=False),
        sa.Column("catalog_part_id", UUID(as_uuid=True),
                  sa.ForeignKey("parts_catalog.id", ondelete="CASCADE"), nullable=False),
        sa.Column("quality_level", sa.String(20),  nullable=False),
        sa.Column("manufacturer",  sa.String(100), nullable=True),
        sa.Column("sku",           sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True,
                  server_default=sa.text("now()")),
        sa.CheckConstraint(
            "quality_level IN ('OEM','OEM_Equivalent','Aftermarket_Premium',"
            "'Aftermarket_Standard','Economy')",
            name="ck_part_variants_quality_level",
        ),
        sa.UniqueConstraint(
            "master_part_id", "catalog_part_id",
            name="uq_part_variants_master_catalog",
        ),
    )
    op.create_index("idx_part_variants_master_part_id",  "part_variants", ["master_part_id"])
    op.create_index("idx_part_variants_catalog_part_id", "part_variants", ["catalog_part_id"])


def downgrade() -> None:
    op.drop_index("idx_part_variants_catalog_part_id", table_name="part_variants")
    op.drop_index("idx_part_variants_master_part_id",  table_name="part_variants")
    op.drop_table("part_variants")
