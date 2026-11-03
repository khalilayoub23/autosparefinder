#!/usr/bin/env python3
"""
Script: debug_escalation.py
Purpose: mechanical bookkeeping for the CLAUDE.md "Two-Attempt Debugging Rule" (golden rule 10).
Process: `attempt` records one genuine debugging attempt (suspected root cause, action, verification, outcome) in a JSON ledger;
         after TWO attempts that did not fix the issue the ledger is LOCKED (exit 3): a third attempt is refused until `handoff`
         has produced the Opus context package and `escalated` has been recorded. `handoff` writes that package (attempts, git
         state, constraints, acceptance criteria) - it refuses before two failed attempts. `status` prints OK / ESCALATE.
Data Imported/Modified: only the ledger file (default $DEBUG_LEDGER_DIR or ./.debug_ledger/<issue>.json).
Limits (honest): a tool cannot stop Claude from debugging without recording; it is the checkable trail the rule relies on.
         Splitting one underlying problem into several issue ids is NOT detectable here: use one id per ROOT problem.
Last Updated: 2026-11-02

Usage:
  debug_escalation.py attempt  ISSUE --cause TEXT --action TEXT --verify TEXT --outcome fixed|failed|no-progress [--evidence TEXT]
  debug_escalation.py status   ISSUE
  debug_escalation.py handoff  ISSUE --task TEXT --acceptance TEXT [--file PATH ...] [--error TEXT] [--out FILE] [--repo DIR]
  debug_escalation.py escalated ISSUE
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

MAX_ATTEMPTS = 2
EXIT_ESCALATE = 3
OUTCOMES = ("fixed", "failed", "no-progress")

# Non-negotiable constraints copied into EVERY handoff: escalation grants Opus no extra permission.
CONSTRAINTS = [
    "Production is read-only: no file, DB, setting or schedule is modified; no deploy.",
    "No restart / recreate / stop / cycle of any production container or service without explicit owner approval.",
    "Do not interrupt AliExpress or Facebook scans, harvesters, seeders, schedulers, background jobs, queues or locks.",
    "No production data change, backfill, customer/supplier communication, scheduled-task activation, commit or push unless separately authorized.",
    "Do not use or copy credentials, tokens, secrets or private customer data; none are included in this package.",
    "Work only in the candidate/isolated test environment; never weaken, skip or reorder tests to obtain a PASS.",
    "Escalation does not override any existing approval gate.",
]


def _dir() -> Path:
    return Path(os.environ.get("DEBUG_LEDGER_DIR") or ".debug_ledger")


def _path(issue: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", issue):
        raise SystemExit("issue id must be [A-Za-z0-9._-]{1,80}")
    return _dir() / f"{issue}.json"


def load(issue: str) -> dict:
    p = _path(issue)
    if p.exists():
        return json.loads(p.read_text())
    return {"issue": issue, "attempts": [], "escalated": False, "handoff_built": False, "closed": False}


def save(led: dict) -> None:
    p = _path(led["issue"])
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(led, indent=2, ensure_ascii=False))


def failed_count(led: dict) -> int:
    return sum(1 for a in led["attempts"] if a["outcome"] != "fixed")


def locked(led: dict) -> bool:
    return failed_count(led) >= MAX_ATTEMPTS and not led["closed"]


def cmd_attempt(a) -> int:
    led = load(a.issue)
    if led["closed"]:
        print(f"{a.issue}: already fixed - open a new issue id only for a genuinely different problem", file=sys.stderr)
        return 2
    if locked(led):
        print(f"{a.issue}: LOCKED - {failed_count(led)} attempts did not fix it. A third independent attempt is not allowed: "
              f"build the handoff and escalate to Opus.", file=sys.stderr)
        return EXIT_ESCALATE
    for field in ("cause", "action", "verify"):
        if len((getattr(a, field) or "").strip()) < 8:
            print(f"--{field} must state something concrete (>= 8 characters)", file=sys.stderr)
            return 2
    if led["attempts"] and (a.action.strip(), a.verify.strip()) == (led["attempts"][-1]["action"], led["attempts"][-1]["verify"]):
        print("identical action+verify to the previous attempt: that is a repeat, not a new attempt", file=sys.stderr)
        return 2
    led["attempts"].append({"n": len(led["attempts"]) + 1, "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                            "cause": a.cause.strip(), "action": a.action.strip(), "verify": a.verify.strip(),
                            "outcome": a.outcome, "evidence": (a.evidence or "").strip()})
    if a.outcome == "fixed":
        led["closed"] = True
    save(led)
    return cmd_status(a)


def cmd_status(a) -> int:
    led = load(a.issue)
    n = failed_count(led)
    if led["closed"]:
        print(f"{a.issue}: FIXED after {len(led['attempts'])} attempt(s) - continue with normal verification")
        return 0
    if n >= MAX_ATTEMPTS:
        state = "escalated to Opus" if led["escalated"] else ("handoff built, record `escalated`" if led["handoff_built"] else "build the handoff")
        print(f"{a.issue}: ESCALATE - {n}/{MAX_ATTEMPTS} attempts failed ({state})")
        return EXIT_ESCALATE
    print(f"{a.issue}: OK - {n}/{MAX_ATTEMPTS} failed attempts")
    return 0


def _git(repo: str, *args: str) -> str:
    try:
        return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, timeout=60).stdout.rstrip("\n")
    except Exception as exc:  # noqa: BLE001 - the package must still be produced
        return f"(git unavailable: {exc})"


def build_handoff(led: dict, task: str, acceptance: str, files: list[str], error: str, repo: str) -> str:
    out = [f"# Opus handoff - {led['issue']}", "",
           "You are being handed a debugging task Claude could not resolve in two attempts. You own it to completion: do NOT only advise.",
           "Review the evidence below rather than repeating the failed attempts; find the root cause independently, implement the fix,",
           "add/update regression tests, run them, and return changed files, exact commands, results and any unresolved limitation.", "",
           "## Original task and expected outcome", task.strip(), "", "## Acceptance criteria", acceptance.strip(), "",
           "## Exact failure / error", (error.strip() or "(see attempt evidence)"), "", "## The two attempts (root-cause hypotheses already examined)"]
    for at in led["attempts"]:
        out += [f"### Attempt {at['n']} - outcome: {at['outcome']}", f"- suspected root cause: {at['cause']}", f"- action: {at['action']}",
                f"- verification: {at['verify']}", f"- evidence / output: {at['evidence'] or '(none recorded)'}", ""]
    out += ["## Files inspected / changed (as listed by Claude)"] + ([f"- {f}" for f in files] or ["- (none listed)"])
    out += ["", "## Repository state", f"- HEAD: {_git(repo, 'rev-parse', '--short', 'HEAD')} on {_git(repo, 'rev-parse', '--abbrev-ref', 'HEAD')}",
            "- `git status --short` (first 60 lines):", "```", "\n".join(_git(repo, "status", "--short").splitlines()[:60]), "```", "",
            "## Constraints (unchanged by escalation)"] + [f"- {c}" for c in CONSTRAINTS]
    out += ["", "## Return format", "1. Root cause (your own).  2. Files changed.  3. Exact test commands + results.  4. Remaining limitations.",
            "Claude will review the diff, re-run the acceptance tests and reject unsupported PASS claims."]
    return "\n".join(out) + "\n"


def cmd_handoff(a) -> int:
    led = load(a.issue)
    if failed_count(led) < MAX_ATTEMPTS and not a.rehearsal:
        print(f"{a.issue}: only {failed_count(led)} failed attempt(s) recorded - escalation starts after {MAX_ATTEMPTS}", file=sys.stderr)
        return 2
    text = build_handoff(led, a.task, a.acceptance, a.file or [], a.error or "", a.repo)
    led["handoff_built"] = True
    save(led)
    if a.out:
        Path(a.out).write_text(text)
        print(f"handoff written to {a.out} ({len(text)} bytes)")
    else:
        print(text)
    return 0


def cmd_escalated(a) -> int:
    led = load(a.issue)
    if not led["handoff_built"]:
        print("build the handoff first", file=sys.stderr)
        return 2
    led["escalated"] = True
    save(led)
    print(f"{a.issue}: escalation recorded")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sp = ap.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("attempt"); s.add_argument("issue"); s.add_argument("--cause", required=True); s.add_argument("--action", required=True)
    s.add_argument("--verify", required=True); s.add_argument("--outcome", choices=OUTCOMES, required=True); s.add_argument("--evidence")
    s.set_defaults(fn=cmd_attempt)
    s = sp.add_parser("status"); s.add_argument("issue"); s.set_defaults(fn=cmd_status)
    s = sp.add_parser("handoff"); s.add_argument("issue"); s.add_argument("--task", required=True); s.add_argument("--acceptance", required=True)
    s.add_argument("--file", action="append"); s.add_argument("--error"); s.add_argument("--out"); s.add_argument("--repo", default=".")
    s.add_argument("--rehearsal", action="store_true", help=argparse.SUPPRESS)
    s.set_defaults(fn=cmd_handoff)
    s = sp.add_parser("escalated"); s.add_argument("issue"); s.set_defaults(fn=cmd_escalated)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
