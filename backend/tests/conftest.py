"""
Script: tests/conftest.py
Purpose: Fail-closed isolation between the automated test suite and REAL outbound messaging.

Why this exists (incident 2026-09-14): `pytest` (testpaths = tests) ran inside the production
container. The suites register users against BASE_URL = the LIVE server (localhost:8000), whose
WHATSAPP_2FA_PRIMARY=1 sent a real 2FA WhatsApp message through the production Baileys bridge to a
random real-format phone number for every register/login: 99 messages to 51 numbers in 14 minutes
at 01:35 local time. WhatsApp then removed the bridge's linked device (Stream Errored conflict, 401).

Boundary (two ends, one root cause):
  * SERVER end  - create_2fa_code() never delivers to reserved/example-domain accounts
                  (BACKEND_AUTH_SECURITY.py / synthetic_recipients.py).
  * TEST end    - this file:
      1. the WhatsApp bridge URL seen by any in-process code is forced to an unroutable sink
         (*.invalid, RFC 6761). An explicit TEST_WHATSAPP_BRIDGE_URL is honoured, but the run
         ABORTS if that (or the resolved provider URL) is the production bridge.
      2. tests may not drive messaging endpoints (register/login/send-2fa) on a non-local host
         (i.e. the real domain) and may only register/login reserved-domain emails.
Data Imported/Modified: none.
Last Updated: 2026-09-20
"""

from __future__ import annotations

import os
import sys
from urllib.parse import urlparse

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# tests/ is a package (__init__.py), so pytest's default import mode puts only its PARENT on sys.path. Test helpers
# (isolated_db_guard, chat_lifecycle_harness, checkout_fixture, catalog_population, …) are imported by bare name;
# without this line that import worked only if some EARLIER-collected module had inserted tests/ itself, which made
# tests/test_fitment_pipeline_guardrails.py a collection error alone and order-dependent in a full run (#88).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from synthetic_recipients import is_production_bridge_url, is_reserved_test_email  # noqa: E402

TEST_BRIDGE_SINK = "http://whatsapp-bridge.invalid:3001/send"
_MESSAGING_PATHS = ("/auth/register", "/auth/login", "/auth/send-2fa", "/auth/resend")
_LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")


class RealMessagingBlocked(AssertionError):
    """A test tried to trigger real outbound messaging."""


def _force_test_bridge() -> str:
    url = os.environ.get("TEST_WHATSAPP_BRIDGE_URL") or TEST_BRIDGE_SINK
    if is_production_bridge_url(url):
        raise pytest.UsageError(
            f"FAIL-CLOSED: TEST_WHATSAPP_BRIDGE_URL={url!r} is the production WhatsApp bridge. "
            "Automated tests must use a stub/isolated bridge, never production credentials."
        )
    os.environ["WHATSAPP_BRIDGE_URL"] = url
    return url


_TEST_BRIDGE_URL = _force_test_bridge()


def _point_provider_at_test_bridge() -> None:
    """The provider reads WHATSAPP_BRIDGE_URL at import; re-point it if it was imported earlier."""
    prov = sys.modules.get("social.whatsapp_provider")
    if prov is not None:
        prov.BRIDGE_URL = _TEST_BRIDGE_URL
        prov.TYPING_URL = _TEST_BRIDGE_URL.replace("/send", "/typing")
        prov.DELETE_URL = _TEST_BRIDGE_URL.replace("/send", "/delete")


def check_messaging_request(method: str, url, json_body) -> None:
    """Raise RealMessagingBlocked if this HTTP call could cause a real message to be sent."""
    if str(method).upper() != "POST":
        return
    target = str(url)
    if not any(p in target for p in _MESSAGING_PATHS):
        return
    host = (urlparse(target).hostname or "").lower()
    if host not in _LOCAL_HOSTS:
        raise RealMessagingBlocked(f"tests may not drive messaging endpoints on non-local host {host!r}")
    email = json_body.get("email") if isinstance(json_body, dict) else None
    if email is not None and not is_reserved_test_email(email):
        raise RealMessagingBlocked(
            "tests may only register/login reserved-domain accounts (example.com/.test/...): a non-reserved "
            "email could belong to a real person and would be sent a real 2FA message")


