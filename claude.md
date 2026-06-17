# AutoSpareFinder — Agent Core Rules
# READ THIS FIRST before reading any other file.

## Agent Task Execution Rules

- **Do tasks fully.** When the user assigns a task, complete it entirely — do not do partial work and stop.
- **Ask before assuming.** If a task is unclear or ambiguous, ask the user for clarification before starting.
- **Always use a todo list.** For every task, split it into a numbered checklist first, then work through each item in order. Do not skip or miss any item unless the user explicitly confirms skipping.
- **Verify completion.** At the end of every task, review the full todo list and confirm that every item is done. Report any item that was not completed and why.

## Web Scraping Rules — ALWAYS USE THE BROWSER TOOL

- **NEVER use the server IP to fetch external websites.** The server IP (207.180.217.129) is blocked by Cloudflare and many anti-bot systems. Direct requests via `urllib`, `requests`, `httpx`, or `aiohttp` to external sites WILL be blocked.
- **ALWAYS use the browser tool (Playwright / `run_playwright_code`)** for any extraction from external websites (oempartsonline.com, toyota.co.il, samelet.com, eBay, AliExpress, etc.).
- **Two-step import pattern** (mandatory for all external data):
  1. **Extract**: Use the browser tool to scrape and save data to a JSON file on disk.
  2. **Import**: Run a Python script inside the container to read the JSON and write to PostgreSQL.
- **Internal calls only** (localhost, inter-container) may use `urllib`/`requests`/`httpx` — these are not affected.
- **New import scripts** must be written with this split: a browser-based extractor and a separate DB importer. Never combine external HTTP fetch + DB write in one server-side script.

---

## Navigation
- What each agent can do → READ: skills.md
- How the pipeline works → READ: phases.md
- How the UI must look → READ: ui-ux.md

---

## System Overview
Platform: Israeli auto parts marketplace
Stack: FastAPI + PostgreSQL (catalog + PII) + Redis + Meilisearch + Docker Compose
Server: vmi3190597 207.180.217.129

---

## The Two Agent Layers

### Layer A — AI Customer Agents (BACKEND_AI_AGENTS.py)
11 named agents. Each has a defined role and skill set.
→ Full skills and responsibilities for each agent: SEE skills.md § LAYER A

| Agent | Role |
|-------|------|
| AVI | Router & orchestrator — routes ALL requests |
| NIR | Parts specialist — search, fitment, OEM |
| MAYA | Sales — pricing, promotions |
| LIOR | Orders — create, modify, cancel |
| TAL | Finance — invoices, VAT, payments |
| DANA | Support — returns, warranty, complaints |
| OREN | Security — auth, fraud detection |
| SHIRA | Marketing — social content, campaigns |
| BOAZ | Supplier — B2B, quotes |
| NOA | Social media — Telegram, content |
| REX | Scraper coordinator — triggers ingestion |

### Layer B — Data Pipeline Workers
Background processes that build and maintain the catalog.
→ Full function mapping per pipeline layer: SEE phases.md
→ Full skill set per worker: SEE skills.md § LAYER B

| Worker | Primary Responsibility |
|--------|----------------------|
| catalog_scraper.py | Source ingestion (L1, L13) |
| db_cleanup_agent.py | Normalization, validation (L2, L11) |
| db_update_agent.py | Core data pipeline, 30+ functions (L2–L12, L20) |
| ai_catalog_builder.py | GPT-4o enrichment (L16) |
| meili_sync.py | Search indexing (L14) |
| run_rex_transport_office_pipeline.py | Vehicle registry (L4, L19) |
| run_step4_worker_cycle.py | Fitment graph (L5, L6, L15) |
| services/ebay_price_sync.py | eBay pricing (L18, L20) |
| services/aliexpress_price_sync.py | AliExpress pricing (L20) |
| auto_backup.py | Backup every 24h (L17) |

---

## Shared Infrastructure

