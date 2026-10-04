"""
Script: email_agent/config.py
Purpose: Email Agent configuration with three explicit states, so missing credentials produce a
         controlled idle state instead of a crash loop:
           disabled      EMAIL_AGENT_ENABLED != 1 (default)
           unconfigured  enabled, but a Gmail OAuth value is missing (names reported, never values)
           configured    enabled and all three OAuth values present
Process: values come from the environment only (docker-compose passes them from the gitignored
         .env, the same handling as the YouTube / Google Business OAuth values).
Data Imported/Modified: none.
Last Updated: 2026-10-04
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List, Mapping

from email_agent.redaction import register_secrets

DISABLED = "disabled"
UNCONFIGURED = "unconfigured"
CONFIGURED = "configured"

# Canonical business mailbox (CLAUDE.md: every connected Google resource belongs to it).
DEFAULT_MAILBOX = "autosparefinder2024@gmail.com"

SCOPE_READONLY = "https://www.googleapis.com/auth/gmail.readonly"
SCOPE_COMPOSE = "https://www.googleapis.com/auth/gmail.compose"
# Scopes that also satisfy read / draft if the grant happens to be broader.
_READ_SCOPES = {SCOPE_READONLY, "https://www.googleapis.com/auth/gmail.modify", "https://mail.google.com/"}
_DRAFT_SCOPES = {SCOPE_COMPOSE, "https://www.googleapis.com/auth/gmail.modify", "https://mail.google.com/"}

_REQUIRED = ("GMAIL_OAUTH_CLIENT_ID", "GMAIL_OAUTH_CLIENT_SECRET", "GMAIL_OAUTH_REFRESH_TOKEN")


def _int(env: Mapping[str, str], key: str, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(str(env.get(key, default)).strip())))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class EmailAgentConfig:
    enabled: bool = False
    client_id: str = ""
    client_secret: str = field(default="", repr=False)
    refresh_token: str = field(default="", repr=False)
    mailbox: str = DEFAULT_MAILBOX
    gmail_drafts: bool = False          # False = draft text is stored locally only
    query: str = "in:inbox newer_than:14d"
    max_per_cycle: int = 25
    interval_s: int = 600
    max_attempts: int = 3

    def missing(self) -> List[str]:
        vals = {"GMAIL_OAUTH_CLIENT_ID": self.client_id,
                "GMAIL_OAUTH_CLIENT_SECRET": self.client_secret,
                "GMAIL_OAUTH_REFRESH_TOKEN": self.refresh_token}
        return [k for k in _REQUIRED if not vals[k]]

    @property
    def state(self) -> str:
        if not self.enabled:
            return DISABLED
        return UNCONFIGURED if self.missing() else CONFIGURED

    def public(self) -> dict:
        """Safe to log / return from an endpoint: no secret values, only presence."""
        return {
            "state": self.state,
            "enabled": self.enabled,
            "missing": self.missing(),
            "mailbox": self.mailbox,
            "gmail_drafts": self.gmail_drafts,
            "query": self.query,
            "max_per_cycle": self.max_per_cycle,
            "interval_s": self.interval_s,
            "send_enabled": False,
        }


def load_config(env: Mapping[str, str] | None = None) -> EmailAgentConfig:
    env = os.environ if env is None else env
    g = lambda k, d="": (env.get(k, d) or "").strip()
    cfg = EmailAgentConfig(
        enabled=g("EMAIL_AGENT_ENABLED", "0") == "1",
        client_id=g("GMAIL_OAUTH_CLIENT_ID"),
        client_secret=g("GMAIL_OAUTH_CLIENT_SECRET"),
        refresh_token=g("GMAIL_OAUTH_REFRESH_TOKEN"),
        mailbox=(g("EMAIL_AGENT_MAILBOX") or DEFAULT_MAILBOX).lower(),
        gmail_drafts=g("EMAIL_AGENT_GMAIL_DRAFTS", "0") == "1",
        query=g("EMAIL_AGENT_QUERY") or "in:inbox newer_than:14d",
        max_per_cycle=_int(env, "EMAIL_AGENT_MAX_PER_CYCLE", 25, 1, 100),
        interval_s=_int(env, "EMAIL_AGENT_INTERVAL_S", 600, 60, 86400),
        max_attempts=_int(env, "EMAIL_AGENT_MAX_ATTEMPTS", 3, 1, 10),
    )
    register_secrets([cfg.client_secret, cfg.refresh_token])
    return cfg


def can_read(granted_scopes: set[str]) -> bool:
    return bool(granted_scopes & _READ_SCOPES)


def can_draft(granted_scopes: set[str]) -> bool:
    return bool(granted_scopes & _DRAFT_SCOPES)
