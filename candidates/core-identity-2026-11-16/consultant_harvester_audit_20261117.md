# Independent consultant audit — car-parts.ie harvester, 2026-11-17 (read-only)

Auditor: independent consultant (Claude Opus 5.5), 2026-11-17 ~06:55–07:10 Israel (04:55–05:10 UTC).
Scope: STRICTLY READ-ONLY. All DB sessions ran with `default_transaction_read_only=on`, asserted per connection.
No restart, write, config change, commit or job interruption was made.
Side effect to note: one wrong-parameter GET `/api/v1/parts/search?query=…` (the parameter is `q`) ran an empty
search that hit the statement timeout (HTTP 500, read-only). Times below are UTC unless marked IL.

## Verdict summary
| # | Item | Verdict |
|---|---|---|
| 1 | Loaded code | 645e5bc final-relay fix **NOT LOADED** in pid 87 (PROVEN). 2a89e8b features are loaded (PROVEN by behaviour). The importer runs for the 3 models used **pre-480e961** code (PROVEN). |
| 2 | Overnight run / funnel | No scheduled job ran after the restart (PROVEN). The harvester is a continuous supervised loop. Final relay worked end to end for 3/3 models (PROVEN), but it is still not robust (see 8). |
| 3 | Bare-key collision | No cross-brand offer write after the restart: 0/754 (PROVEN). "Upserted part left without an offer" is by design: 371 of 806 payload items. It causes harm a customer can see: **FAIL** for the catalogue design. |
| 4 | BYD Atto 3 fitment | **CONFIRMED**: a generic block. 11/22 rows are ICE-only parts on a battery EV, and 18/22 parts got a wrong category. The same pattern exists in 10 small EV/Chinese-brand payloads from 2026-11-14/15. |
| 5 | Wrong-brand offers | All 835 repaired offers are intact (PROVEN). All 116 unrepaired offers are unchanged and still available. **60 of them still equal a foreign-brand qualified offer price.** |
| 6 | Catalog estimates | The importer (via base_price_rule) writes an estimate base_price for **350/758** touched offer-less parts. A customer sees a priced, orderable "AutoSpareFinder" estimate offer marked `is_base_price_fallback` / "מחיר משוער". |
| 7 | Protected jobs | Nothing protected was cut (PROVEN). Only the db_cleanup cycle 188 was cancelled. |
| 8 | Relay robustness | **FAIL**: 5 silent paths remain, and the one 645e5bc fixes is still live in the running process. |

## 1. Loaded code — PROVEN
- pid 1 (uvicorn) started 03:34:41. pid 87 (harvester) started 03:35:46 (`ps lstart`).
- Harvester file mtime is 04:21:56. This is the 645e5bc commit at 04:22:23 (the host shows `05:21:56 +0100`). Python never re-reads a running script, so **the 645e5bc change is not in pid 87**.
- `git status` for backend/ is clean. pid 87 therefore runs the 2a89e8b blob, under the assumption that nothing uncommitted sat on disk at 03:35.
- The running code still has the old final-chunk guard, `if sent == 0 and (blocked>0 or batch)`. A refused final chunk after a partial delivery is dropped silently and the model is marked `done`.
- Features present in pid 87 (2a89e8b), proven by its log:
  - shared cookie cache: `/app/state/cpie_clearance.json` was written at 03:58:09.481, the same instant as "cf_clearance minted"
  - queue-aware minting: "skipping worker cycle to avoid false-empty queue burn"; models were NOT marked empty
  - `CLEARANCE_MAX_TIMEOUT_S`: the env var is **not set** in pid 87, so the default of 60 is used
  - failure logging: FlareSolverr 500 is logged as a WARNING, then an ERROR after the retries
  - release/cooldown: present in the code but not exercised (no HarvestFetchFailed after the restart)
