# Unified customer price policy — deploy & rollback (v1, 2026-10-29) — DEPLOYED 2026-10-29 07:48 Israel (FIXES_TRACKER)

Owner directive 2026-10-29 (top priority): one price policy, every agent and channel the same price. Policy:
`docs/PRICE_POLICY.md`. No schema change (PII stays `0042_supplier_lifecycle`). Touches the backend (bind-mounted,
`docker restart autospare_backend`) and the website (image rebuild + recreate of `autospare_frontend` only).

Rule: any result other than the stated success → STOP, roll back (R), report. Never `PRE_RESTART_FORCE`.

## Preconditions
1. Not 10:00–14:00 IL and not inside a `run_all_tasks` run (21:30 / 00:15 / 07:15 IL, ~35 min).
2. `cd candidates/price-policy-2026-10-29/files && sha256sum -c --quiet ../SHA256SUMS` → exit 0.
3. `cd /opt/autosparefinder && sha256sum -c --quiet candidates/price-policy-2026-10-29/BASE.sha256` → exit 0 (live files unchanged since the bundle was built).
4. `D=/root/asf-price-$(date -u +%Y%m%dT%H%M%SZ); mkdir -m 700 $D; tar czf $D/replaced.tgz $(cut -d' ' -f3 candidates/price-policy-2026-10-29/BASE.sha256); chmod 400 $D/*`
5. `bash backend/scripts/pre_restart.sh` → `RESTART PREFLIGHT: SAFE`.

## Deploy
| Step | Command | Success |
|---|---|---|
| 1 | `(cd candidates/price-policy-2026-10-29/files && tar cf - .) \| tar xf -` | exit 0 |
| 2 | `sed 's#  ./#  #' candidates/price-policy-2026-10-29/SHA256SUMS \| sha256sum -c --quiet` | no output |
| 3 | `docker restart autospare_backend` | exit 0; health 200; startup log 0 Traceback |
| 4 | `docker compose build frontend && docker compose up -d --no-deps frontend` | exit 0; `autospare_frontend` healthy |
| 5 | live verification (FIXES_TRACKER entry): same part → identical price on search, compare, cart unit, chat, order line, re-price | equal |

## Rollback (R)
`tar xzf $D/replaced.tgz && rm -f backend/price_policy.py backend/tests/test_price_policy_unified.py` → `BASE.sha256` check →
preflight → `docker restart autospare_backend` → `docker compose build frontend && docker compose up -d --no-deps frontend`.
