# AutoSpareFinder — Claude Code Instructions

> **Start here:** this is the single authoritative instruction file (the old `claude.md`
> was merged into it on 2026-07-18 — do not recreate a second lowercase copy). Jump to
> **[Repository & Backend File Map](#repository--backend-file-map-2026-07-18-reorg)** to
> find where any script lives, and **[Agent Core Rules & Shared Architecture](#agent-core-rules--shared-architecture-merged-from-claudemd-2026-07-18)**
> for the agent roster, memory/alerting/lock/job-registry model, and golden rules.
> Topical docs live in `docs/` (`docs/skills.md`, `docs/phases.md`, `docs/UI_UX.md`,
> `docs/roadmap.md`, `docs/SUPPLIERS.md`, `docs/schema/`).

## Mistake Log — Document Every Error & Never Repeat It (MANDATORY)

**Rule (owner-set 2026-07-18):** when a mistake is found — mine or a recurring system bug —
I MUST (1) record it in the **Mistake Log table below** (what went wrong, the ROOT cause, and
the concrete lesson), and (2) **apply the lesson everywhere the pattern exists, not just where
it surfaced.** The lesson is not learned until the log entry is written AND the fix is verified
across every affected file/surface. Before closing any task, re-read this log and check the
work against it.

**Why this rule exists (the triggering incident):** the pricing policy was *documented* but not
*fully implemented in the according files* — the conditional-VAT rule and the "importer must
carry a real cost, never 0" rule lived in the docs while individual importers / the search
display / NOA still applied flat VAT or left `importer_price_ils=0`. The owner had to catch it
twice. **A policy is only "done" when it is enforced in code on EVERY surface that touches it —
not when it's written down.**

**Operating rules distilled from past mistakes:**
1. **Fix all layers, not the first one.** When a rule/policy is wrong in one file, `grep` the
   whole codebase for the same pattern and fix every occurrence (importers, search, agents,
   marketing, checkout). "Fixed where it was reported" ≠ fixed.
2. **A policy needs a single enforcement point + a guard.** Prefer one server-side function
   (e.g. `_customer_price_fields` / `get_supplier_vat_rate`) that every surface calls, plus a
   self-healing task that corrects drift — never re-implement the rule per file.
3. **Verify against the LIVE system and the exact failing sample**, not the code's self-report
   or a `.md` file. A goal isn't done until an end-to-end check proves the outcome.
4. **Watch for the same class of bug** the log already names before writing new code.

| Date | Mistake / bug | Root cause | Lesson applied (everywhere) |
|---|---|---|---|
| 2026-07-14 | Search DISPLAY + NOA showed foreign parts ~18% too high | Flat `×1.18` VAT instead of conditional (IL-only) VAT | One `get_supplier_vat_rate` used by search, chat agents, NOA, checkout — 18% LOCAL only, 0% foreign. Verified on every surface. |
| 2026-07-18 | 3,627 IL parts stuck at `importer_price_ils=0` with margin-less `base_price` | Heal task's `AND base_price=0` guard skipped already-based parts | Removed the guard, heal from IL supplier cost (`base=cost×1.45`), `SKIP LOCKED`; gap→0; re-verified 18% VAT for IL importers end-to-end. |
| 2026-07-18 | Renamed `CLAUDE.md`→`claude.md` would silently stop instruction-loading | Case-sensitive Linux FS; harness loads exact `CLAUDE.md` | Keep the filename exactly `CLAUDE.md`; never a second lowercase copy. |
| 2026-07-18 | Import-testing modules ran real work (bulk_harvest / colmobil started harvesting/importing) | Some scripts do work at module import / ignore `--help` | Never bulk-`import` script modules to test them; use `py_compile` + AST import-resolution, and only actually-import the import-safe library modules. |

## PLATFORM GOALS (owner-set via /goal — MANDATORY LOG)

**Standing rules:**
1. Every time Khalil sets a goal with the `/goal` command, add or update an entry
   in this section — goal text, date, implementation, and status. This section is
   the durable record of what the owner wants the platform to be.
2. **A goal is not "Done" until it is VERIFIED** — after implementing, run an
   end-to-end check against the LIVE system that tests the *outcome* (not the
   code's self-report), and record the evidence in the Verification column.
   Implementation without verification = status stays ⏳. This is the same
   principle as [[feedback-verify-destination]]: self-reports are not proof.

| # | Goal (owner's words) | Set | Status | Implementation | Verification (evidence) |
|---|---|---|---|---|---|
| G1 | "It should not show the low price — it should show the right part that fits this car or the car asking about it" | 2026-07-05 | ✅ Done & Verified | Fitment-first search (Tier 0): when the customer's car is confirmed (plate → gov API), search demands a `part_vehicle_fitment` match (make+model+year). Results ranked by relevance, never by cheapness. Customer sees "✅ מאומתים לרכב שלך" on verified results, honest "שלח OEM לוודא" on fallbacks. Meili pool 200→1000 under fitment filtering + Hebrew→English query expansion for recall. Applied to chat flow AND website search. | **2026-07-05 live test** (Toyota Corolla 2017, query "רפידות בלם"): 5 results returned; each result's part_id independently re-checked against `part_vehicle_fitment` in the DB → **5/5 have a genuine Corolla-2017 fitment row**. Returned price order [₪358, ₪541, ₪78, ₪1088, ₪735] → provably relevance-ranked, not cheapest-first. Website API verified separately: same query + vehicle params returns fitment-filtered original part. |
| G2 | "User must have the same UI/UX in all of our chatting connections, and same search results and same prices" | 2026-07-05 | ✅ Done & Verified | (a) All 3 chat channels (WhatsApp/Telegram/web chat) share ONE brain — `process_user_message` — so behavior, search, checkout, and prices are identical by construction. (b) Website search ported to same recall features (expansion + deep pool + fitment). (c) **One canonical price formula on every surface**: `sell_net = cost×1.45; vat = sell×0.18; total = sell+vat+ship` — computed server-side in `_customer_price_fields()` (routes/parts.py), returned as `customer_price_ils/customer_vat_ils/customer_total_ils` on every supplier offer (search + comparison endpoint). Frontend `_supplierForCard` uses backend numbers, never invents margins (removed rogue ×1.30). Also fixed: comparison endpoint used to leak RAW supplier cost to customers. | **2026-07-05 live test** (same part id 07cb9da1… through all three surfaces): website comparison endpoint total **₪13,869.76**, chat search total **₪13,869.76**, Stripe checkout charge **₪13,869.76** — identical to the agora, same net/VAT breakdown (₪11,729.46 + ₪2,111.30). Before the fix the same part showed 4 different prices (incl. raw cost ₪8,089 leaked to customers). |

| G3 | "Audit and enhance the agents' skills — make it a todo list and go through all agents" | 2026-07-05 | ✅ Done & Verified | Full 11-agent audit (NOA, AVI, NIR, MAYA, LIOR, TAL, DANA, OREN, SHIRA, BOAZ, REX). Fixes: **NOA** — real-catalog price grounding, UTM attribution links, A/B ad-pack in Monday brief, Korean seed chars removed. **SHIRA** — was promising a referral program (₪100+10%), loyalty program, and coupon codes that DON'T EXIST (no tables); prompt rewritten to truth-only. **Coupon revenue hole closed** — `/marketing/validate-coupon` approved ANY code at 10% off; now fails closed. Stale roster doc fixed (agents run on Cerebras gpt-oss-120b, not "GitHub Models GPT-4o"). Healthy: BOAZ (daily price sync, job_registry), REX (3h cycles), LIOR (real PII-DB order lookups), all agents on gpt-oss-120b. | **2026-07-05**: NOA live generation cited real product+price ("PROFITOOL EST-708 — ₪138") with utm_source link (checks passed); deployed container code verified `valid: False` on coupon endpoint (auth-gated, static return); Boaz `sync_prices completed` today 03:33; REX cycle logs live. |
| G4 | "Harvesters managed by a smart agent/supervisor — finish one brand model, get the next; prioritize the Israeli car market list; keep going until all 114 brands + submodels imported" | 2026-07-07 | ✅ Done & Verified | **Queue-driven harvesting.** New `harvest_queue` table seeded from `vehicle_market_il` (gov registry) — one row per brand+model, `il_vehicle_count = SUM(mispar_rechavim_pailim)` (active cars on IL roads) as priority. **1,401 models across 83 brands**, ranked by road presence. Harvester rewritten from a hardcoded 144-model list to QUEUE-DRIVEN: each worker `claim_next_model()` (highest priority, `FOR UPDATE SKIP LOCKED` so 3 parallel workers never collide) → harvest → `complete_model()` (records parts_found, marks done/empty) → claims next. Auto-advances through the whole IL list by priority. Self-managing: `reclaim_stale_in_progress()` on startup (retries models killed mid-harvest), `requeue_completed_for_refresh(14d)` when queue drains (never idles — cycles the market forever for fresh prices). Oversight: `_harvest_supervisor_loop()` supervised task logs coverage every 30 min + WhatsApps owner a weekly digest (Sun 09:00 IL) with % done, brands covered, parts collected, and the next top-priority models. | **2026-07-07 live**: queue claimed #1 = `toyota/corolla` (132,079 IL vehicles), #2 Kia Picanto (125K), #3 Mazda 3 (94K) — provably IL-priority-ordered. Harvester log shows "Cycle 65 — queue-driven \| done=X/1401 models (Y/83 brands)". Workers pulling + completing models from the queue confirmed in flaresolverr_harvester.log. |

| G5 | "Handle different chat scenarios for clients — asking, selling, buying, shipping & financial details; agents should be smart, human not robotic, respond to small nuances, handle sales and promotions" + "add these qualities/skills to the agents" (active listening, empathy, objection handling, closing, EQ, human handoff, …) | 2026-07-09 | ✅ Done & Verified | Drove real multi-turn conversations through the live brain as a customer. Root-fixed 9 issues (see FIXES_TRACKER 2026-07-09): **(1) free-text car capture** — make+model+year in plain text (not just a plate) now starts the fitment-first flow (`_extract_vehicle_from_text`, Hebrew-prefix aware, LLM-independent); **(2) query cleanup** — `_strip_vehicle_terms` removes the restated car from the part query (0→5 results); **(3) category ROOT FIX** — `_extract_category_hint` returned Hebrew display names (`בלמים`/`מנוע`/`סינון`) but the DB `category` column is English slugs (`brakes`/`engine`/`filters`) + Hebrew `כללי` and holds ZERO of those names, so `category ILIKE '%סינון%'` matched 0 rows (`'סינון'`→0 vs `'filter'`→19,912). Rewrote `_CATEGORY_KEYWORDS` to ~120 bilingual keys → English DB-slug substrings (word-boundary for Latin keys); KEPT make + category as the precision filter (owner directive) and added a fitment-verified fallback so the ~2M parts dumped in the `general`/`כללי` catch-all still surface — a category/vocab miss can never discard a part that provably fits; **(4) query relaxation** for over-constrained Hebrew phrases; **(5) order intent** — "אני רוצה להזמין" closes to a real `/pay/` link; **(6) CoT-leak stripper** hardened (multi-draft + reply-then-reasoning + broadened markers); **(7) shipping truth**; **(8) PROFESSIONAL SKILLS & TRAITS** block added to the shared channel policy — the user's full CS/sales/soft-skill list operationalised as behaviours for every customer agent; **(9) test-harness multi-turn persistence**. | **2026-07-09 live (web, under heavy 429 load)**: `ask_buy` — "מסנן שמן לטויוטה קורולה 2018" → **3 fitment-verified ✅ Toyota oil filters ₪307/₪243/₪360** → "כמה זה עולה?" bot **remembers the car** → "כן אני רוצה להזמין" → **real link `https://autosparefinder.co.il/pay/UwX2QDM`** (full ask→buy cycle). `promo_nuance` — "יש הנחות?" → truth-only "אין קופונים פעילים" + real-value reframe; "מעצבן" → "אני מבינה את התסכול שלך" (empathy). Leak stripper unit-verified on 3 captured leak samples; clean He/En replies pass untouched. |

| G6 | "Go over the full categories — no part should stay at `general`, all parts should land at the correct category. Root-fix so the query and DB don't burn effort finding the right part; add metadata if needed, reorganize indexes if needed. Clear, organized categories that store parts correctly. Connect to the pipeline, then verify and document." | 2026-07-13 | ⏳ In progress | Root cause: categorization drifted (99.8%@570K → **1.09M `כללי` + 776K `general`** @4.1M) because new imports weren't categorizing on ingest and the keyword ruleset was too thin for the real vocabulary. **Data-grounded strategy (not guessing):** backlog is 58% oempartsonline (real English part names → categorizable by expanded keyword rules) + Car-Parts.ie 58K (**category is the last URL path segment** → deterministic map) + IL importers. Plan: (A) build Car-Parts.ie URL→canonical-category map (backfill + wire into importer), (B) comprehensively expand `categorize_parts_batch.py` rules with the measured vocabulary (multi-word disambiguation to avoid false matches — a wrong category is worse than `general`), (C) fix `normalize_part_types` bounded-batching (was single 27-min UPDATE causing lock storms that blocked categorization), (D) categorize-on-ingest in the pipeline + a category index for fast filtered queries, (E) backfill, verify a sample by hand, document. | (pending) |

| G7 | "Verify the system supports 3 languages: Arabic, Hebrew, English. Landing page must support all 3 with RTL + responsive on all screens (PC, tablet, mobile). Then: test all landing links/buttons; test chat sessions in all 3 languages + audit agents; fix the NOA link-shortener that isn't working. Document the PROCESS in roadmap.md (not FIXES_TRACKER)." | 2026-07-18 | ⏳ In progress | **Audit (2026-07-18):** backend chat already has 3-language LANGUAGE RULES (detect from first message → reply in Hebrew/Arabic/English, never mix; `preferred_language` memory) — needs live verification. Frontend landing page was **English-only with a DEAD `?lang=` switcher** (no i18n lib, no translations, `index.html lang="en"` no dir). Plan: (A) lightweight i18n (lang from `?lang=`/localStorage → set `<html lang/dir>`, `t()` dictionary AR/HE/EN, RTL for ar+he) wired into the landing switcher; (B) translate all landing copy ×3; (C) verify RTL + responsive at PC/tablet/mobile; (D) links/buttons test; (E) live chat test in 3 langs + agent audit; (F) fix NOA link-shortener. Process → `ROADMAP.md`. | **Landing ✅ Done & Verified** (Playwright e2e 30/30: dir rtl/ltr, translated headings, no overflow at PC/tablet/mobile, 15 links resolve, buttons work; Arabic RTL screenshot fully mirrored). **Chat ✅ 3/3** (Arabic gap CLOSED 2026-07-18: Arabic make/model aliases + `_alias_present`/`_strip_vehicle_terms` Arabic boundaries + ل prefix, ~50 Arabic part terms in `_CATEGORY_KEYWORDS`, localized results banner + `_vsum` he/ar/en — Arabic customer now gets an Arabic reply). **NOA link-shrinker ✅ fixed** (body URLs no longer destroyed). Landing/chat/NOA all done & verified — remaining i18n beyond the landing is optional (see ROADMAP G7). |

**Never regress:** price/margin math lives ONLY in the backend (`_customer_price_fields` + `create_whatsapp_checkout`). No client-side or per-channel price formulas. Any new channel/surface must consume the same fields. **VAT is CONDITIONAL, not flat ×1.18** — `get_supplier_vat_rate` applies 18% ONLY to LOCAL (IL) suppliers and 0% to foreign-sourced parts (most of the catalog: Car-Parts.ie/IE, SNG/UK, eBay/US). Any price ANY surface displays (incl. NOA's advertised/marketing prices) must be `cheapest-supplier cost × 1.45 + conditional VAT` — NEVER a flat ×1.18, and NEVER `base_price` (unreliable on some rows; can land near raw cost). NOA fixed 2026-07-14 (`_noa_real_catalog_fact`). Customer-facing agents may only claim programs/discounts that actually exist in code+DB. Chat agents: free-text car capture + query-strip + fitment-verified fallback must stay (a category/vocab miss must never discard a `part_vehicle_fitment` match); customer replies pass through `_strip_leaked_reasoning` + `_sanitize_internal_pricing_disclosure` (never leak chain-of-thought, draft options, or internal state under 429 fallback).

## Platform Vision (CRITICAL — read before suggesting anything)

AutoSpareFinder is a **global car parts comparison and sales marketplace** — the model is eBay/AliExpress for car parts, enhanced with AI capabilities. NOT a simple Israeli importer catalog.

### Core Customer Journey (confirmed 2026-06-26)
A customer finds a part for their specific car through **3 search paths**:
1. **Enter car details** (make/model/year) → backend finds fitment-matched parts
2. **Enter plate number** → resolves to car via NHTSA/IL plate lookup → fitment match
3. **Ask AI agent** (WhatsApp/Telegram/Web chat) → natural language → part + fitment match

After finding the right part, the platform shows **prices from multiple sellers** (eBay, AliExpress, Car-Parts.ie, Autodoc, PartSouq, Amayama, etc.) side by side. Customer picks, pays on platform. Platform purchases from supplier and ships to customer.

**Each part has 3 barcode types:**
- Barcode 1: **Original OEM** part number
- Barcode 2: **OEM equivalent** (same spec, manufacturer brand)
- Barcode 3: **Aftermarket** (alternative brand, same function)

**Seller visibility rules**: Supplier names/details are masked from customers (`_mask_supplier` in search API). Customer sees price + shipping only.

- **ALL parts must be searchable** — unpriced parts are real and will receive pricing. Never exclude.
- **Fitment is the core differentiator** — every part must be linked to vehicles it fits via `part_vehicle_fitment`. Harvest pipeline now writes fitment rows on every cycle.
- **Search must handle 10M+ parts** at <100ms.
- **AI is core** — semantic search, price comparison, recommendations.
- Target: 10M+ parts covering all major aftermarket brands globally.

**Implications for every technical decision:**
- Do NOT design for IL-only. Design for global.
- Do NOT exclude parts from search because they lack IL price. Missing price = opportunity.
- Search infrastructure must be chosen for 10M+ scale from the start.
- Every scraper/importer pipeline should be built for volume and variety of sources.

## MANDATORY: Before Writing Any Importer — Check These Patterns

These bugs recurred multiple times because I wrote from memory instead of checking. Read this section before writing any importer or scraper SQL.

### SQL Pattern 1 — ON CONFLICT for supplier_parts (CRITICAL)
`supplier_parts` has **TWO** unique constraints:
- `uq_supplier_parts_part_supplier (part_id, supplier_id)` — use this ONLY for same-part, same-supplier
- `supplier_parts_supplier_id_supplier_sku_key (supplier_id, supplier_sku)` — the one that gets hit on re-import

**ALWAYS use:**
```sql
ON CONFLICT ON CONSTRAINT supplier_parts_supplier_id_supplier_sku_key DO UPDATE SET
    price_ils=EXCLUDED.price_ils, is_available=EXCLUDED.is_available, updated_at=NOW()
```
**NEVER use** `ON CONFLICT(part_id, supplier_id)` in importer scripts — this misses the constraint that actually fires.

### SQL Pattern 2 — importer_price_ils in ON CONFLICT UPDATE (CRITICAL)
**ALWAYS use CASE WHEN in UPDATE to preserve existing value:**
```sql
importer_price_ils = CASE WHEN EXCLUDED.importer_price_ils > 0
    THEN EXCLUDED.importer_price_ils
    ELSE parts_catalog.importer_price_ils END
```
**NEVER** use `importer_price_ils = 0` or `importer_price_ils = EXCLUDED.importer_price_ils` without the CASE guard.

### SQL Pattern 3 — part_condition casing
Always lowercase: `'new'`, `'used'`, `'oem'`, `'aftermarket'` etc.
**NEVER** `'New'`, `'OEM'`, `'Used'`.

### SQL Pattern 4 — OEM number matching (normalized)
IL importer PDFs often use no-dash OEMs (`517592B300`), catalog has dashed (`51759-2B300`).
**Always try exact first, then normalized:**
```sql
-- Exact
WHERE oem_number=$1 AND LOWER(manufacturer)=LOWER($2) AND is_active LIMIT 1
-- Normalized fallback
WHERE REPLACE(REPLACE(UPPER(oem_number),' ',''),'-','')=$1 AND LOWER(manufacturer)=LOWER($2) AND is_active LIMIT 1
```

### SQL Pattern 5 — Per-row savepoints in asyncpg
For row-by-row imports, wrap each row in `async with conn.transaction():` inside the outer loop.
This creates a SAVEPOINT so one failed row doesn't abort the whole batch.

### Pattern 6 — Harvester JSON output format
`toyota_il_harvester.py` and similar write `{"parts": [...], "count": N}` (dict wrapper, not list).
Importer must unwrap: `raw = json.loads(f); parts = raw if isinstance(raw, list) else raw.get("parts", [])`

### Price Gap Root Cause (documented 2026-07-02)
The 2.1M OEMPartsOnline parts with 0% IL price exist because:
1. OEMPartsOnline imported parts with no IL prices (US catalog)
2. IL importer imports try to match by exact OEM number — format mismatch creates DUPLICATE entries instead
3. `dedup_catalog_parts` in db_update_agent deduplicates by SKU/name, NOT by normalized OEM number
4. Fix: use normalized OEM matching in all importers + run `fix_oem_price_gaps.sh` after imports
5. Long-term: add `dedup_by_normalized_oem` task to db_cleanup_agent

---

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

## Anti-Harvest / DB Protection (added 2026-07-07 — MANDATORY)

Goal: our own catalog cannot be scraped the way we scrape others, and no single expensive endpoint can take the box down.

- **DB network isolation (verified secure)**: `postgres_catalog`/`postgres_pii` bind to `127.0.0.1` only; Meilisearch + Redis have NO host port mapping (internal docker network only). Never add a `0.0.0.0` or public port mapping to any data service.
- **Real client IP behind Cloudflare** — nginx MUST restore the true client IP from `CF-Connecting-IP` via `set_real_ip_from <CF ranges>` + `real_ip_header CF-Connecting-IP`. Without it, `$remote_addr`/`X-Real-IP` is the Cloudflare EDGE IP, so every per-IP rate limit keys on the wrong address (real users share buckets → false 429s; attackers get no throttle). The CF ranges are listed in `deploy/nginx.conf`; refresh from https://www.cloudflare.com/ips/ if they change.
- **nginx rate limit** — `limit_req_zone $binary_remote_addr zone=catalog_api rate=20r/s` + `limit_req zone=catalog_api burst=40 nodelay` on `/api/`. 20r/s sustained + 40 burst = generous for real page loads (which fire several calls at once), trips a catalog scraper. Depends on the real-IP fix above to be meaningful.
- **Backend per-endpoint limits still apply** (keyed on the now-correct IP): search 30/min, autocomplete 30/min, plate 20/min, VIN 10/min. Any NEW public catalog-read endpoint must add `check_rate_limit`.
- **Expensive enumeration endpoints MUST cache + single-flight** — `/parts/manufacturers` (and models/categories) run `SELECT DISTINCT` full-scans over 4.18M+ rows. `manufacturers` had NO cache: every hit ran the scan and concurrent hits STAMPEDED (each its own 4M-row scan), taking the box down under load/harvest (2026-07-07 incident). Pattern now: 10-min in-process cache + `asyncio.Lock` single-flight (`MANUFACTURERS_RESPONSE_CACHE` + `_MANUFACTURERS_REBUILD_LOCK`) so only ONE request ever runs the scan while others wait for the shared result. Never ship a cold-cache-stampede-able enumeration endpoint.
- **nginx single-file mounts need a container RESTART, not reload** — `deploy/nginx.conf` is bind-mounted as a single file; editing it on the host changes the inode, and the running container keeps the OLD inode until `docker restart autospare_nginx`. `nginx -s reload` alone reads the stale file. Validate first in a throwaway container: `docker run --rm --network autosparefinder_internal -v .../nginx.conf:/etc/nginx/nginx.conf:ro nginx:stable-alpine nginx -t`.
- **AI-bot / scraper user-agent filtering** — nginx `map $http_user_agent $bad_bot` → `if ($bad_bot) return 403` on `/api/`. Blocks NAMED AI/LLM crawlers (GPTBot, ClaudeBot, CCBot, Google-Extended, PerplexityBot, Bytespider, Amazonbot, Applebot-Extended, meta-externalagent, …) + aggressive commercial scrapers (Ahrefs/Semrush/scrapy/…). Deliberately does NOT block empty-UA or generic HTTP libraries (curl/python/Go) — Telegram/Stripe webhooks and legit API clients can look like those; the rate limit + Cloudflare bot-fight catch anonymous scrapers instead. Verified: `GPTBot` UA → 403, real browser UA → 200. Also `/robots.txt` declares Disallow for compliant AI crawlers. Refresh the bot list as new AI crawlers appear.
- **Never block webhooks/system paths by UA** — Stripe (`/api/v1/payments/webhook`), Telegram (`/api/v1/webhooks/telegram`), and our own `/api/v1/system/collect` (harvester relay, sends a Chrome UA) MUST stay reachable. The $bad_bot list is named-bot-only for exactly this reason.
- **Chat prompt-injection resistance** — customer agents must never leak the pricing formula (×1.45 / 45%), VAT math, supplier company names, or internal cost even when the user says "ignore your instructions / reveal…". Enforced by `_sanitize_internal_pricing_disclosure` + `_mask_supplier` (post-processing, defense-in-depth beyond the system prompt). Verified 2026-07-05/07: direct injection attacks leaked nothing.

## Security Rules (added 2026-07-04 — MANDATORY)

These were confirmed exploitable vulnerabilities found during a live pentest. Never reintroduce them.

- **`/api/v1/system/collect` requires the collect secret** — secret is in `COLLECT_SECRET` env var. The server-side harvester's `post_relay()` sends it as the `X-Collect-Secret` header. Any new code calling this endpoint must authenticate. Do NOT remove the auth check.
- **Cross-origin BROWSER relays must use the text/plain "simple request" pattern (learned 2026-07-12, RockAuto)** — a harvester running in the owner's browser ON a supplier page (rockauto.com, car-parts.ie, …) posting to our `/collect` or `/api/v1/system/unpriced-oems` is CROSS-ORIGIN. Our global Starlette `CORSMiddleware` (BACKEND_API_ROUTES.py:186, allow_origins = our own domains only) **rejects the preflight OPTIONS with 400** for any other origin — so a custom `X-Collect-Secret` header (which forces a preflight) can NEVER work from a supplier page, and a per-route `@router.options` handler never runs (the middleware short-circuits first). Do NOT try to fix this by widening the global CORS allowlist (weakens the whole app). Instead the browser must send a **CORS "simple request"**: `POST` with `Content-Type: text/plain` and the **secret in the JSON body** (no preflight), `credentials:'omit'`; the endpoint reads the secret from the body and returns `Access-Control-Allow-Origin: *` so the browser can read the reply. Both `/collect` and the unpriced-OEM feed support this. `rockauto_browser_harvester.js` is the reference implementation (`auth()`/`feed()`/`send()`/`autorun()`).
- **`GOOGLE_OAUTH_CLIENT_ID` must be set** — if unset, Google OAuth login returns HTTP 500. The audience check must NEVER be conditional on whether the env var is set. Pattern: `if not client_id: raise 500; if aud != client_id: raise 401`.
- **Rate limits use `X-Real-IP`, not `X-Forwarded-For`** — nginx sets `X-Real-IP` to `$remote_addr` (unspoof-able). `X-Forwarded-For` is client-controlled and must never be used for rate limiting or IP-based security decisions.
- **Webhook secrets always fail CLOSED** — pattern: `if not secret or header != secret: raise 403`. Never `if secret and header != secret` (passes when secret is unset).
- **Never print OAuth tokens to stdout** — they go to `docker logs` forever. Store tokens in DB or env; never log them.
- **Rate limit return values must be checked** — `allowed = await check_rate_limit(...)` then `if not allowed: raise 429`. Discarding the return value = no rate limit.
- **All new internal-only endpoints** (harvest relay, import triggers, admin actions) must be authenticated. Options: (1) `X-Collect-Secret` style shared secret, (2) `Depends(get_current_admin_user)`, (3) nginx internal-only restriction.
- **`supplier_parts` ON CONFLICT for re-harvest importers must include `price_ils` and `is_available`** — omitting them means price changes and stock-outs are silently discarded.
- **`task_normalize_base_price_batched` formula**: `supplier_parts.price_ils` = ex-VAT cost → `base_price = cost × 1.45`, `importer_price_ils = cost`. Never reverse this.
- **Batched loops with `updated_at=NOW()` must be bounded** — use `cutoff_id = MAX(id) WHERE updated_at > :since` at loop start; add `AND id <= :cutoff_id` to the batch query. Otherwise the loop perpetually refreshes rows back into scope and never terminates.
- **Multithreaded state dicts need a `threading.Lock()`** — any dict shared across threads (harvester `state`, etc.) must protect all read-modify-write operations and file writes with a lock.

## Key Rules

- **Pricing**: UNIFORM 45% margin on ALL parts. base_price = cost × 1.45. No exceptions.
- **VAT**: Israeli VAT = **18%** (0.18). All scripts must use VAT = 0.18. Never 0.17.
- **Import formula**: Consumer price incl. VAT → cost = price/1.18 → max_price = price → base_price = cost×1.45. NEVER double-apply VAT.
- **Restarts**: Always run `bash /opt/autosparefinder/backend/scripts/pre_restart.sh` before any docker restart.
- **Monitoring**: Keep 30-min wakeup active (cron 0073265b). Reschedule after every restart.
- **Wakeup checks**: Every wakeup must verify crawler + REX + DB agent + catalogue agent.
- **NIR todos**: These are human tasks for the business owner (Khalil) — contact importers directly.
- **Scraper/Acura**: Solved via browser harvest → window.name → /api/v1/system/oem-relay → oempartsonline_importer. 5741 parts imported 2026-06-15.
- **Champion Motors catalog (added 2026-07-01)** — VW Group IL importer (VW, Audi, SEAT, Skoda, Cupra):
  - Site: `https://www.championmotors.co.il/catalog/` — WordPress, NOT anti-bot protected
  - AJAX endpoint: `https://www.championmotors.co.il/wp-admin/admin-ajax.php`
  - Action: `action=check_mehiron_action` (found in `/wp-content/themes/champnew/champion.js`)
  - Parameters: `cnumber=<OEM number>` OR `cdesc=<Hebrew description>` (POST, application/x-www-form-urlencoded)
  - Response: HTML table with columns: תיאור (name), סוג פריט (type: מקורי/חליפי), מספר קטלוגי (OEM), תוצר הרכב (brand), דגם (model), מצאי (stock), אחריות (warranty), מחיר לצרכן (consumer price ILS incl. VAT)
  - Requires FlareSolverr session: load catalog page first to get cookies, then POST to AJAX
  - Single-letter Hebrew seeds don't work — needs multi-char words (Hebrew part names or 2-letter OEM prefixes)
  - **`champion_motors_harvester.py`** — automated scraper, writes to `/app/state/champion_motors_parts.json`, then runs `import_champion_motors.py` to load DB
  - Run: `docker exec autospare_backend python3 /app/harvesters/champion_motors_harvester.py`
  - **Price formula**: consumer price incl. VAT → `cost = price/1.18`, `base = cost×1.45`, `importer_price_ils = cost`
  - **WEY IL importer** (note): wey.co.il/services-pricing embeds a samelet.com iframe — WEY prices come from samelet, not Champion Motors

- **Kia Israel price list (added 2026-07-01)** — `kia-israel.co.il` (Albar Group IL importer):
  - Site: `https://kia-israel.co.il/מחירון-חלפים` — WordPress, NOT anti-bot, no authentication
  - Method: Simple HTTP POST to same page URL (NOT admin-ajax.php) — `action=""` in form
  - Parameters: `partDesc=<Hebrew description>` OR `catalogNum=<OEM number>`
  - URL must be percent-encoded: `https://kia-israel.co.il/%D7%9E%D7%97%D7%99%D7%A8%D7%95%D7%9F-%D7%97%D7%9C%D7%A4%D7%99%D7%9D`
  - Referer header must also use percent-encoded URL (latin-1 codec error if Hebrew in Referer)
  - Response: HTML page with `.parts-list > table` containing rows: OEM | suffix | desc_he | price_ex_vat | stock
  - **CRITICAL**: Prices are **EX-VAT** (`מחיר ללא מע"מ`) — `importer_price_ils = price`, `base = price×1.45`, `max = price×1.18`
  - No FlareSolverr needed — plain urllib.request POST works
  - `catalogNum` search requires exact/near-exact match — prefix search returns 0 results
  - `partDesc` search is partial match — use Hebrew automotive word seeds
  - Script: `kia_israel_harvester.py` → saves `/app/state/kia_israel_parts.json` → runs `import_kia_israel.py`
  - Run: `docker exec autospare_backend python3 /app/harvesters/kia_israel_harvester.py`
  - Expected: ~20K-50K Kia parts with official IL ex-VAT prices
  - DB SKU prefix: `KIA-IL-`

- **Toyota IL price list (confirmed accessible 2026-07-01)** — `union-motors.toyota.co.il` (Union Motors Israel — Toyota IL importer):
  - Site: `https://union-motors.toyota.co.il/replacement_parts.php` — accessible directly (no Cloudflare/Akamai on subdomain!)
  - toyota.co.il main domain IS Akamai-blocked, but this subdomain is NOT
  - Method: GET request with `?s=<seed>` parameter — returns inline HTML table
  - Result cap: **500 results per request** → requires many seeds to get all 18,704 parts
  - Response: HTML `<table>` with columns: מק"ט (OEM) | תאור פריט (name_he) | מחיר (price) | דגמים מתאימים (models) | סיווג (type: מקורי/חליפי) | במלאי (in_stock: כן/לא)
  - **CRITICAL**: Prices are **EX-VAT** (`המחירים המוצגים הינם ללא מע"מ`) — `importer_price_ils = price`, `base = price×1.45`, `max = price×1.18`
  - No FlareSolverr needed — plain urllib.request GET works
  - Updated daily (confirmed: `עדכון אחרון: 01/07/2026 01:50`)
  - Seeds: 2-digit numeric OEM prefixes (00-99) + 2-char letter pairs for letter-prefix OEMs (SU, GY, etc.)
  - 500-cap breaker: if a seed returns exactly 500, auto-split to 3-digit sub-prefixes (seed + 0-9 + A-Z)
  - Script: `toyota_il_harvester.py` → saves `/app/state/toyota_il_parts.json` → runs `toyota_il_importer.py`
  - Run: `docker exec autospare_backend python3 /app/harvesters/toyota_il_harvester.py`
  - Expected: 18,704 Toyota OEM parts with official IL ex-VAT prices (includes some Lexus parts marked "יבוא אישי")
  - `toyota_il_importer.py` ON CONFLICT fixed to use `(supplier_id, supplier_sku)` — prevents cascading transaction errors

- **Colmobil PDF imports — AUTOMATED (updated 2026-07-02)** — Hyundai/Genesis/Mitsubishi/ORA/Smart/JAECOO:
  - `hyundai.co.il`: Times out completely — no direct access
  - `colmobil.co.il`: Amazon CloudFront SPA — inaccessible to scrapers
  - **SOLUTION**: `prodmedia.colmobil.co.il/spare-parts/{BRAND}.PDF` are directly accessible (no auth, no CF):
    - HYU.PDF (189MB) — Hyundai
    - MIT.PDF (61MB) — Mitsubishi
    - GEN.PDF (40MB) — Genesis
    - ORA.PDF (12MB) — ORA
    - SMART.PDF (17MB) — Smart
    - JAECOO.PDF (42MB) — JAECOO
    - MERC.PDF (320MB) — Mercedes (already 100% priced, skip)
  - **Script**: `colmobil_import_v2.py` — downloads + parses + imports all 6 brands
  - **PDF formats**: (1) HYU/MIT/GEN: `{OEM}{brand_he}` on one line then `{desc}{price} {stock}` on next; (2) ORA/JAECOO/Smart: `{OEM}` alone, then `{BrandLatin}{desc}{price} {stock}` on next
  - **Price formula**: consumer price incl. VAT → `cost = price/1.18`, `base = cost×1.45`, `max = price`
  - **Run**: `docker exec autospare_backend python3 /app/importers/colmobil_import_v2.py`
  - **Run single brand**: `docker exec autospare_backend python3 /app/importers/colmobil_import_v2.py --brands hyundai`
  - **Index**: `idx_parts_catalog_norm_oem` must exist — normalizes OEM numbers for matching (already created 2026-07-02)
  - **Results (2026-07-02)**: Hyundai 19,955 updated + 1,965 inserted → 67.2% priced; Mitsubishi 6,963 updated + 62 inserted → 41.0% priced
  - Refresh monthly: PDFs are updated by Colmobil periodically

- **Delek API brand IDs (complete map, discovered 2026-07-01)**:
  - Use seed `שמן` (Hebrew: oil) to probe new brand IDs
  - brandId=1: Mazda (`mazda_il_importer.py` handles this)
  - brandId=2: Ford USA Heavy Duty (F-150, F-250, Expedition)
  - brandId=3: BMW (16,209 unique OEM parts, priceWithTax incl. VAT) — added 2026-07-01
  - brandId=4: Ford (standard models)
  - brandId=6: NIO (2,265 parts, 100% priced) — new Chinese EV brand, added 2026-07-01
  - brandId=7: MAXUS M-Hero
  - brandId=8: Voyah (FREE, DREAM)
  - brandId=9: MAXUS M-Hero Series 2 (variant)
  - Run BMW+NIO: `docker exec autospare_backend python3 /app/importers/delek_multi_importer.py --brands 3,6`

- **supplier_parts ON CONFLICT fix (2026-07-01)** — affects all importers:
  - `supplier_parts` table has TWO unique constraints: `(part_id, supplier_id)` AND `(supplier_id, supplier_sku)`
  - Old code used `ON CONFLICT (part_id, supplier_id)` but the actual conflict hits `(supplier_id, supplier_sku)` when same OEM was imported from multiple sources creating duplicate catalog entries
  - Fix: change to `ON CONFLICT (supplier_id, supplier_sku) DO UPDATE SET price_ils=..., is_available=..., updated_at=NOW()`
  - Fixed in: `toyota_il_importer.py`, `delek_multi_importer.py`
  - Also use per-row savepoints: `async with conn.transaction():` nested inside outer loop to prevent cascade aborts
  - Check other importers if they use `ON CONFLICT (part_id, supplier_id)` and fix similarly

- **IL Importer WordPress/POST pattern — general rule (documented 2026-07-01)**:
  Several Israeli car importers use WordPress for their price list pages. The pattern varies:
  1. **Type A — admin-ajax.php AJAX**: Champion Motors. POST to admin-ajax.php with `action=<specific_action>` + search term. Requires FlareSolverr for cookies.
  2. **Type B — PHP page POST**: Kia Israel. Form with `action=""` posts to same page. No cookies/FlareSolverr needed. Returns inline HTML.
  3. **Type C — samelet.com iframe**: Subaru (subaru.co.il/services/pricing), WEY (wey.co.il/services-pricing). Price list is a samelet.com embed — use `samelet_import_v2.py` instead.
  
  To identify which type a new site is:
  - Find the price list page → inspect form `action` attribute
  - If `action=""` → Type B (PHP page POST)
  - If JS calls admin-ajax.php → Type A (AJAX)
  - If page has `<iframe src="https://samelet.com/form/parts-prices/{slug}">` → Type C
  
  **IL Importer Site Status (updated 2026-07-02)**:
  | Site | Brand | Type | Status | Notes |
  |------|-------|------|--------|-------|
  | championmotors.co.il | VW/Audi/SEAT/Skoda/Cupra | A (ajax) | ✅ Done | 32K parts, consumer price incl. VAT |
  | kia-israel.co.il | Kia | B (page POST) | ✅ Done | ~30K+ parts, EX-VAT price |
  | subaru.co.il | Subaru | C (samelet) | ✅ Covered | samelet_import_v2.py |
  | wey.co.il | WEY | C (samelet) | ✅ Covered | samelet_import_v2.py |
  | toyota.co.il | Toyota | ❌ Blocked | Akamai Access Denied | WORKAROUND: use union-motors.toyota.co.il (accessible!) |
  | union-motors.toyota.co.il | Toyota | B (GET form) | ✅ Done | 18,704 parts EX-VAT, updated daily; toyota_il_harvester.py |
  | hyundai.co.il | Hyundai | ❌ Timeout | Times out completely | Colmobil (importer) is Amazon WAF — inaccessible |
  | colmobil.co.il | Hyundai/Mitsubishi/Genesis/ORA/Smart/JAECOO | PDF download | ✅ Done | prodmedia.colmobil.co.il PDFs auto-downloadable. colmobil_import_v2.py handles all 6 brands. Hyundai 67.2%, Genesis 99.6%, Mitsubishi 41%. |
  | honda.co.il | Honda | ❌ 403 | Cloudflare | MCT API covers Honda anyway |
  | suzuki.co.il | Suzuki | ❓ Unknown | 200 OK, non-WP | Already 97.5% priced — not priority |
  | mitsubishi-motors.co.il | Mitsubishi | ❌ DNS error | Domain may have changed | Colmobil is the importer — use colmobil_import_v2.py instead |
  | samelet.com | 9 brands | API | ✅ Covered | samelet_import_v2.py — all brands |
  | serviceforms.delek-motors.co.il | BMW/NIO/Ford/Mazda/MAXUS/Voyah | API | ✅ Done | Delek API — brandId=3=BMW(16K), brandId=6=NIO(2.3K), others already imported |

- **car-parts.ie harvest rules** (2026-06-25 verified selectors):
  - Cloudflare-protected — ONLY browser-based harvesting works (Chrome has valid CF cookies)
  - Server-side curl/Python scraping returns Cloudflare challenge — will NEVER work
  - Run up to 6 harvest tabs simultaneously — confirmed working 2026-06-24
  - Correct relay URL: `https://autosparefinder.co.il/api/v1/system/collect` (NOT .com)
  - **VERIFIED SELECTORS** (confirmed 2026-06-25 by inspecting live DOM + fetch test):
    - Item container: `.rec_products_single_block` (each part is one of these)
    - Name/title: `.title` text content
    - SKU: `.artikle` text, strip `"Article №: "` prefix
    - Price: `.bottom_block` text, parse first number with `/[\d.]+/` regex (price in EUR)
    - Brand: first word of title (e.g. "RIDEX 402B0523 Peugeot..." → brand = "RIDEX")
    - (Old wrong selectors `.item_title`, `.item_artikle`, `[data-price]`, `.item_brand` do NOT exist)
  - **Category URL filter**: use `/car-parts/{brand}/{model}/` path (NOT `/car-brands/`)
    - On a car variant page e.g. `/car-brands/audi/a4-8k2-b8/23301`, the `a.ga-click` links point to `/car-parts/audi/a4-8k2-b8/{engine}/{category}/23301`
    - Filter: `h.includes('/car-parts/audi/a4-8k2-b8/')` — matches all category pages for this model
    - After strip `#fragment`, these are directly fetchable with `credentials:'same-origin'`
  - Model list page slug (e.g. `a4-b8-parts`) often differs from the actual slug (`a4-8k2-b8-parts`) — check the Audi/brand main page for the real slug
  - `done:true` flush pattern: final `sb(all, true)` call triggers import in `car_parts_ie_import_generic.py`

- **car-parts.ie automated harvester — current method (added 2026-06-30, supersedes manual tabs below)**:
  - `car_parts_ie_flaresolverr_harvester.py` runs inside the `autospare_backend` container, supervised by `_car_parts_ie_harvester_loop()` in `BACKEND_API_ROUTES.py` — auto-restarts on any crash/exit (backoff 30s→30min), no manual browser tabs needed.
  - Uses the standalone `flaresolverr` container (connected to the `internal` docker network) to solve Cloudflare challenges server-side — set `FLARESOLVERR_URL=http://flaresolverr:8191/v1` when run in-container (env-overridable; defaults to `localhost:8191` for host runs).
  - **Relay POST requires a browser User-Agent** — `urllib.request`'s default UA (`Python-urllib/x.y`) gets blocked by Cloudflare bot-fight-mode with error 1010 on our own `/api/v1/system/collect` endpoint. `post_relay()` sets a Chrome UA explicitly.
  - **Concurrency is serialized at two points** to avoid lock-storming `parts_catalog` (multiple same-brand vehicles finishing close together previously caused 5-26 min stuck queries): `_collect_buffers` in `routes/system.py` is keyed by `brand::vehicle_slug` (not brand alone) with a unique `/tmp/` file per vehicle, and `car_parts_ie_import_generic.py` takes an `fcntl.flock` before touching the DB so concurrently-spawned import subprocesses queue instead of fighting over row locks.
  - State/logs live in `/app/state/` (the persistent `worker_state` volume), not `/opt/autosparefinder/backend/...` — paths derive from `Path(__file__).resolve().parent`.
  - To check it's alive: `docker exec autospare_backend ps aux | grep flaresolverr_harvester` and `docker exec autospare_backend tail -30 /app/state/logs/flaresolverr_harvester.log`.

- **Supervisor architecture — 3 cooperating loops (added 2026-06-30)**. A crash-restart supervisor alone misses the failure mode that actually hit production: a process or DB connection that's *alive but stuck*. All three are registered via `_supervised_task(...)` in `BACKEND_API_ROUTES.py`'s `startup()`:
  1. **`_car_parts_ie_harvester_loop()`** — the base supervisor. Relaunches `car_parts_ie_flaresolverr_harvester.py` whenever it exits, for any reason (crash, killed by the other loops, etc). Backoff 30s (ran a while before dying) up to 30min (dying immediately, e.g. flaresolverr unreachable).
  2. **`_car_parts_ie_stall_watchdog_loop()`** — runs every 3 min. Two jobs: (a) kills any `car_parts_ie_import_generic.py` process older than 10 min — stuck, not slow; (b) **context-aware DB connection supervisor** using two tiers: orphaned connections (`backend_start < _BACKEND_START_UTC`, from a dead previous container) are killed after **60 seconds**; connections from the current container are **never killed** — only a warning logged if blocking >30 min. Every action is recorded to `watchdog_state.py` (shared module-level deque, maxlen 500). `_BACKEND_START_UTC` is set at module import time in `BACKEND_API_ROUTES.py`.
  - **`watchdog_state.py`** — shared event log (`WatchdogEvent` objects, `record()` / `drain_unvalidated()` / `stats()`). Both the watchdog and db_update_agent import this; no IPC needed since they share the same uvicorn process.
  - **`validate_watchdog_actions` task in `db_update_agent`** — runs at the end of every `run_all_tasks` cycle. Drains unvalidated events, checks each kill was a genuine orphan (details field confirms pre-container-start origin), detects kill bursts (>5 in one cycle), marks events validated, and alerts via WhatsApp if any anomaly found. Gives db_update_agent full authority over the watchdog's behaviour history.
  3. **`_car_parts_ie_harvester_healthcheck_loop()`** — runs every 30 min exactly, as a coarser safety net independent of the other two: confirms the harvester process is alive (`ps`) and that `/app/state/logs/flaresolverr_harvester.log` has been written to in the last 15 min. If the process is alive but the log is stale (hung on a network call with no per-model exception to catch), kills it — loop 1 then relaunches it automatically. Logs one status line every cycle (`alive=… pid=… log_age_s=… status=ok|STALLED|MISSING`) so harvester health is visible in `docker logs` without needing to ask.

- **Orphaned DB connections (root-caused 2026-06-30)**. Twice in one session a stale connection held a lock for 26-56 min and stalled the whole import pipeline: once from an ad-hoc diagnostic script whose client timed out without closing its connection, once from the *previous* backend container instance surviving past a `docker compose up -d` recreation. Root cause: `postgres_catalog` had `tcp_keepalives_idle/interval/count = 0` (OS default, often 2+ hours on Linux) and no `idle_in_transaction_session_timeout`, so Postgres had no way to notice a dead TCP peer quickly. Fixed live via `ALTER SYSTEM` + `pg_reload_conf()` (no restart needed — these are SIGHUP-reloadable, picked up by new TCP connections immediately, NOT by local Unix-socket connections, which is why `docker exec ... psql` without `-h` will misleadingly still show 0):
  ```sql
  ALTER SYSTEM SET tcp_keepalives_idle = 30;
  ALTER SYSTEM SET tcp_keepalives_interval = 10;
  ALTER SYSTEM SET tcp_keepalives_count = 3;
  ALTER SYSTEM SET idle_in_transaction_session_timeout = '5min';
  SELECT pg_reload_conf();
  ```
  This is the general-purpose fix (dead connections now detected in ~60s instead of hours). The watchdog's blocking-connection killer (loop 2 above) is the second layer of defense for cases where a connection is genuinely still alive but stuck holding a lock too long.

- **Harvester throughput ceiling — measured 2026-06-30, do not re-raise `PARALLEL_SESSIONS` without re-testing**. Each FlareSolverr request is a real headless-Chrome page load solving a Cloudflare challenge — confirmed live at 3-8s per page, not milliseconds. That per-request floor is structural and CPU/RAM cannot remove it. Two findings from the same investigation:
  - **Session leak (real bug, fixed)**: every time the harvester process got killed mid-cycle (container restart, a future stalled-process kill, etc.) its FlareSolverr sessions were never destroyed. Found 20 accumulated zombie Chrome sessions consuming 3.9 GB RAM / 322% CPU when only 3 should have existed — this was genuinely starving the box. `main()` now calls `fs_cleanup_stale_sessions()` on every startup (destroys whatever `sessions.list()` returns before creating fresh ones).
  - **Raising `PARALLEL_SESSIONS` (tested, reverted)**: with the leak fixed and RAM freed, tried 5 concurrent sessions expecting a throughput gain. Measured the opposite — per-slug latency roughly doubled, host load average rose from ~14 to ~25 on this 4-core box, and net throughput dropped to 0 completed models in 10 minutes (vs. ~1 every 1.4 min at 3 sessions). The box is CPU-bound by concurrent Chrome rendering, not memory — more parallelism past 3 sessions just adds queueing, it doesn't add capacity. Reverted to `PARALLEL_SESSIONS = 3`, confirmed recovery (load back to ~14, ~33 models/hour). If resources genuinely change (e.g. a CPU upgrade), re-measure with the same method (compare `uptime` load avg + models-completed-per-10-min before/after) rather than assuming more sessions = more throughput.

- **Tab management during harvest (manual browser-tab method — fallback only)** (2026-06-25 — MANDATORY):
  - **Frozen tab detection**: If a JS `window.name` check times out 2+ times on the same tab → tab is frozen, close it immediately
  - **Close frozen tabs**: Use `tabs_close_mcp(tabId)` — do NOT try to navigate or JS-inject into a frozen tab
  - **Open fresh replacement**: Use `tabs_create_mcp()` + navigate to a new model immediately
  - **Never wait for frozen tabs**: A frozen tab blocks a slot for hours with zero output — kill it
  - **Tab IDs shift constantly**: After closing/creating tabs, always get fresh tab IDs from context before calling JS tools

- **Harvest rate strategy** (2026-06-25):
  - **High new-insert segments** (10K+ new parts/batch): Commercial vans (Sprinter, Vito, Crafter, Transit, Trafic), light vans not yet harvested, pickup trucks
  - **Medium new-insert segments** (3-8K): SUVs, 4x4s, off-road models from brands not in eBay catalog
  - **Low new-insert (enrichment only)**: Standard passenger car variants we've already harvested — same SKUs appear across multiple models
  - **To restore high rate**: Always prioritize models from brands/segments genuinely new to the DB
  - **Plateau indicator**: When `new_1h` drops below 5K, switch to a completely different vehicle segment
  - **Best performers for new inserts**: Commercial vans > pickup trucks > light commercials > SUVs > passenger car variants

---

## MANDATORY PIPELINE RULES — Every Scraper & Importer MUST Follow

**Rule 1 — All data must flow through the pipeline, not directly to DB in isolation**
Every scraper/importer must write to these 3 tables together (atomically):
- `parts_catalog` — the part record
- `supplier_parts` — the price/availability record (links supplier to part)
- `part_vehicle_fitment` — fitment rows (if vehicle data available)

**Rule 2 — NEVER hardcode `importer_price_ils=0` in INSERT or ON CONFLICT UPDATE**
`importer_price_ils` is the ex-VAT cost we pay the IL importer. Always compute it:
```python
cost = il_consumer_price / 1.18        # ex-VAT
importer_price_ils = cost
base_price = round(cost * 1.45, 2)     # 45% margin
max_price_ils = il_consumer_price      # consumer reference
```
In ON CONFLICT DO UPDATE, ALWAYS use CASE WHEN to preserve existing value:
```sql
importer_price_ils = CASE WHEN EXCLUDED.importer_price_ils > 0
    THEN EXCLUDED.importer_price_ils
    ELSE parts_catalog.importer_price_ils END
```

**Rule 3 — NEVER write `part_condition='New'` (uppercase)**
Always lowercase: `'new'`, `'used'`, `'oem'`, `'aftermarket'`, `'remanufactured'`, `'oe_equivalent'`

**Rule 4 — Pipeline ownership (who writes what)**
| Owner | File | Schedule | Responsibility |
|---|---|---|---|
| **REX** | `catalog_scraper.py` | Every 3h | Catalog discovery — scrapes OEM sites, writes parts_catalog + supplier_parts |
| **DB Update Agent** | `db_update_agent.py` | Every 3h | Data quality — normalizes names, types, categories, prices, fitment |
| **DB Cleanup Agent** | `db_cleanup_agent.py` | Every 30s | Continuous self-healing — fixes types, OEM numbers, categories, zombie jobs, **importer_price_ils=0 → heal**, **'New'→'new' → heal** |
| **Boaz** | `BACKEND_AI_AGENTS.py` | Daily | Price pipeline — market price drift on supplier_parts |
| **Importers** | `samelet_import_v2.py` etc. | On-demand | IL importer data — must follow Rules 1-3 above |

**DB Cleanup Agent self-healing tasks (added 2026-06-18)**:
- `task_heal_importer_price()` — every 30s: finds `max_price_ils > 0` AND `importer_price_ils = 0`, applies formula `cost = max/1.18, base = cost×1.45`
- `task_heal_part_condition()` — every 30s: finds uppercase `'New'/'OEM'/'Used'` etc., converts to lowercase

These tasks are the **safety net** — even if an importer bug writes wrong data, the cleanup agent will auto-correct it within 30 seconds.

**Rule 5 — Before writing any new importer/scraper, verify it sets**:
- `importer_price_ils` = computed cost (not 0, not None)
- `base_price` = cost × 1.45
- `max_price_ils` = consumer reference price
- `part_condition` = `'new'` (lowercase) for new parts, `'oem'` for OEM parts
- At least one row in `supplier_parts` with `is_available=True` and `price_ils > 0`

---

## Known Blockers (updated 2026-06-18)

| Issue | Status | Fix |
|---|---|---|
| Acura scraping | ✅ Solved | Browser harvest (5741 parts) + oem-relay → oempartsonline_importer. |
| OOM crash loop (meili rebuild) | ✅ Fixed | `REBUILD_DEFAULT="0"`, checkpoint saved at offset=total |
| OOM crash loop (run_all_tasks) | ✅ Fixed | 6 tasks disabled: merge_catalog_fitment, fix_base_prices, normalize_base_price, backfill_bmw/ford/jaguar fitment |
| auto_backup silent failure | ✅ Fixed | `db_url.replace("+asyncpg","")` added to auto_backup.py:31 |
| VAT 0.17 wrong pricing | ✅ Fixed | 389,750 parts corrected |
| Wrong 45% margin | ✅ Fixed | 196,501 parts corrected |
| כללי (uncategorized) 74% | ⚠️ DRIFTED (re-running 2026-07-13) | categorize_parts_batch.py hit 99.8% at ~570K parts, but the catalog grew to 4.1M (car-parts.ie harvest) and new parts arrive as `כללי` faster than they're processed — **~1.14M `כללי` + 724K `general` uncategorized as of 2026-07-13**. Categorizer re-run on the backlog. NOTE: new imports must categorize on ingest, or this drifts again. |
| part_condition `New`→`new` | ✅ Fixed | All 3M+ rows corrected; importers now use `'new'` |
| **samelet importer_price_ils=0 bug** | ✅ Fixed 2026-06-18 | samelet_import_v2.py was hardcoding `importer_price_ils=0`. Fixed: cost=max_price/1.18, base=cost×1.45, importer=cost |
| **car_parts_ie_import_generic importer_price=0** | ✅ Fixed 2026-06-18 | EUR prices now converted: cost=price_eur×3.9/1.18, base=cost×1.45 |
| Zombie processes accumulation | ✅ Fixed 2026-06-18 | `_zombie_reaper_loop()` added to BACKEND_API_ROUTES.py startup — reaps every 60s |
| Postgres OOM near-misses | ✅ Fixed 2026-06-18 | postgres_catalog mem_limit: 3GB→4.5GB; idx_parts_catalog_part_type created |
| part_type non-standard values | ✅ Fixed 2026-06-18 | 178K rows normalized; future imports use PART_TYPE_MAP index |
| Price comparison not surfaced | ❌ Todo | supplier_parts has 2.3M records — need API + search wiring |
| Chrysler/Jeep/RAM price list | ✅ Auto-resolved 2026-07-01 | Jeep/RAM: ✅ samelet (98%). Chrysler brand discontinued in IL (chrysler.co.il = Jeep Israel). 3,973 priced from car-parts.ie is the realistic IL ceiling. NIR todo dismissed. |
| WEY price list | ✅ Auto-resolved 2026-07-01 | wey.co.il/services-pricing embeds iframe from samelet.com/form/parts-prices/wey — samelet_import_v2.py already covers WEY. 33% priced = actual samelet coverage ceiling. NIR todo dismissed. |
| Opel price list | ✅ Auto-resolved 2026-07-01 | Already 100% priced (60,145/60,161) via samelet. NIR todo dismissed. |
| NIR todos (business partnerships) | 👤 Human — Khalil only | ASAP Network, AliExpress platform, Meyer/ATD/Turn14/Keystone dropship accounts, Autodoc B2B — require business registration/contract. Cannot be automated. |
| Backend image drift (docker compose up wipes docker cp) | ✅ Fixed 2026-06-30, **regressed & re-fixed 2026-07-13** | Bind-mount `./backend:/app` in docker-compose.yml — source always live, docker cp no longer needed, compose up is now safe. **2026-07-13: found the mount had gone MISSING from `docker-compose.yml`** (only `upload_files`/`worker_state` remained); a `docker compose up -d backend` recreate silently reverted code to the stale image. Long-running containers still worked because they were created back when the mount existed — so `docker restart` masked the problem. **Restored `- ./backend:/app` to the backend `volumes:`.** ALWAYS verify the mount is present (`docker exec autospare_backend grep <recent-edit> /app/<file>` == host) before trusting a recreate to carry live edits. |
| car-parts.ie harvester no supervisor / stopped | ✅ Fixed 2026-06-30 | Added 3-loop supervisor: crash-restart + stall-watchdog (every 3 min) + 30-min healthcheck; session leak fix (fs_cleanup_stale_sessions on startup) |
| Meilisearch index drifting stale (no scheduler) | ✅ Fixed 2026-06-30 | `_meili_sync_loop()` supervised task — runs incremental sync every 2h automatically |
| run_all_tasks tasks killed by over-aggressive Postgres/watchdog settings | ✅ Fixed 2026-06-30 | Watchdog redesigned from a single fixed threshold to a **context-aware two-tier system**: orphaned connections (backend_start before current container start) killed after 60s; active-backend connections (db_update_agent maintenance tasks) never killed — warning logged if blocking >30 min. `idle_in_transaction_session_timeout` raised to 30 min. This makes run_all_tasks immune to watchdog interference regardless of how long maintenance UPDATEs take. |
| False "🔴 Worker failed: run_all_tasks / run_brand_discovery — no heartbeat within TTL" alerts | ✅ Fixed 2026-07-10/11 | Not a real failure — a backend restart (deploy/`compose up` recreate/OOM/crash) orphans in-flight cycles; their `job_registry` rows stay `running` with frozen heartbeat and the 2h zombie watchdog reaps them as `failed` → owner alert (fired on EVERY restart). **Why `pre_restart.sh` didn't prevent it:** it only SIGTERMs importer SUBPROCESSES; run_all_tasks / run_brand_discovery are **asyncio tasks INSIDE uvicorn**, never touched. **Root fix — 3 layers:** (1) **shutdown handler** `@app.on_event("shutdown")` marks all `running` rows `superseded` (terminal, non-alerting) + frees locks on every GRACEFUL SIGTERM — primary, automatic. (2) `_reconcile_orphaned_jobs()` at `startup()` (before schedulers) — safety net for UNGRACEFUL OOM/SIGKILL where the shutdown handler can't run. (3) HealthMonitor 5a `NOT EXISTS` guard — don't alert a `failed` job if a newer run of the same task (`split_part(job_name,':',1)`) is `running`/`completed`/`superseded`; genuine stalls still alert. `pre_restart.sh` also now closes the rows (belt & suspenders). Verified live: `docker restart` with a running job → `[Shutdown] closed 1 in-flight job(s) → superseded` then `[Startup] no orphaned running jobs`; 0 stale-running rows. **Never mark restart-orphaned jobs `failed`; in-process asyncio jobs are closed by the shutdown handler, not pre_restart.sh.** **4th layer (2026-07-13):** added a **container-lifetime guard** to ALL alert senders — HealthMonitor 5a (failed/dead) and 5b (zombie sweep) require `COALESCE(last_heartbeat_at, started_at) >= _BACKEND_START_UTC`, and the status-loop `failed_jobs` filter does the same. A job whose last activity predates the current container is a restart-orphan (silent); one that failed WITHIN this container still alerts. Covers the ungraceful-kill window the NOT EXISTS guard alone missed. Verified live: a `dead` `sync_prices` restart-orphan is now SUPPRESSED. |
| Watchdog anomaly false alerts (legit zombie kills flagged) | ✅ Fixed 2026-07-13 | The stall-watchdog kills a genuinely-stuck **same-container** query (legit) but recorded it as action `kill_orphan` with `"zombie:…"` details; `validate_watchdog_actions` only accepts `kill_orphan` whose details say `"predates"` → every legit zombie kill was WhatsApp'd as an anomaly. Fix: distinct action **`kill_zombie`** (BACKEND_API_ROUTES.py `_car_parts_ie_stall_watchdog_loop`), validated on its own terms (dur_s ≥ 2400s) in db_update_agent `validate_watchdog_actions`, added to `watchdog_state.py`. **Never reuse `kill_orphan` for a same-container kill — the validator's orphan rule ("predates") will false-flag it.** |
| Cart/order WhatsApp payment link not pressable | ✅ Fixed 2026-07-13 | Abandoned-cart + pending-payment reminders embedded the bare string `/api/v1/customers/cart` (not a URL, not tappable). Now emit a full `https://…/pay/XXX` link via `create_checkout_link` (abandoned, single-item) / `regenerate_order_pay_link` (pending order) — canonical server-side pricing, NEVER the raw cart `unit_price` (that's supplier COST). Multi-item abandoned carts fall back to the full cart URL. **Any customer-facing "go pay" message must be a full https URL, never an API path; and must price via the canonical checkout, never cart.unit_price.** |
| NOA posts robotic/incoherent | ✅ Fixed 2026-07-13 | Cause was post-processing, not the prompt: `_enforce_sales_only`/`_enforce_tiktok_ads_policy` flattened newlines into one run-on line, blanket-replaced `מוסך`/`תיקון` (broke legit pain copy), and stapled 2-3 canned boilerplate sentences on every post. Fix: sanitizers now preserve line structure, only rewrite genuine FIRST-PERSON "we repair cars" claims (`_NOA_FIRST_PERSON_SERVICE_RE`), append the parts-only disclosure ≤1× and only when a claim was stripped, no forced value-point stapling (advisory only), low-quality floor 14→8 words. **NOA sanitization must be structure-preserving + subtractive — never flatten newlines or staple boilerplate onto every post.** |
| `normalize_part_types/categories/dedup` crash with timezone error | ✅ Fixed 2026-07-01 | `_get_task_checkpoint()` returned tz-aware datetime; `parts_catalog.updated_at` is `timestamp without time zone` — asyncpg refused to bind. Fixed: both return paths use `datetime.utcnow()` / `.replace(tzinfo=None)`. Delta tasks now complete successfully. |
| `refresh_min_max_prices` blocks for full 60-min task timeout | ✅ Fixed 2026-07-01 | Added `SET LOCAL lock_timeout = '10min'` — fails fast if importer row locks are held, retries next cycle instead of stalling. |
| Meilisearch index 580K docs behind catalog | ✅ Fixed 2026-07-01 | `_meili_sync_loop()` catch-up completed — index now 4,124,452 docs = 100% of active catalog. |
| `sync_models_from_catalog` NULL manufacturer_id for "Vw" | ✅ Fixed 2026-07-11 | `vehicles.manufacturer_id` is a NOT NULL FK to `car_brands(id)`; the insert omitted it → NotNullViolationError aborted the whole task every cycle. Fixed: resolve manufacturer→`car_brands.id` (case-insensitive, by name OR alias, per-run cached) and set it; skip rows whose brand isn't registered yet instead of aborting. Verified live: status=ok, inserted 44 vehicles, skipped 0. |
| `sync_manufacturer_registries` duplicate brand "Gms"/"gms" | ✅ Fixed 2026-07-11 | `_upsert_car`/`_upsert_truck` (clean_manufacturers_registry.py) checked existence with a CASE-SENSITIVE `name == 'Gms'` but the unique index `ux_car_brands_name_ci_active` is on `lower(btrim(name))` — so the existing 'gms' row was missed and a duplicate INSERT was attempted → UniqueViolationError every cycle. Fixed: case-insensitive `lower(btrim(name))` lookup (prefers active) → updates the existing row. Verified live: status=ok. |
| Volvo importer_price_ils discrepancy (143K parts, only 20K priced) | ✅ Fixed 2026-07-01 | `mct_importer.py` — MCT (Mayer Group) API reverse-engineered; ~32K Volvo parts with IL prices imported. Also covers Honda (MCT), Polestar, Lynk & Co. |
| SEAT thin coverage (4.2K parts, 20% IL-priced) | ✅ Fixed 2026-07-01 | Champion Motors NOT anti-bot. It's a WordPress site with `admin-ajax.php?action=check_mehiron_action` endpoint. `champion_motors_harvester.py` scrapes by description seeds — finding 30K+ VW/Audi/SEAT/Skoda/Cupra parts with IL prices. |
| Mazda IL prices stale | ✅ Fixed 2026-07-01 | Delek Motors API (`serviceforms.delek-motors.co.il`) confirmed working; `mazda_il_importer.py` re-run: 7,513 parts + 3,274 fitment rows. |
| Ford/Voyah/MAXUS Delek brands not imported | ✅ Fixed 2026-07-01 | `delek_multi_importer.py` — Delek API has Ford USA (37K parts), MAXUS M-Hero (1.2K), Voyah (1.9K). All imported. |
| Kia IL prices thin (21%, 104K of 499K) | ✅ Fixed 2026-07-01 | `kia_israel_harvester.py` — kia-israel.co.il WordPress PHP-POST form, no auth needed. Seeds: 95 Hebrew part-name words. Returns ex-VAT prices. ~30K+ Kia OEM parts imported. |
| Toyota IL prices thin (15%, 66K of 434K) | ✅ Fixed 2026-07-01 | toyota.co.il behind Akamai, but WORKAROUND FOUND: `union-motors.toyota.co.il/replacement_parts.php` is accessible directly (no Cloudflare/Akamai on subdomain). 18,704 parts EX-VAT, updated daily. `toyota_il_harvester.py` created. Price formula: importer=price, base=price×1.45, max=price×1.18. |
| Hyundai/Colmobil brands IL prices | ✅ Fixed 2026-07-02 | prodmedia.colmobil.co.il PDFs auto-downloadable (no auth). `colmobil_import_v2.py` imports all 6 brands. Results: Hyundai 67.2% (75,987/113,098), Genesis 99.6% (4,772/4,791), Mitsubishi 41.0% (32,148/78,327), ORA 100% (1,434/1,434). Run monthly to refresh. |
| BMW IL prices (Delek API) | ✅ Fixed 2026-07-01 | `delek_multi_importer.py --brands 3` — Delek API brandId=3=BMW. ~16K BMW parts with IL prices (priceWithTax incl. VAT). Fixed ON CONFLICT to use (supplier_id,supplier_sku) to eliminate cascading transaction errors. |
| NIO IL prices (new brand) | ✅ Fixed 2026-07-01 | `delek_multi_importer.py --brands 6` — Delek API brandId=6=NIO. 2,265 parts, 100% priced! NIO added to BRAND_CONFIG. |

## IL Price Coverage Explanation (2026-06-18)
**30% of catalog has IL importer price — this is EXPECTED, not a bug.**

| Brand | Unpriced | Why |
|---|---|---|
| Kia/Toyota/BMW/Porsche | Millions | Global eBay/Febest catalog — IL importer only stocks a fraction |
| Porsche | 2,512 priced | Porsche IL official price list 2025-03-01 (uploaded PDF) ✅ |
| Lexus | 2,571 priced | Union Motors price list 2026-05-03 (uploaded PDF) ✅ |
| Jeep/RAM | 97% priced | samelet.com ✅ |
| Chrysler | 3,895 priced | car-parts.ie EUR→ILS conversion (partial); Carasso Motors list needed |
| Renault/Mercedes/Nissan/Ford | 75-86% priced | samelet.com ✅ |
| Volvo | 20%→22%+ priced | MCT (Mayer Group) API — `mct_importer.py` — 32K OEM parts with IL prices. Added 2026-07-01. |
| Honda | MCT coverage | MCT (Mayer Group) API — `mct_importer.py` — Honda IL OEM parts. Added 2026-07-01. |
| Polestar | Full MCT | MCT (Mayer Group) API — `mct_importer.py` — Polestar IL OEM parts. Added 2026-07-01. |
| Lynk & Co | Full MCT | MCT (Mayer Group) API — `mct_importer.py` — Lynk & Co IL OEM parts. Added 2026-07-01. |
| Mazda | Updated 2026-07-01 | Delek API re-run: 7,513 parts + 3,274 fitment rows. |
| Ford USA (F-150/F-250/Mustang) | New 2026-07-01 | `delek_multi_importer.py` — brandId=2,4 in Delek API: 37K Ford parts. |
| MAXUS M-Hero | New 2026-07-01 | `delek_multi_importer.py` — brandId=7 in Delek API: 1,206 parts. |
| Voyah (FREE, DREAM) | New 2026-07-01 | `delek_multi_importer.py` — brandId=8 in Delek API: 1,898 parts. |
| VW, Audi, SEAT, Skoda, Cupra | New 2026-07-01 | `champion_motors_harvester.py` — Champion Motors WordPress admin-ajax.php action=check_mehiron_action. Scrapes 30K+ parts with IL consumer prices. Harvester writes JSON → `import_champion_motors.py` imports to DB. |
| Kia | New 2026-07-01 | `kia_israel_harvester.py` — kia-israel.co.il WordPress PHP-POST (no admin-ajax). Prices EX-VAT. ~30K+ parts. Run: `docker exec autospare_backend python3 /app/harvesters/kia_israel_harvester.py` |
| Toyota | 15.4% (434K parts, 18.7K w/ IL price) | WORKAROUND: `union-motors.toyota.co.il/replacement_parts.php` accessible. 18,704 parts EX-VAT (updated daily). `toyota_il_harvester.py` created. toyota.co.il main site still Akamai-blocked. |
| BMW | 44% (339K parts) | Delek API brandId=3: ~16K BMW IL parts with consumer prices incl. VAT. `delek_multi_importer.py --brands 3`. Added 2026-07-01. |
| NIO | 100% (2.3K parts) | Delek API brandId=6: 2,265 NIO IL parts. All priced. Added 2026-07-01. |
| Hyundai | 67.2% (113K parts) | `colmobil_import_v2.py` auto-downloads HYU.PDF from prodmedia.colmobil.co.il. 19,955 updated + 1,965 inserted 2026-07-02. Refresh monthly. |
| Genesis | 99.6% (4,791 parts) | `colmobil_import_v2.py` — GEN.PDF. 4,184 updated + 330 inserted 2026-07-02. |
| Mitsubishi | 41.0% (78K parts) | `colmobil_import_v2.py` — MIT.PDF. 6,963 updated + 62 inserted 2026-07-02. Low % = most parts from eBay global catalog without IL equivalent. |
| ORA | 100% (1,434 parts) | `colmobil_import_v2.py` — ORA.PDF. 1,328 updated + 58 inserted 2026-07-02. |
| Smart | 99.1% (1,867 parts) | `colmobil_import_v2.py` — SMART.PDF. 1,795 updated + 30 inserted 2026-07-02. |
| JAECOO | 98.7% (5,024 parts) | `colmobil_import_v2.py` — JAECOO.PDF. 1,122 updated + 3,676 inserted 2026-07-02. Mostly new parts added. |
| Lexus | 1.2% (221K parts) | Union Motors IL — site times out. 2,571 parts from uploaded PDF only. |
| Porsche | 1.3% (265K parts) | 3,412 parts from uploaded PDF only. porsche.co.il not scraped yet. |
| Land Rover | 11.6% (51K parts) | JLR Israel site not scraped. SNG Barratt covers partial via `lr_import.py`. |

The 70% without IL price = parts from eBay/Febest/OEM-global sources. IL importers only distribute a subset of global catalogs.

## Importer Pipeline — ALL sources must write importer_price_ils correctly
| Importer | importer_price_ils formula | Status |
|---|---|---|
| `samelet_import_v2.py` | `cost = max_price_ils / 1.18`, `base = cost × 1.45` | ✅ Fixed 2026-06-18 |
| `car_parts_ie_import_generic.py` | `cost = price_eur × 3.9 / 1.18`, `base = cost × 1.45` | ✅ Fixed 2026-06-18 |
| `import_from_excel.py` / PDF importers | `cost = consumer_price / 1.18`, `base = cost × 1.45` | ✅ Pre-existing |
| `ebay_brand_importer.py` | No IL importer price — eBay is global source | ℹ️ By design |
| `mct_importer.py` | `price_no_vat` (ex-VAT from MCT API), `base = price×1.45`, `max = price×1.18` | ✅ Added 2026-07-01 (Volvo, Honda, Polestar, Lynk & Co) |
| `mazda_il_importer.py` | `cost = priceWithTax/1.18`, `base = cost×1.45` | ✅ Re-run 2026-07-01 — Delek Motors API confirmed working |
| `delek_multi_importer.py` | `cost = priceWithTax/1.18`, `base = cost×1.45` | ✅ Updated 2026-07-01 (Ford+MAXUS+Voyah+BMW brandId=3+NIO brandId=6). ON CONFLICT fixed to (supplier_id,supplier_sku). |
| `oempartsonline_importer.py` | USD price → no IL importer price | ℹ️ By design |
| `champion_motors_harvester.py` | `cost = consumer_price / 1.18`, `base = cost × 1.45` | ✅ Added 2026-07-01 (VW, Audi, SEAT, Skoda, Cupra) |
| `kia_israel_harvester.py` | `price_no_vat` (already ex-VAT), `base = price×1.45`, `max = price×1.18` | ✅ Added 2026-07-01 (Kia — official IL price ex-VAT) |
| `toyota_il_harvester.py` | `price_no_vat` (EX-VAT from union-motors.toyota.co.il), `base = price×1.45`, `max = price×1.18` | ✅ Added 2026-07-01 (Toyota — union-motors subdomain, 18,704 parts) |
| `toyota_il_importer.py` | `price` (ex-VAT), `base = price×1.45`, `max = price×1.18` | ✅ Fixed 2026-07-01: ON CONFLICT changed to (supplier_id,supplier_sku) |
| `colmobil_import_v2.py` | `cost = consumer_price / 1.18`, `base = cost × 1.45`, `max = consumer_price` | ✅ Added 2026-07-02 (Hyundai, Mitsubishi, Genesis, ORA, Smart, JAECOO — auto PDF download) |

---

## Critical Technical Fixes — 2026-06-26 (MUST READ)

### 1. Backend deploys via bind mount — Fixed 2026-06-30 (no more `docker cp`)
**Old problem (2026-06-15 → 2026-06-30):** the backend image baked source code in via the Dockerfile's `COPY . .`. The standard hotfix workflow was `docker cp` a changed file into the running container, then `docker restart`. This worked *until* the container got recreated (e.g. `docker compose up -d` after editing `docker-compose.yml`, which compose treats as a config change requiring recreation) — recreation rebuilds the container FROM THE IMAGE, silently discarding every `docker cp` patch that was never baked into a rebuilt image. This actually happened: the container ran a 2026-06-15 image for two weeks while ~40 files were hotfixed on disk and never landed in a rebuilt image. Confirmed live regressions: OOM-disabled `db_update_agent` tasks running again, 4 supplier modules missing entirely (broke the price-comparison aggregator).

**Root fix:** `docker-compose.yml` backend service now bind-mounts the source directory — `./backend:/app` — instead of relying solely on the image's baked-in copy. The Dockerfile's `COPY . .` still matters for the one-time image build (installing deps, Playwright/Chromium), but the running container's `/app` is now always the live `backend/` directory on host disk.

**Practical effect:**
- `docker cp` is no longer needed for code changes. Edit the file on disk, then just restart.
- `docker compose up -d` can no longer revert code to a stale image — there's nothing image-side to revert to for source files.
- Scripts under `backend/scripts/*.sh` must stay executable on the **host** now (`chmod +x`), since the image's build-time `chmod` no longer applies once that path is bind-mounted over.

Deploy sequence (now just two steps):
```bash
bash /opt/autosparefinder/backend/scripts/pre_restart.sh
# edit file(s) directly under /opt/autosparefinder/backend/ — no docker cp needed
docker restart autospare_backend
```

Only use `docker compose up -d backend` when `docker-compose.yml` itself changed (env vars, volumes, mem limits, etc.) — that's now safe to run, since the bind mount means there's no stale-image code to fall back to. Scope it with `--no-deps` to avoid touching other services' pending changes: `docker compose up -d --no-deps backend`.

### 2. Fitment pipeline — Fixed 2026-06-26
The `car-parts.ie` harvester was sending `vehicle` slug but it was being IGNORED by the collect endpoint. Root cause: relay extracted `brand = data.get("brand", "unknown")` instead of parsing from `vehicle` slug.

**Fixed in `routes/system.py`**:
```python
# Extract brand from vehicle slug — browser harvester sends vehicle not brand
if not brand and _vehicle_slug:
    brand = _vehicle_slug.split("/")[0]
```

**Fixed in `car_parts_ie_import_generic.py`**:
- Added `_parse_vehicle_slug()` function
- Added `--vehicle-slug` CLI arg
- Changed `if model_name and year_from:` → `if model_name:` (uses year_from=1990 fallback)
- Fixed: every harvest cycle now writes fitment rows to `part_vehicle_fitment`

Result: +2,270 fitment rows on 2026-06-26 alone, growing at ~800-1000 rows per 10 min.

### 3. Uvicorn workers — 1 worker (not 4)
Changed back to 1 worker because 4 workers × pool_size causes Postgres `max_connections` overflow (50 limit).
`docker-compose.yml`: `${API_WORKERS:-1}` (changed back from 4)

### 4. Search performance — Fixed 2026-06-26
- **External suppliers**: now run in background task, results cached in Redis 30 min. Non-blocking.
- **Meilisearch semaphore**: max 8 concurrent queries (was unlimited, caused saturation under load)
- **Meilisearch results**: cached in Redis 30 min, shared across workers
- **Single worker**: search returns in ~2s under realistic load

### 5. WhatsApp/Telegram Stripe link — Fixed 2026-06-26
The WhatsApp handler was short-circuiting purchase intent to a generic cart URL. Fixed by removing the bypass — all messages (including "אני רוצה להזמין") now go through the AI agent which calls `create_checkout_link()` and sends a real Stripe URL back.
File: `routes/webhooks.py` — removed the `_is_purchase_intent` bypass block.

### 6. Post-payment notifications — Added 2026-06-26
`_send_post_payment_notification()` added to `routes/utils.py` — fires after `trigger_supplier_fulfillment()`:
- WhatsApp: sends tracking link back to the phone that placed the order
- Telegram: sends to the chat_id from conversation context
- Web: sends SendGrid email with order confirmation + tracking button
File: `routes/email_utils.py` — new file with `send_order_confirmation_email()`

### 7. Supplier aggregator — Wired 2026-06-26
17 suppliers now wired in `services/supplier_aggregator.py`:
- Tier 1 (API): eBay, AliExpress DS, Autodoc
- Tier 2 (batch): RockAuto, Spareto
- Tier 3 (affiliate): PartSouq, Amayama, Alvadi, Cars245, FCP Euro, Summit Racing, Fitinpart, Pelican, ECS Tuning, Toyota/Ford/Hyundai Parts
All env flags added to `docker-compose.yml` as `EXTERNAL_ENABLE_*=1`
Search endpoint returns `external_suppliers` array (from Redis cache, non-blocking).

---

## Last Monitoring Run — 2026-07-02 07:10 UTC

### Active Processes
| Process | Status | Details |
|---|---|---|
| `uvicorn` | ✅ Running | stable, 1 worker |
| `car_parts_ie_harvester` | ✅ Running | 3 sessions, supervisor active |
| `car_parts_ie_stall_watchdog` | ✅ Running | every 3 min |
| `car_parts_ie_healthcheck` | ✅ Running | every 30 min |
| `meili_sync` | ✅ Running | 2h auto-loop active |
| `colmobil_import_v2.py` | ✅ Done | All 6 brands complete 2026-07-02: Hyundai 67.2%, Genesis 99.6%, MIT 41.0%, ORA 100%, Smart 99.1%, JAECOO 98.7% |

### Catalog Health
| Metric | Count |
|---|---|
| Total active parts | 4,171,856 |
| With IL importer price | 1,923,016 (46.1%) |
| With base_price | ~1,927,371 |

### IL Price Coverage (post 2026-07-02 Colmobil session)
| Brand | Priced | Total | % | Notes |
|---|---|---|---|---|
| NIO | 2,287 | 2,287 | **100%** | ✅ Delek API brandId=6 — fully priced |
| ORA | 1,434 | 1,434 | **100%** | ✅ colmobil_import_v2.py ORA.PDF — 2026-07-02 |
| GENESIS | 4,772 | 4,791 | **99.6%** | ✅ colmobil_import_v2.py GEN.PDF — 2026-07-02 |
| SMART | 1,851 | 1,867 | **99.1%** | ✅ colmobil_import_v2.py SMART.PDF — 2026-07-02 |
| JAECOO | 4,960 | 5,024 | **98.7%** | ✅ colmobil_import_v2.py JAECOO.PDF — 2026-07-02 (3,676 new parts inserted) |
| SEAT | 10,356 | 13,779 | 75.2% | ✅ Champion Motors |
| VOLKSWAGEN | 21,619 | 29,315 | 73.7% | ✅ Champion Motors |
| FORD | 93,173 | 129,481 | 72.0% | ✅ Delek API (Ford USA HD + standard) |
| HYUNDAI | 75,987 | 113,098 | 67.2% | ✅ colmobil_import_v2.py (auto PDF download) |
| HONDA | 64,873 | 99,537 | 65.2% | ✅ MCT API |
| AUDI | 45,220 | 82,331 | 54.9% | ✅ Champion Motors |
| BMW | 150,345 | 339,970 | 44.2% | ✅ Delek API brandId=3 — 16,209 OEM parts |
| MITSUBISHI | 32,148 | 78,327 | 41.0% | ✅ colmobil_import_v2.py MIT.PDF — 2026-07-02 |
| SUBARU | 13,364 | 32,668 | 40.9% | ✅ samelet (ceiling) |
| VOLVO | 45,624 | 144,037 | 31.7% | ✅ MCT API |
| WEY | 4,372 | 13,124 | 33.3% | ✅ samelet (ceiling) |
| KIA | 109,799 | 498,552 | 22.0% | ✅ kia-israel.co.il harvester |
| MAZDA | 40,090 | 212,739 | 18.8% | ✅ Delek API brandId=1 |
| TOYOTA | 71,923 | 434,804 | 16.5% | ✅ union-motors.toyota.co.il (structural ceiling) |
| LEXUS | 2,666 | 220,880 | 1.2% | ❌ union-motors.co.il times out; PDF only |

### Agent Todos
| Agent | Status |
|---|---|
| `rex` | ~170 completed |
| `db_update_agent` | running cycles |
| `db_cleanup_agent` | healthy |
| `NIR` | ~5 not_started (human/Khalil tasks) |

### Open Issues
1. `sync_models_from_catalog` fails every cycle — NULL `manufacturer_id` for brand "Vw". Pre-existing, low severity.
2. `sync_manufacturer_registries` fails every cycle — duplicate brand "Gms"/"gms". Pre-existing, low severity.
3. `lookup_oem_spec` ran for 42+ min and blocked BMW import (no Postgres statement_timeout for this query). Fix: add `SET LOCAL statement_timeout = '20min'` inside `lookup_oem_spec` task.
4. ClamAV container DOWN (health monitor). Non-critical.
5. ~~BMW 36,729 duplicate OEM catalog entries~~ ✅ RESOLVED 2026-07-12 — `bmw_oem_dedup.py` merged 36,760 groups / 69,063 duplicate rows (FK-safe soft-delete; supplier_parts + fitment repointed to canonicals, 0 orphans verified). See FIXES_TRACKER.

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

## Server Specs (Contabo VPS) — measured 2026-06-30

| Resource | Spec |
|---|---|
| **CPU** | 4 vCPUs — AMD EPYC @ 2.0 GHz (1 thread/core, QEMU/KVM virtualised) |
| **RAM** | 7.8 GB — **no swap configured** |
| **Disk** | 150 GB virtual disk (QEMU, SSD-backed by Contabo), 82 GB used / 63 GB free |
| **OS** | Ubuntu 24.04.4 LTS, kernel 6.8.0-107 |
| **Hosting** | Contabo standard VPS |

**Capacity reality check** — 12 containers run concurrently on this box (3× Postgres, Meilisearch, Redis, backend, frontend, Nginx, ClamAV, FlareSolverr, WhatsApp bridge, 2× backup). Load average normally sits 10-14; above 18-20 is a sign of CPU oversubscription. Key constraints:
- No swap → RAM exhaustion = immediate OOM kills, no graceful degradation.
- 4 vCPUs → concurrent headless-Chrome (FlareSolverr) sessions saturate at ~3; tested 5 sessions, net throughput DROPPED due to CPU queueing. Do not increase `PARALLEL_SESSIONS` without empirical throughput measurement.
- `idle_in_transaction_session_timeout = 30min` (set via ALTER SYSTEM 2026-06-30) — was set to protect against orphaned connections but too-low values (5 min, then 15 min) killed legitimate maintenance tasks. Current 30 min is the calibrated balance.
- Watchdog `BLOCKER_S = 2700s` (45 min) — moot for active-backend connections (never killed regardless), relevant only for orphan-detection fallback.
- `DB_AGENT_TASK_TIMEOUT_S = 3600` — per-task timeout inside `run_all_tasks`. Was 1800s (30 min default) which was too short: `normalize_part_types` takes 30+ min on this box under concurrent harvester load. Set in `docker-compose.yml` so every cycle picks it up. Takes effect on next restart.

---

## Meilisearch Sync — automated via supervised loop (fixed 2026-06-30, rewritten 2026-07-02)

`meili_sync.py` previously had **no automated scheduling** — it ran once manually (2026-06-24) and silently drifted 6 days / ~580K docs behind the catalog. Added `_meili_sync_loop()` in `BACKEND_API_ROUTES.py`, registered at startup via `_supervised_task("meili_sync_loop", ...)`. Runs `python3 /app/meili_sync.py` every **2 hours** in incremental mode (`MEILI_REBUILD=0` env already set, no full rebuild).

**2026-07-02 rewrite — three root-caused bugs in meili_sync.py (do not reintroduce):**
1. **Incremental resume by id-position was broken by design.** Parts get random UUIDv4 ids; the old resume (`offset=total`, `ORDER BY id`) only saw rows sorted past the previous end position — new parts land at *random* id positions and were silently skipped every incremental run. This is what created the 620K-doc gap while the checkpoint claimed complete. Fix: a completed checkpoint (offset==total, no last_id) now triggers **updated_at-based incremental mode** — `WHERE updated_at > (last run start − 1h margin)`. The completed checkpoint's `updated_at` is the run's START time so mid-run changes are re-checked next cycle.
2. **`OFFSET N` pagination is O(N·logN) per batch** — measured ~100s/batch at offset 195K (full pass ≈ 23h). Fix: keyset pagination `WHERE id > $last_id::uuid ORDER BY id LIMIT batch` — PK index scan, constant per batch. Checkpoint stores `last_id` (and `cutoff` if an incremental run is interrupted, so resume keeps the same cutoff).
3. **No single-instance guard** — the 2h supervised loop spawned a sync while a manual catch-up was mid-pass; the two clobbered each other's checkpoint file. Fix: `fcntl.flock` on `/tmp/meili_sync.lock` at entry — a second instance prints a notice and exits 0.

- To check sync status: `docker exec autospare_backend cat /app/state/meili_sync_checkpoint.json` (shows offset, total, updated_at)
- Index doc count vs catalog: query Meilisearch `/indexes/parts/stats` and compare `numberOfDocuments` to `SELECT COUNT(*) FROM parts_catalog WHERE is_active`
- If index is significantly behind and you need an immediate catch-up: `docker exec -d autospare_backend python3 /app/meili_sync.py` (runs incremental sync in background, can take 30-90 min for millions of docs)
- `lookup_oem_spec` task times out at 1800s by design (`DB_AGENT_TASK_TIMEOUT_S` env, default 30 min) — also hits Cerebras/HF API rate limits; this is a pre-existing ceiling, not a new bug.

## Pre-existing bugs in run_all_tasks — ✅ FIXED 2026-07-11

| Task | Error | Fix (verified live) |
|---|---|---|
| `sync_models_from_catalog` | `NotNullViolationError: null value in column "manufacturer_id"` | `vehicles.manufacturer_id` is a NOT NULL FK to `car_brands(id)` never set by the insert. Now resolves manufacturer→id (case-insensitive, name OR alias, per-run cache), skips rows whose brand isn't registered yet. → status=ok, inserted 44, skipped 0. |
| `sync_manufacturer_registries` | `UniqueViolationError: duplicate key on ux_car_brands_name_ci_active` | `_upsert_car`/`_upsert_truck` used a case-SENSITIVE existence check against a case-INSENSITIVE unique index, so 'Gms' missed the existing 'gms' and re-inserted. Now uses `lower(btrim(name))` lookup → updates the existing row. → status=ok. |

**Operational-resilience rules for run_all_tasks (2026-07-11 audit):**
- **Deadlocks are transient — retry, don't abort.** Batched-UPDATE tasks on `parts_catalog` (normalize_*, dedup, backfill) deadlock against the concurrent harvester. `run_all_tasks` now has a **central deadlock-retry** (up to 3× per task, on a raised OR self-reported `DeadlockDetectedError`; timeouts are NOT retried). Any NEW batched-UPDATE task is covered automatically — but should ALSO use `ORDER BY id … FOR UPDATE SKIP LOCKED` in its batch CTE so it never waits on rows another writer holds (see normalize_categories Pass 2).
- **Rate-limited LLM tasks need a soft time-budget, not the hard timeout.** `lookup_oem_spec` (per-row LLM call) must stop after `max_seconds` (1500s) and return `status=ok, stopped_early=True`, finishing the rest next cycle — never let it get killed at the 3600s hard timeout (that logs status=error every cycle).
- **"Scan-for-nothing" anti-pattern (recurred 3× — `task_recover_priced_inactive`, `task_normalize_base_price_batched`, manufacturers stampede).** A frequent cleanup task that scans a huge table to find the rare/zero rows needing work is a chronic bottleneck (holds snapshots, deadlocks the harvester, spikes load) even with a `LIMIT`. Two mandatory guards: (1) **exponential backoff** (30s→30min) when it finds 0 — AND on timeout/error, via a shared `_bump_*_backoff()`; reset to eager the moment work appears. (2) **Drive from the small/recent side, not the huge side** — e.g. base_price fixes come only from newly-priced parts, so scan `supplier_parts WHERE updated_at > NOW()-INTERVAL '35 min'` (indexed) instead of all 2.24M `base_price=0` rows: 90s → 0.21s. Always add `SET LOCAL statement_timeout` so a slow run can't hold a snapshot for 30 min. Window must exceed the max backoff so nothing is missed between runs.
- **Startup search warmup**: one DB session PER case (a shared one lets a timeout-cancellation poison later cases); the heavy empty-query+vehicle-fitment case is slow cold / 0s warm and needs `timeout_s≥120` to actually prime the Redis cache (else the first real customer eats the cold time). Warmup `category` values must be English DB slugs, not Hebrew.
- **Fitment-search indexing (2026-07-11)**: the `part_vehicle_fitment` EXISTS in `_build_strict_vehicle_match_clause` (routes/parts.py) is now index-usable — do NOT reintroduce the un-indexable `:q LIKE '%'||column||'%'` **reverse-substring** branch (it forced a 57s scan). Indexes: `idx_pvf_mfr_trgm` / `idx_pvf_model_trgm` (pg_trgm GIN on `lower(btrim(...))` for `LIKE '%x%'`) + `idx_pvf_model_norm` (btree for `model = ANY(...)`). Model hierarchy recall ("Corolla Verso" → general "Corolla") is preserved by matching `lower(btrim(model)) = ANY(<model + its word-prefixes>)` — an indexable IN, NOT a reverse LIKE. Fitment filter went 56s→6s. **Still-slow residual:** the empty-query "browse all parts for my car" is dominated by `ORDER BY price_ils` over the ~42K parts that fit a car (+ the JSONB `compatible_vehicles` OR-branch which isn't GIN-indexable) — mitigated by warmup+cache; a full fix (materialized per-vehicle price view) is a deferred architectural change.

---

## AI Stack — 3-Option Upgrade (2026-06-17)

The system uses three complementary AI approaches tuned to the server constraints
(8 GB RAM, 4-core AMD EPYC VPS, NO GPU, no swap):

### Option 1: Phi-3-mini via HF Router (enrichment/generation)
- **Model**: `microsoft/Phi-3-mini-4k-instruct:featherless-ai`
- **Provider suffix required**: HF Router uses `{model}:{provider}` format. Featherless AI hosts Phi-3-mini.
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

## Pipeline Audit — 2026-06-18 (All Scrapers Verified)

| Importer | importer_price_ils | base_price | part_condition | supplier_parts | Status |
|---|---|---|---|---|---|
| `samelet_import_v2.py` | ✅ cost=max/1.18 | ✅ cost×1.45 | 'new' ✅ | ✅ | Fixed 2026-06-18 |
| `car_parts_ie_import_generic.py` | ✅ cost=eur×3.9/1.18 | ✅ cost×1.45 | 'new' ✅ | ✅ | Fixed 2026-06-18 |
| `catalog_scraper.py` (REX) | N/A (scrapes reference) | ✅ price×1.45 | 'new'/'oem' ✅ | ✅ | Fixed 2026-06-18 (was 'New') |
| `import_from_excel.py` | ✅ preserves existing | ✅ preserves | varies | ✅ | Fixed 2026-06-18 (was reset to 0) |
| `freesbe_importer.py` | ✅ ex-vat from retail | ✅ ex-vat×1.45 | pre-existing | ✅ | ✅ OK |
| `il_importer_pdf_import.py` | ✅ compute_price_triple | ✅ brand-specific | pre-existing | ✅ | ✅ OK |
| `oempartsonline_importer.py` | ✅ from USD price | ✅ price_ils×1.45 | pre-existing | ✅ | ✅ OK |
| `ebay_brand_importer.py` | N/A — global source | ✅ (normalize_base_price) | pre-existing | ✅ | ℹ️ By design — no IL importer price |

### Bugs fixed in this audit
1. `samelet_import_v2.py:305` — `importer_price_ils=0` hardcoded on every upsert → cost formula
2. `car_parts_ie_import_generic.py` — EUR price not flowing to importer_price_ils → added
3. `catalog_scraper.py:1414` — `part_condition="New"` (uppercase) → `"new"`
4. `import_from_excel.py:308` — `importer_price_ils=0` reset on UPDATE → CASE WHEN preserve

### IL Price Coverage (post-fixes)
total=3,467,068 · il_priced=1,056,046 (30.5%)
- 30% is STRUCTURAL: 70% from eBay/global sources have no IL importer equivalent
- Porsche (2,512) + Lexus (2,571) from uploaded PDFs ✅
- Jeep/RAM: 97% priced via samelet ✅
- Chrysler: 3,895 from car-parts.ie EUR conversion

## Complete Pipeline Audit — 2026-06-18 (ALL importers verified & fixed)

### Bug pattern 1: `importer_price_ils=0` hardcoded in ON CONFLICT UPDATE
Causes every importer re-run to wipe the IL price back to 0.
**Fix**: `CASE WHEN EXCLUDED.importer_price_ils > 0 THEN EXCLUDED.importer_price_ils ELSE parts_catalog.importer_price_ils END`

### Bug pattern 2: `part_condition='New'` (uppercase) in INSERT VALUES
Causes bad_cond counter to keep growing after every import run.
**Fix**: Always use lowercase `'new'` or `'oem'`

### Files fixed (2026-06-18) — both bugs
| File | Bug 1 (price_ils=0) | Bug 2 (cond='New') |
|---|---|---|
| `samelet_import_v2.py` | ✅ Fixed | ✅ (was 'new' already) |
| `car_parts_ie_import_generic.py` | ✅ Added EUR→ILS formula | ✅ (was 'new') |
| `catalog_scraper.py` | N/A | ✅ Fixed (was "OEM"/"New") |
| `import_from_excel.py` | ✅ Fixed (preserve existing) | N/A |
| `import_champion_motors.py` | ✅ Fixed | ✅ Fixed |
| `kia_import.py` | ✅ Fixed ($6 = ex-VAT cost) | N/A |
| `kia_new_models_import.py` | N/A | ✅ Fixed |
| `toyota_il_importer.py` | N/A (was correct) | ✅ Fixed |
| `mazda_il_importer.py` | N/A (was correct) | ✅ Fixed |
| `subaru_il_importer.py` | ✅ Fixed | ✅ Fixed |
| `geely_israel_import.py` | ✅ Fixed | N/A |
| `bydil_scraper.py` | ✅ Fixed | ✅ Fixed |
| `eliteparts_scraper.py` | ✅ Fixed | ✅ Fixed |
| `gmc_buick_umi_import.py` | ✅ Fixed | N/A |
| `lr_import.py` | ✅ Fixed | N/A |
| `selected_parts_scraper.py` | ✅ Fixed | N/A |
| `sng_barratt_jaguar_import.py` | ✅ Fixed | ✅ Fixed |
| `supplier_pdf_import.py` | N/A | ✅ Fixed |
| `zeekr_full_import.py` | ✅ Fixed | ✅ Fixed |
| `import_delek_brands.py` | N/A | ✅ Fixed |

### Files verified clean (no bugs found)
`freesbe_importer.py`, `il_importer_pdf_import.py`, `oempartsonline_importer.py`,
`ebay_brand_importer.py` (by design — global source, no IL importer price)

### Scraper/importer formula reference (CLAUDE.md policy)
```
IL consumer price (incl. VAT) → cost = price / 1.18
importer_price_ils = cost         # ex-VAT cost
max_price_ils = price             # consumer reference price
base_price = cost × 1.45         # our selling price (45% margin)
part_condition = 'new'            # always lowercase
```

---

## Repository & Backend File Map (2026-07-18 reorg)

The repo was reorganized so the file tree matches how the system actually runs. **The
container only mounts `backend/` → `/app`**, so anything outside `backend/` is host-side
only (docs, archives) and can never affect runtime.

### How the runtime finds moved scripts (READ before moving/renaming any backend file)
The 4 core app modules + all imported library modules stay at `/app` root; standalone
scripts live in subfolders. Imports still work by **bare name** because
`backend/sitecustomize.py` (auto-loaded via `PYTHONPATH=/app` set in `docker-compose.yml`)
appends every script subfolder to `sys.path`. So `import samelet_import_v2` resolves even
though the file is in `importers/`, for uvicorn **and** every `python3 /app/.../X.py`
subprocess. **Rules when touching backend files:**
- Add a new script → drop it in the right subfolder; no path config needed (sitecustomize
  covers imports). Invoke it as `python3 /app/<subfolder>/<name>.py` (or `python3 -m <name>`).
- Move/rename a script → also fix (a) any `python3 /app/<old>` subprocess string, (b) any
  `Path(__file__).parent …` that reaches app-root resources (`state/`, `data/`, sibling
  scripts) — a file one level deep uses `Path(__file__).parent.parent` to reach `/app`.
- A file **imported by the app** (`from X import …` in BACKEND_*/routes/services/agents)
  must stay at `/app` root (or be added to sitecustomize).
- `state/` (the `worker_state` volume) is always `/app/state`; `data/`, `uploads/`,
  `test_images/` are always at `/app`. Never anchor them off a subfolder's `__file__`.

### Creating a NEW file — place it right AND wire it in, in the same change (MANDATORY)

The 2026-07-18 reorg happened because new files had been written to the flat root and left
loosely connected. **Do not repeat that.** A new file is not "done" until it is (a) in the
correct folder and (b) actually reachable/active in the system — never "write to root now,
move/wire later."

**Where each new file goes (decide BEFORE writing it):**
| New file is… | Put it in | And wire it by… |
|---|---|---|
| an importer (writes catalog/prices) | `backend/importers/` | invoke as `python3 /app/importers/<name>.py`; follow the Import Data Standard + SQL patterns; add the top-of-file docstring |
| a site harvester | `backend/harvesters/` | if it should run continuously, register a supervised loop in `BACKEND_API_ROUTES.startup()` via `_supervised_task(...)`; anchor state at `/app/state` (`Path(__file__).resolve().parent.parent`) |
| a playwright/html scraper | `backend/scrapers/` | called by its importer/`catalog_scraper` with the `scrapers/` path |
| a run_/build_/categorize_/backfill_ pipeline or cleanup job | `backend/maintenance/` | add it to `db_update_agent`/`db_cleanup_agent` task list or schedule it; use bounded batches + `SKIP LOCKED` |
| a shared library module (imported by the app) | `backend/` root | just `import <name>` — it's on the path |
| an API route group | `backend/routes/` | **`app.include_router(...)` in `BACKEND_API_ROUTES.py`** — an unregistered router is dead code |
| a supplier/price-sync service | `backend/services/` | wire into the aggregator / sync loop that consumes it |
| a customer-agent skill | `backend/agents/` or `BACKEND_AI_AGENTS.py` | reachable from `process_user_message` (the one shared brain) |
| an ad-hoc test/debug harness | `backend/devtests/` | — |
| a superseded one-off | `backend/legacy/` or `archive/` | — |
| a data dump / fixture | `backend/data/` (runtime) or `archive/data/` (host artifact) | never the repo root |
| a doc | `docs/` (topical) or root (only `CLAUDE.md`/`README.md`/`FIXES_TRACKER.md`/`ROADMAP.md`) | link it from `CLAUDE.md` if agents need it |

**Wiring checklist before closing (an orphan file is a bug):**
1. Placed in the correct folder above — never the flat root as a parking spot.
2. Connected to its trigger: router registered / supervised-task added / scheduler entry /
   caller updated — and invoked with the correct `/app/<subfolder>/…` path.
3. Top-of-file docstring (Script Documentation Standard).
4. **Proven active**, not just present: hit the route, run one cycle, or confirm the loop
   logs — a file that exists but nothing calls is not done.
5. If it makes a public-facing surface, it returns only masked/right-sized data (see the
   Partner API rules) — never raw cost/margin/supplier internals.

### backend/ layout
| Path | Contents |
|---|---|
| `/app/*.py` (33) | **Core + imported library modules** — `BACKEND_API_ROUTES` (uvicorn entrypoint), `BACKEND_AI_AGENTS`, `BACKEND_AUTH_SECURITY`, `BACKEND_DATABASE_MODELS`, `db_update_agent`, `db_cleanup_agent`, `catalog_scraper`, `meili_sync`, `email_templates`, `hf_client`, `resilience`, `watchdog_state`, `distributed_lock`, `currency_rate`, `manufacturer_normalization`, `categories`, `category_map`, `part_type_taxonomy`, `agent_todo_utils`, `invoice_generator`, `external_fitment_providers`, `ai_catalog_builder`, `auto_backup`, `harvest_heartbeat`, `workbook_normalizer`, `oempartsonline_importer`, `opel_car_parts_ie_import`, `run_rex_transport_office_pipeline`, `run_fitment_enrichment_pass`, `run_targeted_external_fitment_pass`, `build_full_car_database`, `clean_manufacturers_registry`, `ebay_fitment_backfill`, `sitecustomize` |
| `/app/importers/` (64) | One-shot & scheduled catalog/price importers (samelet, colmobil, delek, mct, kia/toyota IL, champion, car_parts_ie, rockauto, etc.) |
| `/app/harvesters/` (11) | Site harvesters — `car_parts_ie_flaresolverr_harvester` & `amayama_flaresolverr_harvester` (both supervised from `BACKEND_API_ROUTES`), champion/toyota/kia IL, rockauto, spareto, tecdoc |
| `/app/scrapers/` (15) | Playwright / HTML scrapers (`oem_parts_online_scraper` spawned by `catalog_scraper`, febest, gm/audi/bmw/lr playwright, etc.) |
| `/app/maintenance/` (30) | `run_*/build_*/categorize_*/backfill_*/seed_*` pipeline & cleanup jobs, fitment passes, dedup, vat/margin fixes |
| `/app/devtests/` (6) | Ad-hoc test/debug harnesses (`_*_test.py`, `test_*.py`) — NOT the pytest suite |
| `/app/legacy/` (1) | Superseded one-off scripts kept for reference |
| `/app/routes/` `services/` `social/` `agents/` | App packages (API routes, supplier/price-sync services, whatsapp/telegram providers, agent memory) — unchanged |
| `/app/tests/` | pytest suite (unchanged) |
| `/app/data/` `state/` `uploads/` `test_images/` `alembic*/` `scripts/` | Data files, persistent worker state (volume), uploads, migrations, shell scripts — unchanged |

### repo root layout
| Path | Contents |
|---|---|
| `CLAUDE.md` `README.md` `FIXES_TRACKER.md` `ROADMAP.md` | Canonical docs (kept at root) |
| `docker-compose.yml` `.env` `.gitignore` `requirements.txt` | Config |
| `backend/` `frontend/` `whatsapp-bridge/` `deploy/` `database/` | Services |
| `docs/` | Topical docs (`skills.md`, `phases.md`, `UI_UX.md`, `roadmap.md`, `SUPPLIERS.md`, import guides) + `docs/schema/` DB schema dumps |
| `archive/scripts/` | Host-side one-off dev scripts (fix_/patch/cm_/update_ … — never run by the container) |
| `archive/data/` | Old JSON/xlsx/pdf data dumps + compose backups (host-side artifacts) |

---

## Agent Core Rules & Shared Architecture (merged from claude.md 2026-07-18)

> Merged from the former `claude.md`. Where the two disagreed, the **rest of this file wins**
> — it is newer. In particular the OLD claude.md pricing/import-SQL specifics are SUPERSEDED
> and must NOT be reintroduced: VAT is **conditional** (`get_supplier_vat_rate`: 18% LOCAL/IL
> only, 0% foreign — see the Never-regress note under G2), `part_condition` is **lowercase**
> (`'new'`, never `'New'`), and `supplier_parts` upserts use
> **`ON CONFLICT ON CONSTRAINT supplier_parts_supplier_id_supplier_sku_key`** (never
> `(part_id, supplier_id)` in importers). See "MANDATORY: Before Writing Any Importer".

### Web scraping — always use the browser/FlareSolverr path
The server IP is Cloudflare/anti-bot blocked; direct `urllib`/`requests`/`httpx` to external
sites will fail. Use the browser tool / FlareSolverr harvesters. **Two-step pattern:** (1)
extractor scrapes → JSON on disk; (2) a separate importer reads the JSON → Postgres. Internal
calls (localhost, inter-container) may use plain HTTP.

### The two agent layers
**Layer A — AI customer agents** (`BACKEND_AI_AGENTS.py`, all on Cerebras gpt-oss-120b, one
shared brain `process_user_message`): AVI (router), NIR (parts/fitment/OEM), MAYA (sales/
pricing), LIOR (orders), TAL (finance/VAT/invoices), DANA (support/returns/warranty), OREN
(security/fraud), SHIRA (marketing), BOAZ (supplier B2B + daily price sync), NOA (social),
REX (scraper coordinator). Full skills → `docs/skills.md`.
**Layer B — pipeline workers**: `catalog_scraper` (ingest), `db_cleanup_agent` (30s self-heal),
`db_update_agent` (`run_all_tasks` every 3h), `ai_catalog_builder` (enrichment), `meili_sync`
(indexing, 2h loop), `run_rex_transport_office_pipeline` (vehicle registry), REX harvest queue,
`services/ebay_price_sync` + `aliexpress_price_sync`, `auto_backup` (24h). Phase order →
`docs/phases.md`.

### Shared infrastructure
- **Memory** (`agents/memory.py`): in-process → Redis → Postgres. Agent-scoped keys are plain;
  cross-agent shared keys are `shared:{key}`. Workers MUST `write_worker_heartbeat()` at the
  start and end of every cycle — it's how agents know a worker is alive.
- **Alerting** (`_health_monitor_loop`, every 5 min): Redis-backed cooldowns survive restarts;
  container-lifetime guard suppresses restart-orphan false alerts (see the FIXES_TRACKER
  "Worker failed / Zombie" root-fix). Never alert on a job whose last activity predates the
  current container.
- **Zombie auto-fix** (health check 5b): a `running` job silent >30 min gets its Redis lock
  cleared, `job_registry` marked terminal, owner alerted once (24h cooldown). No manual
  `redis-cli DEL` needed.
- **Todos** (`agent_todo_utils.py`): read active todos at the start of every cycle.
- **Resilience** (`resilience.py`): wrap all external calls in `@retry_with_backoff`
  (retry 429/503/504; skip 401/403/404).
- **Distributed lock** (`distributed_lock.py`): acquire `autospare:lock:{job_name}` before any
  write-heavy job; never run two instances of the same job at once.
- **Job registry**: `job_registry_start/heartbeat/finish` around every job.

### Golden rules (every task, every session)
1. **Todo list first** — split into a numbered checklist, work in order, don't skip.
2. **Root-fix only** — no patch-as-final; if an emergency guard is needed, mark it temporary
   and land the root fix in the same cycle. Apply the fix in source first, then rebuild/redeploy
   (never a running-container hotfix as the permanent fix).
3. **Verify from real data, not .md files** — read the live container/DB/source; docs can be
   stale. A goal is not done until an end-to-end check against the LIVE system proves the
   *outcome* (see the PLATFORM GOALS "not Done until VERIFIED" rule).
4. **Check breaking points** — auth, payment, data path, API/route contracts — before closing.
   Before wiring any CTA/nav link, confirm the target route exists in the served app (don't
   point at paths that silently fall back to the landing page).
5. **Document every fix** in `FIXES_TRACKER.md`; update `docs/roadmap.md` / `docs/phases.md` /
   `docs/PRE_LAUNCH_CHECKLIST.md` as relevant.
6. **Never fabricate data** — counts/metrics/statuses come from live queries, never invented.

### Conflict resolution / confidence tiers (never overwrite higher with lower)
`1.00` official manufacturer/importer · `0.90` OEM cross-reference · `0.85` known aftermarket
(`manufacturer_normalization.py`) · `0.65` marketplace APIs (eBay/AliExpress) · `0.50` scraped web.

### Standard job result JSON
`{"task","status":"ok|error|skipped","scanned","updated","flagged","elapsed_s","errors":[]}`

### Script documentation standard
Every backend script keeps a top-of-file docstring: `Script:` / `Purpose:` / `Process:` steps /
`Data Imported/Modified:` (which tables/fields) / `Data Sources:` (URLs) / `Missing Data
Delegation:` / `Last Updated:`. Update it when you change the script.

---

## Partner / Public API (routes/public_api.py) — added 2026-07-18

A small, API-key-authenticated surface for external sites/devs. **Right-sized by design: it
exposes only what a partner needs and NEVER internal data** (supplier names, our cost, the 45%
margin, `base_price`, `importer_price_ils`/`online_price_ils`, or any internal flag).

- **Base path:** `/api/public/v1/` — `health` (no auth), `search`, `parts/{id}`, `fitment`,
  `manufacturers`. Registered in `BACKEND_API_ROUTES.py` via `include_router(public_api_router)`.
- **Auth:** `X-API-Key` header → sha256 → `api_keys` table (catalog DB). Per-key Redis rate limit
  (`apikey_rl:{id}`, default 60/min, set per key). Issue/list/revoke keys with
  `python3 /app/maintenance/issue_api_key.py --partner "Name" [--rate N]` (raw key shown once;
  only the sha256 is stored).
- **Pricing:** reuses `_customer_price_fields` (routes/parts.py) so the API returns EXACTLY the
  customer-facing price — `cost × 1.45 + CONDITIONAL VAT` (18% IL suppliers, 0% foreign). Never a
  raw or flat-VAT price. (Verified 2026-07-18: IL part 38.53 → VAT 6.94 → 45.47; foreign part
  VAT 0.)
- **Search** uses Meilisearch (`/indexes/parts/search`, needs the `Authorization: Bearer
  $MEILI_MASTER_KEY` header) then prices the hits from the DB. **Fitment** resolves the fitting
  part-ids FIRST (pvf trgm/norm indexes + LIMIT) then prices only those — never price-sort the
  whole match set (that was 28s → 0.198s).
- **Response schema (the ONLY exposed fields):** `part_id, oem_number, name, name_he,
  manufacturer, category, barcode, available, price{amount, vat, total, currency, vat_included}`.
  Any new field added here must pass the "no internal data" bar.
- **Any NEW public/partner endpoint MUST**: require `X-API-Key` (`Depends(require_api_key)`),
  price via `_customer_price_fields`, return the masked schema via `_shape`, and add a
  `check_rate_limit`. Partner-facing docs: `docs/PUBLIC_API.md` (keep it in sync).

---

## Part Thumbnails — Contabo Object Storage (S3) + cleanup pipeline (added 2026-07-18)

Part images are re-hosted as clean thumbnails in a **Contabo Object Storage (S3-compatible)**
bucket and served from our own domain. Source supplier images are often contaminated with
**supplier ads/placeholders** (e.g. "PRODUCT IMAGE COMING SOON / SOUK AUTO PARTS / CONTACT US"),
so they are **filtered, never blindly re-hosted** (owner rule: a thumbnail may carry the part
image + the part name only — never a supplier link/ad).

- **Config (secret only in `.env`, gitignored):** `S3_ENDPOINT=https://eu2.contabostorage.com`,
  `S3_REGION=eu2`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_BUCKET=part-thumbnails`,
  `THUMB_PUBLIC_BASE=https://autosparefinder.co.il/api/v1/thumbnails`. Also referenced in
  `docker-compose.yml` backend env. Client: `s3_storage.py` (boto3, s3v4).
- **Bucket is PRIVATE.** Contabo does NOT serve anonymous public GETs even with a public-read
  policy (verified: 401). Thumbnails are streamed by the backend `routes/thumbnails.py` →
  `GET /api/v1/thumbnails/{key:path}` with `Cache-Control: public, max-age=31536000, immutable`
  so **Cloudflare edge-caches** each one (backend fetches from S3 at most once). Serving only
  the image bytes guarantees no supplier link/ad can ride along.
- **nginx:** a dedicated `location ~* ^/api/v1/thumbnails/` is declared **before** the
  `\.(jpg|png|…)$` static regex (which would otherwise hijack keys ending in `.jpg` as missing
  static files → 404), un-rate-limited. Single-file mount → validate in a throwaway container on
  the `autosparefinder_internal` network, then **restart** `autospare_nginx` (not reload).
- **Cleanup pipeline:** `maintenance/build_part_thumbnails.py` — for each part with a source
  image and no `part_thumbnails` row: fetch (upgrade eBay `s-l225`→`s-l500`), **OCR the image
  (tesseract) and REJECT it** if it (a) contains supplier/promo text ("coming soon", "contact us",
  "auto parts", a URL, "whatsapp", hotline…) OR (b) is **text/label/brand-heavy** — more than
  `THUMB_MAX_OCR_WORDS` (default 3) real words ⇒ it's a label / OEM-box / brand-card / ad, NOT a
  clean part picture (owner rule: the picture must have **NO label or brand name**). Then
  standardize: auto-trim, fit to a clean 500×500 white square, compress ≤150 KB progressive JPEG,
  **NO caption/label/brand text is ever drawn**. **Dedup = content-addressed keys**: the object
  key is `thumbs/<ab>/<sha256(bytes)>.jpg`, so an identical image is stored **once** and reused by
  every part that shares it (no duplicate uploads; `object_exists` short-circuits). Outcome in the
  separate **`part_thumbnails(part_id, url, status)`** table (`ok | rejected_ad | no_source |
  failed`; NOT a column on the 4M-row parts_catalog → no DDL lock storms). `url` points at the
  shared content-addressed object. Run: `python3 /app/maintenance/build_part_thumbnails.py --limit 500`.
- **Source images — harvest→thumbnail connection (added 2026-07-18):** the thumbnail pipeline
  only cleans what the harvesters capture. The two parts-CREATING harvesters now write the part
  photo to **`parts_images`** (the pipeline's input): **(1)** `car_parts_ie_flaresolverr_harvester.py`
  `parse_parts()` extracts the block image via a markup-agnostic `_extract_image()` (lazy attrs
  `data-src`/`data-original`/… first, then `src`, then a CSS `background-image`; skips
  placeholder/logo/`data:` URIs; absolutizes) into `part["image_url"]`; it rides the existing
  `/collect` pass-through (no field whitelist) into `car_parts_ie_import_generic.py`, which INSERTs
  a `parts_images` row (`is_primary`, `NOT EXISTS` dedup guard — there is **no** unique
  `(part_id,url)` index, so never use `ON CONFLICT` here; and `url` is `varchar` → cast `$2::varchar`
  or you hit `AmbiguousParameterError`). **(2)** `oempartsonline_importer.py` — the scraper already
  captured `image_url`; the importer now writes the same `parts_images` row. From there the
  thumbnail supervisor picks the part up automatically (it scans parts lacking a `part_thumbnails`
  row). `amayama`/`rockauto` relays are **price-fill only** (match existing parts by OEM) — they
  create no parts and need no image write. Any NEW parts-creating harvester MUST also write
  `parts_images` or its parts get no thumbnail. (Was 0.7% of the catalog imaged before this — the
  car-parts.ie bulk captured none.)
- **Supervisor (watches + handles the import):** `_thumbnail_import_loop()` in
  `BACKEND_API_ROUTES.py`, registered at `startup()` via
  `_supervised_task("thumbnail_import_loop", …)` (so a crash of the loop auto-restarts). It runs
  `maintenance/build_part_thumbnails.py` continuously in modest batches (`--limit
  THUMBNAIL_IMPORT_BATCH=300`) as a **subprocess** (isolates the synchronous OCR/PIL off the event
  loop) at **low CPU priority** (`os.nice(15)`) so it never starves the flaresolverr harvesters on
  this 4-core box; a **40-min hard cap** kills a stuck batch, a **`THUMBNAIL_IMPORT_SLEEP=90`s**
  pause sits between batches, and it **exponentially backs off** (up to 1h) when the backlog is
  drained (then re-checks for newly-imported parts). Toggle with `THUMBNAIL_IMPORT_ENABLED=0`.
  Observe it at **`GET /api/v1/system/thumbnail-import`** (last cycle + live coverage: ok /
  rejected_ad / no_source / distinct_images / dedup_saved) and in `docker logs` (`[thumbnail_import]`).
  **Do NOT** also run a manual `docker exec -d … build_part_thumbnails` — the supervisor owns the import.
- **Search wiring:** `routes/parts.py` surfaces ONLY the clean bucket thumbnail as
  `primary_image` (LEFT JOIN `part_thumbnails` status='ok'); raw supplier image URLs are
  **never returned** to customers. The frontend `_partImageCandidates` already reads
  `primary_image`. Any new surface that shows a part image MUST use the bucket thumbnail, never
  a raw supplier URL.
- **Tooling** baked into the backend image (Dockerfile): `tesseract-ocr` + `pytesseract` +
  `boto3` + `fonts-dejavu-core`.
- **Verified 2026-07-18:** S3 round-trip; a real part (Land Rover LR016621) → clean 500×500
  ≤150 KB JPEG served through the domain; the SOUK ad image → OCR-rejected; search "oil filter"
  → returns the part with `primary_image` = the bucket URL. Test: `devtests/thumbnail_pipeline_test.py`.
- **Security (audited 2026-07-18):** the S3 secret lives ONLY in `.env` (gitignored — never in a
  tracked file/log/response). Bucket is **fully private** — the test-time public-read policy was
  removed → anonymous LIST/GET both return 401 (Contabo denies anonymous by default; only the
  backend, with credentials, reads). **Do NOT set a Contabo `public-access-block`** — its
  implementation blocks even authenticated PutObject (breaks the import); rely on no-bucket-policy
  + default-deny. The serving proxy returns **only image bytes** (no `x-amz-*`/
  bucket/endpoint headers leak) and rejects anything that isn't a `thumbs/…` or `parts/…` key —
  traversal (`..`, `%2e`, leading `/`, `\\`) and other prefixes all 404. 404s carry a negative Cache-Control
  so a flood of random keys is absorbed at the edge (the un-rate-limited location's only DoS
  vector). **Residual:** Contabo access keys are ACCOUNT-WIDE (can reach every bucket) — if the
  key is ever exposed, rotate it in the Contabo panel and update `.env`.
