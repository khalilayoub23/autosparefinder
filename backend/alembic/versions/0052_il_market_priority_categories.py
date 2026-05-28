"""Add il_market_priority to car_brands; normalize Hebrew/English category duplicates

Revision ID: 0052_il_market_priority_categories
Revises: 0051_scraper_api_calls_schema_alignment
Create Date: 2026-05-12

Purpose:
  1. Add il_market_priority (integer) to car_brands so the discovery cycle can
     scrape Israeli-market brands first.  Lower value = higher priority.
     NULL = not yet triaged.
  2. Normalize Hebrew-named categories into their canonical English equivalents
     in parts_catalog so categories are not duplicated.
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = "0052_il_market_priority_categories"
down_revision = "0051_scraper_api_calls_schema_alignment"
branch_labels = None
depends_on = None

IL_PRIORITY_MAP = {
    "toyota":       1,
    "hyundai":      2,
    "kia":          3,
    "mazda":        4,
    "volkswagen":   5,
    "bmw":          6,
    "mercedes-benz":7,
    "honda":        8,
    "nissan":       9,
    "skoda":       10,
    "audi":        11,
    "ford":        12,
    "subaru":      13,
    "mitsubishi":  14,
    "renault":     15,
    "peugeot":     16,
    "citroen":     17,
    "opel":        18,
    "suzuki":      19,
    "volvo":       20,
    "tesla":       21,
    "fiat":        22,
    "seat":        23,
    "cupra":       24,
    "dacia":       25,
    "mini":        26,
    "jeep":        27,
    "chevrolet":   28,
    "land rover":  29,
    "lexus":       30,
    "byd":         31,
    "mg":          32,
    "xpeng":       33,
    "nio":         34,
    "geely":       35,
    "great wall":  36,
    "haval":       37,
    "maxus":       38,
    "jaecoo":      39,
    "ora":         40,
    "smart":       41,
    "genesis":     42,
    "ssangyong":   43,
    "chrysler":    44,
    "dodge":       45,
}

CATEGORY_NORMALIZE_MAP = {
    "\u05d1\u05dc\u05de\u05d9\u05dd":        "brakes",
    "\u05de\u05e0\u05d5\u05e2":         "engine",
    "\u05ea\u05d0\u05d5\u05e8\u05d4":        "lighting",
    "\u05de\u05e2\u05e8\u05db\u05ea \u05d3\u05dc\u05e7":    "fuel-air",
    "\u05db\u05dc\u05dc\u05d9":         "service-general",
    "\u05d2\u05dc\u05d2\u05dc\u05d9\u05dd \u05d5\u05e6\u05de\u05d9\u05d2\u05d9\u05dd": "wheels-bearings",
    "\u05d4\u05d9\u05d2\u05d5\u05d9":        "suspension-steering",
    "\u05d7\u05e9\u05de\u05dc \u05e8\u05db\u05d1":     "electrical-sensors",
    "\u05de\u05d2\u05d1\u05d9\u05dd":        "wipers-washers",
    "\u05de\u05e1\u05e0\u05e0\u05d9 \u05d0\u05d5\u05d5\u05d9\u05e8":  "filters",
    "\u05e7\u05d9\u05e8\u05d5\u05e8":        "cooling",
    "\u05d2\u05d5\u05e3 \u05e8\u05db\u05d1":     "body-exterior",
    "\u05e4\u05e0\u05d9\u05dd \u05e8\u05db\u05d1":     "interior-comfort",
    "\u05d0\u05e7\u05dc\u05d9\u05dd":        "air-conditioning-heating",
    "\u05ea\u05d9\u05d1\u05ea \u05d4\u05d9\u05dc\u05d5\u05db\u05d9\u05dd": "gearbox",
    "\u05de\u05e6\u05de\u05d3":         "clutch-drivetrain",
    "\u05e4\u05dc\u05d9\u05d8\u05d4":        "exhaust",
    "\u05e8\u05e6\u05d5\u05e2\u05d5\u05ea \u05d5\u05e9\u05e8\u05e9\u05e8\u05d0\u05d5\u05ea": "belts-chains",
    "\u05e0\u05d5\u05d6\u05dc\u05d9\u05dd":       "fluids",
    "accessories":  "accessories",
}


def upgrade() -> None:
    op.add_column(
        "car_brands",
        sa.Column("il_market_priority", sa.Integer(), nullable=True),
    )
    op.create_index(
        "idx_car_brands_il_priority",
        "car_brands",
        ["il_market_priority"],
    )

    conn = op.get_bind()
    for brand_name, priority in IL_PRIORITY_MAP.items():
        conn.execute(
            sa.text(
                "UPDATE car_brands SET il_market_priority = :p "
                "WHERE LOWER(name) = :n AND il_market_priority IS NULL"
            ),
            {"p": priority, "n": brand_name},
        )

    for he_cat, en_cat in CATEGORY_NORMALIZE_MAP.items():
        if he_cat == en_cat:
            continue
        conn.execute(
            sa.text(
                "UPDATE parts_catalog SET category = :en WHERE category = :he"
            ),
            {"en": en_cat, "he": he_cat},
        )


def downgrade() -> None:
    op.drop_index("idx_car_brands_il_priority", table_name="car_brands")
    op.drop_column("car_brands", "il_market_priority")
