"""
Regression: automated tests can never send real WhatsApp messages through the production bridge.

Incident 2026-09-14 - one `pytest` run against the live server registered 51 synthetic users; each
register + login called create_2fa_code(), which sent a real WhatsApp 2FA message through the
production Baileys bridge (99 messages / 51 real-format numbers / 14 min); WhatsApp removed the
linked device. See synthetic_recipients.py and tests/conftest.py.

Nothing here touches the network, the DB or the real bridge.
"""
import asyncio
import inspect
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

import BACKEND_AUTH_SECURITY as sec
from synthetic_recipients import is_production_bridge_url, is_reserved_test_email
from tests import conftest as harness


# ── the boundary helpers ──────────────────────────────────────────────────────
@pytest.mark.parametrize("email", [
    "secrtc_ab12@example.com", "fulltest_1@example.com", "syscheck_x@EXAMPLE.COM", "a@example.org",
    "a@sub.example.net", "a@corp.test", "a@x.invalid", "a@host.localhost", "a@thing.example",
])
def test_reserved_domains_are_synthetic(email):
    assert is_reserved_test_email(email)


@pytest.mark.parametrize("email", [
    "person@gmail.com", "owner@autosparefinder.co.il", "a@notexample.com", "a@example.com.evil.io",
    "", None, "no-at-sign", "a@",
])
def test_real_or_malformed_addresses_are_not_synthetic(email):
    assert not is_reserved_test_email(email)


@pytest.mark.parametrize("url", [
    "http://whatsapp-bridge:3001/send", "http://whatsapp_bridge:3001/send", "http://localhost:3001/send",
    "http://127.0.0.1/send", "", None, "http://",
])
def test_production_bridge_endpoints_are_recognised(url):
    assert is_production_bridge_url(url)


@pytest.mark.parametrize("url", ["http://whatsapp-bridge.invalid:3001/send", "http://stub-bridge.test/send",
                                 "http://127.0.0.1:9/send"])
def test_non_production_endpoints_are_not_flagged(url):
    assert not is_production_bridge_url(url)


# ── test end: the harness points everything at a sink and fails closed ───────
def test_bridge_url_is_forced_to_an_unroutable_sink():
    import social.whatsapp_provider as wp
    assert os.environ["WHATSAPP_BRIDGE_URL"] == harness._TEST_BRIDGE_URL
    assert not is_production_bridge_url(wp.BRIDGE_URL)
    assert wp.BRIDGE_URL.split("//")[1].split(":")[0].endswith(".invalid")
    assert not is_production_bridge_url(wp.TYPING_URL) and not is_production_bridge_url(wp.DELETE_URL)


async def test_provider_send_cannot_reach_the_production_bridge(monkeypatch):
    import social.whatsapp_provider as wp
    seen = []

    async def _fake_post(self, url, *a, **kw):
        seen.append(str(url))
        raise httpx.ConnectError("blocked in test")

    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post)
    res = await wp.send_message("+972500000000", "isolation probe")
    assert res["ok"] is False
    assert seen and all(not is_production_bridge_url(u) for u in seen), seen


def test_explicit_production_bridge_override_aborts_the_run(monkeypatch):
    monkeypatch.setenv("TEST_WHATSAPP_BRIDGE_URL", "http://whatsapp-bridge:3001/send")
    with pytest.raises(pytest.UsageError, match="FAIL-CLOSED"):
        harness._force_test_bridge()


def test_explicit_stub_bridge_is_honoured(monkeypatch):
    monkeypatch.setenv("TEST_WHATSAPP_BRIDGE_URL", "http://stub-bridge.test:8080/send")
    monkeypatch.setenv("WHATSAPP_BRIDGE_URL", os.environ["WHATSAPP_BRIDGE_URL"])  # restored on teardown
    assert harness._force_test_bridge() == "http://stub-bridge.test:8080/send"


def test_tests_cannot_drive_messaging_endpoints_on_the_real_domain_or_with_real_emails():
    with pytest.raises(harness.RealMessagingBlocked):  # real domain
        httpx.post("https://autosparefinder.co.il/api/v1/auth/register", json={"email": "x@example.com"})
    with pytest.raises(harness.RealMessagingBlocked):  # local server but a real-looking address
        httpx.post("http://localhost:8000/api/v1/auth/login", json={"email": "person@gmail.com", "password": "x"})
    with pytest.raises(harness.RealMessagingBlocked):
        httpx.request("POST", "http://localhost:8000/api/v1/auth/register", json={"email": "person@gmail.com"})
    # allowed shape (checked directly so no request is made)
    harness.check_messaging_request("POST", "http://localhost:8000/api/v1/auth/login", {"email": "sec_x@example.com"})
    harness.check_messaging_request("GET", "https://autosparefinder.co.il/api/v1/auth/login", None)


# ── server end: the single delivery choke point ──────────────────────────────
class _Result:
    def __init__(self, user):
        self._user = user

    def scalar_one_or_none(self):
        return self._user


class _FakeDB:
    def __init__(self, user):
        self._user = user
        self.added = []

    async def execute(self, *_a, **_k):
        return _Result(self._user)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        return None


@pytest.fixture
def channels(monkeypatch):
    wa = AsyncMock(return_value={"ok": True, "key": None})
    sms = AsyncMock(return_value=True)
    monkeypatch.setattr(sec, "send_whatsapp_2fa", wa)
    monkeypatch.setattr(sec, "send_sms_2fa", sms)
    return wa, sms


@pytest.mark.parametrize("primary,enable", [(True, False), (False, True), (False, False)])
async def test_reserved_domain_account_never_gets_a_real_2fa_message(monkeypatch, channels, primary, enable):
    wa, sms = channels
    monkeypatch.setattr(sec, "WHATSAPP_2FA_PRIMARY", primary)
    monkeypatch.setattr(sec, "ENABLE_WHATSAPP_2FA", enable)
    db = _FakeDB(SimpleNamespace(full_name="Sec Test", email="secrtc_deadbeef@example.com"))
    code = await sec.create_2fa_code("00000000-0000-0000-0000-000000000001", "+972500000000", db)
    assert code and len(db.added) == 1, "the code must still be stored/returned so test flows keep working"
    wa.assert_not_awaited()
    sms.assert_not_awaited()


async def test_real_customer_delivery_is_unchanged(monkeypatch, channels):
    wa, sms = channels
    monkeypatch.setattr(sec, "WHATSAPP_2FA_PRIMARY", True)
    db = _FakeDB(SimpleNamespace(full_name="Real Customer", email="person@gmail.com"))
    code = await sec.create_2fa_code("00000000-0000-0000-0000-000000000002", "+972500000000", db)
    assert code
    wa.assert_awaited_once()          # WhatsApp still the primary channel for real customers
    sms.assert_not_awaited()          # and SMS is still only the fallback


def test_guard_sits_before_any_delivery_channel_selection():
    src = inspect.getsource(sec.create_2fa_code)
    assert src.index("is_reserved_test_email") < src.index("send_whatsapp_2fa(")
    assert src.index("is_reserved_test_email") < src.index("send_sms_2fa(")
