# Closure report — session of 2026-11-20 (partial; status per item)

Legend: PASS / BLOCKED / FAILED / NOT VERIFIED / NOT DONE. Commits are LOCAL only; nothing pushed.

## 1/2. Gmail supplier-email audit — ROOT CAUSE FOUND, FIX WRITTEN + TESTED, NOT YET DEPLOYED (APPLIED to disk only)
- Mailbox searched read-only through the agent's own GmailClient, `includeSpamTrash`, paged, all folders.
- Root cause: the agent scans only `in:inbox newer_than:14d` (+ spam 14d) and was first armed 2026-10-10. SNG's reply
  (2026-09-14) pre-dates arming and is outside the window, so it was never ingested/classified/routed. Also the original
  inquiry (2026-09-02) was sent manually, so no `supplier_outreach` row existed to route a reply to.
- Fix (code, `backend/email_agent/`): `_history_pass` (known supplier domains from `suppliers.website`, `in:anywhere`,
  400d, paginated up to 12 pages/query, rests 6h when nothing new, local drafts only), `supplier_intake.py`
  (domain match to exactly one supplier, deterministic fact extraction, persist to `suppliers.credentials.contacts`,
  `suppliers.shipping_info.supplier_stated`, qualification CLAIM), routing of a reply from a known supplier to BOAZ
  (NIR for nir_sourcing candidates). Off switch `EMAIL_AGENT_SCAN_HISTORY=0`.
- Tests: `tests/test_supplier_history_intake.py` (18) + existing email/supplier suites: 364 passed, 1 skipped.
- DEPLOY: DONE 2026-11-20 ~06:25-06:28 IL via guarded_restart (2 recreates: 1st exposed a bind-parameter bug ':https' in the domain query, fixed + regression test, 2nd verified). Live cycle: history queries=9 listed=22 processed=13 failed=0, healthy, OOM false, restarts 0, 0 tracebacks. Recovered older supplier mail: asapnetwork.org x8 (6 supplier->BOAZ, 2 newsletters), lkqcorp.com x4 (BOAZ), sngbarratt.com x2; no Gmail drafts created. (Content of the ASAP/LKQ mails NOT yet reviewed.) Original note: DEPLOY: NOT DONE. `sync_prices` was mid-run (started 01:14 IL); a guarded restart (`pre_restart.sh` then
  `guarded_restart.sh`) must follow its completion. Until then the running uvicorn still uses the old scan.
- Evidence of the original miss: PII `email_agent_messages` had no row for 1a0a07c56c725637.

## 3. SNG Barratt — PASS (data) / BLOCKED (pickup address awaits supplier)
- Thread 1a063430bf19b1f1; reply 1a0a07c56c725637, Simon.Cottrill@sngbarratt.com, 2026-09-14 15:14 UTC, DKIM+DMARC pass.
- Ingested through the real pipeline (class `supplier`, route BOAZ, task f3f0cc86). Stored: contact name/role/phone,
  carrier DHL, transit 3-5 days, rates `variable_live_quote_required` (no price invented), claim on `israel_shipping`
  (UNVERIFIED). Backup before: `backups/suppliers_sng_before.txt`.
- Pickup-address request sent 2026-11-20 via the authorized outreach path (outreach 4ef942de, Gmail msg 1a1f633a1772d4b1,
  new fixed template purpose `pickup_address`). Reply NOT received; allowlist NOT changed.

## 4. ALVADI — thread verified; supplier data UNVERIFIED
- The two "ALVADI" items are NEWSLETTERS (news@alvadimail.com) that the owner forwarded from Hotmail (10 forwards, last
  1a1f274c8202e864). Not a supplier reply; the footer is not a pickup address. Agent classified it automated_notification.
- Recorded as candidate `ALVADI OÜ` (id 5380cc9c…, is_active FALSE, pending_review) with the footer as an UNVERIFIED claim.
  Official pages returned HTTP 403 to automated fetch -> identity/contact/pickup NOT VERIFIED. No email sent.

## 5. NIR placeholder incident — PARTIAL
- Message: outreach 8bd5318e to `info@yourwebsite.com`, supplier b32a938b (ukarauto), sent 2026-11-18 21:48:38 UTC,
  Gmail thread 1a1f0a20e3dc7a58 (delivery to a placeholder domain: bounce/delivery NOT checked). Placeholder domains were
  already blocked at contact recording (commit 3423f14); this session adds validation at `plan()` AND `send_outreach()`.
- Also found: 24+ FAILED retry rows to `management@autoworks.co.i` (typo domain) on 2026-11-13 — blocked by valid_email now.
- NOT DONE: review of all 21 recipients/duplicates/follow-up counters, discovery for the 54 candidates without contacts.

## 6. Eurosender — BLOCKED per supplier (allowlist stays empty)
SNG: awaiting reply to pickup request. ALVADI: not verified. Car-Parts.ie, eBay Motors: NOT DONE this session.

## Mail agent check (owner request) — PASS
12 recovered ASAP/LKQ/SNG messages (Jul-Sep): all classified (10 `supplier`, 2 newsletter), 10 escalated requires_human with ONE BOAZ task each (SNG welcome -> NIR), owner_notified set, 0 Gmail drafts. Mail content not read by me.

