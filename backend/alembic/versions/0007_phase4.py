"""Phase 4: reliability_score CHECK, brand_aliases, catalog_versions

Revision ID: 0007_phase4
Revises: 0006_add_part_variants
Create Date: 2026-03-17

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0007_phase4"
down_revision = "0006_add_part_variants"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------ #
    # 1. suppliers.reliability_score                                       #
    #    Clamp any values outside [0.00, 1.00], then add CHECK constraint  #
    # ------------------------------------------------------------------ #
    op.execute(
        "UPDATE suppliers SET reliability_score = 1.00 "
        "WHERE reliability_score > 1.00"
    )
    op.execute(
        "UPDATE suppliers SET reliability_score = 0.50 "
        "WHERE reliability_score IS NULL"
    )
    op.alter_column(
        "suppliers",
        "reliability_score",
        existing_type=sa.Numeric(3, 2),
        server_default=sa.text("0.50"),
        nullable=False,
    )
    op.create_check_constraint(
        "ck_suppliers_reliability_score_range",
        "suppliers",
        "reliability_score >= 0.00 AND reliability_score <= 1.00",
    )

    # ------------------------------------------------------------------ #
    # 2. brand_aliases                                                     #
    #    Normalised companion to car_brands.aliases[]; starts empty        #
    # ------------------------------------------------------------------ #
    op.create_table(
        "brand_aliases",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "brand_id",
            UUID(as_uuid=True),
            sa.ForeignKey("car_brands.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("alias", sa.String(200), nullable=False),
        sa.Column("normalized", sa.String(200), nullable=False),
        sa.Column("source", sa.String(50), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime,
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_brand_aliases_brand_id", "brand_aliases", ["brand_id"])
    op.create_index("ix_brand_aliases_normalized", "brand_aliases", ["normalized"])
    op.create_unique_constraint(
        "uq_brand_aliases_brand_alias", "brand_aliases", ["brand_id", "alias"]
    )

    # ------------------------------------------------------------------ #
    # 3. catalog_versions                                                  #
    #    Audit log for catalog import/sync runs                            #
    #    triggered_by is a plain UUID ref to autospare_pii.users (no FK)  #
    # ------------------------------------------------------------------ #
    op.create_table(
        "catalog_versions",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("version_tag", sa.String(50), nullable=False, unique=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("parts_added", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("parts_updated", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("parts_total", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("source", sa.String(100), nullable=True),
        # Plain UUID — cross-DB FK to autospare_pii.users is not possible
        sa.Column("triggered_by", UUID(as_uuid=True), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime,
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime, nullable=True),
        sa.Column(
            "status",
            sa.String(20),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("error_log", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime,
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_catalog_versions_status",
        "catalog_versions",
        "status IN ('pending', 'running', 'completed', 'failed')",
    )
    op.create_index("ix_catalog_versions_status", "catalog_versions", ["status"])
    op.create_index("ix_catalog_versions_started_at", "catalog_versions", ["started_at"])


def downgrade() -> None:
    # catalog_versions
    op.drop_index("ix_catalog_versions_started_at", table_name="catalog_versions")
    op.drop_index("ix_catalog_versions_status", table_name="catalog_versions")
    op.drop_table("catalog_versions")

    # brand_aliases
    op.drop_constraint("uq_brand_aliases_brand_alias", "brand_aliases", type_="unique")
    op.drop_index("ix_brand_aliases_normalized", table_name="brand_aliases")
    op.drop_index("ix_brand_aliases_brand_id", table_name="brand_aliases")
    op.drop_table("brand_aliases")

    # suppliers reliability_score
    op.drop_constraint(
        "ck_suppliers_reliability_score_range", "suppliers", type_="check"
    )
    op.alter_column(
        "suppliers",
        "reliability_score",
        existing_type=sa.Numeric(3, 2),
        server_default=None,
        nullable=True,
    )
