"""catalog enhancements — new columns + new tables

Revision ID: 0002_catalog_enhancements
Revises: 999938805828
Create Date: 2026-03-03

Changes:
  parts_catalog    — name_he, oem_number, barcode, weight_kg,
                     importer_price_ils, online_price_ils,
                     part_condition, superseded_by_sku,
                     min_price_ils, max_price_ils,
                     customs_tariff_code, is_safety_critical,
                     search_vector (tsvector), needs_oem_lookup

  supplier_parts   — stock_quantity, min_order_qty, supplier_url,
                     last_in_stock_at,
                     express_available, express_price_ils,
                     express_delivery_days, express_cutoff_time,
                     express_last_checked

  suppliers        — supports_express, express_carrier,
                     express_base_cost_usd, avg_delivery_days_actual

  car_brands       — warranty_years, warranty_km, warranty_notes,
                     il_importer, il_importer_website,
                     parts_availability, avg_service_interval_km,
                     popular_models_il

  NEW TABLES:
    part_vehicle_fitment  — make/model/year_from/year_to fitment links
    part_cross_reference  — OEM / OEM_EQUIVALENT / AFTERMARKET cross-refs
    part_aliases          — search aliases (Hebrew variants)
    price_history         — per-row price change log
    purchase_orders       — supplier purchase order tracking
    scraper_api_calls     — external API call tracking
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
import uuid

revision = "0002_catalog_enhancements"
down_revision = "999938805828"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── parts_catalog — new columns ──────────────────────────────────────────
    op.add_column("parts_catalog", sa.Column("name_he", sa.String(255), nullable=True))
    op.add_column("parts_catalog", sa.Column("oem_number", sa.String(100), nullable=True))
    op.add_column("parts_catalog", sa.Column("barcode", sa.String(50), nullable=True))
    op.add_column("parts_catalog", sa.Column("weight_kg", sa.Numeric(6, 3), nullable=True))
    # All ILS prices stored WITH 18% VAT included
    op.add_column("parts_catalog", sa.Column("importer_price_ils", sa.Numeric(10, 2), nullable=True,
                                              comment="Israeli importer price incl. 18% VAT"))
    op.add_column("parts_catalog", sa.Column("online_price_ils", sa.Numeric(10, 2), nullable=True,
                                              comment="Competitor online reference price incl. 18% VAT"))
    op.add_column("parts_catalog", sa.Column("min_price_ils", sa.Numeric(10, 2), nullable=True,
                                              comment="Cheapest supplier price incl. 18% VAT — auto-updated by scraper"))
    op.add_column("parts_catalog", sa.Column("max_price_ils", sa.Numeric(10, 2), nullable=True,
                                              comment="Most expensive supplier price incl. 18% VAT"))
    op.add_column("parts_catalog", sa.Column(
        "part_condition", sa.String(20), nullable=False,
        server_default="New",
        comment="New / Used / Remanufactured",
    ))
    op.add_column("parts_catalog", sa.Column("superseded_by_sku", sa.String(100), nullable=True,
                                              comment="SKU of replacement part when this one is discontinued"))
    op.add_column("parts_catalog", sa.Column("customs_tariff_code", sa.String(20), nullable=True))
    op.add_column("parts_catalog", sa.Column("is_safety_critical", sa.Boolean(), nullable=False,
                                              server_default="false",
                                              comment="True for brakes, steering, airbags — affects warranty law"))
    op.add_column("parts_catalog", sa.Column("search_vector", TSVECTOR, nullable=True,
                                              comment="PostgreSQL full-text search vector (Hebrew + English)"))
    op.add_column("parts_catalog", sa.Column("needs_oem_lookup", sa.Boolean(), nullable=False,
                                              server_default="false",
                                              comment="True for fake/seeded SKUs awaiting real OEM number"))

    op.create_index("idx_parts_catalog_oem_number", "parts_catalog", ["oem_number"])
    op.create_index("idx_parts_catalog_search_vector", "parts_catalog",
                    ["search_vector"], postgresql_using="gin")
    op.create_index("idx_parts_catalog_superseded", "parts_catalog", ["superseded_by_sku"])

    # ── supplier_parts — new columns ─────────────────────────────────────────
    op.add_column("supplier_parts", sa.Column("stock_quantity", sa.Integer(), nullable=True))
    op.add_column("supplier_parts", sa.Column("min_order_qty", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("supplier_parts", sa.Column("supplier_url", sa.String(1000), nullable=True))
    op.add_column("supplier_parts", sa.Column("last_in_stock_at", sa.DateTime(), nullable=True))
    # Express shipping
    op.add_column("supplier_parts", sa.Column("express_available", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("supplier_parts", sa.Column("express_price_ils", sa.Numeric(10, 2), nullable=True,
                                               comment="Express surcharge incl. 18% VAT"))
    op.add_column("supplier_parts", sa.Column("express_delivery_days", sa.Integer(), nullable=True))
    op.add_column("supplier_parts", sa.Column("express_cutoff_time", sa.String(5), nullable=True,
                                               comment="e.g. '14:00' — order before this time for express today"))
    op.add_column("supplier_parts", sa.Column("express_last_checked", sa.DateTime(), nullable=True))

    # ── suppliers — new columns ───────────────────────────────────────────────
    op.add_column("suppliers", sa.Column("supports_express", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("suppliers", sa.Column("express_carrier", sa.String(100), nullable=True))
    op.add_column("suppliers", sa.Column("express_base_cost_usd", sa.Numeric(8, 2), nullable=True))
    op.add_column("suppliers", sa.Column("avg_delivery_days_actual", sa.Numeric(5, 1), nullable=True,
                                          comment="Calculated from real order history vs estimated"))

    # ── car_brands — new columns ──────────────────────────────────────────────
    op.add_column("car_brands", sa.Column("warranty_years", sa.Integer(), nullable=True))
    op.add_column("car_brands", sa.Column("warranty_km", sa.Integer(), nullable=True))
    op.add_column("car_brands", sa.Column("warranty_notes", sa.Text(), nullable=True))
    op.add_column("car_brands", sa.Column("il_importer", sa.String(200), nullable=True,
                                           comment="Official Israeli importer name"))
    op.add_column("car_brands", sa.Column("il_importer_website", sa.String(500), nullable=True))
    op.add_column("car_brands", sa.Column("parts_availability", sa.String(20), nullable=True,
                                           comment="Easy / Medium / Hard in Israel"))
    op.add_column("car_brands", sa.Column("avg_service_interval_km", sa.Integer(), nullable=True))
    op.add_column("car_brands", sa.Column("popular_models_il", JSONB, nullable=True,
                                           comment="Most sold models in Israel from transport ministry data"))

    # ── NEW TABLE: part_vehicle_fitment ───────────────────────────────────────
    op.create_table(
        "part_vehicle_fitment",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("part_id", sa.dialects.postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("parts_catalog.id", ondelete="CASCADE"), nullable=False),
        sa.Column("manufacturer", sa.String(100), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("year_from", sa.Integer(), nullable=False),
        sa.Column("year_to", sa.Integer(), nullable=True, comment="NULL = still in production"),
        sa.Column("engine_type", sa.String(50), nullable=True),
        sa.Column("transmission", sa.String(50), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_fitment_part_id", "part_vehicle_fitment", ["part_id"])
    op.create_index("idx_fitment_mfr_model", "part_vehicle_fitment", ["manufacturer", "model"])
    op.create_index("idx_fitment_years", "part_vehicle_fitment", ["year_from", "year_to"])

    # ── NEW TABLE: part_cross_reference ───────────────────────────────────────
    op.create_table(
        "part_cross_reference",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("part_id", sa.dialects.postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("parts_catalog.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ref_number", sa.String(100), nullable=False, index=True),
        sa.Column("manufacturer", sa.String(100), nullable=False),
        sa.Column("ref_type", sa.String(20), nullable=False,
                  comment="OEM_ORIGINAL / OEM_EQUIVALENT / AFTERMARKET"),
        sa.Column("is_superseded", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("superseded_by", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_crossref_part_id", "part_cross_reference", ["part_id"])
    op.create_index("idx_crossref_ref_number", "part_cross_reference", ["ref_number"])
    op.create_index("idx_crossref_number_mfr", "part_cross_reference", ["ref_number", "manufacturer"], unique=False)

    # ── NEW TABLE: part_aliases ───────────────────────────────────────────────
    op.create_table(
        "part_aliases",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("part_id", sa.dialects.postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("parts_catalog.id", ondelete="CASCADE"), nullable=False),
        sa.Column("alias", sa.String(255), nullable=False, index=True),
        sa.Column("language", sa.String(10), nullable=False, server_default="he"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_aliases_part_id", "part_aliases", ["part_id"])

    # ── NEW TABLE: price_history ──────────────────────────────────────────────
    op.create_table(
        "price_history",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("supplier_part_id", sa.dialects.postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("supplier_parts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("old_price_ils", sa.Numeric(10, 2), nullable=True),
        sa.Column("new_price_ils", sa.Numeric(10, 2), nullable=False),
        sa.Column("old_price_usd", sa.Numeric(10, 2), nullable=True),
        sa.Column("new_price_usd", sa.Numeric(10, 2), nullable=False),
        sa.Column("change_pct", sa.Numeric(7, 4), nullable=True, comment="(new-old)/old * 100"),
        sa.Column("source", sa.String(50), nullable=True, comment="scraper / manual / import"),
        sa.Column("ils_per_usd_rate", sa.Numeric(8, 4), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_price_history_sp_id", "price_history", ["supplier_part_id"])
    op.create_index("idx_price_history_created", "price_history", ["created_at"])

    # ── NEW TABLE: purchase_orders ────────────────────────────────────────────
    op.create_table(
        "purchase_orders",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("po_number", sa.String(30), unique=True, nullable=False),
        sa.Column("order_id", sa.dialects.postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("orders.id"), nullable=True),
        sa.Column("supplier_id", sa.dialects.postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("suppliers.id"), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="draft",
                  comment="draft / sent / confirmed / shipped / received / cancelled"),
        sa.Column("total_usd", sa.Numeric(10, 2), nullable=True),
        sa.Column("total_ils", sa.Numeric(10, 2), nullable=True),
        sa.Column("shipping_type", sa.String(20), nullable=False, server_default="standard"),
        sa.Column("tracking_number", sa.String(100), nullable=True),
        sa.Column("shipped_at", sa.DateTime(), nullable=True),
        sa.Column("received_at", sa.DateTime(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_po_supplier_id", "purchase_orders", ["supplier_id"])
    op.create_index("idx_po_order_id", "purchase_orders", ["order_id"])
    op.create_index("idx_po_status", "purchase_orders", ["status"])

    # ── NEW TABLE: scraper_api_calls ──────────────────────────────────────────
    op.create_table(
        "scraper_api_calls",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("source", sa.String(50), nullable=False,
                  comment="autodoc / ebay / aliexpress / rockauto / google_shopping / data_gov_il"),
        sa.Column("query", sa.String(200), nullable=True),
        sa.Column("part_number", sa.String(100), nullable=True),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("success", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("results_count", sa.Integer(), nullable=True),
        sa.Column("response_ms", sa.Integer(), nullable=True, comment="Response time in milliseconds"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_api_calls_source", "scraper_api_calls", ["source"])
    op.create_index("idx_api_calls_created", "scraper_api_calls", ["created_at"])
    op.create_index("idx_api_calls_success", "scraper_api_calls", ["success"])

    # ── orders: add shipping_type ─────────────────────────────────────────────
    op.add_column("orders", sa.Column("shipping_type", sa.String(20), nullable=False,
                                       server_default="standard",
                                       comment="standard / express"))


def downgrade() -> None:
    op.drop_column("orders", "shipping_type")
    op.drop_table("scraper_api_calls")
    op.drop_table("purchase_orders")
    op.drop_table("price_history")
    op.drop_table("part_aliases")
    op.drop_table("part_cross_reference")
    op.drop_table("part_vehicle_fitment")

    for col in ["warranty_years", "warranty_km", "warranty_notes", "il_importer",
                "il_importer_website", "parts_availability",
                "avg_service_interval_km", "popular_models_il"]:
        op.drop_column("car_brands", col)

    for col in ["supports_express", "express_carrier",
                "express_base_cost_usd", "avg_delivery_days_actual"]:
        op.drop_column("suppliers", col)

    for col in ["stock_quantity", "min_order_qty", "supplier_url", "last_in_stock_at",
                "express_available", "express_price_ils", "express_delivery_days",
                "express_cutoff_time", "express_last_checked"]:
        op.drop_column("supplier_parts", col)

    for col in ["name_he", "oem_number", "barcode", "weight_kg",
                "importer_price_ils", "online_price_ils", "min_price_ils", "max_price_ils",
                "part_condition", "superseded_by_sku", "customs_tariff_code",
                "is_safety_critical", "search_vector", "needs_oem_lookup"]:
        op.drop_column("parts_catalog", col)
