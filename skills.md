# AutoSpareFinder — Agent Skills Catalog

## Navigation

- Core rules all agents must follow → READ: claude.md
- Which pipeline phase each skill feeds into → READ: phases.md
- UI standards for customer-facing skills → READ: ui-ux.md

---

# LAYER A — AI Customer Agents (BACKEND_AI_AGENTS.py)

Rules that apply to ALL Layer A agents (SEE claude.md § Data Rules):

- Never expose supplier cost or margins
- Never allow ordering before payment
- VAT 18% on all Israeli transactions
- Always respond in the user's language

---

## AVI — Router & Orchestrator

**Pipeline role:** None directly. Coordinates Layer A agents.
**Receives:** All incoming user requests
**Delegates to:** NIR, MAYA, LIOR, TAL, DANA, OREN, SHIRA, BOAZ, NOA, REX

**Skills:**

- `ROUTE_TO_AGENT(intent)` — Identify intent → delegate to correct agent
- `CONTEXT_MANAGE` — Maintain conversation context across handoffs
- `ESCALATE` — Detect when request exceeds agent capability
- `MEMORY_READ` — Read shared AgentMemory for context (agents/memory.py)
- `TODO_CHECK` — Read active todos via agent_todo_utils.py at start of session

**Routing rules:**

```
part search / fitment / OEM   → NIR
pricing / promotions          → MAYA
order create / status         → LIOR
invoice / VAT / payment       → TAL
return / warranty / complaint → DANA
auth / fraud / security       → OREN
social / campaign             → SHIRA + NOA
supplier / B2B / quote        → BOAZ
scrape / data / catalog       → REX
```

---

## NIR — Parts Specialist

**Pipeline role:** Consumes output of Phases 1–5 (SEE phases.md)
**UI surfaces:** Search results, part detail page, compatibility checker (SEE ui-ux.md § Search Experience)

**Skills:**

- `PART_SEARCH(query)` — Search by name, OEM, SKU, VIN → queries parts_catalog via Meilisearch (phases.md L14)
- `FITMENT_CHECK(part_id, vehicle)` — Verify fitment via part_vehicle_fitment (phases.md L6)
- `OEM_LOOKUP(part_id)` — Return OEM number from part_cross_reference (phases.md L7)
- `CROSS_REFERENCE(oem)` — Find equivalent parts across brands (phases.md L9)
- `CLASSIFY_ORIGIN(part)` — Return original/oe_equivalent/aftermarket tag (phases.md L8)
- `COMPATIBILITY_SCORE(part, vehicle)` — Return 0.0–1.0 fit score (phases.md L15)
- `VIN_DECODE(vin)` — Decode VIN via NHTSA + Israeli transport office

**Data access:** parts_catalog, part_vehicle_fitment, part_cross_reference, vehicle_market_il, car_brands

---

## MAYA — Sales

**Pipeline role:** Consumes Phase 6 pricing output (SEE phases.md § Phase 6)
**UI surfaces:** Price comparison table, best price badge (SEE ui-ux.md § Price Display)

**Skills:**

- `PRICE_COMPARE(part_id)` — Show all tiers: OEM / OE Equivalent / Economy from supplier_parts
- `BEST_PRICE(part_id)` — Return MIN price that ships to Israel (phases.md L20)
- `UPSELL(part_id)` — Suggest related/complementary parts
- `APPLY_PROMO(code)` — Apply discount codes

**Price tier logic (SEE phases.md § Phase 6 Layer 20):**

```
Tier 1 Original:     importer_price_ils
Tier 2 OE Equiv:     supplier_parts WHERE aftermarket_tier = 'oe_equivalent'
Tier 3 Economy:      supplier_parts WHERE aftermarket_tier = 'economy'
Best:                MIN(all tiers with stock AND ships_to_israel = TRUE)
```

**Rules:** Never reveal supplier cost. Show customer price in ILS only.

---

## LIOR — Orders

**Pipeline role:** Reads Phase 6 stock data
**UI surfaces:** Cart, checkout, order status (SEE ui-ux.md § Supplier Price Comparison Table)

**Skills:**

