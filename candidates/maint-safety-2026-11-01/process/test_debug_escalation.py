"""Tests for process/debug_escalation.py (the Two-Attempt Debugging Rule ledger). Run: python3 -m pytest process/test_debug_escalation.py"""
import json
import subprocess
import sys
from pathlib import Path

import debug_escalation as de
import pytest

KW = dict(cause="suspected stale pooled connection", action="dispose pool in conftest", verify="pytest pair both orders", evidence="x")


@pytest.fixture(autouse=True)
def ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("DEBUG_LEDGER_DIR", str(tmp_path))


def att(issue, outcome, **over):
    kw = {**KW, **over}
    return de.main(["attempt", issue, "--cause", kw["cause"], "--action", kw["action"], "--verify", kw["verify"], "--outcome", outcome, "--evidence", kw["evidence"]])


def test_first_failed_attempt_is_ok_and_second_fixes_it():
    assert att("I1", "failed") == 0
    assert att("I1", "fixed", action="a different fix: guard the insert") == 0
    assert de.main(["status", "I1"]) == 0


def test_two_failed_attempts_lock_the_ledger_and_a_third_is_refused():
    assert att("I2", "failed") == 0
    assert att("I2", "no-progress", action="trace the SQL with EXPLAIN instead") == de.EXIT_ESCALATE      # the 2nd miss itself signals ESCALATE
    assert de.main(["status", "I2"]) == de.EXIT_ESCALATE
    assert att("I2", "failed", action="yet another idea entirely") == de.EXIT_ESCALATE        # third independent attempt refused
    assert len(json.loads((Path(de._dir()) / "I2.json").read_text())["attempts"]) == 2


def test_a_repeat_of_the_same_action_is_not_a_new_attempt():
    assert att("I3", "failed") == 0
    assert att("I3", "failed") == 2


def test_vague_attempts_are_rejected():
    assert att("I4", "failed", cause="?") == 2


def test_handoff_is_refused_before_two_failures_and_complete_after(tmp_path):
    assert att("I5", "failed") == 0
    assert de.main(["handoff", "I5", "--task", "t", "--acceptance", "a"]) == 2
    assert att("I5", "failed", action="second approach: log the loop id") == de.EXIT_ESCALATE
    out = tmp_path / "h.md"
    assert de.main(["handoff", "I5", "--task", "Fix the pair failure", "--acceptance", "pair passes both orders", "--file", "tests/conftest.py",
                    "--error", "RuntimeError: attached to a different loop", "--out", str(out), "--repo", str(Path(__file__).parent)]) == 0
    h = out.read_text()
    for needle in ("Fix the pair failure", "pair passes both orders", "attached to a different loop", "Attempt 1", "Attempt 2",
                   "dispose pool in conftest", "log the loop id", "tests/conftest.py", "HEAD:", "git status", "No restart / recreate",
                   "Escalation does not override", "do NOT only advise"):
        assert needle in h, needle
    assert "password" not in h.lower() and "token=" not in h.lower()
    assert de.main(["status", "I5"]) == de.EXIT_ESCALATE
    assert de.main(["escalated", "I5"]) == 0


def test_cli_exit_code_through_a_real_subprocess(tmp_path):
    env = {"DEBUG_LEDGER_DIR": str(tmp_path), "PATH": "/usr/bin:/bin"}
    run = lambda *a: subprocess.run([sys.executable, str(Path(de.__file__)), *a], env=env, capture_output=True, text=True)
    for act, rc in (("first approach to the bug", 0), ("second approach to the bug", 3)):
        assert run("attempt", "I6", "--cause", "suspected cause", "--action", act, "--verify", "run the tests", "--outcome", "failed").returncode == rc
    r = run("attempt", "I6", "--cause", "a third cause", "--action", "third approach to bug", "--verify", "run tests again", "--outcome", "failed")
    assert r.returncode == 3 and "LOCKED" in r.stderr
