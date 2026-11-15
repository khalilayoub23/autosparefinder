# Workstream C — car-parts.ie identity: measured root cause + design DRAFT (2026-11-14, read-only measurements)
Queries: /app/state/unmerge_distinct/_c_scope.py, _c_brands.py (temp table of parts with specifications->>'source'='Car-Parts.ie').

## Root cause (code: importers/car_parts_ie_import_generic.py)
- PART key  = `sku = <first 5 chars of VEHICLE make>-<bare article>` (l.169,234); `ON CONFLICT (sku) DO UPDATE SET name = EXCLUDED.name` (l.318). The product's BRAND is not in the key (only `specifications.part_brand`, which is the first word of the title and is often a vehicle make).
- OFFER key = `(supplier_id, supplier_sku = bare article)` (l.398-412); ON CONFLICT updates price/url but NOT part_id, so one offer row per article for every brand and every vehicle-make part row; price = last writer, part_id = first writer.
## Measured scope (2026-11-14 ~23:00 IL)
- cpie-source parts 1,057,548 (active 1,050,612); offers of supplier Car-Parts.ie 764,763 (745,433 on cpie-source parts, 19,348 on other parts); cpie parts with no offer 296,181; offers whose part number != supplier_sku: 2.
- articles with >1 part row (one per vehicle make): 141,705 (446,538 rows). Brand resolved with part_identity.title_brand: 132,243 single-brand (same product replicated per make), 8,573 articles with >=2 DIFFERENT resolved brands (true cross-brand collisions: one offer row, price/part shared), 889 no brand resolvable; 4,140 rows unresolved.
- `part_brand` field is NOT usable as a brand (FT51759 -> CHRYSLER, HONDA, MG, PORSCHE ...). Use part_identity's vocabulary only.
- Name flips of a single (make, article) row between brands are not observable in the current state; measurable only from backups (not done yet).
## Design (proposal, needs consultant review)
- Identity of a car-parts.ie product = (supplier, canonical_brand, article). New part sku `CPIE~<CANONICAL_BRAND>~<article>` (no vehicle make); new offer supplier_sku `<CANONICAL_BRAND>~<article>`; fitment attaches per vehicle (already per row).
- canonical_brand only from part_identity.title_brand / canonical_brand (vocabulary + evidenced aliases); unresolved brand => import row HELD (fail closed, counted in skip_reasons), never keyed on a guess.
- Migration classes: M1 same brand+article, N make-prefixed rows -> one part (SAME by supplier+brand+article; fitment/images union, via central policy); M2 article with >=2 brands -> separate parts, offer split per brand only where the brand of the existing offer is provable (else HOLD, offer kept, flagged); M3 brand unresolved -> HOLD untouched.
- Cutover must be atomic: harvester stopped, migration, new code, restart (same operation).

## Consultant review round 1 (advisor, 2026-11-15 00:0x IL) — open items, status
1. Population filter misses parts whose specifications were wiped to {"merged_from"} (workstream E): redefine population = source key OR holder of a Car-Parts.ie offer OR pre-wipe source in backup. TODO (heavy scan: after the 00:15 run_all_tasks).
2. Re-run brand analysis with title_brand ONLY (no part_brand fallback); report how many of the 8,573 multi-brand articles survive. TODO.
3. Single-row cross-brand overwrite unmeasured: compare supplier_url vs product_url/title brand; diff title brand per part id across monthly backups. TODO.
4. Hold rate (brand unresolved) over whole population + a raw harvest file; how a new brand enters the vocabulary (build_article_brands.py from catalogue). TODO.
5. Design fork: collapsing same-brand rows across vehicle makes (~300k parts mass merge) is NOT required to fix cross-brand collisions. Need measured counts for: search filter on parts_catalog.manufacturer; PII-DB tables referencing part_id; classify() on same-brand cross-make samples. Present both options (in-place sku rename keeping part ids vs merge) to the owner.
6. FOUND: `supplier_ordering.py:364` sends `supplier_sku` to the supplier as "Your SKU"; `services/suppliers/local_db_supplier.py` shows it as the article. => brand-qualifying supplier_sku for ALL offers would break ordering/display. Qualify ONLY the colliding brands' offers (the unique constraint (supplier_id, supplier_sku) is the reason two brands cannot share a bare article), and add a reader that strips the qualifier. Both `supplier_sku` and `sku` column widths still to check (supplier_sku is varchar(100)).
7. Reading PII tables: role is not `postgres` in autospare_postgres_pii (use the DATABASE_URL/PII user from the backend env).

## Round 1 results (2026-11-15 ~07:00 IL, queries /app/state/unmerge_distinct/_c_brands2.py)
- Population (source key OR holds a Car-Parts.ie offer): 1,103,742 parts (source key 1,084,394; 2,585 more are wiped-spec parts that only hold a cpie offer).
- title_brand ONLY (no part_brand fallback): 150,665 articles with >1 part row: 140,928 single brand, 8,623 with >=2 different brands (cross-brand collisions SURVIVE the stricter rule), 1,114 no brand. Brand unresolved over the whole population: 10,268 of 1,103,742 (0.93%) -> the "hold row, fail closed" rate. List: _c_multibrand_titleonly_20261115.json.
- part_id is referenced by 5 PII tables (cart_items, wishlist_items, part_reviews, order_items, part_price_watches; no cross-DB FK) and by 15 catalog tables (fitment, images, thumbnails, aliases, price_history via supplier_part_id, ...). A mass MERGE of same-brand cross-make rows (M1) would orphan PII references unless remapped -> recommend Option 1: keep part ids, qualify only what collides; M1 merge stays out of scope.
- supplier_ordering.py:364 sends supplier_sku to the supplier: qualify the offer key ONLY for the 8,623 colliding articles and strip the qualifier when ordering/displaying.

## Live collision evidence (2026-11-15, _c_ex.py) — the offer URL names the product it belongs to
- Article 62505: six part rows (MAPCO fuel filter x2, JAPKO cv-joint x2, NISSENS radiator x2) but ONE offer row (supplier_sku 62505, 29.23, URL .../nissan/prairie-pro-m11/fuel-filter, attached to HONDA-62505). Only the MAPCO fuel filter has a real offer; the JAPKO and NISSENS products have none, and the stored price is the last writer's.
- Article 28044: an Umbra Rimorchi TOWBAR part (OPEL-28044) holds the offer whose URL is .../suzuki/wagon-r-em/thermostat (56.24) = the MAPCO thermostat. The offer sits on the wrong product.
- So the offer's own `supplier_url` category slug + brand-bearing part is usable evidence to say WHICH product an existing offer belongs to; parts of the other brands have no real offer to restore (their prices were overwritten) and get theirs from the next harvest under a qualified key.
## Decision needed from the owner (genuine fork; consultant agreed it is not mine to settle)
- Option 1 (recommended): keep all part ids. Existing bare-article offers stay (their supplier_sku is what the supplier is ordered with). Re-point each colliding offer to the part its URL proves (HONDA-62505 stays, OPEL-28044 -> the MAPCO thermostat part), and let importer write `<BRAND>~<article>` offers ONLY when the bare article is already owned by a different brand (stateful rule, one function, tested). 8,623 articles in scope; 10,268 brand-unresolved rows held.
- Option 2: uniform key `<BRAND>~<article>` for every offer (764,763 renames + readers strip the qualifier) and a mass merge of same-brand cross-make rows (~300k parts) with PII part_id remapping. Larger blast radius; not recommended.
