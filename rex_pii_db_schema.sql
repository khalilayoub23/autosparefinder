--
-- PostgreSQL database dump
--

\restrict DWYUYLSmaZcuAThpu0vb3Ib0coeLJPDsL1Arfg481UqxbeBAjtZIcbSr4KC1yAM

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
-- Name: pgcrypto; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public;


--
-- Name: EXTENSION pgcrypto; Type: COMMENT; Schema: -; Owner: 
--

COMMENT ON EXTENSION pgcrypto IS 'cryptographic functions';


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
-- Name: approval_queue; Type: TABLE; Schema: public; Owner: autospare
--

CREATE TABLE public.approval_queue (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    entity_type character varying(50) NOT NULL,
    entity_id uuid NOT NULL,
    action character varying(50) NOT NULL,
    payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    status character varying(20) DEFAULT 'pending'::character varying NOT NULL,
    requested_by uuid,
    resolved_by uuid,
    resolution_note text,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    resolved_at timestamp without time zone,
    idempotency_key character varying(255),
    CONSTRAINT ck_approval_queue_status CHECK (((status)::text = ANY ((ARRAY['pending'::character varying, 'approved'::character varying, 'rejected'::character varying])::text[])))
);


ALTER TABLE public.approval_queue OWNER TO autospare;

--
-- Name: COLUMN approval_queue.entity_id; Type: COMMENT; Schema: public; Owner: autospare
--

COMMENT ON COLUMN public.approval_queue.entity_id IS 'UUID reference — target table determined by entity_type; no FK (may be cross-DB)';


--
-- Name: COLUMN approval_queue.idempotency_key; Type: COMMENT; Schema: public; Owner: autospare
--

COMMENT ON COLUMN public.approval_queue.idempotency_key IS 'Idempotency key for deduplication (sha256 of entity_type:entity_id:action)';


--
-- Name: cart_items; Type: TABLE; Schema: public; Owner: autospare
--

CREATE TABLE public.cart_items (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    cart_id uuid NOT NULL,
    part_id uuid NOT NULL,
    supplier_part_id uuid NOT NULL,
    quantity integer DEFAULT 1 NOT NULL,
    unit_price numeric(10,2) NOT NULL,
    added_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_cart_items_quantity CHECK ((quantity > 0))
);


ALTER TABLE public.cart_items OWNER TO autospare;

--
-- Name: carts; Type: TABLE; Schema: public; Owner: autospare
--

