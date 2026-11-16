# Consultant verification - Phase D: production repair of the 3 provable offers

Reviewer: independent read-only consultant. Production reads 2026-11-16 20:44-20:50 UTC (21:44 CET / 22:44 IL); every connection
`SET default_transaction_read_only=on`; catalog + PII DB, payload archives, the backend API on localhost:8000 (GET only). No write.
Scratch: `/tmp/claude-0/-opt-autosparefinder/0463056d-0823-4669-be4b-7e8313c87a56/scratchpad/c/` (v3.py/v3.json, main_run.py/out2.json).

## Verdict

**VERIFIED - repair correct and contained.** All 3 offers carry the owner-brand price, every side effect is the one intended, nothing
else among the 1,043 was changed by these writes. Meilisearch pickup is UNVERIFIED (not observable until the next 2-h sync).

Final counts (my live re-derivation, 20:4x UTC): **G 89** (74 preserved + 15 newly healed by owner re-harvests) · **REPAIRED 3** ·
**H 951** (950 foreign-only + 1 NGK/NTK tie). 89 + 3 + 951 = 1,043. Disagreement with `classification_1043_final.json`: only the
15 H rows that the owner brand healed between 17:06 and 19:58 UTC (expected drift, before the repair; listed below).

## Evidence integrity

- Manifest `wo_manifest_20261116.json.gz` sha256 `01bb5c65...7d35a` == pinned value == container copy. 3 rows, targets 149.67 / 585.75 /
  267.02, target URL == current URL, disposition `C_WRONG_PRICE`.
- Candidate copies under `production/` are byte-identical to `/app/state/cpie_ownership/overwrite_repair/*20261116T2039*` (6 files, sha256 compared).
- Tool provenance: `cpie_overwrite_repair.py` (sha256 a4ad4f51...) and `cpie_wrong_offer_manifest.py` (c73211b7...) on disk == git HEAD
  a27770b; file mtimes 20:37 UTC, i.e. before the 20:39 run (the run used the committed code).
- Timing gate: `run_all_tasks:2026-11-16T19:30` completed (last heartbeat 20:26:21 UTC) before the first write at 20:39:29 UTC.

## The 3 offers (VERIFIED)

| offer | live price / url / updated_at | == journal `after` | price_history | part min_price_ils | API customer price |
|---|---|---|---|---|---|
| 35d2d2e7 PT155 JAPANPARTS | 149.67 / daihatsu/wildcat-rocky-f70/ignition-system-auto / 20:39:29.663076 | yes | 1 row 54.91 -> 149.67, `cpie_overwrite_repair` | 149.67 | 217.02 = 149.67 x 1.45, VAT 0 |
| b1b5e079 ZR CT728 R JUMASA | 585.75 / citroen/c3-pluriel-hb/window-regulator / 20:39:44.499554 | yes | 1 row 242.14 -> 585.75 | 585.75 | 849.34 = 585.75 x 1.45, VAT 0 |
| 10d88c3d ZR PG57 L ELECTRIC LIFE | 267.02 / citroen/c1-ii/window-regulator / 20:39:44.712920 | yes | 1 row 635.48 -> 267.02 | 267.02 | 387.18 = 267.02 x 1.45, VAT 0 |

- Full-row diff vs the two pre-apply backups (to_jsonb, every column): offers changed **only** `price_ils`, `updated_at`; parts changed
  **only** `min_price_ils`, `updated_at` (bumped 20:39:31 / 20:39:46 / 20:39:46). part_id, supplier_sku, is_available, warranty,
  price_usd (0.00), supplier_url unchanged.
- price_history: exactly 3 rows since 20:00 UTC for these offers, ids == journal `price_history_id`; no repair-source row on any other
  of the 1,043.
- Journal: 3 `intent` + 3 `committed`, each intent == a real change (before != after price); results `applied` 1 + 2, `skipped` 0;
  `minmax_refresh` before/after match the live parts.
- API `/api/v1/parts/<part>/suppliers`: 1 supplier each, `supplier_sku` = bare article (no `~`), IE, `customer_vat_ils` 0.0, totals
  255.09 / 906.44 / 444.28 incl. shipping once. Supplier name masked.
