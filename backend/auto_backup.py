"""
auto_backup.py — pg_dump both databases once a night.
Keeps last 7 daily + 4 weekly + 3 monthly backups per DB (retention tagging).
run_backup() is the first job of night_pipeline.Controller (01:00 Israel window) — this
module no longer schedules itself (FIXES_TRACKER #57).
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

# ── environment isolation (FIXES_TRACKER #66) ────────────────────────────────────
# The sandbox backend bind-mounts this same backend/ directory. With one shared default
# directory and one shared file name ("autospare_<ts>.sql") its daily dump landed among
# production's dumps and its retention deleted them. Three independent rules now:
#   1. identity   — only ENVIRONMENT=production without SANDBOX_MODE is "production";
#   2. directory  — a non-production process can never resolve to the production directory,
#                   not by default and not by configuration;
#   3. file name  — non-production dumps carry the environment name, and retention /
#                   restore select files by an exact "<prefix>_<YYYYMMDD>_<HHMMSS>.sql"
#                   match, so no process can select another environment's — or another
#                   database's — files ("autospare_*.sql" used to match "autospare_pii_*").
PRODUCTION_BACKUP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backups")


def backup_environment() -> str:
    if os.environ.get("SANDBOX_MODE", "").strip().lower() in ("1", "true", "yes", "on"):
        return "sandbox"
    env = os.environ.get("ENVIRONMENT", "").strip().lower()
    if env == "production":
        return "production"
    return re.sub(r"[^a-z0-9]+", "", env) or "nonprod"


def _is_production_dir(path: str) -> bool:
    real, prod = os.path.realpath(path), os.path.realpath(PRODUCTION_BACKUP_DIR)
    return real == prod or real.startswith(prod + os.sep)


def resolve_backup_dir(env: str | None = None) -> str:
    env = env or backup_environment()
    explicit = os.environ.get("BACKUP_DIR", "").strip()
    if env == "production":
        return explicit or PRODUCTION_BACKUP_DIR
    private = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state", f"backups-{env}")
    if not explicit or _is_production_dir(explicit):
        return private                      # state/ is each environment's own volume
    return explicit


def file_prefix(db_label: str, env: str | None = None) -> str:
    env = env or backup_environment()
    return db_label if env == "production" else f"{env}_{db_label}"


def backup_files(db_label: str, env: str | None = None, suffix: str = "") -> list:
    """This environment's dumps of exactly this database, oldest first."""
    rx = re.compile(r"^" + re.escape(file_prefix(db_label, env)) + r"_\d{8}_\d{6}\.sql(\.gz)?" + re.escape(suffix) + r"$")
    try:
        names = os.listdir(BACKUP_DIR)
    except OSError:
        return []
    return [os.path.join(BACKUP_DIR, n) for n in sorted(names) if rx.match(n)]


BACKUP_DIR = resolve_backup_dir()
DATABASE_URL = os.environ.get("DATABASE_URL", "")
# The container sets DATABASE_PII_URL; the old name PII_DATABASE_URL was never set, so the
# PII database was silently "skipped" on every run (0 autospare_pii_* dumps ever existed).
PII_DATABASE_URL = os.environ.get("DATABASE_PII_URL") or os.environ.get("PII_DATABASE_URL", "")
KEEP_LAST = 7  # Keep last 7 daily backups

if not (backup_environment() != "production" and _is_production_dir(BACKUP_DIR)):
    os.makedirs(BACKUP_DIR, exist_ok=True)


def _pg_dump(db_url: str, out_path: str) -> bool:
    """Invoke pg_dump for one connection URL. Returns True on success."""
    m = re.match(
        r"postgres(?:ql)?://([^:@]+)(?::([^@]*))?@([^/:]+)(?::(\d+))?/(.+)",
        db_url.replace("+asyncpg", ""),
    )
    if not m:
        logger.error("auto_backup: cannot parse db_url")
        return False
    user, password, host, port, dbname = m.groups()
    env = {**os.environ}
    if password:
        env["PGPASSWORD"] = password
    # Compressed custom format (-Fc, zlib level 6 by default): ~3.5x smaller
    # than plain SQL (1.6GB vs 5.6GB for the 4M-part catalog). Uncompressed
    # dumps at 7-daily retention were consuming 39GB of the 150GB disk.
    # Restore with: pg_restore -h <host> -U <user> -d <db> <file>
    cmd = [
        "pg_dump",
        "-h", host,
        "-p", port or "5432",
        "-U", user,
        "-Fc", "--no-owner", "--no-acl",
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
    - Keep last 2 daily backups
    - Keep last 1 weekly backup
    - Keep last 1 monthly backup

    Retention reduced from 7/4/3 on 2026-07-02: this task is the SECOND backup
    layer — the dedicated postgres_backup containers already keep rotated
    daily/weekly/monthly compressed backups in /opt/autosparefinder/backups.
    7 daily uncompressed dumps were consuming 39GB (26%) of the 150GB disk.
    """
    files = backup_files(db_label)      # exact match: this environment, this database only

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
            if daily_count > 2:
                _delete_backup(backup_path)
        elif tag == "weekly":
            weekly_count += 1
            if weekly_count > 1:
                _delete_backup(backup_path)
        elif tag == "monthly":
            monthly_count += 1
            if monthly_count > 1:
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
    env_name = backup_environment()
    if env_name != "production" and _is_production_dir(BACKUP_DIR):
        # Hard stop: whatever the configuration says, a non-production process does not
        # write to or prune the production backup directory (#66).
        logger.error("auto_backup: REFUSED — %s environment resolved to the production backup directory", env_name)
        return {"autospare": "skipped", "autospare_pii": "skipped", "reason": "backup directory isolation"}
    os.makedirs(BACKUP_DIR, exist_ok=True)
    # A dump is written to "<name>.partial" and renamed only when pg_dump succeeded
    # (FIXES_TRACKER #58): a run killed mid-dump (backend restart, OOM) used to leave a
    # truncated file under its final name, which restore_latest_backup() would pick as
    # "the latest backup" and retention would count as a real one. Leftovers of a killed
    # run are removed here; "*.sql.partial" never matches the "*.sql" restore/retention globs.
    if not dry_run:
        for _stale in (backup_files("autospare", suffix=".partial") + backup_files("autospare_pii", suffix=".partial")):
            try:
                os.remove(_stale)
                logger.warning("auto_backup: removed partial dump left by an interrupted run: %s", _stale)
            except OSError:
                pass
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    results: dict = {}
    for label, url in [("autospare", DATABASE_URL), ("autospare_pii", PII_DATABASE_URL)]:
        if not url:
            logger.warning("auto_backup: no URL configured for %s — skipping", label)
            results[label] = "skipped"
            continue
        out_path = os.path.join(BACKUP_DIR, f"{file_prefix(label, env_name)}_{ts}.sql")
        if dry_run:
            logger.info("auto_backup [dry-run]: would write %s", out_path)
            results[label] = "dry_run"
            continue
        t0 = time.monotonic()
        tmp_path = out_path + ".partial"
        ok = await asyncio.get_running_loop().run_in_executor(None, _pg_dump, url, tmp_path)
        if ok:
            os.replace(tmp_path, out_path)
        else:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
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
    
    files = [f for f in reversed(backup_files(db_label)) if f.endswith(".sql")]  # newest first, this env + db only
    
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
