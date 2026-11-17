# Independent consultant — FINAL verification of the CORE relay recovery (P2/P3/P5 runtime), 2026-11-17

Auditor: independent consultant (Claude Opus 5.5). Work done 19:39–20:10 IL on 2026-11-17 (17:39–18:10 UTC).
All times are Israel local time (IL = UTC+2). UTC is given in brackets where it identifies a log line or a DB value.

**Mode: READ-ONLY.**
- Every DB session ran with `default_transaction_read_only=on`, asserted per connection.
- No restart, stop, kill, import, requeue, commit or edit was made. Nothing was written to `backend/`.
- The targeted tests ran in a throwaway container: `docker run --rm --network none` from the same image (`bf7efaf6e2bf`), on a COPY of `backend/`.
- **Side effect (disclosed):** one helper file was copied by mistake into the production container's `/tmp` (`/tmp/consult_db.py`). It was deleted immediately. It was not in `backend/` and nothing ran from it. All other scripts were piped through stdin. Scratch output is in `/tmp/claude-0/-opt-autosparefinder/e01c3c12-ef7b-4040-8548-e5f0a5995311/scratchpad/v/`.

## Verdict summary

| # | Item | Task status | Key evidence |
|---|---|---|---|
| 1 | Relay integrity | **PASS** (Talbot partial collection = **PARTIAL by design**) | 63 chunks + 8 `done` = 71/71 POSTs answered 200; every chunk set equals the payload (3,003 items, 0 duplicate SKUs). 8/8 importer exit 0, visible to the harvester. Each `parts_found` delta equals `sent` exactly. Only the success path ran live. |
| 2 | Offer identity | **PASS** | 2,825/2,825 offers written in the window carry the OWNER-brand payload price (EUR × 3.48). 0 foreign. 2,965/2,968 imported items own an offer at their own price. The 3 without one are the fail-closed skips. 0 duplicates. 0 `@` in 16/16 live API responses. |
| 3 | Fitment guard | **PARTIAL** | The 8 imports ran the **pre-e9941ed** guard (PROVEN). 15 block-item fitment rows on the 2 hybrids were re-asserted, 2 of them diesel glow plugs. The current guard would deny 13 of the 15, but it is **not yet exercised live**, and it never deletes existing rows. |
| 4 | base_price | **PASS** | 2,828/2,828 touched parts equal my own recomputation: 2,819 car-parts.ie-only offer, 6 mixed, 3 estimate, 0 mismatches. The reported 815 updates is PLAUSIBLE (bounded between 332 and 1,281); it cannot be reconciled exactly. |
| 5 | Skips | **PASS** (fail-closed correct) with resolver gaps | 35 `brand_unresolved` (claim of "2-8 per run" confirmed) and **3** `offer_owner_brand_unresolved`. **The claim "4 runs had 1" is rejected.** 17 of the 35 skipped articles still have an older live offer; 3 of those prices are stale. |
| 6 | Regression / running code | **PASS** with a process finding | 187 targeted tests pass, 4 skipped (isolated DB only). Running code equals HEAD for 16 files. **The claim "eBay files are uncommitted" is rejected:** they went into e9941ed and have been LIVE since restart #2. |
| 7 | Scheduled-run proof | **BLOCKED** (not satisfiable today) | The 8 models were requeued **manually** at 17:28:35 IL by the operator session (UPDATE 8). The ~01:00 IL night pipeline never runs the harvester. The earliest system-driven requeue is **2026-11-27 ≈09:17–10:17 IL**. |
| 8 | Open items unchanged | **PASS** (unchanged), still **NOT DONE** | 951/951 round-4 offers, 89/89 G89 and 3/3 earlier repairs were untouched by the 8 runs. Scoped-key growth measured: 1,403 new scoped rows from 3,003 items. |

**Overall: CLOSE-WITH-CAVEATS stands for the relay, offer identity and base_price paths on these 8 runs.**
- I found **no wrong price, wrong-brand write, lost offer, duplicate or base_price error created by the 8 runs**.
- Two effects of the runs are by design, not defects:
  - They re-asserted 15 existing generic-block fitment rows on 2 hybrids, 2 of them diesel glow plugs (they used the pre-e9941ed importer).
  - They created 1,403 scoped twin offers. **345 of those already differed in price from their bare same-brand twin at creation.**
- The new full-payload fitment guard (e9941ed) was **not** exercised by them. It is test-verified only.
- The acceptance criterion "genuine scheduled collection on the new code" is **not met** and cannot be met before 2026-11-27.

---

## Timeline (IL; verified from the harvester log, the backend log, DB values and the operator transcript)

