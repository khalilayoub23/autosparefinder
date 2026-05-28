# AUTOSPAREFINDER RUNTIME ORCHESTRATION AUDIT

**Evidence-based factual architecture mapping**

---

## SECTION 1: SCHEDULING LAYER

### CRON SCHEDULER
**Location:** System crontab (installed via `scripts/install_step4_worker_cron.sh`)  
**Type:** Linux crontab

#### Active Cron Jobs:

1. **Step 4 Worker Nightly**
   - Schedule: `35 2 * * *` (2:35 AM UTC, daily)
   - Command: `./scripts/run_step4_nightly.sh`
   - Log: `logs/phase_c1_step4_nightly_cron.log`

2. **Load Test (100 Users)**
   - Schedule: `0 3 * * *` (3:00 AM UTC, daily)
   - Command: `./scripts/run_100_users_load_test.sh`
   - Log: `logs/loadtest_100_users_cron.log`

### PYTHON BACKGROUND LOOPS (asyncio-based)
**Framework:** FastAPI with `asyncio.create_task()`  
**Trigger:** `@app.on_event("startup")` in `BACKEND_API_ROUTES.py`  
**Architecture:** No external message broker (no Celery/RQ).
All work uses `asyncio.create_task()` + `Semaphore(50)` concurrency cap.

#### Background Loops Started at API Startup:

| Loop | Type | Frequency | Purpose |
|------|------|-----------|--------|
| `_price_sync_loop()` | asyncio | Continuous | Price synchronization |
| `_stuck_orders_monitor_loop()` | asyncio | Every 30 min | Monitor/handle stuck orders |
| `_notify_search_miss_loop()` | asyncio | Every 60 min | User notifications for resolved searches |
| `_scrape_search_misses_loop()` | asyncio | Every 6 hours | eBay search for unresolved misses |
| `_abandoned_cart_loop()` | asyncio | Every 60 min | WhatsApp re-engagement |
| `_pending_payment_reminder_loop()` | asyncio | Every 30 min | WhatsApp payment reminders |
| `_health_monitor_loop()` | asyncio | Every 5 min | Service health + admin alerts |
| `_vip_detection_loop()` | asyncio | Every 24 h | VIP promotion + order sync |
| `_backup_loop()` | asyncio | Every 24 h | pg_dump autospare + autospare_pii |
| `_warmup_embed_model()` | asyncio | Once (startup) | Embedding model warmup (conditional) |

---

## SECTION 2: AGENT LAYER

**System Type:** Hybrid (asyncio tasks + direct function calls)

### Agent 1: Catalog Scraper
- **File:** `backend/catalog_scraper.py`
- **Type:** Background loop with `while True`
- **Start:** `start_scraper_task()` called in `startup()`
- **Behavior:** Continuous background loop

### Agent 2: DB Update Agent
- **File:** `backend/db_update_agent.py`
- **Type:** Background loop with nested `while True` loops
- **Start:** `start_agent_task(get_db, 6.0)` called in `startup()`
- **Interval:** 6.0 seconds between cycles
- **Behavior:** Periodic database normalization/update tasks

### Agent 3: DB Cleanup Agent
- **File:** `backend/db_cleanup_agent.py`
- **Type:** Async loop with `while True`
- **Function:** `async def run_cleanup_loop() -> None`
- **Start:** `asyncio.create_task(run_cleanup_loop())` in `startup()`
- **Behavior:** Micro-batch cleanup (continuous with sleep intervals)

#### Cleanup Tasks (sequential within loop):
1. `task1_fix_part_types()` → Sleep: 2 sec
2. `task2_fill_oem_from_crossref()` → Sleep: 3 sec
3. `task3_categorize_by_keywords()` → Sleep: 2 sec
4. `task4_fix_oem_lookup_flag()` → Sleep: 0.5-1.0 sec
   - **Acceleration:** Reduces sleep from 1.0s to 0.5s if 300+ consecutive full batches

### Agent 4: NOA Marketing Agent
- **File:** Part of `BACKEND_API_ROUTES.py`
- **Type:** asyncio task (`_noa_marketing_loop`)
- **Frequency:** Every 24 hours
- **Function:** Social media content scheduling and engagement tracking

---

## SECTION 3: EXECUTION LAYER

### EXECUTION FLOW: FastAPI Application Startup

```
docker compose up autospare_backend
    ↓
FastAPI initialization
    ↓
@app.on_event("startup") triggers
    ↓
Sequence of startup tasks:
  1. Load AI overrides from DB
  2. Create WhatsApp sentinel user
  3. Conditionally create embed model warmup task
  4-13. Create 9 asyncio background loops (price sync, stuck orders, search misses, etc.)
  14. Start catalog scraper task
  15. Start DB update agent
  16. Start DB cleanup agent
  17. Start NOA marketing agent
    ↓
Status: "✅ All systems ready — price-sync + catalog-scraper + db-agent schedulers started"
    ↓
All background loops running continuously
```

