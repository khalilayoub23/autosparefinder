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
# Ordered by relevance — the context builder uses this order for budget allocation.
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

# ---------------------------------------------------------------------------
# Task type → relevant departments (task-specific, not agent-wide)
# Ordered by relevance — first entries get more of the context budget.
# ---------------------------------------------------------------------------
TASK_DEPARTMENTS: Dict[str, List[str]] = {
    "social_post":          ["brand", "positioning", "content"],
    "social_campaign":      ["brand", "positioning", "content", "campaign_launch", "analytics"],
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
