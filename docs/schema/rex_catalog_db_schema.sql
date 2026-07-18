--
-- PostgreSQL database dump
--

\restrict 8f5pXrgMAtFBgQffZyMoXOSZsjFkslkhd8lnHfkK1mMONYr0WfO1gWVZqvurMmS

-- Dumped from database version 16.13 (Debian 16.13-1.pgdg12+1)
-- Dumped by pg_dump version 16.13 (Debian 16.13-1.pgdg12+1)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: pg_trgm; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public;


--
-- Name: EXTENSION pg_trgm; Type: COMMENT; Schema: -; Owner: 
--

COMMENT ON EXTENSION pg_trgm IS 'text similarity measurement and index searching based on trigrams';


--
-- Name: pgcrypto; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public;


--
-- Name: EXTENSION pgcrypto; Type: COMMENT; Schema: -; Owner: 
--

COMMENT ON EXTENSION pgcrypto IS 'cryptographic functions';


--
-- Name: uuid-ossp; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS "uuid-ossp" WITH SCHEMA public;


--
-- Name: EXTENSION "uuid-ossp"; Type: COMMENT; Schema: -; Owner: 
--

COMMENT ON EXTENSION "uuid-ossp" IS 'generate universally unique identifiers (UUIDs)';


--
-- Name: vector; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;


--
-- Name: EXTENSION vector; Type: COMMENT; Schema: -; Owner: 
--

COMMENT ON EXTENSION vector IS 'vector data type and ivfflat and hnsw access methods';


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: alembic_version; Type: TABLE; Schema: public; Owner: autospare
--

CREATE TABLE public.alembic_version (
    version_num character varying(32) NOT NULL
);


ALTER TABLE public.alembic_version OWNER TO autospare;

--
-- Name: audit_logs; Type: TABLE; Schema: public; Owner: autospare
--

CREATE TABLE public.audit_logs (
    id uuid NOT NULL,
    user_id uuid,
    action character varying(100) NOT NULL,
    entity_type character varying(50),
    entity_id uuid,
    old_value jsonb,
    new_value jsonb,
    ip_address character varying(45),
    user_agent text,
    created_at timestamp without time zone
);


ALTER TABLE public.audit_logs OWNER TO autospare;

--
-- Name: brand_aliases; Type: TABLE; Schema: public; Owner: autospare
--

CREATE TABLE public.brand_aliases (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    brand_id uuid NOT NULL,
    alias character varying(200) NOT NULL,
    normalized character varying(200) NOT NULL,
    source character varying(50),
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.brand_aliases OWNER TO autospare;

--
-- Name: bug_reports; Type: TABLE; Schema: public; Owner: autospare
--

CREATE TABLE public.bug_reports (
    id uuid NOT NULL,
    user_id uuid,
    user_role character varying(20),
    title character varying(255) NOT NULL,
    description text NOT NULL,
    severity character varying(20) DEFAULT 'medium'::character varying,
    platform character varying(20),
    app_version character varying(20),
    screen_name character varying(100),
    endpoint_url character varying(500),
    http_method character varying(10),
    http_status_code integer,
    error_trace text,
    last_api_calls jsonb,
    device_info jsonb,
    tech_analysis jsonb,
    status character varying(20) DEFAULT 'open'::character varying,
    admin_notes text,
    resolved_at timestamp without time zone,
    created_at timestamp without time zone DEFAULT now(),
    updated_at timestamp without time zone DEFAULT now()
);


ALTER TABLE public.bug_reports OWNER TO autospare;

--
-- Name: cache_entries; Type: TABLE; Schema: public; Owner: autospare
--

CREATE TABLE public.cache_entries (
    id uuid NOT NULL,
    cache_key character varying(255) NOT NULL,
    cache_value jsonb,
    expires_at timestamp without time zone,
    created_at timestamp without time zone
);


ALTER TABLE public.cache_entries OWNER TO autospare;

--
-- Name: car_brands; Type: TABLE; Schema: public; Owner: autospare
--

CREATE TABLE public.car_brands (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name character varying(100) NOT NULL,
    name_he character varying(100),
    group_name character varying(100),
    country character varying(100),
    region character varying(50),
    is_luxury boolean DEFAULT false NOT NULL,
    is_electric_focused boolean DEFAULT false NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    logo_url character varying(500),
    website character varying(500),
    notes text,
    aliases text[] DEFAULT '{}'::text[],
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now(),
    warranty_years integer,
    warranty_km integer,
    warranty_notes text,
    il_importer character varying(200),
    il_importer_website character varying(500),
    parts_availability character varying(20),
    avg_service_interval_km integer,
    popular_models_il jsonb
);


ALTER TABLE public.car_brands OWNER TO autospare;

--
-- Name: COLUMN car_brands.il_importer; Type: COMMENT; Schema: public; Owner: autospare
--

COMMENT ON COLUMN public.car_brands.il_importer IS 'Official Israeli importer name';


--
-- Name: COLUMN car_brands.parts_availability; Type: COMMENT; Schema: public; Owner: autospare
--

COMMENT ON COLUMN public.car_brands.parts_availability IS 'Easy / Medium / Hard in Israel';


--
-- Name: COLUMN car_brands.popular_models_il; Type: COMMENT; Schema: public; Owner: autospare
--

COMMENT ON COLUMN public.car_brands.popular_models_il IS 'Most sold models in Israel from transport ministry data';


--
-- Name: catalog_versions; Type: TABLE; Schema: public; Owner: autospare
--

CREATE TABLE public.catalog_versions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    version_tag character varying(50) NOT NULL,
    description text,
    parts_added integer DEFAULT 0 NOT NULL,
    parts_updated integer DEFAULT 0 NOT NULL,
    parts_total integer DEFAULT 0 NOT NULL,
    source character varying(100),
    triggered_by uuid,
    started_at timestamp without time zone DEFAULT now() NOT NULL,
    completed_at timestamp without time zone,
    status character varying(20) DEFAULT 'pending'::character varying NOT NULL,
    error_log text,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_catalog_versions_status CHECK (((status)::text = ANY ((ARRAY['pending'::character varying, 'running'::character varying, 'completed'::character varying, 'failed'::character varying])::text[])))
);