- `CREATE_ORDER` — Create order after payment confirmed (never before)
- `ORDER_STATUS(order_id)` — Check status
- `MODIFY_ORDER(order_id)` — Modify before fulfillment
- `CANCEL_ORDER(order_id)` — Cancel with refund eligibility check

**Rules:** Payment must be confirmed before CREATE_ORDER. Always verify stock first.

---

## TAL — Finance

**Pipeline role:** Reads pricing from Phase 6 (SEE phases.md § Layer 20)

**Skills:**

- `GENERATE_INVOICE(order_id)` — Create invoice with 18% VAT (Israel)
- `PAYMENT_STATUS(order_id)` — Check payment confirmation
- `REFUND_PROCESS(order_id)` — Initiate refund
- `CURRENCY_CONVERT(amount, from, to)` — USD/EUR → ILS via currency_rate.py

**Rules:** VAT 18% mandatory on all Israeli transactions. ILS is always primary currency.

---

## DANA — Support

**UI surfaces:** Support chat, returns flow (SEE ui-ux.md)

**Skills:**

- `RETURN_REQUEST(order_id)` — Open return, check 30-day window
- `WARRANTY_CHECK(part_id, supplier)` — Verify coverage from supplier_parts.warranty_months
- `COMPLAINT_LOG` — Log complaint with priority
- `ESCALATE_HUMAN` — Route to human when needed

---

## OREN — Security

**Skills:**

- `AUTH_VERIFY` — Verify user identity and session
- `FRAUD_FLAG` — Flag suspicious orders or payment patterns
- `RATE_LIMIT_CHECK` — Detect API abuse
- `PERM_CHECK` — Verify user permissions

**Rules:** Never expose security logic. Never confirm or deny specific fraud rules.

---

## SHIRA — Marketing

**Pipeline role:** Uses enriched parts from Phase 5 (SEE phases.md § Layer 16)
**UI surfaces:** Campaigns, SEO content

**Skills:**

- `SOCIAL_POST(part_id)` — Draft social post for new part or promotion
- `CAMPAIGN_CREATE` — Create marketing campaign
- `SEO_ENRICH(part_id)` — Generate SEO description using ai_catalog_builder output

**Integration:** Works with NOA for Telegram posts. Approval via NOA → webhooks.py callback flow.

---

## BOAZ — Supplier

**Pipeline role:** Feeds into Phase 6 supplier data (SEE phases.md § Layer 18)

**Skills:**

- `SUPPLIER_QUOTE(part_id)` — Request quote via API or form
- `SUPPLIER_STATUS(supplier_id)` — Check availability and lead time
- `BULK_ORDER` — Handle B2B bulk requests
- `SUPPLIER_RANK` — Show supplier confidence score (phases.md L18)

---

## NOA — Social Media

**Skills:**

- `TELEGRAM_POST(content)` — Send to Telegram channel/group
- `CONTENT_GENERATE(part_id)` — Generate part feature content
- `APPROVAL_FLOW(content)` — Send to admin Telegram bot → approve/reject via webhooks.py callback_query

---

## REX — Scraper Coordinator

**Pipeline role:** Triggers Phase 1 (SEE phases.md § Phase 1)
**Schedule:** 00:00 + 12:00 UTC (SEE claude.md § Scheduled Jobs)

**Skills:**

- `TRIGGER_SCRAPE(supplier)` — Start scraping job for specific supplier
- `SCRAPE_STATUS(job_id)` — Check running job status via job_registry
- `BRAND_DISCOVERY` — Trigger run_brand_discovery in catalog_scraper.py
- `TRANSPORT_SYNC` — Trigger run_rex_transport_office_pipeline.py
- `CATALOG_IMPORT(brand)` — Trigger full catalog import cycle for a brand (SEE IMPORT SKILL below)

### Approved Fitment & OEM Data Sources

> **Never rely on a single source.** REX must query sources in priority order and stop at the first confirmed match.
> TecDoc is NOT a free/public API — do not call it unless credentials are configured in env vars.

#### Tier 1 — Official Manufacturer Global Sites (confidence 1.00)
Query these first for OEM part numbers, fitment, and supersession chains.
All are scrapeable or have public EPC portals — no API key required.

