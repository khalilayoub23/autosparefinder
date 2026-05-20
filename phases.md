# AutoSpareFinder — Pipeline Phases & 20-Layer Architecture

## Navigation

- Agent rules that govern all phases → READ: claude.md
- Which agent/worker runs each layer → READ: skills.md § LAYER B
- UI output of phases 5-6 → READ: ui-ux.md

---

## Pipeline Overview

```
PHASE 1: Data Acquisition          L1, L13
PHASE 2: Normalization & Quality   L2, L3, L11, L12, L17
PHASE 3: Vehicle Intelligence      L4, L5, L19
PHASE 4: Fitment & Classification  L6, L7, L8, L9, L10
PHASE 5: Publishing & Search       L14, L15, L16
PHASE 6: Pricing & Market          L18, L20
```

**Rule:** Phases run sequentially. Layers within a phase can run in parallel.
A phase cannot start until the previous phase meets its exit criteria.
All workers must follow rules in claude.md § Data Rules.

---

## PHASE 1 — Data Acquisition

**Goal:** Ingest raw parts data from all sources
**Worker:** catalog_scraper.py (SEE skills.md § catalog_scraper.py)
**Schedule:** 00:00 + 12:00 UTC — triggered by REX agent (SEE claude.md § Scheduled Jobs)

### Layer 1 — Source Ingestion

**Worker:** catalog_scraper.py

Sources:

- `scrape_motorstore(part_number, manufacturer)` → Motorstore.co.il ✅ accessible
- `scrape_meyle(...)` → Meyle.com ✅
- `scrape_gates(...)` → Gates.com ✅
- `scrape_brembo(...)` → Brembo.com ✅
- `scrape_ebay_motors(...)` → eBay Motors ✅ (API connected)
- `scrape_aliexpress(query, max_results)` → AliExpress ✅ (IOP API)
- `scrape_google_shopping(query)` → Google Shopping ✅
- `scrape_autodoc(...)` → ⚠️ Cloudflare limited
- `scrape_rockauto(...)` → ❌ blocked from Hetzner
- `ask_gpt4o(brand, prompt)` → AI-generated via ai_catalog_builder.py

All API calls logged to `scraper_api_calls` table.

### Layer 13 — Import Orchestration

**Worker:** catalog_scraper.py (REX scheduler)

- Distributed lock prevents duplicate runs (distributed_lock.py)
- Trigger logic: DAILY if new_records_7d ≥ 1000 OR manufacturer_changes_7d ≥ 5
- WEEKLY otherwise
- All jobs logged via job_registry_start/finish (resilience.py)

**Exit Criteria:** Raw data staged. Import job logged. No duplicate runs within 6h.

---

## PHASE 2 — Normalization & Quality

**Goal:** Transform raw data into canonical, validated format
**Workers:** db_cleanup_agent.py + db_update_agent.py (SEE skills.md § these workers)
**Cycle:** db_cleanup_agent runs every 30s, 340 rows per cycle

### Layer 2 — Canonical Normalization

**Worker:** db_cleanup_agent.py (tasks 1, 6, 8) + db_update_agent.py

db_cleanup_agent tasks:

- `task6` → `_apply_task6_rules(name)` — fix encoding, RTL artifacts, reversed French/Latin prefixes
- `task8` → extract spec prefixes into specifications JSONB

db_update_agent functions:

- `clean_part_names(db)` — full name normalization pipeline
- `normalize_part_types(db)` — canonical part type values
- `normalize_categories(db)` — sync with categories.py CATEGORY_MAP
- `normalize_availability(db)` — canonical availability strings
- `_strip_trailing_vehicle_suffix(value)` — remove vehicle suffixes from names
- `_normalize_part_name_punctuation(value)` — fix dashes, dots
- `_reverse_latin_prefix(name)` — fix reversed RTL text

Brand canonicalization uses: `manufacturer_normalization.py` → PARTS_BRANDS, CANONICAL_CAR_BY_ALIAS

### Layer 3 — Manufacturer Intelligence

**Worker:** db_cleanup_agent.py (tasks 5, 7) + db_update_agent.py

