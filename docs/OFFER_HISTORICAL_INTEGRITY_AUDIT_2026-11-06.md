# Offer historical integrity audit — 2026-11-06

Read-only audit of `supplier_parts` and `price_history` over time, using the catalog backup dumps. Nothing was written to production, nothing was restored, no backup or extract was deleted. Labels: **CONFIRMED** (directly shown by data), **PROBABLE** (mechanism consistent with evidence, no direct log), **UNKNOWN**.

## 1. Sources
Plain-SQL dumps in `/opt/autosparefinder/backups/catalog/` (made by `autospare_postgres_backup_catalog`, cron 03:00): monthly 202605 (2026-05-31, earliest), 202606 (06-30), 202607 (07-31), 202608, 202609, 202610; daily 20261102, 20261105, 20261106. Live DB read at 2026-11-06 ~14:23 (read-only `COPY`). No other backups exist (checked docker volumes and /root).

## 2. Method
`supplier_parts` columns (id, supplier_id, part_id, supplier_sku, price_ils, is_available, part_type, created_at, updated_at) were streamed out of each dump (`zcat | awk`) and sorted by id (LC_ALL=C). Per consecutive pair: `comm -23` gives ids in the older snapshot but not the newer one ("missing"); each missing row is then classified by one awk pass over the newer snapshot, in this order:
1. **REKEYED** – same supplier + same SKU + same part exists under a new id.
2. **REMAPPED** – same supplier + same SKU exists on a different part.
3. **SAME_SUPPLIER_PART_other_sku** – same supplier + same part exists with another SKU (none occurred).
4. **SUPPLIER_CHANGED** – same part + same SKU under another supplier.
5. **GENUINELY_ABSENT** – none of the above (absent by id, supplier+SKU, supplier+part and part+SKU). This means "not found by those keys", **not** "proven lost" — see section 5 for what each group really is.

An earlier in-memory Python run produced identical counts for the first two pairs (cross-check). The 202607→202608 python run was killed for memory safety and re-done with the streaming method. The streaming script is `classify.sh` in the scratchpad.

## 3. Extraction verification
| snapshot | extract rows (`wc -l`) | independent dump COPY count (`verify.sh`) |
|---|---|---|
| 202605 | 573,713 | 573,713 |
| 202606 | 3,761,587 | 3,761,587 |
| 202607 | 4,150,460 | 4,150,460 |
| 202608 | 4,213,706 | 4,213,706 |
| 202609 | 4,230,067 | 4,230,067 |
| 202610 | 4,245,253 | 4,245,253 |
| 20261102 | 4,247,688 | 4,247,688 |
| 20261105 | 4,248,850 | not independently counted |
| 20261106 | 4,248,896 | not independently counted |
| live | 4,248,896 | n/a |

The 20261106 extract and the live extract have the **same sha256** (`5aa5ff54…`), i.e. the 03:00 dump equals the live table on id, supplier, part, SKU, price, availability, type and timestamps. That validates the extraction method. sha256 of every extract is in `scratchpad/extract_sha256.txt` (copied below); extracts are kept, nothing deleted.

