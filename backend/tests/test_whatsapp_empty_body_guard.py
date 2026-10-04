"""
Regression: WhatsApp send_message() must reject empty / whitespace-only bodies
at the provider boundary before any network call reaches the bridge.

Root cause (2026-09-27): Baileys retry/recovery path returned { conversation: '' }
for unknown message IDs → empty string reached send_message() → entered the 5-retry
loop, burning bridge capacity and generating confusing 503s.

Structural fix (2026-10-02): guard at the top of send_message() in
social/whatsapp_provider.py returns {"ok": False, "error": "INVALID_PAYLOAD: ..."}
before any httpx client is created. An INVALID_PAYLOAD is distinct from BRIDGE_DOWN.
"""
import asyncio
import pytest
import sys
import os

# Ensure the backend root is on sys.path regardless of CWD
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from social.whatsapp_provider import send_message


# ── helpers ──────────────────────────────────────────────────────────────────

def _run(coro):
    return asyncio.run(coro)   # get_event_loop() fails once an earlier async test has closed the loop


# ── INVALID_PAYLOAD cases (must never reach network) ─────────────────────────

@pytest.mark.parametrize("bad_text", [
    "",           # empty string
    "   ",        # whitespace only
    "\t\n",       # tabs and newlines
    None,         # None coerced to str
])
def test_empty_body_rejected_before_network(bad_text, monkeypatch):
    """Guard must return INVALID_PAYLOAD without calling httpx."""
    calls = []

    # If send_message() tries to open a network connection this will blow up.
    import httpx
    original_post = httpx.AsyncClient.post
    async def _spy_post(self, *args, **kwargs):
        calls.append(args)
        return await original_post(self, *args, **kwargs)
    monkeypatch.setattr(httpx.AsyncClient, "post", _spy_post)

    result = _run(send_message("972501234567", bad_text))

    assert result["ok"] is False, f"Expected ok=False for {bad_text!r}, got {result}"
    assert "INVALID_PAYLOAD" in result.get("error", ""), \
        f"Expected INVALID_PAYLOAD in error, got {result.get('error')!r}"
    assert result.get("key") is None
    assert not calls, "send_message() must not open network connections for empty body"


def test_non_empty_body_proceeds_to_network(monkeypatch):
    """A valid non-empty body must NOT be rejected by the guard."""
    attempted = []

    async def _stub_post(self, *args, **kwargs):
        attempted.append(True)
        # Simulate bridge down (connection refused) without actually hitting the network
        raise ConnectionRefusedError("test stub")

    import httpx
    monkeypatch.setattr(httpx.AsyncClient, "post", _stub_post)

    result = _run(send_message("972501234567", "Valid message text"))

    # The guard passed — the network call was attempted (and failed as expected from stub)
    assert attempted, "A valid body must reach the network path"
    # The error is from the connection, not INVALID_PAYLOAD
    assert "INVALID_PAYLOAD" not in result.get("error", ""), \
        "A valid body must not produce INVALID_PAYLOAD"


# ── distinct error codes (INVALID_PAYLOAD ≠ BRIDGE_DOWN) ─────────────────────

def test_invalid_payload_error_is_distinct_from_bridge_down(monkeypatch):
    """The caller can distinguish an empty-body error from a bridge failure."""
    import httpx
    monkeypatch.setattr(httpx.AsyncClient, "post",
                        lambda *a, **kw: (_ for _ in ()).throw(ConnectionRefusedError("down")))

    invalid_result = _run(send_message("972501234567", ""))
    down_result    = _run(send_message("972501234567", "real message"))

    assert "INVALID_PAYLOAD" in invalid_result.get("error", "")
    # Bridge-down error must NOT contain INVALID_PAYLOAD
    assert "INVALID_PAYLOAD" not in down_result.get("error", "")


# ── boundary: single space vs visible char ────────────────────────────────────

def test_single_space_rejected():
    result = _run(send_message("972501234567", " "))
    assert result["ok"] is False
    assert "INVALID_PAYLOAD" in result.get("error", "")


def test_single_char_passes_guard(monkeypatch):
    import httpx
    monkeypatch.setattr(httpx.AsyncClient, "post",
                        lambda *a, **kw: (_ for _ in ()).throw(ConnectionRefusedError("stub")))
    result = _run(send_message("972501234567", "x"))
    assert "INVALID_PAYLOAD" not in result.get("error", "")
