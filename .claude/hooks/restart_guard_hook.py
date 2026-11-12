#!/usr/bin/env python3
"""
Script:  .claude/hooks/restart_guard_hook.py   (PreToolUse hook for the Bash tool)
Purpose: Stop a Claude Code session from running a restart / stop / recreate of a production service
         (or `systemctl restart|stop docker`, reboot, shutdown) any way other than through
         backend/scripts/guarded_restart.sh. Same rule as the docker shim, enforced where an agent
         session would otherwise type the raw command (FIXES_TRACKER 2026-11-12 restart guard).
Process: tokenise the command with shlex (quotes respected), split it into simple commands at
         ; & | ( ) and newlines, and for each one: guarded_restart.sh / restart_guard.py -> fine;
         `docker ...` -> backend/scripts/docker_shim.classify() (the SAME classification as the shim);
         systemctl restart|stop|kill docker, reboot, shutdown, halt, poweroff -> denied; `bash -c '...'`
         is analysed recursively. PRE_RESTART_FORCE anywhere in a command is denied.
         Exit 2 = blocked (stderr is shown to Claude); 0 = allowed.
Last Updated: 2026-11-12
"""
import importlib.util
import json
import os
import re
import shlex
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SHIM = os.path.join(ROOT, "backend", "scripts", "docker_shim.py")
WRAPPERS = {"sudo", "env", "command", "exec", "nohup", "time", "nice", "timeout"}
SHELLS = {"bash", "sh", "zsh"}
HALTERS = {"reboot", "shutdown", "halt", "poweroff"}


def _shim():
    spec = importlib.util.spec_from_file_location("docker_shim", SHIM)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def simple_commands(command: str):
    lex = shlex.shlex(command.replace("\n", " ; "), posix=True, punctuation_chars=";&|()")
    lex.whitespace_split = True
    cur = []
    for tok in lex:
        if tok and all(c in ";&|()" for c in tok):
            if cur:
                yield cur
            cur = []
        else:
            cur.append(tok)
    if cur:
        yield cur


def violation(command: str, shim, depth: int = 0) -> str:
    if re.search(r"PRE_RESTART_FORCE=", command):
        return "PRE_RESTART_FORCE was removed - there is no override of the restart guard"
    try:
        cmds = list(simple_commands(command))
    except ValueError:
        return ""
    for toks in cmds:
        i = 0
        while i < len(toks) and (("=" in toks[i] and not toks[i].startswith("-") and toks[i].split("=")[0].isidentifier())
                                 or os.path.basename(toks[i]) in WRAPPERS):
            i += 1
        if i >= len(toks):
            continue
        first = os.path.basename(toks[i])
        rest = toks[i + 1:]
        if first in ("guarded_restart.sh", "restart_guard.py") or (
                first in ("bash", "sh", "python3") and rest and os.path.basename(rest[0]) in ("guarded_restart.sh", "restart_guard.py")):
            continue
        if first in SHELLS and "-c" in rest and depth < 3:
            j = rest.index("-c")
            if j + 1 < len(rest):
                v = violation(rest[j + 1], shim, depth + 1)
                if v:
                    return v
            continue
        if first == "docker":
            try:
                op = shim.classify(shim.real_docker(), rest)
            except SystemExit:
                op = ""
            except Exception:  # noqa: BLE001 - cannot classify a docker call: deny
                op = "unclassifiable docker call"
            if op:
                return f"`docker {' '.join(rest)}` ({op})"
        elif first == "docker-compose":
            if any(a in shim.COMPOSE_VERBS for a in rest) and "--dry-run" not in rest:
                return f"`docker-compose {' '.join(rest)}`"
        elif first == "systemctl" and any(a in ("restart", "stop", "kill", "reboot", "poweroff", "halt") for a in rest) \
                and any("docker" in a or "containerd" in a for a in rest):
            return f"`systemctl {' '.join(rest)}` (stops every container)"
        elif first in HALTERS:
            return f"`{first}`"
    return ""


def main() -> int:
    try:
        command = (json.load(sys.stdin).get("tool_input") or {}).get("command") or ""
    except Exception:  # noqa: BLE001 - not a Bash payload we understand
        return 0
    interesting = any(w in command for w in ("docker", "systemctl", "reboot", "shutdown", "halt", "poweroff", "PRE_RESTART_FORCE"))
    if not interesting:
        return 0
    try:
        v = violation(command, _shim())
    except Exception as exc:  # noqa: BLE001
        print(f"restart_guard_hook: could not analyse a docker/system command ({type(exc).__name__}); blocked.", file=sys.stderr)
        return 2
    if v:
        print("BLOCKED by the restart guard: " + v + ". Restarts/stops/recreates must go through "
              "`backend/scripts/guarded_restart.sh <op> -- docker ...` (fresh preflight must say SAFE; "
              "no override).", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
