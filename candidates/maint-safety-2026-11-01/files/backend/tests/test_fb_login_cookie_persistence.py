"""SP12 - the Facebook login cookie-writing path, executed for real (2026-11-02 state-path audit, final gap).

WHAT IS AND IS NOT VERIFIED. fb_browser_login.login() is the REAL code, run in REAL headless Chromium (fb_login_fixture_run.py). Facebook
is NOT contacted and NO account is used: every *.facebook.com request is answered locally by a Playwright route. In `authenticated` mode
the fixture serves a page with no login form and Set-Cookie c_user / xs / datr (values are marker strings, never real), which is exactly
what login()'s health check needs to take its cookie-writing branch; in `unauthenticated` mode it serves the login form. So this proves
the persistence behaviour of login() (path, content, overwrite/skip rules, permissions, consumers, failure messages) - it does NOT prove
that Facebook accepts a given account or that a 2FA/CAPTCHA flow completes; that stays an owner-supervised, live-only action.

Cookie VALUES never appear in assertions' messages, stdout, stderr or logs: the fixture marker is searched for in every captured stream."""
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent
MARKER = "FIXTURE-COOKIE-VALUE-DO-NOT-LOG"
try:                                               # a missing browser is an environment limit, reported as a skip, never a pass
    from playwright.sync_api import sync_playwright
    with sync_playwright() as _p:
        _have_chromium = os.path.exists(_p.chromium.executable_path)
except Exception:
    _have_chromium = False
needs_chromium = pytest.mark.skipif(not _have_chromium, reason="Playwright Chromium is not installed here")


def _env(state_dir, **extra):
    env = {k: v for k, v in os.environ.items() if k not in ("STATE_DIR", "FB_PROFILE_DIR", "FB_EMAIL", "FB_PASSWORD")}
    env.update(PYTHONPATH=str(APP), STATE_DIR=str(state_dir), HOME=os.environ.get("HOME", "/tmp"))
    env.update({k: str(v) for k, v in extra.items()})
    return env


def login_run(state_dir, mode, cwd="/", user=None):
    p = subprocess.run([sys.executable, "-W", "ignore", str(APP / "tests" / "fb_login_fixture_run.py"), str(APP), mode], cwd=cwd,
                       env=_env(state_dir), capture_output=True, text=True, timeout=180, user=user)
    assert MARKER not in p.stdout and MARKER not in p.stderr, "cookie VALUE leaked into a log/stdout stream"
    return json.loads([ln for ln in p.stdout.splitlines() if ln.startswith("{")][-1]), p


def reader_run(state_dir, user=None):
    p = subprocess.run([sys.executable, "-W", "ignore", str(APP / "tests" / "fb_session_reader_run.py"), str(APP)], cwd="/", env=_env(state_dir),
                       capture_output=True, text=True, timeout=120, user=user)
    assert p.returncode == 0, p.stderr[-1500:]
    assert MARKER not in p.stdout and MARKER not in p.stderr
    return json.loads(p.stdout.strip().splitlines()[-1])


@pytest.fixture
def state(tmp_path):
    d = Path(tempfile.mkdtemp(prefix="sp12_"))
    d.chmod(0o755)
    yield d
    import shutil
    shutil.rmtree(d, ignore_errors=True)


# ── the real cookie write ────────────────────────────────────────────────────
@needs_chromium
def test_login_writes_the_session_cookies_to_the_resolved_state_dir(state):
    out, p = login_run(state, "authenticated")
    f = state / "fb_browser_session" / "cookies.json"
    assert p.returncode == 0 and out["login_returned"] is True
    assert out["cookies_file"] == str(f) and f.is_file()
    assert out["names"] == ["c_user", "datr", "xs"] and out["count"] == 3
    data = json.loads(f.read_text(encoding="utf-8"))
    assert {"c_user", "xs"} <= {c["name"] for c in data} and all(c["domain"].endswith("facebook.com") for c in data)
    assert out["has_secret_marker"] is True                                 # the values ARE persisted (that is the point) ...
    assert "Cookie names: ['c_user', 'xs', 'datr']" in p.stderr             # ... and only NAMES are logged (the leak check ran in login_run)
    assert (f.stat().st_mode & 0o777) == 0o644 and not list(f.parent.glob("*.tmp"))


@needs_chromium
def test_repeat_login_overwrites_in_place_without_duplicates(state):
    login_run(state, "authenticated")
    f = state / "fb_browser_session" / "cookies.json"
    out, _ = login_run(state, "authenticated")
    assert out["count"] == 3 and out["names"] == ["c_user", "datr", "xs"]          # same cookies, not 6
    assert sorted(x.name for x in f.parent.iterdir() if x.suffix == ".json") == ["cookies.json"]


@needs_chromium
def test_failed_login_never_overwrites_the_last_known_good_session(state):
    login_run(state, "authenticated")
    f = state / "fb_browser_session" / "cookies.json"
    before = hashlib.sha256(f.read_bytes()).hexdigest()
    out, p = login_run(state, "unauthenticated")
    assert out["login_returned"] is False and hashlib.sha256(f.read_bytes()).hexdigest() == before
    assert "cookie write SKIPPED" in p.stderr


@needs_chromium
def test_failed_first_login_creates_no_cookie_file(state):
    out, _ = login_run(state, "unauthenticated")
    assert out["login_returned"] is False and out["exists"] is False


