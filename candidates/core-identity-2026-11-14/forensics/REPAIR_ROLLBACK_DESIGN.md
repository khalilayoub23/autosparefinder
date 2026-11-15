# Repair-specific rollback design (NOT EXECUTED, not built into a tool yet)

The old `cpie_offer_ownership.py --rollback` is NOT used: it restores `part_id` only and refuses 111 rows; this repair changes price/url, never part_id.

## Source of truth
Per applied row the apply tool writes a result record: `offer_id, part_id, supplier_sku, before:{price_ils,supplier_url,updated_at}, after:{price_ils,supplier_url,updated_at}, price_history_id (if written), manifest_sha256, run_id`. Plus the full pre-apply COPY of the target rows (sha256). The result file is written BEFORE the transaction commits (write-ahead) and fsynced.

## Rollback (`--rollback --result F --expect-sha256 H`)
Per row, one transaction, `FOR UPDATE NOWAIT`:
1. Identity: row exists with the same `id`, `supplier_id`, `part_id`, `supplier_sku`.
2. Expected current == `after` for price_ils, supplier_url AND updated_at. If ANY differs (the harvester/importer wrote a newer value) -> SKIP, count `newer_legitimate_write`; never overwrite newer data.
3. Write `before.price_ils`, `before.supplier_url`, `updated_at = NOW()`; delete nothing; record the `price_history` rows created by the repair as reversed by a compensating row (source `cpie_overwrite_repair_rollback`), never by deleting history.
4. Refuse the whole run if the result file sha256 != expected, a row id is not in the result, or any row is in an unexpected state class (ambiguous -> skip + report; exit non-zero if skipped > 0 so a partial rollback is never reported complete).
Independent verification: after rollback, for every restored row current == `before`; for every skipped row current == its newer value and a later `updated_at`; counts restored + skipped == applied; the pre-apply COPY compared row by row (excluding skipped).

## Tests required before use (sandbox_backend only)
apply -> rollback exact; apply -> importer write -> rollback skips the row; wrong sha refused; row with changed part_id refused; idempotent second rollback restores 0.
