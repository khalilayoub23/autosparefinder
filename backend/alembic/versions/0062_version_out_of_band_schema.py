"""Version the catalog schema that exists ONLY in production (FIXES_TRACKER #88).

Read-only comparison on 2026-10-21 (production catalog vs a database built from the migrations — the sandbox):
production has objects that NO migration creates — added by hand or by one-off scripts:
  tables  api_keys (Partner API keys), gov_il_manufacturer_xref, harvest_queue (car-parts harvester queue),
          part_thumbnails (thumbnail pipeline), aliexpress_candidates (also self-created at runtime by
          services/aliexpress_price_sync.py, but no migration had it)
  columns supplier_parts.warranty_source varchar(24), parts_catalog.thumbnail_status text,
          parts_catalog.thumbnail_url text (+ index idx_parts_thumb_status)
So the production schema could not be rebuilt from the repository (disaster recovery, a fresh environment), and code
that uses these objects failed on such a database: in the sandbox, UndefinedColumnError (warranty_source) and
UndefinedTableError (part_thumbnails) broke 14+ tests.

Definitions are copied from production (`pg_dump -s`, read-only), with production's own constraint/index/sequence
names. Deliberately NOT versioned:
  * night_pipeline_runs — production-only BY DESIGN: the night pipeline runs only in production
    (night_pipeline.enabled(); tests/test_backup_isolation.py) and creates this table itself at runtime, and
    tests/isolated_db_guard.py uses its ABSENCE to tell a non-production database from production. Creating it in
    every migrated database would disarm that safety check (found in this change: the guard then refused the sandbox).
  * dedup_work_jaguar — a one-off dedup job's work table, not part of the application schema.

PRODUCTION SAFETY: every object is created only when it is missing (looked up in pg_catalog / information_schema
first). In production — where all of them exist — this migration executes NO DDL and takes NO lock. This matters:
`CREATE INDEX IF NOT EXISTS` / `ADD COLUMN IF NOT EXISTS` take their table lock BEFORE noticing the object exists
(CLAUDE.md), and parts_catalog / supplier_parts are hot multi-million-row tables. lock_timeout is bounded for the case
where something IS created.

Downgrade is a deliberate NO-OP: these objects hold production data and pre-date this migration.

Revision ID: 0062_version_oob_schema   (<= 32 chars)
Revises: 0061_noa_ops_audit
Create Date: 2026-10-21
"""

from alembic import op

revision = "0062_version_oob_schema"
down_revision = "0061_noa_ops_audit"
branch_labels = None
depends_on = None

