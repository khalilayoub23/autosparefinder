"""Add master_enriched to parts_catalog; add unique constraint on parts_master

Revision ID: 0008_add_master_enriched
Revises: 0007_phase4
Create Date: 2026-03-18
"""
from alembic import op
import sqlalchemy as sa

revision = "0008_add_master_enriched"
down_revision = "0007_phase4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Deduplicate parts_master — keep oldest row per (canonical_name, category)
    op.execute("""
        DELETE FROM parts_master
        WHERE id NOT IN (
            SELECT DISTINCT ON (canonical_name, category) id
            FROM parts_master
            ORDER BY canonical_name, category, created_at ASC
        )
    """)

    # 2. Unique constraint on parts_master (canonical_name, category)
    op.create_unique_constraint(
        "uq_parts_master_name_category",
        "parts_master",
        ["canonical_name", "category"],
    )

    # 3. master_enriched column on parts_catalog
    op.add_column(
        "parts_catalog",
        sa.Column(
            "master_enriched",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )

    # 4. Partial index for fast candidate lookup
    op.create_index(
        "idx_parts_catalog_needs_enrichment",
        "parts_catalog",
        ["master_enriched"],
        postgresql_where=sa.text(
            "master_enriched = false AND needs_oem_lookup = false"
        ),
    )


def downgrade() -> None:
    op.drop_index(
        "idx_parts_catalog_needs_enrichment",
        table_name="parts_catalog",
    )
    op.drop_column("parts_catalog", "master_enriched")
    op.drop_constraint(
        "uq_parts_master_name_category",
        "parts_master",
        type_="unique",
    )
