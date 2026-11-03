"""Script: app_paths.py
Purpose: ONE place that decides where application-owned files live, so no importer / harvester / scraper hard-codes a
         developer-host path (/opt/autosparefinder/...) that does not exist inside the container.
Process: APP_ROOT is the directory this module lives in (/app in the container, <repo>/backend on the host) - resolved from
         __file__, never from the working directory. state/ data/ uploads/ hang off it. An environment override keeps its
         name and meaning: an ABSOLUTE value is used as given (it must be visible INSIDE the container); a RELATIVE value is
         resolved against APP_ROOT (never against the cwd). A required input that is missing raises SourceMissing naming the
         importer and the resolved path - callers exit non-zero; they never report a successful import of nothing.
Data Imported/Modified: none (path arithmetic + existence checks only).
Data Sources: none.
Missing Data Delegation: n/a.
Last Updated: 2026-11-02
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, Optional

APP_ROOT: Path = Path(__file__).resolve().parent


class SourceMissing(FileNotFoundError):
    """A REQUIRED input file/directory is absent or unreadable. The message names the importer and the exact path."""


def _from_env_or(env_var: Optional[str], default: Path) -> Path:
    raw = os.environ.get(env_var) if env_var else None
    if raw:
        p = Path(raw)
        return p if p.is_absolute() else APP_ROOT / p
    return default


def state_dir() -> Path:
    """Persistent worker state (the worker_state volume: /app/state). Override: STATE_DIR."""
    return _from_env_or("STATE_DIR", APP_ROOT / "state")


def data_dir() -> Path:
    return APP_ROOT / "data"


def state_path(env_var: Optional[str], *parts: str, honor_state_dir: bool = False) -> Path:
    """A path under the persistent state volume (/app/state), overridable by `env_var`. `honor_state_dir=True` roots the default
    at STATE_DIR (modules that already honoured it); otherwise at <app root>/state exactly as the old hard-coded default."""
    return resolve(env_var, *parts, base=state_dir() if honor_state_dir else APP_ROOT / "state")


def uploads_dir() -> Path:
    return APP_ROOT / "uploads"


def resolve(env_var: Optional[str], *default_parts: str, base: Optional[Path] = None) -> Path:
    """Path from `env_var` when set, else APP_ROOT[/base]/<default_parts>. Pure arithmetic - no existence check."""
    return _from_env_or(env_var, (base or APP_ROOT).joinpath(*default_parts))


def cli_or(argv: list, index: int, env_var: Optional[str], *default_parts: str, base: Optional[Path] = None) -> Path:
    """argv[index] when given (relative values resolve against the cwd, as a shell user expects), else resolve(...)."""
    if len(argv) > index:
        return Path(argv[index])
    return resolve(env_var, *default_parts, base=base)


def require_file(path, importer: str) -> Path:
    """Return `path` if it is a readable file, else raise SourceMissing naming `importer` and the path."""
    p = Path(path)
    if not p.is_file():
        raise SourceMissing(f"{importer}: required source file not found: {p}")
    if not os.access(p, os.R_OK):
        raise SourceMissing(f"{importer}: required source file is not readable: {p}")
    return p


def require_dir(path, importer: str) -> Path:
    p = Path(path)
    if not p.is_dir():
        raise SourceMissing(f"{importer}: required source directory not found: {p}")
    return p


def split_present(paths: Iterable, importer: str, log=print) -> tuple[list[Path], list[Path]]:
    """For OPTIONAL sources: (present, skipped). Every skipped path is reported explicitly through `log`; a caller whose
    `present` is empty must treat the run as a failure, not as success."""
    present, skipped = [], []
    for raw in paths:
        p = Path(raw)
        (present if p.is_file() and os.access(p, os.R_OK) else skipped).append(p)
    for p in skipped:
        log(f"[{importer}] SKIPPED optional source (not found): {p}")
    return present, skipped


def require_any_or_exit(paths: Iterable, importer: str, log=print) -> list[Path]:
    """Guard for importers whose sources are individually optional: report every skipped path, and EXIT NON-ZERO when none
    exists - a run that processed no source must never end as a successful import."""
    present, skipped = split_present(paths, importer, log)
    if not present:
        raise SystemExit(f"ERROR: {importer}: none of the {len(skipped)} configured source file(s) exist; nothing was imported. "
                         f"Searched: " + ", ".join(str(p) for p in skipped))
    return present


def ensure_dir(path) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p
