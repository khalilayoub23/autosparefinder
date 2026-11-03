"""runtime_identity: prove which source bytes a process actually compiled for watched modules (2026-10-28)."""
import hashlib
import importlib
import json
import os
import py_compile
import subprocess
import sys
from pathlib import Path

import pytest

import runtime_identity as ri

BACKEND = Path(__file__).resolve().parent.parent
CHECK = BACKEND / "maintenance" / "runtime_identity_check.py"


@pytest.fixture
def pkg(tmp_path, monkeypatch):
    root = tmp_path / "src"
    (root / "watchpkg").mkdir(parents=True)
    (root / "watchpkg" / "__init__.py").write_text("")
    (root / "watchpkg" / "mod.py").write_text("VALUE = 1\n")
    (root / "watchpkg" / "other.py").write_text("VALUE = 2\n")
    monkeypatch.syspath_prepend(str(root))
    monkeypatch.setenv("RUNTIME_IDENTITY_MODULES", "watchpkg.mod")
    monkeypatch.setenv("RUNTIME_IDENTITY_DIR", str(tmp_path / "state"))
    for m in ("watchpkg", "watchpkg.mod", "watchpkg.other"):
        sys.modules.pop(m, None)
    ri._REGISTRY.clear()                                  # each test starts with no recorded loads
    ri.install()
    yield root, tmp_path / "state"
    for m in ("watchpkg", "watchpkg.mod", "watchpkg.other"):
        sys.modules.pop(m, None)


def _sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def test_records_exactly_the_compiled_bytes(pkg):
    root, state = pkg
    import watchpkg.mod as m
    rec = ri.loaded()["watchpkg.mod"]
    assert m.VALUE == 1 and rec["sha256"] == _sha(root / "watchpkg" / "mod.py") and rec["pid"] == os.getpid()
    files = list(state.glob("watchpkg.mod.*.json"))
    assert len(files) == 1 and json.loads(files[0].read_text())["sha256"] == rec["sha256"]


def test_stale_pyc_is_not_what_runs(pkg):
    root, _ = pkg
    src = root / "watchpkg" / "mod.py"
    py_compile.compile(str(src), cfile=importlib.util.cache_from_source(str(src)))   # a valid cache for VALUE=1
    st = src.stat()
    src.write_text("VALUE = 9\n")                       # same size, restore mtime: the cache would look valid
    os.utime(src, (st.st_atime, st.st_mtime))
    import watchpkg.mod as m
    assert m.VALUE == 9 and ri.loaded()["watchpkg.mod"]["sha256"] == _sha(src)


def test_unwatched_modules_import_normally(pkg):
    import watchpkg.other as o
    assert o.VALUE == 2 and "watchpkg.other" not in ri.loaded()


def test_reload_records_a_second_load(pkg):
    root, _ = pkg
    import watchpkg.mod as m
    (root / "watchpkg" / "mod.py").write_text("VALUE = 3\n")
    importlib.reload(m)
    rec = ri.loaded()["watchpkg.mod"]
    assert m.VALUE == 3 and rec["load_count"] == 2 and rec["sha256"] == _sha(root / "watchpkg" / "mod.py")


def _check(state, env_extra=None, *args):
    env = {**os.environ, "RUNTIME_IDENTITY_DIR": str(state), "RUNTIME_IDENTITY_MODULES": "watchpkg.mod",
           "RUNTIME_IDENTITY_DISABLED": "1", **(env_extra or {})}
    r = subprocess.run([sys.executable, str(CHECK), "--pid", str(os.getpid()), *args], capture_output=True,
                       text=True, env=env)
    return r.returncode, r.stdout


def test_checker_states(pkg):
    root, state = pkg
    assert _check(state)[1].startswith("watchpkg.mod: NOT_LOADED")
    import watchpkg.mod  # noqa: F401
    rc, out = _check(state)
    assert rc == 0 and "LOADED_MATCH" in out
    sha = ri.loaded()["watchpkg.mod"]["sha256"]
    rc, out = _check(state, None, "--expect", "watchpkg.mod=" + "0" * 64)
    assert rc == 1 and "LOADED_EXPECT_MISMATCH" in out
    assert _check(state, None, "--expect", f"watchpkg.mod={sha}")[0] == 0
    (root / "watchpkg" / "mod.py").write_text("VALUE = 5\n")              # disk changed after load
    rc, out = _check(state)
    assert rc == 1 and "LOADED_DISK_CHANGED" in out


def test_record_from_another_process_instance_is_not_accepted(pkg):
    root, state = pkg
    state.mkdir(parents=True, exist_ok=True)
    stale = {"module": "watchpkg.mod", "pid": os.getpid(), "process_start_ticks": "1", "boot_id": ri.boot_id(),
             "sha256": _sha(root / "watchpkg" / "mod.py"), "path": str(root / "watchpkg" / "mod.py"), "load_count": 1}
    (state / f"watchpkg.mod.{os.getpid()}.1.json").write_text(json.dumps(stale))
    rc, out = _check(state)
    assert rc == 1 and "NOT_LOADED" in out


def test_interpreter_start_installs_the_hook_and_never_breaks(tmp_path):
    env = {**os.environ, "PYTHONPATH": str(BACKEND), "RUNTIME_IDENTITY_DIR": str(tmp_path / "s"),
           "RUNTIME_IDENTITY_MODULES": "json.tool"}
    r = subprocess.run([sys.executable, "-c", "import runtime_identity as r, json.tool; print(r._INSTALLED, "
                        "'json.tool' in r.loaded())"], capture_output=True, text=True, env=env)
    assert r.stdout.strip() == "True True", r.stderr
    env["RUNTIME_IDENTITY_DIR"] = "/proc/forbidden"                        # unwritable: still starts, still imports
    r = subprocess.run([sys.executable, "-c", "import json.tool; print('ok')"], capture_output=True, text=True, env=env)
    assert r.stdout.strip() == "ok"


def test_webhooks_loads_owner_console_at_import_and_owner_console_is_side_effect_free():
    import ast
    src = (BACKEND / "routes" / "webhooks.py").read_text()
    assert "import agents.owner_console" in src
    tree = ast.parse((BACKEND / "agents" / "owner_console.py").read_text())
    assert all(isinstance(n, (ast.Import, ast.ImportFrom, ast.Assign, ast.FunctionDef, ast.AsyncFunctionDef, ast.Expr))
               for n in tree.body)
    for n in tree.body:
        if isinstance(n, ast.Expr):
            assert isinstance(n.value, ast.Constant)                    # docstring only
