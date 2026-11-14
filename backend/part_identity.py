"""
Script:  part_identity.py
Purpose: Decide whether two catalogue rows that share a NORMALISED part number are provably different articles.
Process: A manufacturer OEM number is unique whatever its punctuation (06Q-103-495-H == 06Q103495H). An AFTERMARKET
         article number is only unique WITHIN ITS BRAND: REMSA 1187.02 (brake pads) and TOPRAN 118 702 (gear cable)
         collapse to the same string once dots/dashes/spaces are stripped. merge_master_parts merged such rows
         (2026-11-14 audit: REMSA pads into a TOPRAN cable, K2 brake fluid into a STEINHOF towbar), moving the
         loser's offers and fitment onto an unrelated master.
         Rule (conservative - uncertain = leave unchanged): two rows are DISTINCT when their raw numbers differ by
         more than case/spaces AND each row's name carries its own number with a different text in front of it
         (the brand). Identical raw numbers are never called distinct here (same article, differently titled).
Data Imported/Modified: none (pure functions).
Data Sources: none.
Missing Data Delegation: rows whose name does not contain their number are not classified (treated as mergeable).
Last Updated: 2026-11-14
"""
from __future__ import annotations

import re
from typing import Any, Mapping, Optional

_PREFIX = re.compile(r"^\s*article\s*(№|no\.?|#)?\s*:?\s*", re.I)


def raw_number(oem_number: Any) -> str:
    """The number as the source wrote it, minus the 'Article №:' label, case and spaces (punctuation KEPT)."""
    return re.sub(r"\s+", "", _PREFIX.sub("", str(oem_number or ""))).upper()


def brand_prefix(name: Any, oem_number: Any) -> Optional[str]:
    """Text in front of the row's own number inside its name ('REMSA 1187.02 Brake pad' -> 'REMSA'), else None."""
    n, num = str(name or ""), _PREFIX.sub("", str(oem_number or "")).strip()
    if not n or not num:
        return None
    i = n.upper().find(num.upper())
    if i <= 0:
        return None
    return re.sub(r"\s+", " ", n[:i]).strip(" ,-–|").upper() or None


def distinct_articles(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
    """True only when a and b are PROVABLY different aftermarket articles (see module docstring)."""
    if raw_number(a.get("oem_number")) == raw_number(b.get("oem_number")):
        return False
    pa, pb = brand_prefix(a.get("name"), a.get("oem_number")), brand_prefix(b.get("name"), b.get("oem_number"))
    return bool(pa and pb and pa != pb)
