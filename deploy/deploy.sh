#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "Pulling latest changes..."
git pull origin main

echo "Validating docker compose config..."
docker compose config -q

echo "Building backend/frontend images (with latest base layers)..."
docker compose build --pull backend frontend

echo "Recreating backend/frontend/nginx containers from rebuilt images..."
docker compose up -d --no-deps --force-recreate backend frontend nginx

echo "Waiting for backend health..."
for i in {1..30}; do
  if docker compose ps --format json backend 2>/dev/null | grep -q '"Health":"healthy"'; then
    echo "Backend is healthy"
    break
  fi
  if [[ "$i" -eq 30 ]]; then
    echo "Backend did not become healthy in time"
    docker compose logs --tail=150 backend
    exit 1
  fi
  sleep 5
done

echo "Waiting for nginx health..."
for i in {1..20}; do
  if docker compose ps --format json nginx 2>/dev/null | grep -q '"Health":"healthy"'; then
    echo "Nginx is healthy"
    break
  fi
  if [[ "$i" -eq 20 ]]; then
    echo "Nginx did not become healthy in time"
    docker compose logs --tail=120 nginx
    exit 1
  fi
  sleep 3
done

echo "Current compose status:"
docker compose ps

echo "Running post-deploy manufacturer fitment audit with delta..."
if [ -x "./scripts/run_manufacturer_fitment_audit_with_delta.sh" ]; then
  ./scripts/run_manufacturer_fitment_audit_with_delta.sh || true
else
  echo "Audit script not found/executable, skipping"
fi

echo "Deployed!"