- `task7` → link manufacturer text to `manufacturer_id` in car_brands table
- `task5` → detect_manufacturer_overflow
- `normalize_imported_manufacturers(db)` → free-text → canonical name
- `fill_car_brands(db)` → seed il_importer, warranty_years, warranty_km (Israeli market)
- `sync_manufacturer_registries(db)` → sync brand registries
- `fix_manufacturer_overflow(db)` → correct wrong brand assignments

### Layer 11 — Validation

**Worker:** db_update_agent.py + db_cleanup_agent.py (tasks 1, 4)

- `fix_base_prices(db)` → correct invalid price values
- `flag_fake_skus(db)` → detect non-real SKUs via `_is_fake_sku(sku)`
- `dedup_catalog_parts(db)` → find and merge duplicates
- `task1` → fix_part_types (normalize part_type)
- `task4` → fix_oem_lookup_flag

### Layer 12 — Conflict Resolution

**Worker:** db_update_agent.py

When multiple sources disagree, apply confidence order from claude.md § Conflict Resolution:

```
Official manufacturer (1.00) > OEM cross-ref (0.90) >
PARTS_BRANDS (0.85) > Marketplace API (0.65) > Scraped (0.50)
```

Higher confidence always wins. All conflicts logged in audit trail.

### Layer 17 — Audit/Versioning

**Workers:** auto_backup.py + resilience.py + agents/memory.py

- `auto_backup.py` → pg_dump every 24h, retention: 7 daily + 4 weekly + 3 monthly
- `job_registry_start/finish` → every job logged in job registry
- `AgentMemory.append_event` → agent action audit trail
- `agent_memory_usage_logs` table → memory usage tracking

**Exit Criteria:** 0 mismatched manufacturer_id. 0 Hebrew+Latin concatenation. <1% invalid rows.

---

## PHASE 3 — Vehicle Intelligence

**Goal:** Build vehicle hierarchy, segment by platform and engine family
**Workers:** run_rex_transport_office_pipeline.py + run_step4_worker_cycle.py + db_update_agent.py

### Layer 4 — Vehicle Platform Segmentation

**Worker:** run_rex_transport_office_pipeline.py (SEE skills.md § this worker)

- Syncs 36,831+ Israeli registered vehicles from data.gov.il
- `sync_market_priority_to_db` → update vehicle_market_il priorities
- `_norm_key` → normalize vehicle keys for cross-matching
- Output table: `vehicle_market_il`

### Layer 5 — Engine/Family Segmentation

**Worker:** run_step4_worker_cycle.py (steps 1-2) + db_update_agent.py

- `sync_models_from_catalog(db)` → extract manufacturer+model+year from catalog
- `sync_models_from_catalog_file(db)` → extract from parts_database.xlsx workbook
  - Reads manufacturer → model → sub-model → year_from/year_to hierarchy
  - Writes to `vehicle_hierarchy_xls` table

### Layer 19 — Cross-Market Localization (Israeli)

**Worker:** run_rex_transport_office_pipeline.py + db_update_agent.fill_car_brands

- `vehicle_market_il` → Israeli registry with WLTP data
- `il_importer` field → Israeli importer per brand
- `name_he` field → Hebrew part names
- `customs_tariff_code` field → Israeli customs codes
- ILS as primary currency (SEE claude.md § Business Rules)
- UI display: SEE ui-ux.md § Price Display

**Exit Criteria:** vehicle_market_il updated. vehicle_hierarchy_xls populated.

---

## PHASE 4 — Fitment & Classification

**Goal:** Build part↔vehicle graph, classify all parts by type and origin
**Workers:** run_step4_worker_cycle.py + db_update_agent.py + db_cleanup_agent.py

### Layer 6 — Fitment Graph Engine

**Worker:** run_step4_worker_cycle.py (steps 3-4) (SEE skills.md § run_step4_worker_cycle.py)
**Retry:** 3 attempts, 1.5s×attempt backoff on deadlock

Sequential tasks:

1. `sync_models_from_catalog` → initial model sync
2. `sync_models_from_catalog_file` → XLS-based sync
3. `backfill_catalog_fitment_from_xls(db)` → build fitment rows
4. `merge_catalog_fitment_from_part_vehicle_fitment(db)` → merge into canonical table

Target table: `part_vehicle_fitment`
Unique index: `(part_id, manufacturer, model, year_from)`

NIR agent reads this table via FITMENT_CHECK skill (SEE skills.md § NIR)

