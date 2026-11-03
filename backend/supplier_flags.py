"""
Script: supplier_flags.py
Purpose: ONE place that decides whether a supplier-lifecycle capability is switched on (owner task 2026-10-27,
         pre-deployment safety gate). Every capability added by the supplier-lifecycle candidate is OFF unless its
         variable is exactly "1": unset, empty, "true", "yes", " 1 ", malformed -> OFF. A deploy or an automatic
         restart therefore activates nothing by itself; each switch is a separate, explicit owner decision.
Process:
  enabled(name)   True only for os.environ[name] == "1" and name in FLAGS (an unknown name is a programming error)
  FLAGS           BOAZ_TASKS_ENABLED            BOAZ task loop (claims/escalates todos, WhatsApps the owner)
                  EMAIL_AGENT_SCAN_SPAM         Email Agent Spam/Junk pass (Gmail reads)
                  SUPPLIER_ROUTING_ENABLED      supplier-reply routing to NIR / BOAZ (writes tasks)
                  NIR_OUTREACH_ENABLED          NIR routine supplier inquiries (sends email)
                  SUPPLIER_AUTO_ORDER_ENABLED   automatic supplier ordering / payment connections
  The safety gates themselves (procurement gate before supplier spend, authorization checks) are NOT flags:
  they are always on.
Data Imported/Modified: none.
Last Updated: 2026-10-27
"""
from __future__ import annotations

import os
from typing import Mapping, Optional

FLAGS = ("BOAZ_TASKS_ENABLED", "EMAIL_AGENT_SCAN_SPAM", "SUPPLIER_ROUTING_ENABLED", "NIR_OUTREACH_ENABLED",
         "SUPPLIER_AUTO_ORDER_ENABLED")


def enabled(name: str, env: Optional[Mapping[str, str]] = None) -> bool:
    if name not in FLAGS:
        raise KeyError(f"unknown supplier-lifecycle flag {name!r}")
    env = os.environ if env is None else env
    return env.get(name) == "1"


def snapshot(env: Optional[Mapping[str, str]] = None) -> dict:
    return {n: enabled(n, env) for n in FLAGS}
