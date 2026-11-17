# CORE consultant - round 3, pre-write review of the 787-row offer repair (2026-11-17 ~03:00 IL)

Independent, read-only. Every DB session `SET default_transaction_read_only=on`; no repo file edited (this report is the only
file written); no container restarted. Live HTTP: 2 + 2 + 20 car-parts.ie GETs through the harvester's shared cookie (cache was
~10 min old, reused, no mint). Scratch scripts/outputs: `/tmp/claude-0/-opt-autosparefinder/8e41f1a8-6801-47b8-83b9-35372e26e678/scratchpad/r3/`.
Labels: VERIFIED = checked against live DB/code/page this session; ASSUMED = inferred (what would confirm it is named).

**Verdict: the 20-offer canary is NOT blocked.** One plan correction (KAMOKA/NGK handled consistently) and the run flags below
should be fixed before the run. The harvester diff has real bugs, but they do not touch this repair; they block deploying the harvester.

## 1. Manifest vs live - PASS (VERIFIED)
- Manifest sha256 = `14cd1a50...6c84` (matches the pin). 787 rows. All are `VERIFIED_WRONG`/`READY`/`C_WRONG_PRICE_AND_URL`. 787 unique offer_ids and 787 unique parts.
- Live supplier_parts compared with manifest `current` for all 787 rows (live read 02:57 IL). Drift: **0**. That covers price_ils, supplier_url, updated_at (exact
  microseconds; column is `timestamp without time zone`), part_id, supplier_id (= 88f6ce24...) and supplier_sku (== article, and == evidence.sku). No `~` key.
  No repair price_history row exists yet.
- `target_price == round_half_up(price_eur x 3.48, 2)`: 787/787. eur_ils_rate = 3.48 on every row. target_url == evidence.page_url on every row, and every one is a
  `/car-parts/<brand>/p-<id>` product page. Brand slug == evidence.brand after normalisation: 780 rows. The other 7 differ only by a diacritic (`malo`/MALÒ x6,
  `lemforder`/LEMFÖRDER x1), so they are fine.
- For all 787 parts, `parts_catalog.name` contains the evidence brand and the article. All 787 parts are active.
- Live `is_available` = TRUE on all 787. Evidence availability is InStock 545 / **OutOfStock 242 (30.7%)**. The canary's first 20 hold 5 OutOfStock rows. The brief keeps availability unchanged, so this is a non-blocking CONCERN and a separate follow-up.
- **Two rows already hold the target price**: KAMOKA 25216 (`223a9e2c`, manifest index 190) and **NGK 92043 (`d303b09e`, index 745,
  491.65 == 141.28 x 3.48)**. Only KAMOKA is in the exclude plan. See task 3.
- Freshness: evidence was fetched 23:28-23:56 IL. I re-fetched the 20 canary owner pages at 03:01 IL: brand, sku and currency match on 20/20, and the EUR price matches on 19/20.
  `577b3ec1` Metalcaucho 02266 moved 9.93 -> 9.94 EUR (manifest 34.56 vs live-page 34.59 ILS). The target is a snapshot, not a live price.
  The difference is negligible, but the result must not be reported as "current page price".
- The current/target price ratio runs from 0.014 to 1719. 150 rows are >5x, and 103 rows are <0.2x, meaning the foreign price was lower. For example, the WALKER 28494 catalytic
  converter goes from 21.30 to 1,536.80 ILS. These are large customer-visible increases. They are correct per the evidence, but expect them.

## 2. Side effects of apply - PASS with notes (VERIFIED, code + pg catalog)
For each row, `apply_rows` (cpie_overwrite_repair.py:140-179) writes the following in one transaction:
- `supplier_parts`: `price_ils`, `supplier_url`, `updated_at=NOW()`. This is a CAS on the price, url and updated_at it read, under `FOR UPDATE NOWAIT`.
- `price_history`: one INSERT only when the price changes. It holds old/new ILS and `new_price_usd = price_usd` (0.00 here), and leaves ils_per_usd_rate NULL. Source is `cpie_overwrite_repair`.
- `parts_catalog.updated_at=NOW()` (Meili incremental key, so expect a wave of ~786 docs).
- With `--refresh-minmax` (:182-204), `db_update_agent.refresh_min_max_for_parts` (:3085) also writes `parts_catalog.min_price_ils`, `max_price_ils` (only
  from an eligible LOCAL offer, else unchanged) and `updated_at`. Its SQL uses `suppliers.vat_price_basis` directly, not the in-process registry.
- **Never written:** `is_available`, `availability`, stock, warranty, part_id, supplier_sku, shipping. The `fulldiff` allowlist (:218-219) enforces this
  after the fact.
- Triggers: none on supplier_parts or price_history, and no rules. On parts_catalog, `trg_sync_parts_catalog_mfr` (BEFORE INSERT/UPDATE) fills
  `manufacturer_id` only when it is NULL. All 787 parts already have it set, so the trigger is a no-op here. The other two parts_catalog triggers fire on DELETE and on is_active->false only.
