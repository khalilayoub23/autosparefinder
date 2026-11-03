
## 2026-10-28 (night) — Supplier lifecycle RELEASED: deployed 20:20 Israel, PII 0042, E2E verified — PASS

Times Israel (UTC+2). Labels: PRODUCTION = live server, read-only unless stated; ISOLATED = disposable containers / DBs on
internal-only networks. No commit, no push. No supplier order, supplier payment, customer charge or supplier email was created.

### Finding 1 — "candidate == live + bundle" vs "14 shared files differ" — RESOLVED (no discrepancy)
- Both statements were true and about different things: the candidate tree is the live tree with the 28 bundle files laid over
  it (0 byte differences to the bundle), and the 14 bundle files that also exist in live differ from live because they ARE the
  candidate's changes. Per file (live / previous baseline / current baseline / candidate hash + diff size):
  `evidence/2026-10-28-release/reconciliation/table.txt`.
- VERIFIED: each of the 14 shared files is byte-identical across live, the previous deployed baseline and the current baseline
  (nothing on live changed since the candidate was built → deploying it reverts nothing); the runtime-identity files
  (`runtime_identity.py`, `maintenance/runtime_identity_check.py`, `tests/test_runtime_identity.py`, `sitecustomize.py`,
  `routes/webhooks.py`) do not overlap the bundle; the reviewed `candidate_vs_deployed.patch` applied to live reproduces the
  bundle byte-for-byte (`patch` exit 0, 0 mismatches); the bundle holds only `.py` files, no logs/data, no secrets (scan: the
  only hits are `sk_test_` prefix checks), no added e-mail/phone literals.

### Finding 2 — Regression by test identity — PASS
- Same command for every tree (image `bf7efaf6…` = production): previous baseline, live (with runtime identity), candidate v3,
  final candidate v4. Main suite: 0 outcome changes, 0 failure-type/message changes, 0 removed tests; 119 candidate-only tests
  all pass (117 + 2 new race tests); 8 runtime-identity tests pass.
- The 127 failed + 4 errors are identical in all trees and environmental (no API server / no DB → connection refused,
  read-only `/app`, host-only files, `HF_VISION_MODEL` unset) — none in supplier authorization, procurement, pricing or
  rollback code.
- NEW: the 203 tests that need an isolated DB were run against a disposable pgvector PG + Redis (22 files, 565 tests; base on
  0041, candidates on 0042): 0 outcome changes, 0 removed; 472 pass in every tree (checkout idempotency, customer price
  boundary, order payment policy, privacy, customer flow, AliExpress discovery/price sync …). Remaining 80 failures identical and
  environmental; `test_order_shipping_e2e` fails only in the combined run ("Future attached to a different loop") and passes
  alone on both trees. Evidence: `evidence/2026-10-28-release/regression/`, `…/db_suites/`.

### Finding 3 — Startup order — VERIFIED defect, NOT a blocker for this release → follow-up #121
- Reproduced in isolation with the exact production command (`evidence/2026-10-28-release/startup_order/result.txt`):
  migration blocked > lock timeout → 0042 rolls back, PII stays 0041, app serves 200, `container_start.sh` skipped, container
  `running`, 0 restarts; normal start → app serves on the OLD schema until the migration commits (~3 s).
- Why not a blocker: the candidate re-checks `supplier_schema_ready()` until True (no stale "not ready" cache), refuses supplier
  features on 0041, and 0042 is one transaction with `lock_timeout = 15s` in both directions; runbook steps 2.5/2.6 detect a
  failed migration (→ F-A). Not changed in this release (production config change; fail-closed vs degraded is an owner
  decision) — tracker #121.

### Defect found by the E2E and fixed — one supplier-payment approval released fulfillment 3 times — FIXED, VERIFIED
- Root cause: `procurement.payment_refusal` checked the approval (`require`) and then marked it consumed with an
  unconditional UPDATE; 5 concurrent payment webhooks all passed the check before any consumed it — real PG: **3 of 5
  released** (`evidence/2026-10-28-release/e2e/result_cand3_before_fix.txt`, check C11). (Downstream `supplier_payments` has
  UNIQUE(order_id, supplier_id), but the one-shot approval itself must hold.)
