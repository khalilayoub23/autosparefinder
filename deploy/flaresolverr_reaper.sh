#!/bin/bash
# flaresolverr_reaper.sh — cap the FlareSolverr headless-Chrome LEAK.
#
# FlareSolverr has no session TTL and leaves ZOMBIE Chrome processes behind that are
# NOT tied to any tracked session (sessions.destroy does not reap them — verified
# 2026-07-23: destroying all 4 sessions dropped chrome 40→30). They accumulate (~0.8/min
# under 2 harvester sessions), pinning the flaresolverr container's CPU (saw 49 chrome →
# 577% CPU / host load 18.7 → DB statement-timeouts failing heal/parity tasks + starving
# sync_prices' heartbeat). Only a container restart clears the zombies; FlareSolverr is
# STATELESS (the car-parts.ie harvester recreates its sessions on demand and tolerates a
# brief FS outage via retries + its supervisor), so restarting it is safe.
#
# This runs from cron every 15 min and restarts an FS container ONLY when its Chrome
# process count exceeds the threshold (default 50) — so we reap on demand (minimal
# harvester disruption) instead of on a blind timer.
#
# Cron (root):  */15 * * * * /opt/autosparefinder/deploy/flaresolverr_reaper.sh >> /var/log/fs_reaper.log 2>&1
# Last Updated: 2026-07-23
THRESH="${FS_CHROME_THRESHOLD:-50}"
for c in flaresolverr flaresolverr2; do
    running=$(docker inspect -f '{{.State.Running}}' "$c" 2>/dev/null)
    [ "$running" = "true" ] || continue
    n=$(docker exec "$c" bash -lc 'ps -e 2>/dev/null | grep -c "[c]hrom"' 2>/dev/null | tr -dc '0-9')
    [ -z "$n" ] && n=0
    if [ "$n" -gt "$THRESH" ]; then
        echo "$(date '+%F %T') reaping $c — $n chrome procs > $THRESH"
        docker restart "$c" >/dev/null 2>&1 && echo "$(date '+%F %T') $c restarted"
    fi
done
