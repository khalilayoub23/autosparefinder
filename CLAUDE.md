# AutoSpareFinder — Claude Code Instructions

> **Single authoritative instruction file.** Jump to any section by heading number.
> Topical docs: `docs/skills.md`, `docs/phases.md`, `docs/UI_UX.md`,
> `docs/roadmap.md`, `docs/SUPPLIERS.md`, `docs/POSTMORTEMS.md` (full incident log).

## 1. Critical Directives


**The owner (Khalil) has granted full working access: credentials in `.env`, the web/browser
tool, direct server + container (`docker exec`) access, and the live DB. When the owner asks
for something, DO IT with that access — do not stall, do not re-request permission already
granted, do not hand a task back that I can perform myself.**

Rules:
1. **Default to execution.** An instruction is an instruction. Use the creds/web/server/.env I
   already have and complete the task end to end. Don't narrate "privacy/permission" caveats for
   things the owner has already authorized.
2. **Verify with my own tools BEFORE claiming I can't.** I have the server, `.env`, the browser,
   and the DB — inspect the real thing (console, config, DB) instead of asserting a limitation or
   delegating. (This is what I failed on with the Google Desktop-client bug.)
3. **Secrets:** the owner's own credentials go in the gitignored `.env` / config as normal — that
   IS the handling; no extra hand-wringing. Never commit/log them, that's the only constraint.
4. **The only genuine stops** (state once, in one line, then proceed with the owner's call — never
   a silent refusal, never repeated nagging):
   - I can't type the owner's **password / 2FA**, or click **"approve" on an OAuth consent dialog
     AS the owner** — I drive right up to that point and let them do that one click.
   - If an action would likely **get an account banned** or is **structurally impossible** (a
     platform wall), I flag it in ONE line so the owner can decide — then do what they say.
5. **No repeating a caveat the owner has already overridden.** Once told to proceed, proceed.
6. **If a tool that does the job is available, use it — don't ask the owner to do it instead.**
   Browser/Playwright tool, `docker exec`, DB access, `.env` creds, web fetch: if one of these
   can perform the step, perform it. Do not respond to a blocker by handing the owner a manual
   procedure (paste cookies, click this button, run this command yourself) when the tool to do
   that step myself is sitting right there. This was said explicitly on 2026-08-10 after I asked
   the owner to copy Facebook cookies out of DevTools instead of driving the login through the
   browser tool myself.
   - Exception: the genuine stops in rule 4 (owner's password/2FA keystroke, clicking "approve"
     on a consent dialog AS the owner) — everything up to that one click is still mine to drive.
   - If a tool-driven attempt hits a real wall (CAPTCHA I cannot visually solve, a platform ban
     risk), say so in one line with what was tried, then proceed on the owner's call — not a
     silent handoff disguised as a caveat.

This directive is high priority: when in doubt, act on the owner's instruction using the access
provided.



**Every account, API, OAuth client, Cloud project, channel, or third-party site connected to
the platform MUST be created under / owned by the business Google account
`autosparefinder2024@gmail.com` — NEVER `khalilayoub23@gmail.com` (the owner's personal
account) or any other.**

Why: this session I built the YouTube OAuth client + Data-API enablement under khalilayoub23's
project ("My First Project"/`aesthetic-root-463607-q7`) instead of the business account's
project (`valid-moment-444021-r6`). Business assets belong to the BUSINESS account so they
survive independent of the owner's personal login and can be handed over cleanly.

How to apply:
1. **Before creating any Google/OAuth/Cloud/channel resource, confirm the console's ACTIVE
   account is `autosparefinder2024@gmail.com`** (read the account button, not the `authuser=`
   URL param — it lies) AND the active project is the business project
   (`valid-moment-444021-r6`). If it's khalilayoub23, switch first.
2. Applies to ALL connected surfaces: Google Cloud projects, OAuth clients, YouTube, Google
   Business, Gmail-based signups for any new third-party tool/site, analytics, ad accounts, etc.
3. The Gmail connector is authorized for `autospare`'s Google account — I can READ this mailbox
   (verification links, OAuth notices, signup confirmations) to complete flows myself instead of
   asking the owner.
4. If a resource already exists under the wrong account, migrate it to
   `autosparefinder2024@gmail.com` and document the migration in FIXES_TRACKER.

---

