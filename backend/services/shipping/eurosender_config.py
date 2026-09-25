"""Eurosender feature-flag and safety-limit configuration.

Sandbox-only implementation. All defaults are the SAFE (off) state — nothing
here ever defaults to enabled. Every value is read fresh from the environment
on each call (no module-level caching) so tests can monkeypatch os.environ
without reloading the module, matching this repo's existing config pattern
(see routes/utils.py _env_flag).

No credentials live here. EUROSENDER_API_KEY (if ever set) is read directly
by eurosender_adapter.py and is never logged.
"""
from __future__ import annotations

import os


def _env_flag(name: str, default: str = "0") -> bool:
    return (os.getenv(name, default) or "").strip().lower() in ("1", "true", "yes", "on")


def eurosender_enabled() -> bool:
    """Global kill switch. Defaults OFF. Must be explicitly set to enable."""
    return _env_flag("EUROSENDER_ENABLED", "0")


def sandbox_mode() -> bool:
    """Whether the adapter must use the sandbox base URL. Defaults ON (safe)."""
    return _env_flag("EUROSENDER_SANDBOX", "1")


def supplier_allowlist() -> set[str]:
    """Supplier UUIDs explicitly approved for Eurosender routing.

    Empty by default. Never derived from country/region — an allowlist entry
    must be added explicitly (no invented UUIDs; Car-Parts.ie's real supplier
    UUID must be looked up from the live suppliers table before it can be
    added here).
    """
    raw = os.getenv("EUROSENDER_SUPPLIER_ALLOWLIST", "") or ""
    return {s.strip().lower() for s in raw.split(",") if s.strip()}


def max_weight_kg() -> float:
    return float(os.getenv("EUROSENDER_MAX_WEIGHT_KG", "25") or "25")


def api_key() -> str:
    return (os.getenv("EUROSENDER_API_KEY", "") or "").strip()


def webhook_secret() -> str:
    return (os.getenv("EUROSENDER_WEBHOOK_SECRET", "") or "").strip()


# ---------------------------------------------------------------------------
# Webhook signature verification — HARD CODE CONSTANT, not env-overridable.
#
# The Eurosender Webhook-Signature algorithm was not published in any public
# documentation or the OpenAPI spec (confirmed 2026-09-08 sandbox-contract
# research). This is a code-level guarantee, not a config toggle, precisely
# so a stray .env edit cannot silently "enable" production webhook trust in
# an unverified signature scheme. This constant only ever flips via a source
# change reviewed the same way as this comment — never via .env.
#
# VERIFIED TRUE 2026-09-25 (Phase 22): the algorithm was obtained from
# Eurosender support (message = Webhook-Event + Webhook-Id + raw_body,
# HMAC-SHA256, sandbox signing secret as UTF-8, header `sha256=<hex>`),
# implemented in routes/eurosender_webhook.py's verify_signature() (Phase 19),
# and cryptographically matched against a genuine Sandbox delivery: cancelling
# real Sandbox order 935766-26 produced a live `order_cancelled` webhook
# (Webhook-Id 10847) whose HMAC, computed with this same secret, equalled the
# received `Webhook-Signature`, and the unmodified production verifier
# accepted it. See FIXES_TRACKER.md 2026-09-25 (Phase 22) for full evidence.
#
# This flag means the SIGNATURE ALGORITHM is confirmed correct — it does NOT
# mean production is enabled. EUROSENDER_ENABLED / EUROSENDER_SANDBOX / the
# supplier allowlist remain the separate, independent gates for that.
# ---------------------------------------------------------------------------
EUROSENDER_WEBHOOK_SIGNATURE_VERIFIED = True


def production_url() -> str:
    return "https://api.eurosender.com"


def sandbox_url() -> str:
    return "https://sandbox-api.eurosender.com"


def base_url() -> str:
    """Always sandbox in this implementation. Never returns the production URL
    unless EUROSENDER_SANDBOX is explicitly set to 0 — and even then, the
    adapter itself refuses to run against production (see eurosender_adapter.py).
    """
    return sandbox_url() if sandbox_mode() else production_url()