CREATE TABLE public.carts (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.carts OWNER TO autospare;

--
-- Name: conversations; Type: TABLE; Schema: public; Owner: autospare
--

CREATE TABLE public.conversations (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    session_id character varying(100),
    context jsonb,
    status character varying(50) DEFAULT 'active'::character varying,
    created_at timestamp without time zone DEFAULT now(),
    updated_at timestamp without time zone,
    deleted_at timestamp without time zone
);


ALTER TABLE public.conversations OWNER TO autospare;

--
-- Name: invoices; Type: TABLE; Schema: public; Owner: autospare
--

CREATE TABLE public.invoices (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    order_id uuid NOT NULL,
    user_id uuid NOT NULL,
    invoice_number character varying(50) NOT NULL,
    total_ils numeric(12,2) NOT NULL,
    vat_ils numeric(12,2) NOT NULL,
    issued_at timestamp without time zone DEFAULT now(),
    due_at timestamp without time zone,
    pdf_url character varying(500),
    status character varying(50) DEFAULT 'issued'::character varying,
    created_at timestamp without time zone DEFAULT now()
);


ALTER TABLE public.invoices OWNER TO autospare;

--
-- Name: job_failures; Type: TABLE; Schema: public; Owner: autospare
--

CREATE TABLE public.job_failures (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    job_name character varying(255) NOT NULL,
    payload json,
    error text,
    attempts integer DEFAULT 1 NOT NULL,
    next_retry_at timestamp with time zone,
    status character varying(50) DEFAULT 'pending'::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    resolved_at timestamp with time zone,
    resolved_by character varying(255)
);


ALTER TABLE public.job_failures OWNER TO autospare;

--
-- Name: login_attempts; Type: TABLE; Schema: public; Owner: autospare
--

CREATE TABLE public.login_attempts (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid,
    email character varying(255),
    ip_address character varying(45) NOT NULL,
    success boolean NOT NULL,
    failure_reason character varying(100),
    created_at timestamp without time zone DEFAULT now()
);


ALTER TABLE public.login_attempts OWNER TO autospare;

--
-- Name: messages; Type: TABLE; Schema: public; Owner: autospare
--

CREATE TABLE public.messages (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    conversation_id uuid NOT NULL,
    role character varying(20) NOT NULL,
    content text NOT NULL,
    metadata jsonb,
    created_at timestamp without time zone DEFAULT now(),
    deleted_at timestamp without time zone
);


ALTER TABLE public.messages OWNER TO autospare;

--
-- Name: notifications; Type: TABLE; Schema: public; Owner: autospare
--

CREATE TABLE public.notifications (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    title character varying(255) NOT NULL,
    message text,
    type character varying(50) DEFAULT 'info'::character varying,
    is_read boolean DEFAULT false,
    data jsonb,
    created_at timestamp without time zone DEFAULT now(),
    read_at timestamp without time zone,
    channel character varying(20) DEFAULT 'push'::character varying,
    sent_at timestamp without time zone
);


ALTER TABLE public.notifications OWNER TO autospare;

--
-- Name: order_items; Type: TABLE; Schema: public; Owner: autospare
--

CREATE TABLE public.order_items (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    order_id uuid NOT NULL,
    part_id uuid,
    supplier_part_id uuid,
    part_sku character varying(100),
    name_he character varying(255),
    name_en character varying(255),
    quantity integer NOT NULL,
    unit_price numeric(12,2) NOT NULL,
    total_price numeric(12,2) NOT NULL,
    is_express boolean DEFAULT false,
    created_at timestamp without time zone DEFAULT now(),
    part_name character varying(255) NOT NULL,
    manufacturer character varying(100),
    part_type character varying(50),
    supplier_name character varying(255),
    supplier_order_id character varying(100),
    vat_amount numeric(10,2) DEFAULT '0'::numeric NOT NULL,
    warranty_months integer DEFAULT 12
);


ALTER TABLE public.order_items OWNER TO autospare;

--
-- Name: orders; Type: TABLE; Schema: public; Owner: autospare
--

CREATE TABLE public.orders (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    vehicle_id uuid,
    status character varying(50) DEFAULT 'pending_payment'::character varying NOT NULL,
    subtotal numeric(12,2) NOT NULL,
    vat_amount numeric(12,2) NOT NULL,
    shipping_cost numeric(12,2) DEFAULT '0'::numeric,
    total_amount numeric(12,2) NOT NULL,
    currency character varying(3) DEFAULT 'ILS'::character varying,
    notes text,
    shipping_address jsonb,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone,
    deleted_at timestamp without time zone,
    order_number character varying(20) NOT NULL,
    discount_amount numeric(10,2) DEFAULT '0'::numeric,
    tracking_number character varying(100),
    tracking_url character varying(500),
    estimated_delivery timestamp without time zone,
    coupon_code character varying(50),
    shipping_type character varying(20) DEFAULT 'standard'::character varying,
    shipped_at timestamp without time zone,
    delivered_at timestamp without time zone,
    cancelled_at timestamp without time zone
);


ALTER TABLE public.orders OWNER TO autospare;

--
-- Name: part_reviews; Type: TABLE; Schema: public; Owner: autospare
--

CREATE TABLE public.part_reviews (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    part_id uuid NOT NULL,
    order_id uuid,
    rating integer NOT NULL,
    title character varying(255),
    body text,
    is_verified_purchase boolean DEFAULT false NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_part_review_rating CHECK (((rating >= 1) AND (rating <= 5)))
);


ALTER TABLE public.part_reviews OWNER TO autospare;

--
-- Name: password_resets; Type: TABLE; Schema: public; Owner: autospare
--

CREATE TABLE public.password_resets (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    token character varying(255) NOT NULL,
    expires_at timestamp without time zone NOT NULL,
    used_at timestamp without time zone,
    created_at timestamp without time zone DEFAULT now()
);


ALTER TABLE public.password_resets OWNER TO autospare;

--
-- Name: payments; Type: TABLE; Schema: public; Owner: autospare
--

CREATE TABLE public.payments (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    order_id uuid NOT NULL,
    user_id uuid NOT NULL,
    amount_ils numeric(12,2) NOT NULL,
    currency character varying(3) DEFAULT 'ILS'::character varying,
    status character varying(50) DEFAULT 'pending'::character varying NOT NULL,
    provider character varying(50),
    provider_transaction_id character varying(255),
    last_four character varying(4),
    card_brand character varying(30),
    error_message text,
    created_at timestamp without time zone DEFAULT now(),
    updated_at timestamp without time zone,
    payment_intent_id character varying(255),
    amount numeric(10,2) NOT NULL,
    payment_method character varying(50),
    stripe_customer_id character varying(255),
    last_4_digits character varying(4),
    paid_at timestamp without time zone,
    refunded_at timestamp without time zone,
    refund_amount numeric(10,2),
    refund_reason character varying(255)
);


ALTER TABLE public.payments OWNER TO autospare;

--
-- Name: returns; Type: TABLE; Schema: public; Owner: autospare
--

CREATE TABLE public.returns (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    order_id uuid NOT NULL,
    user_id uuid NOT NULL,
    reason text,
    status character varying(50) DEFAULT 'pending'::character varying,
    refund_amount_ils numeric(12,2),
    approved_at timestamp without time zone,
    created_at timestamp without time zone DEFAULT now(),
    updated_at timestamp without time zone,
    item_shipped_at timestamp without time zone,
    supplier_confirmed_at timestamp without time zone,
    refund_issued_at timestamp without time zone,
    supplier_notes text
);


ALTER TABLE public.returns OWNER TO autospare;

--
-- Name: stripe_webhook_logs; Type: TABLE; Schema: public; Owner: autospare
--

CREATE TABLE public.stripe_webhook_logs (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    event_id character varying(255) NOT NULL,
    event_type character varying(100) NOT NULL,
    processed boolean DEFAULT false NOT NULL,
    payload json,
    result json,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    processed_at timestamp with time zone
);


ALTER TABLE public.stripe_webhook_logs OWNER TO autospare;

--
-- Name: COLUMN stripe_webhook_logs.event_id; Type: COMMENT; Schema: public; Owner: autospare
--

COMMENT ON COLUMN public.stripe_webhook_logs.event_id IS 'Stripe event_id for deduplication';


--
-- Name: COLUMN stripe_webhook_logs.event_type; Type: COMMENT; Schema: public; Owner: autospare
--

COMMENT ON COLUMN public.stripe_webhook_logs.event_type IS 'Stripe event type (e.g., charge.succeeded)';


--
-- Name: COLUMN stripe_webhook_logs.processed; Type: COMMENT; Schema: public; Owner: autospare
--

COMMENT ON COLUMN public.stripe_webhook_logs.processed IS 'Whether event was successfully processed';


--
-- Name: COLUMN stripe_webhook_logs.payload; Type: COMMENT; Schema: public; Owner: autospare
--

COMMENT ON COLUMN public.stripe_webhook_logs.payload IS 'Full Stripe event payload';


--
-- Name: COLUMN stripe_webhook_logs.result; Type: COMMENT; Schema: public; Owner: autospare
--

COMMENT ON COLUMN public.stripe_webhook_logs.result IS 'Processing result or error details';


--
-- Name: COLUMN stripe_webhook_logs.processed_at; Type: COMMENT; Schema: public; Owner: autospare
--

COMMENT ON COLUMN public.stripe_webhook_logs.processed_at IS 'When event was processed';


--
-- Name: two_factor_codes; Type: TABLE; Schema: public; Owner: autospare
--

CREATE TABLE public.two_factor_codes (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    code character varying(6) NOT NULL,
    phone character varying(20),
    attempts integer DEFAULT 0,
    expires_at timestamp without time zone NOT NULL,
    verified_at timestamp without time zone,
    created_at timestamp without time zone DEFAULT now()
);


ALTER TABLE public.two_factor_codes OWNER TO autospare;

--
-- Name: user_profiles; Type: TABLE; Schema: public; Owner: autospare
--

CREATE TABLE public.user_profiles (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    address_line1 character varying(255),
    address_line2 character varying(255),
    city character varying(100),
    postal_code character varying(20),
    default_vehicle_id uuid,
    marketing_consent boolean DEFAULT false,
    newsletter_subscribed boolean DEFAULT false,
    terms_accepted_at timestamp without time zone,
    marketing_preferences jsonb,
    preferred_language character varying(10) DEFAULT 'he'::character varying,
    avatar_url character varying(500),
    created_at timestamp without time zone DEFAULT now(),
    updated_at timestamp without time zone,
    customer_type character varying(20) DEFAULT 'individual'::character varying NOT NULL,
    total_orders integer DEFAULT 0 NOT NULL,
    total_spent_ils numeric(12,2) DEFAULT 0 NOT NULL,
    is_vip boolean DEFAULT false NOT NULL,
    vip_since timestamp without time zone,
    CONSTRAINT user_profiles_customer_type_check CHECK (((customer_type)::text = ANY ((ARRAY['individual'::character varying, 'mechanic'::character varying, 'garage'::character varying, 'retailer'::character varying, 'fleet'::character varying])::text[])))
);


ALTER TABLE public.user_profiles OWNER TO autospare;

--
-- Name: user_sessions; Type: TABLE; Schema: public; Owner: autospare
--

CREATE TABLE public.user_sessions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    token character varying(500) NOT NULL,
    refresh_token character varying(500),
    device_fingerprint character varying(255),
    device_name character varying(255),
    ip_address character varying(45),
    user_agent text,
    is_trusted_device boolean DEFAULT false,
    trusted_until timestamp without time zone,
    expires_at timestamp without time zone NOT NULL,
    last_used_at timestamp without time zone DEFAULT now(),
    revoked_at timestamp without time zone,
    created_at timestamp without time zone DEFAULT now()
);


