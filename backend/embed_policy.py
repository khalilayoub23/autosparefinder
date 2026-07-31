"""
embed_policy.py — the Phase-1 / Phase-2 gate for embedding-driven categorization.

OWNER DIRECTIVE (2026-07-28): a CONTROLLED write path, not an open one.

  PHASE 1 (active now)
      • The model only looks at input types already proven reliable.
      • It CREATES CLASSIFICATION RULES — it does not write categories.
      • Bulk apply happens only after the owner approves the rule.

  PHASE 2 (later, per type)
      • Once enough history and validation data exist, auto-write may be enabled
        for the SPECIFIC types that have proven consistently accurate.

The point of this module is that Phase 2 must be EARNED FROM RECORDED EVIDENCE,
not switched on because someone believes the model is good enough. Every keyword
the model proposes is stamped with its input type, and every owner decision
(approve / reject) is therefore attributable to a type. `type_scorecard()` reads
that history back; `autowrite_enabled()` refuses until the bar is met.

PROMOTION BAR (all must hold for a given input type):
  1. the type is eligible in category_input_type.POLICY (measured precision);
  2. at least MIN_DECISIONS owner decisions recorded for that type;
  3. approval rate >= MIN_APPROVAL_RATE;
  4. the global kill-switch EMBED_AUTOWRITE_ENABLED is on;
  5. the type is named in EMBED_AUTOWRITE_TYPES.
Conditions 4 and 5 mean the owner still makes the final call — the evidence makes
promotion POSSIBLE, it never makes it automatic.

Data Modified: none (reads category_learned_keywords)
Last Updated:  2026-07-28
"""
from __future__ import annotations

import logging
import os
from typing import Dict, List

from sqlalchemy import text

import category_input_type as cit

logger = logging.getLogger("embed_policy")

# Evidence bar for promoting a type out of Phase 1.
MIN_DECISIONS = int(os.getenv("EMBED_PROMOTE_MIN_DECISIONS", "50"))
MIN_APPROVAL_RATE = float(os.getenv("EMBED_PROMOTE_MIN_APPROVAL", "0.90"))


def _enabled_types() -> List[str]:
    raw = os.getenv("EMBED_AUTOWRITE_TYPES", "").strip()
    return [t.strip() for t in raw.split(",") if t.strip()]


def global_autowrite_on() -> bool:
    return os.getenv("EMBED_AUTOWRITE_ENABLED", "0").strip().lower() in ("1", "true", "yes")


async def type_scorecard(db) -> Dict[str, Dict]:
    """
    Owner-decision history per input type — the Phase-2 evidence.

    approved / rejected are counted from the keywords the embedding assist
    proposed, grouped by the input type of the parts that produced them.
    """
    rows = (await db.execute(text("""
        SELECT COALESCE(input_type, 'unknown') AS ty,
               status,
               COUNT(*) AS n
        FROM category_learned_keywords
        WHERE source = 'embed_assist'
        GROUP BY 1, 2
    """))).fetchall()

    out: Dict[str, Dict] = {}
    for ty, status, n in rows:
        d = out.setdefault(ty, {"approved": 0, "rejected": 0, "pending": 0})
        if status in d:
            d[status] += n

    for ty, d in out.items():
        decided = d["approved"] + d["rejected"]
        d["decisions"] = decided
        d["approval_rate"] = (d["approved"] / decided) if decided else None
        d["eligible"] = cit.autowrite_eligible(ty)
        d["meets_bar"] = bool(
            d["eligible"]
            and decided >= MIN_DECISIONS
            and d["approval_rate"] is not None
            and d["approval_rate"] >= MIN_APPROVAL_RATE
        )
    return out


async def autowrite_enabled(db, input_type: str) -> bool:
    """
    True only if EVERY Phase-2 condition holds for this type.

    Deliberately conservative: any missing evidence, any switch off, and the
    answer is False. Phase 1 behaviour (propose -> owner approves -> bulk apply)
    is the default and stays correct forever if Phase 2 is never enabled.
    """
    if not global_autowrite_on():
        return False
    if input_type not in _enabled_types():
        return False
    if not cit.autowrite_eligible(input_type):
        logger.warning("autowrite requested for ineligible type %r — refused", input_type)
        return False
    card = (await type_scorecard(db)).get(input_type)
    if not card or not card["meets_bar"]:
        logger.info("autowrite for %r blocked: insufficient history (%s)",
                    input_type, card and card.get("decisions"))
        return False
    return True


def describe_phase() -> str:
    """One-line human summary for the owner console / logs."""
    if not global_autowrite_on():
        return ("PHASE 1 — the model proposes rules only; nothing is written to a "
                "part until you approve the rule.")
    types = _enabled_types()
    return (f"PHASE 2 — auto-write enabled for: {', '.join(types) or '(none)'}; "
            f"all other types remain proposal-only.")
