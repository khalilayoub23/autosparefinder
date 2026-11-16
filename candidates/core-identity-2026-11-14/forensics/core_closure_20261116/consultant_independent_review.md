# Independent consultant review - CORE identity repair (Car-Parts.ie)

Reviewer run: 2026-11-16 05:56-06:18 UTC (07:56-08:18 Israel). Read-only against production.
All times below are UTC unless marked IL. The harvester was writing throughout, so every count carries its read time.

## Overall verdict

| Scope | Verdict |
|---|---|
| Repair of the 90 offers (price, URL, identity, journal, backup) | **VERIFIED** |
| Closure evidence for the CORE identity mission as a whole | **PARTIALLY VERIFIED** - three claims are true only in a narrower sense than stated, one fix is not live, and the known damage outside the 90 is untouched |

| # | Claim | Verdict |
|---|---|---|
| 1 | 90 repaired, 75 still equal `after`, 15 owner rewrites, 3 preserved | CONFIRMED WITH CORRECTION (74 / 16 at my read time) |
| 2 | 3,858 moves intact, SKU unchanged, 0 parts with >1 offer | CONFIRMED |
| 3 | 0 customer references | CONFIRMED |
| 4 | 12 of 14 base_price are a legacy estimate, not repair-caused; ~57% catalogue-wide | CONFIRMED WITH CORRECTION (54.5% in my sample; disposition challenged) |
| 5 | Replay: hashes, classification method | CONFIRMED WITH CORRECTION (25/25 re-derived; method caveats) |
| 6a | 0 foreign-brand writes after the fix into offers whose holder brand is resolved | CONFIRMED (own test) |
| 6b | The 5 unresolved-holder events are not real cross-brand overwrites | **NOT CONFIRMED** (all 5 are foreign-brand writes; 3 changed the price) |
| 7 | Importer identity protections | CONFIRMED WITH CORRECTION (one fail-open path; display/PO fix is not live) |
| 8 | Rollback evidence | CONFIRMED WITH CORRECTION (sandbox suite ran on a later tool version) |
| 9 | 36 UNDO + 1,070 NOT_READY unchanged by the repair | CONFIRMED WITH CORRECTION (65 differ now, none by the repair) |
| 10 | Overall | see "Residual defects" |

## Method and access

