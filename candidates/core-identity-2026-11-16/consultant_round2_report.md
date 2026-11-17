# CORE consultant - round 2 (2026-11-16, independent, read-only)

Scope: base_price writers, independent recount, owner-page evidence method, harvester diff + pre-midnight schedules.
Rules kept: every DB session `SET default_transaction_read_only=on`; no file under `backend/` edited; no container
restarted; no car-parts.ie call. Scratch: `/tmp/claude-0/-opt-autosparefinder/consultant_2026-11-16/` (scripts, raw
outputs `recount.json`, `all_pp_out.json`, `s25_out.json`).

Status labels: VERIFIED = checked against live DB/code this session; ASSUMED = inferred, what would confirm it is named.

## 0. Two process findings first (VERIFIED)

(All times Israel local unless marked UTC.)

1. **The WIP was stashed and popped on the bind-mounted production tree.** At ~23:42 IL `git stash list` showed
   `stash@{0}` holding `base_price_rule.py`, the importer, harvester and the two new maintenance scripts; `backend/base_price_rule.py`
   did not exist; a minute later everything was back (the three files now have mtime 23:42:33 IL = 21:42:33 UTC). `./backend` is
   mounted at `/app`, so during that window any newly spawned `car_parts_ie_import_generic.py` subprocess would have run the HEAD
   importer (writes `price_ils x 1.45` into base_price) and `cpie_product_page_harvest.py` did not exist. The harvester log shows
   `Cycle 4610 done - queue drained ... Resting 3600s` at 23:37:55 IL, so the rest covers the window: no HARVESTER-launched import
   ran. The `/api/v1/system/collect` relay path (which also spawns the importer) is not excluded - unverified. Do not use
   stash/checkout on this tree.
2. **My own first sampling query was a full parallel seq scan (killed after ~5 min).** `SELECT id::text id ... ORDER BY id` sorts by
   the TEXT alias, not the uuid PK (EXPLAIN: `Sort Key: ((id)::text)` over `Parallel Seq Scan on parts_catalog`). **The same
   construct is in the WIP `maintenance/cpie_base_price_repair.py --build`** (`SELECT id::text id FROM parts_catalog WHERE ...
   ORDER BY id LIMIT $3`): every 500-row batch would seq-scan + sort ~1.2M rows (~2,400 scans for the full build). Fix: select the
   uuid (`SELECT pc.id ... ORDER BY pc.id`), cast in Python - verified plan becomes `Index Scan using parts_catalog_pkey`.

## 1. base_price writer trace

DB triggers (VERIFIED, pg_trigger/pg_proc): only `trg_meili_part_deactivated`, `trg_meili_part_deleted` (meili_pending_deletes) and
`trg_sync_parts_catalog_mfr` (manufacturer_id) on parts_catalog; none on supplier_parts; **no function body mentions base_price**.

Car-Parts.ie supplier row: `88f6ce24-...`, name `Car-Parts.ie`, country `IE`, vat_price_basis NULL (foreign -> 0% VAT, basis irrelevant).