| Manufacturer | Global Parts Portal | Notes |
|---|---|---|
| **Toyota / Lexus** | `https://www.toyota-tech.eu` / `https://parts.toyota.com` | Full EPC with model+year+engine fitment |
| **BMW / MINI** | `https://www.realoem.com` | Free EPC mirror — model, year, engine, OEM number |
| **Mercedes-Benz** | `https://www.mercedes-benz-parts.com` / `https://epc.mercedes-benz.com` | Full OEM catalog |
| **Volkswagen Group** (VW, Audi, Skoda, SEAT, Cupra, Porsche) | `https://www.erwin.volkswagen.de` / `https://eshop.audi.com` | ERWIN workshop portal; Audi e-shop for OEM numbers |
| **Kia** | `https://www.kiaparts.com` / `https://parts.kia.com` | Global OEM catalog |
| **Hyundai** | `https://www.hyundaiparts.com` | OEM parts + fitment |
| **Ford** | `https://www.fordparts.com` / `https://www.motorcraftservice.com` | Global OEM + Motorcraft aftermarket |
| **Mazda** | `https://parts.mazda.com` | OEM parts by model/year |
| **Subaru** | `https://parts.subaru.com` | OEM parts + fitment |
| **Mitsubishi** | `https://www.mitsubishicars.com/genuine-parts` / `https://partsfinder.mitsubishimotors.com` | OEM lookup |
| **Nissan / Infiniti** | `https://www.nissanparts.com` / `https://www.nparts.com` | OEM catalog with VIN lookup |
| **Jeep / Fiat / Alfa Romeo / RAM** (Stellantis) | `https://mopar.com/en-us/parts` / `https://www.mopar.com` | Mopar OEM parts portal |
| **Land Rover / Jaguar** | `https://www.jaguarlandrover.com/parts` / `https://eTIS.jlrext.com` | JLR eTIS parts catalog (requires free registration) |
| **Volvo** | `https://epc.volvocars.com` | Free EPC with VIN/model/year |
| **Renault** | `https://renault-parts.com` / `https://rparts.renault.com` | OEM parts by model |
| **Peugeot / Citroën / DS / Opel** (Stellantis EU) | `https://ecat.peugeot.com` / `https://ecat.citroen.com` | eCat portals |
| **Honda** | `https://www.hondaautomotiveparts.com` / `https://epc.honda.com` | OEM EPC |
| **BYD** | `https://www.bydauto.co.il` (IL) / `https://parts.byd.com` (global) | IL importer + global parts |
| **Zeekr / Geely** | `https://www.zeekr.eu/parts` / `https://parts.geely.com` | EV OEM parts |
| **Chery / Omoda / Jaecoo** | `https://www.chery.cn/parts` | Chinese OEM — scrape with translation |

#### Tier 2 — Israeli & Regional Aggregators (confidence 0.90)

| Priority | Source | URL | What it provides |
|----------|--------|-----|-----------------|
| 1 | **samelet.com API** | `https://www.samelet.co.il` | OEM cross-refs, fitment, all major IL brands — best for IL-market parts |
| 2 | **PartSouq** | `https://www.partsouq.com` | MENA OEM lookup — strong on Toyota/Nissan/Mitsubishi/Kia |
| 3 | **autodoc.co.il / autodoc.co.uk** | `https://www.autodoc.co.uk` | OEM + aftermarket fitment by ktype — ⚠️ Cloudflare-limited, use with backoff |

#### Tier 3 — Global Aftermarket & Marketplace (confidence 0.65)

| Priority | Source | URL | What it provides |
|----------|--------|-----|-----------------|
| 1 | **eBay Motors fitment API** | `https://api.ebay.com/buy/browse/v1` | compatibility_properties per listing — always log to scraper_api_calls |
| 2 | **7zap.com** | `https://www.7zap.com` | Scrapeable EPC mirror — fitment from VIN/model tables |
| 3 | **motorstore.com** | `https://www.motorstore.com` | UK aftermarket — OEM cross-ref + European fitment |
| 4 | **AliExpress** | `https://www.aliexpress.com` | Price + availability — fitment from listing title/description only |

