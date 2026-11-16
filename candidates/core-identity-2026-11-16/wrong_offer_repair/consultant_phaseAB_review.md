# Consultant review - Phase A/B wrong-offer dispositions (1,043 Car-Parts.ie bare-key offers)

Reviewer: independent read-only consultant. Date 2026-11-16 (live reads ~17:55-18:05 server time; DB timestamps are UTC, Israel = UTC+2).
Mode: every DB connection opened `SET default_transaction_read_only=on` first; catalog DB + PII DB read only; payloads read from
`/app/state/cpie_ownership/forensics/payloads_and_import_logs_2026111{5,6}.tar.gz` + `/tmp/*_cpie.json` (14,751 payload files
ingested). No DB write, no restart, no external fetch, no repo edit except this file. Scratch scripts/results:
`/tmp/claude-0/-opt-autosparefinder/0463056d-0823-4669-be4b-7e8313c87a56/scratchpad/c/` (main_run.py, aux_run.py, pc_run.py, out.json ...).

## Verdict (short)

| Disposition | Proposed | Mine (live) | Verdict |
|---|---|---|---|
| G VALID_CURRENT_STATE -> PASS-PRESERVED | 73 | **74** | AGREE on all 73; +1 (`625485ba-70eb-492e-9caf-bcaa15b40e95`, healed by the owner AFTER the S1 snapshot) |
| REPAIR (owner payload price) | 3 | **3** | AGREE on owner, price; DISAGREE with the label `D_WRONG_URL` (target URL == current URL for all 3) - see must-fix |
| H UNRESOLVED evidence gap | 967 | **966** (965 foreign-only + 1 owner/foreign tie) | AGREE that none is provable today; DISAGREE with the stated gap/route (wrong fetch target, self-heal overstated) |

Disagreements by id: **one** - `625485ba-70eb-492e-9caf-bcaa15b40e95` (art 53506, owner AIC) is now G: live price 211.55 / url
`.../peugeot/partner-origin-combispace-g/window-regulator` written 2026-11-16 16:52:57 UTC = AIC payload row
(peugeot__peugeot_partner-origin-combispace-g_cpie.json row 277, 60.79 EUR x 3.48 = 211.55), no foreign row equal; S1 had it at the
foreign value 200.66. The disposition set is time-dependent: the importer keeps rewriting these keys, so every apply must carry an
expected-current guard and a fresh re-classification.

## Method (independent re-derivation, not the S1-S4 outputs)

1. Live DB: `supplier_parts` x `parts_catalog` for the 1,043 ids (price_ils, supplier_url, updated_at, created_at, part name/sku/
   specifications/created_at), full `price_history` of the ids, every Car-Parts.ie offer whose key's article (bare, `BRAND~art`,
   legacy `Article №: art`) normalizes to one of the 1,043 articles, every `parts_catalog` row with the same normalized oem_number,
   other-supplier offers on those parts, `parts_catalog.online_price_ils/min/max/base`, `parts_images`, fitment counts.
2. Owner brand = `part_identity.title_brand(live part name, article)` (recomputed: **0 / 1,043** differ from the proposal).
3. Payload rows: every row of every payload file whose sku normalizes (A-Z0-9 only) to an article; brand = `title_brand(name, sku)`;
   ILS = `round(price_eur*3.48,2)` (importer line 278-279, `EUR_ILS_RATE` default 3.48, IE = 0% VAT). Row equals current iff
   ILS == price_ils AND url == supplier_url. Rows with an unresolvable brand tracked separately (`U`).
4. Classes: G = current equals >=1 owner row and no foreign / unknown-brand row; REPAIR = current equals a foreign row, owner rows
   exist with one price; H = current equals a foreign row and no owner row; tie = equals both.

Results (live): G 74 (all 74: matching owner row is also the LATEST owner row, single owner price, write time within 0-900 s after the
row's payload mtime, no unknown-brand row equal); REPAIR 3; H 965 foreign-only-no-owner-row + 1 tie (`5f78ad57-...`, NTK row 174 and
NGK row 168 of the same Opel Insignia file, both 77.10 EUR and the same URL; NGK/NTK are NOT aliased in
`backend/data/article_brand_aliases.json`, so this stays an unresolvable tie - correct). 9 articles have spacing/punctuation variants in
the payloads (`20 10 10` vs `201010`, `32.003` vs `32003`, `51.006`...): all of them belong to FOREIGN brands; **no owner row hides
behind a variant spelling**, so the exact-sku match of the proposal loses nothing.

## (2) The three REPAIR rows - challenged

| offer | art | owner (part) | current (foreign) | owner payload rows | target |
|---|---|---|---|---|---|
| 35d2d2e7-e7de-4d7e-afb1-a2320162ba2f | PT155 | JAPANPARTS ("Toyota Land Cruiser J7 Pickup Glow plug JAPANPARTS PT155") | 54.91 = JAPKO 15.78 EUR (daihatsu wildcat row 347) | 5 rows, 5 listings, all 43.01 EUR | 149.67 |
| b1b5e079-635b-4ce8-8d74-0779806c33dd | `ZR CT728 R` | JUMASA ("JUMASA ZR CT728 R Citroën C3 Pluriel window regulator") | 242.14 = ELECTRIC LIFE 69.58 EUR (row 303) | **1 row** (row 299), 168.32 EUR | 585.75 |
| 10d88c3d-b1e5-4543-8d84-9b897616f297 | `ZR PG57 L` | ELECTRIC LIFE ("... PEUGEOT 108 Hatchback window regulator") | 635.48 = JUMASA 182.61 EUR (citroen c1-ii row 303) | 3 rows, 3 listings, all 76.73 EUR | 267.02 |

