#!/bin/sh
# AutoSpareFinder backup script
# Runs daily at 02:00 via crond

BACKUP_DIR=/var/backups/autosparefinder
DATE=$(date +%Y-%m-%d)
DAY=$(date +%u)
WEEK=$(date +%V)
MONTH=$(date +%m)

mkdir -p $BACKUP_DIR/daily $BACKUP_DIR/weekly $BACKUP_DIR/monthly $BACKUP_DIR/meilisearch

# Catalog DB
pg_dump -h postgres_catalog -U autospare -d autospare | gzip > $BACKUP_DIR/daily/catalog_$DATE.sql.gz

# PII DB
pg_dump -h postgres_pii -U autospare -d autospare_pii | gzip > $BACKUP_DIR/daily/pii_$DATE.sql.gz

# Weekly backup (Sunday)
if [ "$DAY" = "7" ]; then
  cp $BACKUP_DIR/daily/catalog_$DATE.sql.gz $BACKUP_DIR/weekly/catalog_W${WEEK}.sql.gz
  cp $BACKUP_DIR/daily/pii_$DATE.sql.gz $BACKUP_DIR/weekly/pii_W${WEEK}.sql.gz
fi

# Monthly backup (1st of month)
if [ "$(date +%d)" = "01" ]; then
  cp $BACKUP_DIR/daily/catalog_$DATE.sql.gz $BACKUP_DIR/monthly/catalog_${MONTH}.sql.gz
  cp $BACKUP_DIR/daily/pii_$DATE.sql.gz $BACKUP_DIR/monthly/pii_${MONTH}.sql.gz
fi

# Meilisearch snapshot
curl -X POST http://meilisearch:7700/snapshots \
  -H "Authorization: Bearer $MEILI_MASTER_KEY"

# Cleanup — keep 7 daily, 4 weekly, 3 monthly
ls -t $BACKUP_DIR/daily/catalog_*.sql.gz | tail -n +8 | xargs rm -f
ls -t $BACKUP_DIR/daily/pii_*.sql.gz | tail -n +8 | xargs rm -f
ls -t $BACKUP_DIR/weekly/*.sql.gz | tail -n +9 | xargs rm -f
ls -t $BACKUP_DIR/monthly/*.sql.gz | tail -n +7 | xargs rm -f

echo "Backup completed: $DATE"
