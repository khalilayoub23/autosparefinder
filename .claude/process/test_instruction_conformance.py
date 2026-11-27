"""
Script:  .claude/process/test_instruction_conformance.py
Purpose: Guard the Claude Code instruction system (FIXES_TRACKER #160). Fails when the Assignment Execution Protocol,
         the status vocabulary, the precedence list or the escalation mechanism disappears from CLAUDE.md, or when a
         contradicting phrase that caused premature stopping (audit 2026-11-27, F1-F13) comes back in CLAUDE.md, the
         moved docs/claude/ files, or the project memory (memory checks skip when the memory dir is absent).
Process: plain text assertions; no network, no docker, no database.
Run:     python3 -m pytest .claude/process/test_instruction_conformance.py   (or, without pytest:
         python3 .claude/process/test_instruction_conformance.py). Wired as a PostToolUse hook on edits to instruction files
         (.claude/hooks/instruction_guard_hook.py).
Last Updated: 2026-11-27
"""
import hashlib
import re
import sys
from pathlib import Path

try:
    import pytest
except ImportError:  # minimal stand-in so the guard runs on a host without pytest
    class _Mark:
        def parametrize(self, name, values):
            def deco(fn):
                fn._params = (name, values); return fn
            return deco
        def skipif(self, cond, reason=""):
            def deco(fn):
                fn._skip = bool(cond); return fn
            return deco
    class pytest:  # noqa: N801
        mark = _Mark()

ROOT = Path(__file__).resolve().parents[2]
CLAUDE = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
MEM = Path("/root/.claude/projects/-opt-autosparefinder/memory")


def _section(title: str) -> str:
    start = CLAUDE.index(title)
    nxt = CLAUDE.find("\n### ", start + len(title))
    return CLAUDE[start:nxt]


PROTOCOL = _section("### Assignment Execution Protocol")


@pytest.mark.parametrize("clause", [
    "I own the complete outcome of the assignment",
    "task register",
    "The register is the FIRST file written",
    "one row per requirement",
    "Map the system impact",
    "Waiting is not stopping",
    "End the turn only when EVERY open item depends on the pending event",
    "re-read the register",
    "Integrate.",
    'model: "opus"',
    "Required before closing any significant change",
    "never report a consult that did not happen",
    "Separate pre-existing\nfailures from regressions",
    "Documentation is part of completion",
    "Never inferred, whatever the wording",
    "ask for all approvals in one consolidated message",
    "**AWAITING-APPROVAL**",
    "has **no** status",
    "*committed locally* · *pushed* · *deployed* · *verified in\nproduction*",
    "finished-but-uncommitted work",
    "What counts as approval for class 2",
    "Treat EVERY runtime file under these paths as live",
    "at least one row DONE or\n  AWAITING-APPROVAL",
    "a parent agent's prompt (a subagent's prompt is not the\n  owner) is NOT approval",
    "**Light tier:**",
    "**IN-PROGRESS** — interim only",
    "**DESCOPED**",
    "tests, docs and the tracker do not count as layers here",
    'isolation: "worktree"',
    "How to keep working while an agent or job runs",
    "do the independent rows FIRST, then start it",
    "merely *needs* a restart/deploy to take effect",
])
def test_protocol_has_required_clause(clause):
    assert clause in PROTOCOL, clause


def test_protocol_sits_in_section_1_before_lessons():
    assert CLAUDE.index("### Assignment Execution Protocol") < CLAUDE.index("## 2. Critical Lessons Learned")
    assert CLAUDE.index("### Instruction precedence") < CLAUDE.index("## 2. Critical Lessons Learned")


H_REQUIRED = [
    "restart/recreate/stop of a service", "interrupting a scan/job", "production deploy", "production config/env change",
    "activating a scheduled\n   task", "email_agent_cli.py arm", "each supplier-lifecycle switch", "NOA_ENGAGEMENT_AUTOREPLY",
    "approving an owner queue item", "destructive or bulk production data change", "credential rotation/revocation",
    "real outbound\n   messages", "any UI visual change", "push to the remote",
    "removing or\n   weakening any gate, guard, hook, permission rule, prohibition or this H list, or reversing a rule the owner set",
    "customer charges, supplier orders or payments", "supplier messages that create a\n   commercial commitment",
    "legal/commercial acceptance", "Live-mounted paths reach production without a restart",
]