ALTER TABLE public.user_sessions OWNER TO autospare;

--
-- Name: user_vehicles; Type: TABLE; Schema: public; Owner: autospare
--

CREATE TABLE public.user_vehicles (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    vehicle_id uuid NOT NULL,
    nickname character varying(100),
    is_primary boolean DEFAULT false,
    created_at timestamp without time zone DEFAULT now()
);


ALTER TABLE public.user_vehicles OWNER TO autospare;

--
-- Name: users; Type: TABLE; Schema: public; Owner: autospare
--

CREATE TABLE public.users (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    email character varying(255) NOT NULL,
    phone character varying(20),
    password_hash character varying(255),
    full_name character varying(255) NOT NULL,
    role character varying(50) DEFAULT 'customer'::character varying NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    is_verified boolean DEFAULT false NOT NULL,
    is_admin boolean DEFAULT false NOT NULL,
    failed_login_count integer DEFAULT 0 NOT NULL,
    locked_until timestamp without time zone,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone,
    is_super_admin boolean DEFAULT false NOT NULL,
    oauth_provider character varying(32),
    oauth_id character varying(255)
);


ALTER TABLE public.users OWNER TO autospare;

--
-- Name: wishlist_items; Type: TABLE; Schema: public; Owner: autospare
--

CREATE TABLE public.wishlist_items (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    part_id uuid NOT NULL,
    added_at timestamp without time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.wishlist_items OWNER TO autospare;

--
-- Name: alembic_version alembic_version_pkc; Type: CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.alembic_version
    ADD CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num);


