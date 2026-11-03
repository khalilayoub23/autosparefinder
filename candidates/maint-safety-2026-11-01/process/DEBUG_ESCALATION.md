# Claude → Opus debug escalation (CLAUDE.md §4 golden rule 10)

## Where the rule lives
`/opt/autosparefinder/CLAUDE.md` → "Golden rules (every task, every session)" → rule **10. Two-Attempt Debugging Rule**. CLAUDE.md is the project
instruction file Claude Code loads at the start of every session (it is what rules 1-9, including rule 8 production safety, already use).
It is a host-side document: the container mounts only `backend/`, so it cannot affect runtime.

## The rule (verbatim summary)
Max TWO genuine attempts per debugging problem; each different in approach and each recording suspected root cause, targeted fix or
concrete diagnostic, a relevant verification test, and the actual outcome. Fixed within either attempt → normal verification and close. Two
failed / no-progress attempts → stop and escalate to Opus; never a third independent attempt; never split one underlying issue into several
nominal tasks. Not for routine implementation, normal testing, documentation or unrelated work.

## The real delegation mechanism (verified 2026-11-02)
* There is NO `.claude/agents/` definition and no standing Opus agent in this project; none was invented.
* The supported mechanism is the `Agent` tool with **`model: "opus"`** (enum value of the tool's `model` parameter) and
  `subagent_type: "general-purpose"` (or omitted). `subagent_type: "fork"` must NOT be used: a fork "always runs on your model — a model override is ignored".
* The new agent starts COLD: its whole context is the `prompt`. The prompt therefore is the handoff package.
* It runs in the background; its final message comes back to Claude as a task notification. Claude then reviews, re-tests, accepts or rejects.
* **Not automatic**: nothing triggers the `Agent` call by itself; Claude must make it after the ledger says ESCALATE. The ledger tool below makes the
  limit checkable (a third attempt is refused with exit 3), but a tool cannot physically stop Claude from debugging without recording - the rule is an
  instruction plus an auditable trail.
* The Agent tool's own guidance says not to spawn agents unless asked; the owner's rule 10 is that standing request for the escalation case only.

## The ledger / handoff tool: `process/debug_escalation.py` (tests: `process/test_debug_escalation.py`, 6 pass)
```
DEBUG_LEDGER_DIR=<dir>  # default ./.debug_ledger
debug_escalation.py attempt ISSUE --cause … --action … --verify … --outcome fixed|failed|no-progress [--evidence …]
   # exit 0 = continue (OK); exit 3 = ESCALATE (2 attempts did not fix) or LOCKED (a 3rd attempt was refused); exit 2 = rejected (vague / repeat)
debug_escalation.py handoff ISSUE --task … --acceptance … [--file F]… [--error …] [--repo DIR] [--out handoff.md]   # refuses before 2 failed attempts
debug_escalation.py escalated ISSUE ; debug_escalation.py status ISSUE
```
The handoff contains: original task + expected outcome, exact error, both attempts with their hypotheses/outcomes/evidence, files, HEAD +
`git status --short`, the fixed production-safety constraints (restart/recreate/stop ban, protected scans/jobs, no data change/commit/push, no
credentials, escalation overrides no approval gate), acceptance criteria and the return format. Credentials are never read or included.
Use ONE issue id per root problem (splitting is not mechanically detectable).

## Closing after Opus returns
Claude reviews the diff, re-runs the acceptance tests itself, rejects unsupported PASS claims, updates `FIXES_TRACKER.md`, reports remaining blockers.

## Rehearsal evidence (2026-11-02) - see `evidence/escalation_drill/`
A real round trip on a scratch problem (not production): two genuine attempts failed (10/12 cases), the ledger refused a third, the handoff was built, an Opus agent was started
with it, and its result was reviewed and re-verified by Claude (details in the FIXES_TRACKER entry).
