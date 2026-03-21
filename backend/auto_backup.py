"""
auto_backup.py — pg_dump both databases every 24 h.
Keeps last 7 daily + 4 weekly + 3 monthly backups per DB (retention tagging).
Called via _backup_loop() registered at startup.
"""
from __future__ import annotations

import asyncio
import glob
import logging
import os
import re
import subprocess
import time
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

BACKUP_DIR = os.environ.get("BACKUP_DIR", "/backups")
DATABASE_URL = os.environ.get("DATABASE_URL", "")
PII_DATABASE_URL = os.environ.get("PII_DATABASE_URL", "")
KEEP_LAST = 7  # Keep last 7 daily backups


def _pg_dump(db_url: str, out_path: str) -> bool:
    """Invoke pg_dump for one connection URL. Returns True on success."""
    m = re.match(
        r"postgres(?:ql)?://([^:@]+)(?::([^@]*))?@([^/:]+)(?::(\d+))?/(.+)",
        db_url,
    )
    if not m:
        logger.error("auto_backup: cannot parse db_url")
        return False
    user, password, host, port, dbname = m.groups()
    env = {**os.environ}
    if password:
        env["PGPASSWORD"] = password
    cmd = [
        "pg_dump",
        "-h", host,
        "-p", port or "5432",
        "-U", user,
        "-Fp", "--no-owner", "--no-acl",
        "-f", out_path,
        dbname,
    ]
    result = subprocess.run(cmd, env=env, capture_output=True, text=True)
    if result.returncode != 0:
        logger.error("auto_backup pg_dump failed: %s", result.stderr[:500])
        return False
    return True


def _tag_backup(backup_path: str) -> str:
    """
    Tag backup with retention label: daily | weekly | monthly
    Daily: today's backup (keep last 7)
    Weekly: oldest backup from each Monday (keep last 4)
    Monthly: oldest backup from each month (keep last 3)
    
    Returns tag string: 'daily' | 'weekly' | 'monthly'
    """
    now = datetime.utcnow()
    backup_mtime = datetime.utcfromtimestamp(os.path.getmtime(backup_path))
    
    # Tag today's backups as 'daily'
    if backup_mtime.date() == now.date():
        tag = "daily"
    # Tag Monday backups as 'weekly'
    elif backup_mtime.weekday() == 0:  # Monday
        tag = "weekly"
    # Tag first-of-month backups as 'monthly'
    elif backup_mtime.day <= 7:
        tag = "monthly"
    else:
        tag = "daily"
    
    # Write metadata file
    meta_path = f"{backup_path}.meta"
    with open(meta_path, "w") as f:
        f.write(f"tag={tag}\n")
        f.write(f"created_at={backup_mtime.isoformat()}\n")
        f.write(f"size_mb={os.path.getsize(backup_path) / 1_048_576:.1f}\n")
    
    logger.info(f"auto_backup: tagged {os.path.basename(backup_path)} as '{tag}'")
    return tag


def _prune_old_backups_smart(db_label: str) -> None:
    """
    Smart pruning with retention tags:
    - Keep last 7 daily backups
    - Keep last 4 weekly backups
    - Keep last 3 monthly backups
    """
    pattern = os.path.join(BACKUP_DIR, f"{db_label}_*.sql")
    files = sorted(glob.glob(pattern))
    
    if not files:
        return
    
    daily_count = 0
    weekly_count = 0
    monthly_count = 0
    
    # Iterate from newest to oldest (reversed)
    for backup_path in reversed(files):
        meta_path = f"{backup_path}.meta"
        tag = "daily"  # default
        
        if os.path.exists(meta_path):
            try:
                with open(meta_path, "r") as f:
                    for line in f:
                        if line.startswith("tag="):
                            tag = line.split("=")[1].strip()
                            break
            except Exception as e:
                logger.warning(f"auto_backup: failed to read tag from {meta_path}: {e}")
        
        # Count and keep based on tag
        if tag == "daily":
            daily_count += 1
            if daily_count > 7:
                _delete_backup(backup_path)
        elif tag == "weekly":
            weekly_count += 1
            if weekly_count > 4:
                _delete_backup(backup_path)
        elif tag == "monthly":
            monthly_count += 1
            if monthly_count > 3:
                _delete_backup(backup_path)


