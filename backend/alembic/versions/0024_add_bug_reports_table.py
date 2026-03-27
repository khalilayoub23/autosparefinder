"""Add bug_reports table"""

revision = "0024_add_bug_reports"
down_revision = "ff3c724af523"
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB


def upgrade():
    op.create_table(
        "bug_reports",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), nullable=True),
        sa.Column("user_role", sa.String(20), nullable=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("severity", sa.String(20), server_default="medium"),
        sa.Column("platform", sa.String(20), nullable=True),
        sa.Column("app_version", sa.String(20), nullable=True),
        sa.Column("screen_name", sa.String(100), nullable=True),
        sa.Column("endpoint_url", sa.String(500), nullable=True),
        sa.Column("http_method", sa.String(10), nullable=True),
        sa.Column("http_status_code", sa.Integer, nullable=True),
        sa.Column("error_trace", sa.Text, nullable=True),
        sa.Column("last_api_calls", JSONB, nullable=True),
        sa.Column("device_info", JSONB, nullable=True),
        sa.Column("tech_analysis", JSONB, nullable=True),
        sa.Column("status", sa.String(20), server_default="open"),
        sa.Column("admin_notes", sa.Text, nullable=True),
        sa.Column("resolved_at", sa.DateTime, nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now()),
    )
    op.create_index("idx_bug_reports_status", "bug_reports", ["status"])
    op.create_index("idx_bug_reports_severity", "bug_reports", ["severity"])
    op.create_index("idx_bug_reports_created", "bug_reports", ["created_at"])


def downgrade():
    op.drop_table("bug_reports")
