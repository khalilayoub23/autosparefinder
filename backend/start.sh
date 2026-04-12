#!/bin/sh
set -eu

echo "[startup] Running catalog and PII migrations"
alembic -c alembic.ini upgrade head
alembic -c alembic_pii.ini upgrade head

echo "[startup] Seeding brand metadata"
python seed_brands.py
python clean_manufacturers_registry.py

if [ "${RUN_NORMALIZED_IMPORT_ON_STARTUP:-false}" = "true" ]; then
  echo "[startup] RUN_NORMALIZED_IMPORT_ON_STARTUP=true -> rebuilding normalized catalog"
  python import_parts_db.py
  python import_from_excel.py

  if [ "${RUN_DB_UPDATE_TASKS_ON_STARTUP:-true}" = "true" ]; then
    echo "[startup] Running db_update_agent task chain"
    python - <<'PY'
import asyncio
from BACKEND_DATABASE_MODELS import async_session_factory
from db_update_agent import run_all_tasks

async def main():
    async with async_session_factory() as db:
        report = await run_all_tasks(db)
        print(report)

asyncio.run(main())
PY
  fi
fi

workers="${UVICORN_WORKERS:-4}"
if [ "$workers" = "1" ]; then
  exec uvicorn BACKEND_API_ROUTES:app --host 0.0.0.0 --port 8000
fi

exec uvicorn BACKEND_API_ROUTES:app --host 0.0.0.0 --port 8000 --workers "$workers"