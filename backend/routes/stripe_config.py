"""Shared Stripe configuration helpers."""

import os
import re


STRIPE_SECRET_ENV_CANDIDATES = (
    "STRIPE_SECRET_KEY",
    "STRIPE_API_KEY",
    "STRIPE_KEY",
    "STRIPE_SECRET",
)

STRIPE_ISSUING_WEBHOOK_SECRET_ENV_CANDIDATES = (
    "STRIPE_ISSUING_WEBHOOK_SECRET",
)


def normalize_stripe_secret_key(raw_key: str | None) -> str:
    """Normalize key text from env vars (strip whitespace and optional quotes)."""
    if raw_key is None:
        return ""

    key = str(raw_key).strip()
    if len(key) >= 2 and key[0] == key[-1] and key[0] in ('"', "'"):
        key = key[1:-1].strip()
    return key


def resolve_stripe_secret_key() -> tuple[str, str | None]:
    """Return first configured Stripe secret key and its source env var name."""
    for env_name in STRIPE_SECRET_ENV_CANDIDATES:
        key = normalize_stripe_secret_key(os.getenv(env_name))
        if key:
            return key, env_name
    return "", None


def is_valid_stripe_secret_key(raw_key: str | None) -> bool:
    """Validate Stripe secret/restricted API keys and reject placeholders."""
    key = normalize_stripe_secret_key(raw_key)
    if not key:
        return False

    upper_key = key.upper()
    placeholder_markers = (
        "CHANGE_ME",
        "CHANGEME",
        "XXXXX",
        "REPLACE",
        "YOUR_",
        "DUMMY",
        "PLACEHOLDER",
    )
    if any(marker in upper_key for marker in placeholder_markers):
        return False

    # Accept secret keys (sk_*) and restricted keys (rk_*).
    return bool(re.fullmatch(r"(?:sk|rk)_(?:test|live)_[A-Za-z0-9_-]{16,}", key))


def resolve_stripe_issuing_webhook_secret() -> tuple[str, str | None]:
    """Return first configured issuing webhook secret and its source env var name."""
    for env_name in STRIPE_ISSUING_WEBHOOK_SECRET_ENV_CANDIDATES:
        secret = normalize_stripe_secret_key(os.getenv(env_name))
        if secret:
            return secret, env_name
    return "", None
