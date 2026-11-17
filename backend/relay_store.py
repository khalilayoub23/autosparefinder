"""
Script:  relay_store.py
Purpose: DURABLE, ACKNOWLEDGED chunk relay for the car-parts.ie harvester -> /api/v1/system/collect (2026-11-17).
         Replaces the uvicorn-memory buffer for runs that carry a run_id: every chunk is written to disk (survives a backend
         restart), acknowledged with its sequence number, and `done` is only accepted when EVERY expected chunk and the
         expected part count are present (otherwise HTTP 409 + the missing sequence numbers, so the harvester re-sends).
         The importer runs under relay_run.py, which keeps a PER-RUN log and writes a result file (exit code) the harvester
         polls, so an importer failure is visible to the harvester instead of fire-and-forget.
Process:  store_chunk() -> missing_or_parts() -> start_import() -> import_state()
Data Imported/Modified: files under RELAY_DIR (/app/state/relay): chunks/<tag>__<run>/<seq>.json, payloads/, logs/, results/.
Data Sources: harvester POSTs.
Missing Data Delegation: none.
Last Updated: 2026-11-17
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional, Tuple

RELAY_DIR = Path(os.getenv("RELAY_DIR", "/app/state/relay"))
KEEP_S = 3 * 86400


class RelayIncomplete(Exception):
    def __init__(self, missing: List[int], have_parts: int):
        super().__init__(f"missing chunks {missing[:20]} ({have_parts} parts present)")
        self.missing, self.have_parts = missing, have_parts


def _safe(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(s))[:120]


def _key(tag: str, run_id: str) -> str:
    return f"{_safe(tag)}__{_safe(run_id)}"


def _atomic_write(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp{os.getpid()}")
    tmp.write_text(data)
    os.replace(tmp, path)


def store_chunk(tag: str, run_id: str, seq: int, chunk: list) -> int:
    """Idempotent: the same seq overwrites itself (a retry after a lost ack cannot duplicate parts)."""
    _atomic_write(RELAY_DIR / "chunks" / _key(tag, run_id) / f"{int(seq):05d}.json", json.dumps(chunk))
    return len(chunk)


def missing_or_parts(tag: str, run_id: str, expected_chunks: int, expected_parts: int) -> list:
    d = RELAY_DIR / "chunks" / _key(tag, run_id)
    present = {}
    for f in d.glob("*.json") if d.exists() else []:
        try:
            present[int(f.stem)] = json.loads(f.read_text())
        except Exception:
            continue  # a torn/garbled chunk counts as missing
    missing = [i for i in range(int(expected_chunks)) if i not in present]
    parts = [p for i in sorted(present) if i < int(expected_chunks) for p in present[i]]
    if missing or len(parts) != int(expected_parts):
        raise RelayIncomplete(missing, len(parts))
    return parts


def result_path(tag: str, run_id: str) -> Path:
    return RELAY_DIR / "results" / f"{_key(tag, run_id)}.json"


def start_import(cmd: List[str], tag: str, run_id: str, payload_path: Path) -> dict:
    """Idempotent per run: a second `done` for the same run returns the first launch."""
    res = result_path(tag, run_id)
    if res.exists():
        return json.loads(res.read_text())
    log_path = RELAY_DIR / "logs" / f"{_key(tag, run_id)}.log"          # one log per RUN, never overwritten
    log_path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(res, json.dumps({"state": "running", "started_at": time.time(), "log": str(log_path)}))
    runner = Path(__file__).resolve().parent / "maintenance" / "relay_run.py"
    proc = subprocess.Popen([sys.executable, str(runner), str(res), str(log_path), "--", *cmd],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True, start_new_session=True)
    return {"state": "running", "pid": proc.pid, "log": str(log_path)}


def import_state(tag: str, run_id: str, stale_after_s: int = 3600) -> dict:
    res = result_path(tag, run_id)
    if not res.exists():
        return {"state": "unknown"}
    d = json.loads(res.read_text())
    if d.get("state") == "running" and time.time() - float(d.get("started_at") or 0) > stale_after_s:
        d["state"] = "failed"; d["reason"] = "importer produced no result within the limit (killed or hung)"
    return d


def gc(now: Optional[float] = None) -> int:
    now = now or time.time(); n = 0
    for sub in ("chunks", "payloads", "logs", "results"):
        for p in (RELAY_DIR / sub).glob("*") if (RELAY_DIR / sub).exists() else []:
            try:
                if now - p.stat().st_mtime > KEEP_S:
                    if p.is_dir():
                        for f in p.glob("*"): f.unlink()
                        p.rmdir()
                    else:
                        p.unlink()
                    n += 1
            except Exception:
                pass
    return n
