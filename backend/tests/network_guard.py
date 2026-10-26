"""Outbound-network guard for the test process (FIXES_TRACKER #100).

Every outbound connection starts with a name lookup — sync sockets, asyncio, anyio/httpx, aiohttp, asyncpg and
redis alike call socket.getaddrinfo — so refusing it at that one point refuses the connection before any packet is
sent. Allowed: local and environment services (docker service names, which have no dot; sandbox/disposable
container names; loopback and private addresses). Everything else — a supplier site, a public API, a public IP — is
refused with socket.gaierror (what the egress-free test network itself returns) and RECORDED, so conftest can fail
the test that tried, even when the code under test swallowed the error.

Scope: the pytest process. A server the tests call over HTTP is a different process: its egress is closed by the
application rule (outbound_policy.external_suppliers_enabled, off outside production) and by the network
(`internal: true`). Subprocesses started by a test are not covered by this guard.
"""
from __future__ import annotations

import ipaddress
import socket

LOCAL_HOST_PREFIXES = ("sandbox_", "h78disp", "asfrel", "localhost", "127.", "::1", "0.0.0.0")


def is_local(host) -> bool:
    h = (host.decode() if isinstance(host, bytes) else str(host or "")).strip().lower().rstrip(".")
    if not h:
        return True                                   # getaddrinfo(None, port): a local bind, not a lookup
    if any(h.startswith(p) for p in LOCAL_HOST_PREFIXES) or "." not in h:     # docker service names
        return True
    try:
        ip = ipaddress.ip_address(h.strip("[]"))
        return ip.is_private or ip.is_loopback
    except ValueError:
        return False


class NetworkGuard:
    def __init__(self):
        self.blocked = []

    def install(self, monkeypatch):
        real = socket.getaddrinfo

        def guarded(host, *a, **kw):
            if not is_local(host):
                self.blocked.append(str(host.decode() if isinstance(host, bytes) else host))
                raise socket.gaierror(socket.EAI_NONAME, f"blocked by test network guard: {host}")
            return real(host, *a, **kw)
        monkeypatch.setattr(socket, "getaddrinfo", guarded)
        return self
