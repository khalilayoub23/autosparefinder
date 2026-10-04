"""
Script: email_agent/senders.py
Purpose: Facts about WHO sent an email, derived from the sender address and Gmail's own
         authentication verdict - not from the subject line.
Process:
  - org_domain(): registrable domain (handles co.il / co.uk style second-level suffixes).
  - sender_kind(): eurosender | ebay | aliexpress | payment | google | free_mail | business.
  - is_authenticated(): True only when Gmail's Authentication-Results header reports
    dmarc=pass, or an aligned dkim=pass. A From header alone is forgeable, so a platform /
    customer identity is trusted for context resolution only when this passes.
Data Imported/Modified: none.
Last Updated: 2026-10-04
"""
from __future__ import annotations

import re
from typing import Mapping

_TWO_LEVEL = {"co.il", "org.il", "net.il", "ac.il", "gov.il", "muni.il", "co.uk", "org.uk",
              "com.au", "com.tr", "com.cn", "com.hk", "co.jp", "com.br", "co.za", "com.sg"}

PLATFORM_DOMAINS = {
    "eurosender.com": "eurosender",
    "ebay.com": "ebay", "ebay.co.uk": "ebay", "ebay.de": "ebay", "ebay.ie": "ebay", "ebay.fr": "ebay",
    "ebay.it": "ebay", "ebay.es": "ebay",
    "aliexpress.com": "aliexpress", "alibaba.com": "aliexpress", "aliexpress.us": "aliexpress",
    "stripe.com": "payment", "paypal.com": "payment", "paypal.co.il": "payment",
    "google.com": "google",
}

FREE_MAIL = {"gmail.com", "googlemail.com", "hotmail.com", "outlook.com", "live.com", "yahoo.com",
             "walla.co.il", "walla.com", "icloud.com", "me.com", "proton.me", "protonmail.com",
             "012.net.il", "013.net", "bezeqint.net", "netvision.net.il", "aol.com", "msn.com",
             "yandex.com", "mail.ru", "gmx.com", "zoho.com"}

_AUTOMATED_LOCAL = re.compile(
    r"^(no[-_.]?reply|do[-_.]?not[-_.]?reply|noreply|mailer-daemon|postmaster|notifications?|"
    r"notify|alerts?|bounce[s]?|automated|system|info-noreply)([+\-_.].*)?$", re.I)


def org_domain(domain: str) -> str:
    labels = [l for l in (domain or "").lower().strip(".").split(".") if l]
    if len(labels) <= 2:
        return ".".join(labels)
    if ".".join(labels[-2:]) in _TWO_LEVEL:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def sender_kind(email_address: str) -> str:
    dom = org_domain(email_address.rsplit("@", 1)[-1]) if "@" in (email_address or "") else ""
    if not dom:
        return "unknown"
    if dom in PLATFORM_DOMAINS:
        return PLATFORM_DOMAINS[dom]
    if dom in FREE_MAIL:
        return "free_mail"
    return "business"


def is_automated_address(email_address: str) -> bool:
    local = (email_address or "").split("@", 1)[0]
    return bool(_AUTOMATED_LOCAL.match(local))


def is_authenticated(headers: Mapping[str, str], sender_email: str) -> bool:
    """Gmail stamps Authentication-Results on inbound mail. Absent header => not authenticated."""
    ar = (headers.get("authentication-results") or "").lower()
    if not ar or "@" not in (sender_email or ""):
        return False
    dom = org_domain(sender_email.rsplit("@", 1)[-1])
    if re.search(r"\bdmarc=pass\b", ar):
        m = re.search(r"header\.from=([\w.\-]+)", ar)
        return (not m) or org_domain(m.group(1)) == dom
    for m in re.finditer(r"\bdkim=pass\b[^;]*?header\.(?:i=@?|d=)([\w.\-]+)", ar):
        if org_domain(m.group(1).split("@")[-1]) == dom:
            return True
    return False
