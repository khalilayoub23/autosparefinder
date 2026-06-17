# AutoSpareFinder — Claude Code Instructions

## System Review Format

When the user asks "give me a review / review the system / check everything", always query live data and fill in this exact table format:

### Active Processes
| Process | Status | Details |
|---|---|---|
| `uvicorn` | ✅/❌ | CPU% MEM% |
| `run_all_tasks` | ✅/⏳/❌ | Current task, elapsed time |
| `meili_sync` | ✅/⏳/❌ | N/M docs (%), ETA |
| `freesbe_importer` | ✅/⏳/❌ | page N/total |

### Memory
| Container | Used | Limit | % |
|---|---|---|---|
| Backend | X GB | 2 GB | % |
| Meilisearch | X GB | 1.5 GB | % |
| Postgres | X MB | 2 GB | % |
| Redis | X MB | 256 MB | % |

### Catalog Health
| Metric | Count |
|---|---|
| Total active parts | N |
| With IL importer price | N (%) |
| With base_price | N (%) |
| With fitment data | N rows |
| Categorized | N |

### Agent Todos
| Agent | Status | Count |
|---|---|---|
| `db_update_agent` | ✅/⏳ completed | N pending |
| `rex` | ✅ | N pending |
| `db_cleanup_agent` | ⏳/✅ | N pending |
| `scraper` | ⏳/⚠️ | N pending |
| `NIR` | ⏳ human | N manual tasks |

### Job History (today)
| Job | Result | Duration |
|---|---|---|
| last run_all_tasks | ✅/❌ | elapsed |
| last scraper_cycle | ✅/❌ | elapsed |

### Open Issues
List any blockers, errors, or pending decisions.

---

## Data to collect for system review

```bash
# Memory per container
docker stats --no-stream --format "{{.Name}} {{.MemUsage}} {{.MemPerc}}" 2>/dev/null

# Running processes in backend
docker exec autospare_backend ps aux | grep python | grep -v grep

# Meili progress
docker exec autospare_backend tail -3 /app/state/logs/meili_sync.log 2>/dev/null

# Catalog health
docker exec autospare_backend python3 -c "
import asyncio, asyncpg, os
DB = os.environ.get('DATABASE_URL','').replace('postgresql+asyncpg://','postgresql://')
async def main():
    conn = await asyncpg.connect(DB)
    row = await conn.fetchrow('''
        SELECT
            COUNT(*) FILTER (WHERE is_active) as total,
            COUNT(*) FILTER (WHERE is_active AND importer_price_ils > 0) as with_il_price,
            COUNT(*) FILTER (WHERE is_active AND base_price > 0) as with_base_price
        FROM parts_catalog
    ''')
    print(f'total={row[\"total\"]} il_price={row[\"with_il_price\"]} base={row[\"with_base_price\"]}')
    await conn.close()
asyncio.run(main())
"

# Agent todos
docker exec autospare_backend python3 -c "
import asyncio, asyncpg, os
DB = os.environ.get('DATABASE_URL','').replace('postgresql+asyncpg://','postgresql://')
async def main():
    conn = await asyncpg.connect(DB)
    rows = await conn.fetch(\"SELECT assigned_to_agent, status, COUNT(*) FROM agent_todos GROUP BY 1,2 ORDER BY 1,2\")
    for r in rows: print(f'  {r[0]} {r[1]}: {r[2]}')
    await conn.close()
asyncio.run(main())
"

# Job registry
docker exec autospare_backend python3 -c "
import asyncio, asyncpg, os
DB = os.environ.get('DATABASE_URL','').replace('postgresql+asyncpg://','postgresql://')
async def main():
    conn = await asyncpg.connect(DB)
    rows = await conn.fetch(\"SELECT job_id, status, started_at, last_heartbeat_at FROM job_registry WHERE started_at > NOW()-INTERVAL '24h' ORDER BY started_at DESC LIMIT 10\")
    for r in rows: print(f'  {r[\"job_id\"]} | {r[\"status\"]} | {r[\"last_heartbeat_at\"]}')
    await conn.close()
asyncio.run(main())
"
```