```
3c4f9b5877ec53bb68d1bd34828433ff887f0a23e64da0f493cfb5de655f48c1  autospare-202605.tsv
4b29955ec5223278315f3a5c8d1268404f1bd7dfbc21c8ea25edf931f83e0d11  autospare-202606.tsv
755b62921c7d951335bffc48e9998c34cb1f19baa0faac7ab53102a7e0c66faa  autospare-202607.tsv
65f5649583c02604b97961b81ea037f1c1039ae080a94751c4747e01dfea0b1d  autospare-202608.tsv
477213094b3bfd9216868ba9453daff68a5a024d7010bbfa708db9b7b40323ef  autospare-202609.tsv
948c8a131701675cea83dc09983290d838c8ae9f06640a15d9976ca3a5dd675c  autospare-202610.tsv
568be1846998dd17f813bcd950ee84de362947468ccc79899e1728985eac1423  autospare-20261102.tsv
153a4a4abf44bf499ae93238581b6ffaf6ac3938cf262b5e18bc128f2520a74c  autospare-20261105.tsv
5aa5ff54660aa46e4b0646c74c87290e922f720278a794c8913ebe4d1b950b4e  autospare-20261106.tsv
5aa5ff54660aa46e4b0646c74c87290e922f720278a794c8913ebe4d1b950b4e  live.tsv
cd9555f6f80f04ff9c578743f13db4714a3141855343f267f771e75246198441  ph_autospare-202605.tsv
a9339208bc0bd6fe3f5b86c1b79b136001879ddfc1cb73ab70925d1252288865  ph_autospare-20261102.tsv
51b0fdff0aaa7c9f4c6ce0480fe9f17f3e3e7c4432157a0121289894096485a4  ph_live.tsv
```
Scratchpad: `/tmp/claude-0/-opt-autosparefinder/1bf54c6d-3b18-4254-9ed2-c0212016ebf4/scratchpad/` (`snap/` ~6 GB, `pairs/` per-pair missing ids, classified ids, per-supplier counts, examples).

## 4. Per-pair results (offers, `supplier_parts`)
| pair | missing ids | new-only ids | classification of the missing |
|---|---|---|---|
| 202605 → 202606 | 5,617 | 3,193,491 | REKEYED 1,426; GENUINELY_ABSENT 4,191 |
| 202606 → 202607 | 7,971 | 396,844 | GENUINELY_ABSENT 7,968; REMAPPED 3 |
| 202607 → 202608 | 7 | 63,253 | ABSENT 6; REMAPPED 1 (all Car-Parts.ie) |
| 202608 → 202609 | 2 | 16,363 | ABSENT 2 (Car-Parts.ie) |
| 202609 → 202610 | 6 | 15,192 | ABSENT 6 (Car-Parts.ie) |
| 202610 → 20261102 | 0 | 2,435 | – |
| 20261102 → 20261105 | 0 | 1,162 | – |
| 20261105 → 20261106 | 0 | 46 | – |
| 20261106 → live | 0 | 0 | – |
| 202605 → live (direct) | 12,407 | 3,687,590 | ABSENT 10,980; REKEYED 1,426; SUPPLIER_CHANGED 1 |
| 202606 → live (direct) | 7,986 | 495,295 | ABSENT 7,822; REKEYED 89; REMAPPED 74; SUPPLIER_CHANGED 1 |

Offer count never falls between consecutive snapshots in aggregate; the net rise is import growth. All offer disappearances happened between 2026-05-31 and 2026-07-31 (plus 15 small ones in Aug–Oct); nothing was lost after the 2026-10-31 dump.

By supplier (names resolved read-only):
- 202605→202606 absent: Official Manufacturer Sites (`3b038e45`, inactive pseudo-supplier, country Global) 4,088; SNG Barratt 88; Motorstore IL 15. Re-keyed: AutoParts Pro IL 1,426.
- 202606→202607 absent: SNG Barratt 6,694; BMW Delek Motors 485; Car-Parts.ie 349 (+3 remapped); AutoParts Pro IL 296; Motorstore IL 50; Official Manufacturer Sites 44; WEY Official Importer Israel 38; Kia Official Importer Israel 4; Champion Motors IL 2; eBay Motors 2; Subaru Official Importer Israel 2; Geo Mobility – Zeekr Israel 2; 1 more (other).

