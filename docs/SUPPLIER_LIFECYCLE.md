# Supplier lifecycle — NIR, BOAZ, Email Agent

Owner task 2026-10-27 ("MASTER IMPLEMENTATION TASK — NIR, BOAZ & GMAIL AGENT").
**Status: DEPLOYED 2026-10-28 20:20 Israel** (artifact v4, `SHA256SUMS` `649f41dd…`; PII migration
`0042_supplier_lifecycle` applied; runtime identity LOADED_MATCH `1e1458c7…`). **Every new capability is OFF**
(BOAZ_TASKS_ENABLED, EMAIL_AGENT_SCAN_SPAM, SUPPLIER_ROUTING_ENABLED, NIR_OUTREACH_ENABLED, SUPPLIER_AUTO_ORDER_ENABLED —
each needs its own owner approval A5). Always on: the paid-order procurement gate (a customer payment prepares a
procurement and refuses any supplier spend until the owner separately approves the purchase order AND the payment).
Bundle, evidence and runbook stay in `candidates/supplier-lifecycle-2026-10-27/` (rollback target
`/root/asf-deploy-20261028T181942Z/`). Tracker: `FIXES_TRACKER.md` 2026-10-27 entries and "2026-10-28 (night) — Supplier
lifecycle RELEASED".

## 0. Safety gates (2026-10-27)

| Gate | Result | Evidence |
|---|---|---|
| Restart / lazy-import exposure | ROOT-FIXED: candidate moved out of the bind mount; `backend/` = deployed (pyc source sizes match; 0 diffs) | bundle `DEPLOY.md` |
| Implicit activation | all new capabilities OFF unless exactly `1` (`supplier_flags.py`): BOAZ loop, Spam scan, supplier routing, NIR outreach, auto-ordering | `tests/test_supplier_safety_gates.py` |
| New code on an un-migrated schema | email store selects 0042 columns only when present; supplier routing/Spam refused, 0041 owner flow keeps working | throwaway PG (`evidence/gates.py`) |
| Migration / rollback | rehearsed: clean chain 0001→0041→0042, repeat no-op, forced failure → stays 0041, downgrade with candidate code, wrong order fails safely, old code OK on both schemas | `evidence/rehearsal_result.txt` |
| Rollback data-loss protection | `ops/backup_0042_data.sh` / `verify_unchanged_0042.sh` / `restore_0042_data.sh` + DEPLOY.md v2 failure branches F-A..F-F; backup→downgrade→restore content fingerprint identical; corrupt backups restore nothing; downgrade lock timeout 15 s | `evidence/rehearsal2_result.txt` |
| Concurrency | atomic claim (one SQL statement) per submission / payment: 1 of 10 concurrent claims wins (real PG) | `evidence/gates.py` |
| Regression | baseline (2026-10-28 evening, with runtime identity) 2288 pass / 127 fail / 4 err; candidate on that baseline 2405 / 127 / 4; 0 baseline-pass→candidate-fail, identical failure messages, 117 candidate-only tests pass | `evidence/2026-10-28-closure/regression/` |
| Rollback via the exact production command | `docker compose run -T --rm --no-deps --entrypoint "" backend alembic -c alembic_pii.ini …` rehearsed in an isolated compose project: backup → downgrade → re-upgrade → restore identical; repeat downgrade no-op; repeat restore refused (exit 7); repeat backup refused (exit 2); killed migration stays 0041; startup hooks never ran | `evidence/2026-10-28-closure/rehearsal3/` |
| Which owner_console is running | `backend/runtime_identity.py` records the hash of the bytes the process actually compiled; `maintenance/runtime_identity_check.py --pid 1 --expect agents.owner_console=<sha>` → LOADED_MATCH (DEPLOY.md step 2.10) | live since 2026-10-28 19:03 IL |

