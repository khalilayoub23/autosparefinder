"""Create truck_brands and truck_brand_aliases tables; move MAN/Hino from car_brands"""

revision = "0026_truck_brands"
down_revision = "0025_supplier_manufacturer_flag"
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, ARRAY

TRUCK_ONLY_BRANDS = [
    # name,           name_he,          country,      region,   group_name
    ("MAN",           "מאן",            "Germany",    "Europe", "Traton Group"),
    ("Hino",          "הינו",           "Japan",      "Asia",   "Toyota Group"),
    ("Scania",        "סקניה",          "Sweden",     "Europe", "Traton Group"),
    ("Iveco",         "איבקו",          "Italy",      "Europe", "CNH Industrial"),
    ("DAF",           "דאף",            "Netherlands","Europe", "PACCAR"),
    ("Freightliner",  "פרייטליינר",     "USA",        "America","Daimler Trucks"),
    ("Kenworth",      "קנוורת'",        "USA",        "America","PACCAR"),
    ("Peterbilt",     "פיטרביילט",      "USA",        "America","PACCAR"),
    ("Mack",          "מאק",            "USA",        "America","Volvo Group"),
    ("Fuso",          "פוסו",           "Japan",      "Asia",   "Daimler Trucks"),
    ("UD Trucks",     "יו-די טראקס",   "Japan",      "Asia",   "Isuzu"),
    ("Isuzu Commercial", "איסוזו מסחרי","Japan",      "Asia",   "Isuzu"),
    ("Mercedes-Benz Trucks", "מרצדס טראקס", "Germany","Europe", "Daimler Trucks"),
    ("Volvo Trucks",  "וולוו טראקס",   "Sweden",     "Europe", "Volvo Group"),
]


def upgrade():
    # ── Create truck_brands ──────────────────────────────────────────────────
    op.create_table(
        "truck_brands",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(100), unique=True, nullable=False),
        sa.Column("name_he", sa.String(100), nullable=True),
        sa.Column("group_name", sa.String(100), nullable=True),
        sa.Column("country", sa.String(100), nullable=True),
        sa.Column("region", sa.String(50), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("logo_url", sa.String(500), nullable=True),
        sa.Column("website", sa.String(500), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("aliases", ARRAY(sa.String()), nullable=True, server_default="{}"),
        sa.Column("il_importer", sa.String(200), nullable=True),
        sa.Column("il_importer_website", sa.String(500), nullable=True),
        sa.Column("parts_availability", sa.String(20), nullable=True),
        sa.Column("avg_service_interval_km", sa.Integer(), nullable=True),
        sa.Column("popular_models_il", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_truck_brands_name", "truck_brands", ["name"])
    op.create_index("ix_truck_brands_group", "truck_brands", ["group_name"])

    # ── Create truck_brand_aliases ───────────────────────────────────────────
    op.create_table(
        "truck_brand_aliases",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("brand_id", UUID(as_uuid=True), sa.ForeignKey("truck_brands.id", ondelete="CASCADE"), nullable=False),
        sa.Column("alias", sa.String(200), nullable=False),
        sa.Column("normalized", sa.String(200), nullable=False),
        sa.Column("source", sa.String(50), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_truck_brand_aliases_brand_id", "truck_brand_aliases", ["brand_id"])
    op.create_index("ix_truck_brand_aliases_normalized", "truck_brand_aliases", ["normalized"])

    # ── Seed truck brands ────────────────────────────────────────────────────
    op.execute(sa.text("""
        INSERT INTO truck_brands (id, name, name_he, group_name, country, region, is_active, created_at, updated_at)
        VALUES
          (gen_random_uuid(), 'MAN',                 'מאן',             'Traton Group',    'Germany',     'Europe',  true, NOW(), NOW()),
          (gen_random_uuid(), 'Hino',                'הינו',            'Toyota Group',    'Japan',       'Asia',    true, NOW(), NOW()),
          (gen_random_uuid(), 'Scania',              'סקניה',           'Traton Group',    'Sweden',      'Europe',  true, NOW(), NOW()),
          (gen_random_uuid(), 'Iveco',               'איבקו',           'CNH Industrial',  'Italy',       'Europe',  true, NOW(), NOW()),
          (gen_random_uuid(), 'DAF',                 'דאף',             'PACCAR',          'Netherlands', 'Europe',  true, NOW(), NOW()),
          (gen_random_uuid(), 'Freightliner',        'פרייטליינר',      'Daimler Trucks',  'USA',         'America', true, NOW(), NOW()),
          (gen_random_uuid(), 'Kenworth',            'קנוורת',          'PACCAR',          'USA',         'America', true, NOW(), NOW()),
          (gen_random_uuid(), 'Peterbilt',           'פיטרביילט',       'PACCAR',          'USA',         'America', true, NOW(), NOW()),
          (gen_random_uuid(), 'Mack',                'מאק',             'Volvo Group',     'USA',         'America', true, NOW(), NOW()),
          (gen_random_uuid(), 'Fuso',                'פוסו',            'Daimler Trucks',  'Japan',       'Asia',    true, NOW(), NOW()),
          (gen_random_uuid(), 'UD Trucks',           'יו-די טראקס',    'Isuzu',           'Japan',       'Asia',    true, NOW(), NOW()),
          (gen_random_uuid(), 'Isuzu Commercial',    'איסוזו מסחרי',   'Isuzu',           'Japan',       'Asia',    true, NOW(), NOW()),
          (gen_random_uuid(), 'Mercedes-Benz Trucks','מרצדס טראקס',    'Daimler Trucks',  'Germany',     'Europe',  true, NOW(), NOW()),
          (gen_random_uuid(), 'Volvo Trucks',        'וולוו טראקס',    'Volvo Group',     'Sweden',      'Europe',  true, NOW(), NOW()),
          (gen_random_uuid(), 'Hyundai Trucks',      'יונדאי טראקס',   'Hyundai',         'South Korea', 'Asia',    true, NOW(), NOW()),
          (gen_random_uuid(), 'Tata Commercial',     'טאטא מסחרי',     'Tata Motors',     'India',       'Asia',    true, NOW(), NOW()),
          (gen_random_uuid(), 'Renault Trucks',      'רנו טראקס',      'Volvo Group',     'France',      'Europe',  true, NOW(), NOW())
        ON CONFLICT (name) DO NOTHING
    """))

    # ── Remove MAN and Hino from car_brands (no aliases → safe to delete) ───
    op.execute(sa.text("DELETE FROM car_brands WHERE name IN ('MAN', 'Hino')"))


def downgrade():
    # Restore MAN and Hino to car_brands
    op.execute(sa.text("""
        INSERT INTO car_brands (id, name, country, region, is_active, is_luxury, is_electric_focused, created_at, updated_at)
        VALUES
          (gen_random_uuid(), 'MAN',  'Germany', 'Europe', true, false, false, NOW(), NOW()),
          (gen_random_uuid(), 'Hino', 'Japan',   'Asia',   true, false, false, NOW(), NOW())
        ON CONFLICT (name) DO NOTHING
    """))
    op.drop_table("truck_brand_aliases")
    op.drop_table("truck_brands")
