#!/bin/bash
# Install / verify the docker shim (scripts/docker_shim.py) as /usr/local/bin/docker.
#   install_docker_shim.sh            install (atomic copy; docker keeps working during it)
#   install_docker_shim.sh --verify   exit 0 only if the installed shim is byte-identical to the repo's
#   install_docker_shim.sh --remove   restore plain docker (rollback)
# Needs no restart of anything. The self-test uses a container name that does not exist, so a broken
# shim could never touch a real service.
set -Eeuo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$HERE/docker_shim.py"
DST="/usr/local/bin/docker"
case "${1:-install}" in
  --verify)
    cmp -s "$SRC" "$DST" && echo "shim installed and identical to the repo" || { echo "shim missing or different" >&2; exit 1; } ;;
  --remove)
    rm -f "$DST"; echo "shim removed" ;;
  install)
    install -m 0755 "$SRC" "$DST.new" && mv -f "$DST.new" "$DST"
    out="$("$DST" restart autospare_restart_guard_probe_nonexistent 2>&1 || true)"
    case "$out" in *"DENIED"*) echo "shim installed; raw protected restart is denied" ;;
                   *) echo "shim self-test FAILED: $out" >&2; rm -f "$DST"; exit 1 ;; esac ;;
  *) echo "usage: $0 [--verify|--remove]" >&2; exit 2 ;;
esac