- Importer subprocesses ran at 03:59:10–03:59:46. `car_parts_ie_import_generic.py` mtime is 04:05:00 (480e961), so **the 3 imports ran the aaf5e21 importer without the owner-part base_price fix**. The divergence this caused is already gone: 754/754 owning parts are consistent now (406 upserted, 348 not), apparently healed by round4 `heal32`.
- `cpie_identity.py` dates from 11-16 06:28 and `base_price_rule.py` from 01:12, so both were loaded.

## 2. Overnight run and funnel — PROVEN
**Scheduled jobs on night D:2026-11-17:** all COMPLETED and all finished before the restart.

| Job | Start (UTC) | End (UTC) |
|---|---|---|
| auto_backup | 23:00 | 23:06 |
| ebay_fitment_backfill | 23:07 | 23:13 |
| sync_prices (includes AliExpress) | 23:14 | 00:36:45 |
| rex_night / run_scraper_cycle | 00:37 | 00:40 |
| category_discovery | 00:41 | 02:45:44 |

- `run_all_tasks` ran 22:15–23:17. job_registry shows **0 jobs started after 03:34**. The next `run_all_tasks` is 07:15 IL.
- The harvester is a supervised loop, not a scheduled job.
- The queue held pending=0 at 03:35:47 and pending=3 at 03:39:53. Something outside the harvester requeued the 3 models; the origin is NOT PROVEN.

**Cloudflare mint attempts:**
- 10 FlareSolverr HTTP 500 failures: 03:40:56, 03:42:10, 03:43:17, 03:46:30, 03:47:43, 03:48:56, 03:52:09, 03:53:22, 03:54:34, 03:57:47.
- 3 rounds failed ("could not mint"): 03:43:23, 03:49:02, 03:54:40.
- 1 success at 03:58:09 (12 cookies).
- The pre-restart harvester also failed minting at 01:50 and 02:53 while idle; that was the old code minting with an empty queue.

**Funnel per model:**

| Model | Slugs fetched | Parsed (payload) | Inserted + updated | Skipped | Fitment rows | Offer writes | Images |
|---|---|---|---|---|---|---|---|
| renault/fuego-136 | 82 (1×404) | 389 | 0+387 | 2 `brand_unresolved` | 387 (3 new) | 387 | 0 |
| renault/master-ii-bus-jd | 82 (404s on `fuel-supply-system-auto`) | 395 | 1+392 | 1 `brand_unresolved`, 1 `existing_row_brand_unresolved` (PW680492) | 393 (3 new) | 393 | 3 |
| byd/atto-3 | 79 (parts stuck at 23 from slug 20) | 23 | 13+9 | 1 `brand_unresolved` (OPEL GM 19 42 003) | 22 (22 new) | 22 | 13 |

- 802 offer upserts made **754 distinct offers** with `updated_at` > 03:57: 737 bare keys, 17 brand-keyed, 5 newly created. The two Renault models share articles.
- base_price: 14+112+109 updated, 1 `locked_or_changed`.
- Skips are logged in `/tmp/*_cpie_import.log` and `cpie_import_skips.log` (3 lines). None of them reaches the harvester or the owner.
- **The relay delivered end to end (PROVEN):** parts, fitment, offers, images and base_price were all written. `harvest_queue.parts_found` (2729/2763/87) is a cumulative figure, not this run's count.

## 3. Bare-key collision
Per payload item, using the upserted part identified from its fitment row:

| Model | Offer on this part | Offer on another part, same brand | Offer on another part, different brand | No offer anywhere |
|---|---|---|---|---|
| Fuego | 235 | 152 | 0 | 0 |
| Master | 196 | 197 | 0 | 0 |
| Atto 3 | 0 | 22 | 0 | 0 |

