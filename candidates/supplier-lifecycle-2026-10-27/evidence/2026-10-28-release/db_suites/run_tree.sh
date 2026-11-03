#!/bin/bash
# usage: run_tree.sh <label> <backend tree> <pii target revision>
set -u; cd "$(dirname "$0")"; L=$1; T=$2; REV=$3; C="docker compose -p dbt -f compose.yml"
$C down -v >/dev/null 2>&1; $C up -d --wait >/dev/null 2>&1 || { echo "stack failed"; exit 1; }
docker exec dbt-sandbox_postgres-1 psql -U autospare -tAqc 'CREATE DATABASE autospare_pii' 
E="-e PYTHONPATH=/app -e PYTHONDONTWRITEBYTECODE=1 -e ENVIRONMENT=test -e RUN_DB_MUTATION_TESTS=1 -e DATABASE_URL=postgresql+asyncpg://autospare@sandbox_postgres:5432/autospare -e DATABASE_PII_URL=postgresql+asyncpg://autospare@sandbox_postgres:5432/autospare_pii -e REDIS_URL=redis://sandbox_redis:6379/0 -e RUNTIME_IDENTITY_DIR=/tmp/ri -e ENABLE_EXTERNAL_SUPPLIERS=0"
R="docker run --rm --network dbtnet -v $T:/app:ro $E -w /app --memory 2500m autosparefinder-backend:latest"
$R alembic -c alembic.ini upgrade head > $L.migrate.log 2>&1; echo "catalog migrate exit=$?"
$R alembic -c alembic_pii.ini upgrade $REV >> $L.migrate.log 2>&1; echo "pii migrate exit=$? -> $(docker exec dbt-sandbox_postgres-1 psql -U autospare -d autospare_pii -tAqc 'select version_num from alembic_version')"
docker exec dbt-sandbox_postgres-1 psql -U autospare -d autospare_pii -tAqc "INSERT INTO users (id,email,phone,password_hash,full_name,role,is_active,is_verified,is_admin,failed_login_count,created_at,updated_at) VALUES ('00000000-0000-0000-0000-000000000001','whatsapp@autospare.internal','+00000000000000','!disabled!','WhatsApp Bot','system',true,true,false,0,NOW(),NOW()) ON CONFLICT (id) DO NOTHING"; echo "sentinel user (same SQL as app startup) exit=$?"
FILES=${FILES_OVERRIDE:-$(grep -lE "isolated DB|isolated_db_guard|RUN_DB_MUTATION_TESTS" $T/tests/test_*.py | sed "s|$T/||" | tr "\n" " ")}
echo "files: $(echo $FILES | wc -w)"
timeout 1500 $R python3 -m pytest -q -p no:cacheprovider -rfEs --junitxml=/dev/stdout $FILES > $L.raw 2>&1; echo "exit=$?" >> $L.raw
grep -E 'passed|failed' $L.raw | grep -E ' in [0-9.]+s' | tail -1; tail -1 $L.raw
$C down -v >/dev/null 2>&1
