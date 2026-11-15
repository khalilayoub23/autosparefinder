# Repair execution plan — cross-brand offer overwrites (NOT EXECUTED; design only, 2026-11-15)

Scope: only offers classified **VERIFIED_WRONG** in `repair_manifest.json` AND repair-ready with `repair.readiness = READY` (93 rows: owner-brand payload row strictly earlier than the foreign writer). The 36 `UNDO_CANDIDATE_NEEDS_SEPARATE_APPROVAL` rows (buggy-window undo to the pre-defect value) need their own owner decision; the 1,070 `NOT_READY` rows are never written. Everything else (AMBIGUOUS, MISSING_EVIDENCE, backup-only uncorroborated, VERIFIED_CORRECT) is untouched. Creating the missing brand offers is a SEPARATE step (policy-gated INSERT, see §5).

## 0. Preconditions (all must hold, else stop)
1. Owner approval of this plan + the exact manifest sha256 (`SHA256SUMS`). Not granted yet.
2. SAFE restart-style preflight (`pre_restart.sh`) — no AliExpress/eBay scan, no browser job, no thumbnail run; harvester state recorded. The car-parts.ie harvester need NOT be stopped (per-row preconditions below), but the 620ca69 importer fix must be live (it is: cpie_identity.py sha 72a8778a...).
3. Fresh backup of the target rows (`COPY (SELECT * FROM supplier_parts WHERE id = ANY(manifest ids)) TO ...`, sha256 recorded) BEFORE any write.
4. Re-run `f2_replay.py` + `f3_classify.py` immediately before apply and require the repair set to be a SUBSET of the approved manifest (rows whose live state changed since the manifest are dropped, never re-targeted).

## 1. Per-row apply (one transaction per row, offers locked `FOR UPDATE NOWAIT`)
Expected-current check (all must equal the manifest, else SKIP and count the reason): `id`, `supplier_id`, `part_id` (= current_part_id), `supplier_sku` (= article, no `~`), `price_ils` (= current.price), `supplier_url` (= current.url), `updated_at` (= current.updated_at, i.e. no later writer).
Write: `price_ils = target_price` (only when target_price is not null), `supplier_url = target_url`, `updated_at = NOW()`. Nothing else: part_id, supplier_sku, availability, warranty untouched.
Price change goes through `price_history_writer` (policy class C: an offer price change must leave a `price_history` row, source `cpie_overwrite_repair`).
Policy gate: `offer_policy.gate_or_exit` (fail closed) + `POLICY_BOUND_ACTORS` entry for the new actor; `--approval-ref`, `--expect-sha256` of the manifest.
Canary: first 20 rows, verify against the pre-apply copy, then the rest; abort on the first unexpected state.

## 2. After apply
`base_price` of the affected parts through `apply_base_price_rule` (never a new formula); second pass must change 0. Re-run replay classification: every applied row must now classify VERIFIED_CORRECT (current == owner-only state). Reconcile counts: applied + skipped = manifest rows.

## 3. Not covered here (separate remediation, see FIXES_TRACKER)
Customer-visible `supplier_sku` (`BRAND~article`), Opel importer URL overwrite, rollback coverage of the 3,858 moves (111 previous parts / 1,547 base_price changes).

## 4. Acceptance
0 rows changed outside the manifest; 0 part_id/sku changes; every applied row equals its target; second dry run lists 0 VERIFIED_WRONG repair-ready rows; price_history rows == price changes applied.

## 5. Missing brand offers (separate gated step)
For each `foreign_row_bare_write_events` pair classified VERIFIED_WRONG "MISSING_OFFER": INSERT one offer with key `BRAND~article` on the single candidate part, using the latest payload row of that brand (price, url) — through the importer's own `offer_key` rule, `offer_policy.gate_or_exit`, unique-index safe (`ON CONFLICT ON CONSTRAINT supplier_parts_supplier_id_supplier_sku_key DO NOTHING`). Alternatively wait for the harvester: the fixed importer already creates it on the brand's next harvest of that vehicle page.
