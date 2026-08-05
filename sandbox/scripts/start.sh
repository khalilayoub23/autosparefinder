#!/usr/bin/env bash
# Start the AutoSpareFinder sandbox environment
set -e
cd "$(dirname "$0")/.."

echo "=== AutoSpareFinder Sandbox — Starting ==="
echo "Network: sandbox_net (isolated from production)"
echo "Backend: http://127.0.0.1:18000"
echo "Meili:   http://127.0.0.1:17700"
echo ""

# Build toolbox if needed
docker compose -f docker-compose.sandbox.yml --env-file .env.sandbox build sandbox_tools

# Start all services
docker compose -f docker-compose.sandbox.yml --env-file .env.sandbox up -d

echo ""
echo "=== Waiting for backend health check ==="
for i in $(seq 1 40); do
    if curl -sf http://127.0.0.1:18000/api/v1/system/health > /dev/null 2>&1; then
        echo "  Backend healthy after ${i}x5s"
        break
    fi
    echo "  Waiting... ($((i*5))s)"
    sleep 5
done

echo ""
echo "=== Sandbox running ==="
docker compose -f docker-compose.sandbox.yml --env-file .env.sandbox ps
echo ""
echo "  Open a pentest shell:   ./scripts/shell.sh"
echo "  Status / health:        ./scripts/status.sh"
echo "  Stop sandbox:           ./scripts/stop.sh"
echo "  Backend logs:           docker logs -f sandbox_backend"