def _delete_backup(backup_path: str) -> None:
    """Delete backup file and its metadata."""
    try:
        os.remove(backup_path)
        meta_path = f"{backup_path}.meta"
        if os.path.exists(meta_path):
            os.remove(meta_path)
        logger.info(f"auto_backup: deleted old backup {os.path.basename(backup_path)}")
    except OSError as exc:
        logger.warning(f"auto_backup: delete error {backup_path}: {exc}")


async def run_backup(dry_run: bool = False) -> dict:
    """Back up autospare + autospare_pii. Returns {label: status} dict."""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    results: dict = {}
    for label, url in [("autospare", DATABASE_URL), ("autospare_pii", PII_DATABASE_URL)]:
        if not url:
            logger.warning("auto_backup: no URL configured for %s — skipping", label)
            results[label] = "skipped"
            continue
        out_path = os.path.join(BACKUP_DIR, f"{label}_{ts}.sql")
        if dry_run:
            logger.info("auto_backup [dry-run]: would write %s", out_path)
            results[label] = "dry_run"
            continue
        t0 = time.monotonic()
        ok = await asyncio.get_event_loop().run_in_executor(None, _pg_dump, url, out_path)
        elapsed = round(time.monotonic() - t0, 1)
        if ok:
            size_mb = round(os.path.getsize(out_path) / 1_048_576, 1)
            tag = _tag_backup(out_path)
            logger.info(
                "auto_backup: %s → %s (%.1f MB, %.1f s, tag: %s)", label, out_path, size_mb, elapsed, tag
            )
            _prune_old_backups_smart(label)
            results[label] = f"ok:{out_path}:{tag}"
        else:
            results[label] = "error"
    return results


async def _backup_loop() -> None:
    """Runs every 24 h. Registered at startup in BACKEND_API_ROUTES.py."""
    await asyncio.sleep(300)  # 5-min startup grace period
    while True:
        try:
            res = await run_backup()
            logger.info("auto_backup loop: %s", res)
        except Exception as exc:
            logger.error("auto_backup loop error: %s", exc)
        await asyncio.sleep(86_400)