@pytest.mark.parametrize("clause", H_REQUIRED)
def test_approval_lists_are_complete(clause):
    assert clause in PROTOCOL, clause


def test_golden_rule_8_text_intact():
    r8 = CLAUDE[CLAUDE.index("8. **Production safety"):CLAUDE.index("9. **Model selection")]
    for c in ("Never\n   restart, recreate, stop or cycle a production service", "never interrupt an active scan",
              "destructive production data change or a commercial action", "explicit approval for that operation"):
        assert c in r8, c


def test_precedence_and_read_only_exception():
    prec = _section("### Instruction precedence")
    assert prec.index("Safety gates") < prec.index("The owner's current prompt"), "safety gates must rank first"
    assert "No prompt\n   can disable them" in prec and "/root/.claude/CLAUDE.md" in prec
    assert "mission-specific authorization never extends" in prec
    assert "A read-only mission suspends every standing write rule" in prec


def test_golden_rules_point_to_protocol():
    assert "1. **Assignment Execution Protocol first** (§1)" in CLAUDE
    assert "a status from §1 protocol step I" in CLAUDE         # rule 8
    assert "assignment PARTIAL only when every open row is one of the last two" not in CLAUDE
    assert "Every extra occurrence\n   or layer found becomes its own row" in CLAUDE  # meta-rule 1
    assert ".claude/process/debug_escalation.py handoff" in CLAUDE  # rule 10
    assert "Claude continues the other independent register items" in CLAUDE


@pytest.mark.parametrize("phrase", [
    "work in order, don't skip",
    "give every task a status: **PASS**, **PARTIAL** or **BLOCKED**",
    "build it with `process/debug_escalation.py",
    "keep a 30-min wakeup (CronCreate is session-scoped",
    "Single authoritative instruction file",
])
def test_claude_md_has_no_contradicting_phrase(phrase):
    assert phrase not in CLAUDE, phrase


