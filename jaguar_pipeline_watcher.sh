#!/bin/bash
set -e
LOG=/opt/autosparefinder/logs/jaguar_pipeline.log
echo "[$(date -u)] Waiting for scraper PID 303886 to finish..." | tee -a "$LOG"
while kill -0 303886 2>/dev/null; do
    PROG=$(python3 -c "import json; d=json.load(open('/opt/autosparefinder/jaguar_scraper_progress.json')); print(f\"{d['last_page']}/{d['total_pages']} ({100*d['last_page']//d['total_pages']}%) written={d['total_written']}\")" 2>/dev/null || echo "unknown")
    echo "[$(date -u)] Scraper still running: $PROG" | tee -a "$LOG"
    sleep 120
done
echo "[$(date -u)] Scraper finished. Starting import..." | tee -a "$LOG"

# Run import
python3 /opt/autosparefinder/backend/sng_barratt_jaguar_import.py 2>&1 | tee -a "$LOG"

echo "[$(date -u)] Import done. Starting scoped Meilisearch sync for Jaguar..." | tee -a "$LOG"

# Scoped Meilisearch sync
docker exec autospare_backend python /app/meili_sync.py --manufacturer Jaguar --no-rebuild 2>&1 | tee -a "$LOG"

echo "[$(date -u)] === JAGUAR PIPELINE COMPLETE ===" | tee -a "$LOG"

# Final DB count
docker exec autospare_postgres_catalog psql -U autospare -d autospare -c \
    "SELECT COUNT(*) as jaguar_parts FROM parts_catalog WHERE manufacturer='Jaguar' AND is_active;" 2>&1 | tee -a "$LOG"
