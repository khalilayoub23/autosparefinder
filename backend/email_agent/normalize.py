"""
Script: email_agent/normalize.py
Purpose: Turn a raw Gmail API message / thread into a NormalizedEmail the rest of the Email
         Agent can reason about, preserving the Gmail message and thread identifiers.
Process:
  - headers: From / Reply-To / To / Cc / Subject / Date / Message-ID / In-Reply-To / References
    plus the bulk / auto / authentication headers the classifier needs.
  - body: walks multipart trees (bounded depth and size); prefers text/plain, falls back to
    text/html converted to text (script/style removed), then to the Gmail snippet.
  - attachments: METADATA ONLY (filename, mime type, size, risky-extension flag). Attachment
    bytes are never downloaded, opened or executed - the Gmail attachment endpoint is not even
    on the client's allowlist.
  - a payload missing id / threadId / payload raises MalformedMessage.
Data Imported/Modified: none.
Last Updated: 2026-10-04
"""
from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.header import decode_header, make_header
from email.utils import getaddresses, parseaddr, parsedate_to_datetime
from typing import Any, Dict, List, Optional

MAX_BODY_CHARS = 200_000
MAX_DEPTH = 20
MAX_PARTS = 200

RISKY_EXTENSIONS = (
    ".exe", ".scr", ".bat", ".cmd", ".com", ".pif", ".js", ".jse", ".vbs", ".vbe", ".wsf", ".ps1",
    ".jar", ".msi", ".hta", ".lnk", ".iso", ".img", ".html", ".htm", ".svg", ".docm", ".xlsm",
    ".pptm", ".zip", ".rar", ".7z", ".ace",
)

_KEPT_HEADERS = (
    "list-unsubscribe", "list-id", "precedence", "auto-submitted", "x-auto-response-suppress",
    "authentication-results", "x-autoreply", "return-path", "feedback-id",
)

_QUOTE_CUT = re.compile(
    r"^\s*(On .{0,200} wrote:|בתאריך .{0,200} (כתב|כתבה).{0,40}|-{2,}\s*Original Message\s*-{2,}|"
    r"-{2,}\s*הודעה מקורית\s*-{2,}|From:\s.+|מאת:\s.+)\s*$",
    re.IGNORECASE,
)


class MalformedMessage(ValueError):
    pass


@dataclass
class NormalizedEmail:
    message_id: str
    thread_id: str
    rfc822_message_id: str = ""
    in_reply_to: str = ""
    references: str = ""
    sender_email: str = ""
    sender_name: str = ""
    reply_to_email: str = ""
    to: List[str] = field(default_factory=list)
    cc: List[str] = field(default_factory=list)
    subject: str = ""
    date: Optional[datetime] = None
    body_text: str = ""            # full normalized body
    new_text: str = ""             # body without quoted earlier messages
    body_source: str = "none"      # text/plain | text/html | snippet | none
    snippet: str = ""
    labels: List[str] = field(default_factory=list)
    headers: Dict[str, str] = field(default_factory=dict)
    attachments: List[Dict[str, Any]] = field(default_factory=list)
    is_inbound: bool = True

    @property
    def sender_domain(self) -> str:
        return self.sender_email.rsplit("@", 1)[-1] if "@" in self.sender_email else ""

    @property
    def reply_address(self) -> str:
        return self.reply_to_email or self.sender_email


def _decode_header_value(value: str) -> str:
    try:
        return str(make_header(decode_header(value or ""))).strip()
    except Exception:
        return (value or "").strip()


def _b64url(data: str) -> bytes:
    if not isinstance(data, str) or not data or not re.fullmatch(r"[A-Za-z0-9_\-]+={0,2}", data):
        return b""
    try:
        return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
    except (binascii.Error, ValueError):
        return b""


def _charset(part: dict) -> str:
    for h in part.get("headers") or []:
        if isinstance(h, dict) and str(h.get("name", "")).lower() == "content-type":
            m = re.search(r'charset="?([\w\-]+)"?', str(h.get("value", "")), re.I)
            if m:
                return m.group(1)
    return "utf-8"


def _decode_part(part: dict) -> str:
    body = part.get("body")
    raw = _b64url(body.get("data", "") if isinstance(body, dict) else "")
    if not raw:
        return ""
    try:
        return raw.decode(_charset(part), errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def html_to_text(html: str) -> str:
    """HTML -> plain text. Markup is treated as data: scripts/styles are dropped, nothing runs."""
    if not html:
        return ""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html[: MAX_BODY_CHARS * 2], "html.parser")
        for tag in soup(["script", "style", "head", "title", "noscript", "template"]):
            tag.decompose()
        for br in soup.find_all("br"):
            br.replace_with("\n")
        for blk in soup.find_all(["p", "div", "tr", "li", "h1", "h2", "h3", "h4"]):
            blk.append("\n")
        text = soup.get_text()
    except Exception:
        text = re.sub(r"(?is)<(script|style|head)\b.*?</\1>", " ", html)
        text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n[ \t]*", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def strip_quoted(text: str) -> str:
    out = []
    for line in (text or "").splitlines():
        if _QUOTE_CUT.match(line):
            break
        if line.lstrip().startswith(">"):
            continue
        out.append(line)
    return "\n".join(out).strip()


