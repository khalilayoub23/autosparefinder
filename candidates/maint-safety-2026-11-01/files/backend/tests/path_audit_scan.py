"""Script: tests/path_audit_scan.py
Purpose: ONE scanner for hard-coded filesystem paths in executable code - used by the regression guard
         (test_importer_container_paths.py) and by the audit generator (candidates/.../evidence/path_audit.py), so the
         guard and the inventory can never disagree.
Process: AST-walk every .py under a backend root; collect string constants (and the literal parts of f-strings) that are
         ABSOLUTE filesystem paths (/opt/ /app/ /tmp/ /root/ /home/ /var/ /mnt/ /srv/), skipping docstrings and URLs. Each hit
         records file, line, path, whether the enclosing statement reads an environment variable (configurable) and the class.
Classes: BROKEN_IN_CONTAINER (/opt/autosparefinder/... default - the directory does not exist in the image), HOST_ONLY (a
         host-side operator tool or message), CONTAINER_COMPATIBLE (/app, /tmp, /root/.cache of the image),
         CONFIGURABLE_BUT_UNVERIFIED (env override on a non-container-verifiable default).
Data Imported/Modified: none (read-only).
Last Updated: 2026-11-02
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

ABS_RE = re.compile(r"^(/opt/|/app/|/tmp/|/root/|/home/|/var/|/mnt/|/srv/)[^\s]*")
SKIP_DIRS = {"__pycache__", "alembic", "alembic_pii", "node_modules", "legacy"}

# Host-side operator tools / operator-facing message text: the host path is the RIGHT one there (never run in the container).
HOST_ONLY_FILES = {
    "maintenance/gmail_oauth_setup.py": "operator tool: reads/writes the gitignored host .env + /root/.cache state; run on the host",
}
# /opt/autosparefinder/whatsapp-bridge/show_qr.sh: message text in BACKEND_API_ROUTES telling the operator which HOST script to run
HOST_ONLY_PATH_PREFIXES = ("/opt/autosparefinder/whatsapp-bridge/", "/opt/autosparefinder/.env", "/root/.cache/autosparefinder")


def _docstring_nodes(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.body:
            first = n.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                ids.add(id(first.value))
    return ids


def scan_file(path: Path, rel: str) -> list[dict]:
    try:
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src)
    except (SyntaxError, UnicodeDecodeError):
        return []
    parent: dict[int, ast.AST] = {}
    for n in ast.walk(tree):
        for c in ast.iter_child_nodes(n):
            parent[id(c)] = n
    docs = _docstring_nodes(tree)
    lines = src.splitlines()
    out = []
    for n in ast.walk(tree):
        if not (isinstance(n, ast.Constant) and isinstance(n.value, str)) or id(n) in docs:
            continue
        m = ABS_RE.match(n.value.strip())
        if not m or "://" in n.value:
            continue
        stmt: ast.AST = n
        while id(stmt) in parent and not isinstance(stmt, ast.stmt):
            stmt = parent[id(stmt)]
        seg = "\n".join(lines[stmt.lineno - 1:getattr(stmt, "end_lineno", stmt.lineno)])
        env = bool(re.search(r"getenv|environ", seg))
        p = m.group(0)
        out.append({"file": rel, "line": n.lineno, "path": p, "env_override": env, "stmt": lines[n.lineno - 1].strip()[:140]})
    return out


def classify(hit: dict) -> str:
    p, f = hit["path"], hit["file"]
    if f in HOST_ONLY_FILES or p.startswith(HOST_ONLY_PATH_PREFIXES):
        return "HOST_ONLY"
    if p.startswith("/opt/autosparefinder"):
        return "BROKEN_IN_CONTAINER"
    if p.startswith(("/home/", "/mnt/", "/srv/")):
        return "BROKEN_IN_CONTAINER"
    if p.startswith("/root/") and not p.startswith(("/root/.cache/ms-playwright", "/root/.cache/huggingface")):   # the image runs as root: its own caches
        return "BROKEN_IN_CONTAINER"
    return "CONTAINER_COMPATIBLE"


def scan_tree(root: Path, include_tests: bool = False) -> list[dict]:
    hits = []
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        parts = set(Path(rel).parts)
        if parts & SKIP_DIRS:
            continue
        if not include_tests and (Path(rel).parts[0] in ("tests", "devtests")):
            continue
        for h in scan_file(path, rel):
            h["class"] = classify(h)
            hits.append(h)
    return hits