| Time (IL) | Event |
|---|---|
| 08:02:39 | Restart #1. uvicorn pid 1 started 06:02:39 UTC; harvester **pid 52** started 06:03:43 UTC. |
| 08:14:32 | Last successful cf_clearance mint (06:14:32 UTC); `cpie_clearance.json` mtime matches. |
| 17:19:13 | Cycle 4628: queue drained, "Requeued 0 models >14d old", rest 3,600 s. |
| **17:28:35** | **Operator session (transcript `70055dd9…`) ran `update harvest_queue set status='pending' … where id=any(...)`** on 8 randomly chosen `done` models: **UPDATE 8**. A first attempt with a 3-day filter at 17:28:23 returned UPDATE 0. |
| 18:19:18 | Cycle 4629: `pending=8`. |
| 18:20–18:22:49 | 3 × FlareSolverr HTTP 500, then "could not mint cf_clearance cookie after retries". |
| 18:22:49–18:30:06 | All 8 models harvested. The workers used the **in-memory cookie minted 08:14 IL (~10 h old)**. `have_clearance` checks that a cookie exists, not that it is fresh, and Cloudflare still accepted it. |
| 18:24:20–18:29:56 | 8 importer subprocesses under `relay_run.py`: results ok, exit 0. |
| 18:33:23 | e9941ed files written to disk (`cpie_fitment_guard.py`, importer, price_policy, etc.; mtime 16:33:23 UTC). |
| 18:42:24 / 18:43:38 | Restart #2: uvicorn pid 1 and harvester **pid 199** (the new code). Both start after every mtime. |
| 18:43:44 → | pid 199 idle, "Requeued 0", resting 3,600 s. **It has processed no model yet.** |

---

## 1. Relay integrity — PASS (Talbot = PARTIAL by design)

**Code path (read):**
- `harvest_model` sends 50-item chunks under one `run_id` with `seq` and keeps every acknowledged chunk.
- `relay_done` POSTs `done` with `expected_chunks`/`expected_parts`. On a 409 it re-sends the missing seqs (3 tries). It then polls `/collect/import-status` until `ok`; on `failed` or after 1,500 s it raises `HarvestFetchFailed`, which releases the model.
- On the server, `relay_store` writes each chunk atomically to disk, and `missing_or_parts` refuses `done` unless every chunk and the exact part count are present.
- `relay_run.py` writes the exit code into the per-run result file.

**Live evidence (8 runs):**

| Model | run_id | Chunks (sizes) | Payload items = chunk sum = parts_scanned | Unique SKUs | Exit | Harvester log |
|---|---|---|---|---|---|---|
| hyundai/elantra-hev | 4f035bf9… | 7 (6×50 + 28) | 328 | 328 | 0 | importer finished ok 18:25:11 |
| skoda/fabia-iv-pj3 | bcbd60cc… | 9 (8×50 + 12) | 412 | 412 | 0 | 18:25:44 |
| renault/mascott-platform-chassis-uh-hh | de59123f… | 8 (7×50 + 49) | 399 | 399 | 0 | 18:26:01 |
| toyota/rav4-hybrid | d8740d5b… | 7 (6×50 + 27) | 327 | 327 | 0 | 18:26:20 |
| renault/espace-ii-j-s63 | b9518fb8… | 8 (7×50 + 42) | 392 | 392 | 0 | 18:26:39 |
| tata-telco/indica-40-v2 | b92fad8a… | 8 (7×50 + 23) | 373 | 373 | 0 | 18:28:48 |
| talbot/express-1000-1800-box | 45807549… | 8 (7×50 + 33) | 383 | 383 | 0 | 18:29:15 |
| toyota/starlet-ep91 | 6daccc8c… | 8 (7×50 + 39) | 389 | 389 | 0 | 18:30:06 |

- **Backend log, 17:00–18:45 IL:**
  - 71 `POST /api/v1/system/collect`, all `200 OK` (63 chunks + 8 `done`).
  - **0 × 409**.
  - 8 × `[collect] acknowledged relay complete … parts=<n>`; each n equals the table above.
  - The `import-status` polls all returned 200.
- **Live vs test-only (important label):**
  - Only the SUCCESS path ran live.
  - These were NOT exercised live: re-send on 409, release on importer failure / timeout, and refusal of a final chunk. They are verified by tests only (`test_cpie_relay_reliability.py`, part of item 6).
- **Durability caveat:** `relay_store.gc()` deletes chunks, payloads, logs and results older than `KEEP_S` = 3 days.
  - gc runs only when a relay `done` is acknowledged. **The evidence for these 8 runs (and byd/atto-3) is deleted at the first relay completion after ~2026-11-20 18:30 IL.** Most likely that is the 11-27 refresh itself, the run it should be compared against.
  - Archive it before the next harvester run (≤ 2026-11-27).
  - Payload sha256[:16] values: elantra e99710490eb21899 · rav4 1669a83c28674e9c · mascott 7e74eb57e3d53c2f · espace 28e3cd9a8ef0c4cd · fabia 2094f98327245892 · talbot eab237d920415138 · tata f135f0b71217758f · starlet 1300886deef25050 · atto f056a9be36e95f70.

