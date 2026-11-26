"""#158 regression guard: a function must never rebind the name of one of its own nested functions.

Incident (2026-11-24 change, found 2026-11-26): `_health_monitor_loop` defines `async def _probe()` and called
`await _probe()` every 5 minutes; a later disk check in the SAME function assigned `_probe = _da.sample(...)`. From
the second cycle on every pass died with "'dict' object is not callable" (164 times in 14 h) and no health check
after that line ran - including critical alerts. The outer `except` printed one line and the loop carried on, so
nothing looked broken. This test scans the backend so the pattern cannot come back anywhere.
"""
import ast
import pathlib

BACKEND = pathlib.Path(__file__).resolve().parent.parent
SKIP_PARTS = {"tests", "legacy", "devtests", "node_modules", "__pycache__"}


def _scope_nodes(fn):
    """Nodes of fn's own scope (inner functions/classes/lambdas are not descended into)."""
    stack = list(ast.iter_child_nodes(fn))
    while stack:
        n = stack.pop()
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            yield n
            continue
        yield n
        stack.extend(ast.iter_child_nodes(n))


def rebinds(tree):
    out = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        nodes = list(_scope_nodes(fn))
        nested = {n.name: n.lineno for n in nodes if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        for n in nodes:
            targets = []
            if isinstance(n, ast.Assign):
                targets = n.targets
            elif isinstance(n, (ast.AugAssign, ast.AnnAssign, ast.For, ast.AsyncFor)):
                targets = [n.target]
            elif isinstance(n, ast.withitem) and n.optional_vars is not None:
                targets = [n.optional_vars]
            for t in targets:
                for name in ast.walk(t):
                    if isinstance(name, ast.Name) and name.id in nested:
                        out.append((fn.name, name.id, nested[name.id], name.lineno))
    return out


def test_detector_catches_the_incident_shape():
    src = (
        "async def loop():\n"
        "    async def _probe():\n"
        "        return {}\n"
        "    await _probe()\n"
        "    _probe = {'disk': 1}\n"
    )
    assert rebinds(ast.parse(src)) == [("loop", "_probe", 2, 5)]


def test_no_backend_function_rebinds_its_nested_function():
    found = []
    for p in BACKEND.rglob("*.py"):
        if SKIP_PARTS & set(p.relative_to(BACKEND).parts):
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        found += [(str(p.relative_to(BACKEND)),) + r for r in rebinds(tree)]
    assert found == [], f"nested function rebound in its own scope: {found}"