## 5. What the absences actually are (live DB cross-check, read-only)
**Window 202605→202606, 4,191 "absent" offers — hard-deleted catalog parts. CONFIRMED that the part rows no longer exist in `parts_catalog` (4,191 of 4,191: `part_row_gone`) so the offers went with them (FK cascade).** The 202605 dump shows 4,176 of those parts (15 ids not located) were all manufacturer **Chery**, active on 2026-05-31, created 2026-04-13 (4,088, Official Manufacturer Sites offers) or 2026-05-28 (88, SNG Barratt) plus 8 Motorstore. Chery now has 6,033 active parts from other suppliers, so the brand was not wiped, the old Chery rows were replaced. The deleting operation: **UNKNOWN / PROBABLE** — candidate code is a brand replace (`ai_catalog_builder.py` `DELETE FROM parts_catalog WHERE LOWER(manufacturer)=…`, since fixed to refuse when offers exist); no log ties it to these rows. The affected offers were mostly `Official Manufacturer Sites` rows (inactive supplier, 0 available) and a few SNG/Motorstore ones.

**Window 202605→202606, 1,426 re-keyed offers (AutoParts Pro IL).** Same supplier+SKU+part reappear under a new id. CONFIRMED not a loss of the offer; the old id (and by FK cascade its `price_history`) is gone — see section 6.

**Window 202606→202607, 7,971 absent offers — collapsed into a merged part. CONFIRMED for 7,970 of 7,971:** each offer's part is now soft-deleted with `specifications.dedup_merged_into = <canonical part>` and the canonical part holds an offer from the **same supplier**. For 7,970 the canonical offer has a **different SKU** (6,992 also a different type string, but the type vocabulary was later normalised, e.g. `Original` → `oem`, so the type comparison is not reliable). One offer (Car-Parts.ie, SKU `110-995`) has no same-supplier offer on the canonical part. Probable cause: **`maintenance/merge_master_parts.py` conflict branch, which in the committed version ran `DELETE FROM supplier_parts WHERE id = $1` after lifting the cheaper price** — PROBABLE (the soft-merge marker and the same-supplier canonical offer are exactly its signature; the script was introduced 2026-07-29 per FIXES_TRACKER line 4130; the 07-31 dump falls two days later; but no execution log of those deletions was found, and the pair window is a month wide). `bmw_oem_dedup.py` (run 2026-07-12 per tracker line 4527) is a candidate only for the 485 BMW Delek Motors rows — UNKNOWN which script touched which row. The fixed versions (offer kept, no deletes) went live 2026-11-06.
Per supplier within the 7,970: SNG Barratt 6,694; BMW Delek Motors 485; Car-Parts.ie 349/351; AutoParts Pro IL 296; Motorstore IL 50; Official Manufacturer Sites 44; WEY 38; Kia 4; others 2 each.
**Whether each of these was a genuinely distinct variant or a true duplicate is not provable from this data** (different SKU alone is not proof of a different product, per the policy); under policy v1.0.0 they are class B/F candidates, not class D, so the deletion was not policy-compliant evidence of duplication.

**Aug–Oct (15 offers, Car-Parts.ie).** Not traced; not significant.

## 6. price_history
| dump | price_history rows |
|---|---|
| 202605 | 9,974,179 |
| 202606 | 8,654,323 |
| 202607 | 8,663,700 |
| 202608 | 8,666,295 |
| 202609 | 8,668,497 |
| 202610 | 8,670,726 |
| 20261102 | 8,670,809 |
| live | 8,671,037 |

**CONFIRMED:** 1,338,898 rows present on 2026-05-31 are absent today (35,756 rows in live are newer). The count dropped by 1,319,856 between the 05-31 and 06-30 dumps and has only grown since.
- 136,356 of the missing rows belong to offers that no longer exist (all supplier Official Manufacturer Sites; created April–May) — consistent with FK cascade from the deleted Chery parts/offers (`price_history.supplier_part_id` is `ON DELETE CASCADE`).
- 1,202,542 belong to offers that **still exist** (1,202,537 Official Manufacturer Sites + 5 eBay Motors), 1,195,505 of them created in April 2026. These cannot be cascade: history rows were removed while the offer stayed.
- Row content: 1,323,855 of the missing rows are real price changes from source `boaz_sync` (e.g. 107.00 → 105.76 ILS, −1.16%); 15,043 are zero-delta. All 8,635,281 rows that were kept have non-zero change.
- Documented operation (PROBABLE): FIXES_TRACKER line 4847, session 2026-06-17: "price_history bloat: 1.2M zero-delta rows deleted … 9.8M → 8.6M rows". Date window and the row-count drop match; **the stated criterion (zero-delta) is contradicted by the content of the deleted rows** (99% were non-zero changes). No `DELETE FROM price_history` statement was found in `backend/*.py`, so the operation was an ad-hoc SQL run.
- The deleted history is internal `boaz_sync` data of the pseudo-supplier Official Manufacturer Sites (inactive, 0 available offers, not customer visible); it is not an input to customer pricing (`price_policy` reads offers, not history).

