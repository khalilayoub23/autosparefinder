# Cross-brand offer overwrite forensics — report (2026-11-15, Israel evening; READ-ONLY phase, production unchanged)

Status: **PARTIAL — FORENSIC MANIFEST COMPLETE FOR THE EVIDENCE WINDOW; the full population is a LOWER BOUND** (see §4).

## 1. Method (evidence, not inference)
The importer's own inputs survive: every batch's harvest payload (`/tmp/*_cpie.json`, 3,489 batches since the container start; archived at `/app/state/cpie_ownership/forensics/payloads_and_import_logs_20261115.tar.gz`) and its import log (end time). Starting from the catalog backups of 2026-11-14 and 2026-11-15 (03:00 UTC), all batches are replayed in import order through three importer behaviours (old < 06:46:21Z, buggy 06:46:21–16:56:32Z, fixed after commit 620ca69). A replayed offer is "explained" only if its (price,url) equals the live offer AND the live `updated_at` lies inside the writing batch's [payload mtime, log mtime] (moved offers: pre-move `updated_at`). Identity = `part_identity.title_brand(name, article)` on the writing row and on the offer's part; a shared article, price, description or listing page is never used as identity.
Validation of the method: replay from the 11-14 backup reproduces the 11-15 backup state for 146,162 of 146,383 offers (99.85%); the replay reproduces 148,631 of 159,524 live offers (93.2%; the rest are in MISSING_EVIDENCE). Scripts: `f1_collect.py` (first look), `f2_replay.py`, `f3_classify.py`, `f4_reconcile.py`.

## 2. The iwis / AIC case (VERIFIED, with one ASSUMED point)
- Offer 7b3b7b18 (`59008Set`) sits on `CHEVR-59008Set` (iwis Motorsysteme). Price 354.44 since 2026-11-01 (in both backups), URL `.../chevrolet/volt-2011/timing-chain`.
- 2026-11-15 10:32:58.5 UTC (12:32:58 IL): row 82 of `chevrolet__chevrolet_volt-2011_cpie.json` (AIC, "AIC 59008Set CHEVROLET Volt I timing chain", 195.19 EUR × 3.48 = 679.26, the same listing URL) wrote the bare offer: `price_history` 354.44 → 679.26; offer `updated_at` 10:32:58.50 is inside the batch window (payload 10:32:15, log 10:33:08). Defect path: `same_url` true → bare key although the holder (iwis) is a different resolved brand.
- AIC part `CHEVR-AIC~59008Set` (created 10:32:58.49) has **no offer**. Its correct offer would be key `AIC~59008Set`, price 679.26, that URL (exact payload row).
- Correct ownership/price: the live 679.26 belongs to AIC (VERIFIED). The iwis price 354.44 is the value before the write; that it is iwis's own price is **ASSUMED** (no retained payload has an iwis 59008Set row; backups only prove it predates the defect).

## 3. Classification counts (exposed population = 5,838 bare offers; replayed bare offers 159,524)
| Class | Offers |
|---|---|
| VERIFIED_WRONG | **1,199** |
| VERIFIED_CORRECT | 2,596 |
| AMBIGUOUS | 1,279 |
| MISSING_EVIDENCE | 764 |
| not exposed (no other-brand part or row; foreign writer impossible) | 153,686 |
Total 159,524 = 5,838 + 153,686. (The 115 payload articles with no live bare offer are excluded; 159,639 payload articles.)

VERIFIED_WRONG by cause: **listing-page same-URL defect (buggy importer 06:46–16:56Z): 44** · **original C defect (old importer, last-writer-wins on the bare key, never repaired by the moves): 1,155**. Value impact: price differs from the owner-only state on 876 of them; URL-only on 323.
Repair readiness of the 1,199 (independent review applied): **READY 93** (price/URL from the owner brand's own payload row, strictly earlier than the foreign writer, price_history not contradicting) · **UNDO_CANDIDATE_NEEDS_SEPARATE_APPROVAL 36** (restore the pre-defect value; ownership of that prior value not proven) · **NOT_READY 1,070** (8 buggy-window owner-row targets whose batch order is unproven + 1,062 old-importer rows: target is a backup value and backups contain foreign-brand values, or order/price_history unproven).
Price reconstruction: owner-row price known: 101 rows (93 ready); ownership known but original price not provable: 1,098 rows.
Confirmed overwritten offers: 1,199 (current state wrong); further overwrites later healed by the owner brand are not counted (not current damage).
Confirmed missing offers (distinct (article, brand) pairs, brand resolved, brand part(s) own no Car-Parts.ie offer): **32 from the buggy window**, 1,429 from the old importer (original defect aftermath); 76+418 pairs already have an offer; 2+96 have no part. Affected: 138+ owner brands on the 1,199 offers; parts = offers (1,199 distinct parts).
AMBIGUOUS includes: foreign value equals the snapshot value with no owner row (the snapshot may itself be foreign), and writers with an unresolved brand. MISSING_EVIDENCE: live value not reproduced by the retained payloads, or no payload wrote it since 11-14 (state predates evidence).
Not independent: old-mode VERIFIED_CORRECT rows are labelled `consistent_not_independent` in the manifest (the old importer renamed the part with the same row).

## 4. Reconciliation (reconciliation.json, live at 17:5x UTC; production moves with the harvester)
Car-Parts.ie offers 781,833 = 780,577 bare + 1,256 brand-qualified. Resolved multi-brand articles among parts: 8,934 (8,933 with a bare offer): 4,590 of them have NO payload row since 11-14 03:00 UTC, so they cannot be evaluated here (state predates the evidence; the C manifest HOLD set of 3,262 is the same universe); 1,495 manifest offers were exposed only through payload rows. **Therefore 1,199 is a lower bound for the whole catalogue**, exact only inside the evidence window. Differences vs the first run (1,203 → 1,199 etc.) come from the harvester writing during the analysis and the independent-review corrections; every figure is from one run (`repair_manifest.json.gz`, summary.generated_utc).
The 3,858 verified moves were not modified; 0 production writes in this phase (read-only transactions; evidence copies went to the state volume only).

## 5. Independent review (Opus): findings and resolution
PASS: evidence completeness; 84/84 re-derived VERIFIED_WRONG rows; iwis/AIC; moved-offer ordering. Applied: 160 identical-value "correct" rows → AMBIGUOUS; 126 unresolved-writer rows now exposed → AMBIGUOUS; 751 move-reset MISSING_EVIDENCE rows re-tested with the pre-move timestamp; backup-sourced and unproven-order targets → NOT_READY; "corroborated" renamed informational; old-mode CORRECT labelled not independent; missing offers recounted per distinct brand pair. Not fixed (noted): straddling batches keep both modes (10 batches, no class changes); importer holds/exception skips not replayed. Disagreements remaining: none open.

## 6. Artifacts
`repair_manifest.json.gz` (per row: offer/part ids, brand, current + snapshot values, writer file/row/times, counterfactual, price_history, repair readiness, reason), `reconciliation.json`, `f1..f4*.py`, `REPAIR_EXECUTION_PLAN.md`, `REPAIR_ROLLBACK_DESIGN.md`, `SHA256SUMS`.
