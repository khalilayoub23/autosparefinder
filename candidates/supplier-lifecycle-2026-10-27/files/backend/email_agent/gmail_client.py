"""
Script: email_agent/gmail_client.py
Purpose: Minimal Gmail API v1 client for the Email Agent: OAuth refresh-token flow, mailbox
         read (messages / threads), draft create + read-back, and sending ONE existing draft after
         the owner approved it (send_approved_draft + OwnerApproval, 2026-10-27). Nothing is ever
         sent autonomously: messages/send is unreachable, drafts/send only with an OwnerApproval,
         and every other request must be on ALLOWED_ENDPOINTS.
Process:
  - token: POST oauth2.googleapis.com/token (refresh_token grant), cached until ~expiry; the
    granted scope set is kept so read / draft capability is known, not assumed.
  - calls: paced (min_request_interval_s between API calls). GET is retried, bounded, only for
    errors proven retryable (rate limit, 5xx, network); POST is NEVER auto-retried (a retried
    draft create could produce a duplicate draft). 401 triggers one token refresh.
  - errors: classify_error() reads Google's own reason (error.errors[].reason,
    error.details[].reason, message) - a 403 is NOT one thing:
        rate_limit  quota / rate limit (HTTP 429 or a 403 "Quota exceeded")  retryable, throttle
        config      Gmail API disabled / never enabled in the project        owner action
        scope       grant lacks the scope                                    owner re-consent
        auth        invalid / expired / revoked credentials                  owner re-consent
        permission  authorization / IAM / delegation denied                  owner action
        unknown     a 403 with no recognizable reason                        retried next cycle, bounded
        transient   5xx / network                                            retryable
        permanent   other 4xx          malformed   unparseable success body
    Messages are redacted before they leave this module.
  - the mailbox is never mutated except by create_draft and send_approved_draft (sends ONE existing
    draft, only with an OwnerApproval; 2026-10-27), and every non-GET request is sent
    inside a runtime-kill-switch write intent (email_agent/control.py): announced, checked, sent.
Data Imported/Modified: Gmail drafts (create only), nothing else.
Data Sources: https://gmail.googleapis.com/gmail/v1/users/me, https://oauth2.googleapis.com/token
Last Updated: 2026-10-27
"""
from __future__ import annotations

import json
from dataclasses import dataclass
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, List, Optional, Tuple

from email_agent import control as _control
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

# The ONE send endpoint, reachable only through send_approved_draft() with an OwnerApproval
# (owner decision 2026-10-27: every reply is sent only after the owner approves it in the WhatsApp
# console). messages/send - composing and sending a new message - is never reachable.
OWNER_APPROVED_ENDPOINTS: Tuple[Tuple[str, "re.Pattern[str]"], ...] = (
    ("POST", re.compile(r"^/drafts/send$")),
)


# NIR's routine, non-binding supplier inquiries (owner task 2026-10-27, section 4.4): a SEPARATE, narrowly
# scoped path. Reachable only through send_supplier_inquiry() with a SupplierInquiryAuthorization, which
# supplier_outreach.py builds only after the scope check, the verified-recipient check, the dedupe check, the
# draft read-back check and a recorded supplier_message/routine_inquiry authorization.
SUPPLIER_INQUIRY_ENDPOINTS: Tuple[Tuple[str, "re.Pattern[str]"], ...] = (
    ("POST", re.compile(r"^/drafts/send$")),
)


@dataclass(frozen=True)
class SupplierInquiryAuthorization:
    outreach_id: str
    draft_id: str
    authorization_id: str
    recipient: str
    body_sha256: str


# A purchase-order email to a supplier (supplier_ordering.EmailPurchaseOrderConnector): reachable only through
# send_purchase_order_email() with a PurchaseOrderEmailAuthorization, which exists only after the owner's
# purchase_order approval for the exact terms (procurement.submit_po) and a draft read-back check.
PURCHASE_ORDER_ENDPOINTS: Tuple[Tuple[str, "re.Pattern[str]"], ...] = (
    ("POST", re.compile(r"^/drafts/send$")),
)


