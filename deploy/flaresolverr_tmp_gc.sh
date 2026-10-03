#!/bin/sh
# Script: deploy/flaresolverr_tmp_gc.sh
# Purpose: Garbage-collect the temp dirs FlareSolverr's Chromium leaks into the
#          `flaresolverr` container's /tmp (a 512 MB tmpfs, docker-compose.yml).
# Why (FIXES_TRACKER #53, 2026-10-03): FlareSolverr/Chromium never removes its
#   per-launch profile dirs (/tmp/tmpXXXXXXXX), scoped dirs (org.chromium.Chromium.*)
#   or component-updater downloads (…chrome_url_fetcher_* ~6 MB each). After 24 days
#   of uptime the tmpfs was 100% full (12,164 entries); every new Chrome then failed
#   with "session not created: cannot connect to chrome", every cf_clearance solve
#   returned HTTP 500, and the car-parts.ie harvester stopped harvesting (it skips
#   cycles without a clearance cookie). Third-party code, so the fix is a GC.
# Process (runs INSIDE the container via docker exec, as the container user):
#   1. Collect every /tmp entry a live process is using: Chrome --user-data-dir
#      values plus any path held open as an fd/cwd under /proc.
#   2. Delete only top-level /tmp entries matching tmp* or org.chromium.Chromium.*
#      that are older than MIN_AGE minutes AND not in that in-use set.
#   Never touches .X11-unix / X locks / anything else; never signals a process.
# Usage: flaresolverr_tmp_gc.sh [MIN_AGE_MINUTES=120] [--dry-run]
# Scheduled by: /etc/cron.d/autospare-flaresolverr-tmp-gc (hourly, host cron).
# Last Updated: 2026-10-03
set -eu
MIN_AGE="${1:-120}"
DRY="${2:-}"
CONTAINER="${FLARESOLVERR_CONTAINER:-flaresolverr}"

docker exec -i "$CONTAINER" sh -s "$MIN_AGE" "$DRY" <<'INNER'
set -eu
MIN_AGE="$1"; DRY="${2:-}"
INUSE=$(mktemp -p /dev/shm fsgc.XXXXXX 2>/dev/null || mktemp)
trap 'rm -f "$INUSE"' EXIT
for p in /proc/[0-9]*; do
  tr '\0' '\n' < "$p/cmdline" 2>/dev/null | sed -n 's#^--user-data-dir=/tmp/\([^/]*\).*#\1#p' || true
  for l in "$p"/cwd "$p"/fd/*; do readlink "$l" 2>/dev/null || true; done | sed -n 's#^/tmp/\([^/]*\).*#\1#p'
done | sort -u > "$INUSE"
before=$(df -m /tmp | awk 'NR==2{print $3}')
n=0; kept=0
for f in $(find /tmp -mindepth 1 -maxdepth 1 -mmin "+$MIN_AGE" \( -name 'tmp*' -o -name 'org.chromium.Chromium.*' \)); do
  name=${f#/tmp/}
  if grep -qxF "$name" "$INUSE"; then kept=$((kept+1)); continue; fi
  n=$((n+1))
  [ "$DRY" = "--dry-run" ] || rm -rf -- "$f"
done
after=$(df -m /tmp | awk 'NR==2{print $3}')
echo "$(date -u +%FT%TZ) flaresolverr_tmp_gc: min_age=${MIN_AGE}m ${DRY:-live} removed=$n kept_in_use=$kept in_use_total=$(wc -l < "$INUSE") tmp_used_mb ${before}->${after}"
INNER
