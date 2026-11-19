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
- DEPLOY: NOT DONE. `sync_prices` was mid-run (started 01:14 IL); a guarded restart (`pre_restart.sh` then
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

## 7-9, 10 (BDS 2,349 offers; eBay 16/799/210/1,175 reconciliation; AGM; ASAP; BOAZ audit) — NOT DONE
No work performed this session. They remain open exactly as in the 2026-11-19 report.

## 12-14. sync_prices / digest / heal task
- sync_prices 2026-11-20 01:14 IL: RUNNING at time of writing (heartbeat 01:39 IL) -> NOT VERIFIED.
- Digest content: NOT VERIFIED (text cannot be read back). `task_heal_importer_price` plan/timing: NOT VERIFIED.

## 15-17. Production / code / integrity
- No restart, migration or production-service change this session. Data writes: SNG supplier row, 1 PII email row +
  1 BOAZ todo, 1 outreach row + 1 email sent, ALVADI candidate row. Untracked `archive/data/NOA_test_15s.mp4` untouched.
- Files: email_agent/{agent,config,context,loop,supplier_routing,supplier_intake}.py, supplier_outreach.py,
  maintenance/send_pickup_request.py, tests/test_supplier_history_intake.py.