def _walk(part: Any, plain: List[str], html: List[str], atts: List[dict], depth: int, counter: List[int]) -> None:
    if not isinstance(part, dict) or depth > MAX_DEPTH or counter[0] >= MAX_PARTS:
        return
    counter[0] += 1
    mime = str(part.get("mimeType", "")).lower()
    filename = _decode_header_value(str(part.get("filename") or ""))
    body = part.get("body") if isinstance(part.get("body"), dict) else {}
    if filename or body.get("attachmentId"):
        disp = ""
        for h in part.get("headers") or []:
            if isinstance(h, dict) and str(h.get("name", "")).lower() == "content-disposition":
                disp = str(h.get("value", "")).lower()
        atts.append({
            "filename": filename[:255],
            "mime_type": mime[:100],
            "size": int(body.get("size") or 0) if str(body.get("size") or 0).isdigit() else 0,
            "inline": disp.startswith("inline"),
            "risky": filename.lower().endswith(RISKY_EXTENSIONS),
        })
        return
    if mime == "text/plain":
        plain.append(_decode_part(part))
    elif mime == "text/html":
        html.append(_decode_part(part))
    for sub in part.get("parts") or []:
        _walk(sub, plain, html, atts, depth + 1, counter)


def normalize_message(raw: Any, mailbox: str = "") -> NormalizedEmail:
    if not isinstance(raw, dict):
        raise MalformedMessage("message is not an object")
    mid, tid = raw.get("id"), raw.get("threadId")
    payload = raw.get("payload")
    if not mid or not tid or not isinstance(payload, dict):
        raise MalformedMessage("message is missing id, threadId or payload")

    hdr: Dict[str, str] = {}
    for h in payload.get("headers") or []:
        if isinstance(h, dict) and h.get("name"):
            hdr.setdefault(str(h["name"]).lower(), str(h.get("value", "")))

    sender_name, sender_email = parseaddr(_decode_header_value(hdr.get("from", "")))
    _, reply_to = parseaddr(_decode_header_value(hdr.get("reply-to", "")))
    to = [a.lower() for _, a in getaddresses([hdr.get("to", "")]) if a]
    cc = [a.lower() for _, a in getaddresses([hdr.get("cc", "")]) if a]

    date: Optional[datetime] = None
    internal = str(raw.get("internalDate") or "")
    if internal.isdigit():
        date = datetime.fromtimestamp(int(internal) / 1000, tz=timezone.utc)
    elif hdr.get("date"):
        try:
            date = parsedate_to_datetime(hdr["date"])
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            date = None

    plain: List[str] = []
    html: List[str] = []
    atts: List[dict] = []
    _walk(payload, plain, html, atts, 0, [0])
    snippet = str(raw.get("snippet") or "")
    plain_text = "\n".join(p for p in plain if p.strip()).strip()
    if plain_text:
        body, source = plain_text, "text/plain"
    elif any(h.strip() for h in html):
        body, source = html_to_text("\n".join(html)), "text/html"
    elif snippet:
        body, source = snippet, "snippet"
    else:
        body, source = "", "none"
    body = body[:MAX_BODY_CHARS]

    labels = [str(x) for x in (raw.get("labelIds") or []) if isinstance(x, str)]
    sender_email = sender_email.strip().lower()
    outbound = "SENT" in labels or "DRAFT" in labels or (bool(mailbox) and sender_email == mailbox.lower())
    return NormalizedEmail(
        message_id=str(mid), thread_id=str(tid),
        rfc822_message_id=hdr.get("message-id", "").strip(),
        in_reply_to=hdr.get("in-reply-to", "").strip(),
        references=hdr.get("references", "").strip(),
        sender_email=sender_email, sender_name=sender_name.strip()[:200],
        reply_to_email=reply_to.strip().lower(), to=to, cc=cc,
        subject=_decode_header_value(hdr.get("subject", ""))[:500],
        date=date, body_text=body, new_text=strip_quoted(body), body_source=source,
        snippet=snippet[:300], labels=labels,
        headers={k: hdr[k] for k in _KEPT_HEADERS if k in hdr},
        attachments=atts, is_inbound=not outbound,
    )


def normalize_thread(raw: Any, mailbox: str = "") -> List[NormalizedEmail]:
    """All messages of a thread, oldest first. A malformed member is skipped, not fatal."""
    if not isinstance(raw, dict) or not isinstance(raw.get("messages"), list):
        raise MalformedMessage("thread has no messages list")
    out: List[NormalizedEmail] = []
    for m in raw["messages"]:
        try:
            out.append(normalize_message(m, mailbox))
        except MalformedMessage:
            continue
    out.sort(key=lambda e: e.date or datetime.fromtimestamp(0, tz=timezone.utc))
    return out