**`parts_found` 2,790–3,227 vs `parts_scanned` 327–412 — explained (VERIFIED from code + numbers)**
- `complete_model()` runs `SET parts_found = parts_found + %s` with `sent`, the number of parts delivered in THIS run. It is a lifetime cumulative counter over every completed harvest since July, not this run's count.
- The before values come from the operator's own query at 17:28:36 IL (transcript), just before the requeue. Each delta equals that run's delivered parts exactly:

| Model | Before | After (live) | Delta | Run's payload |
|---|---|---|---|---|
| skoda/fabia-iv-pj3 | 2,815 | 3,227 | +412 | 412 |
| renault/mascott… | 2,391 | 2,790 | +399 | 399 |
| hyundai/elantra-hev | 2,642 | 2,970 | +328 | 328 |
| renault/espace-ii-j-s63 | 2,739 | 3,131 | +392 | 392 |
| toyota/starlet-ep91 | 2,698 | 3,087 | +389 | 389 |
| talbot/express… | 2,761 | 3,144 | +383 | 383 |
| tata-telco/indica-40-v2 | 2,595 | 2,968 | +373 | 373 |
| toyota/rav4-hybrid | 2,585 | 2,912 | +327 | 327 |

- `attempts` before → after: 12→13, 9→10, 7→8 (`claim_next_model` adds 1 per claim).
- All 8 rows are `done`. `last_harvested_at` / `updated_at` = completion times 18:25:11–18:30:06 IL.
- **Caveat:** `parts_found` counts parts *delivered*, not parts *imported*. 38 items were not written: 35 parts skipped plus 3 offers skipped (item 5). Those skips are logged as ERROR and appended to `cpie_import_skips.log`, but they never reach harvest_queue, the harvester or the owner.

**Talbot partial collection — PARTIAL by design, not a silent drop**
- Log: `WARNING talbot/express-1000-1800-box: 2 page fetch(es) failed, 83 ok - partial collection`. There were 85 fetches: 83 slug pages, the model page and 1 variant page.
- The first failure is identified: `TimeoutError … /talbot/express-1000-1800-box/brake-pads` at 18:25:50 IL. The payload has **0 brake-pads items** (51 distinct listing slugs).
- **The second failed URL cannot be identified.** `_log_fetch_failure` counts per REASON per PROCESS and logs only the 1st and every 50th, so the second same-reason failure was never logged. Small logging defect.
- **Code behaviour:**
  - `sent > 0`, so no `HarvestFetchFailed`, and the model is recorded `done`.
  - No partial state is stored.
  - The missing pages are not retried before the 14-day refresh.
- 0 live car-parts.ie offers carry a talbot/express brake-pads `supplier_url`, so no stale offer from that page exists either.
- Verdict: correctly *not* silent (logged WARNING), but `done` hides partial coverage. Proposal: record `partial` (or the blocked count) on the queue row and requeue partials sooner.

