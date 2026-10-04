"""
Script: email_agent/gmail_client.py
Purpose: Minimal Gmail API v1 client for the Email Agent: OAuth refresh-token flow, mailbox
         read (messages / threads) and draft create + read-back. THERE IS NO SEND FUNCTION, and
         the transport refuses every request that is not on ALLOWED_ENDPOINTS - a send endpoint
         (messages/send, drafts/send) cannot be reached through this module even by mistake.
Process:
  - token: POST oauth2.googleapis.com/token (refresh_token grant), cached until ~expiry; the
    granted scope set is kept so read / draft capability is known, not assumed.
  - calls: GET is retried on 429/5xx (bounded); POST is NEVER auto-retried (a retried draft
    create could produce a duplicate draft). 401 triggers one token refresh.
  - errors: GmailError(kind) with kind in auth | scope | transient | permanent | malformed;
    messages are redacted before they leave this module.
  - the mailbox is never mutated except by create_draft.
Data Imported/Modified: Gmail drafts (create only), nothing else.
Data Sources: https://gmail.googleapis.com/gmail/v1/users/me, https://oauth2.googleapis.com/token
Last Updated: 2026-10-04
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, List, Optional, Tuple

from email_agent.config import EmailAgentConfig
from email_agent.redaction import redact, register_secrets

API_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"
TOKEN_URL = "https://oauth2.googleapis.com/token"

_ID = r"[A-Za-z0-9_\-]{1,128}"
# The complete set of Gmail operations this phase may perform. No send endpoint, no
# modify/trash/delete/labels/attachments endpoint.
ALLOWED_ENDPOINTS: Tuple[Tuple[str, "re.Pattern[str]"], ...] = (
    ("GET", re.compile(r"^/profile$")),
    ("GET", re.compile(r"^/messages$")),
    ("GET", re.compile(rf"^/messages/{_ID}$")),
    ("GET", re.compile(rf"^/threads/{_ID}$")),
    ("GET", re.compile(rf"^/drafts/{_ID}$")),
    ("POST", re.compile(r"^/drafts$")),
)

# (method, url, headers, body) -> (http_status, response_bytes). Raises OSError on network failure.
Transport = Callable[[str, str, Dict[str, str], Optional[bytes]], Tuple[int, bytes]]


class GmailError(Exception):
    def __init__(self, kind: str, message: str, http: int = 0):
        super().__init__(redact(message)[:400])
        self.kind = kind
        self.http = http

    @property
    def retryable(self) -> bool:
        return self.kind == "transient"


class SendProhibited(GmailError):
    """Raised for any attempt to reach an endpoint outside ALLOWED_ENDPOINTS."""

    def __init__(self, method: str, path: str):
        super().__init__("prohibited", f"Gmail operation not allowed in this phase: {method} {path}")


def assert_allowed(method: str, path: str) -> None:
    if "send" in path.lower():
        raise SendProhibited(method, path)
    for m, pat in ALLOWED_ENDPOINTS:
        if m == method.upper() and pat.match(path):
            return
    raise SendProhibited(method, path)


def urllib_transport(method: str, url: str, headers: Dict[str, str], body: Optional[bytes]) -> Tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


class GmailClient:
    def __init__(self, cfg: EmailAgentConfig, transport: Transport = urllib_transport,
                 clock: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep,
                 get_retries: int = 2):
        self._cfg = cfg
        self._transport = transport
        self._clock = clock
        self._sleep = sleep
        self._get_retries = get_retries
        self._token = ""
        self._token_exp = 0.0
        self.granted_scopes: set[str] = set()

    # ── OAuth ────────────────────────────────────────────────────────────────
    def _refresh(self) -> str:
        if self._cfg.missing():
            raise GmailError("auth", "Gmail OAuth is not configured: missing " + ", ".join(self._cfg.missing()))
        body = urllib.parse.urlencode({
            "client_id": self._cfg.client_id, "client_secret": self._cfg.client_secret,
            "refresh_token": self._cfg.refresh_token, "grant_type": "refresh_token",
        }).encode()
        try:
            status, raw = self._transport("POST", TOKEN_URL,
                                          {"Content-Type": "application/x-www-form-urlencoded"}, body)
        except OSError as e:
            raise GmailError("transient", f"token endpoint unreachable: {type(e).__name__}") from None
        data = _parse_json(raw, "token response")
        if status >= 500 or status == 429:
            raise GmailError("transient", f"token endpoint HTTP {status}", status)
        tok = data.get("access_token") if isinstance(data, dict) else None
        if status != 200 or not tok:
            # invalid_grant = revoked / expired refresh token: needs owner re-consent, never retried blindly
            code = data.get("error") if isinstance(data, dict) else "unknown"
            raise GmailError("auth", f"token refresh rejected (HTTP {status}, error={code})", status)
        register_secrets([tok])
        self._token = tok
        self._token_exp = self._clock() + int(data.get("expires_in", 3600) or 3600)
        self.granted_scopes = set(str(data.get("scope", "")).split())
        return tok

    def _access_token(self) -> str:
        if self._token and self._token_exp > self._clock() + 60:
            return self._token
        return self._refresh()

    # ── transport ────────────────────────────────────────────────────────────
    def _call(self, method: str, path: str, params: Optional[List[Tuple[str, str]]] = None,
              json_body: Optional[dict] = None) -> Dict[str, Any]:
        assert_allowed(method, path)
        url = API_BASE + path + ("?" + urllib.parse.urlencode(params) if params else "")
        body = json.dumps(json_body).encode() if json_body is not None else None
        attempts = (self._get_retries + 1) if method == "GET" else 1
        refreshed = False
        last: Optional[GmailError] = None
        i = 0
        while i < attempts:
            headers = {"Authorization": f"Bearer {self._access_token()}", "Accept": "application/json"}
            if body is not None:
                headers["Content-Type"] = "application/json"
            try:
                status, raw = self._transport(method, url, headers, body)
            except OSError as e:
                last = GmailError("transient", f"Gmail unreachable: {type(e).__name__}")
                i += 1
                if i < attempts:
                    self._sleep(min(2 ** i, 8))
                continue
            if status == 401 and not refreshed:
                refreshed = True
                self._token = ""
                continue
            if status == 429 or status >= 500:
                last = GmailError("transient", f"Gmail HTTP {status} on {method} {path}", status)
                i += 1
                if i < attempts:
                    self._sleep(min(2 ** i, 8))
                continue
            if status in (401, 403):
                detail = _error_status(raw)
                kind = "scope" if "scope" in detail.lower() or status == 403 else "auth"
                raise GmailError(kind, f"Gmail HTTP {status} on {method} {path}: {detail}", status)
            if status >= 400:
                raise GmailError("permanent", f"Gmail HTTP {status} on {method} {path}: {_error_status(raw)}", status)
            data = _parse_json(raw, f"{method} {path}")
            if not isinstance(data, dict):
                raise GmailError("malformed", f"Gmail returned a non-object for {method} {path}")
            return data
        raise last or GmailError("transient", f"Gmail call failed: {method} {path}")

    # ── read path (GET only) ─────────────────────────────────────────────────
    def profile(self) -> Dict[str, Any]:
        data = self._call("GET", "/profile")
        if not data.get("emailAddress"):
            raise GmailError("malformed", "profile response has no emailAddress")
        return data

    def list_message_ids(self, query: str, max_results: int = 25) -> List[Dict[str, str]]:
        data = self._call("GET", "/messages", [("q", query), ("maxResults", str(max_results))])
        msgs = data.get("messages", [])
        if not isinstance(msgs, list):
            raise GmailError("malformed", "messages.list: 'messages' is not a list")
        out = []
        for m in msgs:
            if isinstance(m, dict) and m.get("id") and m.get("threadId"):
                out.append({"id": str(m["id"]), "threadId": str(m["threadId"])})
        return out

    def get_thread(self, thread_id: str) -> Dict[str, Any]:
        data = self._call("GET", f"/threads/{thread_id}", [("format", "full")])
        if data.get("id") != thread_id or not isinstance(data.get("messages"), list):
            raise GmailError("malformed", "threads.get: id mismatch or no messages list")
        return data

    def get_message(self, message_id: str) -> Dict[str, Any]:
        data = self._call("GET", f"/messages/{message_id}", [("format", "full")])
        if data.get("id") != message_id:
            raise GmailError("malformed", "messages.get: id mismatch")
        return data

    # ── drafts (create + read-back; never send) ──────────────────────────────
    def create_draft(self, raw_b64url: str, thread_id: str) -> Dict[str, Any]:
        data = self._call("POST", "/drafts", json_body={"message": {"raw": raw_b64url, "threadId": thread_id}})
        if not data.get("id"):
            raise GmailError("malformed", "drafts.create returned no draft id")
        return data

    def get_draft(self, draft_id: str) -> Dict[str, Any]:
        data = self._call("GET", f"/drafts/{draft_id}", [("format", "full")])
        if data.get("id") != draft_id or not isinstance(data.get("message"), dict):
            raise GmailError("malformed", "drafts.get: id mismatch or no message")
        return data


def _parse_json(raw: bytes, what: str) -> Any:
    try:
        return json.loads(raw) if raw else {}
    except (ValueError, UnicodeDecodeError):
        raise GmailError("malformed", f"{what}: body is not valid JSON") from None


def _error_status(raw: bytes) -> str:
    try:
        err = json.loads(raw or b"{}").get("error", {})
        if isinstance(err, dict):
            return f"{err.get('status', '')} {str(err.get('message', ''))[:160]}".strip()
        return str(err)[:160]
    except Exception:
        return "unparseable error body"