- Note: `--refresh-minmax` and `--rebase` are separate steps. Without `--refresh-minmax`, `min_price_ils` keeps the foreign price until the
  scheduled refresh runs. I recommend passing `--refresh-minmax`. base_price of these parts is stale until `--rebase` runs.

## 3. KAMOKA 25216: price VALID by coincidence, URL WRONG (VERIFIED, fresh fetches at 03:00 IL)
- **Owner page** `kamoka/p-23144068`: JSON-LD brand KAMOKA, sku 25216, **7.51 EUR**, InStock.
  - 7.51 x 3.48 = 26.13, which equals the live offer price of 26.13 ILS.
  - This matches the evidence fetched at 23:33 IL.
- **Live URL** `ruf/turbo-r-1998/gear-shifter`, parsed with the harvester's own `parse_parts`:
  - The 25216 row on that page is **`3RG 25216 RUF Turbo R Coupe gear shifter`, brand 3RG, price_eur 7.51**.
  - KAMOKA does not appear on that page.
  - So the offer's price and URL both came from a different brand's different product. Its price just happens to equal KAMOKA's own price today.
- **How to record it:**
  - Price: VALID_CURRENT_STATE (owner page 7.51 EUR, fetched 03:00 IL, body sha cbeedfce...).
  - **The match is coincidental:** the offer price comes from the 3RG row, which is also 7.51 EUR.
  - URL: WRONG (3RG gear-shifter listing).
  - The v2 "wrong offer" verdict is correct in mechanism. Only the price harm is zero today.
  - "VALID_CURRENT_STATE" for the offer as a whole is **not** honest. It is honest for the price only.
- **NGK 92043** (`d303b09e`, included in the plan):
  - Owner page: NGK 92043, **141.28 EUR** (= live 491.65 ILS).
  - The live URL is a Volvo listing with the row **`NTK 92043`, brand NTK, 141.28 EUR**.
  - NTK is (ASSUMED, general knowledge, not checked against a car-parts.ie brand page) NGK Spark Plug's own sensor brand, so this row is likely the **same product**.
  - That would make it a false-positive "wrong offer". The URL change to the NGK product page is then harmless cosmetics, not a fix.
- **Recommendation:**
  - Both rows are price-only no-ops. The tool keeps the price, writes no price_history row and changes only the URL.
  - Treat them consistently and record the reason per row:
    - KAMOKA: a URL fix is justified, because the URL points to a 3RG product. I would include it.
    - NGK: URL cosmetics only.
  - The current plan excludes KAMOKA and silently applies NGK, which is the opposite of what the evidence supports.
  - If KAMOKA stays excluded, list its wrong URL as an open item.

## 4. Concurrent writers - CONCERN, low (VERIFIED)
- `category_discovery` was running (started 02:41 IL). One of its connections was **idle-in-transaction for 17+ min**, last statement `SELECT sku ... WHERE manufacturer`.
  - VERIFIED from code: it writes parts_catalog only through `db_upsert_part` (UPDATE by exact `sku`) and never writes supplier_parts.
  - The 787 parts have SKUs like `NISSA-230043`, while discovery SKUs are bare part numbers.
  - ASSUMED, from the SKU namespace only: it holds no row locks on these parts. Earlier UPDATEs in that same open transaction would not be visible in pg_stat_activity.
  - What would confirm it: a `pg_locks` join on the 787 part ids.
  - Either way, `FOR UPDATE NOWAIT` turns any such conflict into a guard skip, never an overwrite.
- harvest_queue: 6,694 done, 1 empty, **0 pending**. The harvester is idle, so no importer spawn comes from it. The `/api/v1/system/collect` relay can still spawn the importer.
  The importer (`car_parts_ie_import_generic.py` ~413-451) upserts by `(supplier_id, supplier_sku)`, writing price_ils, is_available=TRUE, url and updated_at. Its key logic means a foreign-brand
  listing writes `BRAND~article` and not the bare key. The race is safe: if the importer writes first, the repair's CAS fails and the row is skipped as `guard:*`. If the repair writes first, a later
  legitimate owner re-harvest simply supersedes it (importer `_old_offer` may record a stale old price in price_history - cosmetic).
- BOAZ `sync_prices` runs in REAL_DATA_ONLY=true (verified env), so there is no synthetic drift. Its Sendcloud step bulk-updates CPIE `shipping_cost_ils` only where NULL. All 787 rows have it
  set, so there is no effect. Its next run is ~01:14 IL tomorrow.
- db_cleanup_agent writers touch inactive / base NULL-0 parts, which are not these. The transient `UPDATE parts_catalog SET updated_at` seen is
  `task_recover_motorstore_prices` (inactive parts).