--
-- Name: approval_queue approval_queue_idempotency_key_key; Type: CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.approval_queue
    ADD CONSTRAINT approval_queue_idempotency_key_key UNIQUE (idempotency_key);


--
-- Name: approval_queue approval_queue_pkey; Type: CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.approval_queue
    ADD CONSTRAINT approval_queue_pkey PRIMARY KEY (id);


--
-- Name: cart_items cart_items_pkey; Type: CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.cart_items
    ADD CONSTRAINT cart_items_pkey PRIMARY KEY (id);


--
-- Name: carts carts_pkey; Type: CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.carts
    ADD CONSTRAINT carts_pkey PRIMARY KEY (id);


--
-- Name: conversations conversations_pkey; Type: CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.conversations
    ADD CONSTRAINT conversations_pkey PRIMARY KEY (id);


--
-- Name: invoices invoices_invoice_number_key; Type: CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.invoices
    ADD CONSTRAINT invoices_invoice_number_key UNIQUE (invoice_number);


--
-- Name: invoices invoices_pkey; Type: CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.invoices
    ADD CONSTRAINT invoices_pkey PRIMARY KEY (id);


--
-- Name: job_failures job_failures_pkey; Type: CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.job_failures
    ADD CONSTRAINT job_failures_pkey PRIMARY KEY (id);


