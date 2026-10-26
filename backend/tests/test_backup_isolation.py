"""
Script: tests/test_backup_isolation.py
Purpose: Regression guard for FIXES_TRACKER #66 — the sandbox backend (which bind-mounts the
         production backend/ directory) wrote its dumps into production's in-app backup
         directory under production's file name and its retention deleted production dumps.
         Also guards the retention over-match found with it ("autospare_*.sql" matched
         "autospare_pii_*.sql", so one database's retention counted the other's files).
Method: auto_backup reloaded under fake environments; temp directories; pg_dump replaced by a
        stub. No database, no real backup directory is touched.
Last Updated: 2026-10-04
"""
from __future__ import annotations

import asyncio
import importlib
import os
import re
import sys

import pytest

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(BACKEND)
sys.path.insert(0, BACKEND)

SANDBOX = {"SANDBOX_MODE": "1", "DATABASE_URL": "postgresql+asyncpg://u:p@sandbox_postgres:5432/autospare",
           "DATABASE_PII_URL": "postgresql+asyncpg://u:p@sandbox_postgres:5432/autospare_pii"}
PROD = {"ENVIRONMENT": "production", "DATABASE_URL": "postgresql+asyncpg://u:p@postgres_catalog:5432/autospare",
        "DATABASE_PII_URL": "postgresql+asyncpg://u:p@postgres_pii:5432/autospare_pii"}


