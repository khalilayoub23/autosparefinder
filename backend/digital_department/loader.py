"""
Script: digital_department/loader.py
Purpose: Secure, cached loader for Digital Department skill content.
         Reads only explicitly registered .md files from the departments/
         subdirectory. The LLM NEVER supplies a file path — paths are
         computed internally from a strict allowlist of short department names.

Security model:
  - Input is a short name like "brand" or "positioning" — never a path.
  - The allowlist maps names → filenames; unlisted names are rejected.
  - Path is resolved with Path.resolve() and confirmed to stay within
    SKILLS_DIR — prevents any traversal via symlinks or "../" injection.
  - Content is treated as plain text; no code is executed.
  - Cache keyed on (mtime, size) invalidates automatically on file change.

Data Imported/Modified: none (read-only)
Last Updated: 2026-08-09
"""

from __future__ import annotations

import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, Tuple

from .models import DepartmentContext

# ---------------------------------------------------------------------------
# Allowlist — short name → filename inside departments/
# Design skills (dept-design-*) are intentionally excluded: they are
# gstack-runtime heavy and not relevant for social/marketing runtime context.
# dept-cmo is also excluded: it is an orchestrator for human sessions only.
# ---------------------------------------------------------------------------
_ALLOWLIST: Dict[str, str] = {
    "context":         "dept-context.md",
    "brand":           "dept-brand.md",
    "positioning":     "dept-positioning.md",
    "content":         "dept-content.md",
    "analytics":       "dept-analytics.md",
    "campaign_launch": "dept-campaign-launch.md",
    "competitor_intel":"dept-competitor-intel.md",
    "keyword_seo":     "dept-keyword-seo.md",
    "seo_programmatic":"dept-seo-programmatic.md",
    "seo_technical":   "dept-seo-technical.md",
    "market_research": "dept-market-research.md",
    "geo_content":     "dept-geo-content.md",
    "ppc":             "dept-ppc.md",
    "cro":             "dept-cro.md",
    "crm_email":       "dept-crm-email.md",
    "b2b_leads":       "dept-b2b-leads.md",
    "internal_comms":  "dept-internal-comms.md",
    "sop_library":     "dept-sop-library.md",
}

# Sections whose content is ONLY useful for a human-operated Claude Code
# session and should be stripped before injecting into an LLM prompt.
_STRIP_SECTIONS: frozenset[str] = frozenset({
    "when to use",
    "applying this skill",
    "output",
    "maintenance",
    "why this exists",
    "workflow",       # Claude Code step-by-step execution instructions
})

# Source attributions ("Adapted from ...") — one or two opening paragraphs
# that describe the gstack/skill source library. Strip these.
_ATTRIBUTION_RE = re.compile(
    r"^Adapted from[^\n]*(?:\n[^\n#][^\n]*)?\n",
    re.MULTILINE,
)

# YAML frontmatter block
_FRONTMATTER_RE = re.compile(r"^---\s*\n.*?\n---\s*\n", re.DOTALL)


class DepartmentLoader:
    """Thread-safe, mtime-invalidated file loader for department content."""

    SKILLS_DIR: Path = Path(__file__).resolve().parent / "departments"

    def __init__(self) -> None:
        # Cache: name → (mtime, size, DepartmentContext)
        self._cache: Dict[str, Tuple[float, int, DepartmentContext]] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def valid_names(self) -> frozenset[str]:
        return frozenset(_ALLOWLIST.keys())

    def load(self, name: str) -> DepartmentContext:
        """Load a department by canonical name. Raises KeyError if unknown."""
        if name not in _ALLOWLIST:
            raise KeyError(
                f"Unknown department {name!r}. "
                f"Valid: {sorted(_ALLOWLIST)}"
            )
        path = self._safe_path(name)
        stat = path.stat()
        cache_key = (stat.st_mtime, stat.st_size)

        with self._lock:
            cached = self._cache.get(name)
            if cached and cached[:2] == cache_key:
                return cached[2]

        # Load outside the lock — file I/O doesn't need it.
        raw = path.read_bytes()
        ctx = self._parse(name, path.name, raw, stat)

        with self._lock:
            self._cache[name] = (stat.st_mtime, stat.st_size, ctx)

        return ctx

    def load_many(self, names: list[str]) -> list[DepartmentContext]:
        """Load multiple departments. Silently skips missing files."""
        results = []
        for name in names:
            try:
                results.append(self.load(name))
            except (KeyError, FileNotFoundError, OSError):
                pass
        return results

    def version(self, name: str) -> str:
        """Return content hash for a department (loads if not cached)."""
        return self.load(name).content_hash

    def content_hash(self, name: str) -> str:
        return self.version(name)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _safe_path(self, name: str) -> Path:
        """Resolve and validate the path for a given department name."""
        filename = _ALLOWLIST[name]  # already validated above
        candidate = (self.SKILLS_DIR / filename).resolve()
        # Confirm the resolved path stays within SKILLS_DIR.
        try:
            candidate.relative_to(self.SKILLS_DIR.resolve())
        except ValueError:
            raise ValueError(
                f"Path traversal detected for department {name!r}"
            )
        return candidate

    def _parse(
        self,
        name: str,
        filename: str,
        raw: bytes,
        stat,
    ) -> DepartmentContext:
        text = raw.decode("utf-8", errors="replace")
        content_hash = DepartmentContext.compute_hash(raw)
        modified_at = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)

        # Extract description from YAML frontmatter
        description = _extract_frontmatter_description(text)

        # Strip frontmatter, then sanitize for runtime use
        text = _FRONTMATTER_RE.sub("", text, count=1)
        text = _ATTRIBUTION_RE.sub("", text)
        text = _strip_sections(text, _STRIP_SECTIONS)
        text = text.strip()

        return DepartmentContext(
            name=name,
            source_file=filename,
            description=description,
            content=text,
            content_hash=content_hash,
            modified_at=modified_at,
        )


# ---------------------------------------------------------------------------
# Parsing helpers (module-level for clarity, not coupled to instance state)
# ---------------------------------------------------------------------------

def _extract_frontmatter_description(text: str) -> str:
    """Pull the 'description:' value from YAML frontmatter, or return ''."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return ""
    fm = m.group(0)
    for line in fm.splitlines():
        if line.startswith("description:"):
            return line[len("description:"):].strip().strip('"').strip("'")
    return ""


def _strip_sections(text: str, strip_names: frozenset[str]) -> str:
    """Remove markdown sections whose ## heading matches any name in strip_names.

    Removes from the ## heading line up to (but not including) the next
    ## heading at the same or higher level. Case-insensitive match.
    """
    lines = text.splitlines(keepends=True)
    result: list[str] = []
    skip = False
    for line in lines:
        # Detect any ## heading (level 2 — the section level used in skills)
        if line.startswith("## "):
            heading = line[3:].strip().lower().rstrip("#").strip()
            skip = heading in strip_names
        if not skip:
            result.append(line)
    return "".join(result)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_loader: Optional[DepartmentLoader] = None
_loader_lock = threading.Lock()


def get_loader() -> DepartmentLoader:
    global _loader
    if _loader is None:
        with _loader_lock:
            if _loader is None:
                _loader = DepartmentLoader()
    return _loader