- All 754 written offers carry a price equal to a payload item of the **same** title brand. 0 cross-brand writes; 0 keyed tokens that differ from the owner's brand.
- 345/754 offers sit on parts with another make prefix (OPEL 107, NISSA 78, PEUGE 25 …).
- **Why this happens.** `offer_key` returns the bare article whenever the bare owner has the same brand. The upsert `ON CONFLICT ON CONSTRAINT supplier_parts_supplier_id_supplier_sku_key` then updates the price and `supplier_url` of the **existing owner's** offer and keeps its `part_id`. One commercial offer per (supplier, SKU) is correct. The defect is that parts_catalog keeps one row per vehicle make (`<MAKE5>-<article>`) for one physical article, and the new make-specific row is never linked to that offer.
- **Can a different-brand offer still be overwritten?** Only through a brand misresolution, or when two brands map to the same `_tok`. The keyed path performs no owner-brand check. NOT observed.
- **Harm a customer can see (PROVEN):** BYD part bc21f499 (RIDEX oil filter) has no offer. `/suppliers` returns only an estimate. The real RIDEX offer sits on another make's part.
- **New risk:** every re-harvest replaces `supplier_url` with a listing page. This would undo the product-page URLs set by the round-4 repair. Not yet observed: 0 of the tracked offers were touched after the restart.

## 4. BYD Atto 3 — CONFIRMED
- The payload comes from 3 listing slugs only: brake-pads 8, brake-discs 8, oil-filter 7. This is a generic "popular parts" block.
- **11/22 rows are ICE-only parts on a BEV:**
  - spark plugs: pvf 4312bb8f, e180f256
  - glow plugs: b0d3b1a4, cbe6beee
  - engine oil: 3afdac5d
  - oil filters: 1b71527c, 6d1f916e, dc9e08b5
  - sump plug washers: 48cbc073, fb209b22, 414eee21
- The other 11 pvf ids: 2c585a04, a49ac75e, cb35d354, 545b41fa, 57ef9396, 33a28ca0, 82348f03, 047f62e0, d1a36723, d1daab82, ddf3df20. All 22 rows have year 1990–2030.
- **Categories:** `categorize_on_ingest(name, url=listing URL)`, plus `ON CONFLICT … category=EXCLUDED.category`, overwrote the category of the 9 existing parts. 18/22 are wrong (glow plug → brakes, bulbs → filters …).
- The payload `brand` field holds the first title word ("Spark", "Oil"). The importer takes the part brand from the title parenthesis via `part_identity.title_brand`. "Spark"/"Oil" ends up only in `specifications.part_brand` of the 13 new parts.
- **The same pattern across the other payloads:**
  - All 6,497 payload files: 11 have fewer than 100 parts.
  - 10 of those are generic blocks: Ex5, Maxus V80, Seal U, Sealion 5, Seal 5, Eterron 9, Coffee 01, Geometry C, Dolphin, Atto 3.
  - In those 10 payloads, 26 SKUs appear in at least 3 of them, and 158 items' listing slug does not match their name.
  - Their fitment rows (year 1990) already exist from 11-14/15: Ex5 16, V80 18, Seal U 20, Sealion 5 25, Seal 5 26, Eterron 9 26, Coffee 01 26, Geometry C 28, Dolphin 39. That is 224, or **246** rows with Atto 3.
  - Fiat 500 (342 items) and Koenigsegg CC (491 items) also contain 12–13 of the 23 Atto-3 SKUs.
- A search for q=7O0005 with BYD / Atto 3 returns the oil filter with `is_estimate=true`.

## 5. Wrong-brand offers
| Set | Result now |
|---|---|
| 835 repaired | 835 equal to `done[].after` (price + URL); 0 updated since |
| IDENTITY_CONFIRMED_NO_PRICE (23) | all unchanged, all available; 18 still equal a foreign-brand price |
| OTHER_EVIDENCE_GAP (41) | all unchanged, all available; 21 still equal a foreign-brand price |
| UNRESOLVED_SOURCE_BLOCKER (36) | all unchanged, all available; 16 still equal a foreign-brand price |
| UNRESOLVED_IDENTITY (15) | all unchanged, all available; 5 still equal a foreign-brand price |
| VALID_CURRENT_STATE (1) | unchanged, available |

