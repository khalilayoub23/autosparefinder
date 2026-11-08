# Offer Deduplication Policy — production readiness package (2026-11-06)

Status of everything below: **PROPOSED / ISOLATED-TESTED. Nothing here is applied to production.** Evidence files:
`candidates/offer-policy-2026-11-06/` (compose diff, migration SQL, candidate patches, Motorstore report, audit JSON).

## A. Policy mount (Compose)
* File/service: `docker-compose.yml`, service `backend` (the only service with `./backend:/app`; the resolved-config diff of old vs new
  shows exactly one added bind and no other change — `docker compose config`, not applied).
* Diff (`compose_policy_mount.diff`): `- ./docs/policies:/policies:ro` directly under `- ./backend:/app`.
  Host `/opt/autosparefinder/docs/policies` → container `/policies`, **read-only**, a *directory* bind (an edited/replaced file is seen —
  the nginx single-file inode problem does not apply). Mounted outside `/app` on purpose: a mount inside the bind-mounted `/app` would
  make Docker create `backend/docs/policies` on the host (verified absent now).
* Loader: no path change needed — `/policies/OFFER_DEDUPLICATION_POLICY.md` is a candidate path in `offer_policy._candidate_paths`
  (tested). Ownership/permissions: file `root:root 0644`, readable by the container user (verified in a throwaway container with the
  same mount: `require_policy()` → 1.0.0 / `9e05c714…8235`, write attempt → `Read-only file system`).
* Hash verification inside the container: `docker exec autospare_backend sha256sum /policies/OFFER_DEDUPLICATION_POLICY.md` must equal
  the pin. **Superseded 2026-11-07 (policy v1.1.0):** the policy hash is now
  `041c62d1f0b3a42e8f54765c7d5de7a8d79690af71fbe5fc289c61068fb91e9f` (v1.0.0 was `9e05c714…8235`); `docker-compose.yml` sets
  `OFFER_POLICY_EXPECTED_SHA256` to that hash and `OFFER_POLICY_REQUIRE_PIN=1` (the loader then refuses to run with no pin and
  refuses any hash that is not listed; several hashes may be listed for a version bump). The sibling
  `docs/policies/OFFER_DEDUPLICATION_POLICY.md.sha256` and `tests/test_offer_dedup_policy_pin.py` keep file, sibling and compose equal;
  `maintenance/verify_policy_pin.py` proves file == pin == runtime hash and the fail-closed cases (exit 3) inside the container.
  Missing/changed file → `PolicyUnavailable` → fail closed (exit 3). Bump procedure: policy §8.
* Other services: none affected; no volume/bind conflicts (`/policies` unused; checked in the resolved config).
* Rollback: remove the line, `docker compose up -d --no-deps --force-recreate backend`.
* **Needs separate approval:** applying it = recreating the backend (preflight `scripts/pre_restart.sh` must say SAFE; never 10:00–14:00 IL;
  no `PRE_RESTART_FORCE`). Not executed.

## B. Index migration (`uq_supplier_parts_part_supplier`)
Measured (read-only): `supplier_parts` 4,241,873 live rows, table 1,018 MB, indexes 1,716 MB; old index 267 MB; `price_history` 1,193 MB
(8,671,037 rows); `wal_level=replica`, `max_wal_size=1GB`, 0 streaming replicas; 56 GB free; 0 active statements touching `supplier_parts`
at measurement time. The old index is **not in Alembic** (created out-of-band / by `scripts/import_merged_catalog.py:134`).
* **Identity model.** catalog part=`part_id`; supplier=`supplier_id`; commercial offer=`(supplier_id, supplier_sku)` (existing unique
  constraint, kept — every importer already targets it); variant=normalized `part_type` (`original/genuine/oem/oe`→`oem`; NULL = unknown,
  never inherited for uniqueness); source=import stamp (no column; unchanged). Same SKU+supplier never implies same variant because variant
  is a separate key; an unknown variant is never combined with a known one by the database.
* **Proposal** (`migration_variant_identity.sql`): replace the unique `(part_id, supplier_id)` by two **partial unique** indexes —
  typed `(part_id, supplier_id, variant) WHERE part_type IS NOT NULL` and untyped `(part_id, supplier_id) WHERE part_type IS NULL`. Not a
  weakening to a plain index: one offer per supplier+part+variant stays enforced. Stage 3 drops the old index only after the code changes.
