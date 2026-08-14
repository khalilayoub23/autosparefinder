"""
Script: digital_department/context.py
Purpose: Context builder — assembles a bounded, task-specific string of
         department policy for injection into an LLM prompt.

         2026-08-15 root-fix (replaces the 2026-08-14 positional patch, which
         only reordered one file and was proven — by a follow-up audit that
         actually inspected the live truncated output — to still silently
         drop the Truth-Only Guardrail on every call). The prior mechanism
         split the character budget EQUALLY across selected DEPARTMENTS, then
         truncated each department's raw text at a fixed character offset —
         survival depended entirely on what an editor happened to put first
         in the .md file. That is exactly what "positional" means, and it
         cannot be fixed by reordering content once; it silently breaks again
         the moment any section grows or a new department is added.

         The allocator now works PER SECTION, across ALL loaded departments
         at once, grouped by an explicit priority declared in the section
         itself (models.Section.priority — critical/high/normal/low, see
         models.py's `<!-- priority: X -->` marker convention). Allocation
         order is CRITICAL first (globally, across every department), then
         HIGH, then NORMAL, then LOW — a LOW section in the file's first
         paragraph is truncated before a CRITICAL section at the very end of
         a different department's file. Position no longer determines
         survival; declared importance does.

         Key constraints (updated):
           * Total context is still capped at MAX_TOTAL_CHARS (default 3 000
             chars) — see the "why characters, not tokens" note below.
           * Each priority TIER is allocated in full if it fits; only once a
             tier doesn't fit is its remaining budget split among that tier's
             sections and truncated — never before a higher tier is satisfied.
           * At most MAX_DEPARTMENTS_PER_CONTEXT departments per call.
           * Every allocation decision is recorded in a TruncationReport and
             logged (WARNING if anything critical was affected, INFO
             otherwise) — see build_context_with_report(). Nothing is ever
             silently dropped without a log line naming it.
           * If CRITICAL sections ALONE exceed the total budget, that is
             flagged explicitly (`report.critical_budget_exceeded = True`,
             logged at ERROR) rather than left to fail silently. The system
             still degrades gracefully (fail OPEN, matching post_guard.py's
             established policy in this codebase) rather than raising and
             blocking generation entirely — a slightly-truncated critical
             section reaching NOA beats no post at all; the ERROR log is what
             makes this an operational finding instead of an invisible one.

         Why characters, not tokens: this stack has no bundled tokenizer for
         the actual generation model (Cerebras gpt-oss-120b) — `tokenizers`
         (HF) is present as a transitive dependency for the local embedding
         model, not loaded with gpt-oss-120b's vocabulary, and fetching one
         would add a new network/model dependency for an approximation this
         module doesn't strictly need. Character count is used as an
         explicit, documented proxy: mixed Hebrew/English marketing copy
         averages roughly 3-4 characters per token, so a 3,000-character
         budget is on the order of 1,800-2,300 tokens — still small relative
         to gpt-oss-120b's actual context window (tens of thousands of
         tokens). The cap is therefore a deliberate PROMPT-ATTENTION-DILUTION
         choice (don't bury the actual task instructions under policy text),
         not a proxy for a real model limit.

         MAX_TOTAL_CHARS was raised 3,000 -> 7,500 on 2026-08-15, AFTER the
         priority mechanism above existed — not instead of it. Evidence, not
         a guess: with the real, current CRITICAL content across brand +
         positioning + context (Voice, both Truth-Only Guardrails, "What NOT
         to claim") measured at ~3,435 chars, the OLD 3,000 total (~2,458
         available after header/footer overhead) could not fit CRITICAL
         content alone even under the new priority-correct allocator —
         `critical_budget_exceeded` came back True. 4,500 was the minimum
         that fit all CRITICAL with zero truncation; 7,500 is where all
         CRITICAL *and* all HIGH content (positioning's differentiation
         facts, context's audience/product facts) fit in full, with LOW
         content (Colors/Typography/redundant pointers) correctly reduced to
         a few dozen characters each — exactly the intended shape: nothing
         important sacrificed, only decorative content thinned. Re-run the
         budget sweep in this module's tests if department content grows
         enough to reopen this question — don't hand-adjust the number
         without checking `critical_budget_exceeded` first.

Data Imported/Modified: none (read-only assembly)
Last Updated: 2026-08-15
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Optional

from .models import DepartmentContext, PRIORITY_LEVELS
from .registry import get_registry, AGENT_DEPARTMENTS, TASK_DEPARTMENTS, TASK_MAX_CHARS

log = logging.getLogger("digital_department.context")

# Hard budget — keeps the injected block from crowding out the actual prompt.
# See the "why characters, not tokens" note in the module docstring.
MAX_TOTAL_CHARS: int = 7_500
MAX_DEPARTMENTS_PER_CONTEXT: int = 5
MIN_CHARS_PER_DEPT: int = 150  # retained for the overhead-reservation calc below

_CONTEXT_HEADER = """\
=== Digital Department Guidelines (read-only, advisory) ===
Precedence: System Safety Rules > Application Business Rules > these guidelines > task instructions.
These guidelines supplement; they NEVER override safety rules, approval requirements, or DB/API invariants.
"""
_CONTEXT_FOOTER = "=== End Department Guidelines ==="


# ---------------------------------------------------------------------------
# Observability — every allocation decision is recorded, not just logged as
# a single total-length line. See requirement: "the system must know which
# sections survived and which were removed."
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SectionAllocation:
    department: str
    heading: str
    priority: str
    original_chars: int
    included_chars: int
    status: str  # "preserved" | "truncated" | "dropped"


@dataclass
class TruncationReport:
    allocations: List[SectionAllocation] = field(default_factory=list)
    critical_budget_exceeded: bool = False
    budget: int = 0
    total_chars_out: int = 0

    def by_status(self, status: str) -> List[SectionAllocation]:
        return [a for a in self.allocations if a.status == status]

    def survived(self, department: str, heading: str) -> bool:
        """True if this exact section is present in the output at all
        (preserved OR truncated — false only if fully dropped or never
        selected for this task in the first place)."""
        for a in self.allocations:
            if a.department == department and a.heading == heading:
                return a.status != "dropped"
        return False


def _allocate(
    loaded: List[DepartmentContext], budget: int
) -> tuple[dict[tuple[str, str], str], TruncationReport]:
    """Global, section-level, priority-tier allocation across every loaded
    department. Returns ({(department, heading): included_text}, report).

    Algorithm: walk PRIORITY_LEVELS in order (critical, high, normal, low).
    For each tier, gather every section at that priority from every loaded
    department. If they all fit in the remaining budget, include them in
    full. If not, split the remaining budget across that tier's sections
    (each still capped at its own real size) and mark the shortfall —
    remaining budget then drops to zero, so every LOWER tier is dropped
    entirely and explicitly logged as such. A higher tier is NEVER
    sacrificed to make room for a lower one, regardless of file position.
    """
    flat: list[tuple[str, "Section"]] = []  # noqa: F821 (Section imported via models below)
    for ctx in loaded:
        for sec in ctx.sections:
            flat.append((ctx.name, sec))

    remaining = budget
    included: dict[tuple[str, str], str] = {}
    allocations: list[SectionAllocation] = []
    critical_budget_exceeded = False

    for tier in PRIORITY_LEVELS:
        tier_sections = [(dn, s) for dn, s in flat if s.priority == tier]
        if not tier_sections:
            continue

        total_needed = sum(s.char_count for _, s in tier_sections)

        if remaining <= 0:
            # A higher tier already consumed the entire budget — everything
            # left, including this whole tier, is dropped. Still logged.
            for dn, s in tier_sections:
                allocations.append(SectionAllocation(dn, s.heading, tier, s.char_count, 0, "dropped"))
            continue

        if total_needed <= remaining:
            # Everything in this tier fits in full — the common, healthy case.
            for dn, s in tier_sections:
                included[(dn, s.heading)] = s.content
                allocations.append(SectionAllocation(dn, s.heading, tier, s.char_count, s.char_count, "preserved"))
            remaining -= total_needed
            continue

        # This tier does not fully fit — split what's left across its
        # sections and consume the rest of the budget. Every subsequent
        # (lower-priority) tier gets nothing, by construction.
        if tier == "critical":
            critical_budget_exceeded = True
        per_section = max(remaining // len(tier_sections), 0)
        for dn, s in tier_sections:
            take = min(per_section, s.char_count)
            if take <= 0:
                allocations.append(SectionAllocation(dn, s.heading, tier, s.char_count, 0, "dropped"))
                continue
            text = s.truncated(take)
            included[(dn, s.heading)] = text
            status = "preserved" if len(text) >= s.char_count else "truncated"
            allocations.append(SectionAllocation(dn, s.heading, tier, s.char_count, len(text), status))
        remaining = 0

    report = TruncationReport(
        allocations=allocations,
        critical_budget_exceeded=critical_budget_exceeded,
        budget=budget,
        total_chars_out=sum(len(t) for t in included.values()),
    )
    return included, report


def _log_report(report: TruncationReport, *, agent: Optional[str], task_type: Optional[str]) -> None:
    """Make truncation visible. Never silent — see module docstring.

    Log LEVEL reflects actual severity, not just "something changed":
    LOW/NORMAL content being trimmed under budget pressure is expected,
    routine, by-design behavior (that's the entire point of the priority
    tiers) — logging it at WARNING on every single call would be exactly
    the "flood production logs" failure mode a 2026-08-15b audit was asked
    to check for (real measurement: at current real department sizes, LOW
    content is trimmed on essentially every call). WARNING is reserved for
    when a CRITICAL or HIGH section — content someone explicitly said
    matters — was actually affected. ERROR is reserved for critical content
    not fitting at all. Full detail is always in the report/log regardless
    of level; only the SEVERITY is tiered.
    """
    if report.critical_budget_exceeded:
        bad_critical = [a for a in report.allocations if a.priority == "critical" and a.status != "preserved"]
        log.error(
            "digital_department: CRITICAL BUDGET EXCEEDED agent=%r task=%r budget=%d — "
            "critical content itself did not fully fit: %s",
            agent, task_type, report.budget,
            [(a.department, a.heading, a.status, a.included_chars, a.original_chars) for a in bad_critical],
        )
    affected = [a for a in report.allocations if a.status != "preserved"]
    important_affected = [a for a in affected if a.priority in ("critical", "high")]
    if important_affected:
        log.warning(
            "digital_department: %d CRITICAL/HIGH section(s) truncated/dropped agent=%r task=%r budget=%d: %s",
            len(important_affected), agent, task_type, report.budget,
            [(a.department, a.heading, a.priority, a.status, a.included_chars, a.original_chars) for a in important_affected],
        )
    elif affected:
        log.info(
            "digital_department: %d low/normal section(s) trimmed as designed agent=%r task=%r budget=%d: %s",
            len(affected), agent, task_type, report.budget,
            [(a.department, a.heading, a.priority, a.status, a.included_chars, a.original_chars) for a in affected],
        )
    else:
        log.info(
            "digital_department: all sections preserved in full agent=%r task=%r (%d/%d chars used)",
            agent, task_type, report.total_chars_out, report.budget,
        )


def build_context_with_report(
    *,
    agent: Optional[str] = None,
    task_type: Optional[str] = None,
    departments: Optional[List[str]] = None,
    max_chars: Optional[int] = None,
) -> tuple[str, TruncationReport]:
    """Same selection/assembly as build_context(), but also returns the
    TruncationReport instead of only logging it — this is what tests and any
    caller that wants to verify "did the critical section actually survive"
    programmatically should use. build_context() is a thin wrapper that
    discards the report after logging it, kept for backward compatibility
    with existing callers that expect a plain string.

    max_chars=None (the default) resolves via TASK_MAX_CHARS.get(task_type,
    MAX_TOTAL_CHARS) — see registry.py's TASK_MAX_CHARS for why some task
    types (more departments => more real HIGH/CRITICAL content) need a
    larger budget than the global default. Pass max_chars explicitly to
    override this resolution entirely (used by this module's own tests to
    sweep budget values).
    """
    if max_chars is None:
        max_chars = TASK_MAX_CHARS.get(task_type, MAX_TOTAL_CHARS) if task_type else MAX_TOTAL_CHARS

    registry = get_registry()

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

    dept_names = dept_names[:MAX_DEPARTMENTS_PER_CONTEXT]
    if not dept_names:
        return "", TruncationReport(budget=max_chars)

    loaded: list[DepartmentContext] = registry.get_many(dept_names)
    if not loaded:
        return "", TruncationReport(budget=max_chars)

    overhead = len(_CONTEXT_HEADER) + len(_CONTEXT_FOOTER) + len(loaded) * 80
    available = max(max_chars - overhead, MIN_CHARS_PER_DEPT)

    included, report = _allocate(loaded, available)
    _log_report(report, agent=agent, task_type=task_type)

    # Reassemble PER DEPARTMENT, in each department's original section order —
    # priority decided HOW MUCH survives, but the rendered text should still
    # read as a coherent document, not a priority-sorted jumble.
    sections: list[str] = [_CONTEXT_HEADER]
    for ctx in loaded:
        dept_body_parts = [
            included[(ctx.name, sec.heading)]
            for sec in ctx.sections
            if (ctx.name, sec.heading) in included
        ]
        if not dept_body_parts:
            continue  # every section of this department was dropped — log already covers why
        header = f"--- [{ctx.name.upper()}] (v{ctx.content_hash}) ---\n"
        sections.append(header + "\n".join(dept_body_parts))

    sections.append(_CONTEXT_FOOTER)
    result = "\n\n".join(sections)
    return result, report


def build_context(
    *,
    agent: Optional[str] = None,
    task_type: Optional[str] = None,
    departments: Optional[List[str]] = None,
    max_chars: Optional[int] = None,
) -> str:
    """Build an LLM-injectable context string from Digital Department guidelines.

    Selection priority (first that produces a non-empty list wins):
      1. Explicit `departments` list (caller knows exactly what they need)
      2. `task_type` mapping from TASK_DEPARTMENTS
      3. First N from `agent` mapping from AGENT_DEPARTMENTS (fallback)

    Content included is chosen by SECTION PRIORITY (critical > high > normal
    > low, declared per-section via `<!-- priority: X -->` markers in the
    source .md files — see models.py), not by physical position in the file
    or which department was listed first. Every allocation decision is
    logged — see build_context_with_report() to get the structured report
    directly instead of only the assembled string.

    Args:
        agent:       "shira" or "noa" — determines fallback department set
        task_type:   e.g. "social_post", "social_campaign" — primary selector
        departments: explicit list of department names (overrides both above)
        max_chars:   total character budget; None (default) resolves per
                     task_type via registry.TASK_MAX_CHARS, falling back to
                     MAX_TOTAL_CHARS — see build_context_with_report()

    Returns:
        Formatted context string ready for prompt injection, or "" if no
        departments load successfully (never raises).
    """
    text, _report = build_context_with_report(
        agent=agent, task_type=task_type, departments=departments, max_chars=max_chars
    )
    return text


def build_prompt_with_context(
    prompt: str,
    *,
    agent: Optional[str] = None,
    task_type: Optional[str] = None,
    departments: Optional[List[str]] = None,
    max_chars: Optional[int] = None,
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
    combined_chars = len(ctx) + len(prompt)
    log.debug(
        "build_prompt_with_context: dept_context=%d chars + caller_prompt=%d chars = %d combined "
        "(caller's system prompt, if any, is NOT visible to this module and is not counted here)",
        len(ctx), len(prompt), combined_chars,
    )
    return f"{ctx}\n\n{prompt}"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _agent_fallback(agent: Optional[str], registry) -> list[str]:
    if agent and agent in AGENT_DEPARTMENTS:
        return list(AGENT_DEPARTMENTS[agent])[:MAX_DEPARTMENTS_PER_CONTEXT]
    return []