**Contents**
[1. Critical Directives](#1-critical-directives) · [2. Critical Lessons Learned](#2-critical-lessons-learned) · [3. Platform Vision & Goals](#3-platform-vision-goals) · [4. Repository & Agent Architecture](#4-repository-agent-architecture) · [5. Hard Constraints](#5-hard-constraints) · [6. Mandatory Importer Patterns](#6-mandatory-importer-patterns) · [7. IL Importer Site Reference](#7-il-importer-site-reference) · [8. Business Rules](#8-business-rules) · [9. Feature Modules](#9-feature-modules) · [10. Operations Reference](#10-operations-reference)

---

## 2. Critical Lessons Learned

> The full chronological incident log lives in [`docs/POSTMORTEMS.md`](docs/POSTMORTEMS.md).
> These are the distilled, topic-grouped rules — optimized for AI retrieval.
> **Before closing any task, re-read this section and check the work against it.**

**Meta-rules (how to apply):**
1. **Fix all layers, not the first one.** When a rule is wrong in one file, `grep` the whole
   codebase and fix every occurrence. "Fixed where it was reported" ≠ fixed.
2. **A policy needs a single enforcement point + a guard.** Prefer one function every surface
   calls over re-implementing per file.
3. **Verify against the LIVE system**, not self-reports or `.md` files.
4. **When a new incident surfaces a new rule**, append a row to `POSTMORTEMS.md` AND update the
   relevant topic group below. The lesson is not learned until both are done.

---

### Pricing & VAT
- **Conditional VAT**: `get_supplier_vat_rate()` returns 18% for IL/local suppliers only, 0% for
  foreign (car-parts.ie/IE, SNG/UK, eBay/US). Never flat ×1.18 across the board.
- **`importer_price_ils` guard**: in every ON CONFLICT DO UPDATE use
  `CASE WHEN EXCLUDED.importer_price_ils > 0 THEN EXCLUDED.importer_price_ils ELSE parts_catalog.importer_price_ils END`.
  Never hardcode `importer_price_ils = 0` or `= EXCLUDED.importer_price_ils` without the guard.
- **One canonical price function**: `_customer_price_fields` (routes/parts.py). Every surface —
  search, chat, checkout, NOA — consumes it. No per-channel price formulas.
  Formula: `sell = cost × 1.45`, `vat = sell × 0.18 (IL only)`, `total = sell + vat + ship`.
- **Import formula**: `cost = consumer_price / 1.18` → `importer_price_ils = cost`,
  `base_price = cost × 1.45`, `max_price_ils = consumer_price`. Never reverse this.

### Database & SQL
- **`MAX(uuid)` does not exist in Postgres.** Use `ORDER BY id DESC LIMIT 1` for keyset cursors.
- **Never `:id::uuid` in SQLAlchemy `text()`.** The `::` cast collides with `:name` param binding.
  Use `CAST(:id AS uuid)`.
- **ON CONFLICT for `supplier_parts` importers**: use
  `ON CONFLICT ON CONSTRAINT supplier_parts_supplier_id_supplier_sku_key` — never
  `ON CONFLICT (part_id, supplier_id)` (wrong constraint, fires silently).
- **No DDL from read/request paths.** `CREATE INDEX IF NOT EXISTS` / `ALTER TABLE ADD COLUMN IF
  NOT EXISTS` take AccessExclusiveLock even when they change nothing. Schema work belongs in startup.
- **Batched writes on harvested tables**: always `FOR UPDATE SKIP LOCKED`. Tables the harvester
  also writes will deadlock otherwise; per-row savepoints (`async with conn.transaction()`) prevent
  cascade aborts.
- **Config seeded with `ON CONFLICT DO NOTHING` stops being the source of truth.** Use `DO UPDATE`
  on definition columns (never on progress/status columns) so source code stays authoritative.
- **An absent column fails open silently.** A guard built on a column that doesn't exist passes
  every row. Verify column names against the real header before trusting any guard.
- **Tally per-task `status=` from logs**, not cycle completion. A task that errors and lets the
  cycle continue looks healthy from the outside. `8,210/8,210 skipped` is a total outage.
- **When a data-writing task is broken, read what it WOULD write before fixing the crash.**
  A "normalizer" whose only working path pushes data into the catch-all destroys good data.

### Background Jobs
- **No LLMs in background cron loops.** Every background LLM task must be gated by an env toggle
  defaulting to 0. Three mandatory limits: small batch (`CLEANUP_LLM_BATCH=25`), minimum interval,
  daily ceiling (`CLEANUP_LLM_DAILY_MAX_CALLS=150`).
- **A drained queue must sleep** (exponential backoff up to `HARVESTER_IDLE_MAX_REST_S`), not poll
  every N seconds. An idle loop at full speed burns CPU without output.
- **"No progress ≠ finished"** unless the previous attempt SUCCEEDED. Gate no-progress termination
  on `attempts == 0` — an errored batch also leaves remaining unchanged.
- **A guard must run on the path that USES data, not only the path that produces it.** A blocklist
  applied only at write time doesn't protect rows written before the filter existed.
- **A job whose unit cost rises as it completes will stall.** When a batched job slows, suspect the
  SEARCH for work — scope discovery to a bounded index range, not catalogue-wide.
- **Two heavy writers on one table contend.** Before a big migration, enumerate every scheduled
  writer of the same table and give each a stand-down. Use `job_queue.queue_busy()`.
- **Notify by exception.** A routine success report is camouflage — it trains the reader to ignore
  the channel. Only send on stall / idle / recovery.

### Categorization
- **One file, one vocabulary.** `category_map.py` is the only file that defines category rules.
  `parts_catalog.category` stores English slugs + `כללי`. Display names (`DISPLAY`) are never
  stored. Any new keyword goes in `category_map.py` only.
- **Longest-keyword-wins, never declaration order.** Specificity is intrinsic to the keyword.
  Short RTL tokens (≤3 chars) use a boundary pattern: one Hebrew proclitic (ה ו ב ל מ ש כ) may
  precede the word; no RTL letter may follow.
- **The only fallback is `כללי`.** Never return `general`/`service-general`/`accessories` as
  defaults. Every importer calls `categorize_on_ingest()`.
- **Blocklist enforced at LOAD time** (`load_into_matcher()` + `purge_blocklisted()` at startup).
  Write-only filtering misses rows written before the filter existed.
- **Approve only the winning `(token, category)` pair.** `WHERE token = :t` without a category
  predicate marks all vote-siblings approved; a future ranking change silently promotes a rejected
  category.
- **Consensus measures consistency, not correctness.** Validate against parts already filed in real
  categories (evidence profile). Two tests: MISMATCH (proposal ≠ evidence's top category) and
  MARGIN (top must beat runner-up ≥1.8×). A substance keyword must not outrank the hardware
  keywords that contain it.

### Social / NOA
- **Sanitization is structure-preserving and subtractive.** Never collapse `\s+` across newlines.
  Never staple boilerplate. Never re-add a multi-link footer.
- **Garble check must exempt Hebrew price prefixes** (`מ-198`, `ב-2020`, `ב AutoSpareFinder`).
  `\b[א-ת]\b` matches these and flags every real-price post as low-quality.
- **All outbound owner/customer WhatsApp goes through `_wa_send_quiet`** (09:00–21:00 IL).
  A new send site that calls `_wa_send` directly reintroduces 03:00 messages.
- **Reminder caps are LIFETIME per entity, never a rolling window.** A rolling window is not a cap.
- **Arabic chars (؀-ۿ) must be in `_NOA_HASHTAG_RE`.** Underscore must be preserved inside
  hashtags (`#قطع_غيار` must not become `#قطعغيار`).
- **Detecting an owner directive must change what the system DOES**, not just what it records.
  A recognised directive injects an explicit acknowledgement note and produces no artefact.

### Agent Behavior
- **Every agent in `_fast_agents` needs its own `_offline_reply` branch.** A missing branch
  silently returns a wrong-context reply (worse than an explicit failure). Check `_fast_agents`
  membership against `_offline_reply` branches whenever either list changes.
- **A policy duplicated into a second prompt silently loses every clause nobody copied.** Use
  `_WA_REPLY_RULES` (shared) + a deterministic post-processing guard (`_clean_wa_reply`) as
  defense-in-depth — a prompt rule does not hold under a fallback model.
- **Silence is the one failure mode a user cannot diagnose.** Every background path that owes an
  answer needs: a strong reference, a deadline (`asyncio.wait_for(..., 120s)`), and a fallback.

### Deployment & Config
- **Bind mount `./backend:/app`** — code changes are live on disk after `docker restart`. No
  `docker cp` needed. `docker compose up -d backend` is now safe (the mount means no stale image
  code). Only use `compose up -d` when `docker-compose.yml` itself changed.
- **A find-and-replace must exclude the definition site** of the thing it replaces — or the
  function calls itself recursively.
- **Scrub URL secrets**, not just headers. `?key=`/`?api_key=`/`?token=` → `<redacted>` via
  `_scrub_secrets()` + `raise_for_status_safe()`. A credential in a URL leaks through every error
  path that logs `str(exc)`.
- **nginx single-file mounts need a container RESTART, not reload.** Editing the host file changes
  the inode; the running container holds the old inode until `docker restart autospare_nginx`.
- **Adding a value to an in-memory enum requires a restart before backfilling.** The bind mount
  updates files, not the loaded module. Watch a backfilled count for drift afterwards.

### External APIs & OAuth
- **External API capabilities drift — verify live before stating a limitation.** Training-era
  knowledge of what a platform API can do is a hint, not fact. When challenged on a capability,
  recheck rather than defend.
- **Design for the durable credential path first.** `app_secret` → `fb_exchange_token` →
  long-lived → non-expiring page token. Never build or verify on a throwaway token that expires
  in hours.
- **GBP verification requires a physical storefront.** An online-only marketplace cannot pass it —
  don't start verification flows that structurally can't complete.
- **Confirm Google account AND project before creating any resource.** Read the account button in
  the console (not `authuser=` URL param — it lies). Business assets → `autosparefinder2024@gmail.com`
  / project `valid-moment-444021-r6`.
- **OAuth client type matters.** A Desktop client cannot have JS origins or do browser sign-in.
  Inspect the live client config before asserting a root cause — error messages name symptoms.

### Data Quality
- **A reversal-looking defect in a browser is a bidi rendering problem until proven otherwise.**
  Fix with `<bdi dir="ltr">` around Latin runs, not string reversal of stored data.
- **Hebrew final forms (ךםןףץ) can only end a word.** A word STARTING with one proves visual
  (reversed) storage order. When reversing: flip the whole string, then flip each Latin/digit run
  back — a naive `s[::-1]` turns `GS330` → `033SG` and `24` → `42`.
- **When normalizing a stored value, grep the rules for the OLD form and add the NEW form in the
  same change.** Rules keyed on abbreviations break silently when the data is cured.
- **Never chain heuristic passes on a population your detector cannot classify.** Exclude
  unclassifiable rows and report them — don't run a second pass to clean up the first.

### Performance
- **`EXPLAIN` a timed-out query before assuming the predicate is at fault.** An `ORDER BY` that
  doesn't match an index forces a full-match-set sort.
- **A "remaining" figure must come from the WORKER's own candidate query**, not a table-wide count
  of a broader population the worker cannot touch.
- **Optimize the leverage, not the unit of work.** When an LLM feeds a rule engine, sample for
  the highest-frequency unknown token — one approved keyword fixes thousands of parts at once.
- **Do not build an index on a table a long-running job is actively writing.** `CREATE INDEX
  CONCURRENTLY` adds write amplification. Build indexes in a quiet window, not mid-migration.

## 3. Platform Vision & Goals


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

| G8 | "Fix NOA language and posts — I keep getting robotic posts and campaigns. Send NOA's posts and notifications to WhatsApp instead of Telegram. Posts should be smart, human, funny and attractive, and be selling posts. Add a QR code in the posts instead of the platform links, so when scanned the user picks the platform he likes. Add more automotive hashtags (also Arabic) so it becomes popular. Configure when notifications are sent — it's not possible to send at 3:00 AM. We configured cart notifications to 3 times and the system keeps breaking that rule. NOA should write like a human — that's the main goal. And I want backend issues on WhatsApp, not silent in the DB until I check." | 2026-07-20 | ✅ Done & Verified | **Robotic posts root-caused to 3 mechanisms, all fixed:** (1) the garble detector `\b[א-ת]\b` matched the ordinary Hebrew price form `מ-198` (and `ב-2020`, `ב AutoSpareFinder`, `ה Corolla`), so **every post quoting a real price** was judged low-quality → `_repair_low_quality_caption` collapsed `\s+` (destroying all line breaks) and stapled canned boilerplate; the rule now exempts Hebrew one-letter prefixes bound to a number/Latin token (genuine garble still caught) and repair preserves line structure. (2) A fixed **7-line footer** (5 platform links + 2 slogans) was appended to every post → replaced by the QR funnel; ≤1 link line remains. (3) A **static hashtag line** → rotating HE/AR/EN pools (`_noa_hashtag_mix`/`_enrich_hashtags`). **Personality:** system prompt rewritten into an explicit smart/funny/human/selling contract + a ban on writing its own disclosure sentences inline; mirrored into the loop prompts. **QR funnel:** `social/qr_media.py` composes thumbnail(or brand canvas)+QR+scan-strip → content-addressed `thumbs/qr/<sha256>.jpg` in the existing private bucket, served by the existing proxy; QR encodes `/api/v1/go?src=qr_<platform>_w<week>` → `routes/connect.py` tri-lingual (HE/AR/EN) RTL channel picker, no DB access, UTM-tagged. `qrcode>=8.0` baked into the image. **Arabic hashtags:** tag regex extended with `؀-ۿ` (they were being silently dropped) and `_normalize_noa_symbols` no longer strips `_` inside tags (it was mangling `#قطع_غيار`). **WhatsApp routing:** post approvals + Monday brief now go to the owner's WhatsApp; Telegram demoted to opt-in `NOA_TELEGRAM_MIRROR=1`. **Quiet hours:** single enforcement point `_notify_window_open`/`_wa_send_quiet` (09:00–21:00 IL) applied to all 9 owner/admin send sites + the payment-reminder loop; night messages are **queued in Redis and flushed** when the window opens (never dropped); NOA fires at a fixed IL time (`NOA_POST_HOUR_IL`, default 09:30) instead of a container-start-anchored 24h timer. **Reminder caps:** the "3 sends" was a **rolling 3-day window**, so a cart earned 3 more every 3 days forever (one live cart had **48**); now a lifetime cap + 24h minimum gap, and the pending-payment loop (which had **no cap**) capped at 3/order. **Backend errors:** `run_all_tasks` now WhatsApps the owner the failing task names when `tasks_error > 0`, deduped by failing-task-set hash with 24h TTL. | **2026-07-20 live:** two real LLM generations (Instagram/Corolla bearings, TikTok/Sportage pads) → human story opener, mechanic's tip, real catalog price, single plate-search CTA, engagement question, multi-line structure kept, 9 tags across HE+AR+EN with underscores intact, no link-footer. QR **decoded out of the final composed image** (cv2) → exactly `https://autosparefinder.co.il/api/v1/go?src=qr_instagram_w30`; picker page HTTP 200 through the public domain; media served 200 `image/jpeg` 59,963 B. Quiet hours: 03:00/06:00/08:00/21:00/23:00 blocked, 09:00/13:00/20:00 open; a simulated night alert queued to Redis and was recoverable (test entry cleaned up — nothing delivered to the owner). Cart cap live in logs: `Skip cart ba23170a… — lifetime cap reached (48/3 reminders ever sent)`. Backend healthy after rebuild+recreate, no errors from the new modules. |

**Never regress (G8, 2026-07-20):** NOA post sanitization must stay **structure-preserving and subtractive** — never collapse `\s+` across newlines, never staple boilerplate onto every post, never re-add a multi-line link footer. The lone-Hebrew-letter garble check must keep its prefix exemption (`מ-198`, `ב-2020`, `ב AutoSpareFinder`) or every real-price selling post gets mangled again. Hashtag handling must keep Arabic (`؀-ۿ`) in `_NOA_HASHTAG_RE` and must not strip `_` inside tags. **All outbound owner/customer WhatsApp must go through `_wa_send_quiet`** (or explicitly justify `critical=True`) — a new send site that calls `_wa_send` directly reintroduces 03:00 messages. **Reminder caps are LIFETIME, never a rolling window** — a rolling window is not a cap.

**Never regress:** price/margin math lives ONLY in the backend (`_customer_price_fields` + `create_whatsapp_checkout`). No client-side or per-channel price formulas. Any new channel/surface must consume the same fields. **VAT is CONDITIONAL, not flat ×1.18** — `get_supplier_vat_rate` applies 18% ONLY to LOCAL (IL) suppliers and 0% to foreign-sourced parts (most of the catalog: Car-Parts.ie/IE, SNG/UK, eBay/US). Any price ANY surface displays (incl. NOA's advertised/marketing prices) must be `cheapest-supplier cost × 1.45 + conditional VAT` — NEVER a flat ×1.18, and NEVER `base_price` (unreliable on some rows; can land near raw cost). NOA fixed 2026-07-14 (`_noa_real_catalog_fact`). Customer-facing agents may only claim programs/discounts that actually exist in code+DB. Chat agents: free-text car capture + query-strip + fitment-verified fallback must stay (a category/vocab miss must never discard a `part_vehicle_fitment` match); customer replies pass through `_strip_leaked_reasoning` + `_sanitize_internal_pricing_disclosure` (never leak chain-of-thought, draft options, or internal state under 429 fallback).


## 4. Repository & Agent Architecture


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
**Owner WhatsApp console** (`agents/owner_console.py`, added 2026-07-23): the OWNER's WhatsApp
messages (`OWNER_WHATSAPP_PHONE`) are intercepted in `routes/webhooks.py` BEFORE the customer
brain and routed here — a private ops console. Two-way owner-mode chat with AVI (default) / NOA
(prefix "נועה"/"noa") via a DIRECT `hf_text` call (NOT `get_agent("router_agent")` — that's a
JSON classifier and emits garbage on freeform chat) seeded with a live system-status block +
rolling Redis history. Deterministic commands: `סטטוס`/status, `שאיבה`/harvester, `פוסטים`/posts,
`אשר <id>`/approve (marks approved + publishes via `social/registry.dispatch`), `דחה`/reject,
`עזרה`/help — so the owner acts on NOA's approval notifications by replying. Uses its own
CATALOG-DB session (those tables aren't in the PII DB the webhook passes).
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


## 5. Hard Constraints


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


## 6. Mandatory Importer Patterns

> **⇒ The full standard now lives in [`docs/IMPORTER_RULES.md`](docs/IMPORTER_RULES.md),
> and it is ENFORCED by `backend/maintenance/audit_importers.py` (exit ≠ 0 on any
> ERROR). Run the audit before shipping an importer AND before triggering any
> import or backfill.** The section below is the quick reference; the doc is
> authoritative and explains which real incident earned each rule.
>
> **The audit PROCESS itself is documented** in `docs/IMPORTER_RULES.md` §10 — how to
> add a rule, how to validate it against reality before fixing anything (every rule
> written that day produced false positives on its first run), and why a check that is
> wrong more often than right is worse than no check. Read it before adding a rule.
>
> Written prose was never enough: on 2026-07-28 an audit of the existing 64
> importers found **146 ERROR-level violations** of rules that had been documented
> here for months — including SEVEN mutually-incompatible category vocabularies all
> writing to the same column. If you add a rule to the doc, add a check to the
> script in the same change.


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


## 7. IL Importer Site Reference

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

---


## 8. Business Rules


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


## 9. Feature Modules


**`backend/category_map.py` is the single source of truth for part categorization.**
If you add a keyword, add it THERE — nowhere else. `categories.py` is a deprecated
re-export shim. An AST check enforces that no other file defines a module-level
category rule set.

**One storable vocabulary.** `parts_catalog.category` holds ENGLISH SLUGS
(`brakes`, `body-exterior`) plus `כללי`. `CANONICAL` is DERIVED from
`part_type_taxonomy` family ids so the two cannot drift. Hebrew/Arabic names are
**display-only** (`category_map.DISPLAY`) and must never be written to the column.
Before the merge, three vocabularies were in play and `normalize_categories` was
validating a Hebrew-name mapping against an English-label set — so nearly every
mapping branch was silently discarded and parts only ever flowed INTO `כללי`.

**Matching is LONGEST-KEYWORD-WINS**, never declaration order. Specificity lives in
the keyword itself, which is what makes it safe for the RULES list to carry bare
head-nouns (`brake`, `door`, `belt`) as a lowest-precedence tier. Short RTL keywords
(≤3 chars) match with a boundary pattern that permits one Hebrew proclitic
(ה ו ב ל מ ש כ) before the word but forbids another RTL letter after it.

**The only fallback is `כללי`.** Never return `general`, `service-general`,
`accessories` or `tools-equipment` as a default — they are real categories reachable
only by a genuine match. Every importer calls `categorize_on_ingest()`; none may
hard-code a fallback. Bare fasteners (`bolt`/`screw`/`בורג`) deliberately stay in
`כללי` — a wrong category is worse than the catch-all, and in context
(`"Bolt Cylinder Head"`) they still classify correctly.

### LLM assist — a helper for a STUCK worker, not the worker (owner rule)

The deterministic keyword matcher does the work. The LLM is only consulted about
parts it genuinely could not place, and **everything the LLM answers is mined back
into permanent keyword rules**, so the matcher handles that shape of name unaided
next time and LLM demand SHRINKS instead of being constant.

| Layer | Role |
|---|---|
| `category_map` RULES | Deterministic, free. Handles ~74% of the backlog alone. |
| `db_cleanup_agent.task3b` | Asks the LLM about a small, budgeted sample of genuinely-stuck rows. |
| `category_learning.mine_tokens` | Extracts the token that predicted the category. |
| `category_learned_keywords` | One row per (token, category) VOTE. Consensus accumulates ACROSS calls. |
| `category_map.register_learned_keywords` | Activates a token once it clears the gates. |

**OWNER APPROVAL GATE (added 2026-07-27).** A keyword the LLM proposes does **not**
go live on its own — one token can move thousands of parts (`bolt` matches 16,541),
so it waits for the owner exactly like NOA's post drafts. Lifecycle:
`pending` → owner approves → `approved` (loaded + bulk-applied) or `rejected`
(never loaded, never re-proposed; the rejected set is rehydrated at startup).
WhatsApp console: **`מילים`** lists what's waiting · **`אשרמילה <word>`** approves ·
**`דחהמילה <word>`** rejects. The owner is pinged once/day via `_wa_send_quiet`
(quiet-hours-safe) when tokens are waiting. If the owner is away, learning pauses —
the correct failure mode for something that can mis-file 16,000 parts.

**EVIDENCE GATE + REVERSIBILITY (owner requirement, 2026-08-02).** The owner's
standing rule is *"fix the categories BEFORE the backfill so I don't get wrong
parts sitting in wrong categories."* Two mechanisms serve it:

1. **`category_learning.evidence_profile()`** — before a keyword can activate, it
   is checked against parts **already filed in a real category**, which is
   evidence independent of the votes. Consensus alone is not enough: it measures
   agreement *among votes cast on catch-all parts*, so it reached 96-99% on
   `קופסת`→gearbox (really storage/relay/control boxes) and `note`→service
   (really "Low Note Horn"). Two tests: **MISMATCH** (proposal isn't the
   evidence's top category) and **MARGIN** (top must beat runner-up ≥1.8×).
   Enforced in `approve()`, not just the display. Owner override:
   `אשרמילה <word> בכוח`, with the real spread shown first.
   *Absolute share was tried first and was WRONG* — it blocked `lens` (48.6%
   lighting), a good rule, because a part name has many words and its stored
   category reflects whichever word won.
2. **Provenance → exact undo.** `_bulk_apply_new_keywords` stamps
   `specifications.category_by` (the keyword) and `category_prev` (where the
   part came from). `undo_keyword()` reverses exactly those rows, and `reject()`
   calls it — so rejecting a live keyword undoes its effect instead of merely
   stopping future ones. **A gate reduces the chance of a bad rule; provenance
   makes one survivable.** One keyword moves thousands of rows (`מים` matches
   1,277), so "we'll untangle it later" is not a plan.

**NEVER-LEARN BLOCKLIST — enforced at THREE layers.** Four classes may never become a
rule: **brand names** (164 entries loaded LIVE from `car_brands` +
`parts_catalog.manufacturer`, split on spaces AND hyphens so "Mercedes-Benz" blocks
`mercedes` and `benz`), **bare fasteners** (`bolt`/`washer`/`screw`/`בורג`/`shim`/
`ring`/`nut`), **position words** (`front`/`rear`/`קד`/`אח`/`ימין` — they say *where*
a part sits, not *what* it is), and **size/quantity codes** (anything with a digit,
`xxl`, `pcs`). Enforced in `mine_tokens()` (producer), `load_into_matcher()`
(consumer) **and** `purge_blocklisted()` at startup — because filtering only on write
does not protect rows written before the filter existed (that hole went live on
2026-07-27: `bolt`/`washer` activated as `service-general` and `rover`/`land` were one
vote from mis-filing ~26,000 Land Rover parts).

**THROUGHPUT — optimise the keyword, not the part.** Per-part LLM classification of
the 403,327-part backlog would take **107 days** (25/call × 150 calls/day). Token
analysis of the real stuck population shows the **top 400 unknown tokens cover 73.6%
(~297,000 parts)**. So task3b samples the parts containing the most FREQUENT unknown
token (every call teaches a high-leverage rule), and `_bulk_apply_new_keywords()`
re-runs the matcher over the whole catch-all the moment a keyword is approved — one
keyword fixes thousands of parts at once, not the 25 that taught it.

**Guarantees (all verified live 2026-07-27):**
1. A learned keyword **can never override a hand-written rule** — `_covered_by_handwritten`
   rejects it, and on an equal-length tie the stable sort keeps hand-written first.
   (Proved: 4 votes for `brake→cooling` left `"brake pad" → brakes` intact.)
2. A token activates only at **`MIN_CONSENSUS = 3` cumulative observations** with
   **`MIN_AGREEMENT = 0.8`**. Consensus must accumulate across calls — a 25-part
   batch almost never repeats a token, so gating inside one batch would learn nothing.
   (Proved: activated on the 3rd batch, not the 1st or 2nd.)
3. Conflicting votes never activate. (Proved: engine/brakes/lighting ×1 each → stays `כללי`.)
4. The LLM may only choose from `CANONICAL`; `_VALID_CATEGORIES` is derived from it.

**Budget — why the 2026-07-27 quota blowout cannot recur.** That incident was not
"the LLM is expensive", it was an unbounded caller: `batch_size=500` inside a loop
ticking every 30s, with nothing ever learned, so demand was constant and infinite.
Three independent limits now make that shape impossible (`docker-compose.yml`):
`CLEANUP_LLM_BATCH=25` (never 500) · `CLEANUP_LLM_MIN_INTERVAL_S=180` ·
`CLEANUP_LLM_DAILY_MAX_CALLS=150`, plus provider-failure backoff. **Any new
background task that calls an LLM must have all three: a small batch, a minimum
interval, and a daily ceiling — and should feed its output back into a
deterministic rule so the LLM is needed less over time, not forever.**




**`backend/warranty_policy.py` is the single source of truth for part warranty**,
the same way `_customer_price_fields` is for price. Never re-implement the default
or the parsing in an importer.

- **Storage:** `supplier_parts.warranty_months` (+ `warranty_source`). Warranty
  belongs to the SUPPLIER OFFER, not the part — two suppliers can warrant the
  same part differently. Already returned by the search/compare API.
- **`resolve(*candidates) -> (months, source)`** takes whatever fields a source
  offers (numeric or free text, priority order) and always returns a usable
  value. `parse_months()` handles the real catalog forms —
  `אחריות לשנתיים כולל עבודה`→24, `ל 6 חודשים או 10000 ק"מ`→6, `24חודשים`→24,
  `12 months / 100,000 km`→12, and the misspelled `חריות לשנה`→12. Anything it
  does not recognise returns None rather than a guess.
- **The default is EVIDENCE-BASED:** 12 months is the dominant real value in our
  own catalog (2,919,823 of 3,734,378 populated rows, 78.2%). Override with
  `PLATFORM_DEFAULT_WARRANTY_MONTHS`. It is a PLATFORM policy (we are the
  seller), not a claim about the supplier.
- **Provenance is mandatory.** `warranty_source` is `'platform_default'` when we
  applied our own default and `'supplier'`/NULL when the figure came from the
  source. **NULL means legacy supplier data** (3.7M rows predate the column and
  were deliberately not rewritten). **Always test with
  `warranty_policy.is_supplier_stated()`, never `== 'supplier'`** — the bare
  comparison silently treats every legacy row as non-supplier. A surface that
  says "supplier warranty" MUST check this, or it presents our default as the
  manufacturer's promise.
- **Captured at source:** `catalog_scraper.py` (REX) now calls `resolve()` on
  every `supplier_parts` insert. It previously wrote no warranty at all, which
  made "Official Manufacturer Sites" the single biggest gap (293,263 rows).
- Coverage went 88.9% → **100.00%** (4,158,026 rows): 36,136 derived from real
  source data, 423,648 platform default. Any NEW importer must call `resolve()`.




`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` (117.7M params,
Apache-2.0) runs LOCALLY via **ONNX Runtime, not PyTorch** — torch adds ~800MB to
the image and ~1.5GB RSS, and this box has 12GB with **NO SWAP**, so an OOM is a
hard kill. Weights live in the persistent `worker_state` volume
(`/app/state/models/minilm-multilingual`, 458MB), NOT in the image; fetch with
`maintenance/fetch_embed_model.py`. Deps in `requirements.txt`.

**The old CLAUDE.md note rejecting local models ("~185 MB free") was STALE** — it
predated the 2026-07-20 upgrade. Live check found 5.3 GiB available. *Re-verify a
capacity claim against the live box before repeating it.*

### EVALUATE BY INPUT TYPE, NOT ONLY BY LANGUAGE (owner insight)

Scoring the model by language alone conflated two different failures: a part
NUMBER or SIZE CODE scoring badly is not a language weakness, it is a **semantic
limitation** — embeddings encode meaning and identifiers carry none. Language-only
segmentation pointed at the wrong fix ("improve Hebrew") instead of the right one
("route identifiers away from the model"). Measured precision, 11,110 ground-truth
parts:

| input type | n | fire@.75 | prec@.75 | fire@.85 | prec@.85 |
|---|---|---|---|---|---|
| english single-word | 267 | 94% | **99%** | 87% | **100%** |
| english short descriptive | 3,603 | 83% | **94%** | 49% | 96% |
| english long descriptive | 6,157 | 16% | 85% | 2% | 93% |
| hebrew descriptive | 788 | 64% | 79% | 39% | 85% |
| size/spec format | 217 | 3% | 100% | 0% | — |
| part number / code | 18 | 17% | 100% | 0% | — |
| supersession/placeholder | 59 | 5% | **33%** | 0% | — |

Findings language-splitting had hidden: (1) **short names are the BEST case**
(99-100%), long descriptive the weak English case — more words = more competing
concepts for a nearest-exemplar match; (2) codes/sizes barely fire at all, so they
were never the real danger — the earlier "it mis-files codes" claim came from a
0.45 threshold; (3) the genuinely dangerous type is **supersession/placeholder,
33% precision AND it fires** — it needs a hard pre-filter, not a threshold.

### THE CONTROLLED WRITE PATH (owner directive — NOT an open write path)

**PHASE 1 (active).** The model only sees types proven reliable, it **creates
classification RULES rather than writing categories**, and bulk apply happens only
after owner approval. `category_input_type.POLICY` blocks size/spec, part numbers,
supersession and brand-only *before* the model runs. Everything else flows through
the existing gates: blocklist → `MIN_CONSENSUS=3` cumulative + 80% agreement →
**owner `אשרמילה`** → bulk apply.

**PHASE 2 (earned, per type).** Auto-write may be enabled only for types that have
**proven consistently accurate in recorded history**. Every proposal is stamped
with its `input_type`, so every owner approve/reject is attributable evidence.
`embed_policy.autowrite_enabled()` requires ALL of: type eligible on measured
precision · ≥`EMBED_PROMOTE_MIN_DECISIONS` (50) owner decisions · ≥90% approval ·
global `EMBED_AUTOWRITE_ENABLED` · type named in `EMBED_AUTOWRITE_TYPES`. Evidence
makes promotion POSSIBLE; the owner still makes the call. Console: **`מודל`**
shows the per-type scorecard.

**Rule: never promote a model to autonomous writing on a benchmark alone — collect
per-type approval history in production first, and make the gate refuse by default.**

### What the model earned immediately
It exposed a real pre-existing bug: `_TYRE_RE` allowed ONE letter (`[rz]`) but
speed-rated tyres write TWO (`225/45ZR18`), so **every ZR tyre was invisible** to
the wheels-bearings rule. Now `[rz]{1,2}`. It also solves word-order structurally
(`Clamp Hose` ↔ `Hose Clamp`, 0.94) which keywords can only handle by enumeration.




A **third category**, distinct from the two agent layers above — do not confuse it with
either. 23 Claude Code Skills (`.claude/skills/dept-*`, git-tracked, project-scoped) built
in an isolated sandbox (`gstack_sandbox` container, its own fenced-off docker network, no
route to `internal`/`public`), security-audited, then migrated into this repo. They are
**Markdown instruction sets a Claude Code session reads and acts on** — NOT autonomous
Python agents. Nothing under `dept-*` runs inside `autospare_backend`, calls Cerebras, or has
a `job_registry` row. They only do anything when an active Claude Code session invokes them
(on-demand today; a scheduled Claude Code session is the intended path for `dept-cmo`'s
recurring target check-ins — see its own SKILL.md — not a new backend worker).

**Roster**: `dept-cmo` (department command + daily/weekly target tracking, OKR-style, every
number evidence-gated per the Truth-Only rule below), `dept-brand`, `dept-positioning`,
`dept-competitor-intel`, `dept-content`, `dept-b2b-leads`, `dept-seo-programmatic`,
`dept-seo-technical`, `dept-ppc`, `dept-crm-email`, `dept-cro`, `dept-analytics`,
`dept-market-research`, `dept-internal-comms`, `dept-keyword-seo`, `dept-geo-content`,
`dept-campaign-launch`, `dept-sop-library`, `dept-context` (shared foundation doc every
section reads), plus 4 design skills — `dept-design-review`, `dept-design-consultation`,
`dept-design-shotgun`, `dept-design-html` — adapted from `garrytan/gstack` (124k★).

**Design-skill execution is CONFINED TO THE SANDBOX — do not run the compiled `browse`/
`design` binaries directly on the production host.** Their SKILL.md/sections/vendor files
live in the real repo (harmless — markdown/JSON, no executable code), but they depend on a
~450MB shared `gstack/` runtime (`~/.claude/skills/gstack/` — Bun-compiled binaries, does
live Playwright/Chromium browser automation) that is installed at the HOST level, NOT
git-tracked. A Claude Code safety classifier blocked direct host execution of these binaries
when attempted 2026-07-27 — **that block is correct, not a false positive**: these are
third-party compiled binaries with one disclosed CVE-style vuln already found+patched (see
below); "patched the one we knew about" is not the same as "trusted to run unconfined on the
box holding customer PII, Stripe keys, and the live DB." Actual invocation of
`/dept-design-*` stays inside the isolated `gstack_sandbox` container (still running, no
network route to `internal`/`public`) pointed at a code-only copy of the frontend or a safe
read-only path to the real dev server. If host-level execution is ever wanted, it requires
the owner explicitly adding a Bash permission rule — never work around the classifier.

**Patched vulnerability (must survive any re-clone/update of `gstack/`)**: a real, publicly
disclosed critical vuln (GitHub issue #1324) let a local auth token leak via the `browse`
daemon's `/health` endpoint (`.startsWith('chrome-extension://')` check + optional/empty
`BROWSE_EXTENSION_ID`). Patched in `browse/src/server.ts` (Host-header allowlist for
`surface==='local'`, `/health` token only returned on exact origin match) and
`browse/src/terminal-agent.ts` (WebSocket origin must exactly match `BROWSE_EXTENSION_ID`,
no longer just "any chrome-extension://"). Verified via live attack replay after the patch,
re-verified after a fresh server restart, and confirmed intact on the version installed on
the real host. If `gstack/` is ever re-cloned or updated from upstream, re-apply and
re-verify this patch before use — do not trust a fresh clone.

**SHIRA/NOA wiring (2026-07-27)**: both live customer/social-facing agents (Layer A, in
`BACKEND_AI_AGENTS.py`) now have a small, additive grounding block folded into their
`system_prompt` — SHIRA (`MarketingAgent`) gets `dept-brand`'s voice rules + `dept-positioning`'s
"fitment verification before payment" differentiator when discussing value/discounts; NOA
(`SocialMediaManagerAgent`) gets the same positioning differentiator as an optional angle, not
forced into every post. **This is intentionally surgical** — condensed, hand-picked extracts
folded directly into the live prompt, NOT the full SKILL.md content (which is full of
Claude-Code-specific automation instructions irrelevant to a live chat/generation prompt) and
NOT a runtime file-read of the skill markdown (would add latency + a new failure mode to
every live agent call for marginal benefit at this scale). If `dept-brand`/`dept-positioning`
change, manually re-sync the relevant block in `BACKEND_AI_AGENTS.py` — there is no automatic
sync between the skill file and the live prompt; this is a deliberate simplicity tradeoff,
revisit only if drift becomes a real problem in practice. Every existing guardrail in both
prompts (SHIRA's Truth Rule, NOA's full personality/format/prohibition/Google-marketing
sections — see the G8 "Never regress" note above) was left untouched; changes were additive
insertions only, verified via a live import+regression check before restart.

**Verification performed (2026-07-27)**: syntax-checked, imported in isolation inside the
running container (confirmed new content present + all original guardrail text unchanged —
no regression), then a REAL live generation call through the full pipeline for SHIRA
succeeded end-to-end (Cerebras→fallback model→Gemini all 429'd that day; correctly fell
through to Groq and produced a real, on-policy Hebrew reply, Truth Rule intact). That same
test surfaced a genuine pre-existing bug (see `docs/POSTMORTEMS.md`, 2026-07-27 entry) — fixed and
re-verified live before restart. `pre_restart.sh` run, container restarted clean, zero errors
in startup logs, `HealthMonitor` pass complete. Did NOT route a synthetic test through
`process_user_message` (writes real rows to the live PII DB) since nothing in that layer was
touched — the isolated in-container test exercises the exact same agent classes/prompt/
`hf_client.py` that a real request would.

**Truth-Only Guardrail (MANDATORY, same as every other section of this platform)**: every
`dept-*` skill's own SKILL.md carries this rule already — no invented programs/discounts/
coverage numbers, every claim traced to a real query result. Applies doubly once folded into
a LIVE prompt (SHIRA/NOA above): a grounding block that encourages a stronger claim must not
weaken the existing Truth Rule enforcement already in that prompt.

---



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



NOA's publishers (`social/*_publisher.py`) only PUBLISH. `social/engagement.py` adds the
other half: **READ** comments/mentions/DMs on our own social pages and **REPLY** to them,
owner-approval-gated.

- **Uniform per-platform contract** (like `social/registry.py`): each platform exposes
  `async fetch_new(limit) -> [EngagementItem]` + `async post_reply(external_id, text) ->
  {ok,id,error}`. A platform with a missing/expired token returns `[]` / fails gracefully —
  it never raises and never blocks the other platforms. `PLATFORMS` registry (5) +
  `configured_platforms()`. **All hand-rolled on the FREE official APIs** (owner: no paid
  aggregator like Ayrshare/Blotato):
  - **Facebook** — Page comments (Graph v21.0; `FACEBOOK_PAGE_TOKEN`).
  - **Instagram** — media comments via the linked FB page (`instagram_business_account`).
  - **Telegram** — groups/channels/DMs via the **NOA admin bot** (`@Noa_autosparefinder_bot`,
    `TELEGRAM_ADMIN_BOT_TOKEN` — distinct from the customer bot `@Askparty_bot`/`TELEGRAM_BOT_TOKEN`).
    **WEBHOOK-FED**, not polled: the bot already holds a webhook (`/webhooks/telegram-admin`), so
    getUpdates would 409 — instead `routes/webhooks.py` calls `engagement.ingest_telegram_update()`
    on every inbound plain message (records status `new`; owner messages skipped), and the loop's
    `draft_new_items()` drafts them. Replies go out via `sendMessage` (`telegram_post_reply`).
    Privacy is OFF (`/setprivacy → Disable`, done 2026-07-25) so it reads all group chatter;
    else in groups it only sees mentions/replies. Opt into polling for a truly dedicated
    non-webhooked bot with `NOA_TELEGRAM_POLL=1` + `NOA_TELEGRAM_BOT_TOKEN`.
    - **Webhook secret (learned 2026-07-25):** the `/telegram-admin` handler enforces
      `X-Telegram-Bot-Api-Secret-Token == TELEGRAM_WEBHOOK_SECRET`. If a bot's webhook is
      registered WITHOUT that secret, Telegram's updates 403 (this had silently killed the
      admin approval buttons too). Both bots' webhooks MUST be `setWebhook` with
      `secret_token=TELEGRAM_WEBHOOK_SECRET`. The NOA bot was re-registered with it +
      `allowed_updates=[message,edited_message,channel_post,callback_query]`.
    - **Owner-skip is a toggle:** `ingest_telegram_update` RECORDS the owner's own DMs by
      default (so the owner can self-test by DMing the bot); `NOA_ENGAGEMENT_SKIP_OWNER=1`
      restores skipping. Replies are approval-gated regardless, so recording owner DMs is safe.
  - **Reddit** — subreddit comments + inbox (OAuth script app: `REDDIT_CLIENT_ID/SECRET/
    USERNAME/PASSWORD/SUBREDDIT`).
  - **Discord** — server-channel messages + DMs via `DISCORD_BOT_TOKEN` +
    `DISCORD_ENGAGE_CHANNELS` (REST; reply via `message_reference`). **LIVE.**
  - **Google Business** — reply to Google **reviews** (Business Profile API v4). Config:
    `GOOGLE_BUSINESS_CLIENT_ID/SECRET/REFRESH_TOKEN` (+ optional `_ACCOUNT`/`_LOCATION`, else
    auto-discovered). `_gbp_token` refreshes the OAuth access token; `external_id` is the review
    resource name. Adapter BUILT but **DROPPED by owner 2026-07-26** — GBP verification is for
    physical storefronts; an online-only marketplace can't pass it. Kept in code (harmless, not_configured).
  - **YouTube** — read + reply to comments on our channel (Data API v3, **free**, 10k units/day).
    Config: `YOUTUBE_CLIENT_ID/SECRET/REFRESH_TOKEN` (+ optional `YOUTUBE_CHANNEL_ID`, else
    `channels?mine=true`). `_yt_token` refreshes OAuth (scope `youtube.force-ssl`); reads
    `commentThreads?allThreadsRelatedToChannelId`, replies via `comments.insert` (`external_id` =
    top-level comment id = the reply parentId). Adapter BUILT; lights up when a channel + youtube-scoped
    refresh token land. (**X/Twitter dropped 2026-07-26 — pay-per-use, not free.**)
  - **`external_id` is COMPOSITE** (`chat/channel:message`) for Telegram/Discord so
    `post_reply(external_id,text)` can route without extra context; FB/IG use the raw comment
    id; Reddit uses the `t1_…` fullname. Shared `_http_json` helper; all urllib, no new deps.
  - **Walled for everyone (verified 2026-07-25, not faked):** Facebook **Groups** (Meta
    retired the Groups API 2024-04-22), **X** reading (paywalled), **TikTok** comments
    (approval-gated), **Meta DMs** (Messenger/IG-direct need app review). The only
    API-reachable "groups" are Telegram groups / Discord servers / Reddit subreddits.
- **`social_inbox` table** (catalog DB, self-created via `ensure_inbox_table`): dedupe
  `UNIQUE(platform, external_id)`; status lifecycle `new → pending_approval → replied |
  skipped`. Helpers: `record_item` (returns new row id or None on dup), `set_draft`,
  `pending_for_owner`, `resolve_inbox` (by 8-char id prefix), `mark_replied`, `mark_skipped`,
  `send_reply` (dispatches via registry). **Never use `:id::uuid` in a `text()` query** — the
  `::` cast collides with SQLAlchemy's `:name` param parser (`syntax error at or near ":"`);
  use `CAST(:id AS uuid)`.
- **NOA reply generator** `draft_reply_text(item)` — LLM (`hf_text`), replies in the SAME
  language the customer wrote (he/ar/en via `_detect_lang`), short/human/on-brand, plate-search
  CTA, **never invents prices/stock**, no hashtags. Same truth-only rules as the customer
  agents apply.
- **Public-reply cap + hand-off role (owner rule 2026-07-25):** NOA does NOT run an endless
  public conversation. `_draft_or_handoff` counts how many replies we've already SENT to that
  customer (`social_inbox` status='replied', per platform+author); once it reaches
  `NOA_ENGAGEMENT_MAX_REPLIES` (default 3), instead of another public answer she drafts a
  localized (he/ar/en) **hand-off** inviting them to a private support chat — the `/api/v1/go`
  channel picker (WhatsApp/Telegram/web chat), `NOA_CONNECT_URL` overridable. Applies on every
  platform (poll- and webhook-fed). Fresh customers still get a normal helpful reply.
- **`poll_once(db, autoreply=False)`** — one pass: fetch→record(dedupe)→draft→`pending_approval`
  (or auto-send if autoreply); skips our own page's replies (`SOCIAL_PAGE_NAME`); exception-safe
  per platform. Returns a summary dict for logging.
- **Supervised loop** `_noa_engagement_loop()` in `BACKEND_API_ROUTES.py` (registered at
  `startup()`): every `NOA_ENGAGEMENT_INTERVAL_S` (900s); idles ≥1h when nothing configured;
  WhatsApps the owner (via `_wa_send_quiet`, quiet-hours-safe) when drafts await approval.
  Toggles: `NOA_ENGAGEMENT_ENABLED` (default 1), `NOA_ENGAGEMENT_AUTOREPLY` (default 0 =
  owner approves each).
- **Owner WhatsApp console** (`agents/owner_console.py`): `תגובות`/`inbox` lists NOA's drafts;
  `ענה <id> [text]` approves+sends (own text overrides the draft); `דלג <id>` skips.
- **Credential state:** **Facebook — FULLY LIVE + E2E-verified 2026-07-25.** `FACEBOOK_APP_ID`+
  `FACEBOOK_APP_SECRET` in `.env`; `FACEBOOK_PAGE_TOKEN` is a **non-expiring PAGE token**
  (`expires_at:0`) minted by exchanging a fresh user token (`fb_exchange_token` → long-lived →
  `/me/accounts`) and carries `pages_read_engagement`+`pages_manage_engagement`. Live E2E proven
  (comment→read→NOA draft→reply posted on FB→verified→deleted). To refresh if ever revoked, re-run
  that exchange with a fresh user token that includes `pages_manage_engagement`. **Instagram —
  blocked by Meta:** the IG account is under review and NOT linked to the FB page
  (`instagram_business_account:none`, business IG edge permission-denied), so the FB-Graph IG path
  can't reach it; the alternative is the Instagram-Login API (`graph.instagram.com`, separate
  `AutoSpareFinder Social-IG` app) which needs an IG User token the owner can't generate until
  verification clears. IG adapter code is ready; activates when a token lands. The loop activates
  automatically once a valid token lands in `.env` — no code
  change. **Telegram + Discord are LIVE** (Telegram via `TELEGRAM_ADMIN_BOT_TOKEN` webhook;
  Discord bot `autosparefinder` in the AutoSpareFinder server watching `#general`
  `DISCORD_ENGAGE_CHANNELS=1528455754787328122`, `DISCORD_BOT_TOKEN` set — invite needed
  *Requires OAuth2 Code Grant* OFF + Message Content Intent ON; E2E send+read verified). **Reddit
  adapter is BUILT** — lights up when a free script app's creds are added. X/TikTok/Meta-DMs/FB-Groups
  stay walled (see FIXES_TRACKER 2026-07-25b). Test: `devtests/engagement_lifecycle_test.py`.



NIR's "superpower": find new sellers on the web, vet them, and onboard them so their offers
enrich search + the price **compare**, and orders route to them e2e.

- **Real web search** = `hf_client.gemini_web_search(query)` — Gemini **Google-Search grounding**
  (returns a grounded answer + the source URLs it used). The server IP is anti-bot blocked for
  direct HTTP, so this is how backend agents "search the web." The free Gemini key 429s often →
  `discover_sellers()` **falls back** to an LLM-propose (Cerebras `hf_text`) + a **live domain
  fetch verify** (`_verify_domain`). **Never fabricates** — unverified/low-score candidates are
  skipped (score gate 0.55 + `http_ok` required).
- **Onboarding** (`onboard_seller`): dedupe by `lower(name)` OR domain; INSERT into `suppliers`
  with **`is_active=FALSE`** and sourcing metadata in the `credentials` JSONB
  (`{source:'nir_sourcing', status:'pending_review'|'pending_credentials'|'approved', needs, reasons, signals}`).
  Nothing goes live in customer-facing compare without **owner approval**.
- **Owner review** (WhatsApp console): `ספקים` list pending · `מקורות` run a discovery cycle now ·
  `אשרספק <id>` approve (→`is_active=TRUE`) · `דחהספק <id>` reject.
- **Proactive loop**: `_supplier_sourcing_loop()` (supervised, weekly; `SUPPLIER_SOURCING_INTERVAL_S`,
  toggle `SUPPLIER_SOURCING_ENABLED`) derives gap queries from `search_misses`, onboards pending
  candidates, WhatsApps the owner to review.
- **Order e2e / compare need NO new plumbing**: a seller becomes orderable + shows in compare the
  moment it is `is_active=TRUE` AND has priced `supplier_parts` rows — routing is by
  `OrderItem.supplier_part_id` → `SupplierPart→Supplier` (trigger_supplier_fulfillment) and compare
  keys on `s.is_active`. So onboarding a seller still needs a **per-seller price connector/importer**
  to write `supplier_parts` before it enriches compare (same as any importer). Automated dropship
  **auto-buy** (`place_order`) is still a stub on every connector — build it per seller when that
  seller's API credentials arrive; do NOT ship a speculative generic connector (each API differs).
- **The 5 NIR dropship todos** (Turn 14, Keystone, Meyer, ATD, ASAP Network) are registered as
  `pending_credentials` supplier rows with the exact owner step in `needs`; each activates when the
  owner supplies its account/token. **ASAP Network is the highest leverage** (one token = a whole
  network of suppliers + millions of ACA-standard SKUs w/ fitment).



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

## 10. Operations Reference


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

### System Review Commands

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

### Architecture Quick Reference

- **Backend container**: `autospare_backend` — uvicorn + supervised background tasks
- **Persistent volume**: `worker_state:/app/state` — survives OOM restarts
- **Meili checkpoint**: `/app/state/meili_sync_checkpoint.json` — resume after crash
- **Freesbe checkpoint**: `/app/state/freesbe_import_progress.json`
- **Worker logs**: `/app/state/logs/`
- **DB**: PostgreSQL via `DATABASE_URL` env var
- **Search**: Meilisearch at `MEILI_URL` (http://meilisearch:7700)

---

### Server Specs (Contabo VPS — upgraded 2026-07-20)

| Resource | Spec |
|---|---|
| **CPU** | **6 vCPUs** — AMD EPYC @ 2.0 GHz (1 thread/core, QEMU/KVM virtualised) |
| **RAM** | **12 GB** (11.68 GiB) — **no swap configured** |
| **Disk** | 145 GB virtual disk (QEMU, SSD-backed by Contabo), 109 GB used / 36 GB free |
| **OS** | Ubuntu 24.04 LTS, kernel 6.8.0-136 |
| **Hosting** | Contabo standard VPS |
| **IP / SSH** | 161.97.158.177, port 63159 |

**Container memory limits (post-upgrade 2026-07-20)**:
| Container | Limit | Notes |
|---|---|---|
| postgres_catalog | 5120m | Up from 3072m |
| postgres_pii | 1024m | Up from 768m |
| redis | 512m | Up from 256m |
| backend | 4096m | Unchanged |
| meilisearch | 2560m | Up from 1536m |
| frontend / nginx | 128m | Unchanged |

**Postgres tuning (applied 2026-07-20 via docker-compose.yml command)**:
- `shared_buffers=2GB` (was 256MB) · `work_mem=32MB` (was 8MB) · `maintenance_work_mem=256MB` (was 64MB) · `effective_cache_size=8GB` (was 512MB) · `shm_size=512m` (was 256m)

**Capacity reality check** — 11 containers run concurrently (3× Postgres, Meilisearch, Redis, backend, frontend, Nginx, FlareSolverr, WhatsApp bridge, 2× backup). Load average normally sits 8-12 on 6 CPUs (~1.5-2 per core); above 18 is oversubscription. Key constraints:
- No swap → RAM exhaustion = immediate OOM kills, no graceful degradation.
- **6 vCPUs → `PARALLEL_SESSIONS = 2`** (env `HARVESTER_PARALLEL_SESSIONS`; lowered 4→2 on 2026-07-23). 4 was fine when the IL-market queue drained fast and the harvester idled between bursts; after the full-catalogue seeding (6,000-model backlog) the harvester runs FLAT-OUT and 4 sessions pinned flaresolverr at ~577% CPU / load 18.7 (oversubscribed) → DB statement-timeouts failed heal/parity tasks + starved sync_prices' heartbeat. Also `INTER_MODEL = 20 s` (env `HARVESTER_INTER_MODEL_S`, was 5) for duty-cycle headroom. The box is 6 vCPU (the 4→6 upgrade, active); raise sessions back toward 4 ONLY after re-measuring load. **FlareSolverr Chrome LEAK — root-fixed 2026-07-23 (FlareSolverr is no longer the fetch engine):** FlareSolverr orphans LIVE Chrome on `sessions.destroy` and accumulates renderers within a session (leaked chromium are running procs reparented to PID 1, NOT zombies). The harvester used to route EVERY page (~80/model) through FlareSolverr's browser → constant churn → 577% CPU / load 18.7 → DB statement-timeouts. **Fix:** car-parts.ie is server-rendered (proven by the full-catalogue seeder's plain-`urllib` fetches), so the harvester now uses FlareSolverr **ONLY to mint a `cf_clearance` cookie (~2×/hour, `_solve_clearance` → destroys the session immediately)** and fetches every page via **plain `urllib` + that cookie** (`http_get`; re-mints on 403/503). `fs_get` is now a thin shim over `http_get`; the session pool / per-cycle create-destroy / worker `session_id` are all gone; workers are cheap HTTP threads. Verified: FS chrome dropped 40-52→~9 and held, load 21→10, full parts still harvested. **Never reintroduce a per-page FlareSolverr call** — that is the leak. If chrome ever climbs, check `docker exec flaresolverr ps -e | grep -c chrom` (should be a handful, spiking only during a ~2×/hour solve). Env: `HARVESTER_CLEARANCE_TTL_S` (cookie refresh, default 1500s).
- `idle_in_transaction_session_timeout = 30min` (set via ALTER SYSTEM 2026-06-30).
- Watchdog `BLOCKER_S = 2700s` (45 min) — moot for active-backend connections (never killed), relevant only for orphan-detection fallback.
- `DB_AGENT_TASK_TIMEOUT_S = 3600` — per-task timeout inside `run_all_tasks`. Set in `docker-compose.yml`.

---

### Meilisearch Sync

`meili_sync.py` previously had **no automated scheduling** — it ran once manually (2026-06-24) and silently drifted 6 days / ~580K docs behind the catalog. Added `_meili_sync_loop()` in `BACKEND_API_ROUTES.py`, registered at startup via `_supervised_task("meili_sync_loop", ...)`. Runs `python3 /app/meili_sync.py` every **2 hours** in incremental mode (`MEILI_REBUILD=0` env already set, no full rebuild).

**2026-07-02 rewrite — three root-caused bugs in meili_sync.py (do not reintroduce):**
1. **Incremental resume by id-position was broken by design.** Parts get random UUIDv4 ids; the old resume (`offset=total`, `ORDER BY id`) only saw rows sorted past the previous end position — new parts land at *random* id positions and were silently skipped every incremental run. This is what created the 620K-doc gap while the checkpoint claimed complete. Fix: a completed checkpoint (offset==total, no last_id) now triggers **updated_at-based incremental mode** — `WHERE updated_at > (last run start − 1h margin)`. The completed checkpoint's `updated_at` is the run's START time so mid-run changes are re-checked next cycle.
2. **`OFFSET N` pagination is O(N·logN) per batch** — measured ~100s/batch at offset 195K (full pass ≈ 23h). Fix: keyset pagination `WHERE id > $last_id::uuid ORDER BY id LIMIT batch` — PK index scan, constant per batch. Checkpoint stores `last_id` (and `cutoff` if an incremental run is interrupted, so resume keeps the same cutoff).
3. **No single-instance guard** — the 2h supervised loop spawned a sync while a manual catch-up was mid-pass; the two clobbered each other's checkpoint file. Fix: `fcntl.flock` on `/tmp/meili_sync.lock` at entry — a second instance prints a notice and exits 0.

- To check sync status: `docker exec autospare_backend cat /app/state/meili_sync_checkpoint.json` (shows offset, total, updated_at)
- Index doc count vs catalog: query Meilisearch `/indexes/parts/stats` and compare `numberOfDocuments` to `SELECT COUNT(*) FROM parts_catalog WHERE is_active`
- If index is significantly behind and you need an immediate catch-up: `docker exec -d autospare_backend python3 /app/meili_sync.py` (runs incremental sync in background, can take 30-90 min for millions of docs)
- `lookup_oem_spec` task times out at 1800s by design (`DB_AGENT_TASK_TIMEOUT_S` env, default 30 min) — also hits Cerebras/HF API rate limits; this is a pre-existing ceiling, not a new bug.