* **Compatibility proof:** the old index is strictly stronger, so all 4,248,896 rows satisfy both new ones; preflight counts = **0 / 0**
  (verified on production, read-only). Sandbox test (indexes created then dropped): oem+aftermarket coexist; `original` vs `oem` rejected;
  NULL+typed coexist; a second NULL rejected.
* **Existing references:** only FK to `supplier_parts` is `price_history(supplier_part_id)` ON DELETE CASCADE; `order_items.supplier_part_id`
  (PII DB) and the cross-DB columns at BACKEND_DATABASE_MODELS.py:974 and :1137 are UUID references without FK. The migration never changes or deletes a row/id →
  history, orders and routing (`OrderItem.supplier_part_id → SupplierPart → Supplier`) are untouched. 1 Motorstore offer is referenced by an order item.
* **Code that assumes one offer per (supplier, part) and must change BEFORE stage 3:** `db_update_agent.py:3826` (`ON CONFLICT (supplier_id, part_id)`
  needs an arbiter index → use the supplier+sku constraint or no target), `scripts/import_merged_catalog.py:134` (recreates the old index),
  `services/aliexpress_price_sync.py` and `services/ebay_price_sync.py` (lookups/updates by supplier+part must also scope by variant/SKU),
  the merge scripts (candidate patches below). Importer upserts keyed on `(supplier_id, supplier_sku)` are unaffected. Importers must treat a
  unique violation on the new indexes as a `variant_conflict` to report, never as something to overwrite.
* **Large-table safety (ESTIMATES from sizes, not measured builds):** `CREATE UNIQUE INDEX CONCURRENTLY` takes a SHARE UPDATE EXCLUSIVE lock — does
  **not block reads or writes**; it waits for older transactions (idle-in-transaction timeout is 30 min), scans the table twice;
  expected a few minutes per index; sizes ≈ 245 MB (untyped, 3.88 M rows) + ≈ 25 MB (typed, 370 k rows); WAL ≈ index size → may cause one extra
  checkpoint with `max_wal_size=1GB`; disk need < 0.6 GB; no replicas to lag; moderate CPU/IO — run off the `run_all_tasks` slots
  (21:30 / 00:15 / 07:15 IL) and the Saturday night pipeline. Bounded stages: the two builds are independent and idempotent
  (`IF NOT EXISTS`); an interrupted build leaves an `INVALID` index (`pg_index.indisvalid=false`) — detect with the stage-2 query, `DROP INDEX
  CONCURRENTLY` it and rerun.
* **Rollback:** drop the two new indexes; if stage 3 ran, recreate the original unique index first (data permits it only while no two variants
  share a part+supplier — which is exactly what the new model allows; therefore stage 3 is the point of no easy return and has its own approval).
* **Approval gates:** (1) mount + recreate (A); (2) code deploy of the importer/agent changes; (3) stage 1 index builds in a quiet window with a
  read-only preflight (active jobs, locks, `pg_stat_activity`, load); (4) stage 3 drop. Each is a separate yes.

## C. Code-path audit (whole repo; `codepath_scan` in the scratchpad, re-runnable regex scan + manual read)
* Offer **deleters:** only `merge_master_parts.py`, `bmw_oem_dedup.py` (policy-bound list) — `ai_catalog_builder.py`/`import_from_excel.py` fixed earlier.
  `DELETE FROM parts_catalog` (cascades offers+history): only `ai_catalog_builder.py` (now refuses when offers exist).
