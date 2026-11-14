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
