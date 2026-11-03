# #120 — does every `run_all_tasks` task do its job? (read-only, 2026-10-28)

Sources (all read-only): the backend log of the 4 complete runs still in it (2026-10-27 21:30 / 00:15, 2026-10-28
07:15 / 21:30 Israel — every run `ok=17 skip=2 err=0`), each task's own result line, the task code, and read-only
counts before (21:24 IL) and after (22:02 IL) the 21:30 run. The in-memory full report (`/api/v1/admin/db-agent/status`)
was NOT read: it needs an admin session row = a production write. Raw lines: `t120_done_lines.txt`.

Legend: **useful** = writes the change it exists for · **no-op** = runs, changes nothing (candidate for removal or a
longer interval) · **broken** = reports `ok` but cannot do its job · **harmful** = its writes cause damage or churn ·
**unverified** = the task logs no result; its effect was not measured here.

| # | Task | 4 runs: status / elapsed | Evidence (own log line, every run) | Verdict |
|---|---|---|---|---|
| 1 | seed_system_settings | ok / 0 s | `inserted=0` | no-op (cheap idempotent seed — keep) |
| 2 | fill_car_brands | ok / 0 s | `updated=0 mapped=0 fallback=0` | no-op |
| 3 | normalize_imported_manufacturers | ok / 18–29 s | no result line | unverified |
| 4 | sync_models_from_catalog | ok / 0–1 s | no result line | unverified |
| 5 | backfill_catalog_fitment_from_xls | ok / 320–415 s | no result line; code: rewrites `compatible_vehicles` + `updated_at = NOW()` for every matched part with NO "unchanged" check, from a static file (`data/parts_database.xlsx`, 2026-03-30). Read-only: **34,784 of the 65,738 parts carrying xlsx fitment got `updated_at` inside tonight's 6-min task window** | **harmful (churn)**: ~35K identical rewrites ×3/day re-feed every delta task and the incremental Meili sync; root fix = skip when the merged JSON equals the stored JSON (as `merge_catalog_fitment…` already does) or run only when the file changes |
| 6 | backfill_mini_fitment_from_name_he | ok / 26–49 s | `parts_matched=0 inserted=0 skipped=169` (identical ×4) | **no-op** → remove (same as #119's BMW/Ford/Jaguar) |
| 7 | sync_manufacturer_registries | ok / 4–10 s | no result line | unverified |
| 8 | clean_part_names | ok / 30–58 s | `checked=14 updated=0` | no-op (delta; harmless) |
| 9 | normalize_part_types | ok / 9–16 s | delta `catalog=0 supplier=0` | no-op this period (delta safety net — keep) |
| 10 | normalize_categories | ok / 15–38 s | pass1 `mapped=0`, pass2 skipped | no-op this period (delta safety net — keep) |
| 11 | dedup_catalog_parts | ok / **601–632 s** | `flagged=26,209–29,889`, `early=True` every run (stops at its time budget) | **harmful (flag ping-pong, see below)**; never finishes its scan |
| 12 | normalize_availability | ok / 0 s | `updated=0` | no-op |
| 13 | fix_manufacturer_overflow | ok / 62–318 s | `flagged=25,950 / 9,745 / 14,554 / 66,187` | **harmful (flag ping-pong)** |
| 14 | flag_fake_skus | ok / 428–709 s | `flagged=12,361 / 0 / 772 / 1,422`, full scan of ~4.3 M rows each run | **harmful (flag ping-pong)**; 7–12 min full scan for it |
| 15 | refresh_min_max_prices | ok / 0 s | `updated=0 / 0 / 1 / 0` | no-op (delta — keep) |
| 16 | lookup_oem_spec | skipped | `OEM_LOOKUP_ENABLED=0` (LLM cron gate, CLAUDE.md rule) | intentionally off — the consumer of `needs_oem_lookup` never runs |
| 17 | enrich_pending_parts | skipped | `ENRICH_PARTS_ENABLED=0` | intentionally off |
| 18 | generate_image_embeddings | ok / 2–3 s | returns `ok, triggered 20`; the background batch logs **`0/20 embedded`** every run; tonight all 20 → `400 Bad Request` from `router.huggingface.co/hf-inference/models/openai/clip-vit-large-patch14`. Read-only: **`parts_images.embedding_generated` = TRUE on 0 rows; 1,700,141 pending** (unchanged by the run) | **broken** — reports ok, has never embedded one image; even if fixed, 60/day vs 1.7 M |
| 19 | validate_watchdog_actions | ok / 0 s | no result line (validates watchdog kill events) | unverified (cheap guard — keep) |

## The flag ping-pong (tasks 11, 13, 14 vs `db_cleanup_agent.task4_fix_oem_lookup_flag`)

- `dedup_catalog_parts`, `fix_manufacturer_overflow` and `flag_fake_skus` set `parts_catalog.needs_oem_lookup = TRUE`
  (+ `updated_at = NOW()`) — `fix_manufacturer_overflow` only on rows where it is FALSE/NULL.
- `db_cleanup_agent.task4_fix_oem_lookup_flag` sets `needs_oem_lookup = FALSE` (+ `updated_at = NOW()`) on ANY row with a
  non-empty `oem_number` and the flag TRUE, 500 rows per pass — and every row the overflow rule flags has an
  `oem_number` (its predicate tests it).
- So the same Renault/Chevrolet rows are flagged by the 3×/day run, reset by the cleanup agent, flagged again: the
  overflow count swings 25,950 → 9,745 → 14,554 → 66,187 instead of falling to 0. Measured: `needs_oem_lookup` (active)
  **86,859 at 21:24 → 162,850 at 22:02** (+75,991 during one run). The only consumer (`lookup_oem_spec`) is OFF.
- Damage: tens of thousands of `updated_at` bumps per run with no lasting change, feeding the delta tasks and the
  incremental Meili sync; 7–22 min of run time per run spent on it.
- Root fix (a separate change, owner decision on intent): ONE rule for `needs_oem_lookup` — decide whether "has an OEM
  number" or "OEM looks wrong" wins, make both agents use the same predicate, and stop bumping `updated_at` for a flag
  nobody consumes while `OEM_LOOKUP_ENABLED=0`.

## Totals

useful: none proven in this window (the delta safety nets 8–10, 15 had no input) · no-op: 1, 2, 6, 8, 9, 10, 12, 15 ·
broken: 18 · harmful: 5, 11, 13, 14 · intentionally off: 16, 17 · unverified (no result line): 3, 4, 7, 19.
