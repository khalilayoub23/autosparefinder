# AGM Products (ASAP brand 53809) - import evidence, 2026-11-19 (Israel time)

* Access: logged-in ASAP session of account `autosparefinder` (id 209986), Chrome extension tab; no credentials requested or typed.
  Brand page `/user/209986/brands` lists AGM Products among the 5 approved brands.
* Download: form `data_download_request_form` (All Products, CSV, ranged years, no separate fitment file) -> Drupal batch 4/4 -> data sheet 2048723.
  Hidden field `price_level` is 0 (no dealer/cost tier exposed).
* Relay: single-use nonce (`maintenance/mint_asap_nonce.py`) -> `asap-collect` with `stage_only` -> `/app/state/asap/incoming/53809_full_20261119T050833Z.csv`,
  173,091 bytes, sha256 `ebb2e63059ad0ff6e2332c84669cd5c2e05a99a7a59380752e893cea512be8a6`.
* Dry run (rolled back) then real run through the existing `importers/asap_import.py` (no new importer): 73 rows -> inserted 73, updated 0, skipped 0,
  rejected 0, priced 73, images 73; live USD->ILS 3.27 (system rate, not a constant). All 73 rows brand "AGM Products", availability Available, not discontinued.
* Result in DB: 73 parts (`specifications.source='asap_network'`, active), 73 ASAP offers (offers 4,163 -> 4,236), `base_price` > 0 on all 73 (ownership rule
  `base_price_rule`), `max_price_ils` = 0 on all (MSRP is never an Israeli consumer price). No offer was created for any non-ASAP supplier. Duplicate handling:
  none needed (0 SKU collisions, 0 variant conflicts).
* Offer state: ASAP supplier `is_active=FALSE` -> not orderable (ORDERABLE_OFFER_SQL + offer_is_orderable). Customers see only a labelled estimate.
* The sheet also carries `jobber_pricing` (73/73 filled); the importer does not use it and nothing proves it is a price we can buy at.
* Index: PostgreSQL active parts 4,327,544 = Meilisearch documents 4,327,544 after an incremental `meili_sync` (documented command) - gap 0; a search for
  manufacturer "AGM Products" returns the 73 documents.
* Temporary data: none created. Staged CSV kept as evidence.