## 7. BDS (2,349) / ASAP MSRP — PASS (data) with a deliberate owner-visible change
- Finding (live): all 2,349 BDS parts had `importer_price_ils` = MSRP/1.18 (a LOCAL COST) and `base_price` derived from it; their only offer is ASAP's (supplier inactive, price_level=0). Customers saw e.g. BDS1613FS at a 12,792 ILS "estimate" (`is_estimate` true, but Parts.jsx ignores the flag, so it looks like a price).
- Outcome per offer (all 2,349 reconciled, identical class): genuine catalog part, no valid supplier offer -> part KEPT and searchable; ASAP offer set `is_available=false` (row, prices, history kept - nothing deleted); MSRP-derived `importer_price_ils`/`base_price` cleared to 0; previous values stored in `specifications.msrp_quarantine` (exact undo: `maintenance/asap_msrp_quarantine.py --restore`). Same proven defect (importer_price_ils == offer price, no other supplier) also fixed for Fox Factory 360, Adams Driveshaft 93, AGM Products 73, Banks Power 1 = 2,876 parts total. Backup: `backups/asap_msrp_before.json` (2,876 rows). Policy gate used; script registered in POLICY_BOUND_ACTORS.
- Live verification: BDS1613FS search -> cheapest_price None, customer_estimate None, is_estimate False; 2,876 offers avail=0.
- Importer `importers/asap_import.py` fixed so a re-run cannot restore it (no importer_price_ils/base write, offers never available; MSRP kept in specifications.msrp_ils). Tests `tests/test_asap_msrp_not_cost.py` (3).
- NOT DONE: Meilisearch pickup (updated_at bumped; next incremental sync) NOT VERIFIED; frontend still ignores `is_estimate` for the remaining (IL importer) estimates; per-brand dealer-terms/permissions inspection of ASAP (5 brands) not done (account price_level=0 per stored evidence). Activation path: only when a manufacturer grants a cost tier.

## 8. eBay reconciliation — PASS (counts), shipping rule FIXED, recheck PENDING
- Populations by unique offer id (backup `/app/state/ebay_repair_backup/supplier_parts_before_repair_1795075554.json`): 1,025 offers = 16 pre-guard + 210 no_listing + 799 other_listing, disjoint (1,025 unique ids). The 1,009 of the earlier report = 210 + 799 and EXCLUDES the 16 and 150 more offers judged by the same guarded refresh on 2026-11-19. Verdict table `supplier_offer_checks` (latest per offer): 1,025 in-population + 150 outside = **1,175**, each with exactly ONE verdict: exact_match 328+64=392, missing_listing 288+30=318, no_israel_shipping 362+47=409, ambiguous 40+8=48, seller_mismatch 4+1=5, product_mismatch 3+0=3 (sums match the earlier report). No double count; the 166 gap was the 16+150 outside the 1,009.
- The 16: 7 now on the ORIGINAL item id (3 same-listing refreshes + 4 restored), 6 on a different guard-verified listing (kept), 3 `product_mismatch` made unavailable (16 total). Seller-level re-comparison of the 6 retained NOT re-run (eBay calls).
- Shipping-to-Israel: code path audited. Defect found: an item with NO shippingOptions AND NO shipToLocations (or only a non-country world region other than ASIA/MIDDLE_EAST) was a hard `no_israel_shipping`. Fixed in `EbaySupplier.live_from_item` -> ambiguous (offer unchanged). Tests +3 (49 passed). Deployed 2026-11-20 07:37 IL (guarded restart, healthy, 0 tracebacks).
- PENDING: the 563 stored `no_israel_shipping` verdicts (all with reason "listing no longer offers shipping to Israel") were produced partly under the old rule; eBay quota is at its 300 reserve until 10:00 IL, so a re-judge (`ebay_listing_repair.py --recheck-verdicts no_israel_shipping --apply --call-budget 700`) is queued to run after the reset. NOT VERIFIED until it reports.
- `EBAY_REFRESH_ALLOW_RELISTING` is no longer read by code (relisting impossible by construction) - nothing to enable.

## 12-14. sync_prices / digest / heal task
- sync_prices 2026-11-20 01:14 IL: COMPLETED 02:02 IL, no error; ebay checked=2769 updated=869 no_listing=1900 errors=0, refresh[exact_match=868, missing_listing=260, no_israel_shipping=154, ambiguous_identity=17, seller_mismatch=1]. Per-offer review of these verdicts NOT DONE.
- Digest content: NOT VERIFIED (text cannot be read back). `task_heal_importer_price` plan/timing: NOT VERIFIED.

## 15-17. Production / code / integrity
- No restart, migration or production-service change this session. Data writes: SNG supplier row, 1 PII email row +
  1 BOAZ todo, 1 outreach row + 1 email sent, ALVADI candidate row. Untracked `archive/data/NOA_test_15s.mp4` untouched.
- Files: email_agent/{agent,config,context,loop,supplier_routing,supplier_intake}.py, supplier_outreach.py,
  maintenance/send_pickup_request.py, tests/test_supplier_history_intake.py.