| One-shot approvals under concurrency (2026-10-28 release) | E2E on real PG found 3 of 5 concurrent payment webhooks released by ONE supplier_payment approval (check-then-mark). Fixed: `consume()` is atomic (`consume_once`: UPDATE … WHERE consumed_at IS NULL RETURNING); now exactly 1 of 5 | `evidence/2026-10-28-release/e2e/` (before/after) |
| Integrated E2E (non-binding) | 76/76 on real PG at 0042: qualification, price/variant/shipping validation, authorization boundaries, order states, recovery, BOAZ ownership, outreach + reply routing. External acceptance by a real supplier NOT tested (needs a real order) | `evidence/2026-10-28-release/e2e/result_cand4.txt` |
| DB-gated regression | 565 tests needing an isolated DB: 0 changes vs live | `evidence/2026-10-28-release/db_suites/` |

Production 2026-10-28 19:03 IL: runtime identity diagnostic deployed. 20:20 IL: supplier lifecycle v4 deployed via DEPLOY.md v4
(steps 2.1–2.10 PASS, PII 0042, switches OFF). Known follow-ups: #121 (start command does not wait for migrations — reproduced,
not a blocker: this code is schema-tolerant), #122 (crashed BOAZ task stays in_progress; BOAZ loop is OFF).

**Next owner decisions (each separate):** turn on a switch (A5) one at a time; the first real AliExpress order (creates a binding
order — needs explicit approval); onboarding credentials for the pending suppliers.

## 0b. Dropshipping qualification (`supplier_qualification.py`, schema v2)

Second, independent dimension next to API/integration. Checks (each VERIFIED / UNVERIFIED / FAILED + source + time +
note; claims/keywords never VERIFY): `dropship_direct_to_customer`, `dropship_israel_delivery`,
`dropship_commercial_terms`, `dropship_delivery_tracking`, `dropship_packaging_identity` (recorded, not blocking),
`dropship_returns_warranty`. **DROPSHIPPING-READY** = the five mandatory dropship checks + israel_shipping,
availability (fresh ≤30 d), catalog, contacts, business all VERIFIED and no contradiction; any of them FAILED →
**DROPSHIPPING-FAILED**; otherwise **DROPSHIPPING-CANDIDATE**. v1 records get every missing check as UNVERIFIED
(`ensure_schema`). An emailed purchase order (ships to our customer) requires DROPSHIPPING-READY; NIR inquiries ask
the dropshipping questions (template-bound, non-binding); BOAZ's onboarding check and the activation record carry it.

## 1. Permission domains (one enforcement point: `backend/supplier_authorization.py`)

| Domain | Who approves | Bound to | Enforced at |
|---|---|---|---|
| customer payment | Stripe (existing `order_payment_policy`) | the order | `routes/utils.trigger_supplier_fulfillment` |
| `supplier_message` / `routine_inquiry` | owner, standing grant `אשרפניותספקים` (revoke `בטלפניותספקים`) | policy (NIR inquiries only) | `supplier_outreach.send_outreach` + `GmailClient.send_supplier_inquiry` (own endpoint set) |
| `supplier_activation` | owner `אשרספק <id>` | one supplier | `services/supplier_sourcing.approve_supplier` |
| `commercial_commitment` | — | — | **no code path performs it**; outreach text cannot contain it (`COMMITMENT_PATTERNS`) |
| `purchase_order` | owner `אשררכש <id>` | one procurement + exact terms hash | `procurement.submit_po` / `record_manual_submission`; DB CHECK |
| `supplier_payment` | owner `אשרתשלוםספק <id>` | one procurement + exact terms hash | `procurement.payment_refusal` (called by `gate_paid_order` before ANY spend); DB CHECK |
| `supplier_integration_change` | — | — | reject no longer deactivates active suppliers |

Only an explicit owner action is an approval source (`APPROVAL_SOURCES`); emails, tasks and model output are not.
Purchase-order / payment / activation approvals are one-shot; a changed price, quantity, item or shipping fee
changes the terms hash, so the old approval no longer matches.

## 2. Flow

