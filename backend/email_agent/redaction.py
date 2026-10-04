"""
Script: email_agent/redaction.py
Purpose: One scrubber every Email Agent log line, stored error and API response passes through,
         so an OAuth token / client secret / password can never reach docker logs or the DB.
Process: pattern-based redaction (Google token shapes, Bearer headers, key=value secrets) plus
         exact-value redaction of the secrets actually loaded from the environment.
Data Imported/Modified: none.
Last Updated: 2026-10-04
"""
from __future__ import annotations

import re
from typing import Iterable

REDACTED = "<redacted>"

_PATTERNS = [
    re.compile(r"ya29\.[A-Za-z0-9_\-.]+"),                    # Google access token
    re.compile(r"1//[A-Za-z0-9_\-]{10,}"),                    # Google refresh token
    re.compile(r"GOCSPX-[A-Za-z0-9_\-]+"),                    # Google client secret
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9_\-.=+/]+"),
    re.compile(
        r"(?i)(\"?(?:access_token|refresh_token|id_token|client_secret|password|passwd|"
        r"smtp_pass|api_key|token)\"?\s*[:=]\s*\"?)[^\"&\s,}]+"
    ),
]

_known_secrets: set[str] = set()


def register_secrets(values: Iterable[str]) -> None:
    """Remember real secret values so they are redacted even in an unexpected shape."""
    for v in values:
        if v and len(v) >= 8:
            _known_secrets.add(v)


def redact(text: object) -> str:
    s = "" if text is None else str(text)
    for secret in _known_secrets:
        if secret in s:
            s = s.replace(secret, REDACTED)
    for pat in _PATTERNS:
        s = pat.sub(lambda m: (m.group(1) if m.groups() else "") + REDACTED, s)
    return s


def safe_error(exc: BaseException, limit: int = 300) -> str:
    return redact(f"{type(exc).__name__}: {exc}")[:limit]
