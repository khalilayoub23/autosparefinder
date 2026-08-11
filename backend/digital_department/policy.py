"""
Script: digital_department/policy.py
Purpose: Documents and enforces the explicit precedence model for Digital
         Department context relative to system safety rules.

         This module is also the single place to query whether dept context
         may safely be injected for a given combination of agent + operation.

         PRECEDENCE (highest to lowest):
           1. System safety rules (approval gates, forbidden actions, auth)
           2. Application business rules (pricing formula, VAT, DB invariants)
           3. Digital Department policies (brand, positioning, content rules)
           4. Task-specific campaign requirements
           5. LLM generation

         Digital Department policies NEVER override levels 1 or 2.

Data Imported/Modified: none
Last Updated: 2026-08-09
"""

from __future__ import annotations

# Operations where dept context MUST NOT be injected even if requested,
# because they touch the approval gate, DB state, or API dispatch.
_FORBIDDEN_OPERATIONS: frozenset[str] = frozenset({
    "execute_campaign_publish",   # Phase 2: publishing approved posts
    "approve_post",               # Approval gate mutation
    "reject_post",                # Approval gate mutation
    "facebook_group_publish",     # Browser-approval-gated
    "facebook_group_comment",     # Browser-approval-gated
    "run_tool",                   # Direct tool dispatch
    "db_write",                   # Any raw DB write
    "auth",                       # Auth checks
})

# Operations where dept context is appropriate (advisory for LLM generation).
_ALLOWED_OPERATIONS: frozenset[str] = frozenset({
    "generate_post",
    "generate_campaign_plan",
    "campaign_proposal",          # Intent detection: returning a proposal, no DB write
    "analytics_synthesis",
    "brief_generation",
    "process_customer_message",
})


def may_inject(operation: str) -> bool:
    """Return True if dept context injection is safe for the given operation.

    Fails closed: unknown operations are permitted if NOT in the forbidden set.
    This is safe because dept context is advisory text — it cannot execute code
    or bypass any safety check regardless of its content.
    """
    return operation not in _FORBIDDEN_OPERATIONS


def assert_not_forbidden(operation: str) -> None:
    """Raise ValueError if operation is in the forbidden set."""
    if operation in _FORBIDDEN_OPERATIONS:
        raise ValueError(
            f"Digital Department context must not be injected for operation "
            f"{operation!r} — this operation is in the safety-critical forbidden set."
        )


PRECEDENCE_SUMMARY = """\
Digital Department Context — Precedence Model
=============================================
1. System safety rules            [IMMUTABLE — approval gates, forbidden actions, auth]
2. Application business rules     [IMMUTABLE — pricing, VAT, DB invariants]
3. Digital Department policies    [ADVISORY — brand, positioning, content rules]
4. Task-specific requirements     [ADVISORY — per-campaign goals]
5. LLM generation                 [OUTPUT — subject to all layers above]

Dept context cannot: invoke tools, write DB, call APIs, publish content,
bypass approval gates, or override safety rules.

Security boundary note (L2, 2026-08-09):
  Department context is TEXT placed in the USER MESSAGE ROLE — it is
  advisory framing, not an API-level security boundary. The actual security
  boundaries are:
    * _TOOL_MAP = MappingProxyType(...)     — LLM cannot invoke unlisted tools
    * APPROVAL_REQUIRED = True              — no autonomous publishing
    * campaign state machine (social_posts) — DB-enforced state transitions
    * admin auth on campaign routes         — PATCH/execute require authentication
  Department files are owner-controlled source files, not user input.
  An adversarial string in dept context ("Ignore previous instructions")
  cannot: add entries to _TOOL_MAP, call approve_post(), run any tool, or
  bypass any DB-level check — all execution passes through deterministic
  Python code that ignores unprompted tool invocations.
"""
