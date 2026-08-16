"""
Script: digital_department/registry.py
Purpose: Central registry mapping agent names and task types to relevant
         department names. Provides the public get()/get_many() interface.

         The registry does NOT expose arbitrary file paths. The LLM never
         supplies department names directly — callers use the predefined
         agent/task mappings, and only explicitly registered names pass through.

Data Imported/Modified: none
Last Updated: 2026-08-09
"""

from __future__ import annotations

from typing import Dict, FrozenSet, List, Optional

from .loader import get_loader
from .models import DepartmentContext

# ---------------------------------------------------------------------------
# Agent → relevant departments
# Order here does NOT affect budget allocation (see the note on TASK_DEPARTMENTS
# below — this comment previously claimed otherwise and was itself stale as of
# 2026-08-15; corrected during the section-priority root-fix). Selection is by
# SECTION PRIORITY now (context.py), not department order or position.
# ---------------------------------------------------------------------------
AGENT_DEPARTMENTS: Dict[str, List[str]] = {
    "shira": [
        "brand", "positioning", "content", "campaign_launch",
        "competitor_intel", "market_research", "analytics",
        "ppc", "cro", "crm_email", "b2b_leads", "geo_content", "keyword_seo",
    ],
    "noa": [
        "brand", "positioning", "content", "campaign_launch",
        "analytics", "geo_content", "ppc", "cro", "competitor_intel",
    ],
}

# CONFIGURATION reachability vs. PRODUCTION reachability (2026-08-15d,
# updated in the Digital Department merge audit — was "exactly 4" as of the
# 13-department audit, now 5 after social/engagement.py's draft_reply_text()
# was deliberately wired in): being listed above is NOT proof a department
# ever reaches an LLM call. Verified by grepping every real call site of
# build_prompt_with_context()/build_context() in the whole backend (pinned by
# devtests/digital_department_integration_test.py::test_m4_..., which fails
# loudly if this goes stale): there are exactly 5 real call sites, ALL with
# agent="noa", ALL with an explicit task_type of "social_post",
# "social_campaign", or "social_reply". Two consequences still hold:
# (1) AGENT_DEPARTMENTS["shira"] above has ZERO real callers — MarketingAgent
# never calls this module; her brand/positioning grounding is a separate
# hand-folded block in her own system_prompt (see BACKEND_AI_AGENTS.py,
# "2026-07-27" comments). (2) The AGENT_DEPARTMENTS fallback path itself
# (used only when a caller omits task_type) is never exercised for "noa"
# either, since every real "noa" caller always passes an explicit task_type,
# which build_context_with_report() always prefers over the agent fallback.
# Do not treat dict membership here as evidence a department is live — check
# TASK_DEPARTMENTS's live task types (currently "social_post"/
# "social_campaign"/"social_reply") instead.
# ---------------------------------------------------------------------------
# Task type → relevant departments (task-specific, not agent-wide)
# Order does NOT affect budget or survival. context.py's allocator (2026-08-15
# root-fix) works at SECTION level across every loaded department, grouped by
# each section's declared `<!-- priority: critical|high|normal|low -->` marker
# (models.py) — a critical section in the department listed LAST here is
# still included before a low-priority section in the department listed
# FIRST. This list only decides WHICH departments are eligible to contribute
# sections at all, and their MAX_DEPARTMENTS_PER_CONTEXT cap order.
# ---------------------------------------------------------------------------
TASK_DEPARTMENTS: Dict[str, List[str]] = {
    # "content" dropped 2026-08-14 (owner: NOA's posts weren't getting more
    # attractive/selling from this connection) — dept-content.md is written for
    # long-form SEO blog articles ("How to find the right brake pads"), not
    # short social posts; it was crowding out genuinely relevant departments
    # for zero benefit.
    # "competitor_intel" dropped 2026-08-15 (follow-up audit): its content is
    # entirely a Claude-Code RESEARCH PROCEDURE ("use WebSearch/WebFetch,
    # extract patterns, compare") — NOA cannot execute those tools mid-generation,
    # so even a section that survived truncation delivered near-zero usable
    # value. Replaced with "context" (dept-context.md), which contains the
    # actual pre-computed facts NOA needs: verified core differentiator,
    # real B2C/B2B audience segments, and the specific "no loyalty/referral/
    # coupon program exists" guardrail that was previously reaching NOA
    # through no path at all (her own system_prompt has a general
    # anti-invention rule but never named these specific programs).
    "social_post":          ["positioning", "brand", "context"],
    "social_campaign":      ["positioning", "brand", "context", "campaign_launch", "analytics"],
    # social_reply (2026-08-15d merge audit): Community Engagement's
    # draft_reply_text() (social/engagement.py) previously called the LLM
    # with a standalone hardcoded system prompt, completely bypassing this
    # module — brand voice and the specific Truth-Only facts in dept-context
    # (e.g. "no loyalty/referral/coupon program exists") never reached it,
    # even though replies are branded as NOA the same way social_post is.
    # Deliberately NARROWER than social_post: positioning (differentiation
    # angle) and campaign_launch/analytics (campaign-planning guardrails) are
    # not relevant to a 1-2 sentence reactive reply — only brand (voice
    # consistency) and context (the Truth-Only facts a commenter could
    # plausibly ask about) are. Do not add more departments here just to
    # raise the connection count — see the same discipline applied to
    # social_post's own department list above.
    "social_reply":         ["brand", "context"],
    "campaign_analysis":    ["analytics", "content"],
    "b2b_campaign":         ["b2b_leads", "brand", "positioning", "content"],
    "ppc_campaign":         ["ppc", "brand", "positioning", "analytics"],
    "seo_campaign":         ["keyword_seo", "seo_programmatic", "seo_technical", "content", "analytics"],
    "geo_content":          ["geo_content", "brand", "positioning", "content"],
    "crm_campaign":         ["crm_email", "brand", "positioning", "content"],
    "cro":                  ["cro", "brand", "analytics"],
    "competitor_research":  ["competitor_intel", "positioning", "analytics"],
    "market_research":      ["market_research", "positioning", "analytics", "context"],
    # context added: foundational product facts (pricing model, target audience,
    # what NOT to claim) ground campaign proposals in reality.
    "campaign_proposal":    ["brand", "positioning", "campaign_launch", "content", "context"],
}

