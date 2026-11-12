#!/usr/bin/env python3
"""
Script:  scripts/restart_guard.py
Purpose: THE fail-closed authorization point for every restart / stop / recreate of a production
         service. A restart runs only when a FRESH preflight, produced for THIS request, says SAFE.
Why:     2026-11-12: `bash pre_restart.sh ; docker restart autospare_backend` ran the restart although
         the preflight said BLOCKED - `;` ignores the previous exit status and a script cannot
         stop a command that is not inside it. The status of an earlier command is never an
         authorization; the restart must be impossible without a token only this guard issues.
Process:
  1. Create a random nonce and run the preflight (`restart_preflight.py --json --nonce N`) in the
     backend container, with a hard timeout. Nothing is cached or reused.
  2. Authorize ONLY when ALL hold: the process ran, exited 0, stdout is exactly one JSON object,
     verdict == "SAFE", safe is True, blockers == [], the echoed nonce equals N, and checked_at
     is a number no older than MAX_AGE_S and not from the future. Anything else (BLOCKED, UNSAFE,
     non-zero exit, timeout, empty/malformed output, missing executable, any exception) is a
     denial: exit 4, a reason on stderr, the command is NOT executed.
  3. With `-- CMD ...`: run CMD (no shell) and return ITS exit status. When CMD is a docker
     command, a one-time token bound to its exact argv is written first, so the docker shim
     (scripts/docker_shim.py) lets exactly that call through and nothing else.
No override: no environment variable or flag skips, shortens or fakes any step. `PRE_RESTART_FORCE`
is ignored (and said so). The only tunable is RESTART_GUARD_TIMEOUT_S, clamped to <= the default,
so it can only make the guard stricter.
Usage:   restart_guard.py --op NAME                         # authorize only (exit 0 / 4)
         restart_guard.py --op NAME -- docker restart autospare_backend
Exit:    0 authorized (and CMD succeeded) | 4 DENIED | otherwise CMD's own exit status.
Data Imported/Modified: token files under /run/restart_guard/tokens (single use, TTL 120 s).
Last Updated: 2026-11-12
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence

PREFLIGHT_CMD = ["docker", "exec", "autospare_backend", "python3",
                 "/app/scripts/restart_preflight.py", "--json"]
DEFAULT_TIMEOUT_S = 90.0
MAX_AGE_S = 30.0
TOKEN_DIR = "/run/restart_guard/tokens"
TOKEN_TTL_S = 120.0
DENIED_EXIT = 4


@dataclass
class Decision:
    authorized: bool
    reason: str
    nonce: str = ""


def _timeout() -> float:
    """Default timeout; RESTART_GUARD_TIMEOUT_S may only LOWER it (stricter, never looser)."""
    raw = os.environ.get("RESTART_GUARD_TIMEOUT_S", "")
    try:
        return max(0.1, min(DEFAULT_TIMEOUT_S, float(raw))) if raw else DEFAULT_TIMEOUT_S
    except ValueError:
        return DEFAULT_TIMEOUT_S


def authorize(run: Callable = subprocess.run, now: Callable[[], float] = time.time,
              nonce: Optional[str] = None, timeout_s: Optional[float] = None) -> Decision:
    """Run a fresh preflight and return the decision. Never raises: every failure is a denial."""
    nonce = nonce or secrets.token_hex(16)
    started = now()
    try:
        proc = run(PREFLIGHT_CMD + ["--nonce", nonce], capture_output=True, text=True,
                   timeout=timeout_s or _timeout(), stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return Decision(False, "preflight timed out", nonce)
    except FileNotFoundError:
        return Decision(False, "preflight executable not found (docker)", nonce)
    except Exception as exc:  # noqa: BLE001 - any failure to run the preflight is a denial
        return Decision(False, f"preflight could not run: {type(exc).__name__}: {str(exc)[:160]}", nonce)

    out = (proc.stdout or "").strip()
    if not out:
        return Decision(False, f"preflight produced no output (exit {proc.returncode})", nonce)
    try:
        data = json.loads(out)
    except ValueError:
        return Decision(False, f"preflight output is not valid JSON (exit {proc.returncode})", nonce)
    if not isinstance(data, dict):
        return Decision(False, "preflight output is not a JSON object", nonce)

    verdict = data.get("verdict")
    if verdict != "SAFE":
        blockers = data.get("blockers")
        shown = "; ".join(str(b) for b in blockers[:3]) if isinstance(blockers, list) and blockers else "no detail"
        return Decision(False, f"preflight verdict {verdict!r} (exit {proc.returncode}): {shown}", nonce)
    if proc.returncode != 0:
        return Decision(False, f"preflight said SAFE but exited {proc.returncode}", nonce)
    if data.get("safe") is not True or data.get("blockers") != []:
        return Decision(False, "preflight SAFE verdict is inconsistent (safe/blockers)", nonce)
    if data.get("nonce") != nonce:
        return Decision(False, "preflight nonce mismatch - result is stale or not for this request", nonce)
    checked = data.get("checked_at")
    if isinstance(checked, bool) or not isinstance(checked, (int, float)):
        return Decision(False, "preflight result has no usable checked_at", nonce)
    if checked < started - 5 or checked > now() + 5 or now() - checked > MAX_AGE_S:
        return Decision(False, "preflight result is outside the freshness window", nonce)
    return Decision(True, "preflight SAFE (fresh, nonce verified)", nonce)


def argv_digest(argv: Sequence[str]) -> str:
    """Digest of the docker arguments (everything after the `docker` word)."""
    return hashlib.sha256(json.dumps(list(argv), separators=(",", ":")).encode()).hexdigest()


def issue_token(op: str, docker_argv: Sequence[str], nonce: str, now: float) -> str:
    """Write the one-time token authorizing exactly this docker call; return its id."""
    os.makedirs(TOKEN_DIR, mode=0o700, exist_ok=True)
    tid = secrets.token_hex(16)
    path = os.path.join(TOKEN_DIR, tid)
    with open(path, "w") as fh:
        json.dump({"op": op, "argv": argv_digest(docker_argv), "issued_at": now, "nonce": nonce}, fh)
    os.chmod(path, 0o600)
    return tid


def deny(op: str, reason: str, cmd: Sequence[str]) -> int:
    shown = " ".join(cmd) if cmd else "(authorization only)"
    print(f"[restart_guard] DENIED op={op}: {reason}", file=sys.stderr)
    print(f"[restart_guard] NOT executed: {shown}", file=sys.stderr)
    return DENIED_EXIT


def main(argv: List[str]) -> int:
    if "--" in argv:
        i = argv.index("--")
        head, cmd = argv[:i], argv[i + 1:]
    else:
        head, cmd = argv, []
    op = ""
    for j, a in enumerate(head):
        if a == "--op" and j + 1 < len(head):
            op = head[j + 1]
        elif a.startswith("--op="):
            op = a.split("=", 1)[1]
    if not op or [a for a in head if not a.startswith("--op") and a != op]:
        print("usage: restart_guard.py --op NAME [-- COMMAND ...]", file=sys.stderr)
        return deny(op or "unknown", "invalid invocation", cmd)
    if os.environ.get("PRE_RESTART_FORCE"):
        print("[restart_guard] PRE_RESTART_FORCE is ignored: there is no override.", file=sys.stderr)

    decision = authorize()
    if not decision.authorized:
        return deny(op, decision.reason, cmd)
    print(f"[restart_guard] AUTHORIZED op={op}: {decision.reason}", file=sys.stderr)
    if not cmd:
        return 0

    env = dict(os.environ)
    env.pop("RESTART_GUARD_TOKEN", None)
    if os.path.basename(cmd[0]) == "docker":
        try:
            env["RESTART_GUARD_TOKEN"] = issue_token(op, cmd[1:], decision.nonce, time.time())
        except OSError as exc:
            return deny(op, f"could not issue the authorization token: {exc}", cmd)
    try:
        return subprocess.run(cmd, env=env).returncode
    except FileNotFoundError:
        print(f"[restart_guard] command not found: {cmd[0]}", file=sys.stderr)
        return 127


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
