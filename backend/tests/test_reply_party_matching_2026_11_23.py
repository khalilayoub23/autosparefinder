"""Reply routing must match the PARTY, not a free-mail domain (2026-11-23).

Incident: our own notification e-mail (autosparefinder2024@gmail.com) was recorded as the REPLY of an inquiry sent to a
gmail.com supplier (domain-equality on gmail.com), creating a false REPLY_RECEIVED + NIR task and stopping follow-ups."""
from email_agent import supplier_routing as sr


def test_free_mail_needs_exact_address():
    assert not sr._same_party("autosparefinder2024@gmail.com", "garryhenaijun@gmail.com")
    assert not sr._same_party("someone.else@gmail.com", "garryhenaijun@gmail.com")
    assert sr._same_party("garryhenaijun@gmail.com", "GarryHenaijun@gmail.com")


def test_business_domain_identifies_the_company():
    assert sr._same_party("Geof.K@motorstate.com", "info@motorstate.com")
    assert not sr._same_party("x@other.com", "info@motorstate.com")
    assert not sr._same_party("", "info@motorstate.com")


def test_own_mailbox_is_never_a_supplier(monkeypatch):
    monkeypatch.setenv("EMAIL_AGENT_MAILBOX", "ops@example-own.test")
    assert sr._is_own_mailbox("autosparefinder2024@gmail.com")
    assert sr._is_own_mailbox("OPS@example-own.test")
    assert not sr._is_own_mailbox("info@motorstate.com")