| Writer (file:line) | Formula | Replace / guarded | Reachability (evidence) | Can recreate divergent signature on CPIE parts? |
|---|---|---|---|---|
| `base_price_rule.apply_to_parts` (base_price_rule.py:90-122, WIP) | rule target | full replace, SKIP LOCKED | called by the importer after every file (car_parts_ie_import_generic.py:489-493, WIP) | No - it IS the rule. Caveat 1.a below |
| `db_update_agent.apply_base_price_rule` (1297) via `fix_base_prices`/`normalize_base_price` | rule target | full replace | **dormant as a sweep**: commented out of run_all_tasks (4503-4504) and blocked in `run_task` by `OOM_BLOCKED_TASKS` (4325-4327) | No |
| same, per-part from `db_cleanup_agent` healers (1161, 1239, 1477, 1600) | rule target | full replace | live, 30 s cleanup loop (`run_cleanup_loop`, supervised `cleanup_loop`) | No; only parts with base NULL/0 or reactivated/healed |
| `maintenance/cpie_overwrite_repair.py` rebase (366, 435) | rule target | full replace | manual, manifest-gated | No (caveat 1.a) |
| **HEAD** `importers/car_parts_ie_import_generic.py:284,343-345` | `price_ils x 1.45` (foreign cost, 0% VAT) | CASE (only if >0) - i.e. overwrites on every re-harvest | live until the WIP is committed+used; subprocess per harvested model | Not the 1/1.18 signature, but a 2nd formula: wrong whenever the price-basis offer is not this CPIE offer (605/19,705 sample parts have a non-CPIE basis) |
| historic importer (68b8ac9, 2026-07-18): `cost=price/1.18; importer_price_ils=cost; base=cost x 1.45` | the legacy signature | - | removed 2026-08-26 (7d9de63) | This is the ORIGIN of the 1/1.18 signature; importer_price_ils is never cleared afterwards (ON CONFLICT keeps the old value) |
| `maintenance/margin_fix_batched.py:39` | `ROUND(importer_price_ils x 1.45,2)` for every active part with importer>0 and base>0 | full replace | **dormant, no caller (grep); whole-file grep: no exit/DEPRECATED guard, runs `asyncio.run(main())` at line 75 unconditionally** | **YES - exactly the legacy signature, catalogue-wide.** One manual run undoes any repair. Recommend a hard exit guard like `price_audit_fix.py:37` |
| `maintenance/vat_fix_batched.py:36` | `max/1.18 x 1.45` | full replace (vat_rate=0.17 rows) | dormant, no caller | Unlikely (CPIE max_price normally NULL) |
| `maintenance/price_audit_fix.py:108,131,154` | old formulas | - | exits at line 37 (DEPRECATED) | No |
| `maintenance/repair_vat_paths_151.py:45,51` | `online x MARGIN` | logged one-off | manual | Not the signature |
| `scripts/fix_oem_price_gaps.sh:79` | copies counterpart's base | CASE src>0 | manual, no caller; only parts with importer_price_ils **= 0** (CPIE inserts NULL) | No (in practice) |
| `catalog_scraper.db_upsert_part` (1395, callers 3611 run_brand_discovery, 4035 _run_category_discovery) | `base_price = price_usd` (USD number, raw cost) | **full replace by sku** | live: night_pipeline `brand_discovery` (monthly, >=03:15 IL) and `category_discovery` (daily) | Only on SKU collision; CPIE SKUs are `BRAND5-<article text>` (e.g. `RENAU-№: 9 2061 1`), scraper SKUs are bare part numbers -> unlikely. But this writer itself violates the rule (prior inventory marks catalog_scraper FIXED_AND_VERIFIED - stale) |
| `oempartsonline_importer.py:541` | its own | CASE base NULL/0 | importer | No |
| `harvesters/spareto_harvester.py:285` COALESCE; `acura_browser_harvest.py:78,91`; `freesbe_importer.py:277,314,408`; `rockauto_price_import.py:175` (only 0); `xls_prices_import.py:175` (only 0); IL importers (mct, delek, toyota, kia, bmw, porsche, ...) | own formulas | various | manual / IL-only SKUs | Not on CPIE rows (different SKU namespaces / only NULL-0) |
| `routes/admin.py:4536,4549` (admin upload) | uploaded price | full replace | admin endpoint | Only if an admin uploads CPIE SKUs |
| SELECT-only (not writers): ebay_price_sync:309, aliexpress_price_sync:307/491, routes/system.py:224, BACKEND_API_ROUTES:3011 | - | - | - | - |

Influences (VERIFIED): the rule reads `importer_price_ils -> max_price_ils/1.18 -> online_price_ils` for the estimate
(price_policy.py:273-296). The CPIE importer's ON CONFLICT never updates `online_price_ils`/`min_price_ils` and keeps the old
`importer_price_ils` (car_parts_ie_import_generic.py:336-348), so the legacy `offer/1.18` importer value lives forever and
is the estimate input whenever the part loses its visible offer.

1.a **Latent divergence between writers (VERIFIED in code, 0 occurrences in sample):** `price_policy._BASIS_REGISTRY` is loaded only
in uvicorn (`BACKEND_API_ROUTES.py:2780`). The importer subprocess, `cpie_base_price_repair.py` and `cpie_overwrite_repair.py`
run with it unloaded, so for a LOCAL basis offer with vat_price_basis `inclusive` they compute `cost x 1.45` instead of
`cost/1.18 x 1.45`, and for `unknown`/NULL they price instead of blocking. In my sample registry-loaded vs unloaded targets were
identical for all 19,705 parts (the local basis suppliers there are Fiat/Subaru official importers), but the "one rule" is not one
rule across processes. Fix: `base_price_rule.get_rate` (or apply_to_parts) should load the registry from `suppliers` first.

