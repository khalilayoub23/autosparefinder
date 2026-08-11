"""
Script: digital_department/__init__.py
Purpose: Public API for the Digital Department Runtime Integration layer.

         Exposes three entry points:
           build_context()              — assemble a dept context string for LLM injection
           build_prompt_with_context()  — prefix an LLM prompt with dept context
           get_registry()               — direct registry access for metadata queries

         This package is a KNOWLEDGE LAYER — it provides advisory policy
         text for SHIRA and NOA's LLM generation. It never executes
         tools, writes to the DB, calls external APIs, or bypasses any
         safety gate.

Data Imported/Modified: none (read-only)
Last Updated: 2026-08-09
"""

from .context import build_context, build_prompt_with_context
from .models import DepartmentContext
from .policy import may_inject, PRECEDENCE_SUMMARY
from .registry import get_registry

__all__ = [
    "build_context",
    "build_prompt_with_context",
    "DepartmentContext",
    "get_registry",
    "may_inject",
    "PRECEDENCE_SUMMARY",
]
