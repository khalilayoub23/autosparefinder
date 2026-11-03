"""app_paths: the one path-resolution helper (2026-11-02). No DB, no container needed - these run anywhere."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

import app_paths

BACKEND = Path(__file__).resolve().parent.parent


def test_app_root_is_where_the_module_lives_not_the_cwd(tmp_path):
    assert app_paths.APP_ROOT == BACKEND
    code = "import sys; sys.path.insert(0, %r); import app_paths; print(app_paths.APP_ROOT)" % str(BACKEND)
    for cwd in ("/", str(tmp_path)):
        out = subprocess.run([sys.executable, "-c", code], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()
        assert out == str(BACKEND)


def test_defaults_hang_off_the_app_root(monkeypatch):
    monkeypatch.delenv("STATE_DIR", raising=False)
    assert app_paths.state_dir() == BACKEND / "state"
    assert app_paths.data_dir() == BACKEND / "data"
    assert app_paths.uploads_dir() == BACKEND / "uploads"
    assert app_paths.resolve(None, "x.json") == BACKEND / "x.json"
    assert app_paths.resolve("NO_SUCH_VAR_XYZ", "x.json", base=app_paths.state_dir()) == BACKEND / "state" / "x.json"


def test_no_default_is_a_host_path(monkeypatch):
    monkeypatch.delenv("STATE_DIR", raising=False)
    for p in (app_paths.state_dir(), app_paths.data_dir(), app_paths.uploads_dir(), app_paths.resolve(None, "a")):
        assert not str(p).startswith("/opt/autosparefinder/") or str(BACKEND).startswith("/opt/autosparefinder/backend")


def test_env_override_absolute_is_used_as_given_relative_resolves_against_app_root(monkeypatch, tmp_path):
    monkeypatch.setenv("SOME_SRC", str(tmp_path / "abs.json"))
    assert app_paths.resolve("SOME_SRC", "default.json") == tmp_path / "abs.json"
    monkeypatch.setenv("SOME_SRC", "rel/dir.json")
    assert app_paths.resolve("SOME_SRC", "default.json") == BACKEND / "rel" / "dir.json"      # never the cwd
    monkeypatch.setenv("SOME_SRC", "")
    assert app_paths.resolve("SOME_SRC", "default.json") == BACKEND / "default.json"          # empty = unset
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    assert app_paths.state_dir() == tmp_path


def test_cli_argument_beats_env_and_default(monkeypatch, tmp_path):
    monkeypatch.setenv("SRC_ENV", str(tmp_path / "env.json"))
    assert app_paths.cli_or(["prog", "given.json"], 1, "SRC_ENV", "d.json") == Path("given.json")
    assert app_paths.cli_or(["prog"], 1, "SRC_ENV", "d.json") == tmp_path / "env.json"


def test_require_file_missing_names_the_importer_and_the_path(tmp_path):
    with pytest.raises(app_paths.SourceMissing) as e:
        app_paths.require_file(tmp_path / "nope.json", "my_importer")
    assert "my_importer" in str(e.value) and str(tmp_path / "nope.json") in str(e.value)
    assert isinstance(e.value, FileNotFoundError)
    with pytest.raises(app_paths.SourceMissing):
        app_paths.require_file(tmp_path, "my_importer")                      # a directory is not a source file


def test_require_file_unreadable(tmp_path):
    f = tmp_path / "locked.json"
    f.write_text("{}")
    f.chmod(0)
    try:
        if os.access(f, os.R_OK):
            pytest.skip("running as root: permission bits are not enforced")
        with pytest.raises(app_paths.SourceMissing, match="not readable"):
            app_paths.require_file(f, "my_importer")
    finally:
        f.chmod(0o600)


def test_require_dir(tmp_path):
    assert app_paths.require_dir(tmp_path, "i") == tmp_path
    with pytest.raises(app_paths.SourceMissing):
        app_paths.require_dir(tmp_path / "missing", "i")


def test_optional_sources_are_reported_not_swallowed(tmp_path):
    a, b = tmp_path / "a.xlsx", tmp_path / "b.xlsx"
    a.write_text("x")
    seen = []
    present, skipped = app_paths.split_present([a, b], "imp", log=seen.append)
    assert present == [a] and skipped == [b]
    assert len(seen) == 1 and "SKIPPED" in seen[0] and str(b) in seen[0] and "imp" in seen[0]


def test_nothing_processed_is_a_failure_not_a_success(tmp_path):
    seen = []
    with pytest.raises(SystemExit) as e:
        app_paths.require_any_or_exit([tmp_path / "a", tmp_path / "b"], "imp", log=seen.append)
    msg = str(e.value)
    assert msg.startswith("ERROR: imp") and str(tmp_path / "a") in msg and str(tmp_path / "b") in msg and len(seen) == 2
    (tmp_path / "a").write_text("x")
    assert app_paths.require_any_or_exit([tmp_path / "a", tmp_path / "b"], "imp", log=seen.append) == [tmp_path / "a"]


def test_paths_with_spaces_and_unicode(tmp_path, monkeypatch):
    d = tmp_path / "קבצים עם רווח" / "full car database"
    d.mkdir(parents=True)
    f = d / "מחירון 2026 ü.xlsx"
    f.write_text("x")
    assert app_paths.require_file(f, "i") == f
    monkeypatch.setenv("UNI_SRC", str(f))
    assert app_paths.resolve("UNI_SRC", "x") == f
    present, skipped = app_paths.split_present([f, d / "חסר.xlsx"], "i", log=lambda m: None)
    assert present == [f] and len(skipped) == 1


def test_ensure_dir_is_idempotent(tmp_path):
    p = tmp_path / "a" / "b"
    assert app_paths.ensure_dir(p) == p and app_paths.ensure_dir(p) == p and p.is_dir()