### Memory System (agents/memory.py)
Three layers in priority order:
1. In-process — ephemeral, fastest, lost on restart
2. Redis — short-term, survives restarts, expires
3. PostgreSQL — persistent, survives all failures

**Key namespacing:**
- Agent-scoped: plain key e.g. `"post_history"` — private to that agent
- Cross-agent shared: `"shared:{key}"` — writable by workers, readable by all agents

**API:**
```python
mem = AgentMemory(db, agent_name="my_worker")
await mem.set(key, value, ttl_hours=N)          # agent-scoped
await mem.get(key)
await mem.set_shared(key, value, ttl_hours=N)   # cross-agent shared
await mem.get_shared(key)
await mem.write_worker_heartbeat(stats_dict)    # workers: call every cycle
ctx = await mem.get_system_context()            # agents: read live system state
await mem.append_event("post_history", event)   # audit trail (max 50 items)
```

**Shared key schema** (all agents can read, workers must write):
| Key | Writer | Content |
|-----|--------|---------|
| `shared:worker_status:{name}` | each worker | `{worker, stats, updated_at}` — heartbeat |
| `shared:catalog_stats` | catalog_scraper | `{total_active, added_24h, updated_24h, top_brands}` |
| `shared:rex_progress` | REX | `{brands_done, brands_total, last_brand, todos_left}` |
| `shared:noa_last_campaign` | NOA loop | `{week_theme, platforms, created_at}` |
| `shared:price_sync_stats` | sync_prices | `{updated, errors, last_run}` |
| `shared:system_health` | health monitor | `{services_down, zombie_count, dlq_count, checked_at}` |

**Rule: every worker MUST call `write_worker_heartbeat()` at the start and end of each cycle.**
This is the only way agents know a worker is alive and what it produced.

### Alerting System (BACKEND_API_ROUTES.py `_health_monitor_loop`)
Runs every 5 min. All owner alerts use Redis-backed cooldowns that survive container restarts.

| Alert | Cooldown key | Default TTL |
|-------|-------------|-------------|
| Service down/restored | state-change dedup (no cooldown) | — |
| catalog_stagnation (<50 parts updated in 6h) | `autospare:alert_cooldown:catalog_stagnation` | 1h |
| high_error_rate (>5% errors/1h) | `autospare:alert_cooldown:high_error_rate` | 1h |
| worker_silence (db_update_agent >2h) | `autospare:alert_cooldown:worker_silence` | 1h |
| DLQ growth (new failures only) | `autospare:dlq_last_alerted_count` in Redis | 24h |
| per-job failure | `autospare:alert_cooldown:job_fail_{job_id}` | 24h |
| zombie auto-kill | `autospare:alert_cooldown:zombie_{job_id}` | 24h |

Admin users also receive WhatsApp per alert type, also gated by `autospare:alert_cooldown_admin_wa:{alert_key}` with same TTL.

### Zombie Auto-Fix (Check 5b in health monitor)
Any job with `status = 'running'` and heartbeat silent > 30 min is automatically:
1. Redis lock `autospare:lock:{job_name}` deleted → next scheduled run can acquire it
2. `job_registry.status` set to `'failed'` with auto-kill message
3. Owner alerted once per job_id (24h cooldown)

No manual `redis-cli DEL` or SQL update needed — the sweep handles it every 5 min.

### Todo System (agent_todo_utils.py)
Read active todos at start of EVERY cycle.
`get_active_agent_todos(db, agent_name)` → returns not_started + in_progress
`todo_requests_ranked_first()` → if True, process high-priority todos first

### Resilience (resilience.py)
ALL external calls: use `@retry_with_backoff`
max_retries=3 | base_delay=1.0s | max_delay=60.0s
Retry: HTTP 429, 503, 504 | Skip: 404, 401, 403

### Distributed Lock (distributed_lock.py)
Acquire lock before any write-heavy job.
Never run two instances of the same job simultaneously.
Lock key pattern: `autospare:lock:{job_name}` — TTL 86400s default.
Zombie sweep auto-clears locks for jobs with stale heartbeat (>30 min silent).

