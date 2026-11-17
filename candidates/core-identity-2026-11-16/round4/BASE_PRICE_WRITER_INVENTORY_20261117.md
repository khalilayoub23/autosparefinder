# base_price writer inventory — 2026-11-17 (traced from code + runtime entry points; VERIFIED = read in this session)

Authoritative implementation: `backend/base_price_rule.py` (`target`, `apply_to_parts`, `ensure_vat_registry`) used by `db_update_agent.apply_base_price_rule`.
Rule: base_price = price policy's NET (cost x 1.45, supplier VAT basis from the registry) of the part's price-basis offer of its own class; else the catalog estimate; else untouched.

| Writer | Runtime entry | State after this round |
|---|---|---|
| `importers/car_parts_ie_import_generic.py` | subprocess of `/collect` relay + harvester | **rule-owned** (`base_price_rule.apply_to_parts`, commit aaf5e21, live via bind mount). Now also loads the VAT registry (`ensure_vat_registry`) — subprocess starts with registry unloaded |
| `db_update_agent.apply_base_price_rule` (tasks `fix_base_prices`, `normalize_base_price`) + `db_cleanup_agent` healers (1161, 1240) | uvicorn tasks (the two maintenance tasks are disabled in run_all_tasks, A3 pending) | rule-owned; now `ensure_vat_registry(db)` first (no-op inside uvicorn) |
| `maintenance/cpie_base_price_repair.py` | manual, SHA-pinned manifest | rule-owned; registry loaded in `_connect`; only `REPAIRABLE` rows applied |
| `catalog_scraper.db_upsert_part` | night pipeline `rex_night` / `category_discovery`, `run_brand_discovery` | **FIXED**: used to write the source's raw USD price into base_price (INSERT and UPDATE — the UPDATE overwrote the rule's value). Parameter removed; new parts start 0.0 and `apply_base_price_rule` runs after the offer exists. Category-discovery parts have no offer -> stay 0.0 (no basis) |
| `maintenance/margin_fix_batched.py` | formerly run_all_tasks | hard-disabled (d0fb160) |
| `ai_catalog_builder.insert_parts` | CLI only (`python3 ai_catalog_builder.py`); no catalog_versions rows ever; only `lookup_oem_spec`/`enrich_pending_parts` are imported by db_update_agent | **FIXED (dormant fabricator)**: LLM "realistic price" (default 100) went into base_price + a supplier offer. Write path hard-disabled (dry-run only) |
| `maintenance/price_audit_fix.py` | `sys.exit("DEPRECATED")` at import | dead (verified line 37) |
| `maintenance/repair_vat_paths_151.py` | manual, journaled, rollback | one-shot repair tool with its own policy-constant formula (pp.MARGIN/VAT); not scheduled |
| `routes/admin.py` 4536/4549 | admin spreadsheet upload (human-entered price) | manual owner/admin input, not computed; unchanged |
| `importers/rockauto_price_import.py` | spawned automatically by `routes/system.py:331` (/collect relay, brand=rockauto) | **OPEN (added after consultant review)**: own `price_ils x 1.45` (foreign USD cost), fills only an empty/zero base_price; no VAT-basis awareness |
| `importers/amayama_price_import.py` | spawned automatically by `routes/system.py:344` from the always-on Amayama harvester (NOT an on-demand one-shot) | **OPEN**: own `price_ils x 1.45` when unpriced |
| `oempartsonline_importer.py` | spawned by `catalog_scraper` (REX path) (NOT an IL importer) | **OPEN**: own `round(price_ils x 1.45, 2)` when empty/zero |
| `harvesters/acura_browser_harvest.py` (unconditional on UPDATE), `harvesters/spareto_harvester.py`, `scrapers/bydil_scraper.py`, `scrapers/eliteparts_scraper.py`, `scrapers/selected_parts_scraper.py` (`base_price = EXCLUDED.base_price`), `maintenance/vat_fix_batched.py` | manual CLI | **OPEN (low)**: own formulas, not rule-owned |
| IL importers (`samelet_import_v2`, `kia*`, `toyota_il`, `colmobil_import_v2`, `mct`, `delek*`, `freesbe*`, `lexus_pdf`, `xlsx_il`, `supplier_pdf`, `il_importer_pdf`, `jaguar*`, `zeekr*`, `amayama`, `oempartsonline_importer`, `asap`, `mixed_brands`, `fitz`, `lr_subaru_byd`, `subaru_il`, `bmw_il`, `hyundai_genesis_mitsubishi`, ...) | on-demand / scheduled one-shots | **OPEN — own `price x 1.45` formula, not the rule.** Differences measured below. NOT changed in this round (needs per-importer VAT-basis/rounding decision; each also stores `importer_price_ils`/`max_price_ils`) |
| `scripts/fix_oem_price_gaps.sh`, `scripts/import_merged_catalog.py`, `importers/import_parts_db.py`, `ai`-free seeds | manual | OPEN, same class as the IL importers |

## Catalogue-wide divergence sample (read-only, 9,600 random active parts, rate 3.21, registry loaded) — `bp_sample_all.py`
correct 2,807 / divergent 1,931 / no target (no visible offer, no estimate) 4,862.
By basis-offer supplier: Car-Parts.ie 994 of 1,746 divergent (57%); SNG Barratt 270/273; Kia IL 98/104; Colmobil Hyundai 48/48; eBay Motors 44/47;
MINI/JLR/Ford Delek 28/26/19 of ~40; Champion 0/146, Mazda 0/23, BYD 0/15 (importer formula == rule).
Examples (cause class): Colmobil/Kia — importers store integer-rounded base (413 vs rule 413.96); Kia — `base_price == max_price_ils` (95, ×1.0) vs rule 116.74;
SNG/eBay — foreign offers valued at an older/different rate than the rule's current rate (basis 'unknown' in the registry for these foreign suppliers; foreign VAT 0).
Status: **UNVERIFIED as defects** (different definition per importer; base_price is an internal reference, customer prices come from price_policy). Reported, not repaired.

## Runtime caveat found by the independent consultant (2026-11-17)
`car_parts_ie_import_generic -> base_price_rule.apply_to_parts` applies the catalog ESTIMATE to any part it upserts, including parts with no offer
(178 of the 470,647 STALE/ESTIMATE-class parts changed after the repair run, each to exactly the manifest target, all via the live importer at
03:59 UTC). So the repair tool's "ESTIMATE_AMBIGUOUS = do not write" is a property of the tool, NOT of the runtime importer. Open decision.
