# Independent consultant: final acceptance verification (round 3)

**Date:** 2026-11-17, checks run 04:07-04:30 UTC (06:07-06:30 Israel time).
**Mode:** read-only. Every DB session ran `SET default_transaction_read_only=on`. Nothing was restarted, and no offer or part was written. The only files written were helper scripts and `/tmp/consult_*` id lists inside the container's `/tmp`, plus scratch files under the session scratchpad.
**Method:** I used my own queries and my own recomputations. I did not run the primary agent's verify scripts. The only shared code I used is `part_identity.classify` (the task asked for it), plus `base_price_rule.target` as a second, cross-check pass in E. The base_price check in C does not use it.

## Verdict summary

| Item | Verdict | Key numbers |
|---|---|---|
| A. Offer repair (835) | **PASS** | 835/835 price_ils and supplier_url equal the result `after` and `round(price_eur*3.48,2)`. 0 non-allowed column changes. 834/834 price_history rows (1 offer had an unchanged price, so it has 0 rows). KAMOKA untouched. 0 customer references. min_price_ils 835/835. API 60/60. Meili min_price_ils 835/835. |
| B. Final accounting (951) | **PASS**, with a CONCERN | 951 unique, one disposition each. The 116 not repaired are all unchanged since the snapshot, all still `is_available=true`, none relinked, deactivated or deleted. Identity spot check: 10/10 owner brands match, and classify agrees with the file on 72/72 sibling pairs. All 75 SAME siblings are present, unchanged and active, and 0 of their offers were touched. |
| C. base_price | **PASS**, with a CONCERN | Random 2,000 journal parts: 2,000/2,000 live == my own independent recomputation. heal32: 32/32. STALE_SOURCE + ESTIMATE_AMBIGUOUS, full population of 470,647: 470,469 unchanged. The 178 that changed were written by the live car-parts.ie importer, not by the repair tool (details below). |
| D. Writers | **PASS** for the 4 claimed fixes, with a CONCERN | The fixes are real in the diffs. The inventory misses `rockauto_price_import.py`, and it misclassifies `amayama_price_import.py` and `oempartsonline_importer.py`, which are all reachable at runtime and still use their own `x1.45`. |
| E. Harvester | **PASS** for the restart and pending-not-empty fix; **CONCERN (High)**: the byd/atto-3 "23 parts" are junk | pid 1 and the harvester (pid 87) started after every relevant mtime. The 3 models were kept `pending` through 4 mint-failure cycles, then collected 389/395/23. Brand check: 786/786 payload items brand-consistent with the written offer. 0 of the 835 overwritten. Post-restart base_price: 754/754 equal to the rule. |
| F. Schedule | **PASS** | Night jobs ran 01:00-04:45 Israel time on 2026-11-17. run_all_tasks slots are unchanged and ran 21:30 and 00:15 Israel time. |

---

## A. Offer repair

Data: the 4 result files (canary + rest for v4 and for v5add), v4 + v5add manifests (sha256 re-checked: `14cd1a50…` and `2e51b00a…`; bp manifest `d83e8dcd…`), and 4 pre_apply backups.

1. **Live state == `after` == evidence.** 835 done rows, 835 unique offers, 835 unique parts. Every done row is in a manifest. The only manifest row that was not applied is KAMOKA `223a9e2c…`.
   - Live `price_ils == after`: 835/835.
   - Live `supplier_url == after == manifest target_url`: 835/835.
   - `after == round(price_eur × 3.48, 2)` with rate 3.48 in every row: 835/835.
   - Live `updated_at == after.updated_at`: 835/835. Nothing touched these offers after the apply. 0 of them have `updated_at > 02:49 UTC`.
2. **No other column changed.** I compared every pre_apply column except price_ils, supplier_url and updated_at: 0 real differences. The 98 `created_at` hits were only microsecond-format strings (`.53629` vs `.536290`), with the same value.
   - Same comparison for parts (allowed: updated_at, base_price, min/max_price_ils): 0 real differences. 72 hits were the same format artifact. All 835 parts are active.
   - **price_history:** 834 rows with source `cpie_overwrite_repair`, exactly one for each changed price, with old/new equal to before/after. The one offer whose price did not change (only its URL changed) has 0 rows. No other source wrote rows for these offers.
