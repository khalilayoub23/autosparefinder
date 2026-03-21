"""
auto_backup.py — pg_dump both databases every 24 h.
Keeps last KEEP_LAST backups per DB.
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
from datetime import datetime

logger = logging.getLogger(__name__)

BACKUP_DIR = os.environ.get("BACKUP_DIR", "/backups")
DATABASE_URL = os.environ.get("DATABASE_URL", "")
PII_DATABASE_URL = os.environ.get("PII_DATABASE_URL", "")
KEEP_LAST = 7


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


def _prune_old_backups(pattern: str) -> None:
    files = sorted(glob.glob(pattern))
    for old in files[:-KEEP_LAST]:
        try:
            os.remove(old)
            logger.info("auto_backup: pruned %s", old)
        except OSError as exc:
            logger.warning("auto_backup: prune error %s: %s", old, exc)


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
            logger.info(
                "auto_backup: %s → %s (%.1f MB, %.1f s)", label, out_path, size_mb, elapsed
            )
            _prune_old_backups(os.path.join(BACKUP_DIR, f"{label}_*.sql"))
            results[label] = f"ok:{out_path}"
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
