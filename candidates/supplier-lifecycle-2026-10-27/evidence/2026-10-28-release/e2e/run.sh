#!/bin/bash
# Run the isolated lifecycle E2E (candidate tree, disposable DB stack 'dbt' on the internal network dbtnet).
S=$(cd "$(dirname "$0")/.." && pwd)
E="-e PYTHONPATH=/app -e PYTHONDONTWRITEBYTECODE=1 -e ENVIRONMENT=test -e ENABLE_EXTERNAL_SUPPLIERS=0 -e DATABASE_URL=postgresql+asyncpg://autospare@sandbox_postgres:5432/autospare -e DATABASE_PII_URL=postgresql+asyncpg://autospare@sandbox_postgres:5432/autospare_pii -e REDIS_URL=redis://sandbox_redis:6379/0 -e RUNTIME_IDENTITY_DIR=/tmp/ri"
C="docker compose -p dbt -f $S/dbtests/compose.yml"; $C down -v >/dev/null 2>&1; $C up -d --wait >/dev/null 2>&1
docker exec dbt-sandbox_postgres-1 psql -U autospare -tAqc 'CREATE DATABASE autospare_pii' >/dev/null 2>&1
M="docker run --rm --network dbtnet -v ${TREE:-$S/cand3/backend}:/app:ro $E -w /app autosparefinder-backend:latest"
$M alembic -c alembic.ini upgrade head >/dev/null 2>&1 && $M alembic -c alembic_pii.ini upgrade head >/dev/null 2>&1 || { echo "migrate failed"; exit 2; }
echo "fresh disposable DB: PII $(docker exec dbt-sandbox_postgres-1 psql -U autospare -d autospare_pii -tAc 'select version_num from alembic_version')"
docker run --rm --network dbtnet -v ${TREE:-$S/cand3/backend}:/app:ro -v $S/e2e:/e2e:ro $E -w /app --memory 1500m autosparefinder-backend:latest python3 /e2e/e2e_lifecycle.py