---

## Key Rules

- **Pricing**: UNIFORM 45% margin on ALL parts. base_price = cost × 1.45. No exceptions.
- **VAT**: Israeli VAT = **18%** (0.18). All scripts must use VAT = 0.18. Never 0.17.
- **Import formula**: Consumer price incl. VAT → cost = price/1.18 → max_price = price → base_price = cost×1.45. NEVER double-apply VAT.
- **Restarts**: Always run `bash /opt/autosparefinder/backend/scripts/pre_restart.sh` before any docker restart.
- **Monitoring**: Keep 30-min wakeup active (cron 0073265b). Reschedule after every restart.
- **Wakeup checks**: Every wakeup must verify crawler + REX + DB agent + catalogue agent.
- **NIR todos**: These are human tasks for the business owner (Khalil) — contact importers directly.
- **Scraper/Acura**: Solved via browser harvest → window.name → /api/v1/system/oem-relay → oempartsonline_importer. 5741 parts imported 2026-06-15.

---

## Known Blockers (as of 2026-06-17)

| Issue | Status | Fix |
|---|---|---|
| Acura scraping | ✅ Solved | Used browser harvest (5741 parts) + oem-relay → oempartsonline_importer. |
| OOM crash loop (meili rebuild) | ✅ Fixed | `REBUILD_DEFAULT="0"`, checkpoint saved at offset=total instead of cleared |
| OOM crash loop (run_all_tasks) | ✅ Fixed | 6 tasks disabled: merge_catalog_fitment, fix_base_prices, normalize_base_price, backfill_bmw/ford/jaguar fitment |
| auto_backup silent failure | ✅ Fixed | `db_url.replace("+asyncpg","")` added to auto_backup.py:31 |
| VAT 0.17 wrong pricing | ✅ Fixed | 389,750 parts corrected (importer_price + base_price) |
| Wrong 45% margin | ✅ Fixed | 196,501 parts corrected |
| 74% parts in כללי (uncategorized) | ⏳ In Progress | categorize_parts_batch.py running overnight (~8h) |
| part_condition `New`→`new` | ⏳ In Progress | Batched DB fix running (2.86M rows) |
| Meilisearch index field gaps | ⏳ In Progress | meili_sync rebuilding with part_condition + importer_price_ils (745K/3.45M, ~2.5h remaining) |
| Price comparison not surfaced | ❌ Todo | supplier_parts has 2.3M records — need API + search wiring |
| normalize_base_price/fix_base_prices | ⚠️ Disabled | Disabled to prevent OOM. Need batched rewrite before re-enabling. |
| NIR todos (Hongqi, WEY, Alfa Romeo) | 👤 Human | Khalil contacts importers directly |

---

## Last Monitoring Run — 2026-06-17 16:05

### Active Processes
| Process | Status | Details |
|---|---|---|
| `uvicorn` | ✅ Running | 6.6% mem (just restarted) |
| `run_all_tasks` | ✅ Running | 47-min cycles (was 12 min before OOM fixes) |
| `meili_sync` | ✅ Running | 745K / 3.45M (21%) — checkpoint-based, resumes on restart |
| `categorize_parts_batch` | ✅ Running | 2,515,656 parts in pool, ~8h remaining |
| `New→new part_condition fix` | ✅ Running | Batched 100K/run |

### Memory
| Container | Used | Limit | % |
|---|---|---|---|
| Backend | 136 MB | 2 GB | 6.6% ✅ (fresh restart) |
| Meilisearch | 1.09 GB | 1.5 GB | 72% ⚠️ (normal during sync) |
| Postgres | ~450 MB | 2 GB | ~22% ✅ |
| Redis | 5.4 MB | 256 MB | 2% ✅ |

### Catalog Health
| Metric | Count |
|---|---|
| Total active parts | 3,449,347 |
| With IL importer price | 1,027,191 (29.8%) |
| Wrong margin (≠ cost×1.45) | **0 ✅** |
| VAT rate = 0.17 | **0 ✅** |
| Categorized (specific) | ~1,073,000 (31%) — growing overnight |
| כללי (uncategorized) | 2,376,019 — categorizer running |
| Fitment data | 3,428,432 rows |