### EXECUTION FLOW: Cron-Triggered Tasks

**Nightly Step4 Worker** (2:35 AM UTC, daily)
```
crontab
    ↓
run_step4_nightly.sh
    ├─ Generate timestamp: YYYY-MM-DDTHH:MM:SSZ
    ├─ Call run_step4_worker_batch.sh with label "batch_nightly_${STAMP}"
    └─ Clean up old nightly logs (keep last 14)
        ↓
    run_step4_worker_batch.sh
        ├─ Execute: fitment_kpi_report.sh (before)
        ├─ Execute: docker compose exec backend python run_step4_worker_cycle.py --label "${LABEL}"
        ├─ Output: logs/phase_c1_step4_${LABEL}/worker_pass_report.json
        ├─ Execute: fitment_kpi_report.sh (after)
        └─ Generate: KPI delta report
```

---

## SECTION 4: TRANSPORT OFFICE PIPELINE STATUS

**Pipeline:** `run_rex_transport_office_pipeline.py`

### TRIGGER MECHANISM:
- **Type:** MANUAL ONLY
- **Command:** `python3 backend/run_rex_transport_office_pipeline.py [--page-limit N]`
- **Scheduler integration:** NONE
- **Cron entry:** NONE
- **Background task:** NONE
- **Agent invocation:** NONE

### INVOCATION SOURCES:
User manual execution (verified: no automated calls in codebase)

### OUTPUT:
- **Status:** Staging-only JSON artifacts
- **Location:** `backend/data/`
- **No production database writes**
- **No automatic import promotion**

### CONCLUSION:
**Transport Office Pipeline is 100% MANUAL**

---

## SECTION 5: CRITICAL FINDINGS

### SCHEDULING SYSTEM:
✓ **ACTIVE SCHEDULER FOUND** (crontab + asyncio)

- **Cron Jobs:** 2 active
  - Step4 nightly worker: 2:35 AM UTC daily
  - Load test: 3:00 AM UTC daily
- **AsyncIO Tasks:** 10 background loops
  - 9 continuous/periodic loops
  - 1 conditional warmup task

### AGENT SYSTEM:
✓ **ACTIVE AGENT SYSTEM** (4 agents)

- Catalog Scraper (continuous background loop)
- DB Update Agent (6-hour cycle)
- DB Cleanup Agent (micro-batch continuous)
- NOA Marketing Agent (24-hour social media)

### PIPELINE ORCHESTRATION:
❌ **NO ORCHESTRATION FOR TRANSPORT OFFICE PIPELINE**

**Relationship chain:**
```
Scheduler → Agent → Pipeline → Output
✓ Scheduler (cron + asyncio)
✓ Agent (4 background agents)
✗ Pipeline connection: MISSING
✗ run_rex_transport_office_pipeline.py: NOT INTEGRATED
```

**The Transport Office pipeline is a STANDALONE MANUAL-ONLY TOOL** not connected to any scheduling or orchestration system.

---

## SECTION 6: RUNTIME ARCHITECTURE DIAGRAM

```
┌─────────────────────────────────────────────────────────────────────────────┐
│              AUTOSPAREFINDER RUNTIME ORCHESTRATION ARCHITECTURE              │
└─────────────────────────────────────────────────────────────────────────────┘

SYSTEM STARTUP (docker compose up autospare_backend)
    ↓
┌──────────────────────────────────────────────────────────────────────────────┐
│ FastAPI @app.on_event("startup")                 BACKEND_API_ROUTES.py       │
└──────────────────────────────────────────────────────────────────────────────┘
    ├─→ asyncio.create_task(_price_sync_loop)
    ├─→ asyncio.create_task(_stuck_orders_monitor_loop)
    ├─→ asyncio.create_task(_notify_search_miss_loop)
    ├─→ asyncio.create_task(_scrape_search_misses_loop)
    ├─→ asyncio.create_task(_abandoned_cart_loop)
    ├─→ asyncio.create_task(_pending_payment_reminder_loop)
    ├─→ asyncio.create_task(_health_monitor_loop)
    ├─→ asyncio.create_task(_vip_detection_loop)
    ├─→ asyncio.create_task(_backup_loop)
    ├─→ asyncio.create_task(_warmup_embed_model) [conditional]
    ├─→ start_scraper_task()    → catalog_scraper.py
    ├─→ start_agent_task()      → db_update_agent.py
    ├─→ asyncio.create_task(run_cleanup_loop()) → db_cleanup_agent.py
    └─→ asyncio.create_task(_noa_marketing_loop)
```
