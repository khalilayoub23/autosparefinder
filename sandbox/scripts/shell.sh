#!/usr/bin/env bash
# Drop into the pentest toolbox shell
# Usage: ./scripts/shell.sh [command]
cd "$(dirname "$0")/.."
if ! docker ps --format '{{.Names}}' | grep -q '^sandbox_tools$'; then
    echo "Sandbox is not running. Start it first: ./scripts/start.sh"
    exit 1
fi
echo "Opening pentest toolbox shell (TARGET=http://sandbox_backend:8000)..."
if [ $# -gt 0 ]; then
    docker exec -it sandbox_tools bash -c "$*"
else
    docker exec -it sandbox_tools bash --login
fi