- Fix: `supplier_authorization.consume()` is atomic for one-shot domains — `PgAuthStore.consume_once` =
  `UPDATE … SET consumed_at … WHERE id = … AND consumed_at IS NULL AND revoked_at IS NULL RETURNING id`, returns who won
  (InMemoryAuthStore equivalent); `payment_refusal` releases only when it wins, else refuses + audit event.
  Files: `supplier_authorization.py` `a58d44de…`, `procurement.py` `79d2d848…`, `tests/test_supplier_safety_gates.py`
  `1d856cb4…` (+2 tests: concurrent release once; second holder of a grant loses — both FAIL on the old code, PASS now).
  After: real PG 5 concurrent → exactly 1 released (C11 PASS).
- Artifact: v3 (`SHA256SUMS` `67013ca3…`) preserved in `archive/files-v3-SHA256SUMS-67013ca3.tgz` (`747bcbc4…`);
  final v4 `SHA256SUMS` = `649f41dd9b0b729ae84b5a38544c6d32943040bcff36b63265fd7e67c0d168b3`, patch regenerated
  (`eea32d16…`), old patch kept as `candidate_vs_deployed.v3.patch`. Full regression rerun on v4 (above).

### Backup / migration / compose / rollback — PASS (isolated + production read-only; no production downgrade)
- Unchanged from the 2026-10-28 (evening) entry: rehearsal 2 (write-after-backup, lock timeout, corrupt backup, interrupted
  deploy, repeated recovery) and rehearsal 3 (exact `docker compose run -T --rm --no-deps --entrypoint ""` form) passed;
  production `… current` read-only returned 0041 with an unchanged fingerprint. The v4 delta touches no migration, backup,
  restore or startup code. Expected revision: before 0041_email_agent_owner_flow → after 0042_supplier_lifecycle (VERIFIED).
- DEPLOY.md v4 (v3 kept as DEPLOY.v3.md): records the v4 artifact hash; step 2.4 = `docker restart autospare_backend`
  (same container; bind-mounted code; container config hash `c4a2b502…` = `docker compose config --hash backend`, so a
  recreate would change nothing — and a restart cannot pick up unrelated config).

### Deployment — PASS (PRODUCTION)
- Preconditions 20:19 IL: bundle `sha256sum -c` exit 0; PII 0041; disk 29 G free; all 5 supplier switches unset (OFF); no
  active PII sessions. Rollback target `/root/asf-deploy-20261028T181942Z/` (chmod 400: `deployed.sha256` 714 files,
  `deployed_manifest_files.tgz` 14 files, `new_paths.txt` 14 paths, `artifact.SHA256SUMS`).
- `bash backend/scripts/pre_restart.sh` exit 0 at 20:19:52 → `RESTART PREFLIGHT: SAFE` (only the continuous car-parts.ie
  harvester running, relaunched by design).
- 2.1 copy exit 0 (20:20:09) · 2.2 bundle + deployed-tree hash check exit 0 · 2.3 dry-run "Running", config hashes equal ·
  2.4 `docker restart autospare_backend` exit 0, 20:20:29.5→20:20:40.8 · 2.5 log `Running upgrade 0041… -> 0042…`, 0 ERROR /
  Traceback · 2.6 PII `0042_supplier_lifecycle` (20:20:45) · 2.7 health 200 (1st try) · 2.8 no switch = 1 ·
  2.10 runtime identity LOADED_MATCH · `container_start.sh` "Startup complete".
- Container `5f1afa2e667f…` (same), image `bf7efaf6…` (same), RestartCount 0, OOMKilled false. Only the backend restarted.
- 2.9 (15 min): __29__

