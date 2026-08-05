#!/usr/bin/env bash
# Show sandbox health status
cd "$(dirname "$0")/.."

echo "=== AutoSpareFinder Sandbox Status ==="
echo ""

echo "--- Containers ---"
docker compose -f docker-compose.sandbox.yml --env-file .env.sandbox ps 2>/dev/null || echo "  (not running)"
echo ""

echo "--- Backend health ---"
HEALTH=$(curl -sf http://127.0.0.1:18000/api/v1/system/health 2>/dev/null)
if [ -n "$HEALTH" ]; then
    echo "  OK: $HEALTH"
else
    echo "  UNREACHABLE (check: docker logs sandbox_backend)"
fi
echo ""

echo "--- Meilisearch health ---"
MEILI=$(curl -sf http://127.0.0.1:17700/health 2>/dev/null)
if [ -n "$MEILI" ]; then
    echo "  OK: $MEILI"
else
    echo "  UNREACHABLE"
fi
echo ""

echo "--- Network isolation check ---"
# Confirm sandbox_net cannot reach production
docker exec sandbox_tools sh -c \
    'curl -sf --connect-timeout 2 http://autospare_backend:8000 > /dev/null 2>&1 && echo "  WARN: can reach production backend!" || echo "  OK: production backend unreachable from sandbox"' \
    2>/dev/null || echo "  (toolbox not running)"

echo ""
echo "--- Resource usage ---"
docker stats --no-stream --format "  {{.Name}} | mem: {{.MemUsage}} ({{.MemPerc}}) | cpu: {{.CPUPerc}}" \
    sandbox_backend sandbox_postgres sandbox_redis sandbox_meili sandbox_tools 2>/dev/null

echo ""
echo "--- Sandbox API quick-test ---"
curl -s http://127.0.0.1:18000/api/v1/system/health 2>/dev/null | python3 -m json.tool 2>/dev/null || \
    echo "  (backend not responding)"
