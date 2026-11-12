#!/bin/bash
# THE only sanctioned way to restart / stop / recreate a production service.
#
#   backend/scripts/guarded_restart.sh <operation-name> -- docker restart autospare_backend
#
# Runs a FRESH preflight and executes the command ONLY when it says SAFE; otherwise exits 4 and
# the command is never started. The decision lives in restart_guard.py - this wrapper adds
# nothing the caller could weaken: no flag, no environment variable, no fallback.
# For a backend restart run `bash backend/scripts/pre_restart.sh` first (it is guarded too), then
# this wrapper. Chaining the two with `;` is harmless now: the restart is INSIDE the guard.
set -Eeuo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
op="${1:-}"
if [ -z "$op" ] || [ "${2:-}" != "--" ] || [ "$#" -lt 3 ]; then
    echo "usage: guarded_restart.sh <operation-name> -- <command> [args...]" >&2
    exit 4
fi
shift
exec python3 "$HERE/restart_guard.py" --op "$op" "$@"