Prior inventory `candidates/maint-safety-2026-11-01/files/docs/BASE_PRICE_WRITERS.md` is stale for at least: the CPIE importer
(marked FIXED_AND_VERIFIED but still wrote its own formula at HEAD), catalog_scraper (writes raw price_usd), and it does not list
margin_fix_batched.py as a live risk.

## 2. Independent recount

Method (VERIFIED): 400 random uuid start points (seed 20261116), the next 50 active `specifications->>'source'='Car-Parts.ie'`
parts by PK after each = **19,705 distinct parts**. Target = `base_price_rule.inputs_sql(True)` + `target()` run inside the
container at the live rate 3.21 (system_settings `ils_per_usd`), with the VAT-basis registry both unloaded and loaded (identical
for all 19,705). CIs: Wilson 95% (simple random) and cluster-robust (50-part clusters) - they agree (e.g. divergent 40.47% +-0.66).
Population now **1,197,625** active CPIE parts (full count, VERIFIED; 815,715 = 68.11% carry the legacy signature exactly;
1,923 base NULL/0). The primary's 1,204,561 / 820,057 has drifted by ~7K parts.

Legacy-signature counting note: with exact numeric half-up rounding (as SQL does) the sample gives 67.42% [66.76, 68.07]
(cluster +-0.63) vs 68.11% population - consistent within ~1 pt (the population count ran ~10 min later on a moving table).
A float test with a 0.005 tolerance gives 69.62% - the tolerance admits half-cent ties. Use exact numeric equality.

| class | share of CPIE parts (95% CI) | ~parts |
|---|---|---|
| divergent (target exists, base != target) | **40.47% [39.79, 41.16]** | ~485K |
| - source `offer` | 7,076 = 35.91% (54.0% of the 13,109 offer-sourced) | |
| - source `estimate` | 899 = 4.56% [4.28, 4.86] (13.7% of 6,561 estimate-sourced) | |
| no target (`no_basis`) | 35 = 0.18% | |
| `other_class_only` | 0 in sample | |
| legacy signature (float test) holds: 13,718 rows | 6,862 divergent (all offer-sourced), 3,529 offer-correct (importer_price_ils == offer, no /1.18), 3,327 estimate-correct | |

The primary's ~41% is confirmed. **The legacy signature is NOT a divergence test**: half the legacy rows are correct under the rule.

Is "divergent" always a defect? Breakdown of the 7,076 offer-sourced divergent rows by base/target:
- **6,239 ~ 1/1.18** (31.7% of all CPIE parts) - the VAT-strip defect; 6,084 have importer_price_ils == offer/1.18. Real defect.
- 105 within 1% and 131 higher - the offer price moved since the last base write; target is right (stale, a defect).
- 42 divergent rows (all sources) differ by exactly 0.01 - rounding artefacts, not defects; exclude.
- ~605 lower and not 1/1.18: **251 of them (41%)** have a basis offer that looks double-margined; the rest are offer price
  increases since the last base write. Double-margin suspect = CPIE basis offer `price_ils == round(online_price_ils x 1.45, 2)`
  (+-0.02): 920 sample parts (4.67% [4.38, 4.97]); 263 of them divergent, 657 already "correct" (base == offer x 1.45).
  Full count on all CPIE offers (VERIFIED): **59,173 of 804,471 offers (59,162 customer-visible)**, 59,168 carry the legacy
  SKU text (`№: ...`), last updated <= 2026-09-10. Relation to #129 (VERIFIED, `maintenance/repair_double_margin_129.py`): that
  repair covers the same CLASS (an importer wrote the margin price into `supplier_parts.price_ils`) but only for 11 named Israeli
  importer suppliers (SUPPLIERS tuple), selection `base == 1.45 x importer_price_ils`; **Car-Parts.ie was out of scope**, and its
  rule would not select these rows anyway (the evidence here is online_price_ils, not importer_price_ils). So: known class, the
  existing tool does not apply to CPIE. Which leg is wrong (offer or online_price_ils) is ASSUMED - confirm with a handful of
  source pages. If the offer is inflated, the customer pays cost x 1.45 x 1.45 on ~59K visible offers; writing base_price from
  them bakes it in.
