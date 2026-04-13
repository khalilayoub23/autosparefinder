"""ensure vehicle_market_il table exists in catalog DB

Revision ID: 0033_ensure_vehicle_market_il
Revises: 0032
Create Date: 2026-04-13
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0033_ensure_vehicle_market_il"
down_revision = "0032"
branch_labels = None
depends_on = None


def _table_exists(table_name: str) -> bool:
    conn = op.get_bind()
    result = conn.execute(
        sa.text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema='public' AND table_name=:t"
        ),
        {"t": table_name},
    )
    return result.fetchone() is not None


def _index_exists(index_name: str) -> bool:
    conn = op.get_bind()
    result = conn.execute(
        sa.text(
            "SELECT 1 FROM pg_indexes "
            "WHERE schemaname='public' AND indexname=:i"
        ),
        {"i": index_name},
    )
    return result.fetchone() is not None


def upgrade() -> None:
    if not _table_exists("vehicle_market_il"):
        op.create_table(
            "vehicle_market_il",
            sa.Column(
                "id",
                postgresql.UUID(as_uuid=True),
                nullable=False,
                server_default=sa.text("gen_random_uuid()"),
            ),
            sa.Column("tozeret_cd", sa.Integer(), nullable=True),
            sa.Column("manufacturer", sa.String(length=150), nullable=True),
            sa.Column("manufacturer_nm", sa.String(length=150), nullable=True),
            sa.Column("country", sa.String(length=100), nullable=True),
            sa.Column("degem_cd", sa.Integer(), nullable=True),
            sa.Column("degem_nm", sa.String(length=100), nullable=True),
            sa.Column("kinuy_mishari", sa.String(length=150), nullable=True),
            sa.Column("shnat_yitzur", sa.Integer(), nullable=True),
            sa.Column("sug_degem", sa.String(length=10), nullable=True),
            sa.Column("mispar_rechavim_pailim", sa.Integer(), nullable=True),
            sa.Column("mispar_rechavim_le_pailim", sa.Integer(), nullable=True),
            sa.Column("nefah_manoa", sa.Integer(), nullable=True),
            sa.Column("koah_sus", sa.Integer(), nullable=True),
            sa.Column("delek_nm", sa.String(length=50), nullable=True),
            sa.Column("technologiat_hanaa_nm", sa.String(length=100), nullable=True),
            sa.Column("sug_tkina_nm", sa.String(length=50), nullable=True),
            sa.Column("ramat_gimur", sa.String(length=50), nullable=True),
            sa.Column("kvutzat_zihum", sa.Integer(), nullable=True),
            sa.Column("madad_yarok", sa.Integer(), nullable=True),
            sa.Column("automatic_ind", sa.SmallInteger(), nullable=True),
            sa.Column("source_tag", sa.String(length=50), nullable=True, server_default="gov_il"),
            sa.Column("updated_at", sa.DateTime(), nullable=True, server_default=sa.func.now()),
            sa.PrimaryKeyConstraint("id"),
        )

    if not _index_exists("uix_vehicle_market_il"):
        op.create_index(
            "uix_vehicle_market_il",
            "vehicle_market_il",
            ["tozeret_cd", "degem_cd", "shnat_yitzur"],
            unique=True,
        )


def downgrade() -> None:
    if _index_exists("uix_vehicle_market_il"):
        op.drop_index("uix_vehicle_market_il", table_name="vehicle_market_il")
    if _table_exists("vehicle_market_il"):
        op.drop_table("vehicle_market_il")