3. **KAMOKA `223a9e2c-0caa-4d49-949a-4cf7be9ef29f`:** price 26.13, `updated_at 2026-11-14 09:30:26.326175`, not in any done list, and 0 price_history rows since 2026-11-15.
4. **Customer references:** I scanned every table in both the catalog and PII databases with a `supplier_part_id` or `part_id` column.
   - The PII database has 0 rows that reference the 835 offers or their parts. There are no order or cart references.
   - The catalog database has only non-customer references (thumbnails, fitment, images, scraper logs), and 0 of them were updated after the apply.
5. **min_price_ils:** I recomputed it independently in my own SQL (orderable, class-compatible offers, VAT by country/basis): 835/835 equal to live (±0.01).
6. **Customer API** `/api/v1/parts/{id}/suppliers`, random 60 (seed 20261117), paced at 2.5 s: 60/60 have `customer_price_ils == round(cost×1.45,2)` and `customer_vat_ils == 0`. The repaired offer was present in every response.
7. **Meilisearch:** `min_price_ils` equals Postgres for 835/835 documents.
   - Note: `base_price` in Meili differs for 776/835. The meili checkpoint is from 02:48 UTC, and the base_price repair ran 03:57-04:03 UTC.
   - It is internal only (tie-break), and the 2-hourly incremental sync should pick it up because `updated_at` was touched. That pick-up is ASSUMED: confirm it after the next sync, about 04:48 UTC.

## B. Final accounting of the 951 offers

- 951 rows, 951 unique offer_ids, no duplicates, one disposition each:
  - REPAIRED_VERIFIED 835
  - OTHER_EVIDENCE_GAP 41
  - UNRESOLVED_SOURCE_BLOCKER 36
  - IDENTITY_CONFIRMED_NO_PRICE 23
  - UNRESOLVED_IDENTITY 15
  - VALID_CURRENT_STATE 1
- Note: `final_accounting.py` gives REPAIRED_VERIFIED to any offer that appears in a result file. The "verified" part rests on my section A checks, not on that script.
- **REPAIRED_VERIFIED (835):** all live and repaired as described in A.
  - 824 offers have a URL slug equal to the owner brand.
  - 11 do not, and all 11 are legitimate:
    - MALÒ→`malo` (6) and LEMFÖRDER→`lemforder` (1) differ only by diacritics.
    - DIEDERICHS→`hd-karosserieteile` (3) and EFI AUTOMOTIVE→`bougicord` (1) are product pages whose parsed structured data says brand DIEDERICHS / EFI AUTOMOTIVE with the exact SKU (pp_*.jsonl), so the slug is a sub-brand line.
