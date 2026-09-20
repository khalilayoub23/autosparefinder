"""
Script: synthetic_recipients.py
Purpose: Safety boundary between AUTOMATED TESTS / synthetic accounts and REAL outbound messaging.
         Incident 2026-09-14: one `pytest` run of tests/ inside the production container
         (BASE_URL = the live server, WHATSAPP_2FA_PRIMARY=1) registered 51 synthetic users;
         register + login each call create_2fa_code(), which sent a real WhatsApp 2FA message
         through the production Baileys bridge to a randomly generated real-format phone
         number: 99 sends to 51 numbers in 14 minutes at 01:35 local time. WhatsApp removed the
         bridge's linked device (Stream Errored conflict, 401) 3 seconds after the last account
         was created.
Process:
  - is_reserved_test_email(): RFC 2606 / RFC 6761 reserved domains (example.com/.net/.org,
    *.test, *.example, *.invalid, *.localhost) can never belong to a real customer, so no real
    message may ever be delivered to such an account. Enforced at the single delivery choke
    point (BACKEND_AUTH_SECURITY.create_2fa_code).
  - is_production_bridge_url(): recognises the production WhatsApp bridge endpoint so the test
    harness (tests/conftest.py) can fail closed instead of ever pointing at it.
Data Imported/Modified: none (pure functions).
Data Sources: none.
Last Updated: 2026-09-20
"""

from __future__ import annotations

from urllib.parse import urlparse

_RESERVED_DOMAINS = ("example.com", "example.net", "example.org")
_RESERVED_TLDS = ("test", "example", "invalid", "localhost")

# Hostnames under which the production Baileys bridge is reachable (docker service / container name)
# plus its port on loopback. Anything else (including *.invalid sinks) is not production.
_PROD_BRIDGE_HOSTS = ("whatsapp-bridge", "whatsapp_bridge")
_PROD_BRIDGE_PORT = 3001
_LOOPBACK = ("localhost", "127.0.0.1", "::1", "0.0.0.0")


def is_reserved_test_email(email: str | None) -> bool:
    """True for addresses on reserved/example domains (never a real customer)."""
    if not email or "@" not in email:
        return False
    domain = email.strip().rsplit("@", 1)[1].strip().lower().rstrip(".")
    if not domain:
        return False
    if domain in _RESERVED_DOMAINS or any(domain.endswith("." + d) for d in _RESERVED_DOMAINS):
        return True
    return domain.rsplit(".", 1)[-1] in _RESERVED_TLDS


def is_production_bridge_url(url: str | None) -> bool:
    """True when `url` points at the real WhatsApp bridge (fail-closed: unparsable => production)."""
    if not url:
        return True  # unset => the provider falls back to the production default
    try:
        u = urlparse(url if "//" in url else "//" + url)
        host = (u.hostname or "").lower()
        port = u.port
    except ValueError:
        return True
    if not host:
        return True
    if host in _PROD_BRIDGE_HOSTS:
        return True
    if host in _LOOPBACK and (port is None or port == _PROD_BRIDGE_PORT):
        return True
    return False