#### Tier 4 — Industry Database (requires credentials)

| Source | Env var required | Notes |
|---|---|---|
| **TecDoc API** | `TECDOC_API_KEY` | Full fitment DB — skip silently if key not set. Paid license required. |

**Source selection rules:**
- **Always start with Tier 1 manufacturer global site** for OEM number and fitment confirmation
- For Israeli brands (Kia, Mazda, Subaru, Alfa, Jeep, Fiat, RAM): follow with samelet.com (Tier 2)
- For BMW/MINI: RealOEM.com (already in Tier 1)
- For Land Rover/Jaguar: JLR eTIS + SNG Barratt catalog
- For VW Group: Audi eshop / ERWIN + Champion Motors export + autodoc
- For eBay: always record API call in `scraper_api_calls` table (rate limit: 5,000 calls/day)
- All scraped Tier 3 data: confidence 0.50 — never overwrite Tier 1/2 data with Tier 3 data
- Check `TECDOC_API_KEY` in env before attempting TecDoc — skip silently if not set

---

## IMPORT SKILL — Full Catalog Import Cycle

> **Used by:** REX (trigger), db_update_agent.py (execution)
> **Applies to:** ALL import scripts — samelet, champion, zeekr, kia, delek, SNG Barratt, BYD, supplier PDFs
> **Mandatory post-import flow:** Raw import → Layer 1 (db_cleanup_agent) → Layer 2 (db_update_agent) → Layer 3 (ai_catalog_builder) → Catalog DB confirmed → Meilisearch sync
> **Data is NOT catalog-ready until master_enriched=TRUE and it has passed all 3 layers**

### Step 0 — Pre-flight checklist (run before every import)
- [ ] Verify source URL / API token is live
- [ ] Confirm brand exists in `car_brands` table; insert if missing
- [ ] Assign brand prefix (e.g. `JP-`, `AR-`, `SU-`) — prevents OEM number collisions between brands
- [ ] Record expected row count from source so post-import parity can be verified
- [ ] Acquire distributed lock (`distributed_lock.py`) for this brand before writing

### Step 1 — Data ingestion (what to pull)
Pull every available field from the source:

| Field | DB column | Notes |
|-------|-----------|-------|
| Part number / OEM | `oem_number` | Raw source key, never strip |
| Brand-prefixed SKU | `sku` (UNIQUE) | `PREFIX-{oem_number.lstrip('0') or oem_number}` |
| English name | `name` | Strip extra quotes / whitespace |
| Hebrew name | `name_he` | Keep original; use for Hebrew search |
| Price ex-VAT | `base_price` | Float ILS |
| Price inc-VAT | `importer_price_ils` | Float ILS (18% VAT) |
| Part type flag | `part_type` | `original` / `aftermarket` from source |
| Model / year data | `compatible_vehicles` (jsonb) | Build in Step 3 |

**Coverage strategy (samelet API):**
- Phase 1: All 2-char UPPER×ALPHANUM combos (26×62 = 1,612 searches) — main coverage
- Phase 2: 3-char expansion for any prefix that hits the 29-result API cap
- Phase 3: Hebrew single-char searches (27 chars)
- Phase 4: SKU numeric prefix searches (option=1, 0–99)
- Use `ON CONFLICT(sku) DO UPDATE` — safe to re-run; adds new, updates existing
- Log every 100 searches with unique-parts count and capped-prefixes count

### Step 2 — Name cleaning
- Strip leading/trailing whitespace and stray quotes from `name`
- If `name` is empty, fall back to `name_he`; if both empty, use SKU
- Normalize brand name via `manufacturer_normalization.py`
- `classify_part(name_en, name_he)` → assign `category` using `CATEGORY_KW` map (28 categories from `categories.py`)
- Never overwrite a manually-set category with auto-classification if confidence < existing

### Step 2b — Category assignment (connection to categories system)

**Source of truth:** `categories.py` → `CATEGORY_MAP` (28 canonical categories)