### Agent Todos
| Agent | Status | Count |
|---|---|---|
| `db_update_agent` | ✅ | 0 pending |
| `rex` | ✅ | 0 pending |
| `db_cleanup_agent` | ✅ | 0 pending |
| `scraper` | ✅ | completed 15:00, next 18:00 UTC |
| `NIR` | 👤 Human | 1 manual task |

### Recent Code Changes (2026-06-17)
- **db_update_agent.py:4372**: Disabled `merge_catalog_fitment_from_part_vehicle_fitment` — OOM per cycle
- **db_update_agent.py:4368,4370,4371**: Disabled `backfill_bmw/ford/jaguar_fitment_from_name_he` — OOM / already complete
- **db_update_agent.py:4382,4383**: Disabled `fix_base_prices`, `normalize_base_price` — OOM / already complete
- **auto_backup.py:31**: Fixed `+asyncpg` prefix in db_url regex — backups were silently failing
- **meili_sync.py**: Added `part_condition`, `importer_price_ils`, `has_il_price` to SELECT + filterableAttributes
- **meili_sync.py**: Fixed rebuild loop — default changed to `"0"`, checkpoint saved at offset=total on completion
- **docker-compose.yml**: Added `MEILI_REBUILD: '0'` to backend env
- **categorize_parts_batch.py**: New script — Python batch categorizer for 2.5M כללי parts using keyword rules

### Open Issues
1. Backend still OOM-crashes every ~47 min — structural issue (uvicorn memory accumulation from background tasks). Recommend increasing backend `mem_limit` from `2g` → `4g` in docker-compose.yml.
2. Meilisearch at 72% during sync — drops to ~44% after sync completes. Not a risk at this level.
3. 29.8% price coverage — 70% are aftermarket parts with no IL importer data. supplier_parts table has 2.3M international prices not yet surfaced in search/API.

---

## Architecture Quick Reference