# ── consumers and a second reader ────────────────────────────────────────────
@needs_chromium
def test_the_session_consumers_read_the_file_login_wrote_from_a_different_process_and_cwd(state):
    login_run(state, "authenticated", cwd="/")
    r = reader_run(state)                                                   # fresh interpreter = what a second container does with the same volume
    assert r["same_dir"] is True and r["session_file"] == r["login_file"] == str(state / "fb_browser_session" / "cookies.json")
    assert r["session_names"] == r["import_tool_file_names"] == ["c_user", "datr", "xs"] and r["identical"] and r["authenticated_pair"]


@needs_chromium
@pytest.mark.skipif(os.getuid() != 0, reason="needs root to drop to another uid")
def test_a_non_root_runtime_user_can_read_the_persisted_session(state):
    login_run(state, "authenticated")
    for part in (state, state / "fb_browser_session"):
        part.chmod(0o755)
    r = reader_run(state, user=1000)
    assert r["uid"] == 1000 and r["authenticated_pair"] is True and r["session_names"] == ["c_user", "datr", "xs"]


# ── unusable session directory: a clear failure BEFORE anything runs ──────────
@pytest.mark.skipif(os.getuid() != 0, reason="needs root to drop to another uid")
def test_unwritable_session_dir_fails_up_front_with_the_path_and_the_uid(state):
    sd = state / "fb_browser_session"
    sd.mkdir()
    sd.chmod(0o555)                                                          # exists, readable, NOT writable by uid 1000
    out, p = login_run(state, "authenticated", user=1000)
    assert out["login_returned"] is False and out["exists"] is False
    assert f"{sd} is not writable by uid 1000" in p.stderr and "login not attempted" in p.stderr
    assert "Launching headed Chromium" not in p.stderr                       # failed BEFORE the browser/login started


def test_uncreatable_session_dir_fails_up_front_with_the_path_and_the_reason(tmp_path):
    blocker = tmp_path / "state_is_a_file"
    blocker.write_text("x")                                                  # STATE_DIR points below a regular file: mkdir cannot succeed
    out, p = login_run(blocker, "authenticated")
    assert out["login_returned"] is False and out["exists"] is False
    assert "cannot be created" in p.stderr and str(blocker / "fb_browser_session") in p.stderr and "login not attempted" in p.stderr
    assert "Traceback" not in p.stderr


# ── STATE_DIR is honoured by EVERY writer of the session directory ────────────
def _load(rel, name, env):
    old = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    try:
        spec = importlib.util.spec_from_file_location(name, str(APP / rel))
        m = importlib.util.module_from_spec(spec)
        sys.path.insert(0, str(APP))
        spec.loader.exec_module(m)
        return m
    finally:
        for k, v in old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


@pytest.mark.parametrize("rel,attr", [("fb_login_extended.py", "_STATE_DIR"), ("devtests/fb_mbasic_login.py", "_STATE"),
                                      ("social/facebook_browser/fb_browser_login.py", "_STATE_DIR"),
                                      ("social/facebook_browser/session.py", "_STATE_DIR"), ("maintenance/fb_cookie_import.py", "_STATE_DIR")])
def test_every_session_dir_writer_honours_state_dir_and_agrees_with_the_default(rel, attr, tmp_path):
    env = {"STATE_DIR": str(tmp_path / "sd"), "FB_EMAIL": "fixture@example.invalid", "FB_PASSWORD": "fixture"}
    assert Path(getattr(_load(rel, "w_" + attr + rel.replace("/", "_"), env), attr)) == tmp_path / "sd" / "fb_browser_session"
    no_override = subprocess.run([sys.executable, "-W", "ignore", "-c",
                                  "import importlib.util, os, sys; sys.path.insert(0, %r); os.environ.update(FB_EMAIL='f@example.invalid', FB_PASSWORD='x');"
                                  "s = importlib.util.spec_from_file_location('w', %r); m = importlib.util.module_from_spec(s); s.loader.exec_module(m);"
                                  "print(getattr(m, %r))" % (str(APP), str(APP / rel), attr)],
                                 cwd="/", env={k: v for k, v in os.environ.items() if k != "STATE_DIR"} | {"PYTHONPATH": str(APP)},
                                 capture_output=True, text=True, timeout=60)
    assert no_override.returncode == 0, no_override.stderr[-800:]
    assert no_override.stdout.strip() == str(APP / "state" / "fb_browser_session")      # default = <app root>/state, cwd-independent


def test_no_application_module_hardcodes_the_session_directory():
    """The directory is resolved ONLY through app_paths (STATE_DIR). A literal '/app/state/fb_browser_session' in code (not a comment/docstring)
    is a writer the override cannot move. devtests/ one-off diagnostics that only READ it are listed explicitly."""
    import ast
    read_only_diagnostics = {"devtests/phase5m_full_closure.py", "devtests/phase5n_reauth_test.py", "devtests/phase5l_full_scan.py",
                             "devtests/phase5i_31group_scan.py"}
    offenders = []
    for path in APP.rglob("*.py"):
        rel = path.relative_to(APP).as_posix()
        if rel.startswith(("tests/", "legacy/", "alembic")) or rel in read_only_diagnostics:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        doc_nodes = {id(n.body[0].value) for n in ast.walk(tree) if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                     and n.body and isinstance(n.body[0], ast.Expr) and isinstance(getattr(n.body[0], "value", None), ast.Constant)}
        for n in ast.walk(tree):
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in doc_nodes and "/app/state/fb_browser_session" in n.value \
                    and "log." not in n.value:
                offenders.append(f"{rel}:{n.lineno}")
    assert offenders == [], offenders