- `cpie_base_price_repair.py build` is running now (pid 151095, read-only). See 5d for the ordering.
- The exact-value CAS + NOWAIT makes any concurrent write safe: it ends in a skip, never an overwrite. The repair does no busy-job check of its own
  (unlike cpie_base_price_repair's `_busy`). That is acceptable for 786 single-row transactions.

## 5. Code review (uncommitted)
(a) Harvester - **3 real bugs, block deploying it (not the repair):**
1. `car_parts_ie_flaresolverr_harvester.py:735-761` and `:800` (session mode): `_fs_get_via_session` never calls `_fetch_outcome`. In
   session mode every failure leaves ok=blocked=0, so `:991` never raises, and `:1017 complete_model(qid,0)` marks the model **'empty'**. This is a failure turned into an empty model.
2. `:991` only fires when `ok==0`. If the model page succeeds (ok=1) and every category page is blocked, the model is recorded **'empty'**. This is partial failure
   turned into empty. The same happens when every `post_relay` fails (`:979`, sent=0, pages ok). Rule: sent==0 and blocked>0 should release the model, or a minimum ok-ratio should apply.
3. `:1033` + `:93-109`: `release_model` on ANY exception, but nothing caps or reads `attempts` (grep). The comment "reclaim/attempts bound the
   retries" is false. A model that errors deterministically is re-claimed first every cycle, because it holds the top priority. With >=5 such models, all 5 workers
   `break` at once in every cycle: **queue starvation**. The old code marked such a model empty.
4. (efficiency) `:681` + `:583`: a forced refresh treats the CURRENT global cookie as failed, and `_load_clearance_cache` refuses a cache entry whose ts equals the
   in-memory ts. As a result, when N worker threads hit 403 together, each pays its own serial FlareSolverr solve after the first one has already succeeded. This is the round-2 finding, still
   open. Also `:696`: the waiter deadline 3x60+30 s is shorter than the holder's worst case (2 hosts x 3 tries x 60 s), so concurrent solves are still possible.
   flock usage itself is correct: a separate fd per call, released on close, `_CLEARANCE_LOCK` is not held while waiting, and there is no deadlock. `_FETCH_TL` is correct
   per worker thread (harvest_model and http_get run on the same thread).
   The tests cover none of 1-4.
(b) catalog_scraper: the base_price writer of the raw source price is removed (OK). `run_brand_discovery` applies the rule after commit (`:3704`).
   `_run_category_discovery` creates parts at 0.0 with no rule call. They wait for the cleanup healer (base NULL/0). Acceptable.
(c) `base_price_rule.ensure_vat_registry`: it runs the same query as uvicorn (`BACKEND_API_ROUTES.py:2779`), and there are no duplicate supplier names with differing bases.
   Loading it changes behaviour only for local suppliers whose basis is `unknown`/NULL. These are now **blocked**, as in uvicorn: 6 active ones (AutoParts Pro IL,
   Hongqi, Iveco, JLR Delek, Leapmotor, Porsche). That is the policy, not a wrong block. The other offers on the 787 parts are Fiat/Subaru/eBay,
   which are not affected. **Bug:** the return value is ignored at `base_price_rule.py:129` and `db_update_agent.py:1306`. On a load failure the writers
   silently price with the legacy reading, which contradicts the docstring ("caller must not pretend"). The writers should refuse instead.
   Also, inside `apply_base_price_rule`, a failed SELECT aborts the caller's SQLAlchemy transaction while the exception is swallowed.
(d) `cpie_base_price_repair.evidence_class`: the string date compare is fine. **Concern:** "fresh offer" is not "correct offer". The 787 wrong
   offers were updated 2026-11-14/15, so their parts would be REPAIRABLE with targets derived from the foreign prices. The tool has no
   wrong-offer exclusion (round-2 recommendation 6 is not implemented). The apply step recomputes the rule and skips `rule_moved`, so running it AFTER the offer repair is
   safe. Running it BEFORE the offer repair writes wrong base prices. `--rebase`'s CAS would then accept them as the "backup" value and fix them, but
   **order the runs: offer repair (+rebase), then the base_price repair**, or exclude the wrong-offer parts from its manifest. Also note that `updated_at` is
   bumped by non-price writes (Sendcloud), so it is a weak freshness signal (ASSUMED impact small).

## 6. Blockers and checks
Run flags: `--expect-count 787 --exclude-offer 223a9e2c-...` (default 93 refuses). Use the **same** `--exclude-offer` in the `rest` run, otherwise KAMOKA is
applied. Add `--refresh-minmax`. Canary = manifest indices 0-19 (KAMOKA is not among them).
Nothing blocks the canary.
- **Before `rest`:** settle the KAMOKA/NGK consistency (section 3).
- **Availability is NOT a gate.** The brief fixes `is_available` as unchanged.
  - Known non-blocking CONCERN: 242/787 rows (5 of the 20 in the canary) will show a repaired price on an item the owner page says is OutOfStock.
  - Changing that is a separate follow-up decision.
After the canary, check:
1. `--accept --phase-check canary` passes: 20 VERIFIED_REPAIRED, journal 20 committed, fulldiff only the allowed columns, 20 price_history rows,
   and the rest scope all NOT_APPLIED.
2. A count of CPIE supplier_parts with updated_at inside the window equals 20.
3. `minmax_refresh` before/after per part: min_price_ils now equals the repaired price, or a cheaper class-compatible offer.
4. A customer-path spot check (search/compare API) of 2-3 canary parts shows the new price and the product-page URL never leaks raw cost.
5. After the next Meili incremental run, check that the documents reflect the change.
6. Re-run `--verify` on the 766 remaining rows immediately before `rest` (drift must be 0 or explained).