1. **NIR discovery** (`services/supplier_sourcing.py`) — new candidates get a qualification record
   (`supplier_qualification.py`, stored in `suppliers.credentials.qualification`): six requirements
   (api, israel_shipping, availability, catalog, contacts, business), each VERIFIED / UNVERIFIED / FAILED with
   source + time + note. Homepage keywords and supplier claims can only make a requirement UNVERIFIED.
   Classes: `integration_ready` (all six VERIFIED) · `no_api_candidate` · `needs_investigation` · `not_qualified`.
   Existing suppliers are never relabelled (re-discovery goes to `credentials.nir_rediscovery`).
2. **NIR outreach** (`supplier_outreach.py`, run from the Email Agent loop) — OFF unless `NIR_OUTREACH_ENABLED=1`
   AND the owner's standing grant exists. Fixed English templates only, verified contacts only, one open inquiry per
   supplier, follow-up after 7 days, max 3 attempts, draft read back and compared before sending.
3. **Email Agent** — inbox + **Spam pass** (`in:spam newer_than:14d`, `includeSpamTrash`): only spam relevant by an
   outreach thread / outreach recipient domain / known supplier domain is read and stored (`source_folder='spam'`).
   Labels are never changed (no `gmail.modify` scope) — the owner alert says "mark not spam in Gmail".
   `email_agent/supplier_routing.py` (before `owner_flow`): reply to an inquiry → NIR task + outreach
   `REPLY_RECEIVED`; ambiguous match → exception (needs the owner); other supplier mail → BOAZ.
4. **BOAZ** (`boaz_tasks.py`, supervised `boaz_task_loop`, every 900 s) — claims todos owned by BOAZ under any
   spelling (`agent_identity`: BOAZ / pricing_agent / supplier_manager_agent), executes `email`, `supplier_handoff`,
   `bulk_scan`; anything else is escalated, never silently completed. Owner: `משימות בועז`.