### Layer 7 — Part Reference Resolution

**Worker:** db_cleanup_agent.py task2 + db_update_agent.py

- `task2` → fill_oem_from_crossref (fill missing OEM from part_cross_reference)
- OEM normalization via `_normalize_oem_candidate`
- Supersession chain via `superseded_by_sku` field
- NIR agent queries via OEM_LOOKUP + CROSS_REFERENCE skills

### Layer 8 — OEM/OES/Aftermarket Classification

**Workers:** catalog_scraper.py + aliexpress_supplier.py + db_update_agent.py

- `classify_part_type(brand, part_name, source)` → catalog_scraper
- `classify_part_origin(title)` → aliexpress_supplier (uses OE_BRANDS + OEM_KEYWORDS)
- Uses `PARTS_BRANDS` from manufacturer_normalization.py

Tags stored in:

- `part_condition` field: `original` | `oe_equivalent` | `aftermarket`
- `aftermarket_tier` field: `OEM` | `OE Equivalent` | `Economy`

Displayed by NIR (CLASSIFY_ORIGIN skill) and MAYA (price tiers).
UI display: SEE ui-ux.md § Part Origin Badges

### Layer 9 — Interchange Engine

**Worker:** db_update_agent.py

- `part_cross_reference` table → OEM/aftermarket cross-references
- `superseded_by_sku` → supersession chains
- `_trigger_scraper_for_registry_gaps_task` → fill gaps

NIR queries via CROSS_REFERENCE skill.

### Layer 10 — Category Ontology

**Worker:** db_cleanup_agent.py task3 + db_update_agent.py
**Source of truth:** categories.py → CATEGORY_MAP

28 Categories:
בלמים | מתלה | היגוי | מנוע | קירור | מערכת דלק | מערכת אוויר | טורבו | פליטה |
תיבת הילוכים וציר | מצמד | רצועות תזמון | הצתה | סינון | חשמל ואלקטרוניקה |
חיישנים | מצבר | תאורה | מזגן וחימום | גוף הרכב | שמשות ומגבים | פנים הרכב |
גלגלים וצמיגים | אטמים וצינורות | מערכת בטיחות | מערכת היברידית וחשמלי |
שמנים ונוזלים | כלי עבודה ואביזרים

- `task3` → keyword-based assignment, random sampling
- `normalize_categories(db)` → sync all from CATEGORY_MAP
- Never hardcode categories — always import from categories.py

Displayed as category grid in UI: SEE ui-ux.md § Top Categories Grid

**Exit Criteria:** 100% active parts have category. 100% have part_origin. Fitment table populated.

---

## PHASE 5 — Publishing & Search

**Goal:** Enrich, promote, and index parts for customer-facing search
**Workers:** ai_catalog_builder.py + meili_sync.py + db_update_agent.py

### Layer 15 — Compatibility Scoring

**Worker:** run_step4_worker_cycle.py (merge step)

- Confidence score generated during fitment merge
- Reflects certainty of part↔vehicle match
- Used by NIR COMPATIBILITY_SCORE skill

### Layer 16 — Catalog Promotion Pipeline

**Worker:** ai_catalog_builder.py (SEE skills.md § ai_catalog_builder.py)
**Triggered by:** db_update_agent._enrich_pending_parts_task + SHIRA.SEO_ENRICH

Promotion stages:

```
staged → normalized → classified → enriched → indexed → LIVE
```

- `enrich_pending_parts(db, limit=100)` → GPT-4o enrichment per brand
- `build_new_prompt(brand)` → generate new parts
- `build_expand_prompt(brand, existing, need)` → expand catalog
- `insert_parts(conn, supplier_id, brand, parts)` → write to catalog
- `master_enriched = TRUE` → set after full enrichment cycle

### Layer 14 — Search Indexing

**Workers:** meili_sync.py + catalog_scraper._meili_sync_part + db_update_agent

- `meili_sync.run(dry_run=False)` → full index sync
- `_meili_sync_part(part_id, doc)` → per-part sync on update
- `search_vector` (tsvector) → PostgreSQL full-text search
- `_generate_image_embeddings_task(db)` → HuggingFace CLIP via hf_client.hf_clip
- Meilisearch container: autospare_meilisearch

NIR queries Meilisearch via PART_SEARCH skill.
UI search bar: SEE ui-ux.md § Search Experience