--
-- Name: login_attempts login_attempts_pkey; Type: CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.login_attempts
    ADD CONSTRAINT login_attempts_pkey PRIMARY KEY (id);


--
-- Name: messages messages_pkey; Type: CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.messages
    ADD CONSTRAINT messages_pkey PRIMARY KEY (id);


--
-- Name: notifications notifications_pkey; Type: CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.notifications
    ADD CONSTRAINT notifications_pkey PRIMARY KEY (id);


--
-- Name: order_items order_items_pkey; Type: CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.order_items
    ADD CONSTRAINT order_items_pkey PRIMARY KEY (id);


--
-- Name: orders orders_pkey; Type: CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.orders
    ADD CONSTRAINT orders_pkey PRIMARY KEY (id);


--
-- Name: part_reviews part_reviews_pkey; Type: CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.part_reviews
    ADD CONSTRAINT part_reviews_pkey PRIMARY KEY (id);


--
-- Name: password_resets password_resets_pkey; Type: CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.password_resets
    ADD CONSTRAINT password_resets_pkey PRIMARY KEY (id);


--
-- Name: password_resets password_resets_token_key; Type: CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.password_resets
    ADD CONSTRAINT password_resets_token_key UNIQUE (token);


--
-- Name: payments payments_pkey; Type: CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.payments
    ADD CONSTRAINT payments_pkey PRIMARY KEY (id);


--
-- Name: returns returns_pkey; Type: CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.returns
    ADD CONSTRAINT returns_pkey PRIMARY KEY (id);


--
-- Name: stripe_webhook_logs stripe_webhook_logs_event_id_key; Type: CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.stripe_webhook_logs
    ADD CONSTRAINT stripe_webhook_logs_event_id_key UNIQUE (event_id);


--
-- Name: stripe_webhook_logs stripe_webhook_logs_pkey; Type: CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.stripe_webhook_logs
    ADD CONSTRAINT stripe_webhook_logs_pkey PRIMARY KEY (id);


--
-- Name: two_factor_codes two_factor_codes_pkey; Type: CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.two_factor_codes
    ADD CONSTRAINT two_factor_codes_pkey PRIMARY KEY (id);


--
-- Name: cart_items uq_cart_item; Type: CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.cart_items
    ADD CONSTRAINT uq_cart_item UNIQUE (cart_id, supplier_part_id);


