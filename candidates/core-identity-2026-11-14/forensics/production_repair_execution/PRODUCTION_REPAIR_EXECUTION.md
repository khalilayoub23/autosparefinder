# Production execution of the cross-brand offer repair (2026-11-15) — supersedes the FAIL in ../production_repair_audit/ (kept unchanged)

## Result
**PASS for the offer repair, with a smaller count than the brief:** 90 of 93 READY offers repaired (canary 20 + rest 70); 3 preserved because the owner brand itself rewrote them after the manifest. Reconciliation of the 93 (`final_reconciliation_93.json`, as of 20:5x IL): 89 VERIFIED_REPAIRED, 1 REPAIRED_THEN_OWNER_REWRITE (58984b1f: repaired 20:43:19 IL, BRISK rewrote it 20:46:54 IL keeping the target price), 3 PRESERVED_OWNER_WRITE (856bf4de NRF, ebde0803 FEBI BILSTEIN, c5dbbb3f KAMOKA). Not 92: two more rows were healed by their owner brands at 18:30 UTC, between the 19:10 CET check and the run.
Window 20:43:17–20:43:26 IL, tool `cpie_overwrite_repair.py` sha256 0deaa5333d1a8882 (commit 534b73e), manifest sha256 65595265…3158 (93 READY, pinned).
**NOT done:** `--rebase` (not run in production, see below) and the plan §2 replay re-classification (f2/f3 not re-run; replaced by the exact per-row compare-and-swap guard). A deviation from plan §2.

## Gates and evidence
- Preflight (in-container) read SAFE immediately before canary (20:43:17) and again before rest (20:43:21); the thumbnail and meili jobs were never touched. Outside 21:30 run_all_tasks. Importer fix live: cpie_identity.py 72a8778a…. 0 customer references (carts, orders, wishlist, reviews, watches) to the 93 offers/parts before and after.
- Canary 20: acceptance passed (repaired, only price_ils/supplier_url/updated_at on offers and updated_at on parts changed, price_history 20, journal committed 20, rest scope still NOT_APPLIED). Rest 70: same, 0 skips. Files + sha256: `SHA256SUMS` (journals, results, full-row pre-apply backups, full-row diffs, final verifies).
- Independent post-run read: 90/90 live == journal `after` == manifest target (as of 20:45 IL); 89 still so at 20:50 IL. 3,858 earlier moves intact; 0 Car-Parts.ie parts with >1 offer; 36 UNDO, 1,070 NOT_READY untouched by this run (9 NOT_READY rows differ from the manifest: all written by car_parts_ie_import at the moment of their own updated_at, outside the window); 90 `cpie_overwrite_repair` price_history rows = the 90 price changes, 0 outside.
- Preserved rows: writers confirmed by `part_identity.title_brand` on the payload rows (NRF 55327, FEBI 44949, KAMOKA 6046 x2): all the owner brand, so the importer fix holds. The four parts_catalog rows stamped 20:43:25 outside my 90 parts are not in this repair's id set (the tool writes by id only).
- Rollback: 90/90 rows have exact before/after + write-ahead journal; guarded (a later write is skipped, never overwritten; 58984b1f will be skipped). Tested in sandbox on the same code.

## Sandbox (45/45 on 0deaa533): offer apply/rollback/idempotency/guards/concurrency, journal rollback, full-row diff, exclusion path, and the `--rebase` value path (valid basis = policy net of repaired price; legit newer write and locked part skipped; second/third pass 0; rollback skips a newer write; unrelated parts untouched).

## Deviation: rebase not run
After the repair 76 of the 90 parts already equal the rule's base_price; 14 differ by stale older owner values (not the foreign price): 033da72c 0b811425 2a39e944 30aaabaf 3c69b249 3f785c50 482dc008 5ea72820 72b090f5 8921cf18 9bf26a62 aa3d6310 be186bed f1e77389 (id prefixes). base_price is internal (customer prices come from the offer via price_policy), so rebase would be a wider correction, not repair-caused; held as follow-up.
## Limits
Consultant reviews were transcript-based (cannot query production). The 36 UNDO / 1,070 NOT_READY / missing brand offers remain outside this mission.