- Owner identification: correct. `title_brand` of the part name and of the owner payload rows agree (verified in-container:
  JUMASA / ELECTRIC LIFE / JAPANPARTS). Article match is exact including spaces (`ZR CT728 R` == payload sku `ZR CT728 R`, no
  L/R or spacing variant exists in any payload). Creator evidence agrees too: the payload image id of the owner row equals
  `specifications.image_url` of the part (insert-only) for all 3 (JUMASA 14636794 etc.), the offer was created in the same transaction
  as the part, and the part is not a renamed other-brand part.
- Target price proven: owner rows are internally consistent (one EUR price each); 43.01x3.48=149.67, 168.32x3.48=585.75, 76.73x3.48=267.02.
  In every source file the owner row precedes the foreign row (344<347, 299<303, 296<303) and `price_history` shows the flip-flop of a
  last-writer-wins batch (PT155: 149.67->54.91->149.67->54.91... five rows on 11-14/11-15) - consistent with the buggy-window cause.
- Variant risk: low for PT155 / ZR PG57 L (several listings, same price). `ZR CT728 R` rests on ONE owner observation and the article
  format is ELECTRIC LIFE's own numbering - JUMASA lists the same code (re-boxed product). Per the alias rule (re-boxed != alias) JUMASA
  and ELECTRIC LIFE stay distinct, so JUMASA's own price is the right one for the JUMASA part; flag the single observation in the record.
- URL: **target URL == current URL for all 3** (daihatsu/wildcat-rocky-f70/ignition-system-auto, citroen/c3-pluriel-hb/window-regulator,
  citroen/c1-ii/window-regulator). Only price_ils changes. The label `C_WRONG_PRICE+D_WRONG_URL` is wrong (the URL is a multi-brand
  listing page that is correct for both brands) - relabel `C_WRONG_PRICE`, and the UPDATE must not claim a URL repair.
- Customer references: none. PII DB: `cart_items` (0 rows total), `wishlist_items` (0), `part_price_watches` (0), `part_reviews` (0),
  `order_items` (154 rows) - **0 references** by supplier_part_id or part_id for ALL 1,043 offers / parts (not only the 3).
  Catalog side: `price_history` only. Exposure: all 3 parts active, offers available, no other supplier on these parts.
- Side effects an UPDATE must handle (as the 90-offer repair did): a `price_history` row; `parts_catalog.min_price_ils/max_price_ils`
  via `db_update_agent.refresh_min_max_for_parts`; Meilisearch re-index (ASSUMED: `meili_sync` keys on `parts_catalog.updated_at`, so a
  `supplier_parts`-only UPDATE may not be re-indexed unless the part row is touched - check before apply). Guard on expected current values
  (price, url, updated_at) because the importer is live and the fixed importer will itself rewrite these keys at the next owner
  re-harvest (all 3 owners appear on regularly harvested listings).

## (3) The 73 PRESERVED - live check

All 73 proposed G rows still hold a value equal to an owner payload row and to no foreign/unknown-brand row (live read); for each one the
matching owner row is the newest owner row and its payload mtime precedes the offer's updated_at by 0-900 s. `5444fe57-e647-4738-beef-9d2491ca3444`
changed since S1 but is still owner-only. Plus 1 new G (above). Caveats: (a) 3 G parts were created from a THIRD brand's product page
(listed below) - price/URL now match the part NAME, but the part row itself is mixed identity (fitment/images of another brand);
(b) the G set is a moving target - re-run the check at apply time.

## (4) Can any of the 966 H rows be proven? - **0 movable to REPAIR with proof of the CURRENT owner price**

Sources tried and their measured reliability (measured on the 74 G + 3 REPAIR rows, where the true owner price is known):

| candidate source | coverage in H | reliability vs true owner price |
|---|---|---|
| 11-14 backup value (as proposed) | 966 | 42/74 G (57%); in **339 H** the 11-14 value itself equals a non-owner payload price |
| 11-14 backup value whose category slug is absent from every non-owner payload row | 130 | 40/58 G (69%) - not proof |
| `parts_catalog.online_price_ils` (insert-only in the CURRENT importer = CREATOR row's price - ASSUMED for past importer versions; mostly at the old 3.90 rate, eur = onl/3.90) | 958 | creator proven = owner: 12/56 (21%) equal today's owner price after re-rating to 3.48 |
| legacy `Article №: art` offers on an owner-named part | 371 | 0/9 G |
| `OWNER~art` qualified offers / other suppliers / other parts with the same article | 0 / 0 / 0 useful | - |

Two stronger-than-proposed findings, neither is current-price proof:
- **102 H rows: the pre-defect (11-14) value carried the OWNER brand's own product-page URL** (`/car-parts/<owner-slug>/p-<id>`, a
  single-product page, so ownership of that value is proven by URL, not by a shared listing). In 96 of them it is the creation-time
  value (url == `specifications.product_url`, price == online_price_ils), i.e. last refreshed June-September; given 21% creation-to-today
  agreement it is NOT a valid current target. Use: owner-decision class "last proven owner value (dated)", not REPAIR. List in Appendix A.
- **794 H owner parts were CREATED from the owner's own product page** (`specifications->>'product_url'` = `/car-parts/<owner>/p-<id>`).
  VERIFIED: 0 owner rows for these articles in the 14,751 retained payload files (the listing harvester did not visit those pages in
  the retained window; ASSUMED that it never will). Consequences:
  (i) the proposal's S3 fetched the WRONG page - `s3_live_fetch.py` fetches the 11-14 snapshot URL, which for 857 H is a multi-brand
  listing and for 339 already shows the foreign value; the page that can prove the owner price is the owner p-page in
  `specifications.product_url` (ASSUMED that it needs a product-page parser - `parse_parts` was written for listing blocks, not
  tested on a p-page here); (ii) the `self_heal` statement
  ("owner re-harvest rewrites the bare key, observed for 73") does not transfer: the 74 healed were 55 listing-created + 19 p-page-created
  owners that also appear on listings; for the 794 there is no observed harvest path that will heal them.

