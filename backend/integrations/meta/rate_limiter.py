"""
integrations/meta/rate_limiter.py — Meta API rate limit utilities.

Delegates to the in-process rolling-window tracker in social/meta_client.py.
Exposes check_rate_limit() and a status inspector for the admin dashboard.
"""

from __future__ import annotations

import time


def check_rate_limit(endpoint: str) -> bool:
    """Return True if the call is within rate limits, False if blocked.

    Delegates to the same _call_log / _call_log_lock tracker used by graph_get().
    """
    from social.meta_client import _call_log, _call_log_lock, _RATE_LIMITS
    import threading

    cfg = _RATE_LIMITS.get(endpoint) or _RATE_LIMITS.get("default", {"max_calls": 200, "window_seconds": 3600})
    max_calls = cfg["max_calls"]
    window = cfg["window_seconds"]
    now = time.monotonic()

    with _call_log_lock:
        log = _call_log.setdefault(endpoint, [])
        _call_log[endpoint] = [t for t in log if now - t < window]
        return len(_call_log[endpoint]) < max_calls


def rate_limit_status() -> dict:
    """Return current rate limit usage for all tracked endpoints."""
    from social.meta_client import _call_log, _call_log_lock, _RATE_LIMITS
    now = time.monotonic()
    status = {}
    with _call_log_lock:
        for endpoint, cfg in _RATE_LIMITS.items():
            window = cfg["window_seconds"]
            recent = [t for t in _call_log.get(endpoint, []) if now - t < window]
            status[endpoint] = {
                "calls_in_window": len(recent),
                "max_calls": cfg["max_calls"],
                "window_seconds": window,
                "remaining": max(0, cfg["max_calls"] - len(recent)),
            }
    return status
