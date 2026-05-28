#!/bin/bash
# Run Jaguar import + Meilisearch sync after scraper finishes.
# Usage: bash run_jaguar_import.sh
set -e

NDJSON="/opt/autosparefinder/jaguar_parts_raw.ndjson"
PROGRESS="/opt/autosparefinder/jaguar_scraper_progress.json"
LOG="/opt/autosparefinder/logs/jaguar_import.log"

echo "[$(date)] Starting Jaguar import pipeline"

# Wait for scraper to finish if still running
if pgrep -f sng_barratt_jaguar_scraper.py > /dev/null 2>&1; then
  echo "[$(date)] Scraper still running — waiting..."
  wait $(pgrep -f sng_barratt_jaguar_scraper.py)
  echo "[$(date)] Scraper finished"
fi

LINES=$(wc -l < "$NDJSON" 2>/dev/null || echo 0)
echo "[$(date)] NDJSON has $LINES lines"
if [ "$LINES" -lt 100 ]; then
  echo "ERROR: NDJSON has too few lines ($LINES). Aborting."
  exit 1
fi

# Run import (host → postgres_catalog via localhost:5432)
echo "[$(date)] Starting DB import..."
DATABASE_URL="postgresql://autospare:e4b79d75ca640dbe7f259618f078b82f21573e419308f668beed5e20b26b1d43@localhost:5432/autospare" \
  python3 /opt/autosparefinder/backend/sng_barratt_jaguar_import.py

echo "[$(date)] Import done. Running Meilisearch sync..."

# Sync Jaguar parts to Meilisearch inside the backend container
docker exec autospare_backend python /app/meili_sync.py \
  --manufacturer Jaguar \
  --no-rebuild 2>&1 | tail -20

echo "[$(date)] Meilisearch sync done."

# Final count
docker exec autospare_postgres_catalog psql -U autospare -d autospare -c \
  "SELECT COUNT(*) AS jaguar_parts FROM parts_catalog WHERE manufacturer='Jaguar' AND is_active;"

echo "[$(date)] All done!"