* **`merge_master_parts.py` (runs weekly via night_pipeline Saturday ≥05:00 IL and via job_queue — LIVE):** branches: (1) enrich canonical fields —
  copies prices (max/min) between catalog rows, no offer change; (2) offers: *move* when canonical lacks the supplier (id/price_history preserved —
  safe); *conflict* (canonical already has this supplier): **lifts the cheaper price onto the canonical offer without a price_history row and DELETEs
  the loser's offer** (cascade deletes its price_history) — never classified, different SKUs/variants are destroyed, no audit; (3) fitment dedupe
  (outside offer policy); (4) images; (5) soft-delete loser (reversible). **Defect: branch 2-conflict.** Proven in the sandbox
  (`tests/test_offer_policy_candidates.py`): live → variant offer deleted, history 1→0, canonical price 100→80; candidate
  (`merge_master_parts.candidate.py`) → offer + history kept, price untouched, competing supplier's offer moved with its id, loser stays active.
  Unresolved offers are protected only in the candidate. **Entry gate is deliberately NOT enforced (`enforce=False`)**: enforcing would make the
  scheduled run exit 3 until the mount exists; it warns instead. This was a correction of my own previous change (it was enforced for ~1 h on disk).
* **`bmw_oem_dedup.py` (CLI only, nothing schedules it):** same conflict pattern (1a UPDATE-LEAST price + DELETE; 1a2 loser-vs-loser DELETE keeping the
  cheapest) → destroys variants/history; no OEM-vs-aftermarket check. Candidate `bmw_oem_dedup.candidate.py` moves conflict-free offers only and retires a
  loser only when it has no offers left. Gate stays enforced (policy mount required). Candidate not sandbox-executed (BMW-specific work table).
* **`aliexpress_price_sync.py`:** existing row looked up by (supplier, part) → in-place UPDATE (price_history written on change; verified idempotent,
  history appended, competing supplier independent — tests). `INSERT … ON CONFLICT ON CONSTRAINT supplier_parts_supplier_id_supplier_sku_key DO NOTHING`
  returns None when the SKU belongs to another part of this supplier: it cannot discard a same-offer price update (the pre-lookup catches that) but it **can
  drop a distinct variant** whose SKU already exists on another part (counted as skipped). **Defect:** the UPDATE re-labels `part_type` and overwrites
  `supplier_sku` of the stored row — a oe_equivalent listing silently converts a stored aftermarket offer (proved by test). Candidate
  `aliexpress_price_sync.candidate.py`: skip + report `variant_conflict`, never overwrite SKU. Not deployed.
* **Other paths found:** `ebay_price_sync` (marks only its own supplier+part unavailable when not shipping to IL — safe; scoped), `rockauto_price_import`/
  `amayama_price_import` (upsert on supplier+SKU — safe), `db_update_agent:3826` (`DO NOTHING`, never overwrites — safe), 26 importers with
  `ON CONFLICT … DO UPDATE` on supplier+SKU (static only), `UPDATE … supplier_id|part_id` only in merge scripts and the new repair agent.
  **Gap found:** price changes are written to `price_history` only by BOAZ sync (last row 2026-05-14: 8,626,601), eBay (39,116), AliExpress (4,004),
  `catalog_scraper`; **the ~26 IL importers update `price_ils` in place with no history** (class C requires it). Proposed: one shared helper — not built.
* All of the above is a regex + manual scan, not proof of exhaustiveness (dynamic SQL built from strings/f-strings and ORM `session.delete` were not enumerated).

## D. Importer verification (sandbox, rolled back; `tests/test_offer_policy_importers.py`, `…_candidates.py`)
Exercised: AliExpress `_write_supplier_part` (live **and** candidate): repeat → 1 row, 1 history; price change → same row id, 2 history rows; second supplier →
independent row; variant overwrite (live defect vs candidate fix). `import_from_excel` upsert: repeat idempotent, price update in place with row id and history preserved,
other supplier independent. Merge scenario as in C. **Not exercised:** the 24 other importers/scrapers, Harvester/Seeder, failed-import-retry loss tests, missing-source-evidence
tests per importer (limitation; they are statically classified).

