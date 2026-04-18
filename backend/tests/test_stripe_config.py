import pytest

from routes.stripe_config import (
    normalize_stripe_secret_key,
    resolve_stripe_secret_key,
    is_valid_stripe_secret_key,
)


def test_normalize_stripe_secret_key_strips_quotes_and_spaces():
    raw = "  'sk_test_1234567890ABCDEF123456'  "
    assert normalize_stripe_secret_key(raw) == "sk_test_1234567890ABCDEF123456"


@pytest.mark.parametrize(
    "value",
    [
        "sk_test_1234567890ABCDEF123456",
        "sk_live_1234567890ABCDEF123456",
        "rk_test_1234567890ABCDEF123456",
        "rk_live_1234567890ABCDEF123456",
    ],
)
def test_is_valid_stripe_secret_key_accepts_supported_key_prefixes(value):
    assert is_valid_stripe_secret_key(value) is True


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        "sk_test_CHANGE_ME",
        "sk_test_REPLACE_THIS",
        "pk_test_1234567890ABCDEF123456",
        "something_else",
    ],
)
def test_is_valid_stripe_secret_key_rejects_invalid_or_placeholder_values(value):
    assert is_valid_stripe_secret_key(value) is False


def test_resolve_stripe_secret_key_falls_back_to_legacy_env_names(monkeypatch):
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    monkeypatch.setenv("STRIPE_API_KEY", "rk_test_1234567890ABCDEF123456")

    key, source = resolve_stripe_secret_key()

    assert key == "rk_test_1234567890ABCDEF123456"
    assert source == "STRIPE_API_KEY"