- **Stale legacy-SKU offers (separate customer-visible finding):** 223,993 CPIE offers carry the legacy `№` key and 223,482 of
  them were last updated before 2026-10-01 (full count, VERIFIED). In the sample 3,343 parts (16.97% [16.45, 17.50]) have a VISIBLE
  `№` CPIE offer, 3,333 of them stale (latest one touched 2026-11-15). The current importer writes bare/`BRAND~article` keys, so
  these September prices are shown to customers and never refreshed. Population-wide visibility of the 224K is not counted.
- 87 Fiat / 43 Subaru official-importer, 12 eBay, 2 AliExpress basis offers - rule-correct targets (cheapest class-compatible
  visible offer; 605 sample parts have a non-CPIE basis).
- USD-rate effects: 0 sample basis offers are USD-only, so the 3.21 rate moves no CPIE target today.
- Estimate-sourced divergence (899): small drift of importer_price_ils; 4,262 sample rows have `max_price_ils == online_price_ils`
  (a foreign cost in the LOCAL estimate input, against #126). For estimate rows the legacy value IS rule-correct and the customer
  TOTAL is right by coincidence: (offer/1.18) x 1.45 + 18% VAT = offer x 1.45.

Impact: base_price is internal - search tie-break (routes/parts.py:1635,1661), Meili sortable attribute (meili_sync.py:110),
unpriced-targeting predicates. Customer prices come from the offer via price_policy (BACKEND_AI_AGENTS.py:3026-3046,
routes/cart.py:180). The base_price repair is a ranking/consistency fix; the double-margin and stale offers are the customer-price
problems.

**Recommended manifest definition** (part in manifest iff ALL hold at build time, re-checked at apply):
1. `is_active` and `specifications->>'source' = 'Car-Parts.ie'`;
2. target source == `offer` (estimate-sourced rows: separate, separately approved manifest, or leave);
3. `base_price IS DISTINCT FROM round(target, 2)` AND `|base_price - target| > 0.01`;
4. target computed WITH the VAT-basis registry loaded, and equal to the unloaded target (else exclude + report);
5. basis offer NOT double-margin-suspect (`price_ils == round(online_price_ils x 1.45, 2)` +-0.02) and NOT a stale legacy-key offer
   (`supplier_sku LIKE '%№%'` and `updated_at < 2026-10-01`) - both go to an offer decision first;
6. part NOT in the wrong-offer set (`classification_1043_final_v2.json`; 16 sample parts are);
7. keep the apply-time CAS on `current` and the rule re-check (already in the WIP script); fix the build query's text-sort (section 0).

Size: with ALL exclusions **4,586/19,705 = 23.27% [22.69, 23.87] -> ~279K parts** (4,067 of them the 1/1.18 class). Without the
stale-key exclusion (rule 5b) it is ~34% / ~410K; the difference (3,251 sample parts = 16.50%) is parts whose only basis is a
September offer. Expect a Meilisearch incremental wave of the same size (updated_at is bumped).

## 3. Owner-page evidence method

Files read (VERIFIED, via docker exec): `product_page_canary.jsonl` 25 rows (23 verified, 2 no parsable product) and
`product_page_owner_group.jsonl` 491 rows at 23:43 IL (harvest pid 99297 still running, started 23:28 IL; classification input `/tmp/cls.json`
sha256 `4d93ba50...` == `classification_1043_final_v2.json`). Owner group: 461 verified (436 product_page JSON-LD, 25 listing_row),
29 no parsable owner product, 1 fetch failure; all verified rows currency EUR; 107 brand differences are case-only (`AMiO`,
`febi bilstein`); sku == article verbatim for every verified row (leading zeros preserved: `00251`, `02051`).

Is brand==owner AND sku==article from the page's own JSON-LD sufficient? For a product page (`/p-<id>`) it is strong evidence
that the page is the owner brand's article, and verify() fails closed on brand/article/currency/price. Weak points:
1. **OutOfStock: 130 of 461 verified (28%)**. verify() accepts them; the current offers are all `is_available=true`. Repairing the
   price while keeping the offer available shows a price for an item the page says is out of stock. Recommend: write the price
   but set is_available=false (owner decision), or exclude.
2. **listing_row (25)**: identity = title `startswith(owner_brand)` (normalised) + article on a model listing page that shows many
   brands. Prefix match lets a short owner token match a longer different brand (e.g. `ABS` vs a brand starting `ABS...`). Treat as
   lower-grade; I would require product-page evidence or an exact brand-token match.
3. `currency` missing (None) is accepted as EUR (verify(): `not in (None,"EUR")`). No row hit it; tighten to == "EUR".
4. Only the FIRST JSON-LD `Product` block is read; an AggregateOffer/`offers` list without `price` correctly fails closed.
5. `_norm` strips all punctuation: `12-34` and `1234` collide within one brand. Not observed; low risk.
6. Pack size/variant: JSON-LD carries no quantity. Identity by brand+article already fixes the product, and the importer prices
   listing rows with the same convention, so consistency holds; e.g. FEBI 27058 "Wheel Nut" at 87.73 ILS and FEBI 32440
   "Wheel Bolt" at 9.43 ILS cannot be judged per-unit vs pack from the page - unverified.
7. Price basis: page price and the importer use the same EUR price and the same stale `EUR_ILS_RATE=3.48` (set 2026-08-26),
   so the target is consistent with every other CPIE offer (Irish VAT treatment identical) - not a fresh FX rate.
8. Side effect: `cpie_product_page_harvest.py` imports the harvester module, so it writes into the harvester's log
   (the `cf_clearance reused from cache` and `fetch failed: TimeoutError` lines at 23:28/23:34 IL are from it) and, on a mint,
   the harvester's fs-health file. Harvester health readings during the run are not purely the harvester's.

25-row random sample (seed 11162026; page ILS vs current offer vs foreign brand's own `BRAND~article` offer) - full table in
`s25_out.json`. **13/25**: the current offer price EXACTLY equals a foreign brand's own `BRAND~article` offer (e.g. METALCAUCHO
02051 sump washer: page 2.02, current 42.11 = FEBI-BILSTEIN~02051 42.11). **4/25 mismatch** (a foreign offer exists at a different
price, which weakens "current = foreign price" for those rows): SCHRADER 3199 current 39.08 vs NGK 39.50; FEBI 27058 431.38 vs
MEAT-DORIA 432.22; NRF 31217 75.48 vs KROON-OIL 72.70; NRF 34043 19.87 vs FEBI-BILSTEIN 20.85 (the classification's payload-row
equality may predate the foreign offer's own re-price - unverified). **8/25**: no foreign `~article` offer exists, so the
"current is foreign" side rests on the classification alone (e.g. FEBI 32440 wheel bolt: page 9.43, current 295.42). Page prices
look plausible for the named owner products; none of the 25 equals a foreign offer price.

Checks over all 461 verified rows against live supplier_parts (VERIFIED): offer still exists and still points to the same part
461/461; price unchanged since the classification snapshot 461/461 (no self-heal yet); part active and name contains owner
brand + article 461/461; page price equals a foreign offer 0; ratio page/current > 5x in 130.

Rows in `H|creator=owner` that should NOT be repaired from the owner page:
- **KAMOKA 25216** (offer in classification v2): current price 26.13 already EQUALS the owner page 26.13. The "wrong offer"
  verdict for it is evidently false (equal price to a foreign payload row is not proof of wrongness) - no-op, and a signal to
  re-check other classification rows that rest only on price equality.
- the 130 OutOfStock rows - not without an availability decision (above);
- the 25 listing_row rows - lower-grade identity, see 2;
- two pairs share one listing URL (UMBRA RIMORCHI 15009/15010 on `/honda/accord-viii-cu/towbar`, verified separately by
  article - fine; A.B.S. 16211/16244 unverified);
- the 30 unverified rows obviously; and the G group (66 rows, `mixed_part_identity_G`) is correctly not in the harvest prefix.

## 4. Harvester diff + schedules

Diff reviewed (`git diff backend/harvesters/car_parts_ie_flaresolverr_harvester.py`, 84 lines changed per `git diff --stat`):
- OK: `ensure_clearance` declares `global _CLEARANCE_FAILED_TS` (line 653), so the new assignment does not create a local;
  `_save_clearance_cache` takes `_CLEARANCE_LOCK` only after `_solve_clearance` released it (no self-deadlock on the
  non-reentrant Lock); cache rejected when stale/absent cf_clearance.
- Bug (minor): `reject_cookie` = the CURRENT global cookie at the time of the forced call, not the cookie the failing request
  used. With 5 worker threads, a thread whose request 403'd on an old cookie, arriving after another thread re-minted, rejects the
  NEW good cookie (current == cache) and pays another FlareSolverr solve. Pass the failing request's `ck` into
  `ensure_clearance(force=True, failed_cookie=ck)` and skip the solve when the current cookie differs from it.
- Bug (minor): the trailing `_log_fetch_failure(url, "HTTP 403/503/429 after clearance refresh")` after the loop in `http_get` is
  unreachable (attempt 0 continues, attempt 1 returns `HTTP <code>`); the reason label never appears.
- Gap vs the diff's own purpose ("a failed fetch used to return \"\" with NO trace"): `http_get` still returns `""` silently
  in cookie mode when `ensure_clearance()` leaves no cookie (`if not ck: return ""`) and in session mode when no session id
  exists; `_fs_get_via_session` failures are not routed through `_log_fetch_failure` either.
- Race (minor): two processes write the same tmp path `cpie_clearance.tmp` (write_text, then chmod, then replace) - interleaving can
  publish a truncated file (loader then fails = harmless) and the file exists for a moment with the umask mode before chmod 0600.
  Use a per-pid tmp created with `os.open(..., 0o600)`.
- `pending > 0` gating of the mint and of the no-clearance guard: correct for a drained queue.
- The running harvester (pid 56, started 18:07:40 IL) loaded its code at start; it minted at 22:37, 22:56, 23:01 IL while the queue
  was drained and never logged "reused from cache", which matches the PRE-diff behaviour -> the diff is not live in the harvester
  until its next restart (ASSUMED; confirm with `runtime_identity_check.py --pid 56`). The importer change IS live for every new
  import subprocess (bind mount).

Schedules that can START before 00:00 Asia/Jerusalem (VERIFIED in code + live env):
- Wall-clock: `db_update_agent` run_all_tasks **21:30 IL** (`DB_AGENT_RUN_TIMES_IL` default, db_update_agent.py:4838) - maintenance;
  NOA posts 13:00 and 20:00 IL (BACKEND_API_ROUTES.py:4726) - outbound social; NOA EOD report 21:00 IL (5476) - report;
  Facebook group scan 10:00 IL (3189) - collection (reads FB). The night_pipeline (auto_backup, ebay_fitment_backfill, sync_prices
  incl. eBay + AliExpress price sync, rex_night, brand_discovery, category_discovery, weekly_maintenance) cannot start before
  **01:00 IL** (WINDOW_OPEN, night_pipeline.py:73).
- Interval loops (start at any hour, incl. before midnight):
  collection - `car_parts_ie_harvester_loop` (continuous; CPIE fetches), `car_parts_ie_full_seed` (~30 d, CPIE_FULL_SEED_ENABLED=1),
  `scrape_search_misses_loop`, `rex_dispatch_loop`, `supplier_sourcing_loop` (weekly, web search), `thumbnail_import_loop` (fetches
  supplier images), `fb_keepalive` (4 h), `noa_engagement_loop` / `social_feedback_loop` (read social APIs), `job_queue_loop`
  (JOB_QUEUE_ENABLED=1 live; runs queued backlog jobs, content depends on queue), amayama loops (AMAYAMA_HARVEST_ENABLED=0 ->
  idle; server-browser loop has its own toggle, not set = on: unverified whether it fetches), `enrich_catalog_loop` (ENRICH_PARTS_ENABLED=0, idle);
  maintenance - `cleanup_loop` (30 s, incl. the base_price healers), `meili_sync_loop` (2 h), `harvest_supervisor`,
  `car_parts_ie_stall_watchdog` (3 min), `car_parts_ie_healthcheck` (30 min), `health_monitor`, `oom_watchdog`, `zombie_reaper`,
  `vat_basis_registry`; customer/ops - abandoned cart, payment reminders, price watch, stuck orders, boaz tasks, email agent.
- Not a loop but running now: `cpie_product_page_harvest.py` (pid 99297, started 23:28 IL) - collection (car-parts.ie).

## Could not verify
- Which leg (offer vs online_price_ils) of the 59,173 x1.45 offers is wrong - needs source pages.
- Whether a `/collect`-relay-spawned importer ran during the stash window (~23:42 IL); harvester-launched imports did not (resting).
- Population-wide visibility of the 224K stale legacy-key CPIE offers (sample: 17% of CPIE parts show one).
- Pack-size semantics of individual product pages.
- That the harvester process runs pre-diff code (inferred from log behaviour only).