### Job Registry
`job_registry_start(job_name)` before every job.
`job_registry_finish(job_name, result)` after completion.
Workers MUST update heartbeat regularly via `job_registry_heartbeat()` or write to `job_registry.last_heartbeat_at` — silence >30 min triggers auto-kill.

### Concurrency
`_TASK_SEMAPHORE = asyncio.Semaphore(50)` — max 50 concurrent tasks.

---

## Data Rules

### DO
- Filter `is_active = TRUE` before processing parts
- Batch writes: max 25 rows per transaction
- Use `ORDER BY RANDOM()` for sampling (prevents starvation)
- Update `updated_at = NOW()` on every write to parts_catalog
- Use `manufacturer_normalization.py` for brand name canonicalization
- Use `categories.py` CATEGORY_MAP as single source of truth (28 categories)
- Log every job via job_registry
- Read active todos at start of each cycle

### DO NOT
- Never delete rows — use `is_active = FALSE`
- Never process inactive parts
- Never hardcode credentials — env vars only
- Never expose supplier names/margins/cost prices to customers (Layer A agents)
- Never allow ordering before payment confirmed
- Never run destructive operations without before/after row counts
- Never overwrite higher-confidence data with lower-confidence data
- Never fabricate, simulate, or hallucinate data — all counts, metrics, statuses, and query results must come from live DB/service queries; never invent or estimate values when real data is accessible


### Golden Rules (apply to every task, every session)

1. **Make a todo list first.** For every task, split into a numbered checklist before writing any code. Work through items in order. Do not skip or combine steps.
2. **Root fix only.** No patches, no temporary workarounds shipped as final resolution. If a quick guard is needed for emergency mitigation, mark it temporary and complete the root fix in the same cycle.
3. **After each fix, write a test and run it.** Confirm the fix works before moving to the next item.
4. **Be careful of breaking points.** Before any change, identify critical paths that could break (auth flow, payment flow, data path, API contracts). Check them explicitly after the fix.
5. **Document every fix.** Add a session entry to FIXES_TRACKER.md. Update PRE_LAUNCH_CHECKLIST.md, roadmap.md, or phases.md as relevant. Do not close a task without documentation.
6. **Verify from real data, not .md files.** Always read the live container env, DB, or source files — never trust stale documentation as ground truth.

### Root Fix Policy (Production Safety)
- For each task, run explicit breaking-point checks (critical paths, route links, auth flow, and data-path validation) before closing.
- Every fix must include a root-fix action, not only symptom masking.
- Root-fix only: do not ship patch-only or temporary workaround changes as final resolution.
- If an emergency guard is required, mark it as temporary and complete the full source-level root fix in the same task cycle before closure.
- Do not jump to later sections/stages before finishing and validating earlier stages/steps.
- For each task, create a todo checklist first, execute step-by-step, and provide follow-up status after each step.
- Never add or do changes without explicit user confirmation.
- Do not change UI size, spacing, typography, or visual style unless the user explicitly approves that exact visual change.
- Do not patch running containers as a permanent fix.
- Always apply the fix in source first, then rebuild/redeploy the service.
- Runtime hotfix is allowed only for emergency mitigation and must be followed by source commit + rebuild.
- After deploy, verify both: code path output and user-visible behavior.
- For UI issues, first identify the exact served artifact (SPA build, static HTML, or proxy target) and patch that source-of-truth; do not assume the edited app is the one served at runtime.
- Before wiring any CTA/nav link, verify the target route is implemented in the same served app; do not point to paths that silently fall back to landing with no route handler.
- Do not stop at a fallback if the upstream exception or bad data path is already known.
- When a quick guard is added to protect users, fix the originating code or data path in the same work cycle before closing the issue.
- Validate the exact failing command or sample that exposed the defect; do not switch to a broader or different check.
- For NOA social links, prefer shortest stable public URLs (e.g., `https://wa.me/<digits>`).