def _load(monkeypatch, env, backup_dir=None):
    for k in ("SANDBOX_MODE", "ENVIRONMENT", "BACKUP_DIR", "DATABASE_URL", "DATABASE_PII_URL", "PII_DATABASE_URL"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    import auto_backup
    monkeypatch.setattr(os, "makedirs", lambda *a, **k: None)      # importing must not create real directories
    ab = importlib.reload(auto_backup)
    if backup_dir is not None:
        monkeypatch.setattr(ab, "BACKUP_DIR", str(backup_dir))
    return ab


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()
        asyncio.set_event_loop(asyncio.new_event_loop())


def _touch(d, *names):
    for n in names:
        (d / n).write_bytes(b"PGDMP" + b"x" * 64)


@pytest.fixture(autouse=True)
def _restore_module():
    yield
    import auto_backup
    importlib.reload(auto_backup)          # leave the module as the real environment defines it


# 1 + 7 — the two environments never resolve to the same directory, by default or by configuration
def test_sandbox_directory_is_never_the_production_directory(monkeypatch):
    prod = _load(monkeypatch, PROD)
    prod_dir = prod.BACKUP_DIR
    assert prod_dir == prod.PRODUCTION_BACKUP_DIR == os.path.join(BACKEND, "backups")
    sb = _load(monkeypatch, SANDBOX)
    assert sb.backup_environment() == "sandbox"
    assert os.path.realpath(sb.BACKUP_DIR) != os.path.realpath(prod_dir)
    assert sb.BACKUP_DIR == os.path.join(BACKEND, "state", "backups-sandbox")     # its own state volume


@pytest.mark.parametrize("forced", ["backups", "backups/", "backups/nested"])
def test_configuration_cannot_point_a_non_production_process_at_the_production_directory(monkeypatch, forced):
    sb = _load(monkeypatch, {**SANDBOX, "BACKUP_DIR": os.path.join(BACKEND, forced)})
    assert not sb._is_production_dir(sb.BACKUP_DIR)
    assert sb.BACKUP_DIR == os.path.join(BACKEND, "state", "backups-sandbox")


@pytest.mark.parametrize("env", [{}, {"ENVIRONMENT": "staging"}, {"ENVIRONMENT": "production", "SANDBOX_MODE": "1"},
                                 {"ENVIRONMENT": "Production ", "SANDBOX_MODE": "true"}])
def test_anything_that_is_not_plain_production_is_isolated(monkeypatch, env):
    ab = _load(monkeypatch, env)
    assert ab.backup_environment() != "production"
    assert not ab._is_production_dir(ab.BACKUP_DIR)
    assert ab.file_prefix("autospare") != "autospare"


def test_sandbox_run_refuses_if_it_is_somehow_pointed_at_the_production_directory(monkeypatch):
    sb = _load(monkeypatch, SANDBOX, backup_dir=os.path.join(BACKEND, "backups"))   # forced past the resolver
    called = []
    monkeypatch.setattr(sb, "_pg_dump", lambda url, out: called.append(out) or True)
    monkeypatch.setattr(sb, "_delete_backup", lambda p: called.append(("delete", p)))
    res = _run(sb.run_backup())
    assert called == [] and res["autospare"] == "skipped" and res["reason"] == "backup directory isolation"


# 4 + 5 — identity of the dumps each environment writes
def _fake_dump(_url, out_path):
    open(out_path, "wb").write(b"PGDMP" + b"x" * 64)
    return True


def test_sandbox_dump_carries_sandbox_identity_and_path(monkeypatch, tmp_path):
    sb = _load(monkeypatch, SANDBOX, backup_dir=tmp_path)
    monkeypatch.setattr(sb, "_pg_dump", _fake_dump)
    res = _run(sb.run_backup())
    names = sorted(n for n in os.listdir(tmp_path) if n.endswith(".sql"))
    assert len(names) == 2 and all(n.startswith("sandbox_autospare") for n in names)
    assert re.fullmatch(r"ok:.*/sandbox_autospare_\d{8}_\d{6}\.sql:\w+", res["autospare"])
    assert not any(re.match(r"autospare(_pii)?_\d{8}", n) for n in names)          # never production's name


def test_production_dump_keeps_production_identity_and_path(monkeypatch, tmp_path):
    prod = _load(monkeypatch, PROD, backup_dir=tmp_path)
    monkeypatch.setattr(prod, "_pg_dump", _fake_dump)
    res = _run(prod.run_backup())
    assert re.fullmatch(r"ok:.*/autospare_\d{8}_\d{6}\.sql:\w+", res["autospare"])
    assert re.fullmatch(r"ok:.*/autospare_pii_\d{8}_\d{6}\.sql:\w+", res["autospare_pii"])
    assert not any(n.startswith("sandbox_") for n in os.listdir(tmp_path))


MIXED = ["autospare_20261001_220000.sql", "autospare_20261002_220000.sql", "autospare_20261003_220000.sql",
         "autospare_pii_20261001_220000.sql", "autospare_pii_20261002_220000.sql", "autospare_pii_20261003_220000.sql",
         "sandbox_autospare_20261001_132300.sql", "sandbox_autospare_20261002_132300.sql",
         "sandbox_autospare_20261003_132300.sql", "sandbox_autospare_pii_20261003_132300.sql"]


# 2 + 6 — sandbox retention cannot select, and pruning cannot delete, production files
def test_sandbox_retention_selects_only_sandbox_files_and_leaves_production_untouched(monkeypatch, tmp_path):
    _touch(tmp_path, *MIXED)                         # worst case: everything in ONE directory
    sb = _load(monkeypatch, SANDBOX, backup_dir=tmp_path)
    assert [os.path.basename(f) for f in sb.backup_files("autospare")] == MIXED[6:9]
    sb._prune_old_backups_smart("autospare")
    sb._prune_old_backups_smart("autospare_pii")
    left = sorted(os.listdir(tmp_path))
    assert all(n in left for n in MIXED[:6])         # every production dump still there
    assert "sandbox_autospare_20261001_132300.sql" not in left and len([n for n in left if n.startswith("sandbox_autospare_2")]) == 2


# 3 — production retention cannot select sandbox files, nor another database's files
def test_production_retention_is_scoped_to_one_database_and_ignores_sandbox_files(monkeypatch, tmp_path):
    _touch(tmp_path, *MIXED)
    prod = _load(monkeypatch, PROD, backup_dir=tmp_path)
    assert [os.path.basename(f) for f in prod.backup_files("autospare")] == MIXED[0:3]       # no _pii, no sandbox_
    assert [os.path.basename(f) for f in prod.backup_files("autospare_pii")] == MIXED[3:6]
    prod._prune_old_backups_smart("autospare")
    left = sorted(os.listdir(tmp_path))
    assert "autospare_20261001_220000.sql" not in left                                       # oldest catalog pruned
    assert all(n in left for n in MIXED[1:3] + MIXED[3:])                                    # PII + sandbox untouched


def test_new_catalog_dump_survives_its_own_retention_when_pii_dumps_exist(monkeypatch, tmp_path):
    """The old glob counted the two newest PII dumps first and then deleted EVERY catalog dump,
    including the one just written."""
    _touch(tmp_path, "autospare_pii_20261003_220000.sql", "autospare_pii_20261004_220000.sql",
           "autospare_20261004_132339.sql")
    prod = _load(monkeypatch, PROD, backup_dir=tmp_path)
    monkeypatch.setattr(prod, "_pg_dump", _fake_dump)
    res = _run(prod.run_backup())
    path = res["autospare"].split(":")[1]
    assert os.path.isfile(path) and os.path.getsize(path) > 0


def test_restore_picks_this_database_not_a_lexically_later_one(monkeypatch, tmp_path):
    _touch(tmp_path, *MIXED)
    prod = _load(monkeypatch, PROD, backup_dir=tmp_path)
    newest = [f for f in reversed(prod.backup_files("autospare")) if f.endswith(".sql")][0]
    assert os.path.basename(newest) == "autospare_20261003_220000.sql"


# the removed 24-hour loop cannot come back, and the sandbox never runs the night pipeline
def test_no_self_scheduling_backup_loop_exists_or_is_registered():
    src = open(os.path.join(BACKEND, "auto_backup.py"), encoding="utf-8").read()
    assert not re.search(r"^(async )?def \w*loop\w*\(", src, re.M)
    assert "asyncio.sleep(" not in src and "while True" not in src
    routes = open(os.path.join(BACKEND, "BACKEND_API_ROUTES.py"), encoding="utf-8").read()
    assert not re.search(r"_supervised_task\(\s*[\"']auto_backup", routes)
    assert not re.search(r"^\s*(from auto_backup import|import auto_backup)", routes, re.M)
    callers = []
    for root, dirs, files in os.walk(BACKEND):
        dirs[:] = [d for d in dirs if d not in ("tests", "__pycache__", "state", "backups", "node_modules", "data", "uploads")]
        for f in files:
            if f.endswith(".py") and f != "auto_backup.py":
                if re.search(r"\brun_backup\(", open(os.path.join(root, f), encoding="utf-8", errors="ignore").read()):
                    callers.append(f)
    assert callers == ["night_pipeline.py"]          # the only automatic entry point


@pytest.mark.parametrize("env,expected", [({"SANDBOX_MODE": "1"}, False), ({"ENVIRONMENT": "sandbox", "SANDBOX_MODE": "1"}, False),
                                          ({"ENVIRONMENT": "production"}, True)])
def test_night_pipeline_runs_only_in_production(monkeypatch, env, expected):
    for k in ("ENVIRONMENT", "SANDBOX_MODE", "NIGHT_PIPELINE_ENABLED"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    import night_pipeline
    assert night_pipeline.enabled() is expected


def test_sandbox_compose_masks_the_production_backup_directory():
    path = os.path.join(REPO, "sandbox", "docker-compose.sandbox.yml")
    if not os.path.isfile(path):
        pytest.skip("sandbox compose file is not available in this environment (host-side test)")
    conf = re.sub(r"#[^\n]*", "", open(path, encoding="utf-8").read())
    assert re.search(r"-\s*\.\./backend:/app\b", conf)                      # the shared code mount (why this matters)
    assert re.search(r"-\s*sandbox_backups:/app/backups\b", conf)           # its own volume over the shared directory
    assert re.search(r"^\s*sandbox_backups:\s*$", conf, re.M)
    assert re.search(r"ENVIRONMENT:\s*sandbox\b", conf) and re.search(r'NIGHT_PIPELINE_ENABLED:\s*"0"', conf)
    assert re.search(r"BACKUP_DIR:\s*/app/state/backups-sandbox\b", conf)


if __name__ == "__main__":
    test_sandbox_compose_masks_the_production_backup_directory()
    print("PASS test_sandbox_compose_masks_the_production_backup_directory")
