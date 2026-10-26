"""maintenance/gmail_oauth_setup.py - consent URL and redirect capture.

Regression for 2026-10-04: the owner approved the Google consent twice and saw "no code".
Google HAD redirected to https://autosparefinder.co.il/?...&code=... (two requests in the nginx
access log), but that URL is the public homepage, which renders normally and shows nothing.
The helper now binds each attempt to a one-time `state` and finds its own redirect, so nobody
has to notice or copy a code. No network, no real code, no exchange in these tests.
"""
import importlib.util
import json
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parent.parent / "maintenance" / "gmail_oauth_setup.py"
_spec = importlib.util.spec_from_file_location("gmail_oauth_setup", _PATH)
setup = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(setup)

FAKE_CODE = "4/0FAKE-not-a-real-authorization-code_000000000000000000"
READ = "https://www.googleapis.com/auth/gmail.readonly"


def line(query, ts="04/Oct/2026:20:17:34 +0000", path="/", status=200):
    return (f'203.0.113.7 - - [{ts}] "GET {path}?{query} HTTP/1.1" {status} 601 '
            f'"https://accounts.google.com/" "Mozilla/5.0"')


def callback(state, code=FAKE_CODE, **kw):
    q = urllib.parse.urlencode({"state": state, "iss": "https://accounts.google.com", "code": code, "scope": READ})
    return line(q, **kw)


def test_read_only_url_requests_only_gmail_readonly_and_carries_state():
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(setup.build_auth_url("cid", True, "st-1")).query)
    assert q["scope"] == [READ] and q["state"] == ["st-1"]
    assert q["redirect_uri"] == ["https://autosparefinder.co.il/"] and q["response_type"] == ["code"]
    assert q["access_type"] == ["offline"] and q["prompt"] == ["consent"]
    assert q["login_hint"] == ["autosparefinder2024@gmail.com"]
    full = urllib.parse.parse_qs(urllib.parse.urlsplit(setup.build_auth_url("cid", False, "s")).query)
    assert full["scope"] == [f"{READ} https://www.googleapis.com/auth/gmail.compose"]


def test_callback_is_found_by_state_and_summary_never_contains_the_code():
    logs = ['203.0.113.7 - - [04/Oct/2026:20:17:30 +0000] "GET / HTTP/1.1" 200 601 "-" "x"',
            callback("someone-elses-state", code="4/0OTHER"),
            callback("st-1"),
            line("search=brake+pads", path="/parts")]
    cb = setup.find_callback(logs, "st-1")
    assert cb["code"] == FAKE_CODE and cb["scope"] == READ and cb["http_status"] == 200
    now = datetime(2026, 10, 4, 20, 18, 34, tzinfo=timezone.utc)
    info = setup.describe_callback(cb, now)
    assert info == {"callback_received": True, "http_status": 200, "age_s": 60, "code_present": True,
                    "code_length": len(FAKE_CODE), "code_redacted": False, "error": None, "granted_scope": READ,
                    "issuer": "https://accounts.google.com", "code_fresh": True}
    assert FAKE_CODE not in json.dumps(info)


def test_redirect_without_our_state_is_ignored():
    # the two real 2026-10-04 redirects had no state: they must never be picked up by a later run
    stateless = line(urllib.parse.urlencode({"iss": "https://accounts.google.com", "code": FAKE_CODE, "scope": READ}))
    assert setup.find_callback([stateless, callback("other")], "st-1") is None
    assert setup.describe_callback(None) == {"callback_received": False}


def test_only_the_site_root_counts_and_newest_match_wins():
    logs = [callback("st-1", code="4/0OLD", ts="04/Oct/2026:20:10:00 +0000"),
            callback("st-1", code="4/0ELSEWHERE", path="/login"),
            callback("st-1", code="4/0NEW", ts="04/Oct/2026:20:20:00 +0000")]
    assert setup.find_callback(logs, "st-1")["code"] == "4/0NEW"


def test_denied_consent_is_reported_as_an_error_not_a_code():
    denied = line(urllib.parse.urlencode({"state": "st-1", "error": "access_denied"}))
    info = setup.describe_callback(setup.find_callback([denied], "st-1"),
                                   datetime(2026, 10, 4, 20, 18, tzinfo=timezone.utc))
    assert info["error"] == "access_denied" and info["code_present"] is False and info["code_fresh"] is False


def test_stale_code_is_not_fresh():
    cb = setup.find_callback([callback("st-1")], "st-1")
    late = cb["time"] + timedelta(seconds=setup.CODE_MAX_AGE_S + 1)
    assert setup.describe_callback(cb, late)["code_fresh"] is False


