"""
Script: social/noa_fact.py
Purpose: Strict part matching for NOA's "real catalog fact" (root cause of mismatched post prices, 2026-11-13).
Process: the old lookup matched `name_he ILIKE '%<first word of the topic part>%'`, so "חיישן חמצן" (oxygen sensor)
         priced a "חיישן בקרת מהירות" (speed sensor), "מסנן מזגן" a fuel filter, "רפידות מגב" a brake-pad set at
         ₪1,999 - and a second query without the manufacturer put a Ford price in a Dacia post. Now EVERY significant
         word of the topic part must occur in the catalog name, and the manufacturer must match the car (no
         cross-make fallback: no fact is better than a wrong one).
Data Imported/Modified: none (pure SQL-fragment builder).
Last Updated: 2026-11-13
"""
from __future__ import annotations

import re
from typing import Dict, Tuple

_STOP = {"של", "עם", "for", "the", "and", "with"}


def _tokens(phrase: str) -> list[str]:
    phrase = re.sub(r"\([^)]*\)", " ", phrase or "")
    return [t for t in re.split(r"[\s/,\-]+", phrase.strip()) if len(t) >= 2 and t.lower() not in _STOP]


def name_conditions(heb_part: str, eng_part: str) -> Tuple[str, Dict[str, str]]:
    """SQL fragment (use as AND (...)) + params: all Hebrew tokens in name_he, OR all English tokens in name."""
    params: Dict[str, str] = {}
    groups = []
    for col, prefix, phrase in (("pc.name_he", "h", heb_part), ("pc.name", "e", eng_part)):
        toks = _tokens(phrase)
        if not toks:
            continue
        parts = []
        for i, t in enumerate(toks):
            params[f"{prefix}{i}"] = f"%{t}%"
            parts.append(f"{col} ILIKE :{prefix}{i}")
        groups.append("(" + " AND ".join(parts) + ")")
    if not groups:
        return "FALSE", params
    return "(" + " OR ".join(groups) + ")", params