@dataclass(frozen=True)
class PurchaseOrderEmailAuthorization:
    procurement_id: str
    draft_id: str
    authorization_id: str
    recipient: str
    body_sha256: str


@dataclass(frozen=True)
class OwnerApproval:
    """Proof that the owner approved sending ONE specific Gmail draft. Created only by
    email_agent/owner_flow.approve_and_send after the approval is recorded in the store."""
    row_id: str
    draft_id: str
    approved_by: str
    approved_at: float

# (method, url, headers, body) -> (http_status, response_bytes). Raises OSError on network failure.
Transport = Callable[[str, str, Dict[str, str], Optional[bytes]], Tuple[int, bytes]]


RETRYABLE_KINDS = ("transient", "rate_limit", "unknown")
# Mailbox-level failures: not the fault of the message being processed, and not fixed by retrying.
MAILBOX_LEVEL_KINDS = ("auth", "scope", "config", "permission", "prohibited")

_RATE_REASONS = {"ratelimitexceeded", "userratelimitexceeded", "quotaexceeded", "dailylimitexceeded",
                 "rate_limit_exceeded", "resource_exhausted", "limitexceeded"}
_DISABLED_REASONS = {"accessnotconfigured", "service_disabled", "api_disabled"}
_SCOPE_REASONS = {"insufficientpermissions", "access_token_scope_insufficient", "insufficient_scope"}
_AUTH_REASONS = {"autherror", "unauthenticated", "invalid_grant", "invalid_credentials",
                 "access_token_expired", "account_state_invalid", "credentials_missing"}
_PERMISSION_REASONS = {"forbidden", "domainpolicy", "iam_permission_denied", "delegationdenied",
                       "user_project_denied", "consumer_invalid"}


class GmailError(Exception):
    def __init__(self, kind: str, message: str, http: int = 0):
        super().__init__(redact(message)[:400])
        self.kind = kind
        self.http = http

    @property
    def retryable(self) -> bool:
        return self.kind in RETRYABLE_KINDS


def classify_error(status: int, raw: bytes) -> Tuple[str, str]:
    """(kind, short detail) for a non-2xx Gmail response, from Google's structured error."""
    reasons: set = set()
    message = ""
    try:
        err = json.loads(raw or b"{}").get("error", {})
    except Exception:
        err = {}
    if isinstance(err, dict):
        message = str(err.get("message", ""))
        if err.get("status"):
            reasons.add(str(err["status"]).lower())
        for item in list(err.get("errors") or []) + list(err.get("details") or []):
            if isinstance(item, dict) and item.get("reason"):
                reasons.add(str(item["reason"]).lower())
    elif err:
        message = str(err)
    low = message.lower()
    detail = (" ".join(sorted(reasons)) + " " + message[:200]).strip() or "no error detail"
    if status == 429 or reasons & _RATE_REASONS or "quota exceeded" in low or "rate limit" in low:
        return "rate_limit", detail
    if status >= 500:
        return "transient", detail
    if reasons & _DISABLED_REASONS or "has not been used in project" in low or "it is disabled" in low:
        return "config", detail
    if reasons & _SCOPE_REASONS or "insufficient authentication scopes" in low or "insufficient permission" in low:
        return "scope", detail
    if status == 401 or reasons & _AUTH_REASONS:
        return "auth", detail
    if status == 403:
        if reasons & _PERMISSION_REASONS or "delegation denied" in low or "does not have permission" in low:
            return "permission", detail
        return "unknown", detail
    return "permanent", detail


class SendProhibited(GmailError):
    """Raised for any attempt to reach an endpoint outside ALLOWED_ENDPOINTS."""

    def __init__(self, method: str, path: str):
        super().__init__("prohibited", f"Gmail operation not allowed in this phase: {method} {path}")