**How classification works:**
1. `classify_part(name_en, name_he)` scans both the English and Hebrew part name against `CATEGORY_KW` — an ordered keyword list where the **first match wins**
2. Keywords are tested against `(name_en + " " + name_he).lower()` — combined string, case-insensitive
3. If no keyword matches → category defaults to `"General Parts"`

**The 28 canonical categories (from `categories.py` CATEGORY_MAP):**

| Category | Example keywords matched |
|----------|------------------------|
| Filters | oil filter, air filter, fuel filter, cabin filter |
| Brakes | brake pad, brake disc, caliper, abs sensor |
| Engine | spark plug, gasket, timing belt, piston, valve, seal, pump, hose |
| Electronics | sensor, ecu, module |
| Safety | airbag, seat belt |
| Suspension | shock absorber, strut, spring, control arm, ball joint, bearing |
| Steering | rack, track rod, steering |
| Exhaust | exhaust, muffler, catalytic, dpf |
| Cooling | radiator, coolant, thermostat, fan |
| Transmission | gearbox, clutch |
| Drivetrain | axle, driveshaft, cv joint |
| Lighting | headlight, tail light, fog light, bulb, lamp |
| Body | mirror, bumper, bonnet, fender, windshield, wiper, panel, spoiler |
| Fuel System | fuel pump, injector, fuel rail |
| Electrical | battery, alternator, starter, fuse, relay, cable |
| Wheels & Tires | wheel, rim |
| Interior | seat, trim, dashboard |
| HVAC | compressor, air conditioning |
| General Parts | (fallback — no keyword matched) |

**DB column:** `parts_catalog.category` (text, not FK — free-form but must use canonical values)

**Category → Meilisearch filter:**
- `category` is a filterable attribute in Meilisearch (`meili_sync.py` index settings)
- Frontend uses `category` filter to power the category sidebar in the parts search UI
- Must use exact canonical spelling — mismatches silently break the filter

**Re-classification rule:**
- If a part's category is `"General Parts"` AND it has a Hebrew `name_he` → queue it for `ai_catalog_builder.py` enrichment (GPT-4o can classify from Hebrew name)
- `db_update_agent.py` → `categorize_by_keywords` task runs every 30s and re-classifies uncategorized parts using CATEGORY_MAP
- Never bulk-overwrite categories that are already set to a canonical value

**Post-import category audit:**
```sql
SELECT category, COUNT(*) 
FROM parts_catalog 
WHERE manufacturer = $1 AND is_active = TRUE 
GROUP BY category ORDER BY COUNT(*) DESC;
```
If `General Parts` > 60% of total → add todo for `db_update_agent` to re-classify

### Step 3 — Fitment wiring (models & years)
Run `samelet_fitment.py` (or equivalent) after import:
- Load `BRAND_MODELS` dict: brand → list of `(model_name, [keywords])`
- Match keywords against `LOWER(name)` and `LOWER(name_he)` **in Python** (not SQL interpolation — prevents Hebrew syntax errors)
- Write `compatible_vehicles` as JSON array: `[{"brand":"Jeep","model":"Wrangler","years":"2000-2026"}]`
- Parts with no model match → assign generic: `[{"brand":brand,"model":"All Models","years":"2000-2026"}]`
- **If >80% of parts get generic fitment** → flag brand in todo list for NIR (needs richer model keyword list)
- Write fitment to `part_vehicle_fitment` table as well for NIR's `FITMENT_CHECK` queries

### Step 4 — Meilisearch indexing
Run `meili_sync.py --manufacturer {brand} --no-rebuild`:
- For single-brand imports: scoped sync only (never full rebuild unless cross-brand destructive change)
- Index fields: `sku`, `name`, `name_he`, `category`, `manufacturer`, `base_price`, `oem_number`, `compatible_vehicles`
- Verify: DB count == Meilisearch document count for that manufacturer after sync
- If counts mismatch → log error and add todo for db_update_agent