## 7. What is proven and what is not
**Proven (CONFIRMED):** offers disappeared only between 2026-05-31 and 2026-07-31 (plus 15 later); 4,191 offers vanished because their Chery catalog parts were hard-deleted; 7,970 offers vanished while their part was soft-merged into a canonical part that holds a different-SKU offer from the same supplier; 1,426 offers were re-keyed, not lost; ~1.34M price_history rows disappeared in the 05-31→06-30 window; the 11-06 dump equals live; nothing was lost after 2026-10-31.
**Probable:** merge_master_parts conflict branch as the cause of the 7,970; a June 2026 cleanup as the cause of the price_history deletion (but with a wrong stated criterion).
**Not proven / unknown:** which operation hard-deleted the Chery parts; whether the 7,970 merged offers were distinct variants; offers created and deleted between two dumps (never visible); price_history rows created after 2026-05-31 and deleted before the next dump; anything before 2026-05-31 (no earlier dump); per-day timing (dumps are monthly in this window); the 20261105/06 independent dump row counts.

## 8. Restoration proposal (NOT applied; needs owner approval)
Scope limited to CONFIRMED losses.
1. **7,970 merge-collapsed offers (priority).** Build a staging table from the 202606 dump (id, supplier, original part, SKU, price, type) mapped to the canonical part (`dedup_merged_into`). Re-insert only rows where `(supplier_id, supplier_sku)` is absent, through `offer_policy.assert_write_allowed` as class B/F inserts, `is_available=false`, original id reused, price from the dump marked stale until the supplier's own sync refreshes it, provenance stamp `specifications`-side/audit row. Requires the `migration_variant_identity.sql` indexes first (today `uq_supplier_parts_part_supplier` forbids a second offer of the same supplier on the canonical part). Reversible: delete by the staged id list. Run as a dry-run report first.
2. **4,191 Chery offers.** Recommend **not restoring**: pseudo-supplier rows (Official Manufacturer Sites, inactive, 0 available) and the replaced catalog parts exist now. Owner decision.
3. **1.34M price_history rows.** Optional: re-insert from the 202605 dump (original ids, `ON CONFLICT DO NOTHING`, only for surviving offers), reversible by id list. Low value (internal, not customer-visible); owner decision.
No change to production is made by this document.

