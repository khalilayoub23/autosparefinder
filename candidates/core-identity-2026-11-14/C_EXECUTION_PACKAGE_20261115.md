# Workstream C — execution package (owner approved OPTION 1 on 2026-11-15; execution of the production cutover NOT yet approved)
## Scope (dry run, live data, harvester running so counts move by a few rows per run)
Manifest /app/state/cpie_ownership/cpie_ownership_manifest_20261115T052638Z.json sha256 7af358d7b41c15eb06b3ec63f609d888aead6ccdec0b41d101fe1e10f06e63b6
8,612 articles with >=2 brands: KEEP 1,383 | MOVE 4,021 (offer re-pointed, exact product-URL evidence) | HOLD 3,207 (1,212 URL not unique, 1,914 category-only, 81 target not unique, 1 no offer). HOLD rows are untouched.
Brand unresolved rows (importer will HOLD them going forward): 10,268 of 1,103,742 (0.93%).
## What changes in production at cutover (all in one maintenance window; the harvester is stopped for all of it)
1. Harvester stop (supervised loop `_car_parts_ie_harvester_loop`, process car_parts_ie_flaresolverr_harvester.py) after SAFE preflight (`pre_restart.sh` / restart_preflight: not inside 10:00-14:00 IL, no run_all_tasks, no AliExpress/eBay scan). Wait for the running importer subprocess to finish (flock).
2. Fresh manifest from the quiesced state; read the number of MOVEs; `--apply` with --expect-sha256 + --approval-ref (4,021 offers: only supplier_parts.part_id changes; no price/url/delete). Result file = rollback source (previous part_id per offer).
3. Deploy code: candidates/core-identity-2026-11-14/code/{cpie_identity.py, car_parts_ie_import_generic.py, offer_policy.py (+ actor), supplier_ordering.py (bare article in PO text)} -> backend/ (bind mount); offer_policy.py/supplier_ordering.py are in-process modules => the backend needs a guarded recreate (code-only deploy = `--force-recreate`, via guarded_restart.sh). Policy FILE and its sha pin are NOT touched.
4. Harvester restart; observe one real import batch; verify no cross-brand overwrite (a second brand on an owned bare article lands on `BRAND~article` keys; the owner part's name/price unchanged).
## Rollback
Result file: UPDATE supplier_parts SET part_id=<previous_part_id> for each applied offer (tool: same file, reverse mode to be added before the run); code: git revert of the deploy commit + the same guarded recreate. Backups: catalog daily 2026-11-14 04:02 (2.4 GB gz) + a fresh pg_dump of supplier_parts before step 2.
## Tests (sandbox_backend, run 2026-11-15): candidates/.../test_cpie_identity.py 6 passed (same number two brands, repeated import, order independence, fail-closed, token reversible, brand from vocabulary not vehicle make).
## Not yet done (honest list)
- reverse (rollback) mode of the apply tool; tests/test_offer_policy.py coverage for the new bound actor; DB-backed importer test in sandbox_postgres (two-brand harvest file twice, both orders); `Article №:` prefix defect; single-row overwrite measurement from backups; consultant review of THIS package.