--
-- Name: carts uq_carts_user; Type: CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.carts
    ADD CONSTRAINT uq_carts_user UNIQUE (user_id);


--
-- Name: orders uq_orders_order_number; Type: CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.orders
    ADD CONSTRAINT uq_orders_order_number UNIQUE (order_number);


--
-- Name: part_reviews uq_part_review; Type: CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.part_reviews
    ADD CONSTRAINT uq_part_review UNIQUE (user_id, part_id);


--
-- Name: wishlist_items uq_wishlist_item; Type: CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.wishlist_items
    ADD CONSTRAINT uq_wishlist_item UNIQUE (user_id, part_id);


--
-- Name: user_profiles user_profiles_pkey; Type: CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.user_profiles
    ADD CONSTRAINT user_profiles_pkey PRIMARY KEY (id);


--
-- Name: user_profiles user_profiles_user_id_key; Type: CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.user_profiles
    ADD CONSTRAINT user_profiles_user_id_key UNIQUE (user_id);


--
-- Name: user_sessions user_sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.user_sessions
    ADD CONSTRAINT user_sessions_pkey PRIMARY KEY (id);


--
-- Name: user_sessions user_sessions_refresh_token_key; Type: CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.user_sessions
    ADD CONSTRAINT user_sessions_refresh_token_key UNIQUE (refresh_token);


--
-- Name: user_sessions user_sessions_token_key; Type: CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.user_sessions
    ADD CONSTRAINT user_sessions_token_key UNIQUE (token);


--
-- Name: user_vehicles user_vehicles_pkey; Type: CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.user_vehicles
    ADD CONSTRAINT user_vehicles_pkey PRIMARY KEY (id);


--
-- Name: user_vehicles user_vehicles_user_id_vehicle_id_key; Type: CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.user_vehicles
    ADD CONSTRAINT user_vehicles_user_id_vehicle_id_key UNIQUE (user_id, vehicle_id);


--
-- Name: users users_email_key; Type: CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_email_key UNIQUE (email);


--
-- Name: users users_phone_key; Type: CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_phone_key UNIQUE (phone);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- Name: wishlist_items wishlist_items_pkey; Type: CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.wishlist_items
    ADD CONSTRAINT wishlist_items_pkey PRIMARY KEY (id);


--
-- Name: idx_conversations_user_id; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX idx_conversations_user_id ON public.conversations USING btree (user_id);


--
-- Name: idx_login_attempts_created_at; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX idx_login_attempts_created_at ON public.login_attempts USING btree (created_at);


--
-- Name: idx_login_attempts_ip; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX idx_login_attempts_ip ON public.login_attempts USING btree (ip_address);


--
-- Name: idx_messages_conversation_id; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX idx_messages_conversation_id ON public.messages USING btree (conversation_id);


--
-- Name: idx_notifications_unread; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX idx_notifications_unread ON public.notifications USING btree (user_id, is_read);


--
-- Name: idx_notifications_user_id; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX idx_notifications_user_id ON public.notifications USING btree (user_id);


--
-- Name: idx_order_items_order_id; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX idx_order_items_order_id ON public.order_items USING btree (order_id);


--
-- Name: idx_orders_status; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX idx_orders_status ON public.orders USING btree (status);


--
-- Name: idx_orders_user_id; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX idx_orders_user_id ON public.orders USING btree (user_id);


--
-- Name: idx_payments_order_id; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX idx_payments_order_id ON public.payments USING btree (order_id);


--
-- Name: idx_payments_user_id; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX idx_payments_user_id ON public.payments USING btree (user_id);


--
-- Name: idx_returns_user_id; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX idx_returns_user_id ON public.returns USING btree (user_id);


--
-- Name: idx_two_factor_codes_user_id; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX idx_two_factor_codes_user_id ON public.two_factor_codes USING btree (user_id);


--
-- Name: idx_user_sessions_user_id; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX idx_user_sessions_user_id ON public.user_sessions USING btree (user_id);


