# Readiness assessment: dropping `uq_supplier_parts_part_supplier` (stage 3)

Date: 2026-11-06 (Israel time). Author: Agent 2 (importers / harvester / seeder / offer preservation).
Scope: assessment + code fixes that are backward compatible with the old index. **Nothing was dropped, nothing was written to production, no service was restarted.**
Policy: `docs/policies/OFFER_DEDUPLICATION_POLICY.md` 1.0.0. Index facts (verified on production read-only): the old `UNIQUE (part_id, supplier_id)` still exists; `uq_sp_part_supplier_variant` and `uq_sp_part_supplier_untyped` exist (built 2026-11-06).

## VERDICT: NOT READY (code is ready; deployment and approval items remain)

The code defects that depended on the old index are fixed in the working tree and proven in a disposable database in BOTH states (old index present, old index absent). The drop itself must wait for the items in section 4, chiefly: **the running backend (uvicorn) still holds the old modules in memory** and only a recreate loads the fixed ones.

What stage 3 changes (exact): after the drop, the same supplier may hold several offers of one catalog part only when they differ by normalized variant (typed), or one untyped row alongside typed rows. Two untyped rows, or two rows of the same normalized variant, are still rejected (new indexes). Different SKUs of the same supplier+part that are both untyped are therefore STILL blocked: importers that write `part_type = NULL` behave as today.

## 1. Dependency matrix (every code path that touched or assumed the old uniqueness)

Legend: D = depends on the old index? / S = safe after the drop? / F = fix needed? / evidence type: EXERCISED (run in the sandbox) or INSPECTED.