# ---------------------------------------------------------------------------
# Per-task-type character budget override (2026-08-15b full-system audit).
# context.MAX_TOTAL_CHARS is the DEFAULT for any task not listed here — it
# was measured against social_post's 3 departments specifically. A task
# pulling from MORE departments legitimately needs more room: social_campaign
# adds campaign_launch + analytics on top of social_post's 3, so the same
# global 7,500 left 4 of 6 real HIGH-priority sections truncated (40-70%
# each, not dropped, but genuinely incomplete). Swept the same way as the
# original default (find the minimum where all HIGH content fits in full):
# 7,500 -> high incomplete; 9,000 -> all HIGH fits. Not a guess.
#
# If a task here starts missing this override (or a new task type is added
# with more departments than social_post), `critical_budget_exceeded` /
# incomplete-HIGH will show up in the TruncationReport and get logged — this
# is observable, not silent, even before anyone re-runs the sweep.
# ---------------------------------------------------------------------------
TASK_MAX_CHARS: Dict[str, int] = {
    "social_campaign": 9_000,
}

# ---------------------------------------------------------------------------
# Departments intentionally present in the loader allowlist but NOT mapped to
# any task/agent — they are loadable on explicit request (departments=[...])
# but excluded from all automatic runtime injection because their content is
# Claude Code session tooling, not LLM generation guidance.
# ---------------------------------------------------------------------------
_INTENTIONALLY_UNMAPPED: frozenset[str] = frozenset({
    # Claude Code ~/.claude-marketing/sops/ file-management procedures —
    # not useful in an LLM generation prompt.
    "sop_library",
    # Internal status-report formatting (System Review tables, incident reports,
    # Monday brief format) — for human-readable owner reports, not SHIRA/NOA content.
    "internal_comms",
})

_VALID_AGENTS: FrozenSet[str] = frozenset(AGENT_DEPARTMENTS.keys())
_VALID_TASKS: FrozenSet[str] = frozenset(TASK_DEPARTMENTS.keys())


class DepartmentRegistry:
    """High-level access point for department contexts."""

    def __init__(self) -> None:
        self._loader = get_loader()

    # ------------------------------------------------------------------
    # Core access
    # ------------------------------------------------------------------

    def get(self, name: str) -> Optional[DepartmentContext]:
        """Load a single department by canonical name, or None on error."""
        try:
            return self._loader.load(name)
        except (KeyError, FileNotFoundError, OSError):
            return None

    def get_many(self, names: list[str]) -> list[DepartmentContext]:
        """Load multiple departments; skips unknown/missing silently."""
        return self._loader.load_many(names)

    def version(self, name: str) -> Optional[str]:
        """Content hash for a department, or None if unavailable."""
        ctx = self.get(name)
        return ctx.content_hash if ctx else None

    def content_hash(self, name: str) -> Optional[str]:
        return self.version(name)

    # ------------------------------------------------------------------
    # Mapping helpers
    # ------------------------------------------------------------------

    def departments_for_agent(self, agent: str) -> List[str]:
        """Return ordered department names relevant to the given agent."""
        if agent not in _VALID_AGENTS:
            raise ValueError(
                f"Unknown agent {agent!r}. Valid: {sorted(_VALID_AGENTS)}"
            )
        return list(AGENT_DEPARTMENTS[agent])

    def departments_for_task(self, task_type: str) -> List[str]:
        """Return ordered department names relevant to the given task type."""
        if task_type not in _VALID_TASKS:
            raise ValueError(
                f"Unknown task type {task_type!r}. Valid: {sorted(_VALID_TASKS)}"
            )
        return list(TASK_DEPARTMENTS[task_type])

    def valid_agents(self) -> FrozenSet[str]:
        return _VALID_AGENTS

    def valid_task_types(self) -> FrozenSet[str]:
        return _VALID_TASKS

    def valid_department_names(self) -> FrozenSet[str]:
        return self._loader.valid_names


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_registry: Optional[DepartmentRegistry] = None


def get_registry() -> DepartmentRegistry:
    global _registry
    if _registry is None:
        _registry = DepartmentRegistry()
    return _registry
