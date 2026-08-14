"""
Script: digital_department/models.py
Purpose: Data model for a loaded department context — structured representation
         of a Digital Department skill's policy content, ready for LLM injection.

         2026-08-15 root-fix: content used to be one flat string, truncated by
         raw character position — whatever came FIRST in the .md file survived,
         regardless of importance (audit: BRAND's Voice/Truth-Only Guardrail
         were silently cut because Colors/Typography came first in the file).
         Content is now parsed into named ##-level Section objects, each
         carrying an explicit priority (critical/high/normal/low) from an
         optional `<!-- priority: LEVEL -->` marker directly under the heading.
         Allocation (context.py) now happens PER SECTION, grouped by priority
         tier across ALL loaded departments — not per department, not by
         position. A section's survival depends on its declared importance,
         not on where an editor happened to put it in the file.

Data Imported/Modified: none (read-only data container)
Last Updated: 2026-08-15
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Tuple

# ---------------------------------------------------------------------------
# Section priority
# ---------------------------------------------------------------------------
# Ordered CRITICAL -> LOW. This tuple IS the allocation order used by
# context.py's global allocator — critical sections across every loaded
# department are considered before any high section anywhere, etc.
PRIORITY_LEVELS: Tuple[str, ...] = ("critical", "high", "normal", "low")
DEFAULT_SECTION_PRIORITY = "normal"
DEFAULT_INTRO_PRIORITY = "low"  # the H1 title + lead-in paragraph before the first ## heading

_PRIORITY_MARKER_RE = re.compile(
    r"^<!--\s*priority:\s*(critical|high|normal|low)\s*-->\s*$", re.IGNORECASE
)


def _normalize_priority(value: str) -> str:
    v = (value or "").strip().lower()
    return v if v in PRIORITY_LEVELS else DEFAULT_SECTION_PRIORITY


@dataclass(frozen=True)
class Section:
    """One ##-level section of a department file (or the pre-heading intro
    block, heading="").

    Attributes:
        heading   Text of the "## " line, without the marker (e.g. "Voice").
                  "" for the intro block before the first ## heading.
        priority  One of PRIORITY_LEVELS — from an explicit `<!-- priority: X -->`
                  marker on the line directly under the heading, or a default
                  if none was given (DEFAULT_INTRO_PRIORITY for the intro
                  block, DEFAULT_SECTION_PRIORITY for an unmarked ## section).
        content   Full rendered text of the section, INCLUDING its own
                  "## Heading" line (so re-assembly needs no extra formatting),
                  with the priority marker line itself stripped out.
        char_count  len(content) — what the allocator budgets against.
    """

    heading: str
    priority: str
    content: str
    char_count: int = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "char_count", len(self.content))

    def truncated(self, max_chars: int) -> str:
        """This section's content, cut to max_chars at a paragraph/line boundary.
        Same boundary-seeking behavior as before, now scoped to one section
        instead of a whole department — a truncated section still reads as a
        complete thought as far as sentence/paragraph breaks allow."""
        if max_chars <= 0:
            return ""
        if len(self.content) <= max_chars:
            return self.content
        cut = self.content[:max_chars]
        last_para = cut.rfind("\n\n")
        if last_para > max_chars // 2:
            return cut[:last_para].rstrip()
        last_nl = cut.rfind("\n")
        if last_nl > max_chars // 2:
            return cut[:last_nl].rstrip()
        return cut.rstrip()


def parse_sections(text: str) -> Tuple[Section, ...]:
    """Split already-cleaned department text (frontmatter/attribution/
    Claude-Code-only sections already stripped by loader.py) into Section
    objects at each "## " heading, reading an optional priority marker on
    the line immediately below each heading.

    A department file with NO markers at all still works exactly as before —
    every section just defaults to "normal" (or "low" for the intro block),
    so this is backward compatible with every dept-*.md file that hasn't been
    annotated yet.

    Fence-aware (2026-08-15b full-system audit): a "## " line inside a
    ```-fenced code block is NOT a real heading — three real department
    files (b2b-leads, competitor-intel, market-research) contain example
    templates with a literal "## [Competitor Name]"-style line inside a
    code fence, which an earlier version of this parser would have split
    into a phantom section the moment those departments went live. Any
    line starting with ``` (with or without a language tag) toggles fence
    state; heading/marker detection is suspended while inside one.
    """
    lines = text.splitlines(keepends=True)
    sections: list[Section] = []

    heading = ""
    priority = DEFAULT_INTRO_PRIORITY
    buf: list[str] = []
    in_fence = False

    def flush() -> None:
        content = "".join(buf).rstrip("\n")
        if content.strip():
            sections.append(Section(heading=heading, priority=priority, content=content + "\n"))

    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            buf.append(line)
        elif not in_fence and line.startswith("## "):
            flush()
            heading = line[3:].strip().rstrip("#").strip()
            buf = [line]
            priority = DEFAULT_SECTION_PRIORITY
            if i + 1 < n:
                m = _PRIORITY_MARKER_RE.match(lines[i + 1].strip())
                if m:
                    priority = _normalize_priority(m.group(1))
                    i += 1  # consume the marker line — never shown to the LLM
        else:
            buf.append(line)
        i += 1
    flush()
    return tuple(sections)


@dataclass(frozen=True)
class DepartmentContext:
    """Immutable representation of one loaded department's policy content.

    Attributes:
        name          Short canonical name ("brand", "positioning", etc.)
        source_file   Filename inside departments/ that was loaded
        description   One-line description from the skill's frontmatter
        content       Full extracted policy text (frontmatter stripped) —
                      kept for callers that want the raw flat text; the
                      allocator (context.py) uses `.sections` instead.
        content_hash  SHA-256 hex digest of raw file bytes — changes when file changes
        modified_at   Last-modified timestamp of the source file
        char_count    Length of .content in characters
        sections      Parsed ##-level sections with explicit priority — see parse_sections()
    """

    name: str
    source_file: str
    description: str
    content: str
    content_hash: str
    modified_at: datetime
    char_count: int = field(init=False)
    sections: Tuple[Section, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "char_count", len(self.content))
        if not self.sections:
            object.__setattr__(self, "sections", parse_sections(self.content))

    @staticmethod
    def compute_hash(raw_bytes: bytes) -> str:
        return hashlib.sha256(raw_bytes).hexdigest()[:16]