**Minor security finding (latent, does not block)**
- `GET /api/v1/system/collect/import-status` (routes/system.py:430) uses `if _COLLECT_SECRET and header != _COLLECT_SECRET` → **fails OPEN** when the secret is unset. CLAUDE.md §5 forbids this pattern. The same pattern exists at line 189 (the unpriced-OEM feed handler) and line 283 (`/collect`).
- In production `COLLECT_SECRET` is set (verified: present and non-empty in pid 1's environment), so the check is effective today.
- Fix: `if not _COLLECT_SECRET or header != _COLLECT_SECRET: raise 403`.

## 2. Offer identity — PASS

**Method:**
- Script `analyze.py` (read-only) loaded the 8 retained payloads (3,003 items).
- It derived each item's brand with the production resolver (`cpie_identity.brand_of` = `part_identity.title_brand`) and its part row (legacy `<MAKE5>-<article>`, or the brand-keyed row).
- It compared against every car-parts.ie offer (supplier `88f6ce24…`, country IE) with `updated_at` between 18:19 and 18:31 IL (16:19–16:31 UTC).

**Results:**
- **Rate:** `EUR_ILS_RATE` is unset in pid 1's environment, `.env` and docker-compose.yml, so the importer default **3.48** applies. Confirmed empirically: every matched price equals `round(price_eur × 3.48, 2)`.
- **Offer-centric:** 2,825 offers were written in the window (matches the earlier consultant's 2,825).
  - **2,825 / 2,825** have a price equal to a payload item of the offer's OWN part brand.
  - 0 equal only a foreign-brand item. 0 match no payload item. 0 have a brand token that differs from the part brand.
- **Item-centric (the earlier finding (b), bare-key collision):**
  - Of 3,003 items: 35 `brand_unresolved` (no part, no offer) and 3 imported parts with no own offer. Those 3 are the 3 `offer_owner_brand_unresolved` skips, i.e. fail-closed.
  - **2,965 / 2,965** remaining items own an offer whose price equals THAT item's price, and every one was updated in the window.
  - **No part was left without its own offer by a bare-key collision.** The mechanism of finding (b) is closed on this sample.
- **Scoped keys:**
  - 1,555 of those own offers are part-scoped (`MAKE@…`).
  - **1,403 new scoped rows** (1,415 new offers in total) were created in the window. Car-parts.ie scoped total: 352 (P1) + 1,403 = **1,755**.
- **No customer leak of the scoped key:** live `GET http://localhost:8000/api/v1/parts/<id>/suppliers` for 16 parts (12 scoped: TALBO@502 458, SKODA@RS0064, RENAU@VKDS 812007, TATAT@3103283, TOYOT@50007871, …; 4 bare/keyed incl. MAPCO~70151).
  - Every response showed the bare article (`502 458`, `RS0064`, `70151`, …).
  - **0 responses contain `@`.**
  - The displayed net equals offer cost × 1.45 (e.g. 7.45 → 10.80; 730.07 → 1,058.60).
  - I read `customer_vat_ils` on 3 responses: 0.0 each. Their totals are net + 59.00 shipping.
- **Duplicates (car-parts.ie, whole supplier):**
  - (part, supplier) untyped with more than one row: **0**.
  - (supplier, sku) duplicates: **0**.
- **price_history:** 332 `car_parts_ie_import` rows in the window (price changes of existing offers).

## 3. Fitment guard — PARTIAL

**Which code ran (PROVEN by behaviour):**
- The importer is a fresh subprocess and loads the files on disk at spawn time.
- The 8 imports (18:24–18:29 IL) ran BEFORE the e9941ed files were written (18:33:23 IL). That is the 1b613d6 guard: "a <100-item payload gets no fitment; full payloads unchanged".
- Proof: replaying the CURRENT `fitment_allowed` (e9941ed) over the payloads rejects 13 items (7 Elantra HEV, 6 RAV4 Hybrid), yet every run reported `fitment_skipped: 0`. **e9941ed's full-payload rule was therefore NOT exercised by these runs and is NOT verified live.** pid 199 has processed no model since it started.

**Writes:**
- 2,968 `part_vehicle_fitment` rows touched in the window, equal to the sum of the importer's `fitment_rows`.
- Only **30 newly created**; the rest are re-asserted existing rows (`updated_at` bumped).
- By model: Mascott 391, Fabia 410, Elantra HEV 325, Starlet 384, RAV4 Hybrid 322, Espace 390, Talbot 380, Tata 366.
- **0 rows on any BEV model.**

**Block-style items (`Name (BRAND, SKU)`)** occur only in the two hybrid payloads: 8 Elantra HEV and 7 RAV4 Hybrid. All 15 sit on the listing slug `brakes-auto`. All 15 rows pre-existed (created 2026-07-07 to 2026-08-17) and were re-asserted:

| Item | Elantra HEV pvf | RAV4 Hybrid pvf | Current guard | Physically wrong? |
|---|---|---|---|---|
| Glow plug (RIDEX 243G0006) | 58274d4d | 901962df | deny | **YES: diesel-only on a petrol hybrid** |
| Spark plug (RIDEX 686S0003) | 0178ac84 | b132b953 | deny | Plausible part type (petrol engine); specific fit unproven |
| Oil filter (RIDEX 7O0005) | b8808351 | (not in RAV4 payload) | deny | Plausible type; fit unproven |
| Sump plug washer (TOPRAN 108 647) | 5335b36c | abec9563 | deny | Plausible type; fit unproven |
| Indicator bulb (HELLA 8GD 002 078-121) | 2fadc3c0 | 400b5272 | deny | Unproven |
| High beam bulb (BOSCH 1 987 302 777) | 2b8bfb1a | 7016522d | deny | Unproven |
| Brake Fluid (A.B.S. 7501) | c2df78c2 | a4653b1f | deny | Generic fluid; fit unproven |
| Brake pad set (RIDEX 402B0033) | 43578f41 | da5b3f88 | **allow** (slug brakes = name brakes) | Unproven, and the guard cannot catch it |

- The hint "ICE-only parts on hybrid/EV-like models" is **only partly right**. Elantra HEV and RAV4 Hybrid have petrol engines, so spark plugs, oil filters and engine oil are correct PART TYPES for them. Only **diesel-only** parts are certainly wrong.
- Exactly **2 petrol/diesel-only (diesel) rows** exist, the 2 glow plugs. A whole-model query finds no other diesel-named row on these two models (Elantra HEV: 506 rows in total; RAV4 Hybrid: 469).
- The other 13 block rows carry no vehicle-specific evidence (generic block).
- The 6 ICE models contain no block-titled items.
- **Limit of the guard:** `fitment_allowed` only stops NEW positive fitment. The 15 rows stay; after a re-harvest on the e9941ed code only their `updated_at` stops moving.
- Nothing was deleted. A cleanup of these 15 rows (or at minimum the 2 glow plugs) needs a pinned manifest and owner approval, like the P2/BEV cleanups.

## 4. base_price — PASS

**Independent recomputation** (script `bp.py`, my own SQL + arithmetic, not `base_price_rule.target`):
- Over the **2,828 distinct parts** touched by the 8 runs (2,968 imported items map to 2,828 parts; shared articles across models).
- Visible offer = available, supplier active, not in the non-orderable names, non-empty URL, cost > 0. Class-compatible = untyped, or same `part_type`.
- Expected = `round(min cost × 1.45, 2)`. Car-Parts.ie has `vat_price_basis = NULL` and country IE, so 0% VAT does not enter the net.
- Without a visible offer: the catalog estimate (importer → max/1.18 → online) × 1.45.

| Class | Parts | Equal |
|---|---|---|
| Offer, car-parts.ie only | 2,819 | 2,819 |
| Offer, mixed suppliers | 6 | 6 |
| Catalog estimate (no offer) | 3 | 3 |
| **Total** | **2,828** | **2,828 (0 mismatches)** |

- That is far above the 100-part minimum sample.
- The 3 estimate parts are the 3 items with no own offer from item 5. Matched by ID: RENAU-COF100270S, SKODA-10599 and TATAT-OE005/T10, each with 0 offers. That is the open "estimate" decision, not a defect of these runs.
- **Reported updated counts (39–140 per run, total 815): PLAUSIBLE, not exactly reconcilable.**
  - The importer keeps no per-part before-value journal.
  - Lower bound: 332 parts had their offer price changed (price_history). They are expected to move, since 2,819/2,828 parts have only a car-parts.ie offer.
  - Upper bound: 332 ∪ 949 parts whose new (scoped) offer differs from their `online_price_ils` estimate = **1,281**.
  - 815 lies inside [332, 1,281].
  - The skipped counter is 0 in all 8 runs, so no `locked_or_changed`.

## 5. Skips — PASS (fail-closed correct), with resolver gaps

**35 `brand_unresolved`** (no part write, no offer write). Per run: 3/5/8/2/2/3/7/5, which confirms the hint "2–8". Each was re-run through `part_identity` (`brand_key`, `canonical_brand`, `_suffix_brand_ok`):

| Group | n | Items | Assessment |
|---|---|---|---|
| A. Deliberate suffix deny-list (`_NOT_A_SUFFIX_BRAND`) | 16 | CHAMPION ×8 (CFF100111, OE009/T10, OE247, COF100118S, CFF100206, CFF100131, CFF100453, OE179/T10); FAST ×5 (FT49010, FT86220-3); MOTIVE ×3 (T1965, T4460, HL54) | Correct fail-closed per policy. In every case the title brand IS the maker (Champion CFF/COF/OE schemes, FAST FT-, Motive). Coverage gap; fixable with a brand+article-pattern rule. |
| B. Known brand but below the vehicle-first suffix gate (shorter than 3 chars, or fewer than 200 vocabulary rows) | 11 | AE (CAM966), AXYO (85012000006), AS ×2 (61069, 95140), K2 (EB18), FA KROSNO (23325), NTK (92457), XL (132325), EBC BRAKES (DP2762), HI-Q (SP4410), KILEN (464053) | Correct per gate. Genuine brands, notably **NTK, EBC Brakes, Hi-Q**. Coverage gap. |
| C. Not in the vocabulary, even in brand-first titles | 8 | 555 ×5 (SR-A110, SR-T810, SB-4592, SE-4751, SR-2940), SBP (02-RV002), FRAM (G6398), TECH (Z12, €0.54 dashboard) | **FRAM, 555 and SBP** are real brands missing from `article_brands.json`: notable gap. TECH Z12 looks like junk. |

- None of the 35 is genuinely ambiguous in its title. All are resolver coverage gaps, and failing closed was the correct runtime behaviour.
- **Consequence:**
  - 35/35 legacy part rows (`<MAKE5>-<article>`) already exist; **17 of them hold an older car-parts.ie offer** that these runs could not refresh.
  - 14 of the 17 still equal today's payload price.
  - **3 are stale and customer-visible:** TOYOT-95140 796.19 vs 877.31; RENAU-23325 60.37 (from 2026-09-10) vs 54.29; TATAT-SP4410 137.95 vs 137.84.

**3 `offer_owner_brand_unresolved`** (mascott, skoda, tata-telco; **not 4**). The part and fitment were written; the offer was skipped because the bare key is held by an unresolved-brand holder:

| Item | Bare-key holder | Assessment |
|---|---|---|
| RENAU-COF100270S (CHAMPION oil filter) | FIAT-COF100270S "FIAT 130 … Oil filter CHAMPION COF100270S", 36.51, 11-15 | Same brand and product; holder unresolved only through the CHAMPION deny-list → resolver gap |
| TATAT-OE005/T10 (CHAMPION spark plug) | DAEWO-OE005/T10 "… Spark plug CHAMPION OE005/T10", 13.12, 11-15 | Same → resolver gap |
| SKODA-10599 (MEAT & DORIA ignition coil) | **JAG-P-10599 "Borg Warner Model 12 Type Gearbox O Ring Kit" (Jaguar), car-parts.ie offer 110.56, URL `alfa-romeo/spider-939/water-pump`, 11-15** | **A genuinely misattributed, pre-existing offer:** a water-pump listing price on a gearbox O-ring part. It is **not** in the 951 or 116 manifests. It belongs to the ~6,361 "frozen unresolved-holder" class by definition, but that class has no id manifest, so membership is ASSUMED. Report it to the owner as a new example. |

- Fail-closed was correct for all 3: writing would have touched another part's offer.
- These 3 parts now have only a catalog estimate (base_price = estimate, see item 4).

## 6. Regression / unrelated behaviour — PASS, with a process finding

**Targeted tests**
- Environment: throwaway `docker run --rm --network none --memory 1500m`, image `bf7efaf6e2bf` (the image the production container runs), on a copy of `backend/`.
- Files: test_cpie_identity, test_cpie_fitment_guard, test_cpie_relay_reliability, test_cpie_harvester_clearance, test_cpie_display_sku, test_estimate_not_offer, test_cpie_importer_base_price_rule, test_base_price_writers_2026_11_17, test_price_policy_unified, test_part_identity, test_part_identity_policy, test_harvester_health, test_ebay_api_watch.
- **Result: 183 + 4 = 187 passed, 4 skipped (isolated DB only), 0 failed.**
- The first run showed 4 failures that came from the harness, not the code: `catalog_scraper` refuses to import without `DATABASE_URL`, and `test_harvester_health` reads `../docker-compose.yml` and `../CLAUDE.md`. They pass with a dummy unreachable `DATABASE_URL` plus read-only copies of those two files.
- The full suite was NOT run (not requested, and not in production).

**git state**
- `git status`: only `?? archive/data/NOA_test_15s.mp4`.
- `git diff HEAD -- backend`: **empty**.
- **The claim that `services/suppliers/ebay_supplier.py`, `ebay_api_watch.py` and `tests/test_ebay_api_watch.py` are uncommitted is REJECTED.** They were committed inside the CORE commit **e9941ed** ("fix(core): consultant final…").
- Since restart #2 they are **LIVE in uvicorn**: `ebay_supplier` imports `ebay_api_watch` and calls `check()` on every Browse response.
- They are unrelated to CORE and were not reviewed as part of it. Observed behaviour since 18:42 IL is harmless: one eBay token call and one Browse search at startup, both 200; 0 `ebay_api_watch` alert or error lines; 0 Tracebacks.
- Process finding: an unrelated change shipped inside a CORE commit and was deployed by the CORE restart. Recommend recording it in FIXES_TRACKER as its own entry.

**Running code == committed code** (method: disk sha256 vs `git show HEAD:` blob, plus process start after the last mtime)
- 16/16 files SAME: harvester 2986e41c9d1485f8, importer 7033cf7e635d49b3, cpie_identity 33d92257a152aa37, cpie_fitment_guard 1220a6ae70322087, relay_store 45e15bacd91e230f, relay_run 3be5b7719555d03f, routes/system c108227f8d587890, routes/parts 390e75c0a88a0623, routes/utils 33233fdf0c30362a, price_policy b7d26a046433f494, base_price_rule 26cdcb1e597d5c11, routes/cart 0bb6263fe919c407, routes/orders 936a48146a99a312, routes/payments e292528f67ac3257, ebay_api_watch 8982b862a2911bf8, ebay_supplier b33547673374dba8.
- The latest mtime is 18:33:23 IL. uvicorn started 18:42:24 IL and the harvester 18:43:38 IL, both after it.
- VERIFIED for disk + start order. Loaded-module identity (`runtime_identity_check`) was not run (ASSUMED equal).
- **For the 8 runs themselves:**
  - pid 52 harvester = 2986e41c. Its file is unchanged since 07:21 IL, before pid 52 started at 08:03 IL.
  - The importer was the pre-e9941ed blob (see item 3).

## 7. Scheduled-run proof — BLOCKED (verdict: manual requeue)

- **Origin of the requeue (VERIFIED):** operator session transcript `70055dd9-…jsonl`, tool call at 15:28:34 UTC = **17:28:34 IL**. It ran `select … from harvest_queue where status='done' and parts_found between 100 and 3000 order by random() limit 8` and then `update harvest_queue set status='pending', updated_at=now() where id=any($1) and status='done'` → **UPDATE 8**.
- The harvester's own refresh logged "Requeued 0 models >14d old" at 17:19, 18:32, 18:36 and 18:43 IL.
- A grep of every non-test `harvest_queue` writer in `backend/` finds:
  - the harvester itself: claim, complete, release, `reclaim_stale_in_progress` (only `in_progress` rows) and `requeue_completed_for_refresh` (14 days, and only when pending = 0);
  - `maintenance/seed_car_parts_ie_full_catalog.py`, which INSERTs only NEW models (`ON CONFLICT DO NOTHING`).
- Nothing else sets `pending` on `done` rows: no watchdog, no job_registry job, no console command.
- The seed loop (`CPIE_FULL_SEED_ENABLED=1`) logged at restart #2: "first seed after this start in 519.3 h", i.e. **≈2026-12-09 IL**.
- **This run does NOT satisfy "genuine scheduled collection on the new code".** It is a valid live end-to-end exercise of the relay success path on the 08:02 code with a manually seeded queue. It ran the pre-e9941ed importer, and the current harvester pid 199 has run nothing yet.
- **The "~01:00 IL night run" is the wrong target.** The night pipeline (01:00–07:00 IL: auto_backup, ebay_fitment_backfill, sync_prices, rex_night, category_discovery…) does not start, requeue or feed the car-parts.ie harvester. The harvester is continuous and has no time-of-day logic. Waiting for 01:00 IL proves nothing about P5.
- **Earliest system-driven collection:**
  - The oldest `last_harvested_at` among done/empty rows is 2026-11-13 07:17 UTC (197 rows on 11-13, then 1,435 on 11-14, 2,694 on 11-15, 2,358 on 11-16).
  - 14 days later is **2026-11-27 ≈09:17 IL**. The harvester rests up to 3,600 s when idle, so expect the first model **≈09:17–10:17 IL on 2026-11-27**.
  - The only other system path, the seed loop, is due later (≈2026-12-09).
  - Owner alternative: accept this manual-requeue evidence and keep the "scheduled" criterion waived, as recorded in the tracker.
- **What to check after the first genuine refresh (2026-11-27):**
  1. The harvester log shows `Queue drained … Requeued N models` with N > 0 BEFORE any claim; no operator `UPDATE harvest_queue` in any session transcript or shell history for that window.
  2. For each run: a `results/<tag>__<run>.json` with `state ok`, `exit_code 0`; chunk count and sizes equal the payload; `[collect] acknowledged relay complete` in the backend log; 0 unexplained 409.
  3. `parts_found` delta equals the payload size; any model with `N page fetch(es) failed` is listed as partial.
  4. Offers written equal the owner-brand payload price × 3.48 (re-run `analyze.py` with the new window); 0 duplicates; 0 `@` in `/suppliers`.
  5. Where block items exist in a full payload, `skip_reasons.fitment_generic_block` > 0, which is the **first live proof of e9941ed**. The 15 hybrid block rows keep their 18:25–18:26 IL `updated_at` when their model is re-harvested.
  6. base_price of the touched parts equals the rule (`bp.py`).
  7. The 951 + 89 + 3 offers are unchanged.
  8. Archive the relay payloads/results first: they are garbage-collected after 3 days.

## 8. Still OPEN — not in my authority to close (confirmed unchanged by the 8 runs)

| Item | Status | Checked now |
|---|---|---|
| Wrong offers. The "1,043" figure is **outdated**: it was the 11-16 set; after round 4 there are 835 repaired + 116 not repaired. | 116 not repaired (115 UNVERIFIED + 1 VALID_CURRENT_STATE), all `is_available=true`, customer-visible | 951/951 found. **0 updated in the 8-run window, 0 since 06:02 UTC**; max `updated_at` 04:48 IL (02:48 UTC). G89: 89/89, 0 in window, max 11-16 22:17 IL. The 3 earlier repairs still hold 149.67 / 585.75 / 267.02 (11-16 22:39 IL). |
| "116 P4 offers" | Same set as above (owner-brand price UNVERIFIED) | Unchanged |
| "79 large-payload fitment contaminations" | **Figure superseded** (previous consultant: 818 rows on 52 pure-BEV models; BEV cleanup applied to 231; C1: 1,777 ICE-named rows remain on 44 BEV models; ~11.6k catalogue-wide block rows) | 8 runs touched **0 BEV-model fitment rows**. They re-asserted 15 block rows on 2 hybrids (item 3) |
| Frozen unresolved-holder offers (~6,361) | Open | 3 seen this run (item 5), incl. the new misattributed JAG-P-10599 example |
| Catalogue-wide base_price drift (~470K STALE/ESTIMATE) | Open | The 2,828 touched parts are now consistent (item 4) |
| base_price writer formula inventory (IL importers' own ×1.45, rockauto/amayama/oempartsonline) | Open | Not re-checked |
| Scoped-key table growth (owner decision C3) | Open | **Measured:** 1,403 new scoped rows from 3,003 items (46.7%; simulation said ~57%), ≈175 per model. Linear extrapolation to 6,695 models ≈1.17M rows (ASSUMED). **Twin drift already visible: 358 of 1,755 scoped offers differ in price from their same-brand bare twin. Of the 1,403 created by these 8 runs, 345 (24.6%) differed at creation.** |
| Relay robustness paths not exercised live (409 re-send, importer failure, final-chunk refusal) | Test-only | — |
| Full-payload fitment guard (e9941ed) | Test-only, not live-proven | — |

## Defects / findings (none created data damage in the 8 runs)

1. **Relay evidence auto-deletes after 3 days** (`relay_store.KEEP_S`).
   - Proposal: archive payloads/results to `candidates/` (or raise KEEP_S for payloads/results) before 2026-11-20 18:30 IL.
2. **Partial collections are recorded `done`** (Talbot: brake-pads missing, 2nd failed URL unlogged).
   - Proposal: store the blocked count / `partial` on the queue row, requeue partials early, and log every failed URL per model, not 1 in 50 per process.
3. **`import-status` (and `/collect`) secret check fails open when unset.**
   - Proposal: `if not secret or header != secret: 403`.
4. **Stale-cookie reliance:** `have_clearance` checks presence, not freshness. A 10-hour-old cookie was used after a failed mint. It worked here.
   - Proposal: log the cookie age at the start of every claimed model, so a Cloudflare rejection is attributable.
5. **Brand-resolver coverage gaps** (CHAMPION/FAST/MOTIVE deny-list; FRAM, 555, SBP missing; NTK, EBC, Hi-Q under the gate). This leaves 17 frozen older offers, 3 of them with stale prices.
   - Proposal: brand+article-pattern rules for the deny-listed makers; vocabulary entries with evidence (per the part-identity policy).
6. **Pre-existing misattributed offer** JAG-P-10599 (water-pump price on a gearbox O-ring part), outside every manifest. Owner decision.
7. **Process:** unrelated eBay code shipped in CORE commit e9941ed and is live. Record it separately.
8. **2 diesel glow-plug fitment rows** on petrol hybrids (58274d4d, 901962df), plus 13 unproven block rows. These need a pinned cleanup manifest and owner approval.

## Commands and scripts used (all read-only)
- Harvester log: `grep -a '^2026-11-17 1[4-7]:' /app/state/logs/flaresolverr_harvester.log` → `scratchpad/harv_1117.log`.
- Backend log: `docker logs --since 2026-11-17T15:00:00Z --until …16:45Z autospare_backend | grep collect`.
- Relay files: `/app/state/relay/{chunks,payloads,logs,results}`, counted with a stdin Python snippet.
- DB scripts (asyncpg, `default_transaction_read_only=on`, asserted): `scratchpad/v/q1.py` (harvest_queue), `analyze.py` (offers/items), `fit.py` (fitment), `bp.py` (base_price), `extra.py` (Talbot / G89 / skipped legacy / base_price bounds / twins); outputs `*_out.json`, `items.json`.
- API: `GET http://localhost:8000/api/v1/parts/<id>/suppliers` × 16 from inside the container.
- Tests: `docker run --rm --network none … autosparefinder-backend:latest pytest -q <13 files>`.
- Requeue origin: `/root/.claude/projects/-opt-autosparefinder/70055dd9-eb76-47c6-bb2b-57e71adda340.jsonl` (entries at 2026-11-17T15:28:23Z / 15:28:34Z).
