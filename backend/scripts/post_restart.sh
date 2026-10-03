#!/bin/bash
# Run AFTER a docker restart / recreate of the backend.
# Resumes the importer subprocesses pre_restart.sh captured — via the single implementation
# in scripts/restart_workers.py (FIXES_TRACKER #60). container_start.sh calls the same
# helper automatically on every container start; the state is consumed by whichever runs
# first, so running this script afterwards can never launch a second copy.
#
# Exit code: 0 = everything captured was resumed (or there was nothing to resume);
#            3 = at least one captured worker could NOT be resumed (the reason is printed).
echo "=== POST-RESTART RESUME ===" >&2
docker exec autospare_backend python3 /app/scripts/restart_workers.py resume
rc=$?
if [ "$rc" -eq 0 ]; then
    echo "=== POST-RESTART RESUME DONE ===" >&2
else
    echo "=== POST-RESTART RESUME INCOMPLETE (exit $rc) — see NOT RESUMED lines above ===" >&2
fi
exit $rc