def test_moved_docs_exist_and_are_linked():
    for rel in ("docs/claude/IL_IMPORTER_SITES.md", "docs/claude/FEATURE_MODULES.md"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert "<!-- BEGIN VERBATIM -->" in text and "<!-- END VERBATIM -->" in text
        assert f"]({rel})" in CLAUDE, rel
    assert "keep a 30-min wakeup" not in (ROOT / "docs/claude/IL_IMPORTER_SITES.md").read_text(encoding="utf-8")


VERBATIM_SHA256 = {  # pinned 2026-11-27 (#160) after the marked amendments; change only with a recorded reason
    "docs/claude/IL_IMPORTER_SITES.md": "c0d802001b0e4636f5a4161116b47e85d10838a196a6650416a9750c93caa637",
    "docs/claude/FEATURE_MODULES.md": "63840a845219d75022eb1ac875ae4c59b81614310f0c945e0d3c8b82d93721e9",
}


@pytest.mark.parametrize("rel", sorted(VERBATIM_SHA256))
def test_moved_reference_unchanged(rel):
    s = (ROOT / rel).read_text(encoding="utf-8")
    body = s.split("<!-- BEGIN VERBATIM -->\n", 1)[1].rsplit("\n<!-- END VERBATIM -->", 1)[0]
    assert hashlib.sha256(body.encode()).hexdigest() == VERBATIM_SHA256[rel], f"{rel} changed: re-pin with a reason"


def _slug(h):
    return re.sub(r"[^\w\- ]", "", h.strip().lower()).replace(" ", "-")


def test_contents_anchors_resolve():
    heads = {_slug(m) for m in re.findall(r"^#{1,4} (.+)$", CLAUDE, re.M)}
    for anchor in re.findall(r"\]\(#([^)]+)\)", CLAUDE):
        assert anchor in heads, anchor


def test_section_stubs_keep_hard_rules():
    for rule in ("gstack design binaries never run on the production host", "Depends(require_api_key)",
                 "is_supplier_stated()", "small batch, minimum interval, daily ceiling", "DEAD END",
                 "### Monitoring wakeups (opt-in", "Restart sequence (owner-approved restarts only",
                 "secret_token=TELEGRAM_WEBHOOK_SECRET", "ONNX Runtime, never PyTorch", "never `ON CONFLICT` there",
                 "EX-VAT", "owner-added Bash permission rule"):
        assert rule in CLAUDE, rule


def test_escalation_tool_in_permanent_place():
    proc = ROOT / ".claude/process"
    for name in ("DEBUG_ESCALATION.md", "debug_escalation.py", "test_debug_escalation.py"):
        assert (proc / name).is_file(), name
    assert re.search(r"^\.debug_ledger/$", (ROOT / ".gitignore").read_text(encoding="utf-8"), re.M)
    assert "## While Opus works" in (proc / "DEBUG_ESCALATION.md").read_text(encoding="utf-8")


needs_memory = pytest.mark.skipif(not MEM.is_dir(), reason="project memory dir not present on this host")
if not MEM.is_dir():
    print(f"SKIP: memory checks skipped - {MEM} not present on this host", file=sys.stderr)


@needs_memory
@pytest.mark.parametrize("phrase", [
    "end the turn with a one-line status and resume only",
    "Never add changes without explicit user confirmation",
    "restart if >80%",
    "Use 161.97.158.177 in all scripts",
    "21:30, 00:15, 07:15 Israel (`DB_AGENT",
    "MCP setup PENDING",
    "Always have a 30-minute CronCreate wakeup active",
    "Restart with `docker restart autospare_backend`",
    "Routine scheduled jobs (run_all_tasks, harvesters, scans) are NOT a reason to delay a restart",
    "OWNER OPERATING DIRECTIVE",
])
def test_memory_has_no_contradicting_phrase(phrase):
    hits = [p.name for p in MEM.glob("*.md") if phrase in p.read_text(encoding="utf-8")]
    assert not hits, (phrase, hits)


@needs_memory
def test_stale_memories_removed_and_not_linked():
    gone = ("feedback_docker_restart.md", "feedback_pre_restart_procedure.md", "feedback_ssh_port_stale.md")
    for name in gone:
        assert not (MEM / name).exists(), name
    allt = "".join(p.read_text(encoding="utf-8") for p in MEM.glob("*.md"))
    for slug in ("feedback-docker-restart", "feedback-pre-restart-procedure", "feedback-ssh-port-stale", *gone):
        assert slug not in allt, slug


@needs_memory
def test_memory_index_lines_match_revised_rules():
    idx = (MEM / "MEMORY.md").read_text(encoding="utf-8")
    assert "end the turn only when every open item depends on it" in idx
    assert "OPT-IN" in idx
    assert "01:00/03:00/05:00" in idx
    for line in idx.splitlines():
        m = re.search(r"\]\(([^)]+\.md)\)", line)
        if m:
            assert (MEM / m.group(1)).is_file(), line


@needs_memory
def test_wiki_links_resolve():
    names = set()
    for p in MEM.glob("*.md"):
        m = re.search(r"^name:\s*(\S+)", p.read_text(encoding="utf-8"), re.M)
        if m:
            names.add(m.group(1).strip('"'))
    bad = [(p.name, l) for p in MEM.glob("*.md") for l in re.findall(r"\[\[([^\]]+)\]\]", p.read_text(encoding="utf-8"))
           if l not in names and l.replace("_", "-") not in names and l != "name"]
    allowed = {"pricing_policy"}  # pre-existing slug style, target exists as pricing_policy.md
    assert not [b for b in bad if b[1] not in allowed], bad


def _run_without_pytest() -> int:
    fails = 0
    for name, fn in list(globals().items()):
        if not name.startswith("test_") or not callable(fn) or getattr(fn, "_skip", False):
            continue
        params = getattr(fn, "_params", None)
        for v in (params[1] if params else [None]):
            try:
                fn(v) if params else fn()
            except Exception as exc:  # noqa: BLE001
                fails += 1
                print(f"FAIL {name}[{v!r}]: {exc!r}"[:400])
    print("instruction conformance:", "OK" if not fails else f"{fails} failure(s)")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(_run_without_pytest())
