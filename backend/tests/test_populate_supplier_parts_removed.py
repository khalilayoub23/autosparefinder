"""`populate_supplier_parts` was removed (2026-11-07): it raised NameError (six undefined module constants since 2026-07-31) and its design
fabricated offer prices from `parts_catalog.base_price` for every supplier/part pair, which violates the price policy and the
offer-deduplication policy (an offer is a supplier's real commercial offer, never invented). These tests keep it gone and make
sure a registered task can never again reference undefined module-level names."""
import ast
import builtins
import asyncio
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SRC = (ROOT / "db_update_agent.py").read_text()
TREE = ast.parse(SRC)


def _module_names():
    names = set()
    for n in TREE.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(n.name)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                names.add((a.asname or a.name).split(".")[0])
        elif isinstance(n, (ast.Assign, ast.AnnAssign)):
            for tg in (n.targets if isinstance(n, ast.Assign) else [n.target]):
                for x in ast.walk(tg):
                    if isinstance(x, ast.Name):
                        names.add(x.id)
        elif isinstance(n, (ast.Try, ast.If, ast.With)):  # conditional imports / definitions at module level
            for x in ast.walk(n):
                if isinstance(x, (ast.Import, ast.ImportFrom)):
                    for a in x.names:
                        names.add((a.asname or a.name).split(".")[0])
                elif isinstance(x, ast.Name) and isinstance(x.ctx, ast.Store):
                    names.add(x.id)
                elif isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    names.add(x.name)
    return names


def test_populate_supplier_parts_is_not_registered_and_not_defined():
    assert "_populate_supplier_parts_task" not in {n.name for n in ast.walk(TREE) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert '"populate_supplier_parts"' not in SRC and "'populate_supplier_parts'" not in SRC
    for const in ("_UNIVERSAL_SUPPLIERS", "_MANUFACTURER_SUPPLIERS", "_CATEGORY_FALLBACK_ILS", "_WARRANTY_MAP", "_DEFAULT_PRICE"):
        assert const not in SRC


def test_run_task_answers_a_clean_error_for_the_removed_task():
    import db_update_agent as dua
    assert "populate_supplier_parts" not in dua.TASK_REGISTRY
    out = asyncio.run(dua.run_task("populate_supplier_parts", db=None))
    assert out["status"] == "error" and "Unknown task" in out["error"]  # never reaches a DB, never a NameError


def test_admin_api_answers_422_for_the_removed_task():
    try:
        from fastapi import HTTPException
        from routes import admin
    except Exception as exc:  # the admin module needs the full app environment
        pytest.skip(f"admin routes not importable here: {exc}")
    with pytest.raises(HTTPException) as e:
        asyncio.run(admin.db_agent_run_task("populate_supplier_parts", current_user=object(), db=None))
    assert e.value.status_code == 422


def test_no_registered_task_references_an_undefined_module_name():
    """Registry-wide guard: the class of defect that made populate_supplier_parts raise NameError (names deleted from the module,
    function left registered)."""
    mod = _module_names()
    reg = next(n for n in TREE.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "TASK_REGISTRY" for t in n.targets)
               or (isinstance(n, ast.AnnAssign) and getattr(n.target, "id", "") == "TASK_REGISTRY"))
    value = reg.value
    fn_names = {v.id for v in value.values if isinstance(v, ast.Name)}
    funcs = {n.name: n for n in TREE.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in fn_names}
    problems = {}
    for name, f in funcs.items():
        local = {a.arg for a in f.args.args + f.args.kwonlyargs}
        if f.args.vararg:
            local.add(f.args.vararg.arg)
        if f.args.kwarg:
            local.add(f.args.kwarg.arg)
        for x in ast.walk(f):
            if isinstance(x, ast.Name) and isinstance(x.ctx, (ast.Store, ast.Del)):
                local.add(x.id)
            elif isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                if not isinstance(x, ast.Lambda):
                    local.add(x.name)
                local |= {a.arg for a in x.args.args + x.args.kwonlyargs}
                if x.args.vararg:
                    local.add(x.args.vararg.arg)
                if x.args.kwarg:
                    local.add(x.args.kwarg.arg)
            elif isinstance(x, ast.ExceptHandler) and x.name:
                local.add(x.name)
            elif isinstance(x, (ast.Import, ast.ImportFrom)):
                for a in x.names:
                    local.add((a.asname or a.name).split(".")[0])
            elif isinstance(x, ast.Global):
                local |= set(x.names)
        used = {x.id for x in ast.walk(f) if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Load)}
        bad = sorted(n for n in used if n not in local and n not in mod and not hasattr(builtins, n) and n not in ("__file__", "__name__", "__doc__"))
        if bad:
            problems[name] = bad
    assert problems == {}, problems