### Conflict Resolution (when sources disagree)
```
1. Official manufacturer / importer   → confidence 1.00
2. OEM cross-reference tables         → confidence 0.90
3. Known aftermarket brands           → confidence 0.85  (SEE manufacturer_normalization.py PARTS_BRANDS)
4. Marketplace APIs (eBay, AliExpress)→ confidence 0.65
5. Scraped web data                   → confidence 0.50
```
Higher confidence always wins. All conflicts logged.

---

## Business Rules

- Primary currency: ILS (₪). USD converted via `currency_rate.py`
- Part origin tags: `original` | `oe_equivalent` | `aftermarket` → SEE phases.md § Layer 8
- Aftermarket tiers: `OEM` | `OE Equivalent` | `Economy` → displayed by MAYA + NIR
- Fitment source of truth: `part_vehicle_fitment` table → built in phases.md § Phase 4
- Vehicle registry: `vehicle_market_il` (36,831+ vehicles from data.gov.il)

### PRICING POLICY — CORE BUSINESS RULE (do not violate)

**Applies to ALL part types: OEM, original, aftermarket, accessories. No exceptions.**

**SINGLE RULE: base_price = cost × 1.45 for EVERY part.**

| Rule | Detail |
|------|--------|
| Profit margin | **45%** on every part — HIDDEN from customers, never shown in any API response |
| All parts | `base_price = cost_price × 1.45` regardless of source type |
| Shipping | Per supplier policy — env `DEFAULT_CUSTOMER_SHIPPING_ILS` (default ₪59) |
| `importer_price_ils` | KGM/SsangYong only: wholesale cost excl. VAT. **Must be 0 for ALL other brands.** |
| `online_price_ils` | International/eBay buy price (no IL VAT). eBay, Spareto, car-parts.ie, OEMPartsOnline. |
| `max_price_ils` | IL market reference (dealer retail incl. 18% VAT). Source for Case 1 and Case 3. |
| `base_price` | Always stored as `cost × 1.45`. Computed by `normalize_base_price()` every 6h. |

**normalize_base_price() — 3 cases, all × 1.45:**

| Case | Condition | Formula |
|------|-----------|---------|
| 1 | `importer_price_ils > 0` (KGM/SsangYong) | `base = ROUND(max_price_ils × 1.45, 2)` |
| 2 | `importer=0, online_price_ils > 0` (eBay/international) | `base = ROUND(online_price_ils × 1.45, 2)` |
| 3 | `importer=0, online=0, max_price_ils > 0` (IL official ref) | `base = ROUND(max_price_ils × 1.45, 2)` |

**VAT per source (determines max_price_ils, NOT base_price formula):**
- Toyota / Kia / LR / Mazda / Subaru: PDF price **EXCL. VAT** → `max_price_ils = pdf_price × 1.18`
- Porsche: PDF price **INCL. 18% VAT** → `max_price_ils = pdf_price`
- KGM / SsangYong: PDF price **INCL. 17% VAT** → `importer = pdf_price / 1.17`, `max = importer × 1.18`
- eBay / international: no IL VAT → `online_price_ils = converted_price` (no VAT added)

Handled by `PRICES_INCL_VAT` dict in `supplier_pdf_import.py` and `il_importer_pdf_import.py`.

**NEVER expose in customer-facing API:**
- `profit` field
- `cost_ils` field
- `importer_price_ils` in search results
- Any field that lets a customer back-calculate the 45% margin

**MANDATORY VAT CHECK RULE — every import, upload, or harvest:**
Before writing any price to the DB, ALWAYS verify whether the source price includes VAT or not.

| Source | VAT in price? | Action |
|--------|--------------|--------|
| Official IL importer PDFs (Toyota, Kia, LR, Nissan) | EXCL. VAT | `max = price × 1.18` |
| Porsche PDF | INCL. 18% VAT | `max = price` |
| KGM / SsangYong PDF | INCL. 17% VAT | `importer = price / 1.17`, `max = importer × 1.18` |
| eBay / international | No IL VAT | `online_price_ils = converted_price` |
| Unknown source | MUST VERIFY | Log warning, default to excl-VAT if unresolved |