ALTER TABLE public.catalog_versions OWNER TO autospare;

--
-- Name: job_registry; Type: TABLE; Schema: public; Owner: autospare
--

CREATE TABLE public.job_registry (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    job_id character varying(255) NOT NULL,
    job_name character varying(255) NOT NULL,
    worker_host character varying(255),
    status character varying(50) DEFAULT 'running'::character varying NOT NULL,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    completed_at timestamp with time zone,
    ttl_seconds integer,
    error_message text,
    last_heartbeat_at timestamp with time zone DEFAULT now() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.job_registry OWNER TO autospare;

--
-- Name: part_aliases; Type: TABLE; Schema: public; Owner: autospare
--

CREATE TABLE public.part_aliases (
    id uuid NOT NULL,
    part_id uuid NOT NULL,
    alias character varying(255) NOT NULL,
    language character varying(10) DEFAULT 'he'::character varying NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.part_aliases OWNER TO autospare;

--
-- Name: part_cross_reference; Type: TABLE; Schema: public; Owner: autospare
--

CREATE TABLE public.part_cross_reference (
    id uuid NOT NULL,
    part_id uuid NOT NULL,
    ref_number character varying(100) NOT NULL,
    manufacturer character varying(100) NOT NULL,
    ref_type character varying(20) NOT NULL,
    is_superseded boolean DEFAULT false NOT NULL,
    superseded_by character varying(100),
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.part_cross_reference OWNER TO autospare;

--
-- Name: COLUMN part_cross_reference.ref_type; Type: COMMENT; Schema: public; Owner: autospare
--

COMMENT ON COLUMN public.part_cross_reference.ref_type IS 'OEM_ORIGINAL / OEM_EQUIVALENT / AFTERMARKET';


--
-- Name: part_variants; Type: TABLE; Schema: public; Owner: autospare
--

CREATE TABLE public.part_variants (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    master_part_id uuid NOT NULL,
    catalog_part_id uuid NOT NULL,
    quality_level character varying(20) NOT NULL,
    manufacturer character varying(100),
    sku character varying(100),
    created_at timestamp without time zone DEFAULT now(),
    CONSTRAINT ck_part_variants_quality_level CHECK (((quality_level)::text = ANY ((ARRAY['OEM'::character varying, 'OEM_Equivalent'::character varying, 'Aftermarket_Premium'::character varying, 'Aftermarket_Standard'::character varying, 'Economy'::character varying])::text[])))
);


ALTER TABLE public.part_variants OWNER TO autospare;

--
-- Name: part_vehicle_fitment; Type: TABLE; Schema: public; Owner: autospare
--

CREATE TABLE public.part_vehicle_fitment (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    part_id uuid NOT NULL,
    manufacturer character varying(100) NOT NULL,
    model character varying(100) NOT NULL,
    year_from integer NOT NULL,
    year_to integer,
    engine_type character varying(50),
    transmission character varying(50),
    notes text,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.part_vehicle_fitment OWNER TO autospare;

--
-- Name: parts_catalog; Type: TABLE; Schema: public; Owner: autospare
--

CREATE TABLE public.parts_catalog (
    id uuid NOT NULL,
    sku character varying(100) NOT NULL,
    name character varying(255) NOT NULL,
    category character varying(100),
    manufacturer character varying(100),
    part_type character varying(50),
    description text,
    specifications jsonb,
    compatible_vehicles jsonb,
    base_price numeric(10,2),
    is_active boolean,
    created_at timestamp without time zone,
    updated_at timestamp without time zone,
    name_he character varying(255),
    oem_number character varying(100),
    barcode character varying(50),
    weight_kg numeric(6,3),
    importer_price_ils numeric(10,2),
    online_price_ils numeric(10,2),
    min_price_ils numeric(10,2),
    max_price_ils numeric(10,2),
    part_condition character varying(20) DEFAULT 'New'::character varying NOT NULL,
    superseded_by_sku character varying(100),
    customs_tariff_code character varying(20),
    is_safety_critical boolean DEFAULT false NOT NULL,
    search_vector tsvector,
    needs_oem_lookup boolean DEFAULT false NOT NULL,
    master_enriched boolean DEFAULT false NOT NULL,
    image_embedding public.vector(512),
    embedding public.vector(384)
);


ALTER TABLE public.parts_catalog OWNER TO autospare;

--
-- Name: COLUMN parts_catalog.importer_price_ils; Type: COMMENT; Schema: public; Owner: autospare
--

COMMENT ON COLUMN public.parts_catalog.importer_price_ils IS 'Israeli importer price incl. 18% VAT';


--
-- Name: COLUMN parts_catalog.online_price_ils; Type: COMMENT; Schema: public; Owner: autospare
--

COMMENT ON COLUMN public.parts_catalog.online_price_ils IS 'Competitor online reference price incl. 18% VAT';


--
-- Name: COLUMN parts_catalog.min_price_ils; Type: COMMENT; Schema: public; Owner: autospare
--

COMMENT ON COLUMN public.parts_catalog.min_price_ils IS 'Cheapest supplier price incl. 18% VAT — auto-updated by scraper';


--
-- Name: COLUMN parts_catalog.max_price_ils; Type: COMMENT; Schema: public; Owner: autospare
--

COMMENT ON COLUMN public.parts_catalog.max_price_ils IS 'Most expensive supplier price incl. 18% VAT';


--
-- Name: COLUMN parts_catalog.part_condition; Type: COMMENT; Schema: public; Owner: autospare
--

COMMENT ON COLUMN public.parts_catalog.part_condition IS 'New / Used / Remanufactured';


--
-- Name: COLUMN parts_catalog.superseded_by_sku; Type: COMMENT; Schema: public; Owner: autospare
--

COMMENT ON COLUMN public.parts_catalog.superseded_by_sku IS 'SKU of replacement part when this one is discontinued';


--
-- Name: COLUMN parts_catalog.is_safety_critical; Type: COMMENT; Schema: public; Owner: autospare
--

COMMENT ON COLUMN public.parts_catalog.is_safety_critical IS 'True for brakes, steering, airbags — affects warranty law';


--
-- Name: COLUMN parts_catalog.search_vector; Type: COMMENT; Schema: public; Owner: autospare
--

COMMENT ON COLUMN public.parts_catalog.search_vector IS 'PostgreSQL full-text search vector (Hebrew + English)';


--
-- Name: COLUMN parts_catalog.needs_oem_lookup; Type: COMMENT; Schema: public; Owner: autospare
--

COMMENT ON COLUMN public.parts_catalog.needs_oem_lookup IS 'True for fake/seeded SKUs awaiting real OEM number';


--
-- Name: parts_images; Type: TABLE; Schema: public; Owner: autospare
--

CREATE TABLE public.parts_images (
    id uuid NOT NULL,
    part_id uuid NOT NULL,
    file_id uuid,
    url character varying(500),
    is_primary boolean,
    sort_order integer,
    created_at timestamp without time zone,
    embedding_generated boolean DEFAULT false NOT NULL
);


ALTER TABLE public.parts_images OWNER TO autospare;

--
-- Name: parts_master; Type: TABLE; Schema: public; Owner: autospare
--

CREATE TABLE public.parts_master (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    canonical_name character varying(255) NOT NULL,
    canonical_name_he character varying(255),
    category character varying(100) NOT NULL,
    part_type character varying(50),
    is_safety_critical boolean DEFAULT false NOT NULL,
    created_at timestamp without time zone DEFAULT now(),
    updated_at timestamp without time zone DEFAULT now()
);


ALTER TABLE public.parts_master OWNER TO autospare;

--
-- Name: price_history; Type: TABLE; Schema: public; Owner: autospare
--

CREATE TABLE public.price_history (
    id uuid NOT NULL,
    supplier_part_id uuid NOT NULL,
    old_price_ils numeric(10,2),
    new_price_ils numeric(10,2) NOT NULL,
    old_price_usd numeric(10,2),
    new_price_usd numeric(10,2) NOT NULL,
    change_pct numeric(7,4),
    source character varying(50),
    ils_per_usd_rate numeric(8,4),
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.price_history OWNER TO autospare;

--
-- Name: COLUMN price_history.change_pct; Type: COMMENT; Schema: public; Owner: autospare
--

COMMENT ON COLUMN public.price_history.change_pct IS '(new-old)/old * 100';


--
-- Name: COLUMN price_history.source; Type: COMMENT; Schema: public; Owner: autospare
--

COMMENT ON COLUMN public.price_history.source IS 'scraper / manual / import';


--
-- Name: purchase_orders; Type: TABLE; Schema: public; Owner: autospare
--

CREATE TABLE public.purchase_orders (
    id uuid NOT NULL,
    po_number character varying(30) NOT NULL,
    order_id uuid,
    supplier_id uuid NOT NULL,
    status character varying(30) DEFAULT 'draft'::character varying NOT NULL,
    total_usd numeric(10,2),
    total_ils numeric(10,2),
    shipping_type character varying(20) DEFAULT 'standard'::character varying NOT NULL,
    tracking_number character varying(100),
    shipped_at timestamp without time zone,
    received_at timestamp without time zone,
    notes text,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.purchase_orders OWNER TO autospare;

--
-- Name: COLUMN purchase_orders.status; Type: COMMENT; Schema: public; Owner: autospare
--

COMMENT ON COLUMN public.purchase_orders.status IS 'draft / sent / confirmed / shipped / received / cancelled';


--
-- Name: scraper_api_calls; Type: TABLE; Schema: public; Owner: autospare
--

CREATE TABLE public.scraper_api_calls (
    id uuid NOT NULL,
    source character varying(50) NOT NULL,
    query character varying(200),
    part_number character varying(100),
    http_status integer,
    success boolean DEFAULT true NOT NULL,
    results_count integer,
    response_ms integer,
    error_message text,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.scraper_api_calls OWNER TO autospare;

--
-- Name: COLUMN scraper_api_calls.source; Type: COMMENT; Schema: public; Owner: autospare
--

COMMENT ON COLUMN public.scraper_api_calls.source IS 'autodoc / ebay / aliexpress / rockauto / google_shopping / data_gov_il';


--
-- Name: COLUMN scraper_api_calls.response_ms; Type: COMMENT; Schema: public; Owner: autospare
--

COMMENT ON COLUMN public.scraper_api_calls.response_ms IS 'Response time in milliseconds';


--
-- Name: search_misses; Type: TABLE; Schema: public; Owner: autospare
--

CREATE TABLE public.search_misses (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    query text NOT NULL,
    normalized_query text NOT NULL,
    category character varying(100),
    vehicle_manufacturer character varying(100),
    miss_count integer DEFAULT 1 NOT NULL,
    last_seen_at timestamp without time zone DEFAULT now() NOT NULL,
    first_seen_at timestamp without time zone DEFAULT now() NOT NULL,
    triggered_scrape boolean DEFAULT false NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    user_id uuid,
    notified boolean DEFAULT false NOT NULL
);


ALTER TABLE public.search_misses OWNER TO autospare;

--
-- Name: social_posts; Type: TABLE; Schema: public; Owner: autospare
--

CREATE TABLE public.social_posts (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    content text NOT NULL,
    platforms text[] NOT NULL,
    status character varying(20) DEFAULT 'draft'::character varying NOT NULL,
    scheduled_at timestamp without time zone,
    published_at timestamp without time zone,
    external_post_ids jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_by uuid NOT NULL,
    approved_by uuid,
    rejection_reason text,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_social_posts_status CHECK (((status)::text = ANY ((ARRAY['draft'::character varying, 'pending_approval'::character varying, 'approved'::character varying, 'published'::character varying, 'rejected'::character varying])::text[])))
);


ALTER TABLE public.social_posts OWNER TO autospare;

--
-- Name: supplier_parts; Type: TABLE; Schema: public; Owner: autospare
--

CREATE TABLE public.supplier_parts (
    id uuid NOT NULL,
    supplier_id uuid NOT NULL,
    part_id uuid NOT NULL,
    supplier_sku character varying(100),
    price_usd numeric(10,2) NOT NULL,
    price_ils numeric(10,2),
    shipping_cost_usd numeric(10,2),
    shipping_cost_ils numeric(10,2),
    availability character varying(50),
    warranty_months integer,
    estimated_delivery_days integer,
    last_checked_at timestamp without time zone,
    is_available boolean,
    created_at timestamp without time zone,
    stock_quantity integer,
    min_order_qty integer DEFAULT 1 NOT NULL,
    supplier_url character varying(1000),
    last_in_stock_at timestamp without time zone,
    express_available boolean DEFAULT false NOT NULL,
    express_price_ils numeric(10,2),
    express_delivery_days integer,
    express_cutoff_time character varying(5),
    express_last_checked timestamp without time zone,
    updated_at timestamp without time zone DEFAULT now(),
    part_type character varying(50)
);


ALTER TABLE public.supplier_parts OWNER TO autospare;

--
-- Name: COLUMN supplier_parts.express_price_ils; Type: COMMENT; Schema: public; Owner: autospare
--

COMMENT ON COLUMN public.supplier_parts.express_price_ils IS 'Express surcharge incl. 18% VAT';


--
-- Name: COLUMN supplier_parts.express_cutoff_time; Type: COMMENT; Schema: public; Owner: autospare
--

COMMENT ON COLUMN public.supplier_parts.express_cutoff_time IS 'e.g. ''14:00'' — order before this time for express today';


--
-- Name: suppliers; Type: TABLE; Schema: public; Owner: autospare
--

CREATE TABLE public.suppliers (
    id uuid NOT NULL,
    name character varying(255) NOT NULL,
    country character varying(100),
    website character varying(500),
    api_endpoint character varying(500),
    api_key text,
    credentials jsonb,
    shipping_info jsonb,
    return_policy jsonb,
    reliability_score numeric(3,2) DEFAULT 0.50 NOT NULL,
    is_active boolean,
    priority integer,
    created_at timestamp without time zone,
    updated_at timestamp without time zone,
    supports_express boolean DEFAULT false NOT NULL,
    express_carrier character varying(100),
    express_base_cost_usd numeric(8,2),
    avg_delivery_days_actual numeric(5,1),
    rate_limit_per_minute integer DEFAULT 30 NOT NULL,
    is_manufacturer boolean DEFAULT false NOT NULL,
    manufacturer_name character varying(255),
    CONSTRAINT ck_suppliers_reliability_score_range CHECK (((reliability_score >= 0.00) AND (reliability_score <= 1.00)))
);


ALTER TABLE public.suppliers OWNER TO autospare;

--
-- Name: COLUMN suppliers.avg_delivery_days_actual; Type: COMMENT; Schema: public; Owner: autospare
--

COMMENT ON COLUMN public.suppliers.avg_delivery_days_actual IS 'Calculated from real order history vs estimated';


--
-- Name: COLUMN suppliers.rate_limit_per_minute; Type: COMMENT; Schema: public; Owner: autospare
--

COMMENT ON COLUMN public.suppliers.rate_limit_per_minute IS 'Per-supplier request rate limit (requests per minute)';


--
-- Name: system_logs; Type: TABLE; Schema: public; Owner: autospare
--

CREATE TABLE public.system_logs (
    id uuid NOT NULL,
    level character varying(20) NOT NULL,
    logger_name character varying(100),
    message text NOT NULL,
    user_id uuid,
    ip_address character varying(45),
    endpoint character varying(255),
    method character varying(10),
    status_code integer,
    request_data jsonb,
    response_data jsonb,
    exception text,
    stack_trace text,
    created_at timestamp without time zone
);


ALTER TABLE public.system_logs OWNER TO autospare;

--
-- Name: system_settings; Type: TABLE; Schema: public; Owner: autospare
--

CREATE TABLE public.system_settings (
    id uuid NOT NULL,
    key character varying(100) NOT NULL,
    value text,
    value_type character varying(20),
    description text,
    is_public boolean,
    updated_by uuid,
    updated_at timestamp without time zone
);


ALTER TABLE public.system_settings OWNER TO autospare;

--
-- Name: truck_brand_aliases; Type: TABLE; Schema: public; Owner: autospare
--

CREATE TABLE public.truck_brand_aliases (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    brand_id uuid NOT NULL,
    alias character varying(200) NOT NULL,
    normalized character varying(200) NOT NULL,
    source character varying(50),
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.truck_brand_aliases OWNER TO autospare;

--
-- Name: truck_brands; Type: TABLE; Schema: public; Owner: autospare
--

CREATE TABLE public.truck_brands (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name character varying(100) NOT NULL,
    name_he character varying(100),
    group_name character varying(100),
    country character varying(100),
    region character varying(50),
    is_active boolean DEFAULT true NOT NULL,
    logo_url character varying(500),
    website character varying(500),
    notes text,
    aliases character varying[] DEFAULT '{}'::character varying[],
    il_importer character varying(200),
    il_importer_website character varying(500),
    parts_availability character varying(20),
    avg_service_interval_km integer,
    popular_models_il json,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.truck_brands OWNER TO autospare;

--
-- Name: vehicle_hierarchy_xls; Type: TABLE; Schema: public; Owner: autospare
--

CREATE TABLE public.vehicle_hierarchy_xls (
    id bigint NOT NULL,
    manufacturer text NOT NULL,
    model text NOT NULL,
    sub_model text DEFAULT ''::text NOT NULL,
    year_from integer DEFAULT 0 NOT NULL,
    year_to integer DEFAULT 0 NOT NULL,
    year_hint integer DEFAULT 0 NOT NULL,
    source_sheet text,
    source_tag text DEFAULT 'parts_database.xlsx'::text NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.vehicle_hierarchy_xls OWNER TO autospare;

--
-- Name: vehicle_hierarchy_xls_id_seq; Type: SEQUENCE; Schema: public; Owner: autospare
--

CREATE SEQUENCE public.vehicle_hierarchy_xls_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.vehicle_hierarchy_xls_id_seq OWNER TO autospare;

--
-- Name: vehicle_hierarchy_xls_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: autospare
--

ALTER SEQUENCE public.vehicle_hierarchy_xls_id_seq OWNED BY public.vehicle_hierarchy_xls.id;


--
-- Name: vehicles; Type: TABLE; Schema: public; Owner: autospare
--

CREATE TABLE public.vehicles (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    license_plate character varying(20),
    manufacturer character varying(100) NOT NULL,
    model character varying(100) NOT NULL,
    year integer NOT NULL,
    vin character varying(17),
    engine_type character varying(50),
    transmission character varying(50),
    fuel_type character varying(50),
    gov_api_data jsonb,
    cached_at timestamp without time zone,
    created_at timestamp without time zone DEFAULT now()
);


ALTER TABLE public.vehicles OWNER TO autospare;

--
-- Name: vehicle_hierarchy_xls id; Type: DEFAULT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.vehicle_hierarchy_xls ALTER COLUMN id SET DEFAULT nextval('public.vehicle_hierarchy_xls_id_seq'::regclass);


--
-- Name: alembic_version alembic_version_pkc; Type: CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.alembic_version
    ADD CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num);


--
-- Name: audit_logs audit_logs_pkey; Type: CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.audit_logs
    ADD CONSTRAINT audit_logs_pkey PRIMARY KEY (id);


--
-- Name: brand_aliases brand_aliases_pkey; Type: CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.brand_aliases
    ADD CONSTRAINT brand_aliases_pkey PRIMARY KEY (id);


--
-- Name: bug_reports bug_reports_pkey; Type: CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.bug_reports
    ADD CONSTRAINT bug_reports_pkey PRIMARY KEY (id);


--
-- Name: cache_entries cache_entries_cache_key_key; Type: CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.cache_entries
    ADD CONSTRAINT cache_entries_cache_key_key UNIQUE (cache_key);


--
-- Name: cache_entries cache_entries_pkey; Type: CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.cache_entries
    ADD CONSTRAINT cache_entries_pkey PRIMARY KEY (id);


--
-- Name: car_brands car_brands_name_key; Type: CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.car_brands
    ADD CONSTRAINT car_brands_name_key UNIQUE (name);


--
-- Name: car_brands car_brands_pkey; Type: CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.car_brands
    ADD CONSTRAINT car_brands_pkey PRIMARY KEY (id);


--
-- Name: catalog_versions catalog_versions_pkey; Type: CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.catalog_versions
    ADD CONSTRAINT catalog_versions_pkey PRIMARY KEY (id);


--
-- Name: catalog_versions catalog_versions_version_tag_key; Type: CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.catalog_versions
    ADD CONSTRAINT catalog_versions_version_tag_key UNIQUE (version_tag);


--
-- Name: job_registry job_registry_job_id_key; Type: CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.job_registry
    ADD CONSTRAINT job_registry_job_id_key UNIQUE (job_id);


--
-- Name: job_registry job_registry_pkey; Type: CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.job_registry
    ADD CONSTRAINT job_registry_pkey PRIMARY KEY (id);


--
-- Name: part_aliases part_aliases_pkey; Type: CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.part_aliases
    ADD CONSTRAINT part_aliases_pkey PRIMARY KEY (id);


--
-- Name: part_cross_reference part_cross_reference_pkey; Type: CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.part_cross_reference
    ADD CONSTRAINT part_cross_reference_pkey PRIMARY KEY (id);


--
-- Name: part_variants part_variants_pkey; Type: CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.part_variants
    ADD CONSTRAINT part_variants_pkey PRIMARY KEY (id);


--
-- Name: part_vehicle_fitment part_vehicle_fitment_pkey; Type: CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.part_vehicle_fitment
    ADD CONSTRAINT part_vehicle_fitment_pkey PRIMARY KEY (id);


--
-- Name: parts_catalog parts_catalog_pkey; Type: CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.parts_catalog
    ADD CONSTRAINT parts_catalog_pkey PRIMARY KEY (id);


--
-- Name: parts_images parts_images_pkey; Type: CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.parts_images
    ADD CONSTRAINT parts_images_pkey PRIMARY KEY (id);


--
-- Name: parts_master parts_master_pkey; Type: CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.parts_master
    ADD CONSTRAINT parts_master_pkey PRIMARY KEY (id);


--
-- Name: price_history price_history_pkey; Type: CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.price_history
    ADD CONSTRAINT price_history_pkey PRIMARY KEY (id);


--
-- Name: purchase_orders purchase_orders_pkey; Type: CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.purchase_orders
    ADD CONSTRAINT purchase_orders_pkey PRIMARY KEY (id);


--
-- Name: purchase_orders purchase_orders_po_number_key; Type: CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.purchase_orders
    ADD CONSTRAINT purchase_orders_po_number_key UNIQUE (po_number);


--
-- Name: scraper_api_calls scraper_api_calls_pkey; Type: CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.scraper_api_calls
    ADD CONSTRAINT scraper_api_calls_pkey PRIMARY KEY (id);


--
-- Name: search_misses search_misses_pkey; Type: CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.search_misses
    ADD CONSTRAINT search_misses_pkey PRIMARY KEY (id);


--
-- Name: social_posts social_posts_pkey; Type: CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.social_posts
    ADD CONSTRAINT social_posts_pkey PRIMARY KEY (id);


--
-- Name: supplier_parts supplier_parts_pkey; Type: CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.supplier_parts
    ADD CONSTRAINT supplier_parts_pkey PRIMARY KEY (id);


--
-- Name: supplier_parts supplier_parts_supplier_id_supplier_sku_key; Type: CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.supplier_parts
    ADD CONSTRAINT supplier_parts_supplier_id_supplier_sku_key UNIQUE (supplier_id, supplier_sku);


--
-- Name: suppliers suppliers_name_key; Type: CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.suppliers
    ADD CONSTRAINT suppliers_name_key UNIQUE (name);


--
-- Name: suppliers suppliers_pkey; Type: CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.suppliers
    ADD CONSTRAINT suppliers_pkey PRIMARY KEY (id);


--
-- Name: system_logs system_logs_pkey; Type: CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.system_logs
    ADD CONSTRAINT system_logs_pkey PRIMARY KEY (id);


--
-- Name: system_settings system_settings_key_key; Type: CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.system_settings
    ADD CONSTRAINT system_settings_key_key UNIQUE (key);


--
-- Name: system_settings system_settings_pkey; Type: CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.system_settings
    ADD CONSTRAINT system_settings_pkey PRIMARY KEY (id);


--
-- Name: truck_brand_aliases truck_brand_aliases_pkey; Type: CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.truck_brand_aliases
    ADD CONSTRAINT truck_brand_aliases_pkey PRIMARY KEY (id);


--
-- Name: truck_brands truck_brands_name_key; Type: CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.truck_brands
    ADD CONSTRAINT truck_brands_name_key UNIQUE (name);


--
-- Name: truck_brands truck_brands_pkey; Type: CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.truck_brands
    ADD CONSTRAINT truck_brands_pkey PRIMARY KEY (id);


--
-- Name: brand_aliases uq_brand_aliases_brand_alias; Type: CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.brand_aliases
    ADD CONSTRAINT uq_brand_aliases_brand_alias UNIQUE (brand_id, alias);


--
-- Name: part_variants uq_part_variants_master_catalog; Type: CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.part_variants
    ADD CONSTRAINT uq_part_variants_master_catalog UNIQUE (master_part_id, catalog_part_id);


--
-- Name: parts_master uq_parts_master_name_category; Type: CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.parts_master
    ADD CONSTRAINT uq_parts_master_name_category UNIQUE (canonical_name, category);


--
-- Name: search_misses uq_search_misses_normalized_query; Type: CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.search_misses
    ADD CONSTRAINT uq_search_misses_normalized_query UNIQUE (normalized_query);


--
-- Name: vehicle_hierarchy_xls vehicle_hierarchy_xls_pkey; Type: CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.vehicle_hierarchy_xls
    ADD CONSTRAINT vehicle_hierarchy_xls_pkey PRIMARY KEY (id);


--
-- Name: vehicles vehicles_license_plate_key; Type: CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.vehicles
    ADD CONSTRAINT vehicles_license_plate_key UNIQUE (license_plate);


--
-- Name: vehicles vehicles_pkey; Type: CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.vehicles
    ADD CONSTRAINT vehicles_pkey PRIMARY KEY (id);


--
-- Name: idx_aliases_part_id; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX idx_aliases_part_id ON public.part_aliases USING btree (part_id);


--
-- Name: idx_api_calls_created; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX idx_api_calls_created ON public.scraper_api_calls USING btree (created_at);


--
-- Name: idx_api_calls_source; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX idx_api_calls_source ON public.scraper_api_calls USING btree (source);


--
-- Name: idx_api_calls_success; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX idx_api_calls_success ON public.scraper_api_calls USING btree (success);


--
-- Name: idx_bug_reports_created; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX idx_bug_reports_created ON public.bug_reports USING btree (created_at);


--
-- Name: idx_bug_reports_severity; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX idx_bug_reports_severity ON public.bug_reports USING btree (severity);


--
-- Name: idx_bug_reports_status; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX idx_bug_reports_status ON public.bug_reports USING btree (status);


--
-- Name: idx_crossref_number_mfr; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX idx_crossref_number_mfr ON public.part_cross_reference USING btree (ref_number, manufacturer);


--
-- Name: idx_crossref_part_id; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX idx_crossref_part_id ON public.part_cross_reference USING btree (part_id);


--
-- Name: idx_crossref_ref_number; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX idx_crossref_ref_number ON public.part_cross_reference USING btree (ref_number);


--
-- Name: idx_fitment_mfr_model; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX idx_fitment_mfr_model ON public.part_vehicle_fitment USING btree (manufacturer, model);


--
-- Name: idx_fitment_part_id; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX idx_fitment_part_id ON public.part_vehicle_fitment USING btree (part_id);


--
-- Name: idx_fitment_years; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX idx_fitment_years ON public.part_vehicle_fitment USING btree (year_from, year_to);


--
-- Name: idx_part_variants_catalog_part_id; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX idx_part_variants_catalog_part_id ON public.part_variants USING btree (catalog_part_id);


--
-- Name: idx_part_variants_master_part_id; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX idx_part_variants_master_part_id ON public.part_variants USING btree (master_part_id);


--
-- Name: idx_parts_catalog_embedding; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX idx_parts_catalog_embedding ON public.parts_catalog USING ivfflat (embedding public.vector_cosine_ops) WITH (lists='100') WHERE (embedding IS NOT NULL);


--
-- Name: idx_parts_catalog_image_embedding; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX idx_parts_catalog_image_embedding ON public.parts_catalog USING ivfflat (image_embedding public.vector_cosine_ops) WITH (lists='100') WHERE (image_embedding IS NOT NULL);


--
-- Name: idx_parts_catalog_name_he_trgm; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX idx_parts_catalog_name_he_trgm ON public.parts_catalog USING gin (name_he public.gin_trgm_ops);


--
-- Name: idx_parts_catalog_name_trgm; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX idx_parts_catalog_name_trgm ON public.parts_catalog USING gin (name public.gin_trgm_ops);


--
-- Name: idx_parts_catalog_needs_enrichment; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX idx_parts_catalog_needs_enrichment ON public.parts_catalog USING btree (master_enriched) WHERE ((master_enriched = false) AND (needs_oem_lookup = false));


--
-- Name: idx_parts_catalog_oem_number; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX idx_parts_catalog_oem_number ON public.parts_catalog USING btree (oem_number);


--
-- Name: idx_parts_catalog_search_vector; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX idx_parts_catalog_search_vector ON public.parts_catalog USING gin (search_vector);


--
-- Name: idx_parts_catalog_superseded; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX idx_parts_catalog_superseded ON public.parts_catalog USING btree (superseded_by_sku);


--
-- Name: idx_parts_images_embedding_pending; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX idx_parts_images_embedding_pending ON public.parts_images USING btree (part_id) WHERE (embedding_generated = false);


--
-- Name: idx_parts_master_canonical_name; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX idx_parts_master_canonical_name ON public.parts_master USING btree (canonical_name);


--
-- Name: idx_parts_master_category; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX idx_parts_master_category ON public.parts_master USING btree (category);


--
-- Name: idx_po_order_id; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX idx_po_order_id ON public.purchase_orders USING btree (order_id);


--
-- Name: idx_po_status; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX idx_po_status ON public.purchase_orders USING btree (status);


--
-- Name: idx_po_supplier_id; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX idx_po_supplier_id ON public.purchase_orders USING btree (supplier_id);


--
-- Name: idx_price_history_created; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX idx_price_history_created ON public.price_history USING btree (created_at);


--
-- Name: idx_price_history_sp_id; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX idx_price_history_sp_id ON public.price_history USING btree (supplier_part_id);


--
-- Name: idx_search_misses_miss_count_triggered; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX idx_search_misses_miss_count_triggered ON public.search_misses USING btree (miss_count, triggered_scrape);


--
-- Name: idx_search_misses_triggered_notified; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX idx_search_misses_triggered_notified ON public.search_misses USING btree (triggered_scrape, notified) WHERE ((triggered_scrape = true) AND (notified = false));


--
-- Name: idx_suppliers_manufacturer_name; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX idx_suppliers_manufacturer_name ON public.suppliers USING btree (manufacturer_name);


--
-- Name: idx_vehicles_manufacturer_model; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX idx_vehicles_manufacturer_model ON public.vehicles USING btree (manufacturer, model);


--
-- Name: ix_audit_logs_created_at; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX ix_audit_logs_created_at ON public.audit_logs USING btree (created_at);


--
-- Name: ix_audit_logs_user_id; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX ix_audit_logs_user_id ON public.audit_logs USING btree (user_id);


--
-- Name: ix_brand_aliases_brand_id; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX ix_brand_aliases_brand_id ON public.brand_aliases USING btree (brand_id);


--
-- Name: ix_brand_aliases_normalized; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX ix_brand_aliases_normalized ON public.brand_aliases USING btree (normalized);


--
-- Name: ix_car_brands_group; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX ix_car_brands_group ON public.car_brands USING btree (group_name);


--
-- Name: ix_car_brands_name; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX ix_car_brands_name ON public.car_brands USING btree (name);


--
-- Name: ix_catalog_versions_started_at; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX ix_catalog_versions_started_at ON public.catalog_versions USING btree (started_at);


--
-- Name: ix_catalog_versions_status; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX ix_catalog_versions_status ON public.catalog_versions USING btree (status);


--
-- Name: ix_job_registry_job_id; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX ix_job_registry_job_id ON public.job_registry USING btree (job_id);


--
-- Name: ix_job_registry_job_name; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX ix_job_registry_job_name ON public.job_registry USING btree (job_name);


--
-- Name: ix_job_registry_started_at; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX ix_job_registry_started_at ON public.job_registry USING btree (started_at);


--
-- Name: ix_job_registry_status; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX ix_job_registry_status ON public.job_registry USING btree (status);


--
-- Name: ix_job_registry_status_heartbeat; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX ix_job_registry_status_heartbeat ON public.job_registry USING btree (status, last_heartbeat_at) WHERE ((status)::text = 'running'::text);


--
-- Name: ix_part_aliases_alias; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX ix_part_aliases_alias ON public.part_aliases USING btree (alias);


--
-- Name: ix_parts_catalog_base_price; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX ix_parts_catalog_base_price ON public.parts_catalog USING btree (base_price);


--
-- Name: ix_parts_catalog_category; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX ix_parts_catalog_category ON public.parts_catalog USING btree (category);


--
-- Name: ix_parts_catalog_created_at; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX ix_parts_catalog_created_at ON public.parts_catalog USING btree (created_at);


--
-- Name: ix_parts_catalog_is_active; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX ix_parts_catalog_is_active ON public.parts_catalog USING btree (is_active);


--
-- Name: ix_parts_catalog_manufacturer; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX ix_parts_catalog_manufacturer ON public.parts_catalog USING btree (manufacturer);


--
-- Name: ix_parts_catalog_name; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX ix_parts_catalog_name ON public.parts_catalog USING btree (name);


--
-- Name: ix_parts_catalog_sku; Type: INDEX; Schema: public; Owner: autospare
--

CREATE UNIQUE INDEX ix_parts_catalog_sku ON public.parts_catalog USING btree (sku);


--
-- Name: ix_parts_images_part_id; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX ix_parts_images_part_id ON public.parts_images USING btree (part_id);


--
-- Name: ix_social_posts_created_by; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX ix_social_posts_created_by ON public.social_posts USING btree (created_by);


--
-- Name: ix_social_posts_status_scheduled; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX ix_social_posts_status_scheduled ON public.social_posts USING btree (status, scheduled_at);


--
-- Name: ix_supplier_parts_part_id; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX ix_supplier_parts_part_id ON public.supplier_parts USING btree (part_id);


--
-- Name: ix_supplier_parts_supplier_id; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX ix_supplier_parts_supplier_id ON public.supplier_parts USING btree (supplier_id);


--
-- Name: ix_system_logs_created_at; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX ix_system_logs_created_at ON public.system_logs USING btree (created_at);


--
-- Name: ix_truck_brand_aliases_brand_id; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX ix_truck_brand_aliases_brand_id ON public.truck_brand_aliases USING btree (brand_id);


--
-- Name: ix_truck_brand_aliases_normalized; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX ix_truck_brand_aliases_normalized ON public.truck_brand_aliases USING btree (normalized);


--
-- Name: ix_truck_brands_group; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX ix_truck_brands_group ON public.truck_brands USING btree (group_name);


--
-- Name: ix_truck_brands_name; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX ix_truck_brands_name ON public.truck_brands USING btree (name);


--
-- Name: ix_vehicles_manufacturer; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX ix_vehicles_manufacturer ON public.vehicles USING btree (manufacturer);


--
-- Name: brand_aliases brand_aliases_brand_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.brand_aliases
    ADD CONSTRAINT brand_aliases_brand_id_fkey FOREIGN KEY (brand_id) REFERENCES public.car_brands(id) ON DELETE CASCADE;


--
-- Name: part_aliases part_aliases_part_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.part_aliases
    ADD CONSTRAINT part_aliases_part_id_fkey FOREIGN KEY (part_id) REFERENCES public.parts_catalog(id) ON DELETE CASCADE;


--
-- Name: part_cross_reference part_cross_reference_part_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.part_cross_reference
    ADD CONSTRAINT part_cross_reference_part_id_fkey FOREIGN KEY (part_id) REFERENCES public.parts_catalog(id) ON DELETE CASCADE;


--
-- Name: part_variants part_variants_catalog_part_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.part_variants
    ADD CONSTRAINT part_variants_catalog_part_id_fkey FOREIGN KEY (catalog_part_id) REFERENCES public.parts_catalog(id) ON DELETE CASCADE;


--
-- Name: part_variants part_variants_master_part_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.part_variants
    ADD CONSTRAINT part_variants_master_part_id_fkey FOREIGN KEY (master_part_id) REFERENCES public.parts_master(id) ON DELETE CASCADE;


--
-- Name: part_vehicle_fitment part_vehicle_fitment_part_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.part_vehicle_fitment
    ADD CONSTRAINT part_vehicle_fitment_part_id_fkey FOREIGN KEY (part_id) REFERENCES public.parts_catalog(id) ON DELETE CASCADE;


--
-- Name: parts_images parts_images_part_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.parts_images
    ADD CONSTRAINT parts_images_part_id_fkey FOREIGN KEY (part_id) REFERENCES public.parts_catalog(id) ON DELETE CASCADE;


--
-- Name: price_history price_history_supplier_part_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.price_history
    ADD CONSTRAINT price_history_supplier_part_id_fkey FOREIGN KEY (supplier_part_id) REFERENCES public.supplier_parts(id) ON DELETE CASCADE;


--
-- Name: purchase_orders purchase_orders_supplier_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.purchase_orders
    ADD CONSTRAINT purchase_orders_supplier_id_fkey FOREIGN KEY (supplier_id) REFERENCES public.suppliers(id);


--
-- Name: supplier_parts supplier_parts_part_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.supplier_parts
    ADD CONSTRAINT supplier_parts_part_id_fkey FOREIGN KEY (part_id) REFERENCES public.parts_catalog(id) ON DELETE CASCADE;


--
-- Name: supplier_parts supplier_parts_supplier_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.supplier_parts
    ADD CONSTRAINT supplier_parts_supplier_id_fkey FOREIGN KEY (supplier_id) REFERENCES public.suppliers(id) ON DELETE CASCADE;


--
-- Name: truck_brand_aliases truck_brand_aliases_brand_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.truck_brand_aliases
    ADD CONSTRAINT truck_brand_aliases_brand_id_fkey FOREIGN KEY (brand_id) REFERENCES public.truck_brands(id) ON DELETE CASCADE;


--
-- PostgreSQL database dump complete
--

\unrestrict 8f5pXrgMAtFBgQffZyMoXOSZsjFkslkhd8lnHfkK1mMONYr0WfO1gWVZqvurMmS