### Step 5 — XLS export
Run `samelet_xls_export.py` (or equivalent):
- Output: `/app/uploads/{brand_slug}_parts_catalog.xlsx`
- Copy to host: `/opt/autosparefinder/{brand_slug}_parts_catalog.xlsx`
- Columns (fixed order): SKU | Name | Name (Hebrew) | Category | Base Price (ILS) | Importer Price (ILS) | Part Type | OEM Number | Compatible Vehicles | Updated At
- Header fill: `1F4E79`, frozen pane A2, auto-filter on all columns

### Step 6 — Quality gate & todo generation
After every import, compute and log these metrics:

```json
{
  "brand": "Subaru",
  "total_parts": 3912,
  "priced_parts": 3800,
  "named_parts": 3912,
  "generic_fitment_pct": 94.1,
  "categories_used": 12,
  "db_meili_parity": true
}
```

**Auto-generate agent todos if:**

| Condition | Todo assigned to | Priority |
|-----------|-----------------|---------|
| `priced_parts / total < 0.90` | MAYA | high |
| `named_parts / total < 1.00` | db_update_agent | medium |
| `generic_fitment_pct > 80%` | NIR (expand BRAND_MODELS keywords) | medium |
| `categories_used < 5` | db_update_agent (re-classify) | low |
| `db_meili_parity == false` | db_update_agent (resync) | high |
| `total_parts < expected_from_source` | REX (re-import, check coverage) | high |

### Step 7 — Post-import verification
- Run `SELECT COUNT(*) FROM parts_catalog WHERE manufacturer=$1 AND is_active=TRUE`
- Compare to source expected count
- If gap > 5%: log warning + add REX todo to re-run with wider search coverage
- Update `job_registry` with final result JSON (SEE claude.md § Standard Report Format)

---

## POST-IMPORT PIPELINE — Mandatory 3-Layer Flow

> **Every import script writes raw data into `parts_catalog` first.**
> That raw data is NOT catalog-ready until it has passed through all 3 pipeline layers below.
> The pipeline runs automatically on a schedule, but REX must queue todos to prioritize newly imported brands.

```
Import Script
     │
     ▼
parts_catalog (raw insert — is_active=TRUE, master_enriched=FALSE)
     │
     ▼
┌────────────────────────────────────────────────────────────────────┐
│  LAYER 1 — db_cleanup_agent.py  (runs every 30s, 340 rows/cycle)  │
│  • Normalize part types (original / oe_equivalent / aftermarket)  │
│  • Clean part names (strip junk, fix Hebrew prefix reversal)       │
│  • Assign category from CATEGORY_MAP keywords                      │
│  • Fix OEM lookup flag (needs_oem_lookup)                          │
│  • Detect + link manufacturer_id from car_brands                  │
│  • Extract spec prefixes into specifications JSONB                 │
└────────────────────────────────────────────────────────────────────┘
     │
     ▼
┌────────────────────────────────────────────────────────────────────┐
│  LAYER 2 — db_update_agent.py  (scheduled + REX-triggered)        │
│  • Canonicalize manufacturer name via manufacturer_normalization   │
│  • Recalculate min_price_ils / max_price_ils from supplier_parts  │
│  • Wire fitment: sync part_vehicle_fitment from catalog & XLS     │
│  • Deduplicate catalog rows (flag dupes is_active=FALSE)           │
│  • Validate prices, flag fake SKUs, normalize availability         │
│  • Trigger AI enrichment for parts where master_enriched=FALSE    │
└────────────────────────────────────────────────────────────────────┘
     │
     ▼
┌────────────────────────────────────────────────────────────────────┐
│  LAYER 3 — ai_catalog_builder.py  (GPT-4o, triggered by L2)       │
│  • Translate / generate name_he (Hebrew) if missing               │
│  • Write full description + category for "General Parts" parts     │
│  • Set master_enriched=TRUE when complete                          │
│  • Outputs: name, name_he, description, category, specifications  │
└────────────────────────────────────────────────────────────────────┘
     │
     ▼
parts_catalog (master_enriched=TRUE — data is now catalog-ready)
     │
     ▼
meili_sync.py --manufacturer {BRAND} --no-rebuild
     │
     ▼
Meilisearch index (searchable by users)
```

### Rules for import scripts regarding the pipeline:

1. **Never skip the pipeline** — a part is NOT ready for search until `master_enriched=TRUE`
2. **Always set `master_enriched=FALSE` on insert** — this is the gate that triggers Layer 3
3. **Always set `needs_oem_lookup` correctly** — Layer 1 uses this flag to queue OEM lookups
4. **Queue REX todos for missing fitment** — Layer 2 processes these todos (see claude.md § fitment)
5. **Run scoped Meilisearch sync after import** — but only AFTER Layer 1+2 have processed the batch:
   - For immediate search availability: run sync right after import (parts with `master_enriched=FALSE` will appear but with partial data)
   - For full-quality search: wait for pipeline to complete, then re-sync
6. **Do NOT run `meili_sync.py` with `--rebuild`** for a single brand import — scoped sync only
7. **Pipeline processes `is_active=TRUE` parts only** — deactivated parts are invisible to all layers

### How to trigger the pipeline for a newly imported brand:

```python
# After import completes — queue pipeline todos for REX/db_update_agent
await conn.execute("""
    INSERT INTO agent_todos(id, agent_name, title, description, priority, status, created_at, updated_at)
    VALUES
    (gen_random_uuid(), 'REX', $1, $2, 'high', 'not_started', NOW(), NOW()),
    (gen_random_uuid(), 'REX', $3, $4, 'high', 'not_started', NOW(), NOW())
""",
    f'Run db_cleanup cycle for {brand}',
    f'Newly imported {count} {brand} parts need normalization + category assignment. Run db_cleanup_agent tasks 1-8.',
    f'Run db_update enrichment for {brand}',
    f'Newly imported {brand} parts: recalculate prices, wire fitment, trigger AI enrichment.'
)
```

---

# LAYER B — Data Pipeline Workers

All workers must follow rules in claude.md § Data Rules.
Each worker maps to specific pipeline layers in phases.md.

---

## catalog_scraper.py

**Owns layers:** L1 (Source Ingestion), L8 (Classification), L13 (Orchestration)
**SEE phases.md § Phase 1 + Phase 4 Layer 8**

**Functions:**

- `scrape_motorstore(part_number, manufacturer)` → L1
- `scrape_meyle / scrape_gates / scrape_brembo` → L1
- `scrape_autodoc(...)` → L1 (Cloudflare limited)
- `scrape_ebay_motors(...)` → L1
- `scrape_aliexpress(query, max_results)` → L1
- `scrape_google_shopping(query)` → L1
- `scrape_rockauto(...)` → L1 (blocked from Hetzner)
- `classify_part_type(brand, part_name, source)` → L8
- `refresh_and_persist_ils_exchange_rate()` → feeds L20
- `_meili_sync_part(part_id, doc)` → L14

**Constraints:** Log all API calls to scraper_api_calls. Respect rate limits per supplier.

---

## db_cleanup_agent.py

**Owns layers:** L2 (Normalization), L11 (Validation)
**SEE phases.md § Phase 2**
**Cycle:** 340 rows / 30s / random sampling

**Tasks:**

- `task1` — fix_part_types → L2
- `task2` — fill_oem_from_crossref → L7
- `task3` — categorize_by_keywords (CATEGORY_MAP) → L10
- `task4` — fix_oem_lookup_flag → L11
- `task5` — detect_manufacturer_overflow → L3
- `task6` — clean_part_names (_apply_task6_rules, _reverse_latin_prefix) → L2
- `task7` — link_manufacturers (text → manufacturer_id) → L3
- `task8` — extract_spec_prefixes (name → specifications JSONB) → L2

---

## db_update_agent.py (~3,600 lines)

**Owns layers:** L2, L3, L5, L6, L11, L12, L16, L17, L20
**SEE phases.md for full layer descriptions**

**By layer:**

L2 Normalization:

- `clean_part_names(db)`, `normalize_part_types(db)`, `normalize_categories(db)`
- `normalize_availability(db)`, `_apply_task6_rules(name)`, `_reverse_latin_prefix(name)`
- `_strip_trailing_vehicle_suffix(value)`, `_normalize_part_name_punctuation(value)`

L3 Manufacturer:

- `normalize_imported_manufacturers(db)` — uses manufacturer_normalization.py
- `fill_car_brands(db)` — seeds il_importer + warranty data
- `sync_manufacturer_registries(db)`, `fix_manufacturer_overflow(db)`

L5/L6 Vehicle & Fitment:

- `sync_models_from_catalog(db)`, `sync_models_from_catalog_file(db)`
- `backfill_catalog_fitment_from_xls(db)`, `merge_catalog_fitment_from_part_vehicle_fitment(db)`
- `ensure_part_vehicle_fitment_table(db)`

L11 Validation:

- `fix_base_prices(db)`, `flag_fake_skus(db)`, `dedup_catalog_parts(db)`

L16 Promotion:

- `_enrich_pending_parts_task(db)` — calls ai_catalog_builder.enrich_pending_parts
- `_trigger_scraper_for_misses_task(db)`, `_trigger_scraper_for_registry_gaps_task(db)`
- `_generate_image_embeddings_task(db)` — HuggingFace CLIP via hf_client.hf_clip

L20 Pricing:

- `refresh_min_max_prices(db)` — recalculates from supplier_parts

---

## ai_catalog_builder.py

**Owns layer:** L16 (Catalog Promotion Pipeline)
**SEE phases.md § Phase 5 Layer 16**
**Triggered by:** db_update_agent._enrich_pending_parts_task + SHIRA via SEO_ENRICH

**Functions:**

- `enrich_pending_parts(db, limit=100)` — GPT-4o enrichment, sets master_enriched=TRUE
- `ask_gpt4o(brand, prompt)` — AI API call
- `build_new_prompt(brand)` — Generate new parts prompt
- `build_expand_prompt(brand, existing, need)` — Expand catalog prompt
- `insert_parts(conn, supplier_id, brand, parts)` — Write enriched parts to catalog
- `run(mode_new, mode_expand, specific_brands, dry_run)` — Full run

---

## run_step4_worker_cycle.py

**Owns layers:** L5 (Engine/Family), L6 (Fitment Graph), L15 (Compatibility)
**SEE phases.md § Phase 3 + Phase 4**
**Retry:** 3 attempts, 1.5s×attempt backoff on deadlock

**Sequential tasks:**

1. `sync_models_from_catalog` → L5
2. `sync_models_from_catalog_file` → L5
3. `backfill_catalog_fitment_from_xls` → L6
4. `merge_catalog_fitment_from_part_vehicle_fitment` → L6 + L15

---

## run_rex_transport_office_pipeline.py

**Owns layers:** L4 (Vehicle Platform), L19 (Localization)
**SEE phases.md § Phase 3**
**Trigger logic:** DAILY if new_records_7d ≥ 1000 OR manufacturer_changes_7d ≥ 5, else WEEKLY

**Functions:**

- `sync_market_priority_to_db` → updates vehicle_market_il
- `_norm_key` → normalize vehicle keys for matching

---

## services/ebay_price_sync.py

**Owns layers:** L18 (Supplier Confidence), L20 (Pricing)
**SEE phases.md § Phase 6**
**Limit:** 4,400 API calls/day | 0.2s delay between calls

**Functions:**

- `sync_ebay_prices(db, limit_per_run=500)` — main sync loop
- `_record_ebay_api_call(...)` — audit every call to scraper_api_calls
- `_update_catalog_metadata(...)` — update min/max_price_ils in parts_catalog
- `_upsert_part_images(...)` — save images from eBay to parts_images
- `_meili_sync_part(doc)` — push to Meilisearch after price update (SEE phases.md L14)

---

## meili_sync.py

**Owns layer:** L14 (Search Indexing)
**SEE phases.md § Phase 5 Layer 14**

**Functions:**

- `run(dry_run=False)` — full index sync of all active parts

---

## auto_backup.py

**Owns layer:** L17 (Audit/Versioning — backup component)
**SEE phases.md § Phase 2 Layer 17**
**Schedule:** Every 24h
**Retention:** 7 daily + 4 weekly + 3 monthly
**Targets:** autospare (catalog DB) + autospare_pii (user DB)