**Verification:** `test_pricing_policy.py` — T1-T8 must all pass. Key ratios:
- `base_price / max_price_ils = 1.4500` for ALL parts (Cases 1 and 3)
- `base_price / online_price_ils = 1.4500` for international parts (Case 2)
- `importer_price_ils = 0` for all non-KGM/SsangYong brands (enforced in ON CONFLICT)

---

## Scheduled Jobs

| Time UTC | Worker | Jobs | Phase |
|----------|--------|------|-------|
| 00:00 | REX | Category discovery, OEM lookup, eBay sync, Transport pipeline | Phase 1 + 6 |
| 12:00 | REX | FX refresh, Brand discovery, Price sync | Phase 1 + 6 |
| Every 30s | db_cleanup_agent | Tasks 1–8 (340 rows/cycle) | Phase 2 |
| Every 24h | auto_backup | pg_dump both databases | Phase 2 (L17) |

→ Full pipeline phase order: SEE phases.md

---

## Standard Report Format
Every pipeline job must return:
```json
{
  "task": "function_name",
  "status": "ok | error | skipped",
  "scanned": 0,
  "updated": 0,
  "flagged": 0,
  "elapsed_s": 0.0,
  "errors": []
}
```

---

## UI Standards
→ All UI/UX rules, colors, components, and layout: SEE ui-ux.md
Agents that interact with UI: NIR (search), MAYA (pricing), LIOR (orders), DANA (support)

### Import/Dedupe Guardrails (Postmortem Rule)
- Before any destructive dedupe/merge, compute expected target count from source-of-truth (input file/source set) and stop if projected post-merge count cannot be reconciled.
- Do not dedupe only by `(name, oem_number)` when names are synthetic/template-generated; validate with source keys (`supplier_sku` / catalog key) first.
- For high-impact merges (>100 rows), create rollback artifacts first (loser->keeper mapping export + affected IDs) before delete.
- After any import/dedupe that changes catalog rows, run a mandatory DB vs Meilisearch parity check for the affected manufacturer and do not close the task until counts match.
- For reconciliation/finalization jobs, `meili_sync.py` must run in rebuild mode (`--rebuild` or `MEILI_REBUILD=1`); incremental upsert mode is not allowed as final sync.
- For single-manufacturer fixes, do not rebuild/delete the full Meilisearch catalog; use scoped sync (`meili_sync.py --manufacturer <MFR> --no-rebuild`) so only that manufacturer is refreshed.
- Full index rebuild is required only after cross-manufacturer destructive changes (bulk dedupe/delete/merge) or global schema/index-setting resets.
- Import remediation closure must include manufacturer quality metrics: inactive count, missing category count, bad-name count, no-supplier count, and DB vs source key gap.

---

## Import Data Standard (Mandatory for ALL import scripts)

### How to Build a New Import Script — Step by Step

**Before writing any code:**
1. Add the top-of-file docstring (see Script Documentation Standard below)
2. Look up the manufacturer UUID in `car_brands` table — do NOT hardcode a UUID
3. Get-or-create the supplier record in `suppliers` table using `ensure_supplier()` pattern
4. Confirm source price format: ILS incl. VAT / ILS excl. VAT / GBP / USD — apply VAT rule below

**Transaction pattern (MANDATORY — per-row savepoints):**
```python
for row in batch:
    try:
        async with conn.transaction():   # savepoint per row — ONE failure must not abort others
            part_id = await conn.fetchval("INSERT INTO parts_catalog ... RETURNING id", ...)
            await conn.execute("INSERT INTO supplier_parts ...", ...)
    except Exception as e:
        log.warning("row error %s: %s", row["sku"], e)
        errors += 1
```
- `parts_catalog` and `supplier_parts` inserts share the same savepoint
- Never wrap more than 25 rows in one outer transaction