@pytest.fixture(scope="session", autouse=True)
def _no_real_messaging_from_tests():
    _point_provider_at_test_bridge()
    prov = sys.modules.get("social.whatsapp_provider")
    if prov is not None and is_production_bridge_url(prov.BRIDGE_URL):
        pytest.exit("FAIL-CLOSED: social.whatsapp_provider still points at the production bridge", returncode=3)

    import httpx
    mp = pytest.MonkeyPatch()
    _orig_post, _orig_request = httpx.post, httpx.request

    def _guarded_post(url, *a, **kw):
        check_messaging_request("POST", url, kw.get("json"))
        return _orig_post(url, *a, **kw)

    def _guarded_request(method, url, *a, **kw):
        check_messaging_request(method, url, kw.get("json"))
        return _orig_request(method, url, *a, **kw)

    mp.setattr(httpx, "post", _guarded_post)
    mp.setattr(httpx, "request", _guarded_request)
    yield
    mp.undo()


@pytest.fixture(autouse=True)
def _no_real_eurosender_production(monkeypatch):
    """Tests run inside the production container, which carries the LIVE Eurosender config
    (EUROSENDER_ENABLED=1, a production key). A test that inherited it sent a real request to
    api.eurosender.com on 2026-10-18. Remove the production credentials and disable production
    for every test; the adapter then refuses production before any network call. A test that
    needs a key or EUROSENDER_ENABLED sets it itself (fake key, mocked client)."""
    for k in ("EUROSENDER_PRODUCTION_API_KEY", "EUROSENDER_API_KEY",
              "EUROSENDER_PRODUCTION_WEBHOOK_SECRET"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("EUROSENDER_ENABLED", "0")
    monkeypatch.setenv("EUROSENDER_SANDBOX", "1")


# ── #97: deterministic rate-limit state for tests that call the RUNNING sandbox server ─────────────────────────
# Every request from these modules comes from one client address (127.0.0.1); the server's per-IP auth limits use
# 60-second windows (rate:register:{ip} 5/60 s, rate:login:{ip} 20/60 s, …). Whether a test got a 429 — and skipped —
# therefore depended on how many auth calls earlier tests (or a back-to-back previous run) had just made: the skip
# count changed with order and timing. Each such test now starts with a clean bucket for the test client. The limiter
# itself is untouched, so a test that drives it to 429 within itself (brute-force) still sees it; no test relies on a
# bucket filled by an earlier test (checked). Sandbox only: never runs unless the isolation guard passes and Redis is
# the sandbox instance.
_LIVE_SERVER_MODULES = {"test_system", "test_full_system", "test_security", "test_platform_goals",
                        "test_fitment_search_flows"}
_TEST_CLIENT_IPS = ("127.0.0.1", "::1", "localhost")


@pytest.fixture(autouse=True)
def _fresh_rate_limit_bucket_for_live_server_tests(request):
    if request.module.__name__.rsplit(".", 1)[-1] not in _LIVE_SERVER_MODULES:
        yield
        return
    try:
        import isolated_db_guard
        url = os.getenv("REDIS_URL", "")
        if isolated_db_guard.static_refusal() is None and "sandbox" in (urlparse(url).hostname or ""):
            import redis as _redis
            r = _redis.Redis.from_url(url, socket_timeout=2)
            try:
                for ip in _TEST_CLIENT_IPS:
                    keys = list(r.scan_iter(f"rate:*:{ip}")) + list(r.scan_iter(f"login:{ip}"))
                    if keys:
                        r.delete(*keys)
            finally:
                r.close()
    except Exception:
        pass        # best effort: a Redis hiccup must not turn into a test failure here
    yield


# #100 (2026-10-22): no test may reach a host outside the test environment. Every name lookup a test makes is checked
# (tests/network_guard.py): a supplier site, a public API or a public IP is refused before any packet is sent AND the
# test is reported as an error, even when the code under test swallowed the refusal — an attempted request must never
# pass silently. Local services (docker names, sandbox/disposable containers, private and loopback addresses) work
# normally. The server the live-server tests call is a separate process: outbound_policy (off outside production) and
# the network's `internal: true` close its egress.
@pytest.fixture(autouse=True)
def _no_real_outbound_network(monkeypatch):
    from network_guard import NetworkGuard
    guard = NetworkGuard().install(monkeypatch)
    yield guard
    if guard.blocked:
        pytest.fail(f"test tried to reach hosts outside the test environment (refused): {sorted(set(guard.blocked))}",
                    pytrace=False)