## E. Motorstore (1,730 available offers — `motorstore_1730_report.csv`)
Live-reconciled with the prior mapping (all 1,730 ids match; D1=125, D2=1,605). **0 have a `supplier_url`; 0 have price_history; 1 is referenced by an order
item.** Classes (stricter rule: a source is named only when another supplier holds the *same SKU at the same price*): **155 competing, 180 misattributed, 1,395
unresolved** (supersedes the earlier 155/11/1,564 — that rule named "the only other orderable supplier", which is not evidence). 179 of the 180 are exact copies
(same SKU and price) of **Official Manufacturer Sites** offers (inactive supplier, those offers unavailable), 1 of AutoParts Pro IL; 168 Mercedes-Benz, 5 Porsche, 7 D1 brand rows.
They cannot be reassigned: the named supplier already owns `(part, supplier)` (unique index), and that supplier's own offer is unavailable — reassigning or retiring would change
orderability. **Nothing proves any of the 1,730 comes from motorstore.co.il** (its product pages carry no SKU/OEM). The 113-"duplicate" retirement stays withdrawn. Proposed future
strategy, compatible with the variant model: keep rows; when a real Motorstore price source (re-scrape keyed by site product id) exists, write real offers as typed variants;
E rows would be re-pointed only after their true source offer is made available. Offer ids and evidence: the CSV (columns `other_suppliers` carry the colliding offer ids).

## F. Repair agent (`maintenance/offer_policy_repair.py`)
Tests: policy loaded before any DB connection (asserted by patching `asyncpg.connect`); missing file, malformed file, outdated version, **wrong hash pin** → exit 3, no connection;
apply validation, audit row with version+sha, rollback and idempotent re-apply (isolated DB); non-permitted actions for A/B/C/F rejected; read-only audit stamps the hash.
`apply` only supports `reassign_supplier` (E) and `retire/deactivate` (D) through `assert_write_allowed`. Production `--apply/--rollback` not run.

## G. Reconciliation (read-only, 2026-11-06, unchanged since the first audit)
Parts 4,632,819 (4,137,685 active) · suppliers 153 (72 with offers) · offers 4,248,896 (1,523,234 available) · parts with ≥2 suppliers 290,664 · `price_history` 8,671,037 ·
(supplier, part) duplicates 0 · same-supplier groups on different catalog parts 8,319 / 19,036 offers → B 1,280, F 7,038, mixed 1.
**Zero duplicates today does not mean none were lost.** 363,164 catalog parts carry `dedup_merged_into` (merged), 107,621 offers sit on inactive parts (209 on merged losers).
The live merge conflict branch deleted offers on every same-supplier conflict and logged nothing durable (one surviving log line: `sp_dropped=73`); `price_history` rows of those
offers were cascaded away. The magnitude is **unknown (UNVERIFIED)**; it could be estimated only by diffing `autospare_postgres_backup_catalog` against live — not done.
The 8,319 groups remain unclassifiable by `part_id` alone: variant evidence (`part_type`, name, brand) exists for ~1,280 only.

## H. Stage A deployment record (2026-11-06, 14:15 IL)
Applied after the preflight reported SAFE (it was BLOCKED by the Facebook group scan from 11:11 to 14:14 IL; not forced). Mount, recreate (`--no-deps --force-recreate`), three code files and the enforced merge gate are live; the checks, rollback location and test counts are in the FIXES_TRACKER entry of the same date. The candidate `.py` copies and the compose diff were removed from `candidates/` once deployed. **Not applied:** the two index builds (production DDL, blocked pending explicit owner approval) and the drop of the old index (stage 3).


## I. Status 2026-11-08 (supersedes the "NOT applied" statements above)
* **Pin: APPLIED and VERIFIED.** Policy v1.1.0, sha256 `041c62d1f0b3a42e8f54765c7d5de7a8d79690af71fbe5fc289c61068fb91e9f`, pinned through `OFFER_POLICY_EXPECTED_SHA256` + `OFFER_POLICY_REQUIRE_PIN=1` (docker-compose); backend recreated 2026-11-07 07:46:39 IL; `maintenance/verify_policy_pin.py` reports `"ok": true` inside the container. The policy text itself is unchanged since the hash was computed (any edit needs the section 8 bump procedure).
* **Stage 1 indexes / stage 3 (old index drop): APPLIED** (2026-11-06 15:16-15:20 IL and 19:31 IL). **Restore: done** (4,673 net, FIXES_TRACKER #135/#138). **`populate_supplier_parts`: removed** (#136). **Candidate patches** for `merge_master_parts`, `bmw_oem_dedup`, `aliexpress_price_sync`: deployed (section C describes the pre-fix behaviour).
* **Open:** final QA on the last tree; D5b/D6/admin-upload fixes; the 21:30 IL 2026-11-08 `run_all_tasks`; car-parts.ie foreign-key drops (#140, owner decision); nothing is committed.