**ensure_supplier() pattern (copy exactly):**
```python
async def ensure_supplier(conn) -> str:
    row = await conn.fetchrow("SELECT id FROM suppliers WHERE name=$1", SUPPLIER_NAME)
    if row:
        return str(row["id"])
    sid = str(uuid.uuid4())
    await conn.execute(
        "INSERT INTO suppliers(id,name,website,country,reliability_score,is_active,created_at,updated_at)"
        " VALUES($1,$2,$3,'IL',0.90,TRUE,NOW(),NOW())",
        sid, SUPPLIER_NAME, SUPPLIER_URL)
    return sid
# NOTE: suppliers table has NO currency column — do not add it
```

---

### Every import script MUST harvest and store these fields:

#### parts_catalog fields (import all available from source):
| Field | Required | Notes |
|-------|----------|-------|
| `sku` | YES | `{BRAND}-{OEM_CLEAN}` format, e.g. `KIA-12345A` |
| `oem_number` | YES | Raw OEM from source, max 100 chars |
| `name` | YES | English name (translate if needed) |
| `name_he` | YES | Hebrew name — harvest from source or delegate to ai_catalog_builder |
| `manufacturer` | YES | Canonical brand name — use `manufacturer_normalization.py` |
| `manufacturer_id` | YES | UUID FK from `car_brands` — DB constraint: if manufacturer set, this must NOT be NULL |
| `category` | YES | From `categories.py` CATEGORY_MAP keys (28 categories) |
| `description` | HARVEST | Full text description from source |
| `specifications` | HARVEST | JSONB: `{vat_included, vat_rate, currency, source, shipping_to_il, importer, warranty_months}` |
| `compatible_vehicles` | HARVEST | JSONB array of `{manufacturer, model, year_from, year_to}` |
| `importer_price_ils` | HARVEST | Price excl. VAT — official importer sources only |
| `online_price_ils` | HARVEST | Price from online marketplace (eBay/AliExpress) |
| `min_price_ils` | YES | Set = `importer_price_ils` (or `online_price_ils` if no importer price) |
| `max_price_ils` | YES | Set = `importer_price_ils * 1.18` OR source price if already incl. VAT |
| `part_condition` | YES | `'New'` / `'Used'` / `'Remanufactured'` — DB default is `'New'` |
| `aftermarket_tier` | YES | `NULL` (genuine OEM) / `'OE_equivalent'` / `'economy'` / `'generic'` — **NEVER `'OEM'` or `'OE Equivalent'`** — these fail the DB check constraint |
| `is_safety_critical` | HARVEST | `TRUE` for brakes, airbag, steering — DB default is `FALSE` |
| `barcode` | HARVEST | If available |
| `weight_kg` | HARVEST | If available |
| `needs_oem_lookup` | YES | `FALSE` if OEM present, `TRUE` if missing |
| `master_enriched` | YES | Always `FALSE` on insert — AI catalog builder fills this later |
| `is_active` | YES | Always `TRUE` on insert |

> **NEVER add `supplier_id` to `parts_catalog` INSERT** — that column does not exist. Supplier link goes in `supplier_parts` table only.

#### part_vehicle_fitment (always populate if source has model data):
- `part_id` — UUID from parts_catalog RETURNING id
- `manufacturer` — brand name string (e.g. `'Kia'`, `'BMW'`)
- `manufacturer_id` — UUID FK from `car_brands` — required if manufacturer is set (DB constraint)
- `model` — specific model name (e.g. `'Sportage'`, `'X5 30D'`, `'Range Rover Sport'`)
- `year_from` — integer, NOT NULL
- `year_to` — integer or NULL (use NULL if still current)
- `engine_type` — `'Electric'` / `'Gasoline'` / `'Diesel'` / `'Hybrid'` — NULL if unknown
- `notes` — source of fitment data (e.g. `'Champion Motors source'`)
- **NEVER use `year_start`/`year_end`** — those columns do not exist
- Always use: `ON CONFLICT(part_id, manufacturer, model, year_from) DO NOTHING`

