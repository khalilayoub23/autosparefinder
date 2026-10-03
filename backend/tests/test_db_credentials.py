"""
Script: tests/test_db_credentials.py
Purpose: Regression guard for FIXES_TRACKER #61 — the catalog DB password must come from the
         environment (db_dsn.py), never from a literal in tracked source. 54 files used to
         embed "postgresql://autospare:<password>@…".
Method: static scan of the source tree + behaviour of db_dsn and the car-parts.ie importer
        under a FAKE environment. No database connection, no real credential.
Last Updated: 2026-10-03
"""
from __future__ import annotations

import importlib
import os
import re
import sys

import pytest

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(BACKEND)
sys.path.insert(0, BACKEND)

# user:password@ inside a postgres URL where the password is a REAL-looking literal: 20+
# characters, not {var} / ${VAR} / %s / <…>. Short dev placeholders used as getenv fallbacks
# (e.g. "autospare:autospare@localhost") are deliberately out of scope — they are not secrets.
_LITERAL_CRED = re.compile(r"postgres(?:ql)?(?:\+\w+)?://[A-Za-z0-9_]+:(?![{$%<\[*:])[^\s@\"'{}$<>]{20,}@")
_SKIP_DIRS = {"__pycache__", "node_modules", ".git", "state", "uploads", "backups", "test_images", "data"}
_PLACEHOLDERS = ("user:pass@", "user:password@", "u:p@", "username:password@", ":password@", ":pass@", ":secret@", ":xxx@", ":changeme@")


def _source_files():
    for base in (BACKEND, os.path.join(REPO, "archive"), os.path.join(REPO, "deploy")):
        for root, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
            for f in files:
                if f.endswith((".py", ".sh", ".yml", ".yaml", ".js")):
                    yield os.path.join(root, f)


def test_no_literal_database_password_in_source():
    offenders = []
    for path in _source_files():
        try:
            text = open(path, encoding="utf-8", errors="ignore").read()
        except OSError:
            continue
        for m in _LITERAL_CRED.finditer(text):
            if any(p in m.group(0).lower() for p in _PLACEHOLDERS):
                continue
            offenders.append(os.path.relpath(path, REPO))
            break
    assert offenders == [], f"literal DB credential in source: {offenders}"


def test_every_rewritten_script_takes_the_password_from_configuration():
    using = [p for p in _source_files() if "{_DB_PW}" in open(p, encoding="utf-8", errors="ignore").read()
             and os.path.basename(p) not in ("db_dsn.py", "test_db_credentials.py")]   # docs / this test mention it
    assert len(using) >= 40          # 45 backend files (+9 under archive/, not mounted in the container)
    for p in using:
        text = open(p, encoding="utf-8", errors="ignore").read()
        in_backend = p.startswith(BACKEND)
        if in_backend:
            assert "from db_dsn import DB_PASSWORD as _DB_PW" in text, p
        else:
            assert '_os_dbpw.environ["DB_PASSWORD"]' in text, p


def _fresh_db_dsn(monkeypatch, **env):
    for k in ("DB_PASSWORD", "DATABASE_URL"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    import db_dsn
    return importlib.reload(db_dsn)


def test_db_dsn_derives_the_password_from_database_url(monkeypatch):
    m = _fresh_db_dsn(monkeypatch, DATABASE_URL="postgresql+asyncpg://autospare:fake%2Fpw-123@postgres_catalog:5432/autospare")
    assert m.DB_PASSWORD == "fake/pw-123" and m.db_password() == "fake/pw-123"
    assert m.catalog_dsn() == "postgresql://autospare:fake%2Fpw-123@postgres_catalog:5432/autospare"


def test_db_dsn_prefers_explicit_db_password(monkeypatch):
    m = _fresh_db_dsn(monkeypatch, DB_PASSWORD="host-side-pw", DATABASE_URL="postgresql://autospare:other@h/db")
    assert m.DB_PASSWORD == "host-side-pw"


def test_db_dsn_without_configuration_is_empty_not_a_built_in_default(monkeypatch):
    m = _fresh_db_dsn(monkeypatch)
    assert m.DB_PASSWORD == ""                      # no fallback secret baked into source
    with pytest.raises(RuntimeError):
        m.db_password()
    with pytest.raises(RuntimeError):
        m.catalog_dsn()


def test_car_parts_ie_importer_builds_its_dsn_from_configuration(monkeypatch):
    _fresh_db_dsn(monkeypatch, DATABASE_URL="postgresql+asyncpg://autospare:rotated-pw-456@postgres_catalog:5432/autospare")
    sys.path.insert(0, os.path.join(BACKEND, "importers"))
    sys.modules.pop("car_parts_ie_import_generic", None)
    mod = importlib.import_module("car_parts_ie_import_generic")
    try:
        assert mod.DB_DSN == "postgresql://autospare:rotated-pw-456@postgres_catalog:5432/autospare"
        src = open(mod.__file__, encoding="utf-8").read()
        assert "from db_dsn import DB_PASSWORD as _DB_PW" in src and not _LITERAL_CRED.search(src)
    finally:
        sys.modules.pop("car_parts_ie_import_generic", None)


def test_configuration_errors_never_echo_the_connection_string(monkeypatch):
    # A URL that carries no password must fail WITHOUT quoting the URL (host/user) back.
    m = _fresh_db_dsn(monkeypatch, DATABASE_URL="postgresql+asyncpg://autospare@postgres_catalog:5432/autospare")
    assert m.DB_PASSWORD == ""
    with pytest.raises(RuntimeError) as exc:
        m.db_password()
    msg = str(exc.value)
    assert "://" not in msg and "@" not in msg and "postgres_catalog" not in msg


def test_db_dsn_module_never_prints_or_logs(monkeypatch, capsys):
    m = _fresh_db_dsn(monkeypatch, DATABASE_URL="postgresql+asyncpg://autospare:leak-canary-789@postgres_catalog:5432/autospare")
    m.db_password(); m.catalog_dsn()
    out = capsys.readouterr()
    assert "leak-canary-789" not in out.out + out.err
    src = open(m.__file__, encoding="utf-8").read()
    assert "print(" not in src and "logging" not in src and "logger" not in src


def test_the_live_credential_appears_in_no_source_file():
    # Uses whatever credential THIS process is really configured with (skips when none) and
    # reports file paths only — the value itself is never placed in an assertion message.
    import db_dsn
    try:
        live = importlib.reload(db_dsn).db_password()
    except RuntimeError:
        pytest.skip("no database credential configured in this environment")
    if len(live) < 12:
        pytest.skip("configured credential is a short dev placeholder")
    offenders = [os.path.relpath(p, REPO) for p in _source_files()
                 if live in open(p, encoding="utf-8", errors="ignore").read()]
    assert offenders == [], f"live DB credential found in {len(offenders)} source file(s): {offenders}"