def test_state_file_is_private_and_expires(tmp_path, monkeypatch):
    monkeypatch.setattr(setup, "STATE_PATH", tmp_path / "d" / "state.json")
    assert setup.load_state() is None
    setup.save_state("st-1", [READ])
    assert (setup.STATE_PATH.stat().st_mode & 0o777) == 0o600
    assert setup.load_state()["state"] == "st-1"
    assert setup.load_state(now=setup.time.time() + setup.STATE_TTL_S + 5) is None


def test_exchange_refuses_without_a_fresh_state_matched_callback(tmp_path, monkeypatch, capsys):
    env = tmp_path / ".env"
    env.write_text("YOUTUBE_CLIENT_ID=cid\nYOUTUBE_CLIENT_SECRET=sec\n")
    monkeypatch.setattr(setup, "ENV_PATH", env)
    monkeypatch.setattr(setup, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(setup, "_exchange", lambda *a: pytest.fail("exchange must not be attempted"))
    old = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%d/%b/%Y:%H:%M:%S +0000")
    for logs, expected in (([], "no redirect with our state"),
                           ([callback("st-1", ts=old)], "missing or too old"),
                           ([line("state=st-1&error=access_denied")], "access_denied")):
        setup.save_state("st-1", [READ])
        monkeypatch.setattr(setup, "nginx_access_lines", lambda since="70m", _l=logs: _l)
        monkeypatch.setattr(setup.sys, "argv", ["x", "exchange"])
        with pytest.raises(SystemExit) as e:
            setup.main()
        assert expected in str(e.value)
    assert "GMAIL_OAUTH" not in env.read_text() and FAKE_CODE not in capsys.readouterr().out


def test_exchange_from_callback_writes_env_and_prints_no_secret(tmp_path, monkeypatch, capsys):
    env = tmp_path / ".env"
    env.write_text("YOUTUBE_CLIENT_ID=cid\nYOUTUBE_CLIENT_SECRET=sec-value-000\nOTHER=1\n")
    monkeypatch.setattr(setup, "ENV_PATH", env)
    monkeypatch.setattr(setup, "STATE_PATH", tmp_path / "state.json")
    setup.save_state("st-1", [READ])
    now = datetime.now(timezone.utc).strftime("%d/%b/%Y:%H:%M:%S +0000")
    monkeypatch.setattr(setup, "nginx_access_lines", lambda since="70m": [callback("st-1", ts=now)])
    seen = {}

    def fake_exchange(code, cid, sec):
        seen["code"] = code
        return {"refresh_token": "1//fake-refresh-000", "access_token": "ya29.fake", "scope": READ}

    monkeypatch.setattr(setup, "_exchange", fake_exchange)
    monkeypatch.setattr(setup.sys, "argv", ["x", "exchange"])
    assert setup.main() == 0
    out = capsys.readouterr().out
    assert seen["code"] == FAKE_CODE and f"granted scopes: {READ}" in out
    for secret in (FAKE_CODE, "1//fake-refresh-000", "ya29.fake", "sec-value-000"):
        assert secret not in out
    text = env.read_text()
    assert "GMAIL_OAUTH_REFRESH_TOKEN=1//fake-refresh-000" in text and "OTHER=1" in text
    assert not setup.STATE_PATH.exists()


def test_redacted_code_in_the_log_is_reported_and_never_exchanged(tmp_path, monkeypatch):
    # after the nginx log redaction (2026-10-09) the access log holds "code=<redacted>"
    redacted = line("state=st-1&iss=https%3A%2F%2Faccounts.google.com&code=<redacted>&scope=" + urllib.parse.quote(READ))
    cb = setup.find_callback([redacted], "st-1")
    assert cb["code"] == "" and cb["code_redacted"] is True
    info = setup.describe_callback(cb, datetime(2026, 10, 4, 20, 18, tzinfo=timezone.utc))
    assert info["code_redacted"] is True and info["code_present"] is False and info["code_fresh"] is False
    env = tmp_path / ".env"
    env.write_text("YOUTUBE_CLIENT_ID=cid\nYOUTUBE_CLIENT_SECRET=sec\n")
    monkeypatch.setattr(setup, "ENV_PATH", env)
    monkeypatch.setattr(setup, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(setup, "_exchange", lambda *a: pytest.fail("a redacted code must never be exchanged"))
    now = datetime.now(timezone.utc).strftime("%d/%b/%Y:%H:%M:%S +0000")
    setup.save_state("st-1", [READ])
    monkeypatch.setattr(setup, "nginx_access_lines", lambda since="70m": [line("state=st-1&code=<redacted>", ts=now)])
    monkeypatch.setattr(setup.sys, "argv", ["x", "exchange"])
    with pytest.raises(SystemExit) as e:
        setup.main()
    assert "--code" in str(e.value) and "GMAIL_OAUTH" not in env.read_text()
