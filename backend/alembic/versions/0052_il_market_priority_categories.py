"""Add il_market_priority to car_brands and normalize Hebrew category names

Revision ID: 0052_il_market_priority_categories
Revises: 0051_scraper_api_calls_align
Create Date: 2026-05-12
"""
from alembic import op
import sqlalchemy as sa

revision = "0052_il_mkt_priority"
down_revision = "0051_scraper_api_calls_align"
branch_labels = None
depends_on = None

# Hebrew category name → English canonical name
_CATEGORY_MAP = [
    (["בלמים", "רפידות בלם", "דיסקים"],           "brakes"),
    (["מנוע", "מנועים"],                            "engine"),
    (["תאורה", "פנסים", "פנס קדמי", "פנס אחורי"],  "lighting"),
    (["מערכת דלק", "מסנן דלק", "מסנן אוויר"],      "fuel-air"),
    (["כללי", "מסנן שמן"],                          "service-general"),
    (["בולם זעזועים", "קפיצים", "זרוע היגוי"],     "suspension"),
    (["מצבר", "אלטרנטור", "מצת"],                  "electrical"),
    (["תרמוסטט", "משאבת מים", "מצנן"],             "cooling"),
    (["מצמד", "גיר", "תיבת הילוכים"],             "transmission"),
    (["רצועת תזמון", "שרשרת תזמון"],               "timing"),
    (["פגוש קדמי", "פגוש אחורי", "מראה צד"],       "body"),
]


def upgrade() -> None:
    op.add_column(
        "car_brands",
        sa.Column("il_market_priority", sa.Integer(), nullable=True),
    )
    op.create_index(
        "idx_car_brands_il_priority",
        "car_brands",
        ["il_market_priority"],
        postgresql_where=sa.text("il_market_priority IS NOT NULL"),
    )

    conn = op.get_bind()
    for he_names, en_name in _CATEGORY_MAP:
        for he in he_names:
            conn.execute(
                sa.text(
                    "UPDATE parts_catalog SET category = :en "
                    "WHERE category = :he AND is_active = TRUE"
                ),
                {"en": en_name, "he": he},
            )


def downgrade() -> None:
    op.drop_index("idx_car_brands_il_priority", table_name="car_brands")
    op.drop_column("car_brands", "il_market_priority")
