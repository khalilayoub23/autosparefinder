# Rollback source — backend code LIVE since 2026-10-18 22:34:13 UTC (FIXES_TRACKER #69 §16)

The running backend loaded its code at 2026-10-18 22:34:13 UTC. The files on disk were edited in place
afterwards (bind mount; no snapshot existed). These are the 9 runtime modules as they were at that start,
reconstructed by `reconstruct.py` from the session transcript:
43 recorded edits undone newest-first; every step proven by forward replay (byte-identical), the two
slice-based edits proven by re-executing the recorded script itself.

Independent check: all 8 traceback frames the LIVE process logged since the start (`live_traceback_frames.txt`)
fall in the function they name in these files (the current repository files mismatch 4 of 8).

`vehicle_model_match.py` did NOT exist at the start: rolling back means removing it.
Files not listed here were not modified after the start.

Rollback = copy `backend/*` (and, for the bridge, `whatsapp-bridge/index.js`) over `/opt/autosparefinder/backend/`, remove `backend/vehicle_model_match.py`,
then the restart procedure (docs/DEPLOY_69_SEARCH.md). Verify with `sha256sum -c MANIFEST.sha256` (run in backend/).

2026-10-20 (#70): added `backend/social/whatsapp_provider.py`, `backend/routes/utils.py` and
`whatsapp-bridge/index.js` (bridge process started 2026-10-18 22:35:05 UTC) — each had exactly one recorded
edit after its process started; reconstructed by reversing it and proven by byte-identical forward replay.
`MANIFEST.sha256` now lists paths relative to this folder (`backend/…`, `whatsapp-bridge/…`).

2026-10-20 (#71): added `backend/routes/webhooks.py` (2 recorded edits reversed, each proven by forward replay),
`backend/category_learning.py` (= git HEAD; proven: replaying today's recorded scripts on HEAD reproduces the current
file byte for byte) and `backend/db_cleanup_agent.py` (= git HEAD; NOT replay-proven — the replay tripped on scripts that
also edited notify_backlog.py; supported by the session-start git status (unmodified) and no recorded write before
2026-10-20 17:10 UTC). `backend/notify_backlog.py` is new: rollback = remove it. MANIFEST now 15 files.
