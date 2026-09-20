"""The hard rule from Phase 15: a Eurosender-eligible supplier must NEVER
receive synthetic/fake tracking, even via the pre-existing auto_fake_tracking
test-cycle credential flag (routes/utils.py trigger_supplier_fulfillment).
This is the earliest point in that function where the flag would otherwise
apply — tested here in isolation via the extracted _auto_fake_tracking_allowed
helper (see routes/utils.py, next to _resolve_supplier_spend_provider)."""
import uuid

from routes.utils import _auto_fake_tracking_allowed


def test_flag_not_requested_returns_false():
    assert _auto_fake_tracking_allowed({"auto_fake_tracking": False}, "any-id") is False
    assert _auto_fake_tracking_allowed({}, "any-id") is False
    assert _auto_fake_tracking_allowed(None, "any-id") is False


def test_flag_requested_and_not_eurosender_eligible_allows_fake_tracking(monkeypatch):
    monkeypatch.setenv("EUROSENDER_ENABLED", "0")
    sid = str(uuid.uuid4())
    assert _auto_fake_tracking_allowed({"auto_fake_tracking": True}, sid) is True


def test_flag_requested_but_eurosender_eligible_is_hard_blocked(monkeypatch):
    sid = str(uuid.uuid4())
    monkeypatch.setenv("EUROSENDER_ENABLED", "1")
    monkeypatch.setenv("EUROSENDER_SUPPLIER_ALLOWLIST", sid)
    assert _auto_fake_tracking_allowed({"auto_fake_tracking": True}, sid) is False


def test_eligibility_check_error_fails_safe_to_no_fake_tracking(monkeypatch):
    import routes.utils as utils_mod

    def _boom(*a, **kw):
        raise RuntimeError("simulated failure")

    monkeypatch.setattr("services.shipping.eurosender_routing.is_eurosender_eligible", _boom)
    assert _auto_fake_tracking_allowed({"auto_fake_tracking": True}, "some-id") is False
