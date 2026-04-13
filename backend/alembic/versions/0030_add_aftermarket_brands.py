"""add aftermarket brands and link parts_catalog

Revision ID: 0030_add_aftermarket_brands
Revises: 0029
Create Date: 2026-04-06 00:00:00

"""

import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0030_add_aftermarket_brands"
down_revision = "0029"
branch_labels = None
depends_on = None


OE_EQUIVALENT_BRANDS = [
    "Bosch",
    "Denso",
    "Valeo",
    "NGK",
    "Febi",
    "Gates",
    "Mann",
    "Brembo",
    "KYB",
    "Sachs",
    "Mahle",
    "SKF",
    "Hella",
    "Delphi",
    "LuK",
    "Bilstein",
]

ECONOMY_BRANDS = [
    "Meyle",
    "Maxgear",
    "Optimal",
]


def upgrade() -> None:
    op.create_table(
        "aftermarket_brands",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("tier", sa.String(length=20), nullable=False, server_default="generic"),
        sa.Column("categories", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("country", sa.String(length=50), nullable=True),
        sa.Column("website", sa.String(length=255), nullable=True),
        sa.Column("logo_url", sa.String(length=255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=True, server_default=sa.func.now()),
        sa.CheckConstraint(
            "tier IN ('OE_equivalent','economy','generic')",
            name="ck_aftermarket_brands_tier",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_aftermarket_brands_name"),
    )

    op.create_index(
        "ix_aftermarket_brands_name",
        "aftermarket_brands",
        ["name"],
        unique=False,
    )

    op.add_column("parts_catalog", sa.Column("aftermarket_tier", sa.String(length=20), nullable=True))
    op.create_check_constraint(
        "ck_parts_catalog_aftermarket_tier",
        "parts_catalog",
        "aftermarket_tier IS NULL OR aftermarket_tier IN ('OE_equivalent','economy','generic')",
    )

    op.add_column(
        "parts_catalog",
        sa.Column("aftermarket_brand_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_parts_catalog_aftermarket_brand_id",
        "parts_catalog",
        "aftermarket_brands",
        ["aftermarket_brand_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_index(
        "idx_parts_catalog_aftermarket_brand",
        "parts_catalog",
        ["aftermarket_brand_id"],
        unique=False,
    )
    op.create_index(
        "idx_parts_catalog_part_condition_tier",
        "parts_catalog",
        ["part_condition", "aftermarket_tier"],
        unique=False,
    )

    seed_table = sa.table(
        "aftermarket_brands",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("name", sa.String(length=100)),
        sa.column("tier", sa.String(length=20)),
        sa.column("is_active", sa.Boolean()),
    )

    seed_rows = [
        {
            "id": uuid.uuid4(),
            "name": name,
            "tier": "OE_equivalent",
            "is_active": True,
        }
        for name in OE_EQUIVALENT_BRANDS
    ] + [
        {
            "id": uuid.uuid4(),
            "name": name,
            "tier": "economy",
            "is_active": True,
        }
        for name in ECONOMY_BRANDS
    ]

    op.bulk_insert(seed_table, seed_rows)


def downgrade() -> None:
    op.drop_index("idx_parts_catalog_part_condition_tier", table_name="parts_catalog")
    op.drop_index("idx_parts_catalog_aftermarket_brand", table_name="parts_catalog")
    op.drop_constraint("fk_parts_catalog_aftermarket_brand_id", "parts_catalog", type_="foreignkey")
    op.drop_constraint("ck_parts_catalog_aftermarket_tier", "parts_catalog", type_="check")
    op.drop_column("parts_catalog", "aftermarket_brand_id")
    op.drop_column("parts_catalog", "aftermarket_tier")

    op.drop_index("ix_aftermarket_brands_name", table_name="aftermarket_brands")
    op.drop_table("aftermarket_brands")
