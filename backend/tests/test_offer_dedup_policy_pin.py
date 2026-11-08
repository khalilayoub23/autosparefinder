"""Hash pin of OFFER_DEDUPLICATION_POLICY (policy section 8): file hash == sibling .sha256 == docker-compose pin, and the loader /
gate fail CLOSED for a wrong pin, a missing pin when it is required, a missing or malformed file. No database, no network."""
import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

import offer_policy as op
from offer_policy import Classification as C

ROOT = Path(__file__).resolve().parent.parent
POLICY = Path(os.getenv("OFFER_POLICY_PATH") or ROOT.parent / "docs" / "policies" / "OFFER_DEDUPLICATION_POLICY.md")
COMPOSE = ROOT.parent / "docker-compose.yml"
pytestmark = pytest.mark.skipif(not POLICY.exists(), reason="policy file not mounted in this environment")


def _sha():
    return hashlib.sha256(POLICY.read_bytes()).hexdigest()


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("OFFER_POLICY_EXPECTED_SHA256", "OFFER_POLICY_REQUIRE_PIN"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("OFFER_POLICY_PATH", str(POLICY))


# ---------------------------------------------------------------- the three places agree
def test_sibling_sha256_file_matches_the_policy():
    sib = Path(str(POLICY) + ".sha256")
    if not sib.exists():
        pytest.skip("sibling .sha256 not present in this environment")
    assert sib.read_text().split()[0].lower() == _sha()


@pytest.mark.skipif(not COMPOSE.exists(), reason="docker-compose.yml is host-side only")
def test_compose_pin_lists_the_policy_hash_and_requires_the_pin():
    text = COMPOSE.read_text()
    m = re.search(r"OFFER_POLICY_EXPECTED_SHA256:\s*\"?\$\{OFFER_POLICY_EXPECTED_SHA256:-([0-9a-fA-F ,;]+)\}\"?", text)
    assert m, "backend service must set OFFER_POLICY_EXPECTED_SHA256 (policy section 8)"
    pins = [h.lower() for h in re.split(r"[\s,;]+", m.group(1).strip()) if h]
    assert _sha() in pins, f"compose pin {pins} does not list the policy file hash {_sha()}"
    assert all(len(h) == 64 for h in pins)
    assert re.search(r"OFFER_POLICY_REQUIRE_PIN:\s*\"?1\"?", text), "backend service must set OFFER_POLICY_REQUIRE_PIN=1"
    assert re.search(r"-\s*\./docs/policies:/policies:ro", text), "policy directory must be mounted read-only"


# ---------------------------------------------------------------- loader behaviour
def test_correct_pin_loads_and_reports_pinned(monkeypatch):
    monkeypatch.setenv("OFFER_POLICY_EXPECTED_SHA256", _sha())
    info = op.require_policy()
    assert info.sha256 == _sha() and info.pinned is True


def test_list_pin_accepts_old_and_new_hash_for_a_bump(monkeypatch):
    monkeypatch.setenv("OFFER_POLICY_EXPECTED_SHA256", f"{'a' * 64}, {_sha()}")
    assert op.require_policy().sha256 == _sha()
    monkeypatch.setenv("OFFER_POLICY_EXPECTED_SHA256", f"{'a' * 64} {'b' * 64}")
    with pytest.raises(op.PolicyUnavailable):
        op.require_policy()


def test_wrong_pin_fails_closed_for_loader_gate_and_dry_run(monkeypatch):
    monkeypatch.setenv("OFFER_POLICY_EXPECTED_SHA256", "0" * 64)
    with pytest.raises(op.PolicyUnavailable, match="does not match"):
        op.require_policy()
    with pytest.raises(SystemExit) as e:
        op.gate_or_exit("x", dry_run=False)
    assert e.value.code == 3
    assert op.gate_or_exit("x", dry_run=True) is None  # a dry run only reports; it writes nothing


def test_require_flag_without_pin_fails_closed(monkeypatch):
    monkeypatch.setenv("OFFER_POLICY_REQUIRE_PIN", "1")
    with pytest.raises(op.PolicyUnavailable, match="REQUIRE_PIN"):
        op.require_policy()
    with pytest.raises(SystemExit) as e:
        op.gate_or_exit("x", dry_run=False, enforce=True)
    assert e.value.code == 3
    monkeypatch.setenv("OFFER_POLICY_EXPECTED_SHA256", _sha())
    assert op.require_policy().pinned is True


def test_clearing_the_pin_does_not_weaken_a_required_pin(monkeypatch):
    monkeypatch.setenv("OFFER_POLICY_REQUIRE_PIN", "1")
    monkeypatch.setenv("OFFER_POLICY_EXPECTED_SHA256", "   ")
    with pytest.raises(op.PolicyUnavailable):
        op.require_policy()


def test_missing_and_edited_policy_fail_closed(monkeypatch, tmp_path):
    monkeypatch.setattr(op, "_candidate_paths", lambda: [tmp_path / "nope.md"])
    with pytest.raises(op.PolicyUnavailable, match="not found"):
        op.require_policy()
    edited = tmp_path / "OFFER_DEDUPLICATION_POLICY.md"
    edited.write_bytes(POLICY.read_bytes() + b"\nan unreviewed edit\n")
    monkeypatch.setattr(op, "_candidate_paths", lambda: [edited])
    monkeypatch.setenv("OFFER_POLICY_EXPECTED_SHA256", _sha())
    with pytest.raises(op.PolicyUnavailable, match="does not match"):
        op.require_policy()
    # a malformed file (class marker removed) is refused even with no pin
    monkeypatch.delenv("OFFER_POLICY_EXPECTED_SHA256")
    bad = tmp_path / "bad.md"
    bad.write_text(POLICY.read_text().replace("`UNRESOLVED`", "`X`"))
    monkeypatch.setattr(op, "_candidate_paths", lambda: [bad])
    with pytest.raises(op.PolicyUnavailable, match="classes missing"):
        op.require_policy()


def test_policy_version_and_marker_are_current():
    info = op.require_policy()
    assert info.version == "1.1.0"
    assert re.search(r"\|\s*1\.1\.0\s*\|", POLICY.read_text()), "change-history row for the current version is missing"


def test_policy_text_contains_the_restore_rules_the_gate_enforces():
    t = POLICY.read_text()
    for needle in ("5a. Restoration", "`restore`", "R1 Authorization", "R4 Canonical variant first", "1a. Canonical variant identity",
                   "OFFER_POLICY_EXPECTED_SHA256", "Bump procedure"):
        assert needle in t, needle


# ---------------------------------------------------------------- the gate cannot be reached without the policy
def test_write_gate_needs_a_loadable_policy(monkeypatch, tmp_path):
    monkeypatch.setattr(op, "_candidate_paths", lambda: [tmp_path / "nope.md"])
    with pytest.raises(op.PolicyUnavailable):
        op.assert_write_allowed("restore", C.UNRESOLVED, dry_run_done=True,
                                evidence={"approval_ref": "x", "input_sha256": "y", "source_backup": "z", "canonical_variant_checked": True})


def test_verify_script_passes_with_a_correct_pin():
    env = {**os.environ, "OFFER_POLICY_PATH": str(POLICY), "OFFER_POLICY_EXPECTED_SHA256": _sha(), "OFFER_POLICY_REQUIRE_PIN": "1"}
    r = subprocess.run([sys.executable, str(ROOT / "maintenance" / "verify_policy_pin.py")], env=env, capture_output=True, text=True,
                       cwd=str(ROOT))
    assert r.returncode == 0, r.stdout + r.stderr
    assert '"ok": true' in r.stdout
    env["OFFER_POLICY_EXPECTED_SHA256"] = "1" * 64
    r = subprocess.run([sys.executable, str(ROOT / "maintenance" / "verify_policy_pin.py")], env=env, capture_output=True, text=True,
                       cwd=str(ROOT))
    assert r.returncode == 1