- Customer references: PII `cart_items`, `order_items` (supplier_part_id + part_id), `part_price_watches`, `part_reviews`,
  `wishlist_items` = 0; catalog `aliexpress_candidates`, `part_aliases`, `part_cross_reference`, `meili_pending_deletes`,
  `scraper_api_calls`, `supplier_parts_removed_149`, `supplier_price_repairs_129` = 0.

## Foreign brands untouched (VERIFIED)

All Car-Parts.ie offers of the 3 articles (bare, `BRAND~`, legacy `Article №:`) = exactly the 3 repaired offers. The other parts with
these oem numbers (DAIHA-PT155, DAIHA-JAPKO~PT155, CITRO-ELECTRIC-LIFE~ZR CT728 R, CITRO-ELECTRIC-LIFE~ZR PG57 L, CITRO-ZR PG57 L,
TOYOT-ZR PG57 L) have `updated_at` <= 2026-11-15 - not touched. Pre-existing observation (not caused here, follow-up): the foreign
brands JAPKO / ELECTRIC LIFE (CT728) / JUMASA (PG57) have parts but NO offer, and those parts keep a `min_price_ils` (54.91, 242.14,
272.06) with no offer behind it - the known "brand/article pairs with a part but no offer" item.

## The other 1,040 (VERIFIED)

- 74 PRESERVED (incl. 625485ba): all 74 still equal an owner payload row and no foreign row; 5 were re-written by the owner with the
  same value since my 17:57 read (f7b6e46d, e8f8b2e4, 5ccb4f67, 5444fe57, fda5e590 - updated_at newer, value unchanged).
- No offer of the 1,043 other than the 3 has an `updated_at` at/after 20:39:29 UTC; nothing changed because of the repair writes.
- 15 H rows moved, every one by `car_parts_ie_import` (owner-brand re-harvest), 17:06-19:58 UTC, before the repair: current value ==
  the LATEST owner payload row (single owner price), written 2-94 s after that payload file. They are now G (PASS-PRESERVED):
  cb90b41b (103054 FEBI), 0446e840 (10320 FEBI), c4c5b13a (103214 FEBI), be082255 (15011 GEBA), cc8b12fc (21001 MPM),
  27c61a32 (302230 VALEO), d4fa62a5 (3688 NGK), 17ab3cee (37138 A.B.S.), c3bec056 (37615 SIDEM), f11b6337 (37823 A.B.S.),
  04b207ab (44509 FEBI), d4ea12ba (58060 NRF), 1d9b2f7d (5975 SIDEM), 6e4c5b1d (850005 GSP), 9a6c849a (910080 KYB).
  Note: cc8b12fc is in the mixed-identity group (part created from a metalcaucho page) - price now matches the part NAME, the part-level
  identity question stays open. This also confirms (weakly) the self-heal path for owners that appear on listings.
- Remaining H 951 still hold their classified (foreign) value.

## Importer protections (VERIFIED)

`backend/cpie_identity.py` (cde55ab3...) and `backend/importers/car_parts_ie_import_generic.py` (bfd0d006...) on host == git HEAD ==
container `/app` copies; ee18f6d fail-closed logic present (`return article if (same_url and same_part) else None`, cpie_identity.py:60).
The importer runs as a per-batch subprocess, so the disk version is the running version (inference). All 15 heals above were owner-brand
writes - no cross-brand bare-key write observed among the 1,043 since the fix.

## Open / minor

1. Meilisearch: min_price_ils of the 3 parts not yet re-indexed check - UNVERIFIED until the next `meili_sync` run (parts_catalog.updated_at
   was bumped, so the incremental pass should include them).
2. `classification_1043_final.json` uses the pre-correction WRONG_PART split (903 / 33 / 30) instead of the agreed 797 / 112 (ASSUMED) /
   33 / 5 / 19 - conservative (6 extra rows excluded from owner-price updates), no effect on the repair; fix the record.
3. Update the final classification with the 15 owner-healed rows (H -> G) and the 3 REPAIR -> REPAIRED.
