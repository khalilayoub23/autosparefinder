"""Every setting email_agent/config.py reads must be passed to the backend service in docker-compose.yml.

Regression for 2026-10-06: EMAIL_AGENT_QUERY and EMAIL_AGENT_MAX_ATTEMPTS were read by the code but had no
pass-through line, so a value in .env could never reach the container (compose only injects variables that
are listed under the service's `environment:`; .env is used for interpolation, not as an env_file).

Host-side test: docker-compose.yml lives outside the backend bind mount, so it skips inside the container
and runs on the host:  python3 backend/tests/test_email_agent_compose.py
"""
import os
import re
import sys

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMPOSE_PATH = os.path.join(os.path.dirname(BACKEND_DIR), "docker-compose.yml")
CONFIG_PATH = os.path.join(BACKEND_DIR, "email_agent", "config.py")


def _compose_text():
    if not os.path.exists(COMPOSE_PATH):
        import pytest
        pytest.skip("docker-compose.yml is not available in this environment (host-side test)")
    with open(COMPOSE_PATH, encoding="utf-8") as f:
        return f.read()


def _backend_env_lines():
    text = _compose_text()
    start = text.index("\n  backend:")
    nxt = re.search(r"\n  [a-z][a-z0-9_-]*:\n", text[start + 1:])
    block = text[start: start + 1 + nxt.start()] if nxt else text[start:]
    return dict(re.findall(r"^      ([A-Z][A-Z0-9_]*): (.*)$", block, re.M))


def _settings_read_by_code():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        src = f.read()
    return sorted(set(re.findall(r'"((?:EMAIL_AGENT|GMAIL_OAUTH)_[A-Z_]+)"', src)))


def test_every_config_setting_has_a_compose_pass_through():
    env, names = _backend_env_lines(), _settings_read_by_code()
    assert {"EMAIL_AGENT_QUERY", "EMAIL_AGENT_MAX_ATTEMPTS", "EMAIL_AGENT_ENABLED",
            "GMAIL_OAUTH_REFRESH_TOKEN"} <= set(names)
    missing = [n for n in names if n not in env]
    assert missing == [], f"read by email_agent/config.py but not passed to the backend service: {missing}"
    for n in names:
        assert re.fullmatch(r"\$\{%s(:-[^}]*)?\}" % n, env[n]), f"{n} must interpolate its own variable: {env[n]}"


def test_agent_and_gmail_drafts_stay_off_by_default():
    env = _backend_env_lines()
    assert env["EMAIL_AGENT_ENABLED"] == "${EMAIL_AGENT_ENABLED:-0}"
    assert env["EMAIL_AGENT_GMAIL_DRAFTS"] == "${EMAIL_AGENT_GMAIL_DRAFTS:-0}"


def test_no_send_switch_exists_in_compose():
    assert not re.search(r"EMAIL_AGENT_[A-Z_]*SEND", _compose_text())


def test_runbook_never_recreates_the_backend_without_no_deps():
    # 2026-10-09 preflight: a plain `up -d backend` would also recreate both Postgres containers
    docs = os.path.join(os.path.dirname(BACKEND_DIR), "docs", "EMAIL_AGENT.md")
    claude = os.path.join(os.path.dirname(BACKEND_DIR), "CLAUDE.md")
    if not os.path.exists(docs):
        import pytest
        pytest.skip("docs are not available in this environment (host-side test)")
    for path in (docs, claude):
        with open(path, encoding="utf-8") as f:
            text = f.read()
        bad = re.findall(r"docker compose up -d backend", text)
        assert not bad, f"{os.path.basename(path)} recommends 'docker compose up -d backend' without --no-deps"


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS", name)
            except AssertionError as e:
                failures += 1
                print("FAIL", name, "-", e)
    sys.exit(1 if failures else 0)
