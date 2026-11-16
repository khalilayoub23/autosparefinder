# Wrong Car-Parts.ie offers - production repair mission (2026-11-16)

Status: **PARTIAL - 3 repaired, 89 preserved, 951 UNRESOLVED (evidence gap). The 1,043-record scope is NOT closed: 951 offers still hold a foreign-brand value (950 proven foreign, 1 NGK/NTK tie) and are live.** Independent consultant (Opus, read-only on production) re-derived the same counts: G 89 / REPAIRED 3 / H 951.
Times: server CET; Israel = CET+1 (22:39 IL for the writes).

## 1. Final dispositions (every one of the 1,043 has exactly one; `classification_1043_final.json`, sha256 5f8ff994...1801)
| Verdict | Count | Disposition | Evidence |
|---|---|---|---|
| PASS - REPAIRED | 3 | C WRONG_PRICE (price only; URL already equals the owner's listing page) | owner-brand payload row (price_eur x 3.48, the importer's own rate/rounding), foreign value positively equal to a foreign-brand row; part brand == owner by `title_brand`; creator of the part == owner (image id / product page) |
| PASS - PRESERVED | 89 | G VALID_CURRENT_STATE | owner-brand rewrite after the manifest: price AND URL equal an owner payload row and no foreign row; 73 verified by the primary, 625485ba (AIC) rewritten 16:52 UTC after the snapshot + 15 more owner re-harvests 17:06-19:58 UTC (`s8_live_check.json`: all 15 moved only by car_parts_ie_import) |
| UNRESOLVED - EVIDENCE GAP | 951 | H | current price+URL positively equal a FOREIGN-brand payload row (wrong for the owner part) but no owner-brand row exists in any retained payload (14,751 files, both archives + /tmp) and the owner's page could not be fetched (Cloudflare challenge: FlareSolverr timeouts 16:47 CET, one clearance minted 20:37 CET but the page body came back empty). Two attempts made (rule 10), then stopped. |

Why the 966 are not repaired from backups: the 2026-11-14 backup value equals the true owner price in only 42 of the 74 rows where the truth is known (consultant: 42/74); `online_price_ils` re-rated 12/56; legacy `Article N:` offers 0/9. A backup value is not proof of ownership.

