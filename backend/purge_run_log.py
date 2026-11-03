"""
Script:  purge_run_log.py
Purpose: Capture and keep the result of a maintenance subprocess instead of sending it to DEVNULL.
Process:
  1. `run_captured(argv, label)` runs the subprocess, reads stdout+stderr (merged), bounded.
  2. Extracts the `[<label>_summary] {json}` line the script prints at the end of its run.
  3. Appends ONE JSON record per run to `<state>/logs/<label>_runs.jsonl` (rotated by size)
     with the real exit code, wall-clock start/end and the script's own counters.
  4. Keeps the tail of the raw output in `<state>/logs/<label>.log` (overwritten each run).
Data Imported/Modified: files under /app/state/logs only. Never touches a database.
Data Sources: stdout/stderr of the monitored script (counters only; no credentials are printed by it).
Missing Data Delegation: a run without a summary line is recorded as status "no_summary".
Last Updated: 2026-11-03
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

STATE_DIR = Path(os.getenv("ASF_STATE_DIR", str(Path(__file__).resolve().parent / "state")))
MAX_RAW_BYTES = int(os.getenv("PURGE_LOG_MAX_RAW_BYTES", "65536"))
MAX_JSONL_BYTES = int(os.getenv("PURGE_LOG_MAX_JSONL_BYTES", "524288"))


def parse_summary(output: str, label: str) -> dict | None:
    """Return the last `[<label>_summary] {json}` object in the output, or None."""
    tag = f"[{label}_summary] "
    found = None
    for line in output.splitlines():
        if line.startswith(tag):
            try:
                found = json.loads(line[len(tag):])
            except ValueError:
                continue
    return found


def _rotate(path: Path, limit: int) -> None:
    try:
        if path.exists() and path.stat().st_size > limit:
            path.replace(path.with_suffix(path.suffix + ".1"))  # keep exactly one previous generation
    except OSError:
        pass


def record_run(label: str, rc: int, started: float, finished: float, output: str,
               state_dir: Path | None = None) -> dict:
    """Persist one run. Never raises (logging must not break the calling loop)."""
    base = (state_dir or STATE_DIR) / "logs"
    summary = parse_summary(output, label)
    rec = {
        "label": label, "exit_code": rc, "started_at": started, "finished_at": finished,
        "duration_s": round(finished - started, 1),
        "status": (summary or {}).get("status", "no_summary" if rc == 0 else "failed"),
        "summary": summary,
    }
    if rc != 0 and rec["status"] == "completed":
        rec["status"] = "failed"  # a nonzero exit is never reported as completed
    try:
        base.mkdir(parents=True, exist_ok=True)
        jl = base / f"{label}_runs.jsonl"
        _rotate(jl, MAX_JSONL_BYTES)
        with open(jl, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, default=str, separators=(",", ":")) + "\n")
        raw = output.encode("utf-8", "replace")[-MAX_RAW_BYTES:]
        (base / f"{label}.log").write_bytes(raw)
    except OSError as exc:
        print(f"[{label}] run record not written: {exc}", flush=True)
    return rec


async def run_captured(argv: list[str], label: str, state_dir: Path | None = None) -> tuple[int, dict]:
    """Run `argv`, capture merged output (bounded), persist the run. Returns (rc, record)."""
    started = time.time()
    proc = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    chunks: list[bytes] = []
    size = 0
    assert proc.stdout is not None
    while True:
        data = await proc.stdout.read(8192)
        if not data:
            break
        chunks.append(data)
        size += len(data)
        while size > 4 * MAX_RAW_BYTES and len(chunks) > 1:  # bounded memory: drop the oldest
            size -= len(chunks.pop(0))
    rc = await proc.wait()
    out = b"".join(chunks).decode("utf-8", "replace")
    return rc, record_run(label, rc, started, time.time(), out, state_dir)
