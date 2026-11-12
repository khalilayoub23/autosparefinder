#!/usr/bin/env python3
"""
Script:  scripts/docker_shim.py   (installed as /usr/local/bin/docker by scripts/install_docker_shim.sh)
Purpose: Make a RAW `docker restart|stop|kill|rm ...` or `docker compose up|down|restart|...` against
         a production service impossible unless scripts/restart_guard.py authorized THAT exact call.
Why:     restart_guard.py can only protect commands that go through it. The 2026-11-12 incident was a
         raw `docker restart` after `pre_restart.sh ;` - the guard was never consulted. Every
         other docker call (exec, ps, logs, inspect, build, compose ps/logs/config/--dry-run,
         anything on a non-protected container) is passed through unchanged to the real docker.
Process:
  1. Parse the call (global flags skipped; `docker container X` == `docker X`).
  2. Not a restart-class verb, or no protected target -> exec the real docker (first `docker` on
     PATH that is not this shim).
  3. Protected -> require RESTART_GUARD_TOKEN naming a token file written by restart_guard.py for the
     SAME argv, younger than the TTL. The file is consumed (single use). Missing, wrong, expired or
     reused token -> exit 4, nothing is executed.
  Protected: containers named autospare_*, flaresolverr*, whatsapp_bridge (names or ids resolved with
  `docker inspect`), and every `compose` stop/start/recreate verb except `--dry-run`.
Fail closed: any error while deciding on a restart-class call is a denial. Residual (documented):
  calling /usr/bin/docker by absolute path, `systemctl restart docker`, a reboot.
Last Updated: 2026-11-12
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time

TOKEN_DIR = "/run/restart_guard/tokens"
TOKEN_TTL_S = 120.0
DENIED_EXIT = 4
PROTECTED_PREFIXES = ("autospare_", "flaresolverr", "whatsapp_bridge")
CONTAINER_VERBS = {"restart", "stop", "kill", "rm", "rename", "pause", "unpause"}
COMPOSE_VERBS = {"up", "down", "restart", "stop", "kill", "rm", "start", "pause", "unpause", "create"}
VALUE_FLAGS = {"-H", "--host", "-c", "--context", "--config", "-l", "--log-level", "--tlscacert",
               "--tlscert", "--tlskey", "-f", "--file", "-p", "--project-name", "--env-file",
               "--profile", "--project-directory", "--parallel", "--progress", "--ansi"}


def real_docker() -> str:
    me = os.path.realpath(__file__)
    shim_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
    for d in os.environ.get("PATH", "").split(os.pathsep):
        cand = os.path.join(d or ".", "docker")
        if os.path.abspath(d or ".") == shim_dir or not os.access(cand, os.X_OK) or os.path.isdir(cand):
            continue
        if os.path.realpath(cand) == me:
            continue
        return cand
    for cand in ("/usr/bin/docker", "/usr/local/bin/docker.real"):
        if os.access(cand, os.X_OK) and os.path.realpath(cand) != me:
            return cand
    print("[docker_shim] real docker binary not found", file=sys.stderr)
    sys.exit(127)


def split_call(args: list[str]):
    """Return (subcommand, rest) skipping global flags and their values."""
    i = 0
    while i < len(args):
        a = args[i]
        if a.startswith("-"):
            i += 2 if (a in VALUE_FLAGS and "=" not in a) else 1
            continue
        return a, args[i + 1:]
    return "", []


def positional(args: list[str]) -> list[str]:
    out, i = [], 0
    while i < len(args):
        a = args[i]
        if a.startswith("-"):
            # `-t 5` / `--time 5` take a value; others are booleans. Over-collecting a number is harmless.
            i += 2 if a in ("-t", "--time", "-s", "--signal", "-n", "--name") else 1
            continue
        out.append(a)
        i += 1
    return out


def is_protected_name(name: str) -> bool:
    return name.lstrip("/").startswith(PROTECTED_PREFIXES)


def resolve_protected(real: str, target: str) -> bool:
    """True when the container (name or id/prefix) is a protected one. Unknown -> not protected
    (docker itself will report no such container); an error resolving a *known* id is a denial."""
    if is_protected_name(target):
        return True
    try:
        res = subprocess.run([real, "inspect", "-f", "{{.Name}}", target], capture_output=True,
                             text=True, timeout=20)
    except Exception:  # noqa: BLE001 - cannot tell -> treat as protected (fail closed)
        return True
    return res.returncode == 0 and is_protected_name(res.stdout.strip())


def classify(real: str, args: list[str]) -> str:
    """'' = pass through, otherwise a short description of the protected operation."""
    sub, rest = split_call(args)
    if sub == "container":
        sub, rest = split_call(rest)
    if sub == "compose":
        verb, crest = split_call(rest)
        flags = list(rest)
        if "--dry-run" in flags:
            return ""
        if verb in COMPOSE_VERBS:
            return f"compose {verb}"
        return ""
    if sub in CONTAINER_VERBS:
        for target in positional(rest):
            if resolve_protected(real, target):
                return f"{sub} {target}"
    return ""


def consume_token(args: list[str]) -> str:
    """'' when the call is authorized (token consumed), otherwise the denial reason."""
    tid = os.environ.get("RESTART_GUARD_TOKEN", "")
    if not tid or not all(c in "0123456789abcdef" for c in tid) or len(tid) != 32:
        return "no valid RESTART_GUARD_TOKEN - run it through backend/scripts/guarded_restart.sh"
    path = os.path.join(TOKEN_DIR, tid)
    try:
        with open(path) as fh:
            tok = json.load(fh)
        os.unlink(path)  # single use, consumed before anything runs
    except (OSError, ValueError):
        return "authorization token not found, unreadable or already used"
    if time.time() - float(tok.get("issued_at", 0)) > TOKEN_TTL_S:
        return "authorization token expired"
    digest = hashlib.sha256(json.dumps(args, separators=(",", ":")).encode()).hexdigest()
    if tok.get("argv") != digest:
        return "authorization token was issued for a different command"
    return ""


def main(argv: list[str]) -> int:
    real = real_docker()
    args = argv[1:]
    try:
        op = classify(real, args)
    except Exception as exc:  # noqa: BLE001 - cannot classify -> do not guess
        print(f"[docker_shim] DENIED: could not classify the call ({type(exc).__name__})", file=sys.stderr)
        return DENIED_EXIT
    if op:
        reason = consume_token(args)
        if reason:
            print(f"[docker_shim] DENIED `docker {' '.join(args)}`: {reason}", file=sys.stderr)
            print("[docker_shim] a restart is allowed only when a fresh preflight says SAFE: "
                  "backend/scripts/guarded_restart.sh <op> -- docker " + " ".join(args), file=sys.stderr)
            return DENIED_EXIT
    env = dict(os.environ)
    env.pop("RESTART_GUARD_TOKEN", None)
    os.execve(real, [real] + args, env)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
