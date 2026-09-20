"""
Script: eurosender_content.py
Purpose: Deterministic, mechanical guard for Eurosender's PackageRequest
         `content` field. Live Sandbox verification (2026-09-11, Phase 17)
         bisected the exact boundary: 16 characters -> no warning, 18
         characters -> `content-length` warning
         (parameterPath="parcels.packages[0].content"). The observed boundary
         is 17 characters (<=17 safe, >=18 warned). The API returned this as
         a WARNING alongside a still-successful 2xx quote, not a hard
         rejection — this module does not claim otherwise.
Process:
  resolve_package_content(item_content, category) tries, in order:
    1. the real item content (e.g. OrderItem.part_name), if it already fits;
    2. the category slug (already real, existing, short, non-misleading
       business data — "brakes", "filters", etc.), if IT fits;
    3. otherwise MANUAL_REVIEW — this function does NOT invent a truncation/
       abbreviation scheme. Mangling "BOSCH Brake Pad Set Front" down to 17
       characters produces a misleading customs/shipping description, and
       deciding what to keep (brand? position? part type?) is a business
       decision this code is not authorized to make (owner instruction,
       Phase 18 Phase 4). A category whose own slug exceeds 17 characters
       (e.g. "air-conditioning-heating", 25 chars) requires an explicit
       owner-approved short label before it can auto-ship via Eurosender —
       documented as an OWNER DECISION REQUIRED item, not guessed here.
Data Imported/Modified: none (pure function).
Data Sources: live Sandbox response, 2026-09-11 Phase 17 verification.
Missing Data Delegation: neither candidate fitting -> manual_review, never a
  fabricated/truncated string.
Last Updated: 2026-09-11
"""
from __future__ import annotations

from dataclasses import dataclass

# Verified live Sandbox boundary (Phase 17, 2026-09-11): 17 chars = safe,
# 18 chars = warning. Kept as a named constant, not inlined, so a future
# re-verification only needs to change one value.
EUROSENDER_CONTENT_MAX_LEN = 17


@dataclass(frozen=True)
class ContentResolution:
    ok: bool
    content: str | None
    reason: str


def resolve_package_content(
    item_content: str | None,
    category: str | None,
    max_len: int = EUROSENDER_CONTENT_MAX_LEN,
) -> ContentResolution:
    for candidate in (item_content, category):
        text = (candidate or "").strip()
        if text and len(text) <= max_len:
            return ContentResolution(
                ok=True,
                content=text,
                reason=f"{text!r} ({len(text)} chars) fits within the {max_len}-char Eurosender content limit.",
            )
    return ContentResolution(
        ok=False,
        content=None,
        reason=(
            f"Neither the item content ({item_content!r}) nor the category "
            f"({category!r}) fits within Eurosender's verified {max_len}-character "
            "content limit without truncation. Truncating either would risk a "
            "misleading customs/shipping description — that is a business "
            "decision requiring explicit owner approval (e.g. a designated "
            f"short label per category), not an automated guess."
        ),
    )