_TABLES = {
    "aliexpress_candidates": """
        CREATE TABLE public.aliexpress_candidates (
            part_id uuid NOT NULL,
            oem_norm text NOT NULL,
            cited_item_id text,
            status text DEFAULT 'cited'::text NOT NULL,
            attempts integer DEFAULT 0 NOT NULL,
            discovered_at timestamp with time zone DEFAULT now() NOT NULL,
            last_checked_at timestamp with time zone,
            CONSTRAINT aliexpress_candidates_pkey PRIMARY KEY (part_id)
        );
        CREATE INDEX ix_aliexpress_candidates_status ON public.aliexpress_candidates USING btree (status, discovered_at);
    """,
    "api_keys": """
        CREATE TABLE public.api_keys (
            id uuid DEFAULT gen_random_uuid() NOT NULL,
            key_hash text NOT NULL,
            key_prefix text NOT NULL,
            partner_name text NOT NULL,
            scopes text[] DEFAULT ARRAY['read'::text] NOT NULL,
            rate_limit_per_min integer DEFAULT 60 NOT NULL,
            is_active boolean DEFAULT true NOT NULL,
            request_count bigint DEFAULT 0 NOT NULL,
            created_at timestamp without time zone DEFAULT now(),
            last_used_at timestamp without time zone,
            CONSTRAINT api_keys_pkey PRIMARY KEY (id),
            CONSTRAINT api_keys_key_hash_key UNIQUE (key_hash)
        );
        CREATE INDEX idx_api_keys_hash ON public.api_keys USING btree (key_hash);
    """,
    "gov_il_manufacturer_xref": """
        CREATE SEQUENCE public.gov_il_manufacturer_xref_id_seq AS integer START WITH 1 INCREMENT BY 1
            NO MINVALUE NO MAXVALUE CACHE 1;
        CREATE TABLE public.gov_il_manufacturer_xref (
            id integer NOT NULL DEFAULT nextval('public.gov_il_manufacturer_xref_id_seq'::regclass),
            tozar_primary character varying(100) NOT NULL,
            tozar_variants text[],
            db_brand character varying(100),
            note text,
            active_vehicles integer DEFAULT 0,
            inactive_vehicles integer DEFAULT 0,
            total_vehicles integer GENERATED ALWAYS AS ((active_vehicles + inactive_vehicles)) STORED,
            created_at timestamp with time zone DEFAULT now(),
            parts_count integer DEFAULT 0 NOT NULL,
            priority_gap character varying(10),
            CONSTRAINT gov_il_manufacturer_xref_pkey PRIMARY KEY (id)
        );
        ALTER SEQUENCE public.gov_il_manufacturer_xref_id_seq OWNED BY public.gov_il_manufacturer_xref.id;
        COMMENT ON TABLE public.gov_il_manufacturer_xref IS
            'Cross-reference: Israeli MoT gov.il tozar (manufacturer) → car_brands. Fetched 2026-06-07.';
        COMMENT ON COLUMN public.gov_il_manufacturer_xref.priority_gap IS
            'HIGH/MEDIUM/LOW — brands with active IL vehicles but 0 parts in catalog';
    """,
    "harvest_queue": """
        CREATE TABLE public.harvest_queue (
            id uuid DEFAULT gen_random_uuid() NOT NULL,
            brand_en character varying(120) NOT NULL,
            model_name character varying(200) NOT NULL,
            model_slug character varying(240),
            il_vehicle_count integer DEFAULT 0 NOT NULL,
            priority_rank integer,
            source character varying(40) DEFAULT 'il_market'::character varying NOT NULL,
            status character varying(24) DEFAULT 'pending'::character varying NOT NULL,
            parts_found integer DEFAULT 0 NOT NULL,
            attempts integer DEFAULT 0 NOT NULL,
            last_harvested_at timestamp with time zone,
            created_at timestamp with time zone DEFAULT now() NOT NULL,
            updated_at timestamp with time zone DEFAULT now() NOT NULL,
            CONSTRAINT harvest_queue_pkey PRIMARY KEY (id),
            CONSTRAINT harvest_queue_brand_en_model_slug_key UNIQUE (brand_en, model_slug)
        );
        CREATE INDEX idx_harvest_queue_dispatch ON public.harvest_queue USING btree (status, priority_rank)
            WHERE ((status)::text = 'pending'::text);
    """,
    "part_thumbnails": """
        CREATE TABLE public.part_thumbnails (
            part_id uuid NOT NULL,
            url text,
            status text,
            updated_at timestamp without time zone DEFAULT now(),
            CONSTRAINT part_thumbnails_pkey PRIMARY KEY (part_id)
        );
    """,
}

_COLUMNS = (   # (table, column, type)
    ("supplier_parts", "warranty_source", "character varying(24)"),
    ("parts_catalog", "thumbnail_status", "text"),
    ("parts_catalog", "thumbnail_url", "text"),
)

_INDEXES = (   # (name, DDL) on EXISTING tables — created only when the name is absent
    ("idx_parts_thumb_status",
     "CREATE INDEX idx_parts_thumb_status ON public.parts_catalog USING btree (thumbnail_status) "
     "WHERE (thumbnail_status IS NULL)"),
)


def _dollar(sql: str) -> str:
    assert "$ddl$" not in sql
    return "$ddl$" + sql + "$ddl$"


def upgrade() -> None:
    for name, ddl in _TABLES.items():
        op.execute(f"""
            DO $$
            BEGIN
                IF to_regclass('public.{name}') IS NULL THEN
                    PERFORM set_config('lock_timeout', '15s', true);
                    EXECUTE {_dollar(ddl)};
                END IF;
            END $$;
        """)
    for table, column, typ in _COLUMNS:
        op.execute(f"""
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                               WHERE table_schema = 'public' AND table_name = '{table}' AND column_name = '{column}') THEN
                    PERFORM set_config('lock_timeout', '15s', true);
                    ALTER TABLE public.{table} ADD COLUMN {column} {typ};
                END IF;
            END $$;
        """)
    for name, ddl in _INDEXES:
        op.execute(f"""
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE schemaname = 'public' AND indexname = '{name}') THEN
                    PERFORM set_config('lock_timeout', '15s', true);
                    EXECUTE {_dollar(ddl)};
                END IF;
            END $$;
        """)


def downgrade() -> None:
    # Intentionally nothing: these objects hold production data and pre-date this migration (see docstring).
    pass
