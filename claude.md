# AutoSpareFinder — Agent Core Rules
# READ THIS FIRST before reading any other file.

## Navigation
- What each agent can do → READ: skills.md
- How the pipeline works → READ: phases.md
- How the UI must look → READ: ui-ux.md

---

## System Overview
Platform: Israeli auto parts marketplace
Stack: FastAPI + PostgreSQL (catalog + PII) + Redis + Meilisearch + Docker Compose
Server: Hetzner 94.130.150.23

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

API: `AgentMemory.set/get/delete` | `append_event` for audit
Max items: 8 | Max value length: 280 chars

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

### Job Registry
`job_registry_start(job_name)` before every job.
`job_registry_finish(job_name, result)` after completion.

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

- VAT: 18% (Israel only) — TAL agent handles this
- Primary currency: ILS (₪). USD converted via `currency_rate.py`
- Part origin tags: `original` | `oe_equivalent` | `aftermarket` → SEE phases.md § Layer 8
- Aftermarket tiers: `OEM` | `OE Equivalent` | `Economy` → displayed by MAYA + NIR
- Fitment source of truth: `part_vehicle_fitment` table → built in phases.md § Phase 4
- Vehicle registry: `vehicle_market_il` (36,831+ vehicles from data.gov.il)

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
