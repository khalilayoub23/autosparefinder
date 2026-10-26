"""
Script: email_agent/control.py
Purpose: Runtime kill switch for the Email Agent. Stops processing and every Gmail write WITHOUT
         a backend restart. EMAIL_AGENT_ENABLED is read from the container environment, so
         changing it needs a recreation; this control is read on every checkpoint instead.
Process:
  - State lives in one small JSON file on the persistent worker_state volume
    (EMAIL_AGENT_CONTROL_FILE, default /app/state/email_agent_control.json), so the in-process
    loop and a `docker exec` CLI run see the same value, with no network or DB dependency.
  - Processing is allowed ONLY when the file exists, is valid JSON and says {"mode": "run"}.
    Missing file -> halted ("not_armed", the default); unreadable / invalid / unknown mode ->
    halted (fail closed). Running the agent therefore needs BOTH EMAIL_AGENT_ENABLED=1 (the
    loop) and an explicit `arm`; `halt` (or deleting the file) stops it.
  - Checkpoints (email_agent/agent.py, loop.py, gmail_client.py, maintenance/email_agent_cli.py):
    before the connection check, before each message claim, before the draft step, before the
    result is saved, and inside GmailClient._call immediately before any non-GET request.
  - Write intents (no lock is ever held across a network request): a Gmail write first records
    a marker file, THEN reads the control, sends, and removes the marker. `halt` writes the
    control FIRST and THEN reads the markers. With that ordering one of two things is true for
    every write: the writer saw the halt (nothing is sent), or `halt` saw its marker and reports
    it. So `halt` returning writes_in_flight == [] means no Gmail write is in flight and none will
    start. A reported write is NOT cancelled - a request already sent cannot be recalled; it
    finishes within the transport timeout (25 s, a POST is never retried). A marker left by a
    killed process is reported as stale after INFLIGHT_STALE_S and removed by the next write.
  - Operator: docker exec autospare_backend python3 /app/maintenance/email_agent_cli.py halt
              (arm / control likewise). Writes are atomic (temp file + os.replace).
Data Imported/Modified: the control file only.
Data Sources: none.
Missing Data Delegation: n/a.
Last Updated: 2026-10-10
"""
from __future__ import annotations

import contextlib
import json
import os
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

RUN = "run"
HALT = "halt"
DEFAULT_PATH = "/app/state/email_agent_control.json"
_MAX_BYTES = 4096
INFLIGHT_STALE_S = 180    # > transport timeout (25 s): an older marker belongs to a dead process


class Halted(RuntimeError):
    """Raised at a checkpoint when the runtime control does not allow processing."""

    def __init__(self, stage: str, reason: str):
        super().__init__(f"Email Agent halted by runtime control at {stage}: {reason}")
        self.stage, self.reason = stage, reason


def control_path() -> Path:
    return Path(os.environ.get("EMAIL_AGENT_CONTROL_FILE") or DEFAULT_PATH)


def read_control(path: Optional[Path] = None) -> Dict[str, Any]:
    """Current state. Never raises: any problem reading or validating the file means halted."""
    p = path or control_path()
    try:
        with open(p, "rb") as f:
            raw = f.read(_MAX_BYTES + 1)
    except FileNotFoundError:
        return {"mode": HALT, "running": False, "reason": "not_armed"}
    except OSError as e:
        return {"mode": HALT, "running": False, "reason": f"unreadable: {type(e).__name__}"}
    if len(raw) > _MAX_BYTES:
        return {"mode": HALT, "running": False, "reason": "invalid: file too large"}
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return {"mode": HALT, "running": False, "reason": "invalid: not JSON"}
    if not isinstance(data, dict) or data.get("mode") not in (RUN, HALT):
        return {"mode": HALT, "running": False, "reason": "invalid: mode must be 'run' or 'halt'"}
    out = {"mode": data["mode"], "running": data["mode"] == RUN,
           "reason": "armed" if data["mode"] == RUN else "halted_by_operator"}
    for k in ("updated_at", "by"):
        if isinstance(data.get(k), str):
            out[k] = data[k][:100]
    return out


def require_running(stage: str) -> None:
    state = read_control()
    if not state["running"]:
        raise Halted(stage, state["reason"])


def set_mode(mode: str, by: str = "operator", path: Optional[Path] = None) -> Dict[str, Any]:
    if mode not in (RUN, HALT):
        raise ValueError("mode must be 'run' or 'halt'")
    p = path or control_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps({"mode": mode, "by": str(by)[:100],
                       "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".email_agent_control.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(body)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    state = read_control(p)
    if mode == HALT:
        # Read the markers only AFTER the halt is on disk (see the module docstring).
        state["writes_in_flight"] = writes_in_flight(p)
    return state


def _inflight_dir(path: Optional[Path] = None) -> Path:
    p = path or control_path()
    return p.with_name(p.name + ".inflight.d")


def writes_in_flight(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Gmail writes that announced themselves and have not finished. Never raises."""
    out: List[Dict[str, Any]] = []
    try:
        names = sorted(os.listdir(_inflight_dir(path)))
    except OSError:
        return out
    now = time.time()
    for name in names:
        try:
            data = json.loads((_inflight_dir(path) / name).read_text(encoding="utf-8"))
            age = max(0.0, now - float(data["started_at"]))
            out.append({"stage": str(data.get("stage", ""))[:100], "pid": data.get("pid"),
                        "age_s": round(age, 1), "stale": age > INFLIGHT_STALE_S})
        except (OSError, ValueError, KeyError, TypeError):
            out.append({"stage": "unreadable marker", "pid": None, "age_s": None, "stale": False})
    return out


@contextlib.contextmanager
def write_intent(stage: str) -> Iterator[None]:
    """Wrap exactly one external write: announce -> check the control -> (caller sends) -> clear.
    Raises Halted before the caller sends anything when the control is not 'run', or when the
    intent cannot be recorded (fail closed: an unannounced write is never allowed)."""
    d = _inflight_dir()
    marker = d / f"{os.getpid()}-{uuid.uuid4().hex}.json"
    try:
        d.mkdir(parents=True, exist_ok=True)
        now = time.time()
        for name in os.listdir(d):                     # drop markers left by a killed process
            try:
                if now - (d / name).stat().st_mtime > INFLIGHT_STALE_S:
                    (d / name).unlink()
            except OSError:
                pass
        marker.write_text(json.dumps({"stage": stage, "pid": os.getpid(), "started_at": now}), encoding="utf-8")
    except OSError as e:
        raise Halted(stage, f"cannot record write intent: {type(e).__name__}") from None
    try:
        require_running(stage)      # read AFTER the marker is visible
        yield
    finally:
        try:
            marker.unlink()
        except OSError:
            pass