**Exit Criteria:** All published parts indexed. search_vector populated. master_enriched=TRUE.

---

## PHASE 6 — Pricing & Market

**Goal:** Fetch live prices from all suppliers, serve best price to customer
**Workers:** ebay_price_sync.py + aliexpress_price_sync.py + supplier_aggregator.py
**UI output:** SEE ui-ux.md § Supplier Price Comparison Table + Price Display

### Layer 18 — Supplier Confidence Ranking

**Worker:** supplier_aggregator.py (SEE skills.md § supplier_aggregator)

- Supplier scored by: price accuracy, shipping reliability, API stability
- Fan out to all enabled suppliers in parallel
- `_credential_gate` → enable supplier only if env vars present
- BOAZ agent reads confidence via SUPPLIER_RANK skill

### Layer 20 — Pricing/Distributor Layer

**Workers:** ebay_price_sync.py + aliexpress_price_sync.py

eBay (`sync_ebay_prices`, limit 4,400 calls/day, 0.2s delay):

- Search by OEM number, filter ships_to_israel=TRUE
- Convert USD → ILS via currency_rate.py
- Upsert into `supplier_parts`
- Write `price_history` on change
- Update `min_price_ils` / `max_price_ils` in parts_catalog
- Push to Meilisearch (L14)
- Log every call to `scraper_api_calls`

AliExpress (`sync_aliexpress_prices`):

- Search by OEM/keyword, ship_to_country=IL
- `classify_part_origin(title)` → OE_BRANDS + OEM_KEYWORDS → store in part_condition
- Convert to ILS
- Write to supplier_parts

Price tiers (SEE ui-ux.md § Price Display, skills.md § MAYA):

```
Tier 1 Original:     importer_price_ils
Tier 2 OE Equiv:     supplier_parts WHERE aftermarket_tier = 'oe_equivalent'
Tier 3 Economy:      supplier_parts WHERE aftermarket_tier = 'economy'
Best Price:          MIN(all tiers, ships_to_israel=TRUE, is_available=TRUE)
```

**Exit Criteria:** min_price_ils >90% active parts. Price history updated within 24h.

---

## Full Layer → Worker → Agent Map

| Layer | Name                      | Worker (skills.md)             | Customer Agent (skills.md) |
| ----- | ------------------------- | ------------------------------ | -------------------------- |
| L1    | Source Ingestion          | catalog_scraper                | REX (TRIGGER_SCRAPE)       |
| L2    | Normalization             | db_cleanup + db_update         | —                         |
| L3    | Manufacturer Intelligence | db_cleanup + db_update         | —                         |
| L4    | Vehicle Platform          | transport_pipeline             | —                         |
| L5    | Engine/Family             | step4_worker + db_update       | —                         |
| L6    | Fitment Graph             | step4_worker                   | NIR (FITMENT_CHECK)        |
| L7    | Part Reference            | db_cleanup task2               | NIR (OEM_LOOKUP)           |
| L8    | OEM/OES/Aftermarket       | catalog_scraper + aliexpress   | NIR (CLASSIFY_ORIGIN)      |
| L9    | Interchange               | db_update                      | NIR (CROSS_REFERENCE)      |
| L10   | Category Ontology         | db_cleanup task3               | NIR (PART_SEARCH filter)   |
| L11   | Validation                | db_update                      | —                         |
| L12   | Conflict Resolution       | db_update                      | —                         |
| L13   | Import Orchestration      | catalog_scraper (REX)          | REX (SCRAPE_STATUS)        |
| L14   | Search Indexing           | meili_sync + db_update         | NIR (PART_SEARCH)          |
| L15   | Compatibility Scoring     | step4_worker                   | NIR (COMPATIBILITY_SCORE)  |
| L16   | Catalog Promotion         | ai_catalog_builder             | SHIRA (SEO_ENRICH)         |
| L17   | Audit/Versioning          | auto_backup + resilience       | —                         |
| L18   | Supplier Confidence       | supplier_aggregator            | BOAZ (SUPPLIER_RANK)       |
| L19   | Localization              | transport_pipeline + db_update | TAL (CURRENCY_CONVERT)     |
| L20   | Pricing                   | ebay_sync + aliexpress_sync    | MAYA (PRICE_COMPARE)       |