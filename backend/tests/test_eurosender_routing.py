"""Routing eligibility gate — must require BOTH the global flag AND allowlist
membership, and must never route based on country."""
import uuid

from services.shipping import eurosender_config
from services.shipping.eurosender_routing import is_eurosender_eligible


def test_disabled_globally_blocks_even_allowlisted_supplier(monkeypatch):
    sid = str(uuid.uuid4())
    monkeypatch.setenv("EUROSENDER_ENABLED", "0")
    monkeypatch.setenv("EUROSENDER_SUPPLIER_ALLOWLIST", sid)
    assert is_eurosender_eligible(sid) is False


def test_enabled_but_not_allowlisted_blocks(monkeypatch):
    sid = str(uuid.uuid4())
    other = str(uuid.uuid4())
    monkeypatch.setenv("EUROSENDER_ENABLED", "1")
    monkeypatch.setenv("EUROSENDER_SUPPLIER_ALLOWLIST", other)
    assert is_eurosender_eligible(sid) is False


def test_enabled_and_allowlisted_passes(monkeypatch):
    sid = str(uuid.uuid4())
    monkeypatch.setenv("EUROSENDER_ENABLED", "1")
    monkeypatch.setenv("EUROSENDER_SUPPLIER_ALLOWLIST", sid)
    assert is_eurosender_eligible(sid) is True


def test_allowlist_is_case_insensitive(monkeypatch):
    sid = str(uuid.uuid4())
    monkeypatch.setenv("EUROSENDER_ENABLED", "1")
    monkeypatch.setenv("EUROSENDER_SUPPLIER_ALLOWLIST", sid.upper())
    assert is_eurosender_eligible(sid.lower()) is True


def test_empty_allowlist_blocks_everyone(monkeypatch):
    sid = str(uuid.uuid4())
    monkeypatch.setenv("EUROSENDER_ENABLED", "1")
    monkeypatch.setenv("EUROSENDER_SUPPLIER_ALLOWLIST", "")
    assert is_eurosender_eligible(sid) is False


def test_none_supplier_id_never_eligible(monkeypatch):
    monkeypatch.setenv("EUROSENDER_ENABLED", "1")
    monkeypatch.setenv("EUROSENDER_SUPPLIER_ALLOWLIST", "anything")
    assert is_eurosender_eligible(None) is False


def test_multi_entry_allowlist(monkeypatch):
    sid1, sid2 = str(uuid.uuid4()), str(uuid.uuid4())
    monkeypatch.setenv("EUROSENDER_ENABLED", "1")
    monkeypatch.setenv("EUROSENDER_SUPPLIER_ALLOWLIST", f"{sid1}, {sid2}")
    assert is_eurosender_eligible(sid1) is True
    assert is_eurosender_eligible(sid2) is True
    assert is_eurosender_eligible(str(uuid.uuid4())) is False


def test_defaults_are_the_safe_off_state(monkeypatch):
    monkeypatch.delenv("EUROSENDER_ENABLED", raising=False)
    monkeypatch.delenv("EUROSENDER_SUPPLIER_ALLOWLIST", raising=False)
    assert eurosender_config.eurosender_enabled() is False
    assert eurosender_config.supplier_allowlist() == set()
    assert is_eurosender_eligible(str(uuid.uuid4())) is False


def test_sandbox_mode_defaults_on(monkeypatch):
    monkeypatch.delenv("EUROSENDER_SANDBOX", raising=False)
    assert eurosender_config.sandbox_mode() is True


def test_webhook_signature_verified_is_hardcoded_true_not_env_overridable(monkeypatch):
    # VERIFIED 2026-09-25 (Phase 22): the flag flipped True via a reviewed
    # source change after a real Sandbox delivery's signature was
    # cryptographically matched (see eurosender_config.py docstring and
    # FIXES_TRACKER.md). It must still NOT be overridable via environment in
    # either direction — it stays a code guarantee, not a config toggle.
    monkeypatch.setenv("EUROSENDER_WEBHOOK_SIGNATURE_VERIFIED", "false")
    assert eurosender_config.EUROSENDER_WEBHOOK_SIGNATURE_VERIFIED is True


def test_base_url_is_sandbox_by_default(monkeypatch):
    monkeypatch.delenv("EUROSENDER_SANDBOX", raising=False)
    assert eurosender_config.base_url() == eurosender_config.sandbox_url()
    assert eurosender_config.base_url() != eurosender_config.production_url()