### Runtime identity — PASS (PRODUCTION)
- `docker exec autospare_backend python3 /app/maintenance/runtime_identity_check.py --pid 1 --expect
  agents.owner_console=1e1458c7389af2759c48ff3a0161f7622573bf33f6e294436e61c03775078059` → `LOADED_MATCH
  loaded_sha256=disk_sha256=1e1458c7… path=/app/agents/owner_console.py pid=1 start=88074423 host=5f1afa2e667f
  loaded_utc=2026-10-28T18:20:43Z loads=1`, exit 0; pid 1 = uvicorn; the hash is of the bytes the new process compiled
  (startup log line `[runtime_identity] agents.owner_console sha256=1e1458c7… pid=1 start=88074423`), the expected value is
  the artifact file's hash. Re-checked at 2.9: __RI__

### E2E (non-binding) — PASS — ISOLATED E2E (76/76), PRODUCTION READ-ONLY (below); external acceptance BLOCKED
- `evidence/2026-10-28-release/e2e/e2e_lifecycle.py` on the final candidate: real modules + real PG stores on a fresh
  disposable 0042 DB; only the AliExpress Open Platform (documented response shapes) and Gmail (in memory) are fakes.
  A discovery/qualification 10 · B product/price validation 9 · C authorization boundaries 14 · E order lifecycle 20 ·
  G recovery/audit 2 · D BOAZ ownership 8 · F outreach + routing 13 → 76 PASS (`result_cand4.txt`).
- State mapping (candidate names, not renamed): PREPARED/AWAITING_AUTHORIZATION = `AWAITING_PO_AUTHORIZATION`;
  VALIDATION_FAILED = connector refusal before any order request → `SUBMISSION_FAILED` (E11/E12); SUBMISSION_BLOCKED = refused
  without approval, supplier never contacted (E13); CREATED_UNPAID = `SUBMITTED` with `paid: false`, `try_to_pay: "false"`
  (E1, simulator only); REJECTED = `SUBMISSION_FAILED` (E4); TIMEOUT_UNKNOWN / RECONCILIATION_REQUIRED = `EXCEPTION`
  (E6–E9, never resubmitted); FAILED = `SUBMISSION_FAILED` "nothing sent" (E10); confirmed = `SUPPLIER_CONFIRMED` (E3).
- **BLOCKED — REAL EXTERNAL ACTION REQUIRED:** real AliExpress acceptance of `ds.order.create`. The DS API has no sandbox for
  order creation and an order created unpaid still reserves stock / obligates — not tested, by design. Same for a real
  e-mailed purchase order and a real supplier inquiry send.
- PRODUCTION READ-ONLY after deploy: new tables 0 rows (authorizations, outreach, procurement, events); 131/131 historic
  emails marked routed (history not replayed); live AliExpress supplier → `ManualConnector` ("automatic ordering is switched
  off"); 8 onboarding suppliers → `AwaitingCredentialsConnector`; `gate_bypassed()` False; switches all False; non-consuming
  payment check → "no procurement prepared"; BOAZ list read; 0 BOAZ/NIR todos and 0 suppliers changed since 20:20;
  Email Agent cycle on the new code: sent 0, no Spam pass.

### No binding action — VERIFIED (PRODUCTION, 12 h window to 20:3x IL)
- 0 supplier_payments created/updated, 0 Email-Agent sends, 0 orders, 0 payments, 0 `agent_authorizations` / outreach /
  procurement rows, 0 log lines for `ds.order` / `afterpay` / `send_supplier_inquiry` / `drafts/send`.

### Rollback (approval A2, not used)
- F-B per DEPLOY.md v4 with `D=/root/asf-deploy-20261028T181942Z` (backup → verify → one-off downgrade → restore code →
  preflight → restart). Code-only restore is invalid while the DB is at 0042 (old code cannot downgrade).

### Status
- Finding 1 PASS · Finding 2 PASS · Finding 3 VERIFIED, follow-up #121 · race defect FIXED · deploy PASS · runtime identity
  PASS · isolated E2E PASS · real supplier acceptance BLOCKED (needs a real order) · new follow-up #122.
