# Section 7 — scheduled-run proof checklist (system-driven 14-day refresh, 2026-11-27)

Status of the criterion today: **BLOCKED** (nothing automatic can collect before 2026-11-27). Manual requeues and the night
pipeline do NOT count (owner, 2026-11-17 night). All times Israel (IL = UTC+2 in winter).

## What triggers the run (VERIFIED 2026-11-18 from code + DB)
- `requeue_completed_for_refresh(14)` (`harvesters/car_parts_ie_flaresolverr_harvester.py`) sets `status='pending'` for models
  with `status IN ('done','empty') AND last_harvested_at < NOW() - 14 days`. It runs when a cycle finds the queue drained,
  i.e. once per rest cycle (3600 s).
- Oldest `last_harvested_at` in the queue: **2026-11-13 09:17:04 IL**; models older than 13 days today: 0.
  => the first system requeue happens **2026-11-27 between 09:17 and ~10:17 IL** (the hourly cycle boundary after 09:17:04).
- A `partial` model (new, 2026-11-18) is handed out again `HARVESTER_PARTIAL_RETRY_S` (6 h) after it was recorded.

## T-1 day (2026-11-26) — pre-checks
1. `docker exec autospare_backend ps -eo pid,etime,cmd | grep flaresolverr_harvester` — one process; `git diff HEAD -- backend` empty;
   file hashes of harvester / importer / relay_store / routes/system / part_identity equal HEAD (method: tracker 2026-11-17 08:02 entry).
2. `cat /app/state/harvester_status.json` — `state: idle`, `cookie_age_s` present. `COLLECT_SECRET` set in the container env.
3. Free disk on `/app/state` >= 10 GB (`df -h /app/state`); the harvest writes payloads + logs per model.
4. **Evidence preservation:** the relay store garbage-collects files older than 3 days at the next completed run.
   Archive BEFORE the trigger: `candidates/core-identity-2026-11-16/relay_evidence_archive/` holds the 2026-11-17 set (sha256 98bb585c…).
   On 11-27 at ~09:00 run `tar -czf /app/state/relay_archive_<ts>.tar.gz -C /app/state relay` (verify by extract + sha256 compare) and `docker cp` it to the repo.

## T0 (2026-11-27, 09:17–10:30 IL) — observe
- `docker exec autospare_backend grep -a "Requeued" /app/state/logs/flaresolverr_harvester.log | tail -3`
  must show `Queue drained — full IL pass complete. Requeued N models >14d old for refresh` with N > 0 (system, not manual).
- Compare: no `UPDATE harvest_queue` was issued by an operator session that day (`docker logs --since 6h autospare_backend`, shell history of the session).
- Per model: `importer finished ok (run <id>)`; `PARTIAL collection` lines list every missing page; `cookie age` lines show age <= TTL (1500 s)
  or an explicit WARNING.

## T0 + first 20 models — verify (read-only)
1. Result files: every `/app/state/relay/results/*.json` of the day has `state: ok`, `exit_code: 0`; skip reasons in the `tail` are only
   `brand_unresolved` / `offer_owner_brand_unresolved` (list them; compare against `article_brand_extensions.json` coverage).
2. Run `verify_relay_runs.py` (this folder):
   `docker exec -i -w /app autospare_backend python3 - 2026-11-27T07:00 2026-11-27T12:00 < verify_relay_runs.py 2>/dev/null`
   PASS = `offer_check` only `price_eq_owner_brand`; `item_check` without `own_offer_price_MISMATCH`, `no_own_offer`, `no_part_row`;
   `dup_part_supplier_untyped = 0`, `dup_supplier_sku = 0`; scoped offers have no `@` in `/api/v1/parts/<id>/suppliers` (spot-check 10).
3. Fitment guard live proof: any hybrid / BEV model in the batch reports `fitment_skipped > 0` and `0` new rows for ICE-only items on pure-BEV models;
   the 15 hybrid rows deleted on 2026-11-18 (manifest 2235fc56…) are not re-created (`SELECT count(*) FROM part_vehicle_fitment WHERE id::text ~ '^(58274d4d|901962df|…)'` = 0).
4. base_price: sample >= 100 touched parts, recompute with `price_policy` (Car-Parts.ie is IE, 0 % VAT) — 0 mismatches.
5. Partial handling: every model that logged PARTIAL has `harvest_queue.status = 'partial'` (not `done`) and is re-claimed after 6 h.
6. Harvester health: `[car_parts_ie_healthcheck]` lines `status=ok`; no `could not mint cf_clearance` without a following fail-closed `too old` count.

## Closure rule
The criterion turns PASS only when 1–6 hold for the system-requeued models. If any check fails: record the exact failure in `FIXES_TRACKER.md`,
do not substitute a manual requeue, and open the root-cause path (two-attempt rule, then Opus).
