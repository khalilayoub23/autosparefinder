"""
Script: agent_identity.py
Purpose: ONE canonical identity per agent for agent_todos ownership (owner task 2026-10-27).
         Root cause it fixes: BOAZ looked up its todos as "pricing_agent" while every BOAZ todo was filed as
         "BOAZ" (agent_todo_utils matches assigned_to_agent EXACTLY), so BOAZ never saw a single task.
Process:
  canonical(name)   -> the canonical owner ("BOAZ", "NIR", ...), or the name unchanged when unknown
  aliases(name)     -> every historic spelling that means the same agent (lookups match all of them, so old
                       rows keep working without being duplicated or rewritten)
  New todos are filed under canonical(name) only.
Data Imported/Modified: none.
Last Updated: 2026-10-27
"""
from __future__ import annotations

from typing import Dict, FrozenSet, Tuple

# canonical -> historic spellings (class `name`, class `agent_name`, older todo owners)
_ALIASES: Dict[str, Tuple[str, ...]] = {
    "BOAZ": ("boaz", "Boaz", "pricing_agent", "supplier_manager_agent"),
    "NIR": ("nir", "Nir", "parts_finder_agent"),
    "LIOR": ("lior", "Lior", "orders_agent"),
    "DANA": ("dana", "Dana", "service_agent"),
    "TAL": ("tal", "Tal", "finance_agent"),
    "OREN": ("oren", "Oren", "security_agent"),
    "MAYA": ("maya", "Maya", "sales_agent"),
    "SHIRA": ("shira", "Shira", "marketing_agent"),
    "NOA": ("noa", "Noa", "social_media_manager_agent"),
    "EMAIL_AGENT": ("email_agent",),
}
_TO_CANONICAL: Dict[str, str] = {}
for _c, _names in _ALIASES.items():
    _TO_CANONICAL[_c] = _c
    for _n in _names:
        _TO_CANONICAL[_n] = _c


def canonical(name: str) -> str:
    return _TO_CANONICAL.get((name or "").strip(), (name or "").strip())


def aliases(name: str) -> FrozenSet[str]:
    c = canonical(name)
    return frozenset((c,) + _ALIASES.get(c, ()))