- DB sessions: `asyncpg.connect(..., server_settings={'default_transaction_read_only':'on'})` plus `SET default_transaction_read_only=on`; `SHOW default_transaction_read_only` returned `on` on both catalog and PII. Only SELECT was issued. The statements are in the appendix.
- Deviation from the brief: besides the DB, I sent two unauthenticated `GET` requests from inside the container to the running backend (`http://127.0.0.1:8000/api/v1/parts/<id>` and `/api/v1/parts/<id>/suppliers`) to see what the live process returns (item 7). No POST.
- Scripts were copied to `/tmp/consult_review/` in `autospare_backend` and removed at the end (`ls` confirms absent). `/tmp/*_cpie.json` was only read.
- Timestamps: `supplier_parts.updated_at`, `price_history.created_at`, `parts_catalog.updated_at` are `timestamp without time zone`, server timezone `Etc/UTC`.
- Brand = `part_identity.title_brand(name, article)` (container hash of `part_identity.py` 20e3ba35...).
- Writer identification (items 1, 5, 6, 9): a payload row whose article equals the offer key, whose converted price (EUR x 3.48, the importer's `EUR_ILS_RATE` default; env not set) and URL equal the live offer, and whose batch window [payload mtime - 2 s, import-log mtime + 2 s] contains the live `updated_at`.
- Deviation from the brief: pytest was run with `PYTHONDONTWRITEBYTECODE=1 -p no:cacheprovider` so nothing was written into the bind-mounted `backend/`.
- Sandbox DB confirmed before the repair suite: host `sandbox_postgres`, db `autospare` / `autospare_pii` (production is `postgres_catalog` / `postgres_pii`).

## Item 1 - the 90 repaired offers: CONFIRMED WITH CORRECTION

Read at 05:57:18-05:58:50.

Query: `select id, part_id, supplier_id, supplier_sku, price_ils, supplier_url, updated_at, created_at, is_available, part_type from supplier_parts where id = any($1)`; `price_history` for the same ids since 2026-11-13; three-way compare against the journal/result `after`, the manifest target and the `pre_apply` backup.

| Check | Result |
|---|---|
| `done` rows, distinct | 90 / 90 (canary 20 + rest 70) |
| READY in manifest | 93; the 3 not done are 856bf4de, ebde0803, c5dbbb3f |
| result `after` == manifest target | 90 / 90 |
| result `before` == manifest `current` | 90 / 90 |
| live part_id and supplier_sku == result == pre_apply | 90 / 90 |
| live owner brand == manifest owner brand | 90 / 90 |
| live == `after` exactly (price, URL, updated_at) | **74** (claim: 75) |
| live differs from `after` | **16** (claim: 15) |
| of the 16: writer is the owner brand | 16 / 16 |
| of the 16: price differs from `after` | 0 (all 16 are URL + timestamp only; no later `price_history` row) |

So all 90 prices still equal the repaired price. The 75/15 to 74/16 shift is one more owner re-harvest since the primary agent's read.

Were the targets themselves right? I re-derived all 90 from the raw payload rows, without the manifest's writer fields:

| Check per repaired offer | Result |
|---|---|
| A payload row of a different, resolved brand has exactly the `before` price and URL, and `before.updated_at` lies in its batch window (the foreign writer) | 90 / 90 |
| A payload row of the owner brand has exactly the target price and URL and its import ended before the foreign batch started | 90 / 90 |
| That owner row is the **last** owner-brand row before the foreign write | 90 / 90 |

Corroboration from later harvests: all 16 owner re-harvests after the repair kept the repaired price, and 2 of the 3 preserved rows were rewritten by their owner to exactly the manifest target (13.85, 129.91).

The 16 (article, owner brand, payload file of the writer): 100075 FEBI BILSTEIN (honda_jazz-ii-gd), 102602 DOGA (kia_sportage-sl), 1360 BRISK (ford_transit-55-bus), 20535 MAPCO (hyundai_tucson-jm), 27503 MAPCO (mazda_626-iv-hatchback-ge), 28277 WALKER (ford_tourneo-connect-2002), 36604 A.B.S. (lexus_rx-mcu15), 36758 A.B.S. (honda_fr-v-be), 37619 A.B.S. (kia_carnival-i-up), 45165 FEBI BILSTEIN (jeep_grand-cherokee-iii-wh-wk), 49775 FEBI BILSTEIN (infiniti_q60-coupe), 58322 AIC (isuzu_trooper), 6346 MAPCO (kia_joice-2000), 6458 MAPCO (ldv_cub-1998), 76906 MEAT & DORIA (hyundai_elantra-xd), PA425 PAYEN (kia_towner-platform-chassis).

The 3 preserved - confirmed owner-brand rewrites made after the manifest read (17:44:57) and before the repair window (18:43:17):

| Offer | Article / owner | price_history row | Payload row in that window |
|---|---|---|---|
| 856bf4de | 55327 NRF | 1247.55 -> 224.01 at 18:08:02 | ford_taunus-17m-p3, ford_transit-box-81e: NRF 224.01 |
| ebde0803 | 44949 FEBI BILSTEIN | 66.61 -> 13.85 at 18:26:06 | ford_focus-iv-saloon-hm: FEBI BILSTEIN 13.85 |
| c5dbbb3f | 6046 KAMOKA | 50.77 -> 129.91 at 18:30:09 | ford_mondeo-iv-ba7: KAMOKA 129.91 |

For 856bf4de and c5dbbb3f, foreign rows (AS 1247.55 x4, NGK 50.77 x3) arrived after the fix (17:03-17:43) and did not write the bare offer - the fix held for these resolved holders.

Notes:
- 856bf4de: the manifest target was 230.76, the owner brand's own current price is 224.01. A READY target is the owner's last payload price before the foreign write, so the 74 untouched rows carry a price up to about two days old.
- `price_history`: exactly 90 rows with source `cpie_overwrite_repair`, 18:43:18.888-18:43:24.331.
- Four `parts_catalog` rows outside the 90 carry `updated_at = 2026-11-15 18:43:25.118104` (ids 13a31c04, 13a5b787, 13a725ae, 13a768a2; one identical timestamp, consecutive id range). The repair tool writes one part per transaction, so this single-statement stamp is not its pattern. Writer not identified. No offer outside the 90 is stamped inside the window.

## Item 2 - the 3,858 moves: CONFIRMED

Inputs: both `cpie_ownership_result_*.json` (`done` 17 + 3,841) and `pre_apply_supplier_parts_20261115.csv.gz` (sha256 acb0e493..., matches `pre_apply_SHA256SUMS.txt`).

| Check | Result |
|---|---|
| distinct offers / distinct parts | 3,858 / 7,716 |
| offer sits on recorded `new_part_id` | 3,858 |
| backup `part_id` == recorded `previous_part_id` | 3,858 |
| `supplier_sku` unchanged vs backup | 3,858 |
| previous and new part brands both resolved and different | 3,858 |
| Car-Parts.ie parts with more than one offer | 0 (`select count(*) from (select part_id from supplier_parts where supplier_id=$1 group by 1 having count(*)>1) x`) |

Car-Parts.ie offers at read time: 797,667, of which 2,577 brand-qualified. 229 moved offers have a newer price from later harvests.

## Item 3 - customer references: CONFIRMED

Scope checked: 5,056 offers (93 + 1,106 + 3,858, deduplicated) and 8,914 parts.

| Table.column | References | Rows in table |
|---|---|---|
| cart_items.part_id / supplier_part_id | 0 / 0 | 0 |
| order_items.part_id / supplier_part_id | 0 / 0 | 154 |
| wishlist_items.part_id | 0 | 0 |
| part_reviews.part_id | 0 | 0 |
| part_price_watches.part_id | 0 | 0 |

Additional: `information_schema` shows no other PII column named like part/offer/sku except `order_items.part_sku/part_name/part_type` and `part_price_watches.part_name`. 0 `order_items.part_sku` contain `~`. The 20 distinct offers ever ordered include 0 Car-Parts.ie offers. `procurement_orders` and `procurement_events` are empty. The result is strong mainly because four of the five tables are empty and the last order item is from 2026-08-13.

## Item 4 - the 14 base_price rows: CONFIRMED WITH CORRECTION

Read at 06:11:17. I rebuilt the SELECT of `_base_price_inputs_sql` (same `VISIBLE_OFFER_SQL`, `class_rank_sql`, cost expression) and computed the target with `price_policy.unit_price` / `catalog_estimate_price`; nothing was written. `price_policy.vat_rate('Car-Parts.ie','IE')` = 0.0, margin 1.45.

All 90 repaired parts: 78 equal the policy target, 12 do not. The 12 are exactly the claimed ones.

| Part | base_price | importer_price_ils x 1.45 | Policy target (offer) | Offer price |
|---|---|---|---|---|
| 033da72c | 124.08 | 124.08 | 115.96 | 79.97 |
| 0b811425 | 259.11 | 259.11 | 258.56 | 178.32 |
| 2a39e944 | 99.25 | 99.25 | 100.47 | 69.29 |
| 30aaabaf | 300.11 | 300.11 | 354.38 | 244.40 |
| 3c69b249 | 156.14 | 156.14 | 160.86 | 110.94 |
| 482dc008 | 124.24 | 124.24 | 124.03 | 85.54 |
| 5ea72820 | 42.37 | 42.37 | 44.70 | 30.83 |
| 72b090f5 | 251.49 | 251.49 | 252.71 | 174.28 |
| 8921cf18 | 99.46 | 99.46 | 88.45 | 61.00 |
| aa3d6310 | 144.46 | 144.46 | 147.39 | 101.65 |
| be186bed | 29.19 | 29.19 | 29.77 | 20.53 |
| f1e77389 | 100.69 | 100.69 | 96.89 | 66.82 |
| 3f785c50 | 29.01 | (28.81) | 29.01 | 20.01 - already equal |
| 9bf26a62 | 85.93 | (101.83) | 85.93 | 59.26 - already equal |

- 12 of 14 equal `round(importer_price_ils x 1.45, 2)`: confirmed, all 12 exact. My 14 policy targets equal the `target` values in `base_price_14_investigation.json`.
- Not repair-caused: confirmed. My own full-row diff of the 90 parts against the `pre_apply` backups shows `base_price` changed on only 2 parts (3f785c50 28.81 -> 29.01, 9bf26a62 101.83 -> 85.93), both by a later importer write; the repair tool itself only stamps `updated_at`.
- Random sample of 1,000 Car-Parts.ie parts (`order by md5(id::text || 'consultant-20261116') limit 1000`): 998 have a target; **545 (54.5%) differ from it**, and 542 of those 545 equal `importer_price_ils x 1.45`. The claim said about 57%; my figure is 54.5%. The conclusion (catalogue-wide, not repair-caused) holds.

Disposition challenge:
- "Leave the 2" is justified.
- "Correct the 12 with the guarded rebase" is not needed to close the repair. The same drift sits on roughly half of all Car-Parts.ie parts, `base_price` is internal, and the rebase path of the tool at its current version (dc224cba, commit 1caa787) has never run in production. Fixing 12 of several hundred thousand rows changes nothing a customer sees. If it is done, it is a separate owner-approved production write.
- Root cause is still open: `car_parts_ie_import_generic.py` writes `base_price = round(price_ils * 1.45, 2)` in its own `ON CONFLICT (sku) DO UPDATE` (lines 284, 343-345). That is a second `base_price` writer with its own formula, which is why the two "healed" rows changed, and why the drift exists wherever the importer has not re-touched a part.

New finding from the same diff: the repair did not correct `parts_catalog.min_price_ils`. At 05:57, 48 of the 90 parts still carried the pre-repair foreign price there (69 differed from the live offer price). A catalogue-wide batch writer in 1,000-row statements rewrote 56 of them at 06:09:59 and 1 at 06:11:30, during my review (inferred: the 07:15 IL `run_all_tasks` run, which was `running` in job_registry; `db_update_agent.py` line 3151 sets `min_price_ils`). Internal field, now self-healed, but it stayed wrong for about 11.5 hours and the execution report does not mention it.

## Item 5 - historical replay: CONFIRMED WITH CORRECTION

Hashes (all match the summary and `SHA256SUMS`):
- `payloads_and_import_logs_20261115.tar.gz` = 436cdc86e551d090...1331d
- `bk14_offers.tsv` 4b281c22..., `bk_offers.tsv` eb2b89f9..., `f2_replay.py` bb3fd9bd..., `f3_classify.py` b969ee2c..., `f4_reconcile.py` 32693216...
- `core_closure_20261116/replay_manifest_20261116.json.gz` == container `manifest3.json.gz` (88afb8a5...)
- `sha256sum -c` passes for `forensics/SHA256SUMS` and `production_repair_execution/SHA256SUMS`.
- Tarball members vs the current `/tmp` files: 6,968 members (3,484 payloads + 3,484 logs), 6,968 identical.

Sample (seed `random.Random(20261116)`, offers sorted by id): 15 VERIFIED_WRONG + 10 VERIFIED_CORRECT. For each I took the live row, found the payload row by article + price + URL + time window (pre-move timestamp for moved offers), and compared `title_brand` of that row with `title_brand` of the live part name.

| Class | Sampled | My re-derivation agrees |
|---|---|---|
| VERIFIED_WRONG | 15 | 15 |
| VERIFIED_CORRECT | 10 | 10 |

No wrongly VERIFIED case found. Sample ids: wrong 6b07a60b 6dcf4f2a c939ad0b ab3fc02a fde0b33b a4c7d413 069b0f76 fe3c006c 60228a66 02787ab1 fd9995f4 b5e223ba 7cb9321b d89d96e6 b3154fec; correct b5dc243c 53ee5540 e424b8d6 0cc74f0b 49c934cf ba119c7b 8ad4b8d2 8130b5b3 91d64c47 d0dd0098.

Is any classification based on article / price / URL equality alone? No. Equality plus the time window only identifies which payload row wrote the offer; the class comes from comparing two brands. `f3_classify.py` does contain one value-equality branch (foreign writer whose value equals an owner-row value -> VERIFIED_CORRECT), but it has 0 members in both the 11-15 and the 11-16 manifest.

Method caveats the summary should state more plainly:
1. The owner brand is the brand of the **live** part name, held constant over the whole replay. Under the old importer any row with the same make prefix renamed the part, so the name is not a pre-write source.
2. **1,213 of the 3,240 VERIFIED_CORRECT (37%) are `consistent_not_independent`** (old importer: the writing row also named the part). 5 of my 10 sampled CORRECT rows are of this kind. They are consistent, not independently proven.
3. The repaired offers that nobody rewrote are **MISSING_EVIDENCE** in the new replay (my cross-tab of the two manifests by offer id, below). Their correctness rests on the repair journal and my payload re-derivation in item 1, not on the new replay.

   | Old VERIFIED_WRONG readiness | New class | Offers |
   |---|---|---|
   | READY | MISSING_EVIDENCE | 75 |
   | READY | VERIFIED_CORRECT | 18 (15 repaired-then-rewritten + 3 preserved) |
   | NOT_READY | VERIFIED_WRONG | 1,007 |
   | NOT_READY | VERIFIED_CORRECT | 59 |
   | NOT_READY | MISSING_EVIDENCE | 4 |
   | UNDO | VERIFIED_WRONG | 36 |

   Offers VERIFIED_WRONG in the new manifest that were not VERIFIED_WRONG in the old one: 0. These equal the summary's fates table.
4. VERIFIED_WRONG means "the offer's value was written by a different brand than the part's name". In all 15 sampled cases the payloads contain **no row of the owner brand at all**, and in 4 of them the foreign value was already in the 11-14 backup. My hypothesis from these 15 rows (not proven for the population): the offer belongs to the foreign brand's product and sits on the wrong part, so a price repair would be the wrong remedy. The population figure that supports checking this: 1,040 of the 1,043 still-wrong offers have a target that comes from a backup value only, 3 from an owner payload row.
5. 1,780 MISSING_EVIDENCE and 1,177 AMBIGUOUS exposed offers stay unclassified; 3,961 multi-brand bare offers have no payload row at all. 1,043 is a lower bound.

## Item 6 - post-fix period: 6a CONFIRMED (resolved holders), 6b NOT CONFIRMED (the 5 events)

My own test, independent of the replay scripts, over batches whose import ended at or after 2026-11-15 16:56:32 (620,414 payload rows over 87,283 articles at the first run, 06:01).

Test A - state: every Car-Parts.ie offer with `updated_at >= 16:56:32` (96,626 bare + 2,586 qualified at 06:01), matched to its writing payload row.

| Outcome (bare offers) | Count |
|---|---|
| writer brand == holder brand | 80,227 |
| writer brand resolved, holder brand resolved, different | **0** |
| candidates of two brands with identical price and URL (NGK / NTK 96854; NTK has its own `NTK~96854`) | 1 |
| **holder brand unresolved, foreign resolved writer** | **5** |
| no payload match | 16,393 |

Qualified offers: 967 matched and written by the brand in their key; 1 with two candidate rows (the same NGK / NTK pair); 1,618 unmatched. 0 qualified offers have a key token different from their part's brand.

The unmatched rows are explained: 17,073 Car-Parts.ie offers were stamped in 21 bulk statements between 00:49 and 01:15 on 11-16, with 0 `price_history` rows. Attribution (inferred from timing plus code, not from a log line): the `sync_prices` job ran 23:35:21-01:16:27 and calls `services/sendcloud_shipping_sync.py`, whose UPDATE sets `shipping_cost_ils`, `shipping_cost_usd`, `estimated_delivery_days` and `updated_at = NOW()` for offers with no shipping cost. That stamp hides the last importer write time, so Test A cannot speak for those offers.

Test B - price changes: every `price_history` row with source `car_parts_ie_import` on a Car-Parts.ie offer since 16:56:32 (20,074 at 06:01).

| Outcome | Count |
|---|---|
| writer brand == holder brand | 20,006 |
| writer and holder resolved and different | **0** |
| holder unresolved, foreign resolved writer | **3** |
| qualified offers | 15 |
| no payload match (batch still in flight at index time; 5 on re-run) | 50 |

So: **0 foreign-brand writes into a bare offer whose holder brand is resolved** - that part of the claim is confirmed by a test I designed myself. URL-only writes on the 17,073 shipping-stamped offers cannot be verified either way.

The 5 unresolved-holder events are real foreign-brand writes. I disagree with "not new proven cross-brand overwrites; stay AMBIGUOUS":

| Article | Part holding the offer | Row that wrote it after the fix | Effect |
|---|---|---|---|
| 35573 | `TOYOTA Yaris IV ... Coil spring SUPLEX 35573` | ORIGINAL IMPERIUM "Top strut mount", ford_tourneo-connect-2002, 20:12:35 | price 66.92 -> 67.06 |
| 35578 | `Toyota C-HR damping SUPLEX 35578` | ORIGINAL IMPERIUM "Engine mount", ford_transit-platform-chassis-e-1994, 20:48:49 | price 88.50 -> 89.26 |
| 10553 | `Ford Focus 4 Turnier Coil spring SUPLEX 10553` | METALCAUCHO "Steering rack gaiter", kia_pregio-box-tb, 11-16 03:08:14 | price 44.65 -> 44.54 |
| 34606 | `Switch Prs7 Lights` (sku JAG-P-34606, not a Car-Parts.ie part) | MAPCO "Dust cover kit, shock absorber", ford_mondeo-iii-saloon-b4y, 20:19:29 | same price 40.72, timestamp |
| 2611 | `CAR 2611 Brake Power Regulator NUOVA TECNODELTA` | NGK "spark plug", fiat_850-spider, 17:54:38 | same price 13.82, timestamp |

In each case the part is plainly another product. "Unresolved" only means `title_brand` returns None for these names (the text before the number is not a known brand, or there is no number in the name). What is true in the primary's note: the contamination is older (the 11-14 and 11-15 backups already hold the foreign URL, stamped 2026-11-01/02), so the fix did not create these. What is not true: that nothing wrong happened after the fix. The fixed importer kept writing a foreign brand's price into them, three times with a new price, and all five are available to customers. The foreign brand's own part (for example `FORD-35573` ORIGINAL IMPERIUM) exists and has no offer.

Mechanism: `cpie_identity.offer_key` returns the bare article when `bare_owner_brand is None and same_url`. Once a foreign listing URL is on such an offer, that brand's page passes `same_url` on every later harvest. See item 7.

Exposure: 6,361 of 795,108 bare Car-Parts.ie offers have an unresolved holder brand (06:0x read).

## Item 7 - importer identity protections: CONFIRMED WITH CORRECTION

- `/app/cpie_identity.py` sha256 72a8778a5cad891a...c9c4 in production and sandbox. Importer 0df98e74... identical in both.
- `docker exec -e PYTHONDONTWRITEBYTECODE=1 sandbox_backend sh -c 'cd /app && python3 -m pytest tests/test_cpie_identity.py tests/test_cpie_display_sku.py -q -p no:cacheprovider'` -> **9 passed**.

| Protection | Finding |
|---|---|
| Different brands sharing an article stay separate | Confirmed in code and tests (`part_sku` -> `MAKE-BRAND~article`, `offer_key` -> `BRAND~article`), and in production: 0 resolved cross-brand writes after the fix (item 6) |
| Unresolved **writer** brand fails closed | Confirmed (`if not brand: return None`; the importer counts `brand_unresolved` and skips) |
| Unresolved **holder** brand fails closed | **Only when the URL differs.** With the same URL the row writes the bare offer. `test_same_listing_url_keeps_the_bare_key_even_if_holder_brand_unresolved` asserts this behaviour |
| Listing-page same-URL does not prove identity | **True only for a resolved holder.** For an unresolved holder the same listing URL is still treated as proof, although the module's own docstring says a listing page shows several brands. This is the 59008Set mechanism left open for 6,361 offers, with 5 live instances |
| Supplier order text uses the bare article | Code and test confirmed (`supplier_ordering._bare_article`, `routes/utils.display_supplier_sku`, both via `cpie_identity.article_of`). **Not live** - see below |

The display / purchase-order fix (commit 6c36dfe) is BUILT and tested, not LIVE:
- uvicorn PID 1 started 2026-11-14 05:51:47; `supplier_ordering.py` and `routes/utils.py` were modified 2026-11-16 05:37; no restart since.
- Live proof at 06:1x: `GET /api/v1/parts/4b2b5b26-d083-4c85-b751-91c59c06c13f/suppliers`, sent without credentials from inside the container to the running backend, returned `"supplier_sku": "A-B-S~38203"`. I did not test it through nginx from outside.
- `maintenance/runtime_identity_check.py --pid 1` watches only `agents.owner_console`, so it cannot prove these modules either way.
- The supplier switches are off and `procurement_orders` is empty, so no purchase order has gone out with a qualified key. The customer-facing leak of the internal key is live for the 2,577 qualified offers until a guarded restart.

The importer and `cpie_identity` fix themselves are live: the importer is a fresh subprocess per batch and reads the files on disk.

## Item 8 - rollback evidence: CONFIRMED WITH CORRECTION

| Evidence per repaired row | Count |
|---|---|
| `before` + `after` in the result files | 90 / 90 |
| journal `intent` line equal to the result row | 90 / 90 |
| journal `committed` line for the same offer id | 90 / 90 |
| `pre_apply` full-row backup of the offer | 90 / 90 |
| `pre_apply` full-row backup of the part | 90 / 90 |
| `before` == `pre_apply` offer row (price, URL, timestamp, part, SKU) | 90 / 90 |
| `price_history_id` present in production | 90 / 90 |

My own full-row diff of the 90 offers against `pre_apply`: only `price_ils`, `supplier_url`, `updated_at` differ. Parts: `updated_at` (90), `min_price_ils` (57), `base_price` (2), `name` (2) - the last three by later scheduled or importer writes, not by the tool.

`rollback_rows` (read): refuses when part / SKU / supplier changed (`identity_changed`), when already at `before` (`already_rolled_back`), and when price, URL **or** `updated_at` differ from `after` (`newer_legitimate_write`), then repeats the check as a compare-and-swap in the UPDATE. It cannot overwrite a newer write.

Corrections:
- The tool on disk is dc224cba (commit 1caa787), not the 0deaa533 that ran. `git show 534b73e:backend/maintenance/cpie_overwrite_repair.py | sha256sum` reproduces 0deaa5333d1a8882..., and `rollback_rows` is byte-identical between the two versions.
- `docker exec sandbox_backend python3 /app/devtests/cpie_overwrite_repair_sandbox_test.py` final line: **`ALL 47 CHECKS PASSED`**. This exercised the current code, not the version that ran in production (reported then as 45/45).
- A rollback today would restore 74 rows and skip 16. Any writer that touches `updated_at` without changing price or URL (the `sync_prices` shipping sync does) also makes a row non-rollbackable; that fails in the safe direction but shrinks the rollback set over time.

## Item 9 - 36 UNDO and 1,070 NOT_READY: CONFIRMED WITH CORRECTION

Read at 05:57. Compared live price, URL, part, SKU and `updated_at` with the manifest's `current`.

| Group | Unchanged | Differ |
|---|---|---|
| UNDO (36) | 36 | 0 |
| NOT_READY (1,070) | 1,005 | 65 |

The 65: 0 identity changes, 0 stamped inside the repair window, 0 `cpie_overwrite_repair` history rows.
- 62 value changes, all by `car_parts_ie_import`: 61 matched in-window to an owner-brand payload row; 1 (4e3500a6, 6PK2345 GATES) was written by a GATES row at 22:32:18 and later re-stamped by the shipping sync.
- 3 timestamp-only (dd106b79, 77a1525d, 8bd1266b): the `sync_prices` shipping sync.

The primary agent reported 9 at execution time; the number grows as owner brands re-harvest. The claim that the repair did not touch them holds.

## Item 10 - residual defects and evidence gaps, by severity

**High**

1. **1,043 known wrong offers are still live and customer-visible.** All 1,043 VERIFIED_WRONG offers of the 11-16 manifest are available, priced, have a URL and sit on an active part. 734 differ in price from the backup target; for 225 of those the ratio is above 2x or below 0.5x. The 90 repaired offers were about 8% of the known damage, and 1,043 is a lower bound (3,961 multi-brand bare offers have no evidence). For many of them the right remedy is probably moving the offer, not changing its price (item 5, caveat 4).
2. **The same-URL rule is fail-open for unresolved holders.** 6,361 bare offers exposed; 5 observed foreign-brand writes after the fix, 3 with a price change. The closure summary's "0 foreign-brand writers" is true only for resolved holders.

**Medium**

3. **The display / purchase-order fix is not live.** The running backend returns `BRAND~article` on an endpoint that answered without credentials. Needs an owner-approved guarded restart.
4. **Evidence durability.** Payloads after 2026-11-15 17:26 exist only in `/tmp` of the backend container (5,053 files now); a container recreate loses them, and a re-harvest of the same vehicle overwrites its payload file. `core_closure_20261116/` is untracked in git and has no `SHA256SUMS`. The replay outputs exist only on the state volume.
5. **Missing offers for the foreign brands** (summary: 1,242 + 31 brand/article pairs with a part and no offer) are untouched.

**Low**

6. `base_price` differs from the policy target on about 54.5% of Car-Parts.ie parts; the importer keeps its own `x 1.45` `base_price` writer.
7. The repair left `min_price_ils` at the foreign price on 48 parts for about 11.5 hours; healed by the scheduled task, not reported.
8. 37% of VERIFIED_CORRECT are not independent; the 75 untouched repaired rows are MISSING_EVIDENCE in the new replay.
9. Four part rows stamped 18:43:25.118104 outside the 90: writer not identified.
10. Repair targets can be stale relative to the owner brand's current price (856bf4de: 230.76 vs 224.01).

**Could not verify**
- Who wrote the four stray part rows.
- URL-only importer writes on the 17,073 offers re-stamped by the shipping sync.
- Whether the owner brand named on a part is the brand that originally owned the offer (no evidence before 2026-11-14 03:00).
- The sandbox result for the exact tool version that ran in production.

## Plain statement

The repair of the 90 offers is verified: the 90 targets re-derive from the raw payloads, every row has that target value or a later owner-brand value, identity is intact, each has a journal, a backup and a guarded rollback path, and nothing outside the 90 was changed by the tool.

The closure is not verified as a closure. The fix stops cross-brand overwrites where the holder brand is known, and my own test confirms that. It does not stop them where the holder brand is unknown, one of the listed fixes is not running in production, and more than a thousand known wrong offers are still being shown to customers.

## Appendix - statements issued (all inside read-only sessions)

`$SUP` = `88f6ce24-ab5f-4f99-8b41-03cf6828cf3d`. Catalog DB unless marked PII.

Setup
```sql
SET default_transaction_read_only=on;  SET statement_timeout='300s';
select now();  show default_transaction_read_only;  show timezone;
select column_name,data_type from information_schema.columns where table_name=$1 order by ordinal_position;
select id,name,country,is_active from suppliers where id=$SUP;
```

Items 1, 2, 8, 9 (one snapshot, 05:57:18-05:58:50)
```sql
select sp.id::text id, sp.part_id::text part_id, sp.supplier_id::text supplier_id, sp.supplier_sku, sp.price_ils::text price_ils,
       sp.supplier_url, sp.updated_at, sp.created_at, sp.is_available, sp.part_type
from supplier_parts sp where sp.id=any($1::uuid[]);                       -- 93 + 1,106 + 3,858 offer ids
select id::text id, sku, name, manufacturer, part_type, base_price::text, importer_price_ils::text, online_price_ils::text,
       min_price_ils::text, max_price_ils::text, is_active, created_at, updated_at
from parts_catalog where id=any($1::uuid[]);
select id::text id, supplier_part_id::text sp, old_price_ils::text o, new_price_ils::text n, source, created_at
from price_history where supplier_part_id=any($1::uuid[]) and created_at>='2026-11-13' order by created_at;
select count(*) from (select part_id from supplier_parts where supplier_id=$SUP group by 1 having count(*)>1) x;
select count(*) from supplier_parts where supplier_id=$SUP;
select count(*) from supplier_parts where supplier_id=$SUP and supplier_sku like '%~%';
select source,count(*) n,min(created_at) mn,max(created_at) mx from price_history
where source ilike '%repair%' or source ilike '%cpie%' group by 1;
select id::text id, sku, name, updated_at, base_price::text bp from parts_catalog
where updated_at>='2026-11-15 18:43:17' and updated_at<'2026-11-15 18:43:27';
select id::text id, supplier_id::text s, supplier_sku, updated_at from supplier_parts
where updated_at>='2026-11-15 18:43:17' and updated_at<'2026-11-15 18:43:27';
select updated_at,count(*) n from parts_catalog
where updated_at>='2026-11-15 18:43:17' and updated_at<'2026-11-15 18:43:27' group by 1 having count(*)>1;
-- full-row diff vs pre_apply backups
select id::text id,to_jsonb(sp)::text j from supplier_parts sp where id=any($1::uuid[]);
select id::text id,to_jsonb(pc)::text j from parts_catalog pc where id=any($1::uuid[]);
```
The payload side (writer identification, the 90 target re-derivation) is file reading: every `/tmp/*_cpie.json` with its `_cpie_import.log`, article = `inferred_sku` or `sku`, price = `round(price_eur*3.48,2)`, URL = `product_url` or `source_url`, brand = `part_identity.title_brand(name, article)`.

Item 3 (PII DB)
```sql
select count(*) from cart_items where part_id=any($1::uuid[]);          -- same form for cart_items.supplier_part_id,
select count(*) from cart_items;                                         -- order_items.part_id / supplier_part_id,
                                                                         -- wishlist_items, part_reviews, part_price_watches (part_id)
select table_name,column_name,data_type from information_schema.columns
where table_schema='public' and (column_name ilike '%part%' or column_name ilike '%offer%' or column_name ilike '%sku%') order by 1,2;
select count(*) from order_items where part_sku like '%~%';
select distinct supplier_part_id from order_items where supplier_part_id is not null;
select count(*) from procurement_orders;  -- also procurement_events, supplier_payments, supplier_outreach
-- catalog: select count(*) from supplier_parts where id=any($1::uuid[]) and supplier_id=$SUP;   (the ordered offers)
```

Item 4 (`$2` = 3.21, the rate in the investigation file; `VISIBLE` = `price_policy.VISIBLE_OFFER_SQL`, `RANK` = `offer_classification.class_rank_sql("sp.part_type","pc.part_type")`)
```sql
SELECT pc.id::text AS id, pc.sku, pc.name, pc.base_price, pc.importer_price_ils, pc.max_price_ils, pc.online_price_ils, pc.min_price_ils,
       pc.updated_at, pc.created_at, bo.price_ils AS offer_ils, bo.price_usd AS offer_usd, bo.name AS offer_supplier,
       bo.country AS offer_country, bo.spid::text spid, bo.spua,
       EXISTS (SELECT 1 FROM supplier_parts sp JOIN suppliers s ON s.id = sp.supplier_id
               WHERE sp.part_id = pc.id AND VISIBLE AND COALESCE(sp.price_ils, sp.price_usd * $2) > 0) AS has_visible,
       (EXISTS (SELECT 1 FROM supplier_parts sp WHERE sp.part_id = pc.id AND sp.price_ils > 0 AND RANK <> 0)
        AND NOT EXISTS (SELECT 1 FROM supplier_parts sp WHERE sp.part_id = pc.id AND sp.price_ils > 0 AND RANK = 0)) AS other_class_only,
       (select count(*) from supplier_parts sp where sp.part_id=pc.id) n_offers
FROM parts_catalog pc
LEFT JOIN LATERAL (SELECT sp.price_ils, sp.price_usd, s.name, s.country, sp.id spid, sp.updated_at spua
                   FROM supplier_parts sp JOIN suppliers s ON s.id = sp.supplier_id
                   WHERE sp.part_id = pc.id AND VISIBLE AND COALESCE(sp.price_ils, sp.price_usd * $2) > 0 AND RANK = 0
                   ORDER BY RANK, COALESCE(sp.price_ils, sp.price_usd * $2) ASC LIMIT 1) bo ON TRUE
WHERE pc.is_active AND pc.id = ANY($1::uuid[]) ORDER BY pc.id;
-- sample ids:
select part_id from supplier_parts where supplier_id=$SUP order by md5(id::text||'consultant-20261116') limit 1000;
```
Target per row, in Python: `price_policy.unit_price(price_policy.offer_cost_ils(offer_ils, offer_usd, 3.21), offer_supplier, offer_country).net`, else `catalog_estimate_price(importer, max, online).net` when the part has no visible offer. "Drift" = stored `base_price` differs from that target by 0.006 or more; "legacy form" = `abs(base_price - round(importer_price_ils*1.45,2)) < 0.011`.

Item 5
```sql
select o.id::text id,o.part_id::text pid,o.supplier_sku sku,o.price_ils::text pr,o.supplier_url url,o.updated_at ua,o.created_at ca,
       p.name pname,p.sku psku,p.created_at pca,p.updated_at pua
from supplier_parts o join parts_catalog p on p.id=o.part_id where o.id=any($1::uuid[]);
select supplier_part_id::text sp,old_price_ils::text o,new_price_ils::text n,source,created_at
from price_history where supplier_part_id=any($1::uuid[]) order by created_at;
select id::text id,sku,name,oem_number art,created_at from parts_catalog
where oem_number=any($1::text[]) and specifications->>'source'='Car-Parts.ie';
```
Shell: `sha256sum` on the tarball, the two backup TSVs, the manifests and scripts; `sha256sum -c SHA256SUMS` in `forensics/` and `production_repair_execution/`; tarball members hashed against `/tmp/<basename>`.

Item 6
```sql
-- Test A
select o.id::text id,o.supplier_sku sku,o.price_ils::float pr,o.supplier_url url,o.updated_at ua,o.created_at ca,
       p.name pname,p.sku psku,p.id::text pid,p.created_at pca
from supplier_parts o join parts_catalog p on p.id=o.part_id
where o.supplier_id=$SUP and o.updated_at>='2026-11-15 16:56:32';
-- Test B
select h.id::text hid,h.old_price_ils::float o,h.new_price_ils::float n,h.source,h.created_at at,s.id::text id,s.supplier_sku sku,
       p.name pname,p.sku psku
from price_history h join supplier_parts s on s.id=h.supplier_part_id join parts_catalog p on p.id=s.part_id
where s.supplier_id=$SUP and h.created_at>='2026-11-15 16:56:32';
-- bulk stamps and their writer
select updated_at,count(*) n from supplier_parts where supplier_id=$SUP and updated_at>='2026-11-15 16:56:32'
group by 1 having count(*)>3 order by 1;
select job_name,status,started_at,completed_at,last_heartbeat_at from job_registry
where started_at>='2026-11-15 20:00' or completed_at>='2026-11-16 00:00' order by started_at;
select source,count(*) from price_history where supplier_part_id=any($1::uuid[])
  and created_at>='2026-11-16 00:45' and created_at<'2026-11-16 01:20' group by 1;
-- exposure
select o.supplier_sku sku,p.name pname from supplier_parts o join parts_catalog p on p.id=o.part_id
where o.supplier_id=$SUP and o.supplier_sku not like '%~%';               -- title_brand(pname, sku) is None -> 6,361
-- the 5 events
select o.id::text id,o.supplier_sku sku,o.price_ils::text pr,o.supplier_url url,o.updated_at ua,o.created_at ca,o.is_available av,
       p.id::text pid,p.name pname,p.sku psku,p.created_at pca,p.updated_at pua,p.base_price::text bp,p.category cat,
       p.specifications->>'source' src,p.specifications->>'product_url' purl
from supplier_parts o join parts_catalog p on p.id=o.part_id where o.supplier_id=$SUP and o.supplier_sku=any($2::text[]);
select old_price_ils::text o,new_price_ils::text n,source,created_at from price_history
where supplier_part_id=$1::uuid order by created_at desc limit 8;
select sku,name,created_at from parts_catalog where oem_number=$1 and specifications->>'source'='Car-Parts.ie';
select supplier_sku,price_ils::text pr,supplier_url from supplier_parts where supplier_id=$SUP and supplier_sku like '%~'||$2;
```

Item 10
```sql
select count(*) n, count(*) filter (where o.is_available and nullif(btrim(o.supplier_url),'') is not null
                                    and o.price_ils>0 and p.is_active) vis
from supplier_parts o join parts_catalog p on p.id=o.part_id where o.id=any($1::uuid[]);   -- the 1,043 VERIFIED_WRONG ids
select o.part_id::text pid,o.supplier_sku from supplier_parts o
where o.supplier_id=$SUP and o.supplier_sku like '%~%' and o.is_available limit 1;          -- part used for the live GET
```

Commands
```
docker exec -e PYTHONDONTWRITEBYTECODE=1 sandbox_backend sh -c 'cd /app && python3 -m pytest tests/test_cpie_identity.py tests/test_cpie_display_sku.py -q -p no:cacheprovider'   # 9 passed
docker exec -e PYTHONDONTWRITEBYTECODE=1 sandbox_backend python3 /app/devtests/cpie_overwrite_repair_sandbox_test.py                                                              # ALL 47 CHECKS PASSED
docker exec autospare_backend python3 /app/maintenance/runtime_identity_check.py --pid 1
git show 534b73e:backend/maintenance/cpie_overwrite_repair.py | sha256sum                                                                                                         # 0deaa5333d1a8882...
```

---

# Re-review 2026-11-16 (corrections)

Read at 15:20-15:23 UTC (17:20-17:23 IL). Same constraints: read-only sessions, SELECT only, sandbox writes only. Container scratch removed again.

| Item | Verdict |
|---|---|
| A. Fail-closed fix for 6b (commit ee18f6d) | **CONFIRMED** - live for the importer, no regression found, no cross-brand write into an unresolved holder since. Two corrections: go-live time, and `same_part` can never be true |
| B. base_price rebase of the 12 parts | **CONFIRMED WITH CORRECTION** - values, guards and rollback check out; three result files exist, not two; no approval reference is recorded in them |
| C. Effect on the earlier report | 6b and the fail-open row of item 7 are closed going forward. Overall verdict stays **PARTIALLY VERIFIED** |

## A. Fail-closed fix (ee18f6d)

**Diff.** `offer_key` gained `same_part`; with an unresolved holder brand it now returns the bare article only when `same_url and same_part`, otherwise `None` (skip, counted as `offer_owner_brand_unresolved`). The importer's `_own` query now also selects `sp.part_id` and passes `same_part = (holder part id == the row's part id)`. The resolved-holder branch and the unresolved-writer branch are untouched.

**Live state.**

| Check | Result |
|---|---|
| `/app/cpie_identity.py` sha256, container vs git working tree | cde55ab3... both |
| `/app/importers/car_parts_ie_import_generic.py`, container vs working tree | bfd0d006... both |
| `git status` for `backend/` | clean (only the pre-existing untracked `backend/state/`) |
| File mtime of both files in the container | 2026-11-16 06:26:58 UTC = **08:26:58 IL** |
| Copies of `cpie_identity` in the container (`find / -name 'cpie_identity*'`) | `/app/cpie_identity.py` and its `.pyc` only |
| `cpie_identity.__file__` when imported with the importer's path | `/app/cpie_identity.py`, signature has `same_part` |

Correction: the fix went live at 08:26:58 IL, not about 07:20 IL (07:26 is the CET time of the commit). I used the file mtime as the boundary.

The stale-module risk (old `cpie_identity` + new importer -> `TypeError` on every offer) cannot occur in production as deployed: there is one `cpie_identity.py`, the importer is a new process per batch, and both files changed in the same second. The only window was the 2 ms between the two file writes.

**Import logs, 8 hours before vs after the fix** (every `/tmp/*_cpie_import.log`, final JSON report parsed):

| | Before (1,112 batches) | After (976 batches) |
|---|---|---|
| Logs containing `Traceback` / `TypeError` | 0 / 0 | 0 / 0 |
| Batches with rows but 0 offer writes | 0 | 0 |
| Rows scanned | 439,738 | 383,897 |
| Offer rows written | 432,480 (98.3%) | 378,253 (98.5%) |
| `offer_owner_brand_unresolved` skips | 705 (0.160%) | 404 (0.105%) |
| `brand_unresolved` / `existing_row_brand_unresolved` holds | 5,334 / 1,219 | 4,231 / 1,009 |

No silent drop of offer writes, and no rise in skips.

**Tests.** `pytest tests/test_cpie_identity.py tests/test_cpie_display_sku.py -q` in sandbox: **9 passed** (the renamed test now asserts `None` for same-URL alone). `/tmp/imp_test2.py` with `/work/t/car_parts_ie_import_generic.py` (differs from the production file only in the DB host line):

```
A -> supplier_rows 1; offer 75660 = 34.8 on "MAPCO 75660 ..."
holder moved to unresolved part "Coil spring generic 75660"
B (ORIGINAL IMPERIUM, same listing URL) -> supplier_rows 0, supplier_skipped 1, offer_owner_brand_unresolved 1; offer still 34.8
```

I did not re-run the old-behaviour reproduction (34.8 -> 69.6); the old behaviour is proven by the 5 production events.

**Regression probes** (direct calls on the live module): same brand, resolved holder -> bare key with or without `same_part`; different brand, resolved holder, same URL, `same_part=True` -> `FAE~1`; no existing bare offer -> bare key; unresolved writer -> `None`. All unchanged.

**My own post-fix test** (offers and price changes after 06:26:58 UTC, matched to payload rows as before):

| | Result |
|---|---|
| Bare offers written and matched to a payload row | 64,363: 64,361 same brand, 2 with two candidate brands at identical value |
| Written with a resolved foreign brand | 0 |
| **Written while the holder brand is unresolved** | **0** |
| Importer price changes matched | 17,116, all same brand; 0 on an unresolved holder |

Limit: none of the five articles (2611, 35578, 34606, 35573, 10553) was re-harvested after the fix, so those exact offers have not been tested live. Their values and timestamps are unchanged since my first read, which means they still carry the foreign brand's price and URL.

**Challenges.**

1. `same_part` is unreachable. For the row's part to be the holder's part, the row must first pass `part_sku`, which requires the existing part's brand to equal the row's brand; the upsert then writes the row's name onto that part, and the `_own` query that follows reads that name - so the holder brand resolves. If the holder's name is unresolved, the row is held earlier (`existing_row_brand_unresolved`) and never reaches the offer code. Net behaviour: an unresolved holder is never written by the importer. That is safe; the parameter is dead code and the docstring promises an update path that does not exist. This is from reading the code, not from a test.
2. Staleness cost: nothing new is lost. For the same reason, the holder's own rows never reached these offers before the fix either; the only writes they received were foreign same-URL writes. The 6,361 unresolved-holder bare offers are now frozen at their current value, and they stay available to customers with a price that will age. The share of them that already holds foreign data is unknown.
3. Skip versus qualify: skipping is the correct minimum, since it cannot damage anything. It leaves the writing brand's product without an offer on every harvest. Writing `BRAND~article` on the writer's own part would also leave the holder untouched and would keep that brand's price flowing; the cost is a possible duplicate if the unresolved holder is in fact the same product. I would do that as a follow-up, not as part of this closure.
4. The fix does not repair the five contaminated offers, nor identify how many of the 6,361 are contaminated.

## B. base_price rebase

Files in `/app/state/cpie_ownership/overwrite_repair/`: there are **three**, not two.

| File | Changed | Unchanged | Skipped |
|---|---|---|---|
| `rebase_..._063635.json` | 11 | 0 | 1 (0b811425, `not_a_legacy_estimate_value`) |
| `rebase_..._063637.json` | 0 | 0 | 12 (all `not_a_legacy_estimate_value`) |
| `rebase_..._063651.json` | 1 (0b811425) | 11 | 0 |

The middle file is the idempotent second pass: with the legacy guard on, already-rebased parts are reported as skipped. The third file's 11 unchanged is the idempotent pass without that guard.

| Check | Result |
|---|---|
| `before` == `pre_apply` backup `base_price` | 12 / 12 |
| `after` == the policy target I computed independently at 06:11 UTC, before the rebase (item 4 table) | 12 / 12 |
| Live `base_price` == recorded `after` now | 11 / 12; aa3d6310 is 145.93 (recorded 147.39) after an owner re-harvest at 14:33 UTC moved its offer to 100.64 |
| All 14 listed parts == policy target now (recomputed at 15:23) | 14 / 14 |
| `before_equals_importer_x1_45` | true for 11; false for 0b811425 (178.70 x 1.45 = 259.115; stored 259.11) - as described |
| Columns of the 12 parts differing from the `pre_apply` backup | `base_price`, `updated_at` on all 12; `min_price_ils` on 8 and `name` on 1 (482dc008) |

The `min_price_ils` changes were already present in my 06:11 UTC diff, before the rebase, and the `name` change follows an importer write at 14:19 UTC. The rule's UPDATE sets only `base_price` and `updated_at` (`apply_base_price_rule`, read). So I attribute no other column to the rebase, but that is by timing and code, not by a diff taken immediately after it.

Code read: `rebase_parts` locks the part `FOR UPDATE NOWAIT`, refuses unless the current value is the backup value or a prior rebase's `after`, and applies the rule inside that transaction. `rollback_rebase` restores only where `base_price` still equals the recorded `after`; today it would restore 11 and skip aa3d6310.

Corrections and gaps:
- The rebase result files carry no `approval_ref` (the repair result files do). I cannot verify from the evidence that this production write was owner-approved.
- All three files are named `..._canary_...` although the 12 parts come from both phases.
- One of the other 76 repaired parts has since drifted: c380426f, `base_price` 230.35 vs target 224.65 (offer now 154.93). So 89 of the 90 match at 15:23.

Is the correction unnecessary or harmful? **Not harmful** - the values are right, the guards worked, and it is reversible. **Still unnecessary for closure**, and it does not hold: c380426f shows the same drift reappearing within hours, because the cause (no scheduled owner of `base_price`, plus the importer's own `x 1.45` write on a different make's part) is untouched, and about half of all Car-Parts.ie parts carry it.

## C. What this changes

| Earlier finding | Status now |
|---|---|
| 6b - the 5 events are real foreign-brand writes | Stands as history. The path is closed since 08:26:58 IL: 0 such writes in 64,363 matched offer writes |
| Item 7 - unresolved holder is fail-open with same URL | **Closed** (built, tested, live for the importer) |
| Item 7 - display / purchase-order fix not live | **Unchanged**: uvicorn PID 1 still started 2026-11-14 07:51 IL |
| Item 4 - 12 base_price rows | Corrected; 14 / 14 at target |
| High 1 - 1,043 known wrong offers live | Unchanged |
| High 2 - same-URL rule | Downgraded to: 5 known contaminated offers not repaired; 6,361 unresolved-holder offers frozen, contamination share unknown |
| Evidence durability, missing brand offers | Unchanged; `core_closure_20261116/` is still untracked with no `SHA256SUMS` |

**Overall verdict: PARTIALLY VERIFIED.** The repair of the 90 offers is verified, and the importer now stops cross-brand overwrites for both resolved and unresolved holders. It is not a closure of the identity problem: one fix is still not running in production, and the known wrong offers outside the 90 are still shown to customers.