- **The 116 not repaired:**
  - VERIFIED: live price_ils and supplier_url equal `s1_snapshot_20261116.json` (`price_now`/`url_now`) and the v5 `current_at_snapshot` for 116/116. No `updated_at` has moved, and every one still has a vehicle-category URL.
  - v5 flags `foreign_qualified_price_equals_bare=True` (the price provably equals another brand's row) for 58 of them. For the other 57 + 1 it is not proven either way.
  - 0 unavailable, 0 relinked, 0 part deactivated, 0 missing. Nothing was hidden or deleted.
  - **CONCERN:** these 115 wrong offers plus KAMOKA (which is valid) remain customer-visible and orderable at a price that is known to belong to another brand's article.
- **Identity cases (59):**
  - Siblings: 306 (226 different, 75 same, 5 uncertain). All are present and active, and none changed name, oem_number or part_type.
  - 11 siblings carry an old `merged_from` marker, all dated on or before 2026-11-16 22:33 UTC. None was created today.
  - 1 DIFFERENT sibling (febi bilstein 03212, Daewoo) has `updated_at 03:59:24`. The timing matches the Renault import, but sku 03212 is not in the 03:59 payloads, so the writer is ASSUMED, not attributed. Its name, oem_number and part_type are unchanged and it is active, so this is not a merge.
  - Offers on the 75 SAME siblings updated after 01:50 UTC: 0. No merge or relink happened.
  - Spot check, 10 random cases (seed 7): title brand == owner brand in 10/10. I ran `part_identity.classify` myself on the live rows: it matched the file's verdict on 72/72 sibling pairs.

## C. base_price

- **Journal:** 20,066 intents and 20,066 commits (none orphaned), from 03:57:14 to 04:03:25 UTC.
  - All commits are REPAIRABLE.
  - 18 REPAIRABLE rows were not committed. Live, every one of them now equals my own rule recomputation: they were moved by the harvester or importer, which matches the tool's "changed / rule-moved" skips.
- **Own recomputation** (it does not import base_price_rule): visible offer (available, active supplier, not internal, URL present, cost > 0), class rank 0, cheapest; net = round(cost_ex_vat × 1.45, 2); inclusive local offers are converted to ex-VAT with /1.18; local offers with an unknown basis are blocked; otherwise the catalog estimate is used. USD rate 3.24 from `system_settings`.
  - Random 2,000 committed parts (seed 42): **2,000/2,000 live == my target.** 1,999 also equal the journal `after`; 1 moved later through the rule.
  - Rounding note: with decimal half-up rounding, 73 of the 2,000 differ by ₪0.01. The rule uses Python float `round()`. The customer price uses the same function, so the values are consistent, but the rule's tie-breaking is binary-float. This is cosmetic.
- **heal32:** 32/32 live == `after` == my target.
- **STALE_SOURCE and ESTIMATE_AMBIGUOUS were not written by the tool.** I checked the **full** population (470,647 rows), not only the sample of 300 + 300:
  - 470,469 still equal the manifest `current`.
  - 178 changed (172 ESTIMATE, 6 STALE), each **to exactly the manifest target**.
  - They are not in the tool journal. Their `updated_at` values cluster on two transaction timestamps (03:59:29.199963 and 03:59:44.710539) that coincide with the Renault fuego-136 / master-ii-bus-jd imports (all of the clustered rows are Renault parts from Car-Parts.ie). So they were written by `car_parts_ie_import_generic → base_price_rule.apply_to_parts`, the live runtime rule, not by the repair tool.
  - The tool respected the classes. **CONCERN:** the live importer applies the estimate to any part it upserts, including parts with no offer. 79/99 and 83/109 of those batches had no offer at all, so the "ESTIMATE_AMBIGUOUS = do not write" decision is not enforced at runtime.
- **is_active and offers:** 0 inactive in any sample. The tool only writes parts_catalog. The live updated_at checks on the 835 offers confirm that no offer was touched by the base_price run.
- **Is the 2026-10-01 cutoff a sound definition of "stale"?** It is acceptable as a conservative gate, but it is a heuristic, not evidence.
  - The age of an offer does not prove it is wrong, and it does not prove that the current base_price is better: STALE rows keep a base_price that may itself be derived from older and worse data.
  - It also does not catch freshly updated offers that are still wrong-brand. A bare-key offer rewritten by any re-harvest gets a fresh `updated_at`.
  - Because base_price is internal (the customer price is computed live from the offer), leaving 416,596 rows stale is low risk. Writing them would have been equally harmless to customers, so the gate mainly protects reversibility.
  - Recommendation: base staleness on whether the offer key is a "bare article key that has known owner/foreign collisions", not on the date.
- **Danger of using wrong offers as the basis:**
  - The 835 were repaired first (02:46-02:48 UTC, before the base_price run at 03:57), so their parts got the correct basis.
  - The **115 remaining wrong offers** (B), and any other undetected bare-key collisions, are still the price basis for their parts. Their REPAIRABLE base_price was therefore computed from a foreign-brand price.
  - Impact is internal (tie-break and harvest targeting) and does not reach the customer price. Still, it should be listed: a base_price derived from a wrong offer is "rule-consistent", not "correct".

## D. Writers

- **2a89e8b, verified in the diff:**
  - `catalog_scraper.db_upsert_part` no longer writes base_price: the parameter was removed, INSERT uses literal 0.0, and UPDATE no longer sets it. `run_brand_discovery` calls `apply_base_price_rule` after the offer exists, and category discovery no longer passes a price.
  - `ai_catalog_builder.insert_parts` raises unless dry_run.
  - `apply_base_price_rule` calls `ensure_vat_registry(db=)`.
  - `apply_to_parts` calls `ensure_vat_registry(conn=)`. The registry is loaded in the importer subprocess: import logs show `vat_registry_loaded: true`.
- **480e961, verified:** the importer takes `RETURNING part_id` on the offer upsert and adds the owning part to `touched_part_ids`.
  - Its mtime is 04:05 UTC, so it applies only to imports spawned after that.
  - The 3 imports at 03:59 UTC ran without it. They still came out 754/754 rule-consistent (E).
- **Missing or misclassified in BASE_PRICE_WRITER_INVENTORY_20261117.md** (my grep over every non-test file that writes `parts_catalog.base_price`):
  1. **`importers/rockauto_price_import.py`: MISSING.** It is spawned automatically by `routes/system.py:331` (the `/collect` relay for brand=rockauto). It writes `base_price = price_ils*1.45` (own formula, foreign/USD cost) when the part is unpriced.
  2. **`importers/amayama_price_import.py`: MISCLASSIFIED** as an "on-demand one-shot". It is spawned automatically by `routes/system.py:344` from the always-on supervised Amayama harvester. Own `price_ils*1.45` when unpriced.
  3. **`oempartsonline_importer.py`: MISCLASSIFIED** as an on-demand IL importer. It is spawned by `catalog_scraper` (REX path). Own `round(price_ils*1.45,2)` when empty.
  - All three only fill an empty or zero base_price, so they never overwrite the rule's value. They are still second formulas: no VAT-basis awareness, and no ownership by `base_price_rule`. Severity: medium.
  - Manual CLI writers not listed (low): `harvesters/acura_browser_harvest.py` (unconditional `base_price = importer×1.45`, also on UPDATE), `harvesters/spareto_harvester.py` (COALESCE fill), `scrapers/bydil_scraper.py`, `scrapers/eliteparts_scraper.py` and `scrapers/selected_parts_scraper.py` (`base_price = EXCLUDED.base_price`), and `maintenance/vat_fix_batched.py` (`max/1.18*1.45`).
  - `services/aliexpress_price_sync.py` and `ebay_price_sync.py` only read base_price as the "unpriced" selector. They do not write it.
  - `db_cleanup_agent` has 4 call sites into `apply_base_price_rule` (1161, 1240, 1477, 1600). The inventory lists only 2, but all of them are rule-owned, so this is harmless.

## E. Harvester

- **Process start vs code:**
  - pid 1 (uvicorn) started 2026-11-17 03:34:41 UTC. The container `StartedAt` is 03:34:41.96Z.
  - The harvester (pid 87) started 03:35:46 UTC.
  - mtimes (UTC): catalog_scraper.py 00:44:26, db_update_agent.py 00:44:36, harvester 01:12:20, base_price_rule.py 01:12:32. All are earlier than both start times.
  - Disk == HEAD (480e961). Only the importer differs from 2a89e8b, and it runs as a subprocess.
- **Mint failures do not lose models:** after the 3 models were requeued (pending=3 at 03:39:53), the log shows FlareSolverr HTTP 500 at 03:40/03:42/03:43, then `ERROR could not mint cf_clearance cookie after retries` and `skipping worker cycle to avoid false-empty queue burn`.
  - This repeated 4 times (03:43, 03:49, 03:54, 03:57) with pending kept at 3 and no `empty` written.
  - At 03:58:09 the cookie was minted. fuego-136 collected **389**, master-ii-bus-jd **395** and atto-3 **23** parts, followed by "queue drained".
  - `harvest_queue`: all 3 are `done`, last_harvested 03:59 UTC. The queue holds 6,694 done and 1 empty (microcar/m-go, from 2026-11-16, before the fix).
- **Offers written 03:57-04:10 UTC:** 754 Car-Parts.ie offers (5 new). All URLs are vehicle-category pages, which is the normal harvest form. 737 are bare-sku and 17 are `brand~sku`.
  - Brand check against the harvested payload files (`/tmp/*fuego-136*`, `*master-ii-bus-jd*`, `*atto-3*`, 807 items): 785 bare + 17 keyed match the payload's brand. The 1 "mismatch" (COF100136S) is a title-parse artifact; CHAMPION is on both sides. 4 items had no offer in the window. Effectively 786/786.
  - Random 20: title brands 3RG, STC, KAMOKA, FEBI BILSTEIN, goCORE and others, all consistent.
- **The 835 were not overwritten:** 0 have `updated_at` after 02:49 UTC.
- **Post-restart non-recurrence:** for all 754 parts with Car-Parts.ie offers updated after 03:57, base_price equals `base_price_rule.target` in **754/754**.
- **Re-review of the harvester diff.** The 3 previously reported bugs are fixed: session-mode empty body counts as blocked, nothing-delivered raises `HarvestFetchFailed`, and `_RELEASED_AT` adds a cooldown. Residual issues:
  1. **No attempts cap.** `release_model` returns the model to pending and `attempts` increments, but nothing filters on `attempts`. The comment "reclaim/attempts bound the retries" is not true. A model that always fails is retried every 30 min forever (the cooldown is also process-local and lost on restart). Low.
  2. **Partial delivery is "done".** If `sent > 0`, blocked pages only produce a warning, and a failed final `post_relay(batch)` silently drops those parts. The model is marked `done` with partial data and will not be revisited until the 14-day refresh. Medium (silent data loss, against the "importer never drops rows silently" rule).
  3. In session mode, a genuine 404 counts as blocked, so a model with no parts can never become `empty` in session mode. Low.
  4. The mint lock falls through after a 3×timeout+30 s deadline and solves anyway. That is acceptable.
  5. **The byd/atto-3 collection is not real vehicle data (High).** All 23 payload items, found on every slug page (the log stays at 23 from 20/79 to 80/79 slugs), are a page-wide product block, not Atto 3 parts. Examples: Spark plug RIDEX 686S0003, Glow plug RIDEX 243G0006, Glow plug KAMOKA KP002, Engine oil ELF 2202818 and Oil filter RIDEX 7O0005. The BYD Atto 3 is a battery-electric car.
     - The import wrote **22 `part_vehicle_fitment` rows for Byd / Atto 3 at about 03:59 UTC**. These are the only Atto 3 fitment rows (22 in total), and 11 of them are ICE-only parts (spark or glow plug, oil filter, engine oil, sump plug).
     - The harvester has no check that separates a page's real category listing from a "popular products" or fallback block. When a model page has no real listing, this junk becomes fitment, and fitment is the core search differentiator (G1).
     - `harvest_queue` shows `parts_found=87` for atto-3, so earlier harvests did the same. Whether Renault fuego/master also absorbed carousel items is UNVERIFIED. COF100136S (fuego payload) landed on a "Master II" titled part, which is consistent with either a collision or a carousel.
     - E stays PASS for what the item asked: the restart, pending kept through mint failures, and no `empty`. But "23 parts collected" is not evidence of real collection.

## F. Schedule

- `night_pipeline.py`: the window is `[NIGHT_PIPELINE_OPEN_IL 01:00, CLOSE 07:00)` in `local_schedule.LOCAL_TZ = ZoneInfo("Asia/Jerusalem")`, so it is DST-safe. Neither env override is set in the container.
- `night_pipeline_runs` for period `D:2026-11-17` (UTC, Israel time = UTC+2):
  - auto_backup: 23:00:00 → 23:06 (01:00 Israel)
  - ebay_fitment_backfill: 23:07 → 23:13
  - sync_prices: 23:14 → 00:36
  - rex_night: 00:37 → 00:40 (0 inserts, 0 price updates)
  - category_discovery: 00:41 → 02:45 (0 inserted; stopped by the 7,200 s time budget)
  - All are COMPLETED, and all started at or after 01:00 Israel time.
- `run_all_tasks`: `DB_AGENT_RUN_TIMES_IL` default `21:30,00:15,07:15`, not overridden in the environment. `git log -p --since=2026-11-14` over db_update_agent.py and night_pipeline.py shows no added or removed line touching `RUN_TIMES`, `WINDOW_OPEN/CLOSE` or `NIGHT_PIPELINE_OPEN` (VERIFIED). `job_registry` shows runs at 19:30 UTC (21:30 Israel) and 22:15 UTC (00:15 Israel) on 2026-11-16, both completed.
- Note: rex_night and category_discovery ran in the old uvicorn on pre-fix catalog_scraper code (restart was 03:34). They inserted 0 parts and wrote 0 prices, so there was no raw-USD base_price impact. I checked parts touched by category discovery in 00:41-02:46 UTC: 0.

## G. Open items for the tracker

| # | Severity | Item |
|---|---|---|
| 0 | **High** | **Wrong fitment from page-wide product blocks:** byd/atto-3 wrote 22 fitment rows (11 ICE-only: glow and spark plugs, oil) for an EV at 03:59 UTC. The harvester cannot tell a real category listing from a carousel or fallback block. Audit models whose `parts_found` is small and identical across slugs. Remove the Atto 3 rows after the owner approves (a destructive production change). |
| 1 | **High** | 115 known-wrong offers (41 OTHER_EVIDENCE_GAP, 36 UNRESOLVED_SOURCE_BLOCKER, 23 IDENTITY_CONFIRMED_NO_PRICE, 15 UNRESOLVED_IDENTITY) are still `is_available=true` and visible at a price that belongs to another brand's article. Decide: hide them pending evidence, or accept the risk explicitly. |
| 2 | Medium | 256 of the 835 repaired offers have source evidence `OutOfStock` but remain `is_available=true` and `availability=in_stock` (deliberately unchanged). A customer can order a part the supplier lists as out of stock. |
| 3 | **High** | The bare-key collision is still active after the restart. 737/754 Car-Parts.ie offers written at 03:59 UTC are bare article keys. In the two Renault import transactions, 79/99 and 83/109 of the upserted parts ended with **no offer at all**: the harvested price landed on an offer that another part owns (COF100136S from the fuego payload went onto a "Master II" part). The owner part gets only an estimate base_price. Every re-harvest repeats this, so new wrong offers can appear outside the repaired population. |
| 4 | Medium | The live importer writes the catalog-estimate base_price on upserted parts with no offer (178 ESTIMATE/STALE rows changed at 03:59). This contradicts the "ESTIMATE_AMBIGUOUS = do not write" decision. Pick one policy. |
| 5 | Medium | Writer inventory gaps: `rockauto_price_import.py` (missing) plus `amayama_price_import.py` and `oempartsonline_importer.py` (runtime-triggered, not one-shot) still use their own `×1.45` (fill-if-empty). Also the IL importer family (already OPEN in the inventory). |
| 6 | Medium | Harvester partial delivery: a failed relay batch, or blocked pages when `sent>0`, means silent loss and status `done`. |
| 7 | Low | Harvester has no `attempts` cap, and its cooldown is process-local. |
| 8 | Low | Base_price rows whose basis offer is one of the 115 wrong offers are "rule-consistent", not correct. Re-apply the rule when those offers are fixed. |
| 9 | Low | The STALE cutoff (2026-10-01) is date-based, not evidence-based. 416,596 rows are left with legacy base_price. |
| 10 | Low | Meili `base_price` lags for 776/835 parts until the next incremental sync. Confirm after about 04:48 UTC. |
| 11 | Low | EUR→ILS 3.48 is a static env default "updated 2026-08-26, update quarterly". The USD rate shows 3.21 (rex) vs 3.24 (system_settings) on the same night. |
| 12 | Low | base_price rounding uses Python float `round` (binary tie-break). It is consistent with the customer price, but 3.6% of half-cent ties differ from decimal half-up. |
| 13 | Info | `final_accounting.py` assigns REPAIRED_VERIFIED by presence in a result file, not by a live check. The live verification is in this report (A). |
| 14 | Info | category_discovery inserted 0 parts in 2 h (time budget hit). Productivity was not investigated. |
