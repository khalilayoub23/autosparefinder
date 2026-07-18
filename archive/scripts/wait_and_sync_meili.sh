#!/bin/bash
# Watches for task3 to finish, then triggers full meili rebuild
LOG=/opt/autosparefinder/logs/meili_post_categorize_sync.log
THRESHOLD=5000  # trigger when uncategorized drops below this

echo "[$(date -u +%FT%TZ)] Watcher started — polling every 5m until uncategorized < $THRESHOLD" | tee -a "$LOG"

while true; do
    UNCATEGORIZED=$(docker exec autospare_postgres_catalog psql -U autospare -d autospare -t -c \
        "SELECT COUNT(*) FROM parts_catalog WHERE is_active=true AND (category IS NULL OR category='');" \
        2>/dev/null | tr -d ' \n')

    echo "[$(date -u +%FT%TZ)] Uncategorized: $UNCATEGORIZED" | tee -a "$LOG"

    if [[ "$UNCATEGORIZED" =~ ^[0-9]+$ ]] && [ "$UNCATEGORIZED" -lt "$THRESHOLD" ]; then
        echo "[$(date -u +%FT%TZ)] Threshold reached ($UNCATEGORIZED < $THRESHOLD). Starting meili full rebuild..." | tee -a "$LOG"
        docker exec autospare_backend python3 /app/meili_sync.py --rebuild >> "$LOG" 2>&1
        EXIT=$?
        if [ $EXIT -eq 0 ]; then
            echo "[$(date -u +%FT%TZ)] Meili rebuild complete." | tee -a "$LOG"
        else
            echo "[$(date -u +%FT%TZ)] Meili rebuild FAILED (exit $EXIT)." | tee -a "$LOG"
        fi
        break
    fi

    sleep 300  # check every 5 minutes
done