--
-- Name: idx_user_vehicles_user_id; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX idx_user_vehicles_user_id ON public.user_vehicles USING btree (user_id);


--
-- Name: idx_users_email; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX idx_users_email ON public.users USING btree (email);


--
-- Name: ix_approval_queue_entity_type; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX ix_approval_queue_entity_type ON public.approval_queue USING btree (entity_type);


--
-- Name: ix_approval_queue_idempotency_key; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX ix_approval_queue_idempotency_key ON public.approval_queue USING btree (idempotency_key);


--
-- Name: ix_approval_queue_requested_by; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX ix_approval_queue_requested_by ON public.approval_queue USING btree (requested_by);


--
-- Name: ix_approval_queue_status; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX ix_approval_queue_status ON public.approval_queue USING btree (status);


--
-- Name: ix_cart_items_cart_id; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX ix_cart_items_cart_id ON public.cart_items USING btree (cart_id);


--
-- Name: ix_carts_updated_at; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX ix_carts_updated_at ON public.carts USING btree (updated_at);


--
-- Name: ix_conversations_deleted_at; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX ix_conversations_deleted_at ON public.conversations USING btree (deleted_at);


--
-- Name: ix_job_failures_created_at; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX ix_job_failures_created_at ON public.job_failures USING btree (created_at);


--
-- Name: ix_job_failures_job_name; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX ix_job_failures_job_name ON public.job_failures USING btree (job_name);


--
-- Name: ix_job_failures_next_retry_at; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX ix_job_failures_next_retry_at ON public.job_failures USING btree (next_retry_at);


--
-- Name: ix_job_failures_status; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX ix_job_failures_status ON public.job_failures USING btree (status);


--
-- Name: ix_job_failures_status_next_retry; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX ix_job_failures_status_next_retry ON public.job_failures USING btree (status, next_retry_at);


--
-- Name: ix_login_attempts_user_id; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX ix_login_attempts_user_id ON public.login_attempts USING btree (user_id);


--
-- Name: ix_messages_deleted_at; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX ix_messages_deleted_at ON public.messages USING btree (deleted_at);


--
-- Name: ix_notifications_user_read_created; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX ix_notifications_user_read_created ON public.notifications USING btree (user_id, read_at, created_at);


--
-- Name: ix_order_items_part_id; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX ix_order_items_part_id ON public.order_items USING btree (part_id);


--
-- Name: ix_orders_deleted_at; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX ix_orders_deleted_at ON public.orders USING btree (deleted_at);


--
-- Name: ix_orders_order_number; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX ix_orders_order_number ON public.orders USING btree (order_number);


--
-- Name: ix_part_reviews_part_id; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX ix_part_reviews_part_id ON public.part_reviews USING btree (part_id);


--
-- Name: ix_payments_paid_at; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX ix_payments_paid_at ON public.payments USING btree (paid_at);


--
-- Name: ix_payments_payment_intent_id; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX ix_payments_payment_intent_id ON public.payments USING btree (payment_intent_id);


--
-- Name: ix_stripe_webhook_logs_created_at; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX ix_stripe_webhook_logs_created_at ON public.stripe_webhook_logs USING btree (created_at);


--
-- Name: ix_stripe_webhook_logs_event_id; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX ix_stripe_webhook_logs_event_id ON public.stripe_webhook_logs USING btree (event_id);


--
-- Name: ix_stripe_webhook_logs_event_type; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX ix_stripe_webhook_logs_event_type ON public.stripe_webhook_logs USING btree (event_type);


--
-- Name: ix_stripe_webhook_logs_processed; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX ix_stripe_webhook_logs_processed ON public.stripe_webhook_logs USING btree (processed);


--
-- Name: ix_user_profiles_customer_type; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX ix_user_profiles_customer_type ON public.user_profiles USING btree (customer_type);