- **Totals for the 116 unrepaired:** 116/116 unchanged and available. **60** still carry exactly the price of a foreign brand's qualified offer (`<FOREIGN>~article`).
- **89 G_VALID_CURRENT_STATE:** 68 intact and 21 changed. All 21 were changed by `car_parts_ie_import` on 11-15 and on 11-16 16:52–20:17, before the restart. Whether those 21 writes were brand-correct is NOT PROVEN.
- **The 3 earlier repairs** (PT155, ZR CT728 R, ZR PG57 L) still hold their 11-16 20:39 `cpie_overwrite_repair` values.
- **New cross-brand writes after the restart:** 0.

## 6. Catalog estimates
- Parts touched after the restart: 758. **350 have no offer anywhere** (13 new, 337 existing). Every one of them has a base_price equal to `catalog_estimate_price().net` (source "estimate").
- The estimate is built mostly from `online_price_ils`. The importer writes that field **only on insert**, so for existing parts it can be stale.
- base_price itself is not shown to customers.

**What `/api/v1/parts/{id}/suppliers` returned for 5 offer-less parts:**
- Each returned one offer: "AutoSpareFinder", `availability=on_order`, `is_base_price_fallback=true`, 14 days.
- Totals: 78.33, 76.31, 72.53, 99.61 and **2035.12** (the 2035.12 is a RENAULT clutch flywheel).
- The UI labels it "מחיר משוער" (Parts.jsx:1348), and search returns `is_estimate=true`.
- It is labelled, but it is still a priced, orderable offer **on parts whose fitment is wrong** (Atto-3 spark plugs).

## 7. Protected jobs — PROVEN not cut
- AliExpress sync ran inside sync_prices: it finished 00:26:15 (116 checked, 12 updated), and sync_prices finished 00:36:45.
- The old harvester was idle (resting since 02:56).
- The Facebook scan runs at 10:00 IL.
- The restart cut only the in-process `[Cleanup] cycle 188: CancelledError`, a 30-second self-healing loop.
- `whatsapp_bridge` restarted separately (up about 3 hours). That is out of scope and not attributed.

## 8. Remaining silent-failure paths
1. **The 645e5bc fix is not loaded.** A refused final chunk after a partial delivery is dropped and the model is marked `done`.
2. **`post_relay(vehicle, [], done=True)` ignores its return value**, even in 645e5bc. If the done POST fails, the importer never runs, the buffered parts stay in uvicorn memory, and the model is marked `done` with n parts. The only trace is a WARNING.
3. **`/collect` buffers are in memory in uvicorn.** A backend restart between chunks loses them without notice. A later `done` imports a partial or empty buffer. An empty buffer even routes to the OEM importer, because `is_cpie_data` is False.
4. **The importer is a fire-and-forget `Popen`.** Its exit code, crashes, flock waits and skip reasons never reach the harvester or `complete_model`. "Parts" means parts delivered, not parts imported.
5. **Importer evidence is easy to lose.** The `/tmp/*_cpie_import.log` files are overwritten on every re-harvest. There is no owner alert for `ERROR … NOT imported`. Nothing resumes a SIGTERM checkpoint, and a stall-watchdog kill after 10 minutes leaves a partial import.

An extra risk: a 15-second relay timeout on a request the server accepted can be counted as a failure. The chunk is then re-sent, and the buffer has no deduplication. That is harmless, because of ON CONFLICT.

## Where I disagree with the repo
- The "final relay fixed" status should read **APPLIED-on-disk, NOT LIVE** until pid 87 restarts. Even after that, path 2 remains.
- The 480e961 claim covers future imports only. The 03:59 imports ran without it.
- The generic-block problem is not specific to Atto 3. 246 fitment rows on 10 EV and Chinese-brand models need to be reviewed.