Reproduce: `SELECT sp.id FROM supplier_parts sp JOIN parts_catalog p ON p.id=sp.part_id WHERE sp.id = ANY(<H ids>) AND
p.specifications->>'product_url' ~ '/car-parts/[^/]+/p-[0-9]+'` then compare the slug with the owner brand (slugified).

## (5) WRONG_PART check

By construction all 966 H offers carry another brand's product (price + listing) on the owner part; what matters is whether the
OFFER/PART pair itself is mis-attributed. Creator evidence (insert-only `specifications.product_url` slug, `specifications.image_url`
id matched to payload image ids, `specifications.part_brand`, offer.created_at vs part.created_at; `ON CONFLICT (sku) DO UPDATE SET
name=EXCLUDED.name` re-named legacy-sku parts by ANY brand before the part_sku fix, while `supplier_parts` upsert never changes
part_id):

Slug check (VERIFIED, read-only, up to 3,000 Car-Parts.ie parts per slug, brand = `title_brand(name, oem_number)` over all parts whose
`specifications->>'product_url'` is `/car-parts/<slug>/p-*`): `hd-karosserieteile` -> DIEDERICHS 3000/3000, `bougicord` -> EFI AUTOMOTIVE
282/282, `kn-filters` -> K&N 103/103 (slug = the brand's own page, NOT a third brand); `fispa` -> SIDAT 291 / FISPA 210, `triscan` ->
TRISCAN 2285 / KAWE 140, `hoffer` -> HOFFER 1148 / MEAT & DORIA 148 (shared brand pages: creator brand AMBIGUOUS); `abs` A.B.S. 2779/2803,
`3rg` 3RG 1325/1346, `bga` BGA 2978/3000, `carcommerce` 164/167, `japko` 2254/2279, `lpr` 901/944, `metalcaucho` 2154/2202, `febi-bilstein`
2972/3000, `nrf` 2954/3000, `fai-autoparts` 2448/2452 (dominant brand = slug; a different name on such a page = rename). `geba` has only 3
parts (weak).

| H (966) | count | evidence tier |
|---|---|---|
| creator = owner -> WRONG_PRICE(+listing URL) on the right part | 797 | owner product-page slug (VERIFIED by the slug check) |
| creator = owner, by `specifications.part_brand` only | 112 | ASSUMED (part_brand is the importer's first-word guess, `part_identity.row_brand` docstring) |
| creator brand unknown (vehicle-first titles, no image) | 33 | - |
| creator AMBIGUOUS: created from a shared brand page (3 SIDAT on fispa, 1 KAWE on triscan, 1 MEAT & DORIA on hoffer) | 5 | slug check |
| **part renamed after creation: creator = the CURRENT foreign writer** (offer content matches the part's ORIGIN, the part NAME is the other brand) | **8** | 3 image id (VERIFIED: 7b3b7b18, 5f78ad57, 00eb6949), 3 dominant-slug p-page (91eddb49, 490c7621, 634fd760), 2 part_brand-only ASSUMED (be57d22c, 68ad8ca5) |
| part created from a THIRD brand's product page (mixed identity) | 7 | dominant-slug p-page (geba weak) |
| **offer older than its part / part has no created_at** (offer relinked onto this part) | 4 | timestamps (2b4daa20, 67a2afca, a8d0cbe5, 5969032b) |

(Two rows first counted as "not created with the part" - 48b1f53a, 0f46dade - have the offer ~10 s AFTER the part: same-row processing
(part upsert -> fitment loop -> offer insert), not a relink; they are in the owner-created rows.)

So **19 H rows** are WRONG_PART / mixed-identity cases (8 renamed + 7 third brand + 4 relinked; +5 ambiguous) where an UPDATE of
price_ils/supplier_url toward the part-name brand would be the wrong repair (and 3 more among G). Examples: `7b3b7b18` 59008Set - part
created by AIC (image 16567485 = AIC payload image, part_brand AIC), later renamed "iwis Motorsysteme 59008Set", offer = AIC price;
`00eb6949` ZR CT724 L - created by JUMASA (image 14636787), renamed ELECTRIC LIFE; `d1db1658` 8540 10403 - created from a triscan
product page that also hosts KAWE products (ambiguous; TRISCAN/KAWE stay distinct); `5969032b` 6530 and `a8d0cbe5` 908836 - offer
created 2026-06-24, part created later / no created_at (offer relinked onto this part).
Foreign brand also holds its own `BRAND~art` offer: 579 of 966 H (there the bare offer is a duplicate of the foreign offer on the
wrong part); 387 without (the bare key is the only place that foreign price lives).
Category mismatch is the norm: e.g. OSSCA 00251 "Brake pedal pad" carries METALCAUCHO's engine-mount listing, AMiO 01105 "Portable car
cooling fan" carries FEBI's engine mount, KAMOKA 6607 cv joint carries NGK's spark plug (33.93 vs owner 211.51 at creation 11-13).


## (6) Mission-rule check of the proposal

- Article/sku/price/description alone as identity: NOT violated. Every owner/foreign decision uses brand (`title_brand`) + article, and
  G/REPAIR additionally require an exact price+URL payload row. Caveat: the 11-14 backup target (already rejected) and any future
  "category slug" heuristic would be description-only evidence - keep them out of REPAIR.
- Only SAME merges: no merge/deactivate/relink is proposed - OK. But the 19 WRONG_PART + 5 ambiguous H rows (+3 G) must not be "repaired" by a
  price UPDATE toward the part-name brand without a part-identity decision (`part_identity.classify`), because their part row is
  itself a cross-brand rename.
- ferdinand bilstein / febi bilstein: preserved (`title_brand('Front FERDINAND BILSTEIN 116361 ...')` -> FEBI BILSTEIN in-container; no
  row in the 1,043 is split by that alias).
- SIDAT/FISPA, TRISCAN/KAWE: kept distinct by the proposal (no alias) - OK. Data note: 3 SIDAT parts and 1 KAWE part were CREATED from
  fispa / triscan product pages, which host both brands (fispa: SIDAT 291 / FISPA 210; triscan: TRISCAN 2285 / KAWE 140) - creator
  ambiguous, so do not treat SIDAT/KAWE as proven owner of the pre-defect value there.
- METZGER/QUICK BRAKE, COFLE/METZGER: no row of the 1,043 has these as owner/foreign pair - nothing to violate. NGK/NTK (4 rows) is not
  aliased; the proposal correctly treats it as distinct (and leaves the tie unresolved).
- Code fragility (not a wrong result today): `s4_build_manifest.py` labels every changed row that is not owner-only as the "owner AND
  foreign tie" with that reason text; a changed row equal to neither / only foreign would get a false reason. Today 1 such row, and it
  is a genuine tie.

## Must-fix list

1. Move `625485ba-70eb-492e-9caf-bcaa15b40e95` from H to G (PASS-PRESERVED); re-run the classification immediately before any apply
   (expected-current guard on price_ils + supplier_url + updated_at; skip-and-report on mismatch).
2. Relabel the 3 REPAIR rows `C_WRONG_PRICE` (URL unchanged: target URL == current URL); UPDATE only `price_ils` (+ updated_at), write
   the `price_history` row, refresh min/max through `refresh_min_max_for_parts`; record that `b1b5e079` rests on one owner observation.
3. Replace the H gap statement: the owner page to fetch is `parts_catalog.specifications->>'product_url'` (owner product page) for 794
   rows, not the 11-14 listing URL; and drop/qualify the self-heal claim (no harvest path revisits those owner pages).
4. Split H: 797 WRONG_PRICE-on-right-part (owner-slug evidence) / 112 same but ASSUMED (part_brand only) / 33 creator-unknown /
   5 creator-ambiguous / **19 WRONG_PART-or-mixed-identity** (Appendix B) - the last two groups need a part-level decision
   (`part_identity.classify`), never an owner-price UPDATE; also mark the 3 mixed-identity G rows.
5. Record the 102 "last proven owner value (dated)" rows (Appendix A) as an owner-decision option (e.g. restore dated owner value vs.
   hide the offer), explicitly NOT as proven current prices (creation-to-today agreement 21%).
6. Exposure stays: all 966 H offers are `is_available` on active parts (customer-visible wrong prices); 0 customer references exist today,
   so hiding/withholding them carries no cart/order/watch risk.

## Overall

AGREE with 73/73 G, 3/3 REPAIR (owner + price; URL label wrong), and that the 966 remaining H rows cannot be repaired with proof today.
DISAGREE on 1 id (H->G, time race), on the REPAIR label, on the S3 fetch target / self-heal rationale, and on treating H as one class
(19 WRONG_PART / mixed-identity rows + 5 creator-ambiguous). Status: PARTIAL (dispositions sound; evidence route and sub-classification need the fixes above).

## Appendix B - WRONG_PART / mixed-identity lists

### Part renamed after creation (creator = current foreign writer) - 8
- `be57d22c-2643-4a3a-8aa9-8961e09841bc` art `190972` part-name brand SPIDAN / creator LÖBRO (part_brand, ASSUMED) / current writer LÖBRO
- `68ad8ca5-18f0-4ca1-a752-5f6bfddd57c8` art `30214` part-name brand AS / creator 3RG (part_brand, ASSUMED) / current writer 3RG
- `91eddb49-47a4-46d7-a06a-41c492a179a7` art `42022` part-name brand MONROE / creator CARCOMMERCE (p-page) / current writer CARCOMMERCE
- `7b3b7b18-86fe-4a7f-b94a-744b2118255b` art `59008Set` part-name brand IWIS MOTORSYSTEME / creator AIC (image) / current writer AIC
- `490c7621-fe53-4ca6-9a32-57c1aea478b3` art `6PK1698` part-name brand DAYCO / creator BGA (p-page) / current writer BGA
- `5f78ad57-bd05-4462-b5a0-65ae1d3ea0c3` art `93822` part-name brand NTK / creator NGK (image) / current writer NGK
- `634fd760-e918-4cb6-96af-f3059e9407e1` art `PT154` part-name brand ASHIKA / creator JAPKO (p-page) / current writer JAPKO
- `00eb6949-6181-45ac-93b6-ddf98d28d595` art `ZR CT724 L` part-name brand ELECTRIC LIFE / creator JUMASA (image) / current writer JUMASA

### Creator = a third brand (dominant-slug product page) - 7
- `39fa4dd6-32b9-492e-85c1-e2a4ebd04c45` art `10915` part-name brand MEAT & DORIA / creator GEBA (p-page) / current writer 3RG
- `98d9bfeb-9b7c-4a2d-a4ec-50d0c8e0b4d4` art `16102` part-name brand FAE / creator other:3rg (p-page) / current writer A.B.S.
- `ccf7fe51-d9aa-4474-a61f-12ec51e3e7d2` art `1952` part-name brand NGK / creator other:lpr (p-page) / current writer NTK
- `cc8b12fc-6775-4c5e-9a7f-09f0cad7acbf` art `21001` part-name brand MPM / creator other:metalcaucho (p-page) / current writer MAPCO
- `4e853c14-df1e-4e35-a150-0adf68ed6750` art `26211` part-name brand SPIDAN / creator other:febi-bilstein (p-page) / current writer MAPCO
- `bca214bc-f4ef-4a8b-bcf1-db698316251a` art `31831` part-name brand FA KROSNO / creator other:abs (p-page) / current writer FEBI BILSTEIN
- `f076ff1c-0788-4c6c-b8a9-2ead9dda36d9` art `6PK1217` part-name brand DENCKERMANN / creator other:bga (p-page) / current writer CONTITECH

### Offer older than its part / part without created_at (relinked) - 4
- `2b4daa20-2ce6-4dcf-b62a-3457b1f4ef8b` art `03212` part-name brand AMIO / creator None (None) / current writer FEBI BILSTEIN
- `67a2afca-51eb-4aea-b5be-30f8386a6af6` art `49066` part-name brand NGK / creator NGK (p-page) / current writer FEBI BILSTEIN
- `5969032b-2d95-4d71-9206-236b8f07358b` art `6530` part-name brand KAMOKA / creator None (None) / current writer MAPCO
- `a8d0cbe5-c6d5-48f8-af38-88f0487d6553` art `908836` part-name brand AMC / creator None (None) / current writer VALVOLINE

### Creator ambiguous (shared brand page) - 5
- `d1db1658-c575-48a8-8346-e8f91ec1e74d` art `8540 10403` part-name brand KAWE / creator TRISCAN (p-page) / current writer TRISCAN
- `98a11bad-f3bc-4200-ae27-c828c6bfafc3` art `63124` part-name brand MEAT & DORIA / creator other:hoffer (p-page) / current writer NISSENS
- `ce67bacd-6861-4b21-ba32-232930895859` art `71226` part-name brand SIDAT / creator other:fispa (p-page) / current writer AS
- `232a8082-b615-4e7c-91dd-44a321ecaf0b` art `71280` part-name brand SIDAT / creator other:fispa (p-page) / current writer AS
- `9b61d40e-054f-4e20-a3a2-78fad0e51e5c` art `71462` part-name brand SIDAT / creator other:fispa (p-page) / current writer AIC

### G rows whose part was created from a third brand's page - 3
- `a712aeeb-74c8-44fc-b1fc-f998283d94a7` art `36153` part-name brand ORIGINAL IMPERIUM / creator other:nrf (p-page) / current writer 
- `a3610290-0330-41f0-84b6-0f8fa031f7da` art `49215` part-name brand SIDEM / creator FEBI BILSTEIN (p-page) / current writer 
- `4ce50507-7a92-422e-ba78-c967a73e482e` art `TCK58` part-name brand MOTIVE / creator FAI AUTOPARTS (p-page) / current writer 


## Appendix A - 102 H rows whose 11-14 value carried the owner's own product-page URL (dated owner value, NOT current proof)

```
46680f72-2d75-4886-a2c4-97c127d7a6cc 02146 METALCAUCHO snap14=44.62 https://www.car-parts.ie/car-parts/metalcaucho/p-14124244 cur=83.1
18855607-7fd7-465c-b5c0-cb70c60cfc8e 04987 FEBI BILSTEIN snap14=204.79 https://www.car-parts.ie/car-parts/febi-bilstein/p-1868415 cur=86.1
63b73a00-240e-4c48-9e9d-5a8d6bf2f608 104056 KAMOKA snap14=75.54 https://www.car-parts.ie/car-parts/kamoka/p-8794076 cur=141.57
b900bb79-57de-4285-95e7-bed63df75e08 11893 BREMI snap14=93.44 https://www.car-parts.ie/car-parts/bremi/p-1344539 cur=88.15
02d02f13-34fd-47a4-a1c5-0dea9ff1c877 11900112 ALANKO snap14=1458.37 https://www.car-parts.ie/car-parts/alanko/p-13038129 cur=706.68
d056d571-f448-436f-961b-0b006200ea8e 13546 SERCORE snap14=1103.74 https://www.car-parts.ie/car-parts/sercore/p-8965588 cur=499.17
a5e292d8-afbc-4424-8cb2-3087733c9111 15022 HORTUM snap14=44.77 https://www.car-parts.ie/car-parts/hortum/p-20458777 cur=25.86
635f8f96-150b-4f8c-b88c-42f312c883e8 15042 FAE snap14=111.11 https://www.car-parts.ie/car-parts/fae/p-2494957 cur=161.79
fe3bbf0b-619d-468c-9abe-012971154ac3 16340 MAPCO snap14=287.74 https://www.car-parts.ie/car-parts/mapco/p-7593436 cur=167.35
95795306-87bd-43dd-b75c-a5024a7a40c8 185098 QUICK BRAKE snap14=48.79 https://www.car-parts.ie/car-parts/quick-brake/p-16540038 cur=180.96
d4e2da47-6553-4864-9387-358e8f91fb9b 19064 KAMOKA snap14=200.5 https://www.car-parts.ie/car-parts/kamoka/p-9139903 cur=56.1
428840e1-418c-4109-9326-cc78aae0bacc 20103 MEAT & DORIA snap14=51.29 https://www.car-parts.ie/car-parts/meat-doria/p-7757424 cur=252.23
dc6842bb-6b48-43ce-ba19-24d18e35f559 22710 3RG snap14=45.24 https://www.car-parts.ie/car-parts/3rg/p-15371521 cur=57.45
bd0dbff2-13f3-4357-bf4c-0530e68fa1ad 230344 ORIGINAL IMPERIUM snap14=138.14 https://www.car-parts.ie/car-parts/original-imperium/p-20311763 cur=35.08
e3956ef4-6623-4d79-b1a5-32e907936839 24632 3RG snap14=41.92 https://www.car-parts.ie/car-parts/3rg/p-14957010 cur=51.4
0accc124-88d3-4455-aec3-42b8b3eb813f 25072 AS snap14=1198.04 https://www.car-parts.ie/car-parts/as/p-9380261 cur=96.57
223a9e2c-0caa-4d49-949a-4cf7be9ef29f 25216 KAMOKA snap14=42.16 https://www.car-parts.ie/car-parts/kamoka/p-23144068 cur=26.13
07f63ecd-4ddb-4c8c-8e1f-63086ca7a6a4 26263 ORIGINAL IMPERIUM snap14=10.45 https://www.car-parts.ie/car-parts/original-imperium/p-9401720 cur=83.52
1ee87761-988e-465c-970e-d38da38b59cd 26729 MEAT & DORIA snap14=66.26 https://www.car-parts.ie/car-parts/meat-doria/p-19930715 cur=101.09
4f5c24d1-e08c-4177-849d-7da803b41283 27002 FARE SA snap14=213.02 https://www.car-parts.ie/car-parts/fare-sa/p-18158079 cur=167.49
9a5f3b39-d60c-4626-a2cf-8cf35a3290d3 28234 MAPCO snap14=107.52 https://www.car-parts.ie/car-parts/mapco/p-18754640 cur=80.18
1272980d-3137-452e-bc35-450418b8fb7b 30028 FEBI BILSTEIN snap14=17.08 https://www.car-parts.ie/car-parts/febi-bilstein/p-1886231 cur=220.04
52aa7f1d-fbee-4a3c-888c-ff6d67797d34 30179 FEBI BILSTEIN snap14=188.33 https://www.car-parts.ie/car-parts/febi-bilstein/p-7010591 cur=101.65
3dd579c1-2135-4e74-b6ea-d7e27823d8e8 30244 FEBI BILSTEIN snap14=42.63 https://www.car-parts.ie/car-parts/febi-bilstein/p-1886421 cur=79.9
defd835c-1b0c-493e-a741-7d7903019478 30336 FARE SA snap14=464.88 https://www.car-parts.ie/car-parts/fare-sa/p-18161401 cur=47.08
fe3c006c-a31a-4f12-a165-31463e83378b 30669 FEBI BILSTEIN snap14=275.34 https://www.car-parts.ie/car-parts/febi-bilstein/p-1886813 cur=96.64
13e7dcb1-eecf-4a35-954b-fef683a8913b 31043 MEAT & DORIA snap14=567.92 https://www.car-parts.ie/car-parts/meat-doria/p-13661728 cur=107.98
679e5a07-6b8e-49d3-b623-9b14714f76e4 3110038 KAMOKA snap14=141.3 https://www.car-parts.ie/car-parts/kamoka/p-23144301 cur=423.93
2930f922-d185-41e7-9158-9b322a2724a7 31324 NRF snap14=219.61 https://www.car-parts.ie/car-parts/nrf/p-9532064 cur=25.02
9ef676d5-269e-4b70-b510-2a0103750468 31835 NRF snap14=162.08 https://www.car-parts.ie/car-parts/nrf/p-16633384 cur=69.29
3aee0d35-0323-481e-9903-50cff9919efe 32021 NRF snap14=575.84 https://www.car-parts.ie/car-parts/nrf/p-2387731 cur=186.77
9e29550e-c4d7-4670-8349-1ce25ddc3542 32831 NRF snap14=917.75 https://www.car-parts.ie/car-parts/nrf/p-16633475 cur=43.64
4a381b9d-2682-4c98-ab62-d041a57c31bb 33993 FEBI BILSTEIN snap14=31.75 https://www.car-parts.ie/car-parts/febi-bilstein/p-1889683 cur=50.74
892535a7-72b3-4bb8-8efa-de24fd0e8f5c 34515 MAPCO snap14=57.17 https://www.car-parts.ie/car-parts/mapco/p-14927319 cur=116.61
110338ec-ec5e-4fdc-8310-eb83ea895720 35006 BREMI snap14=87.17 https://www.car-parts.ie/car-parts/bremi/p-20839991 cur=118.08
6acc7237-2472-4795-b20d-b18da70b4027 35496 NRF snap14=169.96 https://www.car-parts.ie/car-parts/nrf/p-2388834 cur=159.59
ab1e49b4-05eb-4d81-befe-4c01929d5fca 36067 MEAT & DORIA snap14=60.33 https://www.car-parts.ie/car-parts/meat-doria/p-8127580 cur=36.61
6ae2a9c7-9bcc-4675-bf69-616e3b4a5eb9 36166 NRF snap14=305.41 https://www.car-parts.ie/car-parts/nrf/p-15503238 cur=46.39
ef2cd3d5-f66d-426c-bcbd-a14979371e43 36171 NRF snap14=313.52 https://www.car-parts.ie/car-parts/nrf/p-17415697 cur=54.6
6dcf4f2a-1f76-4952-b904-e89b1e6c13da 36630 FEBI BILSTEIN snap14=421.0 https://www.car-parts.ie/car-parts/febi-bilstein/p-7004011 cur=70.68
7c1a3860-e0fa-421f-8aff-2a5336d88893 37106 MAPCO snap14=16.93 https://www.car-parts.ie/car-parts/mapco/p-7932086 cur=133.7
1b2428a8-36a2-4e2e-9dae-b7b825a9d433 37330 FEBI BILSTEIN snap14=17.32 https://www.car-parts.ie/car-parts/febi-bilstein/p-7618020 cur=63.79
220e860c-c366-4ba0-b9ee-9dc5ba0bf29d 37898 MAPCO snap14=17.39 https://www.car-parts.ie/car-parts/mapco/p-7593182 cur=105.3
7b55e86c-ab5b-474c-88dc-43ab72198acf 38513 NRF snap14=73.55 https://www.car-parts.ie/car-parts/nrf/p-17882330 cur=99.42
159df441-b7e3-4a97-9367-8191fded308b 38610 NRF snap14=199.52 https://www.car-parts.ie/car-parts/nrf/p-20485359 cur=159.14
ce962b23-9599-4463-810d-a974e469f4a3 39650 MAPCO snap14=32.14 https://www.car-parts.ie/car-parts/mapco/p-18755738 cur=80.6
4aefba2d-0d5c-48ba-8a53-8ca2035b1d3d 39785 FEBI BILSTEIN snap14=37.44 https://www.car-parts.ie/car-parts/febi-bilstein/p-7556272 cur=91.77
9fc18173-6c6b-463d-9f30-9b69d104dfab 40913 FEBI BILSTEIN snap14=102.3 https://www.car-parts.ie/car-parts/febi-bilstein/p-7556079 cur=81.92
005fd971-c8b7-4819-aad9-b39e80576912 410007 SPIDAN CHASSIS PARTS snap14=91.81 https://www.car-parts.ie/car-parts/spidan-chassis-parts/p-14698694 cur=86.76
d943295e-e588-4573-b281-13494e983d3f 41320 A.B.S. snap14=323.89 https://www.car-parts.ie/car-parts/abs/p-17416458 cur=83.9
a36bcbff-0863-4eba-8e03-427c45bc3e2e 43038 METALCAUCHO snap14=42.31 https://www.car-parts.ie/car-parts/metalcaucho/p-16276536 cur=139.97
c939ad0b-e4f4-435d-b0a6-46e3d14e98fb 43359 MAPCO snap14=133.69 https://www.car-parts.ie/car-parts/mapco/p-9742209 cur=65.39
2a8ecb40-957d-4089-bbbd-33a34c67b10d 44232 ORIGINAL IMPERIUM snap14=227.21 https://www.car-parts.ie/car-parts/original-imperium/p-12932256 cur=303.53
67a2afca-51eb-4aea-b5be-30f8386a6af6 49066 NGK snap14=168.21 https://www.car-parts.ie/car-parts/ngk/p-9803741 cur=146.93
93ad7277-d144-47df-9a6e-79559e72f762 4948 FARE SA snap14=299.75 https://www.car-parts.ie/car-parts/fare-sa/p-9807173 cur=50.11
d504d80b-7b65-4e30-ae2a-105e4a069035 49560 NRF snap14=344.14 https://www.car-parts.ie/car-parts/nrf/p-7890007 cur=232.53
b8808eb1-c391-48ad-8879-ce52de033c4f 49831 MAPCO snap14=55.85 https://www.car-parts.ie/car-parts/mapco/p-2037385 cur=54.04
6fa3bb9f-ad82-4d8c-9968-128bb6c858da 50552 METALCAUCHO snap14=222.49 https://www.car-parts.ie/car-parts/metalcaucho/p-17500911 cur=130.53
48d5ec63-3163-4238-bbbc-c05a039af6d7 51006 PAGID snap14=209.16 https://www.car-parts.ie/car-parts/pagid/p-995742 cur=90.2
c12b7823-99e1-4602-a7ed-9d34b73d9f30 52753 SPIDAN snap14=1545.3 https://www.car-parts.ie/car-parts/spidan/p-19913211 cur=76.42
0f6c9084-a880-4e14-9f92-877b9af089c7 53084 AIC snap14=86.62 https://www.car-parts.ie/car-parts/aic/p-16110822 cur=118.95
b0c1459b-df21-4edb-a6d5-f6db5b6b3b3d 53096 AIC snap14=85.18 https://www.car-parts.ie/car-parts/aic/p-16110834 cur=298.51
8f1f0364-8326-4b10-81ae-a2a3d1d4dc13 53211 MAPCO snap14=89.62 https://www.car-parts.ie/car-parts/mapco/p-7644737 cur=183.85
3ab4eb11-425e-4980-aa30-1afe6cba48e5 53218 MAPCO snap14=129.09 https://www.car-parts.ie/car-parts/mapco/p-18259874 cur=70.89
09448387-61ef-49d8-8ebd-f49244bbe954 53540 MAPCO snap14=991.93 https://www.car-parts.ie/car-parts/mapco/p-9891638 cur=287.27
2b13772f-4422-4cfb-b8bf-2f3dc37638e4 56004 AIC snap14=102.34 https://www.car-parts.ie/car-parts/aic/p-16113646 cur=468.9
88b8c6fa-063b-4e5b-8f55-58889198fc01 58282 AIC snap14=290.28 https://www.car-parts.ie/car-parts/aic/p-16115819 cur=243.91
3b5d8d7b-ef8f-4b89-b541-3954a16f2e8f 58415 METALCAUCHO snap14=34.09 https://www.car-parts.ie/car-parts/metalcaucho/p-15950201 cur=315.71
e1e33371-5cf2-415a-a8f3-9614f931d3a6 58435 OSSCA snap14=487.89 https://www.car-parts.ie/car-parts/ossca/p-18538930 cur=339.4
f2aa2938-ccc0-4667-adbb-e61068f23930 58498 METALCAUCHO snap14=70.55 https://www.car-parts.ie/car-parts/metalcaucho/p-15379761 cur=412.24
b67f5b5f-c548-4103-961b-d2488013becc 60018 BREMI snap14=60.76 https://www.car-parts.ie/car-parts/bremi/p-13793290 cur=263.71
ae8b3fb1-7141-40ef-a9d2-1cf7785a0b7d 60216 BREMI snap14=86.93 https://www.car-parts.ie/car-parts/bremi/p-13793488 cur=1207.91
98a11bad-f3bc-4200-ae27-c828c6bfafc3 63124 MEAT & DORIA snap14=275.81 https://www.car-parts.ie/car-parts/meat-doria/p-15840358 cur=483.02
6cb20ed6-7990-49da-b9b6-68c77be9f184 64001 MEAT & DORIA snap14=532.62 https://www.car-parts.ie/car-parts/meat-doria/p-15840367 cur=205.01
c8fd4b4b-8624-49a9-beb9-c1b232277fc6 6423 LPR snap14=256.0 https://www.car-parts.ie/car-parts/lpr/p-27072853 cur=112.68
6fbe8ba6-f44e-4031-8094-ba4e759a231d 6436 LPR snap14=178.89 https://www.car-parts.ie/car-parts/lpr/p-24988775 cur=163.46
b7ce455c-855b-46d2-bd01-29ae1474c5c4 64881 NISSENS snap14=752.97 https://www.car-parts.ie/car-parts/nissens/p-7284333 cur=32.57
4602c5a4-f120-4c0a-825e-207860769cd7 69002 MAPCO snap14=76.56 https://www.car-parts.ie/car-parts/mapco/p-2039814 cur=291.21
6df93801-7f5e-48f4-b6a7-e9875de597fb 69003 MAPCO snap14=49.37 https://www.car-parts.ie/car-parts/mapco/p-7644878 cur=226.69
f076ff1c-0788-4c6c-b8a9-2ead9dda36d9 6PK1217 DENCKERMANN snap14=31.2 https://www.car-parts.ie/car-parts/denckermann/p-15272796 cur=40.79
490c7621-fe53-4ca6-9a32-57c1aea478b3 6PK1698 DAYCO snap14=64.08 https://www.car-parts.ie/car-parts/dayco/p-223071 cur=82.55
81db0b6f-afcd-464c-ad65-8c5f6a373f6f 70621 3RG snap14=26.52 https://www.car-parts.ie/car-parts/3rg/p-10142168 cur=131.65
ce67bacd-6861-4b21-ba32-232930895859 71226 SIDAT snap14=325.03 https://www.car-parts.ie/car-parts/sidat/p-7355177 cur=974.05
e2f63cac-a09b-4b2e-ba58-2b5614338294 71266 AIC snap14=730.55 https://www.car-parts.ie/car-parts/aic/p-17715441 cur=1531.27
232a8082-b615-4e7c-91dd-44a321ecaf0b 71280 SIDAT snap14=318.9 https://www.car-parts.ie/car-parts/sidat/p-7355224 cur=1514.36
f1d5de73-e604-47ad-aa57-3ee115cfb346 73006 A.B.S. snap14=30.5 https://www.car-parts.ie/car-parts/abs/p-14771295 cur=96.33
1f8d8db4-d57a-4672-83ff-084ac33d143b 801238 VEMA snap14=288.52 https://www.car-parts.ie/car-parts/vema/p-10229024 cur=217.19
49a8b48e-5a88-4025-8185-a536047b4c53 8071 LASER TOOLS snap14=508.48 https://www.car-parts.ie/car-parts/laser-tools/p-16684467 cur=1031.51
ae7c6e8d-1d2d-4c30-ae82-305bf9091d54 83684 MEAT & DORIA snap14=718.26 https://www.car-parts.ie/car-parts/meat-doria/p-13682023 cur=40.37
a72f8392-0b23-4349-b1ff-c4ac614db4eb 83714 MAPCO snap14=223.2 https://www.car-parts.ie/car-parts/mapco/p-7644974 cur=37.9
fb041dad-38b9-421d-ade5-8bf61ed36614 8572 BIRTH snap14=38.61 https://www.car-parts.ie/car-parts/birth/p-10338770 cur=194.95
c820790b-118f-43f4-afa4-35df365b4b29 89277 NISSENS snap14=720.17 https://www.car-parts.ie/car-parts/nissens/p-7024695 cur=112.86
1fe6dbe7-3d89-4763-b82e-d21d0c2502f8 90202 ROMIX snap14=58.38 https://www.car-parts.ie/car-parts/romix/p-17363434 cur=257.55
0c8a9657-ba20-460a-9850-e918747cfefb 90240 JAPKO snap14=86.0 https://www.car-parts.ie/car-parts/japko/p-10469812 cur=114.6
2d510de9-9470-4807-8a87-b64812bccaa1 91175 MEAT & DORIA snap14=827.11 https://www.car-parts.ie/car-parts/meat-doria/p-10487961 cur=84.53
e2bdb084-3a13-4044-90cf-728bc00408f4 91730 MEAT & DORIA snap14=90.83 https://www.car-parts.ie/car-parts/meat-doria/p-21922293 cur=25.93
0bf108f0-8cc0-48cb-ba5a-5f06ad9bd51b 92722 JAPKO snap14=1039.55 https://www.car-parts.ie/car-parts/japko/p-10508338 cur=208.1
0d694fac-2281-4a5a-9851-7e191530b53f 950005 DAKATEC snap14=1.56 https://www.car-parts.ie/car-parts/dakatec/p-15730350 cur=2389.4
9218d897-a9ea-450c-bb4c-8ad7c53f42ae 95598 NISSENS snap14=119.65 https://www.car-parts.ie/car-parts/nissens/p-10538141 cur=149.64
ae65148b-7d79-4b8e-acb7-8c001114cf48 A3050 DIAMAX snap14=254.9 https://www.car-parts.ie/car-parts/diamax/p-15545743 cur=50.7
7428494f-3e65-493b-be3a-bb561b2c496f G2269 FRECCIA snap14=9.36 https://www.car-parts.ie/car-parts/freccia/p-2479636 cur=272.24
730d7bf4-673e-4ee4-91f9-f864d0451a09 G2270 FRECCIA snap14=8.03 https://www.car-parts.ie/car-parts/freccia/p-7878442 cur=272.24
```
