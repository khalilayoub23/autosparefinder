"""Eurosender routing eligibility — the ONLY place this decision is made.

Double-gate, both required:
  1. EUROSENDER_ENABLED == true              (global kill switch)
  2. supplier UUID in EUROSENDER_SUPPLIER_ALLOWLIST

Deliberately does NOT gate on country (country == 'IE', country in EU, etc).
A country-based shortcut would silently route every EU supplier through an
unvalidated integration the moment the global flag flips on. Car-Parts.ie
(or any other supplier) only becomes eligible once its real UUID is added to
the allowlist by name — never inferred from country/region.
"""
from __future__ import annotations

from services.shipping import eurosender_config


def is_eurosender_eligible(supplier_id) -> bool:
    """Return True only if BOTH gates pass for this exact supplier UUID.

    supplier_id may be a uuid.UUID or a string; compared case-insensitively
    as a string against the allowlist.
    """
    if not eurosender_config.eurosender_enabled():
        return False
    if not supplier_id:
        return False
    allowlist = eurosender_config.supplier_allowlist()
    if not allowlist:
        return False
    return str(supplier_id).strip().lower() in allowlist