H split (consultant, Appendix B): 903 owner-created part, wrong price on the right part (797 proven by product-page slug, 112 weaker/ASSUMED...see consultant report); 33 creator unknown; 30 need a part-level decision through `part_identity.classify` (8 parts renamed after creation by a later brand, 7 created from a third brand's page, 4 offers older than their part, 5 ambiguous shared pages SIDAT/FISPA, TRISCAN/KAWE, HOFFER/MEAT & DORIA) - **excluded from any owner-price UPDATE**. Appendix A (102 rows): the 11-14 value sat on the owner's own single-product page but 96 are creation-time prices from June-September - an owner decision, not current proof.
Self-heal: 74 of the original 1,043 were healed by owner re-harvests in about a day. For the 966 this is NOT assured: 794 owner parts were created from the owner's own product page and no owner payload row exists, so the harvester (which visits listing pages) may never visit them (ASSUMED).

Known imperfection: v2 still carries the first wrong-part split (903/33/30) of the consultant's lists instead of the later 797/112/33/5/19; it errs cautious (6 extra rows excluded from owner-price updates), affects no write. Pre-existing follow-up (consultant): the foreign brands' parts for PT155/ZR CT728 R/ZR PG57 L have no offer but still show a `min_price_ils`.

## 2. Production change (APPLIED, VERIFIED by primary; independent re-query by the consultant 20:44-20:50 UTC: each offer == journal `after`; offers changed only price_ils/updated_at, parts only min_price_ils/updated_at; one price_history row per offer == journal ids; customer API 217.02/849.34/387.18, no `~`; 0 customer references (catalog + PII); foreign brands untouched; no other of the 1,043 changed because of the writes; see consultant_phaseD_verification.md)
- Tool `maintenance/cpie_overwrite_repair.py` (extended: `--expect-count`, `--refresh-minmax`, `--dsn` guard) + `maintenance/cpie_wrong_offer_manifest.py` (read-only builder, fresh re-snapshot, refuses on drift). Manifest sha256 01bb5c65916b2248b133279a497842e53b8e40b23520d4bcad98b19e1ff7d35a (3 rows).
- Gates: offer-policy pin ok; no run_all_tasks/night pipeline running (next 00:15 IL); `--verify` NOT_APPLIED x3; customer references 0 in catalog + PII DB (cart_items, wishlist_items, order_items, part_price_watches, part_reviews); owner mission brief of 2026-11-16 as approval; restart_preflight not applicable (no restart).
- Canary 1 row (PT155 35d2d2e7, `result_20261116T203929Z_canary.json`) -> `--accept` true; then 2 rows (`..T203944Z..`) -> `--accept` true; `--verify` = VERIFIED_REPAIRED x3. Prices: 54.91->149.67, 242.14->585.75, 635.48->267.02 (customer total via API = policy price: 217.02 / 849.34 / 387.18; no `~` in `/parts/<id>` nor `/suppliers`).
- min_price_ils refreshed by the policy function inside the tool (closes the 11.5 h gap of the 11-15 repair); parts_catalog.updated_at bumped (Meili incremental key; re-index of the documents is confirmed only after the next meili_sync, ASSUMED until then).
- Rollback: `cpie_overwrite_repair.py --rollback --result <result> --approval-ref ... --refresh-minmax` (restores only rows still at the recorded `after`; exit 2 on a newer legitimate write).

## 3. Tests
Sandbox: `devtests/cpie_wrong_offer_repair_sandbox_test.py` 16/16 (count pin, sha, approval, apply, full-row diff, accept, idempotency, rollback + min/max restore, newer-write refusal, foreign-dsn refusal); existing `cpie_overwrite_repair_sandbox_test.py` 47/47. The mission's wider fixture list (cross-brand same article, alias, unresolved brand, shared listing page, concurrent importer write...) is covered by the importer/identity suites of the earlier round (cpie identity + display tests, offer policy, part identity: 232 passed / 10 skipped on 2026-11-16) - not re-run this round (state: ASSUMED unchanged, code files equal git HEAD).

## 4. Importer protections (VERIFIED by hash)
`cpie_identity.py` and `importers/car_parts_ie_import_generic.py` on disk == git HEAD (ee18f6d logic: resolved + unresolved holder fail closed; bare article in PO/customer payloads since the 2026-11-16 18:06 IL restart).

## 5. Adjacent populations (separate manifests, nothing repaired)
- Unresolved-holder offers: **6,361** exact (`manifest_unresolved_holder_offers_6361.json`; overlap with the 1,043 = 0 - all 1,043 holders are resolved). Frozen by the fail-closed importer (not refreshed); still visible. Brand evidence per row not assessed this round -> separate remediation (qualify-instead-of-skip).
- Missing brand/article offers (~1,270 lead): NOT reproduced. The replay classes sum to 2,058 pairs (617+1,242+77+31+89+2) and a plain count of car-parts.ie-sourced parts without an offer gives 419,448 of 1,204,561 - different definitions; neither verified as the 1,270. UNVERIFIED; needs its own definition and manifest.
- base_price: 820,057 of 1,204,561 car-parts.ie-sourced parts carry the legacy signature `base_price = round(importer_price_ils x 1.45, 2)` (68%; earlier sample estimate of drift 55-57%). The importer still writes its own `price x 1.45` (importer line ~284). No rebase done (out of scope); root cause = the importer is a second `base_price` writer next to `apply_base_price_rule` (policy: one writer).
- 5 contaminated unresolved-holder offers (articles 2611, 35578, 34606, 35573, 10553): owner brand unknown -> preserved, UNRESOLVED.

## 6. Decisions / authorizations still needed (not taken)
1. The 966: hiding wrong-priced offers (e.g. `is_available=false`) is a NEW visibility action with no existing authorized mechanism - not done. Options: hide until owner re-harvest; or accept the 102 dated owner values; or repair after a working product-page fetch (Cloudflare clearance currently unreliable from this host).
2. A targeted re-harvest/requeue of the owner product pages for the 903 right-part rows.
3. The 30 part-level identity cases via `part_identity.classify`.
