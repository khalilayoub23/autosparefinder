"""
Script: digital_department/context.py
Purpose: Context builder — assembles a bounded, task-specific string of
         department policy for injection into an LLM prompt.

         Key constraints:
           * Total context is capped at MAX_TOTAL_CHARS (default 3 000 chars).
           * Each department gets a proportional slice of the budget.
           * At most MAX_DEPARTMENTS_PER_CONTEXT departments per call.
           * Sections are selected by task type, never all 18 departments at once.
           * Source/version metadata is included in the header (no secrets).
           * The LLM cannot request departments by path — only by task/agent type.

Data Imported/Modified: none (read-only assembly)
Last Updated: 2026-08-09
"""

from __future__ import annotations

import logging
from typing import List, Optional

from .models import DepartmentContext
from .registry import get_registry, AGENT_DEPARTMENTS, TASK_DEPARTMENTS

log = logging.getLogger("digital_department.context")

# Hard budget — keeps the injected block from crowding out the actual prompt.
MAX_TOTAL_CHARS: int = 3_000
MAX_DEPARTMENTS_PER_CONTEXT: int = 5
MIN_CHARS_PER_DEPT: int = 150  # below this a dept excerpt is meaningless

_CONTEXT_HEADER = """\
=== Digital Department Guidelines (read-only, advisory) ===
Precedence: System Safety Rules > Application Business Rules > these guidelines > task instructions.
These guidelines supplement; they NEVER override safety rules, approval requirements, or DB/API invariants.
"""
_CONTEXT_FOOTER = "=== End Department Guidelines ==="


def build_context(
    *,
    agent: Optional[str] = None,
    task_type: Optional[str] = None,
    departments: Optional[List[str]] = None,
    max_chars: int = MAX_TOTAL_CHARS,
) -> str:
    """Build an LLM-injectable context string from Digital Department guidelines.

    Selection priority (first that produces a non-empty list wins):
      1. Explicit `departments` list (caller knows exactly what they need)
      2. `task_type` mapping from TASK_DEPARTMENTS
      3. First N from `agent` mapping from AGENT_DEPARTMENTS (fallback)

    Args:
        agent:       "shira" or "noa" — determines fallback department set
        task_type:   e.g. "social_post", "social_campaign" — primary selector
        departments: explicit list of department names (overrides both above)
        max_chars:   total character budget for the context block

    Returns:
        Formatted context string ready for prompt injection, or "" if no
        departments load successfully (never raises).
    """
    registry = get_registry()

    # -- Resolve the department list --
    if departments is not None:
        dept_names = [n for n in departments if n in registry.valid_department_names()]
    elif task_type is not None:
        try:
            dept_names = registry.departments_for_task(task_type)
        except ValueError:
            log.warning("build_context: unknown task_type=%r — falling back to agent", task_type)
            dept_names = _agent_fallback(agent, registry)
    else:
        dept_names = _agent_fallback(agent, registry)

    # Cap department count
    dept_names = dept_names[:MAX_DEPARTMENTS_PER_CONTEXT]
    if not dept_names:
        return ""

    # -- Load departments --
    loaded: list[DepartmentContext] = registry.get_many(dept_names)
    if not loaded:
        return ""

    # -- Budget allocation --
    # Reserve chars for header + footer + separators
    overhead = len(_CONTEXT_HEADER) + len(_CONTEXT_FOOTER) + len(loaded) * 80
    available = max(max_chars - overhead, MIN_CHARS_PER_DEPT * len(loaded))
    per_dept = max(available // len(loaded), MIN_CHARS_PER_DEPT)

    # -- Assemble --
    sections: list[str] = [_CONTEXT_HEADER]
    total_content_chars = 0

    for ctx in loaded:
        excerpt = ctx.truncated(per_dept)
        if not excerpt.strip():
            continue
        header = (
            f"--- [{ctx.name.upper()}] (v{ctx.content_hash}) ---\n"
            f"# {ctx.name.replace('_', ' ').title()}\n"
        )
        block = header + excerpt
        sections.append(block)
        total_content_chars += len(block)

    sections.append(_CONTEXT_FOOTER)
    result = "\n\n".join(sections)

    log.debug(
        "build_context: agent=%r task=%r depts=%r chars=%d",
        agent, task_type, [c.name for c in loaded], len(result),
    )
    return result


def build_prompt_with_context(
    prompt: str,
    *,
    agent: Optional[str] = None,
    task_type: Optional[str] = None,
    departments: Optional[List[str]] = None,
    max_chars: int = MAX_TOTAL_CHARS,
) -> str:
    """Return prompt prefixed with department context (if any).

    Keeps the context clearly separated so the LLM treats it as advisory,
    not as the main instruction. Returns the original prompt unmodified if
    the context is empty.
    """
    ctx = build_context(
        agent=agent,
        task_type=task_type,
        departments=departments,
        max_chars=max_chars,
    )
    if not ctx:
        return prompt
    return f"{ctx}\n\n{prompt}"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _agent_fallback(agent: Optional[str], registry) -> list[str]:
    if agent and agent in AGENT_DEPARTMENTS:
        return list(AGENT_DEPARTMENTS[agent])[:MAX_DEPARTMENTS_PER_CONTEXT]
    return []
