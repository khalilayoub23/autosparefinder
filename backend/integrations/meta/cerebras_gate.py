"""
integrations/meta/cerebras_gate.py — Cerebras (LLM) usage gating for the social layer.

Phase 8 optimization: the LLM (Cerebras via hf_text) is expensive and subject to
429 rate-limits. This module is the single point that decides whether a given
social action needs LLM reasoning or can be handled deterministically.

Architecture:
    Cerebras (hf_text/hf_text_fast)
            ↑
     [cerebras_gate.py]  ← call needs_llm() before every hf_* call
            |
       Planner (NOA.generate_campaign_plan / generate_post)
            |
       Task Executor (NOA.execute_campaign)
            |
       Tools (social/tools.py)

Rules:
  USE Cerebras for:
    - Campaign plan generation (strategic multi-step reasoning)
    - Post content generation (creative, context-aware)
    - Analytics insight synthesis (interpret engagement patterns)
    - Group comment drafting (match language/tone of target post)
    - Complex routing decisions (inside RouterAgent)

  DO NOT use Cerebras for:
    - Scheduling / timing decisions → use Python datetime + env vars
    - API execution → call social/meta_client.py directly
    - DB reads/writes → call SQLAlchemy directly
    - Simple decisions with ≤3 branches → use Python if/else
    - Rate limit checks → use integrations/meta/rate_limiter.py
    - Signature verification → use integrations/meta/webhook_handler.py
    - List/filter/sort operations → use Python builtins
"""

from __future__ import annotations

LLM_REQUIRED_ACTIONS = frozenset({
    "generate_campaign_plan",
    "generate_post",
    "synthesise_analytics_insights",
    "draft_group_comment",
    "route_message",
})

LLM_FORBIDDEN_ACTIONS = frozenset({
    "schedule_post",
    "execute_api_call",
    "db_read",
    "db_write",
    "rate_limit_check",
    "signature_verify",
    "filter_list",
    "sort_results",
    "update_status",
})


def needs_llm(action: str) -> bool:
    """Return True if this action requires an LLM call.

    Raises ValueError if the action is explicitly forbidden (never use LLM for it).
    Unknown actions default to False (safe: assume no LLM needed).
    """
    if action in LLM_FORBIDDEN_ACTIONS:
        raise ValueError(
            f"action '{action}' must NOT use Cerebras — use a deterministic code path instead. "
            f"See integrations/meta/cerebras_gate.py."
        )
    return action in LLM_REQUIRED_ACTIONS


def llm_budget_context() -> dict:
    """Return the current LLM budget constraints for the social layer.

    Callers should log this when making LLM calls so the budget is traceable.
    """
    import os
    return {
        "batch_size":    int(os.getenv("CLEANUP_LLM_BATCH", "25")),
        "min_interval":  int(os.getenv("CLEANUP_LLM_MIN_INTERVAL_S", "180")),
        "daily_max":     int(os.getenv("CLEANUP_LLM_DAILY_MAX_CALLS", "150")),
        "model":         os.getenv("HF_MODEL", "cerebras/gpt-oss-120b"),
    }
