# Consultant review - Phase C: repair tool + production plan (3 REPAIR rows)

Reviewer: independent read-only consultant, 2026-11-16. Read: `backend/maintenance/cpie_overwrite_repair.py` @ 07eb8b1,
`backend/devtests/cpie_wrong_offer_repair_sandbox_test.py` (13 checks), `backend/devtests/cpie_overwrite_repair_sandbox_test.py`
(47 checks), `db_update_agent.refresh_min_max_for_parts`, `meili_sync.py`, `offer_policy.gate_or_exit`, `scripts/restart_preflight.py`.
Live, read-only: triggers, column types, the 3 offers + parts. I did NOT run the sandbox tests (their PASS is the author's claim, ASSUMED).

## Verdict

Design is sound for a 3-row price-only write: **GO after the must-fixes below** (none is a blocker to correctness of the
offer write itself; D1-D4 are gaps in what is proven/tested around it). Status: PARTIAL.

## Live state of the 3 rows (VERIFIED, ~18:20)

| offer | price_ils / url / updated_at | part min_price_ils | part base_price | offers on part | supplier country |
|---|---|---|---|---|---|
| 35d2d2e7 PT155 | 54.91 / daihatsu/wildcat-rocky-f70/ignition-system-auto / 2026-11-15 13:53:28.250470 | 54.91 | 217.02 = 149.67 x 1.45 | 1 | IE |
| b1b5e079 ZR CT728 R | 242.14 / citroen/c3-pluriel-hb/window-regulator / 2026-11-15 12:20:59.861898 | 242.14 | 849.34 = 585.75 x 1.45 | 1 | IE |
| 10d88c3d ZR PG57 L | 635.48 / citroen/c1-ii/window-regulator / 2026-11-15 12:13:12.890454 | 635.48 | 387.18 = 267.02 x 1.45 | 1 | IE |

Unchanged since the classification snapshot. New corroboration: each part's `base_price` already equals target x 1.45 (written by
the owner-brand row's part upsert) - independent of the payload match, consistent with the targets. No `--rebase` needed.
`min_price_ils` is still on the foreign price (customer-visible) - `--refresh-minmax` is needed. No triggers on `supplier_parts` /
`price_history`; `parts_catalog` triggers are mfr-sync (BEFORE INSERT/UPDATE, harmless for an updated_at touch) and meili delete/
deactivate (not fired). All timestamps are `timestamp without time zone` (naive `ts()` comparison is correct).

## Review by topic

**Expected-value guards - OK.** `classify` requires same part_id, supplier, sku; NOT_APPLIED needs price+url+updated_at equal to
the manifest; the UPDATE is a CAS on price+url+updated_at. Because target URL == current URL for all 3, VERIFIED_REPAIRED is decided
by price only - correct. If the owner re-harvests first (fixed importer writes the bare key only for the owner brand), the row becomes
VERIFIED_REPAIRED / NEWER_WRITE_* and is skipped: safe and convergent.

**Locking / tx boundaries - OK.** One transaction per row; offer then part `FOR UPDATE NOWAIT` (the importer locks part then offer, but
NOWAIT means the tool never waits -> no deadlock, it skips); importer statements are autocommit, so locks are brief. A queued importer
write after our commit can only be an owner-brand write (fixed importer) -> converges.

**Journal / backup - OK with notes.** Intent fsynced inside the tx before commit, `committed` after; crash -> `--journal` rollback is CAS
on `after`, so a never-committed intent is a hard skip "newer_legitimate_write" (misleading reason, safe; covered by the 47-check test).
Pre-apply backup is a full `to_jsonb` row copy but written without fsync and outside the apply transaction (D7).

**Rollback - OK.** CAS on `after` incl. updated_at; a newer write is skipped, exit 2; compensating price_history row; parts touched.
Note D5: `already_rolled_back` compares price+url only - with before.url == after.url here, any later write that happened to restore the
foreign price would be reported as a soft skip (benign: only a buggy-mode foreign write could do that).

**min_price refresh - partly proven.** Apply path: tested (3/3 min == target). Rollback path: the 13-check test passes `--refresh-minmax`
to the rollback but asserts only the offers (D2). `refresh_minmax` runs after the apply connection is closed, in separate sessions -
reported, non-fatal; acceptable (scheduled refresh is the safety net).

**Meili pickup - covered (VERIFIED by code, effect ASSUMED until observed).** Meili docs carry `parts_catalog.min_price_ils/base_price`
only (meili_sync.py:127-143), incremental filter `updated_at > cutoff` (checkpoint = previous run START, 1 h margin). Both the apply
touch and `refresh_min_max_for_parts` (sets `updated_at=NOW()`, does not commit) bump `parts_catalog.updated_at`, so the next 2-h run
re-indexes the new min. Customer prices are computed from the DB, not from Meili (Meili only sorts). Post-check: read the 3 Meili docs
after the next sync.

**Customer-reference protection - not in the tool (D9).** Today 0 references (PII: cart_items 0 rows total, order_items 154 none of
these, watches/wishlist/reviews 0). The tool has no PII check; do it in the runbook immediately before the write.

## Defects / gaps

- **D1 (medium)** `refresh_minmax` (and `--rebase`) use `BACKEND_DATABASE_MODELS.async_session_factory` = `DATABASE_URL`, ignoring
  `--dsn`. A run with `--dsn` pointing elsewhere writes offers to one DB and min/max to another. Fix: refuse `--refresh-minmax`/`--rebase`
  when `--dsn` is given and differs from `DATABASE_URL` (or never pass `--dsn` in production - state it in the runbook).
- **D2 (medium)** min/max after ROLLBACK untested. Add: after rollback, the restored parts' `min_price_ils` == restored offer price; the
  newer-write part keeps its value.
- **D3 (medium)** `--accept` has no sandbox coverage (0 occurrences in either test) and its fulldiff allows ANY change to part
  min/max/base_price - it does not check min_price_ils == policy result. Add an `--accept` run to the 3-row test and assert
  min_price_ils == target for each repaired part.
- **D4 (medium)** The production manifest builder exists only inside the sandbox test (`build_manifest` from `classification_1043.json`
  `current_at_snapshot`). Production needs a committed builder that takes a FRESH read-only re-snapshot (price, url, updated_at, part_id,
  sku) right before the write, filters exactly the 3 ids, pins the sha256, and refuses if any row differs from the classification
  evidence (otherwise re-classify, do not just refresh `current`).
- **D5 (low)** rollback `already_rolled_back` ignores updated_at (see above).
- **D6 (low)** hard-skip reason for a failed-commit intent reads "newer_legitimate_write"; distinguish "intent_not_committed".
- **D7 (low)** pre-apply backup: fsync it, and/or take it inside the row transaction (current order can give false fulldiff alarms - safe direction).
- **D8 (low)** `--approval-ref` is free text, not checked against anything - record the owner's approval message id in it.
- **D9 (medium, process)** no customer-reference check in the tool - add to runbook (below).

## Production plan - gates for a plain data write

`scripts/restart_preflight.py` answers "would a RESTART cut running work"; a 3-row CAS write cuts nothing, so its SAFE is neither
required nor sufficient. The applicable gates:
1. **Owner approval for this production data change** (CLAUDE.md golden rule 8) - explicit, for these 3 offer ids and these targets.
2. **Offer policy pin**: `docker exec autospare_backend python3 /app/maintenance/verify_policy_pin.py` PASS; the tool's
   `gate_or_exit` also enforces it (exit 3).
3. **Timing**: not inside `run_all_tasks` (21:30 / 00:15 / 07:15 IL, refreshes min/max and writes parts_catalog) or the night
   pipeline; `job_queue.queue_busy()` false. Harvester stand-down is NOT required (NOWAIT + CAS; the fixed importer can only write the
   owner value) - but read the importer log/lock to avoid a batch on citroen/daihatsu/toyota models at that minute.
4. **Fresh evidence** (minutes before): re-snapshot the 3 rows; `--verify` = `NOT_APPLIED` x 3; owner-row re-check unchanged; PII refs = 0.
5. **Canary**: only 3 rows exist, so `--phase canary` processes all 3 (CANARY_N=20). Recommended: 1-row canary first (PT155, 5 owner
   rows) with `--exclude-offer` for the other two, `--accept`, then the remaining 2 (the repaired row is guard-skipped, accepted as a
   guard skip).
6. Run with `--refresh-minmax`, without `--dsn`.

## What I will re-query independently after the write

offer price/url/updated_at == journal `after`; part_id/sku/availability/warranty/price_usd unchanged; exactly one `price_history` row per
offer (source `cpie_overwrite_repair`, old = foreign, new = target); `min_price_ils` == target; `parts_catalog.updated_at` bumped;
fulldiff clean; no other Car-Parts.ie offer of these articles changed (the foreign brands' parts/qualified offers untouched); PII
references still 0; after the next meili run the 3 docs show the new min.

## Must-fix list

1. D4 - committed production manifest builder from a fresh re-snapshot, sha-pinned, refusing on drift.
2. D1 - refuse `--refresh-minmax`/`--rebase` with a foreign `--dsn` (or runbook: never `--dsn` in production).
3. D2 + D3 - add sandbox checks: min/max after rollback; `--accept` on the 3-row run with min_price_ils == target.
4. Runbook: owner approval (golden rule 8), policy pin, timing window, fresh `--verify` NOT_APPLIED x 3, PII refs 0, 1-row canary.
5. Low: D5-D8 (rollback soft-skip key incl. updated_at; distinct reason for uncommitted intent; fsync backup; approval reference).