**When source has model names embedded in part names** (e.g. Land Rover, Jaguar):
```python
# Order list longest/most-specific first to avoid partial matches
LR_MODELS = ['Defender 110', 'Defender 90', 'Defender', 'Discovery 5', 'Discovery', 'Range Rover Sport', ...]
for m_name in LR_MODELS:
    if m_name.upper() in part_name.upper():
        # insert fitment row for this model
        break
```

**When source has no fitment data** — queue REX todo:
```python
await conn.execute("""
    INSERT INTO agent_todos(id, agent_name, title, description, priority, status, created_at, updated_at)
    VALUES(gen_random_uuid(), 'REX', $1, $2, 'high', 'not_started', NOW(), NOW())
""", f'Fetch fitment for {manufacturer} parts',
     f'No fitment in source for {manufacturer}. '
     f'Query in priority order: samelet.com API → eBay Motors fitment API → autodoc.co.uk → PartSouq → 7zap.com. '
     f'Use TecDoc only if TECDOC_API_KEY is set in env. SEE skills.md § REX Approved Fitment Sources.')
```

#### supplier_parts (always create for every import source):
```sql
INSERT INTO supplier_parts(
    id, supplier_id, part_id, supplier_sku,
    price_ils, price_usd, availability, is_available,
    warranty_months, estimated_delivery_days, supplier_url,
    created_at, updated_at)
VALUES(gen_random_uuid(), $1::uuid, $2::uuid, $3,
       $4, 0.0, $5, $6, $7, $8, $9, NOW(), NOW())
ON CONFLICT(part_id, supplier_id) DO UPDATE SET
    price_ils=EXCLUDED.price_ils,
    is_available=EXCLUDED.is_available,
    updated_at=NOW()
```
- `price_usd` — **always pass `0.0`, NEVER `NULL`** — column is NOT NULL
- `price_ils` — **must equal `base_price` exactly** (45% margin already applied). Do NOT use `importer_price_ils` (which is 0 for non-KGM) or add any extra multiplier on top.
- `availability` — `'in_stock'` / `'out_of_stock'` / `'on_order'`
- `warranty_months` — **24** for official Israeli importers (Delek, Kia, Land Rover); **12** default for all others
- `estimated_delivery_days` — 14 for local, 21 for UK/EU, 30 for overseas
- ON CONFLICT key is `(part_id, supplier_id)`

**REAL_DATA_ONLY supplier rule (enforced in `db_update_agent.py`):**
- `_UNIVERSAL_SUPPLIERS = []` — intentionally empty. No supplier rows are auto-generated for all parts.
- Every `supplier_parts` row must come from a real scraper/importer that has sourced actual data for that specific part.
- `_MANUFACTURER_SUPPLIERS` only links official IL importers (LR, Zeekr) at `price_mult = 1.0` (price_ils = base_price, no extra multiplier).
- **Never add fake/placeholder rows with fabricated SKUs** (EBAY-uuid, MST-uuid, etc.) — these were deleted in June 2026.

---

### VAT Rules (read carefully per source):

| Source type | Price format | How to store |
|---|---|---|
| Official IL importer (Delek, Kia, LR, Zeekr) | ILS **excl.** VAT | `importer_price_ils = price`; `min_price_ils = price`; `max_price_ils = ROUND(price * 1.18, 2)` |
| samelet.com API | provides `PriceNoVat` + `PriceWithVat` | `importer_price_ils = PriceNoVat`; `min_price_ils = PriceNoVat`; `max_price_ils = PriceWithVat` |
| Champion Motors / BYD | ILS **incl.** VAT | `importer_price_ils = ROUND(price/1.18,2)`; `min_price_ils = ROUND(price/1.18,2)`; `max_price_ils = price` |
| SNG Barratt (UK) | GBP excl. VAT | `base_price = ROUND(gbp * GBP_TO_ILS * 1.18, 2)`; `online_price_ils = base_price`; `min_price_ils = base_price` |
| eBay / AliExpress | USD or ILS, no IL VAT | `online_price_ils = converted`; `min_price_ils = online_price_ils` |