- **Backend container**: `autospare_backend` — uvicorn + supervised background tasks
- **Persistent volume**: `worker_state:/app/state` — survives OOM restarts
- **Meili checkpoint**: `/app/state/meili_sync_checkpoint.json` — resume after crash
- **Freesbe checkpoint**: `/app/state/freesbe_import_progress.json`
- **Worker logs**: `/app/state/logs/`
- **DB**: PostgreSQL via `DATABASE_URL` env var
- **Search**: Meilisearch at `MEILI_URL` (http://meilisearch:7700)

---

## AI Stack — 3-Option Upgrade (2026-06-17)

The system uses three complementary AI approaches tuned to the server constraints
(8 GB RAM, 4-core AMD EPYC VPS, NO GPU, no swap):

### Option 1: Phi-3-mini via HF Router (enrichment/generation)
- **Model**: `microsoft/Phi-3-mini-4k-instruct`
- **Config**: `HF_ENRICH_MODEL` in `hf_client.py:69` + `docker-compose.yml`
- **Use cases**: `enrich_pending_parts` (Hebrew→English translation, part naming),
  search query normalization, chatbot Hebrew responses
- **Server cost**: 0 RAM — pure API call via existing HF PRO Router
- **Previous model**: `Qwen/Qwen2.5-7B-Instruct` (less Hebrew-capable)
- **Why not local**: 3.8B params needs ~8 GB RAM — server only has 7.8 GB total

### Option 2: Expanded keyword rules (bulk categorization)
- **Script**: `categorize_parts_batch.py` — massively expanded RULES list
- **Coverage**: 18 categories × 15-30 rules each = 400+ Hebrew + English keywords
- **New additions**: window glass, fender/splash shield, seat recliner, shock absorber variants,
  exhaust components, fuel tank/cap/sender, transmission details, body pillars/moldings, connectors
- **Performance**: 2000+ parts/sec, 0 RAM overhead, no API calls
- **Match rate target**: 22% → **55-65%** with expanded rules
- **Why not DistilBERT locally**: CPU-only inference = 486 hours for 2.5M parts (impractical)

### Option 3: HF zero-shot + Hebrew expansion (search quality)
- **Functions added to `hf_client.py`**:
  - `hf_classify_query(query)` — calls `facebook/bart-large-mnli` via HF Inference API,
    classifies search queries into 17 auto-part categories, cached 1h
  - `expand_hebrew_query(query)` — static dict of 40+ Hebrew→English automotive expansions,
    zero latency, e.g. `"רפידות לקורולה"` → `"brake pads corolla"`
- **Config**: `HF_ZSC_MODEL` env var (default: `facebook/bart-large-mnli`)
- **Server cost**: 0 RAM — API call, cached
- **Integration point**: Hook into `BACKEND_AI_AGENTS.py` search path where
  `SEARCH_ENABLE_HF_QUERY_NORMALIZATION=1` is checked

### Why NOT run models locally on this server
| Model | RAM needed | Server has | Verdict |
|---|---|---|---|
| DistilBERT + PyTorch CPU | ~600 MB + 1.5 GB for torch | ~185 MB free | ❌ OOM risk |
| Phi-3-mini local | ~8 GB (int8) | 7.8 GB total | ❌ won't fit |
| mBERT uncased | ~600 MB + torch | ~185 MB free | ❌ OOM + wrong for Hebrew |
| Zero-shot 2.5M items CPU | 486 hours | — | ❌ impractical |

---

## Data Pipeline Requirements — Every Scraper/Import MUST capture all 5 fields

Every time REX, the scraper, or any importer runs, it MUST collect and store:

| Field | DB Column | Notes |
|---|---|---|
| **Part type** | `parts_catalog.part_type` | Original / OEM / Aftermarket — never blank |
| **Name** | `parts_catalog.name` + `name_he` | English + Hebrew if available |
| **Price** | `supplier_parts.price_ils` + `price_usd` | Always write to supplier_parts with source |
| **Specs** | `parts_catalog.specifications` JSONB | `{"source","source_url","part_brand","price_ils","price_usd","in_stock","oem_ref","discovered_at"}` |
| **Fitment** | `part_vehicle_fitment` rows | `(part_id, manufacturer, model, year_from, year_to)` |

### Pipeline tables to monitor
- `catalog_versions` — every import run result (type, parts added, timestamp)
- `scraper_api_calls` — API call log (eBay, Google Shopping, etc.)
- `supplier_parts` — 2.3M price records by supplier; `is_available` must be true for search
- `search_misses` — real customer searches with 0 results → priority catalog gaps
- `brand_alias_review_queue` — auto-detected brand name variants; dismiss if confidence < 0.9
- `part_cross_reference` — OEM ↔ aftermarket links (25,939 rows from Febest)
- `part_vehicle_fitment` — vehicle compatibility rows (MUST exist for vehicle-filtered search)
- `job_registry` — all agent job runs with heartbeat and status

### Known pipeline bugs fixed
- `cadillac_israel_import.py` — `importer_price_ils=0` hardcoded → fixed to `cost=price/1.17`
- `gmc_buick_umi_import.py` — same bug → SQL fix applied
- OEM Parts Online supplier_parts — `is_available=false` on all 36K records → fixed to true
- Seat pads miscategorized as `brakes` → moved to `interior-comfort`
- Hyundai i35 zero fitment → 896 fitment rows added (2012-2017)
- Brand alias queue (11 pending low-confidence) → all dismissed

### What captures fitment today
- `run_brand_discovery()` in catalog_scraper.py → writes fitment if source provides `part["fitment"]` list (fixed 2026-06-17)
- `febest_scraper.py` → full fitment from detail pages (192 catalog pages)
- `post_import_fitment.py` → backfills fitment for IL importer Excel/PDF imports
- `isuzu_excel_import.py` → has native fitment parsing

### Scraper data sources priority
1. **OEM parts online** (oempartsonline.com) — OEM quality, has fitment
2. **Febest** (febest.de) — OEM cross-refs + fitment, 192 pages
3. **Official IL importer sites** — IL prices, partial fitment
4. **eBay** — fallback, broad coverage, no fitment
5. **RockAuto** — fallback, US prices, some fitment
