"""
Script: services/sync_checkpoint.py
Purpose: DURABLE, atomic cursor store for the price-sync workers (replaces process-memory cursors that rewound on
         every restart, e.g. ebay_price_sync._EBAY_SYNC_LAST_ID).
Process: one JSON file on the persistent worker_state volume (/app/state); writes go to a temp file + fsync + os.replace
         (atomic on POSIX), so a crash leaves either the old or the new value, never a torn file. A corrupt/missing file
         reads as the default (a replay from the start is safe: all writers are idempotent upserts).
         Callers advance a cursor ONLY after the batch it covers is committed (at-least-once, safe replay of the last
         incomplete batch). Concurrency: sync_prices already holds the distributed lock `sync_prices`; the in-process
         threading.Lock here only serialises read-modify-write inside one process.
Data Imported/Modified: /app/state/sync_cursors.json (keys: caller-defined).
Data Sources: none.
Missing Data Delegation: n/a
Last Updated: 2026-11-18
"""
import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

_PATH = Path(os.getenv("SYNC_CHECKPOINT_FILE", str(Path(__file__).resolve().parent.parent / "state" / "sync_cursors.json")))
_LOCK = threading.Lock()


def _read() -> dict:
    try:
        data = json.loads(_PATH.read_text())
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, ValueError, OSError):
        return {}


def load(key: str, default: Any = None) -> Any:
    with _LOCK:
        return _read().get(key, default)


def save(key: str, value: Any) -> None:
    with _LOCK:
        data = _read()
        data[key] = value
        _PATH.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(_PATH.parent), prefix=".sync_cursors.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(data, fh)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, _PATH)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