| Path | D | S after drop | F | Evidence |
|---|---|---|---|---|
| `db_update_agent._populate_supplier_parts_task` (`ON CONFLICT (supplier_id, part_id) DO NOTHING`) | YES: that conflict target cannot resolve without the old index (error) and would add synthetic untyped twins | YES after fix | FIXED: `WHERE NOT EXISTS (supplier+part)` + target-less `ON CONFLICT DO NOTHING` | statement EXERCISED both states; the function itself is dead (see section 5) |
| `scripts/import_merged_catalog.py` `ensure_supporting_indexes` (`CREATE UNIQUE INDEX IF NOT EXISTS uq_supplier_parts_part_supplier`) | YES: would recreate the old index after the drop | YES after fix | FIXED: creation removed | EXERCISED: real script run with the old index absent leaves it absent (`old index present=0`); first/repeat/price change OK |
| `services/ebay_price_sync` existing-offer lookup (`part_id OR supplier_sku ... LIMIT 1`, UPDATE renamed `supplier_sku`) | YES: arbitrary row once variants coexist; and the old ordering plus SKU rename raises `supplier_parts_supplier_id_supplier_sku_key` and ABORTS the whole sync run when another row already holds the SKU | YES after fix | FIXED: exact SKU match first (offer identity), then untyped row, then typed, oldest first, deterministic; `supplier_sku = COALESCE(supplier_sku, :sku)` (never renamed) | EXERCISED: 4 new tests; reverted-code run: 3 of 4 FAIL (UniqueViolation aborts sync); fixed code: 4 pass in both states |
| `services/aliexpress_price_sync._write_supplier_part` (`LIMIT 1` by supplier+part) | YES (picked an arbitrary row) | YES after fix | FIXED earlier this task: picks the matching-variant/untyped row; all-known-different-class gives `variant_conflict` skip (still required while the old index exists; conservative after) | EXERCISED (real function, 2 variants coexisting with the old index dropped) + existing tests |
| `maintenance/merge_master_parts.py` (`same = ... part_id AND supplier_id` existence checks, move branch, `repair_stranded`) | existence check only | YES (conservative: any same-supplier offer on the canonical keeps the loser's offer in place) | `repair_stranded` conflict branch fixed earlier (no delete / no price lift) | EXERCISED earlier (real `repair_stranded`) + `test_offer_policy_merge` |
| `maintenance/bmw_oem_dedup.py` move branch (`NOT EXISTS` supplier on canonical) | existence check only | YES (same conservative rule) | none | existing `test_offer_policy_merge` (EXERCISED in both states) |
| `maintenance/offer_policy_repair.py`, `quarantine_motorstore_offers_129.py`, `repair_double_margin_129.py`, `backfill_warranty_months.py`, `tag_warranty_source.py` | id / supplier keyed updates | YES | none | INSPECTED |
| All SKU-upsert importers (24 + scrapers): `ON CONFLICT ON CONSTRAINT supplier_parts_supplier_id_supplier_sku_key` | NO | YES | none | EXERCISED statement-level for 25 files in the stage-3 state: first insert, repeat, price change = 1 history row, other supplier independent, a different-variant row accepted (amayama writes the literal `'oem'`, so a different `original` row of the same variant is correctly rejected by the variant index; `ai_catalog_builder` is `DO NOTHING` by design) |
| `importers/car_parts_ie_import_generic.py` (harvester import), `oempartsonline_importer.py` | NO (SKU constraint) | YES | none | EXERCISED (real `import_file`, real CLI, stage-3 state) |
| `catalog_scraper.py` plain INSERT (guarded by `created`) | pair clash raised today; none after | YES | none | INSPECTED + statement analysed |
| `BACKEND_DATABASE_MODELS.SupplierPart` | NO: `__table_args__` has only `UniqueConstraint(supplier_id, supplier_sku)`; no relationship/uniqueness on (part, supplier) | YES | none | INSPECTED |
| Alembic migrations | NO: no migration creates or names the old index (it is out-of-band; revision 0062 versions columns only) | YES | none for the drop; the CONCURRENTLY DDL is not an alembic revision | INSPECTED (grep of `alembic/versions`) |
| Search offer lists, partner API, chat/WhatsApp pricing | list per offer; selection is by `supplier_part_id` or `class_rank_sql` ordering | YES (two rows of one supplier are two offers) | none | INSPECTED: no dict keyed by `supplier_id` collapses offers in `routes/parts.py`, `routes/cart.py`, `routes/orders.py`, `routes/payments.py` |
| Cart add / order lines / fulfilment | keyed by `supplier_part_id`; delivery fee and purchase grouping take `max`/bucket per supplier (`price_policy` line ~204, `routes/utils` `by_supplier`) | YES (one delivery fee per supplier, as policy requires) | none | INSPECTED |
| `maintenance/create_fake_supplier_for_cycle.py` (`scalar_one_or_none` by supplier+part) | assumes one row | only breaks if its own fake supplier gets two variants | none (test helper) | INSPECTED |
| Tests | `tests/test_offer_variant_identity.py` builds both states itself; `tests/test_offer_policy.py::test_no_importer_keys_supplier_parts_on_part_and_supplier_only` forbids the old conflict target | YES | none | EXERCISED, see section 3 |
| Docs | `CLAUDE.md` SQL Pattern 1 still states the old index as the pair constraint; `docs/IMPORTER_RULES.md`, `docs/PRE_LAUNCH_CHECKLIST.md` (populate task) | n/a | wording update after the drop | INSPECTED |

## 2. Exact sandbox proof (stage 3 run in a disposable DB)

Environment: `pgvector/pgvector:pg16` container `asf_agent2_pg` on its own network `asf_agent2_net`, database `sandbox` loaded from a read-only `pg_dump -s` of production (minus `night_pipeline_runs`, which the isolation guard treats as a production fingerprint, and minus the `trg_sync_*` triggers). Indexes on `supplier_parts` in the sandbox: SKU constraint + `uq_sp_part_supplier_variant` + `uq_sp_part_supplier_untyped` (DDL identical to production, verified by reading `pg_indexes` on production). The container and network were removed afterwards.

Stage 3 executed in the sandbox only:
```
DROP INDEX uq_supplier_parts_part_supplier;      -- sandbox only
SELECT indexname FROM pg_indexes WHERE tablename='supplier_parts' AND indexname LIKE 'uq_%';
 -> uq_sp_part_supplier_untyped, uq_sp_part_supplier_variant
```
Then, with the old index absent:
1. Statement-level importer harness (`stmts.py`, STAGE3=1), real SQL extracted from 27 files: 25 ALLPASS (first insert, repeat idempotent, price change = exactly one history row, other supplier independent, different-variant row accepted, original row untouched). The two exceptions are expected and described in section 1 (amayama same-variant clash is rejected; ai_catalog_builder is DO NOTHING).
2. Real code run (`h2.py`), **14/14 passed** with the old index absent: car-parts.ie `import_file` (price-less re-harvest keeps the price; real change = one history row), `catalog_scraper._write_price_history`, eBay `sync_ebay_prices` (7 checks), `rockauto_price_import.main`, `amayama_price_import.main`, and `scripts/import_merged_catalog.py` as a subprocess (first / repeat / change; and the old index is not recreated).
3. Pytest suite in both states, same database, one run with the old index absent and one with it present: `tests/test_offer_variant_identity.py tests/test_offer_policy_importers.py tests/test_offer_policy_merge.py tests/test_offer_policy.py tests/test_aliexpress_price_sync.py tests/test_aliexpress_discovery.py tests/test_ebay_price_sync_lookup.py tests/test_maintenance_policy_alignment.py tests/test_price_policy_unified.py tests/test_offer_classification.py`: **198 passed, 17 skipped, 2 failed in each state** (identical).
4. The two failures are not caused by this work: `test_backfill_counts_real_inserts_and_survives_one_failed_insert` fails identically on pristine HEAD (sandbox `car_brands` seed gap); `test_cleanup_normalizer_uses_the_rule_and_fills_only_local_cost_columns` passes at HEAD and is caused by uncommitted changes in `db_cleanup_agent.py` (bisected by swapping in HEAD's file; not a file touched here). Neither involves `supplier_parts` uniqueness.

Counter-proof for the eBay fix: the new tests run against a copy with the old lookup restored: **3 failed, 1 passed**; the failure is `UniqueViolationError supplier_parts_supplier_id_supplier_sku_key`, which ABORTED the whole sync run (`sync_ebay_prices failed`), not just one part.

## 3. Code changes landed now (all backward compatible with the old index present; old index untouched)

- `backend/services/ebay_price_sync.py`: deterministic lookup (SKU identity first, then untyped, then typed, oldest first) and `supplier_sku` never renamed (`COALESCE`).
- New test `backend/tests/test_ebay_price_sync_lookup.py` (4 tests, isolated-DB guard like the AliExpress tests; the stage-3 cases drop the old index inside the test's own rolled-back transaction).
- Earlier in this task and still required for stage 3: `db_update_agent.py` populate guard, `scripts/import_merged_catalog.py` (no index recreation), `services/aliexpress_price_sync.py` (variant-aware row choice), `maintenance/merge_master_parts.py` `repair_stranded`.

## 4. Blockers and required items before the drop (stage 3 = separate owner approval; NOT authorized here)

1. **Load the fixed code in the running backend.** `uvicorn` imported `services.ebay_price_sync`, `services.aliexpress_price_sync`, `db_update_agent` and `catalog_scraper` at start; the bind mount does not reload them. Until a `--no-deps --force-recreate` of the backend (with the production preflight, never 10:00-14:00 IL, owner approval) and a `runtime_identity_check.py --pid 1 --expect MOD=SHA` for those modules, the live eBay sync still has the old lookup and could abort a run after the drop. Importers/maintenance scripts started as subprocesses already use the disk version.
2. **Commit / deploy hygiene.** The fixes sit in a working tree that also carries other uncommitted work; the code that is deployed must be exactly what was tested.
3. **Owner approval for stage 3**, in a quiet window, `DROP INDEX CONCURRENTLY` with `lock_timeout=0` (CLAUDE.md note), after a read-only preflight (the two new indexes valid, no long transaction, disk free). Rollback: `CREATE UNIQUE INDEX CONCURRENTLY uq_supplier_parts_part_supplier ON supplier_parts (part_id, supplier_id)` only works while no same-supplier variant rows exist; after variants are stored the old index CANNOT be rebuilt without first resolving them, so the drop is effectively one-way once any importer writes a second variant.
4. **Doc updates after the drop**: `CLAUDE.md` SQL Pattern 1, `docs/IMPORTER_RULES.md`, `docs/PRE_LAUNCH_CHECKLIST.md`, `FIXES_TRACKER.md` entry.
5. Decide the dead populate task (section 5) before or independently of the drop: it no longer blocks it.

Not blockers (documented limits): AliExpress still skips a listing whose class differs from every stored row (conservative; storing the second variant is a later feature); merge scripts keep any same-supplier offer in place (conservative); `ai_catalog_builder` and the car-parts.ie importer silently drop a second same-supplier row that the untyped index rejects (unchanged behaviour); importers do not set `part_type`, so most data stays untyped and still gets one row per supplier+part until an importer is made variant-aware.

## 5. `_populate_supplier_parts_task` (dead): exact callers and recommendation

- Defined `backend/db_update_agent.py:3769`; registered only in `TASK_REGISTRY["populate_supplier_parts"]` (line ~4531).
- Callers: `db_update_agent.run_task()` (used by `run_all_tasks` only for tasks in its list; populate is NOT in the 3x/day list), `routes/admin.py:4902` `POST /api/v1/admin/db-agent/run/{task_name}` (admin only), the owner console `הרץ משימה`, and `maintenance/run_targeted_worker_fitment_pass.py` (which runs a caller-supplied task list). No scheduler calls it.
- It cannot run: `_UNIVERSAL_SUPPLIERS`, `_MANUFACTURER_SUPPLIERS`, `_BATCH`, `_CATEGORY_FALLBACK_ILS`, `_WARRANTY_MAP`, `_DEFAULT_PRICE` are undefined (same at HEAD), so it raises `NameError` as soon as any active supplier exists. It also invents prices from `base_price` (contradicts the no-fabrication and price-policy rules).
- Recommendation (not done, no deletion): remove the `TASK_REGISTRY` entry so the admin API answers 422 instead of NameError, then delete the function and the docs lines (`PRE_LAUNCH_CHECKLIST.md` lines 46-48, 70, 81; `roadmap.md` 170; module docstring item 9) in a separate cleanup change with an owner decision.
