# IL Importer Site Reference (moved verbatim from CLAUDE.md §7 on 2026-11-27, FIXES_TRACKER #160)

> Read this BEFORE touching any Israeli importer/harvester, the car-parts.ie harvester, FlareSolverr,
> the harvest supervisor loops, or harvest tab strategy. CLAUDE.md §7 keeps the hard rules and the triggers.
> Content below is byte-identical to the former CLAUDE.md §7 body, except the two monitoring bullets (superseded, marked).

<!-- BEGIN VERBATIM -->

- **Pricing / VAT / import formula**: §2 Pricing & VAT and §6 (VAT 18%, never 0.17 — 27 code sites still use 0.17, see FIXES_TRACKER #130).
- **Restarts**: §2 Deployment & Config + §4 golden rule 8 (preflight, owner approval).
- **Monitoring / wakeup checks**: SUPERSEDED 2026-11-27 (#160) — monitoring wakeups are opt-in and never restart anything; see CLAUDE.md §11 "Monitoring wakeups".
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

- **supplier_parts ON CONFLICT** — see §6 Pattern 1 (constraint) and Pattern 5 (per-row savepoints).

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
  - **FULL-CATALOGUE coverage — the harvester is only as broad as `harvest_queue` (added 2026-07-23)**. The harvester harvests whatever models are in `harvest_queue`; it was originally seeded from the IL market (1,401 models / 83 brands, and ~52% of those were `status='empty'` because the slugs were guessed from Hebrew names and don't match car-parts.ie's TecDoc chassis-code slugs). To cover **all 176 brands**, `maintenance/seed_car_parts_ie_full_catalog.py` enumerates the site's own **static** structure with a cf_clearance cookie (plain urllib — NO Playwright): `/car-brands` → **176 brand slugs**; `/car-brands/{brand}-parts` → every model slug (strip the `-parts` suffix); insert each `{brand}/{model}` into `harvest_queue` (`source='car_parts_ie_full'`, `ON CONFLICT (brand_en, model_slug) DO NOTHING`, `priority_rank=100000+brand_index` so IL keeps priority). Seeded 6,005 real models 2026-07-23. Re-seeds automatically ~monthly via `_car_parts_ie_full_seed_loop()` (`car_parts_ie_full_seed` supervised task; toggle `CPIE_FULL_SEED_ENABLED`, interval `CPIE_FULL_SEED_INTERVAL_S`). **DEAD END — do NOT rebuild it:** the numeric `maker_id/model_id/car_id` "spares-search" cascade returns "0 results found" for every vehicle (even modern popular ones) — car-parts.ie's inventory is NOT indexed against those TecDoc car_ids; it lives on the static slug pages the seeder enumerates. A Playwright cf_clearance-handoff cascade crawler was built, proven useless this way, and removed.

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
  - **Raising `PARALLEL_SESSIONS` (tested on old 4-core box, reverted then re-raised)**: tried 5 concurrent sessions on the old 4-core box expecting a throughput gain. Measured the opposite — per-slug latency roughly doubled, host load average rose from ~14 to ~25, and net throughput dropped to 0 completed models in 10 minutes (vs. ~1 every 1.4 min at 3 sessions). Reverted to `PARALLEL_SESSIONS = 3`. **After the 2026-07-20 server upgrade to 6 vCPUs**, raised to `PARALLEL_SESSIONS = 4` — load avg dropped to ~1.5-2 per core (healthy). Do not raise to 5+ without re-measuring (compare `uptime` load avg + models/10min before/after).

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

<!-- END VERBATIM -->
