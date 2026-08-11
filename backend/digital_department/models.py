"""
Script: digital_department/models.py
Purpose: Data model for a loaded department context — structured representation
         of a Digital Department skill's policy content, ready for LLM injection.
Data Imported/Modified: none (read-only data container)
Last Updated: 2026-08-09
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class DepartmentContext:
    """Immutable representation of one loaded department's policy content.

    Attributes:
        name          Short canonical name ("brand", "positioning", etc.)
        source_file   Filename inside departments/ that was loaded
        description   One-line description from the skill's frontmatter
        content       Full extracted policy text (frontmatter stripped)
        content_hash  SHA-256 hex digest of raw file bytes — changes when file changes
        modified_at   Last-modified timestamp of the source file
        char_count    Length of .content in characters
    """

    name: str
    source_file: str
    description: str
    content: str
    content_hash: str
    modified_at: datetime
    char_count: int = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "char_count", len(self.content))

    def truncated(self, max_chars: int) -> str:
        """Return content truncated to max_chars, breaking at a paragraph boundary."""
        if len(self.content) <= max_chars:
            return self.content
        cut = self.content[:max_chars]
        # Try to break at a paragraph or sentence boundary.
        last_para = cut.rfind("\n\n")
        if last_para > max_chars // 2:
            return cut[:last_para].rstrip()
        last_nl = cut.rfind("\n")
        if last_nl > max_chars // 2:
            return cut[:last_nl].rstrip()
        return cut.rstrip()

    @staticmethod
    def compute_hash(raw_bytes: bytes) -> str:
        return hashlib.sha256(raw_bytes).hexdigest()[:16]
