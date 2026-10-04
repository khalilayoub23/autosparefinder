"""
Script: maintenance/gmail_oauth_setup.py
Purpose: One-time Gmail OAuth setup helper for the Email Agent. Runs ON THE HOST (stdlib only)
         because it reads and writes the gitignored /opt/autosparefinder/.env. It never prints a
         token, a client secret or an authorization code.
Process:
  auth-url [--read-only]
      Prints the Google consent URL for the business mailbox (login_hint) with the minimal
      scopes: gmail.readonly, plus gmail.compose unless --read-only. Uses GMAIL_OAUTH_CLIENT_ID
      from .env, falling back to the existing business-project web client (YOUTUBE_CLIENT_ID).
      The redirect goes to a URI we control (https://autosparefinder.co.il/), so the code is
      not consumed by anyone else - same flow that produced the YouTube token.
  exchange --code <code from the redirect URL>
      Exchanges the code server-side with OUR client, then writes GMAIL_OAUTH_CLIENT_ID /
      GMAIL_OAUTH_CLIENT_SECRET / GMAIL_OAUTH_REFRESH_TOKEN into .env (replacing old lines).
      Prints only which keys were written and the granted scopes.
  The backend picks the values up on its next start (no restart is performed here).
Data Imported/Modified: /opt/autosparefinder/.env (three GMAIL_OAUTH_* lines).
Data Sources: accounts.google.com (consent), oauth2.googleapis.com/token (exchange).
Missing Data Delegation: the consent click must be made by the owner, signed in as the mailbox.
Last Updated: 2026-10-04

Run: python3 /opt/autosparefinder/backend/maintenance/gmail_oauth_setup.py auth-url
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ENV_PATH = Path(__file__).resolve().parent.parent.parent / ".env"
REDIRECT_URI = "https://autosparefinder.co.il/"
MAILBOX = "autosparefinder2024@gmail.com"
SCOPE_READONLY = "https://www.googleapis.com/auth/gmail.readonly"
SCOPE_COMPOSE = "https://www.googleapis.com/auth/gmail.compose"


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


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("auth-url")
    a.add_argument("--read-only", action="store_true")
    e = sub.add_parser("exchange")
    e.add_argument("--code", required=True)
    args = ap.parse_args()
    env = _read_env()
    cid, sec = _client(env)

    if args.cmd == "auth-url":
        scopes = [SCOPE_READONLY] + ([] if args.read_only else [SCOPE_COMPOSE])
        print("https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
            "client_id": cid, "redirect_uri": REDIRECT_URI, "response_type": "code",
            "scope": " ".join(scopes), "access_type": "offline", "prompt": "consent",
            "login_hint": MAILBOX}))
        return 0

    body = urllib.parse.urlencode({"code": args.code, "client_id": cid, "client_secret": sec,
                                   "redirect_uri": REDIRECT_URI, "grant_type": "authorization_code"}).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request("https://oauth2.googleapis.com/token", data=body),
                                    timeout=25) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as err:
        detail = json.loads(err.read() or b"{}")
        sys.exit(f"exchange failed: HTTP {err.code} error={detail.get('error')}")
    refresh = data.get("refresh_token")
    if not refresh:
        sys.exit("exchange returned no refresh_token (re-run auth-url; prompt=consent is required).")
    _write_env({"GMAIL_OAUTH_CLIENT_ID": cid, "GMAIL_OAUTH_CLIENT_SECRET": sec,
                "GMAIL_OAUTH_REFRESH_TOKEN": refresh})
    print("saved to .env: GMAIL_OAUTH_CLIENT_ID, GMAIL_OAUTH_CLIENT_SECRET, GMAIL_OAUTH_REFRESH_TOKEN")
    print("granted scopes:", data.get("scope", ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
