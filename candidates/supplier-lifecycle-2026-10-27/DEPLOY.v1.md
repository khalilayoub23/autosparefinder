# Supplier lifecycle candidate — deploy & rollback runbook

**Not deployed.** This bundle lives OUTSIDE `backend/` on purpose: `backend/` is bind-mounted into
`autospare_backend` (`restart: unless-stopped`), and every container start runs
`alembic … upgrade head && alembic -c alembic_pii.ini upgrade head && container_start.sh &` and then uvicorn.
While the candidate sat in `backend/`, any restart — including an automatic one after a crash — and even the
next lazy import (`agents/owner_console.py` on the owner's next WhatsApp message) would have activated it.
On 2026-10-27 the candidate was moved here and `backend/` was restored to the running deployment (verified:
compiled-cache source sizes match the deployed files; 0 differences live vs deployed).

Contents: `MANIFEST.txt` (28 paths) · `files/` (the candidate versions) · `SHA256SUMS` ·
`candidate_vs_deployed.patch` (review diff) · `evidence/` (rehearsal + regression artifacts).
`*.v1*` = the pre-safety-gate candidate, kept for reference only.

## Deploy (each step needs the owner's explicit OK)

1. `cd /opt/autosparefinder && (cd candidates/supplier-lifecycle-2026-10-27/files && sha256sum -c ../SHA256SUMS)`
2. Back up the deployed versions of the manifest paths:
   `tar czf /root/pre-supplier-lifecycle-$(date +%Y%m%d%H%M).tgz $(cat candidates/supplier-lifecycle-2026-10-27/MANIFEST.txt | xargs -I{} sh -c 'test -e {} && echo {}')`
3. `bash backend/scripts/pre_restart.sh` → must print `RESTART PREFLIGHT: SAFE` (never `PRE_RESTART_FORCE`).
4. `(cd candidates/supplier-lifecycle-2026-10-27/files && tar cf - backend) | tar xf -`
5. `docker compose up --dry-run -d --no-deps backend` (only the backend may be listed), then
   `docker compose up -d --no-deps --force-recreate backend`.
6. Verify: PII `alembic_version` = `0042_supplier_lifecycle`; startup log has no traceback; every switch below is OFF.

Everything new starts **OFF** (`supplier_flags.py`; only the exact value `1` enables):
`BOAZ_TASKS_ENABLED` · `EMAIL_AGENT_SCAN_SPAM` · `SUPPLIER_ROUTING_ENABLED` · `NIR_OUTREACH_ENABLED` ·
`SUPPLIER_AUTO_ORDER_ENABLED`. The safety gates (procurement gate before any supplier spend, separate
authorizations, DB CHECKs) are always on. Each switch is a separate owner decision after the deploy.

## Rollback (rehearsed 2026-10-27 on a throwaway Postgres — `evidence/rehearsal_result.txt`)

**Order matters — downgrade with the CANDIDATE code still in place, then restore the code.**
1. Export what the downgrade destroys (it is not lossless):
   `agent_authorizations`, `supplier_outreach`, `procurement_orders`, `procurement_events` (whole tables) and
   `email_agent_messages.source_folder / supplier_routed_at / supplier_route` (columns; the rows stay).
   e.g. `docker exec autospare_postgres_pii pg_dump -U $POSTGRES_USER -d $POSTGRES_DB -t agent_authorizations -t supplier_outreach -t procurement_orders -t procurement_events > /root/supplier_lifecycle_tables.sql`
2. `docker exec autospare_backend alembic -c alembic_pii.ini downgrade 0041_email_agent_owner_flow`
3. Restore the backed-up files (step 2 of Deploy) and delete the manifest paths that did not exist before.
4. `pre_restart.sh` → recreate the backend as above.

Rehearsed facts: downgrade with the old code fails (`Can't locate revision 0042`, exit 255) and leaves 0042 in
place; if the old code is restored WITHOUT downgrading, every container start fails the migration step, which
skips `container_start.sh` (stale-lock clear / worker resume) while uvicorn still serves — the old code itself
works on the 0042 schema. After a correct rollback the old code works on 0041 and a later re-deploy upgrades
cleanly. A failing 0042 (forced error) leaves the database at 0041 with nothing half-applied (one transaction).