- Israeli VAT = **18%**
- Always store `"vat_included": true/false` and `"vat_rate": 0.18` in `specifications` JSONB

---

### Part Type and aftermarket_tier Rules:

```python
# Determine from source flags: is_original, part_type_he, MaterialType, type_name, etc.
is_orig = source.get("is_original") or source.get("part_type_he") == "מקורי"

part_type        = "original"      if is_orig else "oe_equivalent"
aftermarket_tier = None            if is_orig else "OE_equivalent"

# For known generic/budget brands: aftermarket_tier = "economy" or "generic"

# VALID values:   NULL, 'OE_equivalent', 'economy', 'generic'
# INVALID values: 'OEM', 'OE Equivalent', 'Economy', 'Aftermarket'  ← these raise DB constraint error
```

---

### What to do when data is MISSING from source:
1. Import what is available — never skip a part due to missing optional fields
2. Set `needs_oem_lookup = TRUE` if oem_number is missing
3. Set `master_enriched = FALSE` always (ai_catalog_builder.py fills this later)
4. For missing fitment — queue a REX todo (see fitment section above)
5. For missing Hebrew names — ai_catalog_builder.py handles translation
6. For missing prices — queue a REX todo to fetch from eBay/AliExpress/samelet

---

### Confidence tiers (never overwrite higher with lower):
```
1.00 — Official manufacturer / Israeli importer PDF
0.90 — OEM cross-reference tables
0.85 — Known aftermarket brands (see manufacturer_normalization.py)
0.65 — Marketplace APIs (eBay, AliExpress)
0.50 — Scraped web data
```

---

### Post-import MANDATORY steps (never skip):
1. Per-row savepoints commit automatically — no extra COMMIT needed
2. Run Meilisearch sync (scoped): `python3 /app/meili_sync.py --manufacturer {BRAND} --no-rebuild`
3. Verify catalog counts:
   ```sql
   SELECT COUNT(*),
          COUNT(*) FILTER (WHERE importer_price_ils > 0 OR online_price_ils > 0) AS has_price,
          COUNT(*) FILTER (WHERE min_price_ils IS NOT NULL) AS has_min_price
   FROM parts_catalog WHERE manufacturer='{BRAND}' AND is_active=TRUE;
   ```
4. Verify fitment rows: `SELECT COUNT(*) FROM part_vehicle_fitment pvf JOIN parts_catalog pc ON pvf.part_id=pc.id WHERE pc.manufacturer='{BRAND}';`
5. Log result via `job_registry_finish(job_name, result_dict)`
6. Print standard result JSON:
   ```json
   {"task":"import_{brand}", "status":"ok", "scanned":N, "updated":N, "fitment":N, "flagged":0, "elapsed_s":X, "errors":[]}
   ```

### Import Remediation Closure (after any dedupe/fix run):
- DB vs Meilisearch parity check for the affected manufacturer
- Manufacturer quality metrics: inactive count, missing category count, bad-name count, no-supplier count, DB vs source key gap

---

## Script Documentation Standard (Mandatory for ALL scripts)

Every Python script in this project MUST have a top-of-file docstring with:
```python
"""
Script: <filename>
Purpose: <one-line description>

Process:
  1. <step>
  2. <step>
  ...

Data Imported / Modified:
  - parts_catalog: <what fields are written>
  - part_vehicle_fitment: <what fields are written, or "not used">
  - supplier_parts: <what fields are written, or "not used">
  - Other tables: <list>

Data Sources / Web Links:
  - <source name>: <URL>
  - <source name>: <URL>

Missing Data Delegation:
  - <what REX/AI will fill in>

Author: AutoSpareFinder Agent
Last Updated: <date>
"""
```

When writing a NEW import script — add this docstring before writing any code.
When UPDATING an existing script — update the docstring to reflect current state.
