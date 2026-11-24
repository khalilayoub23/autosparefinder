"""AST inventory of owner-notification call sites (shared by the test and the docs generator)."""
import ast
import collections
import os
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
NAMES = {"notify_owner", "_alert_owner", "notify_backlog", "resolve_owner_alert", "_notify", "_notify_res",
         "_wa_send_update", "notify"}
SKIP = ("tests", "devtests", "legacy", "node_modules", "candidates", "__pycache__")


def scan() -> dict:
    out = {}
    for root, _d, files in os.walk(BACKEND):
        rel_root = os.path.relpath(root, BACKEND)
        if any(part in SKIP for part in Path(rel_root).parts):
            continue
        for f in files:
            if not f.endswith(".py"):
                continue
            path = Path(root) / f
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            c = collections.Counter()
            for n in ast.walk(tree):
                if isinstance(n, ast.Call):
                    fn = n.func
                    nm = fn.id if isinstance(fn, ast.Name) else fn.attr if isinstance(fn, ast.Attribute) else None
                    if nm in NAMES:
                        c[nm] += 1
            if c:
                out[str(path.relative_to(BACKEND))] = dict(sorted(c.items()))
    return dict(sorted(out.items()))
