"""
Script: outbound_policy.py
Purpose: ONE rule for whether this process may send LIVE requests to external supplier sites/APIs
         (FIXES_TRACKER #100). Every supplier lookup that the site search, the search-miss resolver and the supplier
         routes start goes through services.supplier_aggregator, which asks this rule; the search fan-out asks it too.
Process:
  external_suppliers_enabled(env) -> (enabled, source):
    ENABLE_EXTERNAL_SUPPLIERS = 1/true/yes/on   -> enabled  (explicit)
    ENABLE_EXTERNAL_SUPPLIERS = 0/false/no/off  -> disabled (explicit)
    any other value                             -> disabled (unrecognised = fail closed, reported)
    unset or empty                              -> enabled ONLY when ENVIRONMENT=production, else disabled
  So production (ENVIRONMENT=production in docker-compose.yml, variable unset) behaves exactly as before, and a
  sandbox / test / unknown environment never reaches a supplier unless it opts in explicitly. Same pattern as
  night_pipeline.enabled().
Why: the shared sandbox had ENABLE_EXTERNAL_SUPPLIERS unset, so the old default ("1") made every site search there
  start a background lookup at real suppliers: 971 external requests logged 2026-10-10..22, most during test runs.
Data Imported/Modified: none.
Data Sources: environment variables ENABLE_EXTERNAL_SUPPLIERS, ENVIRONMENT.
Missing Data Delegation: n/a.
Last Updated: 2026-10-22
"""
from __future__ import annotations

import os
from typing import Mapping, Optional, Tuple

_ON = ("1", "true", "yes", "on")
_OFF = ("0", "false", "no", "off")
VAR = "ENABLE_EXTERNAL_SUPPLIERS"


def external_suppliers_enabled(env: Optional[Mapping[str, str]] = None) -> Tuple[bool, str]:
    """(enabled, source) for live external-supplier requests. Fails closed outside production."""
    env = os.environ if env is None else env
    raw = (env.get(VAR) or "").strip().lower()
    if raw in _ON:
        return True, f"{VAR}={raw}"
    if raw in _OFF:
        return False, f"{VAR}={raw}"
    if raw:
        return False, f"{VAR}={raw!r} unrecognised -> off"
    environment = (env.get("ENVIRONMENT") or "").strip().lower()
    if environment == "production":
        return True, "default (ENVIRONMENT=production)"
    return False, f"default off (ENVIRONMENT={environment or '<unset>'} is not production)"