5. **Procurement** (`procurement.py`) — a customer payment → `gate_paid_order` → PREPARED rows only + one owner alert.
   Owner: `רכש` · `אשררכש` · `הוזמןידנית <id> <ref>` (connectors' `place_order` is a stub) · `אשרתשלוםספק`. Only then
   does the existing spend path run, once.

## 3. Requirements → code map

| Requirement | Status | Where | Evidence |
|---|---|---|---|
| Separate permissions at execution boundaries | BUILT | `supplier_authorization.py`, gates listed above | tests §1, §6; DB CHECKs (throwaway PG) |
| BOAZ task ownership mismatch | BUILT (verified defect: `pricing_agent` vs `BOAZ`, exact-match SQL) | `agent_identity.py`, `agent_todo_utils.py` | test + real-PG claim test |
| BOAZ executes/escalates tasks | BUILT | `boaz_tasks.py` + loop | tests §2, real PG |
| Bulk-deal wrong DB (verified: PII model on catalog session) | BUILT | `SupplierManagerAgent.detect_bulk_opportunities` | test §2 |
| Bulk-deal threshold (verified: max stock 36 < 50) | **NOT DONE — owner decision** | `BOAZ_BULK_MIN_STOCK` | live query 2026-10-27 |
| NIR qualification + evidence | BUILT | `supplier_qualification.py` | tests §3 |
| NIR evidence collection (automatic VERIFIED) | **NOT DONE** — needs NIR/owner to record evidence; nothing marks VERIFIED automatically | — | — |
| NIR authorized outreach | BUILT, OFF | `supplier_outreach.py` | tests §4 |
| Supplier replies → NIR | BUILT | `email_agent/supplier_routing.py` | tests §5 |
| NIR reviews reply claims automatically | **NOT DONE** — the NIR task is a worklist; claim recording is an API (`record_supplier_claim`) | — | — |
| Spam/Junk inspection | BUILT | `email_agent/agent._spam_pass` | tests §5 |
| Un-spam legitimate mail | **BLOCKED** — needs `gmail.modify` scope (owner re-consent) | — | — |
| Separate offers / no overwrite | EXISTING (verified unchanged) | `offer_classification.py`, `supplier_parts` constraints | — |
| AliExpress currency | FIXED fail-closed (non-USD SKU skipped) | `aliexpress_supplier.get_part_details` | ASSUMED USD per `target_currency=USD`; live response not inspected |
| Procurement state machine + PO/payment auth | BUILT | `procurement.py`, migration 0042 | tests §6, §9, real PG |
| Automatic supplier ordering | **BUILT, OFF** (`SUPPLIER_AUTO_ORDER_ENABLED=0`) — `supplier_ordering.py`: AliExpress DS (`aliexpress.ds.order.create`, unpaid, `out_order_id` idempotency; payment = `afterpay` behind its own approval), order-by-email, awaiting-credentials slots (5 dropship suppliers), manual for the rest | `supplier_ordering.py`, `procurement.submit_po/pay_via_connector/reconcile` | API names + fields VERIFIED against the official AliExpress Open Platform reference (2026-10-27); **live order NOT tested** (needs one owner-approved real order) |
| Cross-agent e2e | SIMULATED | `tests/test_supplier_lifecycle.py::test_end_to_end_supplier_lifecycle` | Gmail, supplier and payment are fakes |

## 3b. Supplier order connections (`backend/supplier_ordering.py`)

| Supplier | Connection | Order | Payment |
|---|---|---|---|
| AliExpress | `AliExpressDsConnector` | `ds.order.create`, `try_to_pay=false`, `out_order_id` = procurement id; live re-check: one SKU (USD, in stock, price ≤ ours), cheapest **tracked** delivery ≤ approved shipping (unknown → `ALIEXPRESS_DS_MAX_SHIPPING_USD`, default 0 = free only) | `ds.order.afterpay` via `אשרתשלוםספק`; fulfillment spend path then refuses that order (no double payment) |
| `credentials.ordering = "email"` + VERIFIED orders/sales contact | `EmailPurchaseOrderConnector` | fixed PO text from approved terms, own Gmail path (`send_purchase_order_email`), draft read back; supplier reply in thread → BOAZ task | outside the system |
| `credentials.status = pending_credentials` (ASAP, Turn14, Keystone, Meyer, ATD) | `AwaitingCredentialsConnector` | refuses with the owner step | — |
| everyone else (active) | `ManualConnector` | owner orders on site, `הוזמןידנית` | — |
| inactive supplier / any onboarding status | `AwaitingCredentialsConnector` / `ManualConnector` | never orderable | — |

Live slots (read-only query 2026-10-27), all **not operational**: Meyer, ATD (`pending_credentials`), ASAP Network
(`account_active_pending_brand_approval`), Keystone (`application_submitted_pending_review`), Turn 14
(`pending_review`) — all `is_active=false`. Email-order suppliers configured live: **0**. AliExpress row active; its
order path is untested live. Shipping: a fee is used only when the API states free shipping or gives a readable fee
(missing ≠ free).

Status check: `בדוקרכש <id>` (`trade.ds.order.get` + `ds.order.tracking.get`). Ambiguous results (timeout after send,
5xx, no result block, `REPEATED_ORDER_ERROR`) → EXCEPTION, never resubmitted.

## 4. Deploy (approval-gated)

Requires the owner's explicit OK: `pre_restart.sh` (preflight SAFE) → `docker compose up --dry-run -d --no-deps backend`
→ `docker compose up -d --no-deps --force-recreate backend`. Startup applies `alembic_pii` 0042 (new tables, 3 new
columns on `email_agent_messages`, historic rows marked routed). On first passes: BOAZ claims the 1 open BOAZ todo
(Eurosender) and escalates it (one WhatsApp alert); the Spam pass starts; outreach stays off.

**Rollback:** with the NEW code still in place, `docker exec autospare_backend alembic -c alembic_pii.ini downgrade
0041_email_agent_owner_flow`, then restore the files (`git checkout -- <modified>` + remove the new files listed in the
tracker entry) and recreate the backend.