## 9. Restoration (#133) — status log
- 2026-11-06 ~18:25 IL: owner approved restoring the merge-collapsed offers (item 1 of section 8). Tool: `backend/maintenance/restore_merged_offers_133.py`, isolated test 12 passed / 0 failed. Input `candidates/offer-policy-2026-11-06/restore_merged_offers_133_input.tsv` (7,971 rows, sha256 `1778da76…d46df5bdaabfd46`).
- **Production DRY RUN (read-only, `candidates/offer-policy-2026-11-06/restore133_prod_dryrun_1.txt`):** 7,971 input rows → **eligible 4,963** (SNG Barratt 4,932; AutoParts Pro IL 31) · `blocked_by_variant_unique_index` 2,846 · `supplier_sku_already_present` 162. `--apply` still refuses: `uq_supplier_parts_part_supplier` is present (the stage-3 drop is a separate step); the replacement indexes are valid.
- The 2,846 blocked rows hold the same normalised variant (or both untyped) as an offer the same supplier already has on the canonical part; they stay unrestored by design (one offer per supplier+part+variant). The 162 SKU-present rows already exist live under their (supplier, SKU).
- Pre-restore snapshot: `candidates/offer-policy-2026-11-06/restore133_pre_snapshot.json` (supplier_parts 4,248,896; available 1,523,234; price_history 8,671,037; max updated_at 2026-11-06 00:27:36 — unchanged from the baseline).
- 2026-11-06 ~19:35 IL, after the old index drop (19:31): second production dry run (`restore133_prod_dryrun_2_after_index_drop.txt`) still eligible 4,963 / blocked 2,846 / sku-present 162 and `apply_would_refuse: null`.
- **Apply 1 (`--limit 500`, run `37373d6a3c52`):** 322 eligible → **320 inserted**, 2 `blocked_by_unique_index_on_insert` (two merged losers of the same canonical part+supplier+variant in one run: the first inserted wins, the second hits `uq_sp_part_supplier_*` and is skipped by `ON CONFLICT DO NOTHING`; no error). Verified: supplier_parts 4,248,896 → 4,249,216 (+320 exactly); available count unchanged 1,523,234; price_history unchanged 8,671,037; all 320 manifest ids present, 0 available, 320 `availability='out_of_stock'`, 0 price_history rows; manifest 320 ids == 320 inserted (`candidates/offer-policy-2026-11-06/restore_merged_offers_133_manifest_37373d6a3c52.json`). The remaining ~4,640 eligible rows were NOT applied yet (execution of the next run was blocked by the tool permission classifier); the scratch deletion has not been done.
- **FINAL APPLY OUTCOME (2026-11-06 evening IL).** Three production runs (all `--apply`, batch 500, policy 1.0.0): `37373d6a3c52` inserted 320 (2 blocked on insert); `f11523b4dcb0` inserted 892 (6 blocked); `f72e60c6a326` inserted 3,468 (140 blocked). **Total inserted 4,680**; manifests in `candidates/offer-policy-2026-11-06/restore_merged_offers_133_manifest_*.json` (4,680 ids, 4,680 unique).
- **Reconciliation (read-only, vs T0 snapshot 2026-11-06 17:24 UTC):** supplier_parts 4,248,896 → 4,253,576 (+4,680 exactly); per-supplier counts = T0 + restored for all 72 suppliers (SNG Barratt +4,673, AutoParts Pro IL +7; 0 mismatches); available 1,523,234 unchanged; price_history 8,671,037 unchanged (0 rows for the restored ids); all 4,680 present, 0 available, 4,680 `availability='out_of_stock'`; rows other than the restored ones with updated_at or created_at after T0: **0** (no drift). Restored rows with NULL `price_ils`: 998 — identical to the 998 NULL-price rows in the 2026-06-30 dump input (0 differences in price_ils between the 4,680 restored rows and the source), so faithful, not dropped. Customer-visible restored offers (search predicate: available + active supplier + not a pseudo-supplier + non-empty url): **0**.
- **Final dry run** (`restore133_prod_dryrun_3_final.txt`): eligible **0**; `original_id_already_present` 4,680 (the restored ones); `blocked_by_variant_unique_index` 3,129 = the 2,846 pre-blocked + 283 rows that were eligible at the first dry run but whose supplier+part+variant slot on the canonical part was then taken by a sibling restored offer (one offer per supplier+part+variant); `supplier_sku_already_present` 162. Total 7,971. Rollback was not run; the manifests are complete (id count == inserted count).
- Unrestored by design: 3,129 + 162 = 3,291 of the 7,971 input rows (the offer's (supplier, part, variant) slot or its SKU is already held by a live offer).
- **Scratch cleanup (owner-approved):** deleted only under the session scratchpad (`snap/` 9.4 GB, `pairs/` 189 MB, `cmp_*.json/.err`): scratchpad 9.6 GB (10,012,136 KB) → 15 MB (14,940 KB), ~9.5 GB freed; root disk used 149 G → 139 G. Kept: `candidates/offer-policy-2026-11-06/historical_audit_evidence/` (per-pair summaries, sha256 list, scripts), the restore input TSV (sha256 `1778da76…`), the three manifests, the dry-run/apply outputs, and all source dumps under `/opt/autosparefinder/backups/` (untouched). The extracts can be regenerated from the dumps with `historical_audit_evidence/classify.sh` method if needed.

## 10. Final verified reconciliation (re-derived 2026-11-07 from the manifests, the dumps and the live DB; earlier numbers not trusted)
Production reads only. Live state used: supplier_parts 4,253,569 (4,248,896 + 4,673), available 1,523,234, price_history 8,671,037, 0 rows with a Hebrew `part_type`. Inputs: 3 manifests (4,680 ids, all unique) minus `restore_merged_offers_133_manifest_subset7_hebrew_synonym.json` (7 ids rolled back after they collided). Dumps: `autospare-202605.sql.gz` sha256 `e9326d5e4017baf9d2f07eeb02da9f5e9ac64bcb10939319864e46daa4850ad7`, `autospare-202606.sql.gz` sha256 `5bb91ac04d2528c869482dbcdcc62fa42ee55956dba31ccb8d21898b58587d75`. Per-row verdicts: `candidates/offer-policy-2026-11-06/restore133_final_verdicts_7971.tsv`.

### 10.1 The 7,971 merge-collapsed offers (restore input, sha256 `1778da76…d46df5bdaabfd46`)
| Bucket | Rows | Evidence | Disposition |
|---|---|---|---|
| Restored, net | **4,673** | all present, 0 available, 4,673 `availability='out_of_stock'`, 0 price_history rows, price_ils equal to the source row (998 NULL in source and in DB) | already represented / DONE |
| Rolled back (Hebrew label `מקורי` collided with the canonical part's `oem` offer) | 7 | 0 present; now `blocked_by_variant_unique_index` | intentionally excluded, blocked by variant collision (correct) |
| Blocked, variant slot held by a pre-existing live offer | 2,877 (incl. the 7) = 764 `oem` + 1,296 `aftermarket` + 817 both untyped | live offer of the same supplier+canonical part+variant exists | blocked by variant collision; storing both would need a schema change (a SKU in the unique key) — not proposed |
| Blocked, slot taken by a sibling offer restored by #133 | 259 = 248 `oem` + 11 `aftermarket` | two merged losers of one canonical part; first inserted wins | blocked by variant collision |
| `(supplier_id, supplier_sku)` already live | 162 = 89 on the original part + 73 on another part | exists under that supplier+SKU | already represented |
| Canonical absent / unrecognised label / ambiguous | 0 | | – |
| **Total** | **7,971** = 4,673 + 2,877 + 259 + 162 | | |
`unrestored` = 3,298 (the 7 are inside the 2,877). With the central canonical-variant module the 31 Hebrew-label input rows are all blocked (7 had been inserted; 24 were already blocked) and **0 rows are eligible**; canonicalisation can only merge variant identities, never split them, so no previously blocked row becomes eligible. A re-run of the dry run on the same input is idempotent.

### 10.2 Offers lost between 2026-05-31 and now that are NOT in the restore input
| Bucket | Rows | Evidence | Disposition |
|---|---|---|---|
| Chery catalog parts hard-deleted with their offers (May→June window) | 4,191 = 4,088 Official Manufacturer Sites + 88 SNG Barratt + 15 Motorstore IL | part rows absent from `parts_catalog`; in the May dump 4,135 priced, 100 flagged available (supplier inactive, so never orderable) | missing; recoverable only by re-creating the catalog part and the offer. 3,326 of 4,174 Chery OEMs now exist as live parts but only 30 have an offer. Intentionally excluded by owner decision 2026-11-06 (pseudo-supplier, replaced parts). UNKNOWN which routine deleted them |
| Re-keyed AutoParts Pro IL offers | 1,426 | same supplier+SKU+part under a new id | already represented |
| Supplier changed (same part+SKU under another supplier) | 1 | | ambiguous, no action |
| Absent merge-window offers (non-Chery) | 2,116 | all inside the 7,971 input (counted in 10.1) | see 10.1 |
(6,307 offers absent by id and by all keys = 4,191 + 2,116; with the re-keyed 1,426 and the 1 supplier-changed, 7,734 May offers are still missing by id = 12,407 − 4,673.)

### 10.3 price_history (2026-05-31 dump 9,974,179 rows; live 8,671,037; 35,756 live rows are newer)
| Bucket | Rows | Evidence | Disposition |
|---|---|---|---|
| Missing on 2026-05-31 vs now | 1,338,898 | ids list sha256 `b2575f8c80d1f89ab95fe3702da5fc99dd599aa21594b5dee2e9f0197e666ed0` (regenerable from the dump) | – |
| …on offers that no longer exist (cascade) | 136,356 | all Official Manufacturer Sites, created Apr–May 2026 | consequence of the Chery part deletion; missing+recoverable from the dump; excluded with the offers |
| …on offers that still exist | 1,202,542 | 1,202,537 Official Manufacturer Sites + 5 eBay Motors; 1,195,505 created Apr 2026; 1,323,855 of the 1,338,898 are real non-zero `boaz_sync` changes | missing+recoverable from the dump; PROBABLE 2026-06-17 cleanup (count and date match, "zero-delta" criterion contradicted); internal data of an inactive supplier |
| History of the restored offers | 0 lost | the 2026-06-30 dump holds 1,277 history rows for only 46 of the 7,971 input offers; none of those 46 is among the 4,673 restored (their 1,277 rows are absent from live and exist only in the dump) | restored offers never had history to re-attach |
No genuine offer gap requires a further restore. The remaining unrestored items are policy-blocked, already represented, or owner-excluded. Re-inserting price_history (1.34M rows, original ids, `ON CONFLICT DO NOTHING`, reversible by id list) is possible from the dump above but is not recommended (internal pseudo-supplier data, not customer-visible) and would need its own approval of exact scope. Nothing was applied by this reconciliation.

## 11. Closure pointers (2026-11-08)
- **Net restore: 4,673** (4,680 inserted, 7 `מקורי` rows rolled back 2026-11-07 after `normalize_part_types` hit `uq_sp_part_supplier_variant`). The earlier statement in §9 that the restore "inserted 4,680" is the gross figure; §10 has the verified reconciliation. Root cause and correction: FIXES_TRACKER #133 UPDATE 4 and #135 (the restore compared the raw label; it now canonicalises through `variant_identity` before every uniqueness check and refuses an unrecognised label).
- **Live checks on 2026-11-08 (VERIFIED, read-only):** both partial unique indexes present, the old index absent, 0 rows with a Hebrew `part_type`; supplier_parts 4,253,767 / available 1,523,432 (+198 natural importer/sync writes since the reconciliation), `price_history` 8,671,104. Backend container up since 2026-11-07 07:46:39 IL with 0 restarts; policy v1.1.0 hash pinned and proven by `maintenance/verify_policy_pin.py` (`"ok": true`).
- **Still not covered by the restore (owner-excluded or blocked):** 3,298 input rows (blocked by the one-offer-per-supplier+part+variant slot, or SKU already live), the 4,191 Chery offers, 1,338,898 deleted `price_history` rows. A further restore needs a new owner approval of an exact scope and the §5a requirements of policy v1.1.0.
- **Related open item discovered during the closure:** the car-parts.ie importer silently dropped parts for 145 of 176 brands (FIXES_TRACKER #140) — unrelated to the offer losses audited here, recorded so it is not mistaken for one.
- Scratch extracts were deleted with owner approval; the evidence kept under `candidates/offer-policy-2026-11-06/historical_audit_evidence/` plus the dump sha256 values above are enough to regenerate them from the dumps (never touch `/opt/autosparefinder/backups/`).