async def restore_latest_backup(db_label: str = "autospare", dry_run: bool = True) -> dict:
    """
    Restore test: verify latest backup is valid (Gap 5).
    
    - Finds newest backup for db_label
    - Restores to temporary test DB (test_restore_<timestamp>)
    - Runs basic validation queries
    - Reports success/failure
    - If not dry_run, drops the test DB; otherwise keeps it for inspection
    
    Args:
        db_label: "autospare" or "autospare_pii"
        dry_run: If True, keeps test DB after test; if False, drops it
    
    Returns:
        {"status": "ok"|"error", "test_db": str, "backup_file": str, ...}
    """
    import psycopg2
    from psycopg2 import sql
    import tempfile
    import shutil
    
    pattern = os.path.join(BACKUP_DIR, f"{db_label}_*.sql")
    files = sorted(glob.glob(pattern), reverse=True)  # newest first
    
    if not files:
        return {"status": "error", "reason": f"no backups found for {db_label}"}
    
    backup_path = files[0]
    logger.info(f"restore_latest_backup: using {os.path.basename(backup_path)}")
    
    # Parse original DB URL to extract credentials
    original_url = DATABASE_URL if db_label == "autospare" else PII_DATABASE_URL
    if not original_url:
        return {"status": "error", "reason": f"no URL configured for {db_label}"}
    
    m = re.match(
        r"postgres(?:ql)?://([^:@]+)(?::([^@]*))?@([^/:]+)(?::(\d+))?/(.+)",
        original_url,
    )
    if not m:
        return {"status": "error", "reason": "cannot parse original db_url"}
    
    user, password, host, port, orig_dbname = m.groups()
    port = int(port or 5432)
    
    # Create test DB name
    test_ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    test_dbname = f"test_restore_{db_label}_{test_ts}"
    
    try:
        # Connect to postgres DB (not the actual DB) to create test DB
        conn = psycopg2.connect(
            host=host,
            port=port,
            user=user,
            password=password or "",
            database="postgres",
        )
        conn.autocommit = True
        cur = conn.cursor()
        
        # Create empty test DB
        cur.execute(sql.SQL("CREATE DATABASE {}")
                .format(sql.Identifier(test_dbname)))
        logger.info(f"Created test database: {test_dbname}")
        cur.close()
        conn.close()
        
        # Restore backup into test DB
        env = {**os.environ}
        if password:
            env["PGPASSWORD"] = password
        
        restore_cmd = [
            "psql",
            "-h", host,
            "-p", str(port),
            "-U", user,
            "-d", test_dbname,
            "-f", backup_path,
        ]
        
        result = subprocess.run(
            restore_cmd,
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
        )
        
        if result.returncode != 0:
            logger.error(f"restore_latest_backup: psql restore failed: {result.stderr[:500]}")
            # Clean up failed test DB
            conn = psycopg2.connect(
                host=host,
                port=port,
                user=user,
                password=password or "",
                database="postgres",
            )
            conn.autocommit = True
            conn.cursor().execute(
                sql.SQL("DROP DATABASE IF EXISTS {}")
                .format(sql.Identifier(test_dbname))
            )
            conn.close()
            return {"status": "error", "reason": f"restore failed: {result.stderr[:200]}"}
        
        logger.info(f"Successfully restored {db_label} to {test_dbname}")
        
        # Run validation queries
        validation_result = {}
        try:
            conn = psycopg2.connect(
                host=host,
                port=port,
                user=user,
                password=password or "",
                database=test_dbname,
            )
            cur = conn.cursor()
            
            # Check basic schema integrity
            cur.execute("""
                SELECT COUNT(*) FROM information_schema.tables
                WHERE table_schema = 'public'
            """)
            table_count = cur.fetchone()[0]
            validation_result["table_count"] = table_count
            
            # Sample row counts from major tables
            for table in ["suppliers", "parts_catalog", "system_logs"]:
                try:
                    cur.execute(
                        sql.SQL("SELECT COUNT(*) FROM {}")
                        .format(sql.Identifier(table))
                    )
                    validation_result[f"{table}_rows"] = cur.fetchone()[0]
                except:
                    pass
            
            cur.close()
            conn.close()
            
            logger.info(f"restore_latest_backup: validation OK — {table_count} tables")
        except Exception as e:
            logger.warning(f"restore_latest_backup: validation query failed: {e}")
            validation_result["validation_error"] = str(e)[:100]
        
        # Clean up unless dry_run
        if not dry_run:
            conn = psycopg2.connect(
                host=host,
                port=port,
                user=user,
                password=password or "",
                database="postgres",
            )
            conn.autocommit = True
            conn.cursor().execute(
                sql.SQL("DROP DATABASE IF EXISTS {}")
                .format(sql.Identifier(test_dbname))
            )
            conn.close()
            logger.info(f"Dropped test database: {test_dbname}")
            test_db_status = "dropped"
        else:
            test_db_status = f"kept (name: {test_dbname})"
        
        return {
            "status": "ok",
            "backup_file": os.path.basename(backup_path),
            "test_db": test_dbname,
            "test_db_status": test_db_status,
            "validation": validation_result,
        }
    
    except Exception as e:
        logger.error(f"restore_latest_backup error: {e}")
        return {"status": "error", "reason": str(e)[:200]}
