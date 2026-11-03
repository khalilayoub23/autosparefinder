"""sitecustomize.py — auto-imported by Python at interpreter startup (it's on sys.path
via PYTHONPATH=/app). Adds the reorganized script subfolders to sys.path so every bare
`import X` keeps resolving no matter which subfolder X.py was moved into. This is what
makes the flat-root → subfolder reorganization safe without rewriting hundreds of imports.

Runtime layout (2026-07-18 reorg):
  /app/                    core app + shared library modules (imported as packages/modules)
  /app/importers/          one-shot & scheduled catalog/price importers
  /app/harvesters/         site harvesters (car-parts.ie, amayama, champion, toyota, ...)
  /app/scrapers/           playwright / html scrapers
  /app/maintenance/        run_*/build_*/categorize_*/backfill_* pipeline & cleanup jobs
  /app/legacy/             superseded one-off scripts kept for reference
  /app/devtests/           ad-hoc test/debug harnesses (_*.py, test_*.py)
"""
import os, sys

_ROOT = os.path.dirname(os.path.abspath(__file__))
for _sub in ("importers", "harvesters", "scrapers", "maintenance", "legacy", "devtests"):
    _p = os.path.join(_ROOT, _sub)
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.append(_p)

# Runtime identity (2026-10-28): record the exact source bytes the process compiles for the watched modules
# (default agents.owner_console). Never allowed to break interpreter start-up.
try:
    if os.environ.get("RUNTIME_IDENTITY_DISABLED") != "1":
        if _ROOT not in sys.path:
            sys.path.insert(0, _ROOT)
        import runtime_identity as _runtime_identity
        _runtime_identity.install()
except Exception:
    pass