def assert_allowed(method: str, path: str, owner_approved: bool = False, supplier_inquiry: bool = False,
                   purchase_order: bool = False) -> None:
    if sum((bool(owner_approved), bool(supplier_inquiry), bool(purchase_order))) > 1:
        raise SendProhibited(method, path)
    if purchase_order:
        if any(m == method.upper() and pat.match(path) for m, pat in PURCHASE_ORDER_ENDPOINTS):
            return
        raise SendProhibited(method, path)
    if owner_approved:
        if any(m == method.upper() and pat.match(path) for m, pat in OWNER_APPROVED_ENDPOINTS):
            return
        raise SendProhibited(method, path)
    if supplier_inquiry:
        if any(m == method.upper() and pat.match(path) for m, pat in SUPPLIER_INQUIRY_ENDPOINTS):
            return
        raise SendProhibited(method, path)
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
                 get_retries: int = 2, min_interval_s: Optional[float] = None,
                 write_intent: Optional[Callable[[str], Any]] = None):
        self._cfg = cfg
        # Runtime kill switch: every non-GET request (a draft create) is sent inside a write
        # intent - announced, then checked against the control, then sent (email_agent/control.py).
        self._write_intent = write_intent or _control.write_intent
        self._transport = transport
        self._clock = clock
        self._sleep = sleep
        self._get_retries = get_retries
        self._min_interval = cfg.min_request_interval_s if min_interval_s is None else min_interval_s
        self._next_call_at = 0.0
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
    def _pace(self) -> None:
        """Keep at least min_interval between Gmail API calls (per-user per-minute quota)."""
        if self._min_interval <= 0:
            return
        now = self._clock()
        if now < self._next_call_at:
            self._sleep(round(self._next_call_at - now, 3))
            now = self._next_call_at
        self._next_call_at = now + self._min_interval

    def _call(self, method: str, path: str, params: Optional[List[Tuple[str, str]]] = None,
              json_body: Optional[dict] = None, _owner_approved: bool = False,
              _supplier_inquiry: bool = False, _purchase_order: bool = False) -> Dict[str, Any]:
        assert_allowed(method, path, owner_approved=_owner_approved, supplier_inquiry=_supplier_inquiry,
                       purchase_order=_purchase_order)
        url = API_BASE + path + ("?" + urllib.parse.urlencode(params) if params else "")
        body = json.dumps(json_body).encode() if json_body is not None else None
        attempts = (self._get_retries + 1) if method == "GET" else 1
        refreshed = False
        last: Optional[GmailError] = None
        i = 0
        while i < attempts:
            if method != "GET":
                _control.require_running(f"gmail {method} {path}")   # halted: not even a token refresh
            headers = {"Authorization": f"Bearer {self._access_token()}", "Accept": "application/json"}
            if body is not None:
                headers["Content-Type"] = "application/json"
            self._pace()
            try:
                if method == "GET":
                    status, raw = self._transport(method, url, headers, body)
                else:
                    with self._write_intent(f"gmail {method} {path}"):   # raises control.Halted; nothing sent
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
            if status >= 400:
                kind, detail = classify_error(status, raw)
                err = GmailError(kind, f"Gmail HTTP {status} on {method} {path}: {detail}", status)
                if kind not in ("rate_limit", "transient"):
                    raise err                      # permanent / owner-action errors are never retried blindly
                last = err
                i += 1
                if i < attempts:
                    # a per-minute quota needs a longer pause than a transient 5xx
                    self._sleep(min(5 * 3 ** (i - 1), 30) if kind == "rate_limit" else min(2 ** i, 8))
                continue
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

    def list_message_ids(self, query: str, max_results: int = 25, include_spam: bool = False) -> List[Dict[str, str]]:
        params = [("q", query), ("maxResults", str(max_results))]
        if include_spam:
            params.append(("includeSpamTrash", "true"))      # messages.list omits SPAM/TRASH otherwise
        data = self._call("GET", "/messages", params)
        msgs = data.get("messages", [])
        if not isinstance(msgs, list):
            raise GmailError("malformed", "messages.list: 'messages' is not a list")
        out = []
        for m in msgs:
            if isinstance(m, dict) and m.get("id") and m.get("threadId"):
                out.append({"id": str(m["id"]), "threadId": str(m["threadId"])})
        return out

    def list_message_page(self, query: str, max_results: int = 25, page_token: Optional[str] = None,
                          include_spam: bool = False) -> Tuple[List[Dict[str, str]], Optional[str]]:
        """One page of messages.list plus the next page token (2026-10-27: the cycle pages past messages it already
        processed instead of re-reading only the newest page)."""
        params = [("q", query), ("maxResults", str(max_results))]
        if include_spam:
            params.append(("includeSpamTrash", "true"))
        if page_token:
            params.append(("pageToken", page_token))
        data = self._call("GET", "/messages", params)
        msgs = data.get("messages", [])
        if not isinstance(msgs, list):
            raise GmailError("malformed", "messages.list: 'messages' is not a list")
        out = [{"id": str(m["id"]), "threadId": str(m["threadId"])} for m in msgs
               if isinstance(m, dict) and m.get("id") and m.get("threadId")]
        nxt = data.get("nextPageToken")
        return out, (str(nxt) if nxt else None)

    def get_thread(self, thread_id: str) -> Dict[str, Any]:
        data = self._call("GET", f"/threads/{thread_id}", [("format", "full")])
        if data.get("id") != thread_id or not isinstance(data.get("messages"), list):
            raise GmailError("malformed", "threads.get: id mismatch or no messages list")
        return data

    def get_message_metadata(self, message_id: str) -> Dict[str, Any]:
        """Headers + labels only (no body) - used to decide whether a Spam message is relevant before reading it."""
        data = self._call("GET", f"/messages/{message_id}",
                          [("format", "metadata"), ("metadataHeaders", "From"), ("metadataHeaders", "Subject")])
        if data.get("id") != message_id:
            raise GmailError("malformed", "messages.get(metadata): id mismatch")
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

    def create_new_draft(self, raw_b64url: str, thread_id: Optional[str] = None) -> Dict[str, Any]:
        """A draft that starts a new thread (or follows up in one of OUR outreach threads)."""
        message: Dict[str, Any] = {"raw": raw_b64url}
        if thread_id:
            message["threadId"] = thread_id
        data = self._call("POST", "/drafts", json_body={"message": message})
        if not data.get("id"):
            raise GmailError("malformed", "drafts.create returned no draft id")
        return data

    def send_supplier_inquiry(self, auth: "SupplierInquiryAuthorization") -> Dict[str, Any]:
        """Send ONE verified supplier-inquiry draft (supplier_outreach.send_outreach). Never retried (POST)."""
        if not isinstance(auth, SupplierInquiryAuthorization) or not all(
                (auth.outreach_id, auth.draft_id, auth.authorization_id, auth.recipient, auth.body_sha256)):
            raise SendProhibited("POST", "/drafts/send (no supplier-inquiry authorization)")
        data = self._call("POST", "/drafts/send", json_body={"id": auth.draft_id}, _supplier_inquiry=True)
        if not data.get("id") or "SENT" not in (data.get("labelIds") or []):
            raise GmailError("malformed", "drafts.send returned no SENT message")
        return data

    def send_purchase_order_email(self, auth: "PurchaseOrderEmailAuthorization") -> Dict[str, Any]:
        """Send ONE verified purchase-order draft (owner-approved PO). Never retried (POST)."""
        if not isinstance(auth, PurchaseOrderEmailAuthorization) or not all(
                (auth.procurement_id, auth.draft_id, auth.authorization_id, auth.recipient, auth.body_sha256)):
            raise SendProhibited("POST", "/drafts/send (no purchase-order authorization)")
        data = self._call("POST", "/drafts/send", json_body={"id": auth.draft_id}, _purchase_order=True)
        if not data.get("id") or "SENT" not in (data.get("labelIds") or []):
            raise GmailError("malformed", "drafts.send returned no SENT message")
        return data

    def send_approved_draft(self, approval: "OwnerApproval") -> Dict[str, Any]:
        """Send ONE existing Gmail draft that the owner approved. Never retried (POST), passes the
        runtime kill switch like every write, and the result must carry the SENT label."""
        if not isinstance(approval, OwnerApproval) or not approval.draft_id or not approval.row_id \
                or not approval.approved_by or not approval.approved_at:
            raise SendProhibited("POST", "/drafts/send (no owner approval)")
        data = self._call("POST", "/drafts/send", json_body={"id": approval.draft_id}, _owner_approved=True)
        if not data.get("id") or "SENT" not in (data.get("labelIds") or []):
            raise GmailError("malformed", "drafts.send returned no SENT message")
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
