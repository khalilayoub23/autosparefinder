#!/usr/bin/env bash
# Stop and remove the sandbox (keeps volumes/data intact)
cd "$(dirname "$0")/.."
echo "=== Stopping AutoSpareFinder Sandbox ==="
docker compose -f docker-compose.sandbox.yml --env-file .env.sandbox down
echo "Done. Data volumes preserved."
echo "(To also wipe all sandbox data: docker compose -f docker-compose.sandbox.yml --env-file .env.sandbox down -v)"
