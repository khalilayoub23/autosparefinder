"""
Script: maintenance/gmail_oauth_setup.py
Purpose: One-time Gmail OAuth setup helper for the Email Agent. Runs ON THE HOST (stdlib only)
         because it reads and writes the gitignored /opt/autosparefinder/.env. It never prints a
         token, a client secret or an authorization code, and nobody has to copy a code by hand.
Process:
  auth-url [--read-only]
      Prints the Google consent URL for the business mailbox (login_hint) with the minimal
      scopes: gmail.readonly, plus gmail.compose unless --read-only. Uses GMAIL_OAUTH_CLIENT_ID
      from .env, falling back to the existing business-project web client (YOUTUBE_CLIENT_ID).
      A one-time random `state` is generated and remembered in a 0600 file outside the repo.
  callback-status
      Google redirects the browser to https://autosparefinder.co.il/?state=..&code=..&scope=..
      That URL is the public homepage: it renders normally and shows nothing about OAuth (the
      2026-10-04 incident - the owner approved twice and saw "no code"). The request is,
      however, recorded by the edge nginx. This command finds the callback whose `state`
      equals ours and reports only non-secret facts: received or not, age, granted scope,
      whether a code or an error came back, and the code's length.
  exchange [--code CODE]
      Exchanges the code server-side with OUR client and writes GMAIL_OAUTH_CLIENT_ID /
      GMAIL_OAUTH_CLIENT_SECRET / GMAIL_OAUTH_REFRESH_TOKEN into .env (replacing old lines).
      Without --code it takes the code from the state-matched callback (see callback-status).
      Prints only which keys were written and the granted scopes.
      Once the 2026-10-09 nginx log redaction is deployed, the access log records the redirect
      as "code=<redacted>": callback-status then reports code_redacted=true and exchange stops
      and asks for --code (copy it from the browser's address bar). Capturing the code
      server-side without logging it needs a dedicated callback route (not built).
  The backend picks the values up on its next start (no restart is performed here).
Data Imported/Modified: /opt/autosparefinder/.env (three GMAIL_OAUTH_* lines);
         /root/.cache/autosparefinder/gmail_oauth_state.json (one-time state, removed after use).
Data Sources: accounts.google.com (consent), oauth2.googleapis.com/token (exchange),
         `docker logs autospare_nginx` (read-only, to see the redirect).
Missing Data Delegation: the consent click must be made by the owner, signed in as the mailbox.
Last Updated: 2026-10-09

Run: python3 /opt/autosparefinder/backend/maintenance/gmail_oauth_setup.py auth-url --read-only
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional

ENV_PATH = Path(__file__).resolve().parent.parent.parent / ".env"
STATE_PATH = Path(os.environ.get("GMAIL_OAUTH_STATE_FILE", "/root/.cache/autosparefinder/gmail_oauth_state.json"))
NGINX_CONTAINER = os.environ.get("GMAIL_OAUTH_NGINX_CONTAINER", "autospare_nginx")
REDIRECT_URI = "https://autosparefinder.co.il/"
MAILBOX = "autosparefinder2024@gmail.com"
SCOPE_READONLY = "https://www.googleapis.com/auth/gmail.readonly"
SCOPE_COMPOSE = "https://www.googleapis.com/auth/gmail.compose"
STATE_TTL_S = 3600          # a consent URL older than this is not accepted
CODE_MAX_AGE_S = 480        # Google authorization codes are short-lived; refuse a stale one
REDACTED = "<redacted>"     # what the redacting nginx log format writes in place of a code

# nginx "combined" access line; only a request for exactly "/" with a query can be the redirect.
_ACCESS = re.compile(r'\[(?P<ts>[^\]]+)\] "GET /\?(?P<query>\S+) HTTP/[0-9.]+" (?P<status>\d{3}) ')


def _read_env() -> dict:
    out = {}
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _client(env: dict) -> tuple:
    cid = env.get("GMAIL_OAUTH_CLIENT_ID") or env.get("YOUTUBE_CLIENT_ID", "")
    sec = env.get("GMAIL_OAUTH_CLIENT_SECRET") or env.get("YOUTUBE_CLIENT_SECRET", "")
    if not cid or not sec:
        sys.exit("No OAuth client in .env: set GMAIL_OAUTH_CLIENT_ID and GMAIL_OAUTH_CLIENT_SECRET.")
    return cid, sec


def _write_env(values: dict) -> None:
    lines = [l for l in ENV_PATH.read_text(encoding="utf-8").splitlines()
             if l.split("=", 1)[0].strip() not in values]
    lines += [f"{k}={v}" for k, v in values.items()]
    tmp = ENV_PATH.with_suffix(".env.tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(tmp, ENV_PATH.stat().st_mode & 0o777)
    os.replace(tmp, ENV_PATH)


def build_auth_url(client_id: str, read_only: bool, state: str) -> str:
    scopes = [SCOPE_READONLY] + ([] if read_only else [SCOPE_COMPOSE])
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
        "client_id": client_id, "redirect_uri": REDIRECT_URI, "response_type": "code",
        "scope": " ".join(scopes), "access_type": "offline", "prompt": "consent",
        "login_hint": MAILBOX, "state": state})


def save_state(state: str, scopes: List[str]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(STATE_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"state": state, "created": time.time(), "scopes": scopes}, f)


def load_state(now: Optional[float] = None) -> Optional[dict]:
    try:
        data = json.loads(STATE_PATH.read_text())
    except (OSError, ValueError):
        return None
    if not data.get("state") or (now or time.time()) - float(data.get("created", 0)) > STATE_TTL_S:
        return None
    return data


def find_callback(lines: Iterable[str], state: str) -> Optional[dict]:
    """Newest redirect to "/" whose `state` equals ours. Returns the parsed query + its time."""
    found = None
    for line in lines:
        m = _ACCESS.search(line)
        if not m or "state=" not in m.group("query"):
            continue
        q = urllib.parse.parse_qs(m.group("query"), keep_blank_values=True)
        if q.get("state", [""])[0] != state:
            continue
        try:
            ts = datetime.strptime(m.group("ts"), "%d/%b/%Y:%H:%M:%S %z")
        except ValueError:
            ts = None
        raw_code = q.get("code", [""])[0]
        found = {"time": ts, "http_status": int(m.group("status")),
                 "code": "" if raw_code == REDACTED else raw_code, "code_redacted": raw_code == REDACTED,
                 "error": q.get("error", [""])[0],
                 "scope": q.get("scope", [""])[0], "iss": q.get("iss", [""])[0]}
    return found


def describe_callback(cb: Optional[dict], now: Optional[datetime] = None) -> dict:
    """Non-secret summary of a callback. The code itself is never included."""
    if cb is None:
        return {"callback_received": False}
    now = now or datetime.now(timezone.utc)
    age = int((now - cb["time"]).total_seconds()) if cb.get("time") else None
    return {"callback_received": True, "http_status": cb["http_status"], "age_s": age,
            "code_present": bool(cb["code"]), "code_length": len(cb["code"]),
            "code_redacted": bool(cb.get("code_redacted")),
            "error": cb["error"] or None, "granted_scope": cb["scope"] or None,
            "issuer": cb["iss"] or None,
            "code_fresh": bool(cb["code"]) and age is not None and age <= CODE_MAX_AGE_S}


def nginx_access_lines(since: str = "70m") -> List[str]:
    proc = subprocess.run(["docker", "logs", "--since", since, NGINX_CONTAINER],
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, errors="replace")
    return proc.stdout.splitlines()


def _exchange(code: str, cid: str, sec: str) -> dict:
    body = urllib.parse.urlencode({"code": code, "client_id": cid, "client_secret": sec,
                                   "redirect_uri": REDIRECT_URI, "grant_type": "authorization_code"}).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request("https://oauth2.googleapis.com/token", data=body),
                                    timeout=25) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as err:
        detail = json.loads(err.read() or b"{}")
        sys.exit(f"exchange failed: HTTP {err.code} error={detail.get('error')}")


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("auth-url")
    a.add_argument("--read-only", action="store_true")
    sub.add_parser("callback-status")
    e = sub.add_parser("exchange")
    e.add_argument("--code")
    args = ap.parse_args()
    env = _read_env()
    cid, sec = _client(env)

    if args.cmd == "auth-url":
        state = secrets.token_urlsafe(24)
        save_state(state, [SCOPE_READONLY] + ([] if args.read_only else [SCOPE_COMPOSE]))
        print(build_auth_url(cid, args.read_only, state))
        return 0

    st = load_state()
    cb = find_callback(nginx_access_lines(), st["state"]) if st else None
    if args.cmd == "callback-status":
        print(json.dumps({"pending_consent_url": bool(st), "requested_scopes": (st or {}).get("scopes"),
                          **describe_callback(cb)}, indent=2))
        return 0 if cb else 1

    code = args.code
    if not code:
        info = describe_callback(cb)
        if not st:
            sys.exit("no pending consent URL (or it expired): run auth-url first.")
        if not info["callback_received"]:
            sys.exit("no redirect with our state has reached the site yet.")
        if info["error"]:
            sys.exit(f"Google returned an error instead of a code: {info['error']}")
        if info["code_redacted"]:
            sys.exit("the redirect arrived, but nginx redacts OAuth codes from its logs: copy the code from the "
                     "browser address bar and run: exchange --code '<code>'")
        if not info["code_fresh"]:
            sys.exit("the authorization code is missing or too old: run auth-url and approve again.")
        code = cb["code"]
    data = _exchange(code, cid, sec)
    refresh = data.get("refresh_token")
    if not refresh:
        sys.exit("exchange returned no refresh_token (re-run auth-url; prompt=consent is required).")
    _write_env({"GMAIL_OAUTH_CLIENT_ID": cid, "GMAIL_OAUTH_CLIENT_SECRET": sec,
                "GMAIL_OAUTH_REFRESH_TOKEN": refresh})
    try:
        STATE_PATH.unlink()
    except OSError:
        pass
    print("saved to .env: GMAIL_OAUTH_CLIENT_ID, GMAIL_OAUTH_CLIENT_SECRET, GMAIL_OAUTH_REFRESH_TOKEN")
    print("granted scopes:", data.get("scope", ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