--
-- Name: ix_user_profiles_is_vip; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX ix_user_profiles_is_vip ON public.user_profiles USING btree (is_vip);


--
-- Name: ix_users_is_super_admin; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX ix_users_is_super_admin ON public.users USING btree (is_super_admin);


--
-- Name: ix_users_oauth_id; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX ix_users_oauth_id ON public.users USING btree (oauth_id);


--
-- Name: ix_wishlist_items_user_id; Type: INDEX; Schema: public; Owner: autospare
--

CREATE INDEX ix_wishlist_items_user_id ON public.wishlist_items USING btree (user_id);


--
-- Name: approval_queue approval_queue_requested_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.approval_queue
    ADD CONSTRAINT approval_queue_requested_by_fkey FOREIGN KEY (requested_by) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: approval_queue approval_queue_resolved_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.approval_queue
    ADD CONSTRAINT approval_queue_resolved_by_fkey FOREIGN KEY (resolved_by) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: cart_items cart_items_cart_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.cart_items
    ADD CONSTRAINT cart_items_cart_id_fkey FOREIGN KEY (cart_id) REFERENCES public.carts(id) ON DELETE CASCADE;


--
-- Name: carts carts_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.carts
    ADD CONSTRAINT carts_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: conversations conversations_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.conversations
    ADD CONSTRAINT conversations_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: invoices invoices_order_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.invoices
    ADD CONSTRAINT invoices_order_id_fkey FOREIGN KEY (order_id) REFERENCES public.orders(id);


--
-- Name: invoices invoices_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.invoices
    ADD CONSTRAINT invoices_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: login_attempts login_attempts_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.login_attempts
    ADD CONSTRAINT login_attempts_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: messages messages_conversation_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.messages
    ADD CONSTRAINT messages_conversation_id_fkey FOREIGN KEY (conversation_id) REFERENCES public.conversations(id) ON DELETE CASCADE;


--
-- Name: notifications notifications_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.notifications
    ADD CONSTRAINT notifications_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: order_items order_items_order_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.order_items
    ADD CONSTRAINT order_items_order_id_fkey FOREIGN KEY (order_id) REFERENCES public.orders(id) ON DELETE CASCADE;


--
-- Name: orders orders_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.orders
    ADD CONSTRAINT orders_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: part_reviews part_reviews_order_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.part_reviews
    ADD CONSTRAINT part_reviews_order_id_fkey FOREIGN KEY (order_id) REFERENCES public.orders(id) ON DELETE SET NULL;


--
-- Name: part_reviews part_reviews_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.part_reviews
    ADD CONSTRAINT part_reviews_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: password_resets password_resets_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.password_resets
    ADD CONSTRAINT password_resets_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: payments payments_order_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.payments
    ADD CONSTRAINT payments_order_id_fkey FOREIGN KEY (order_id) REFERENCES public.orders(id);


--
-- Name: payments payments_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.payments
    ADD CONSTRAINT payments_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: returns returns_order_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.returns
    ADD CONSTRAINT returns_order_id_fkey FOREIGN KEY (order_id) REFERENCES public.orders(id);


--
-- Name: returns returns_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.returns
    ADD CONSTRAINT returns_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: two_factor_codes two_factor_codes_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.two_factor_codes
    ADD CONSTRAINT two_factor_codes_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: user_profiles user_profiles_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.user_profiles
    ADD CONSTRAINT user_profiles_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: user_sessions user_sessions_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.user_sessions
    ADD CONSTRAINT user_sessions_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: user_vehicles user_vehicles_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.user_vehicles
    ADD CONSTRAINT user_vehicles_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: wishlist_items wishlist_items_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: autospare
--

ALTER TABLE ONLY public.wishlist_items
    ADD CONSTRAINT wishlist_items_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- PostgreSQL database dump complete
--

\unrestrict DWYUYLSmaZcuAThpu0vb3Ib0coeLJPDsL1Arfg481UqxbeBAjtZIcbSr4KC1yAM

