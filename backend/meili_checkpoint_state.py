"""
Script: meili_checkpoint_state.py
Purpose: Tell when the last Meilisearch sync COMPLETED, from meili_sync.py's own checkpoint,
         so BACKEND_API_ROUTES._meili_sync_loop can keep its 2-hour cadence across restarts.
Process: a completed checkpoint has offset == total (CLAUDE.md "Meilisearch Sync"); its file
         modification time is when that run ended. A checkpoint with offset < total is an
         interrupted run: returns None so the loop resumes it after its normal settle time.
         Missing / unreadable / invalid file -> None (the sync is never skipped).
Data Imported/Modified: none (reads /app/state/meili_sync_checkpoint.json).
Data Sources: none.
Missing Data Delegation: n/a.
Last Updated: 2026-10-10
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

CHECKPOINT = Path(os.getenv("MEILI_SYNC_CHECKPOINT", "/app/state/meili_sync_checkpoint.json"))


def last_completed_sync(path: Optional[Path] = None) -> Optional[datetime]:
    p = path or CHECKPOINT
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        offset, total = int(data["offset"]), int(data["total"])
        if not data.get("index_ready", True) or offset < total:
            return None
        return datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
    except (OSError, ValueError, KeyError, TypeError):
        return None
