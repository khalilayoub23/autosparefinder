#!/usr/bin/env python3
"""
Script:  .claude/hooks/instruction_guard_hook.py   (PostToolUse hook for Edit|Write and Bash)
Purpose: Run the instruction conformance guard (.claude/process/test_instruction_conformance.py) whenever an instruction
         file is edited (Edit/Write path, or a Bash command naming one): CLAUDE.md, docs/claude/*, .claude/process/*,
         .claude/hooks/*, .claude/settings.json, the user CLAUDE.md or the project memory directory. A failure is
         reported back to Claude (exit 2 + stderr) so a weakened or contradictory instruction cannot be left in place
         silently (FIXES_TRACKER #160).
Process: read the hook JSON from stdin, take tool_input.file_path, run the guard with the plain-python runner (no pytest
         needed) only for watched paths, exit 2 with the failures on stderr, else exit 0.
Last Updated: 2026-11-27 (also watches .claude/hooks, .claude/settings.json and Bash edits)
"""
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GUARD = os.path.join(ROOT, ".claude", "process", "test_instruction_conformance.py")
MEMORY = "/root/.claude/projects/-opt-autosparefinder/memory/"
WATCHED = (os.path.join(ROOT, "CLAUDE.md"), os.path.join(ROOT, "docs", "claude") + os.sep,
           os.path.join(ROOT, ".claude", "process") + os.sep, os.path.join(ROOT, ".claude", "hooks") + os.sep,
           os.path.join(ROOT, ".claude", "settings.json"), MEMORY, "/root/.claude/CLAUDE.md")
# Bash commands are matched by these markers (sed/heredoc/python edits of instruction files)
BASH_MARKERS = ("CLAUDE.md", "docs/claude", ".claude/process", ".claude/hooks", ".claude/settings.json",
                "projects/-opt-autosparefinder/memory")


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:  # noqa: BLE001 - a malformed payload is not an instruction edit
        return 0
    tool_input = data.get("tool_input") or {}
    if data.get("tool_name") == "Bash":
        cmd = tool_input.get("command") or ""
        if not any(m in cmd for m in BASH_MARKERS):
            return 0
        path = "(Bash command touching instruction files)"
    else:
        path = tool_input.get("file_path") or (data.get("tool_response") or {}).get("filePath") or ""
        path = os.path.abspath(path) if path else ""
        if not path or not any(path == w or path.startswith(w) for w in WATCHED):
            return 0
    r = subprocess.run([sys.executable, GUARD], capture_output=True, text=True, timeout=60)
    if r.returncode == 0:
        return 0
    print("Instruction conformance guard FAILED after editing " + path + ":\n" + (r.stdout + r.stderr)[-3000:],
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
