"""
Script: tests/test_docker_build_context.py
Purpose: Regression guard for FIXES_TRACKER #67 — the backend image is built with `COPY . .`
         from backend/ and had no .dockerignore, so runtime data was baked into the image:
         two 1.8 GB production catalog dumps under /app/backups (image of 2026-07-28), which
         Docker then copied into an empty volume mounted there.
Method: static. Checks backend/.dockerignore against the rules that keep database dumps,
        runtime state and secrets out of the build context, and checks every dump-like or
        secret-like file that exists in the context right now against those rules.
        The end-to-end check (a real `docker build` of the context) is host-side and is
        recorded in the tracker entry; it cannot run inside the container.
Last Updated: 2026-10-04
"""
from __future__ import annotations

import fnmatch
import os
import re

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IGNORE = os.path.join(BACKEND, ".dockerignore")

ARTIFACT = re.compile(r"\.(sql|sql\.gz|sql\.partial|sql\.meta|partial|dump|pgdump|pg_dump|backup|bak|sqlite3?|db)$")
SECRET = re.compile(r"(^\.env(\..+)?$|\.pem$|\.key$)")


def _rules():
    lines = [l.strip() for l in open(IGNORE, encoding="utf-8") if l.strip() and not l.lstrip().startswith("#")]
    return [l for l in lines if not l.startswith("!")], [l[1:] for l in lines if l.startswith("!")]


def _match(rel: str, pattern: str) -> bool:
    rel = rel.replace(os.sep, "/")
    if pattern.endswith("/"):                                   # directory rule
        d = pattern.rstrip("/")
        if d.startswith("**/"):
            return ("/" + d[3:] + "/") in ("/" + rel)
        return rel.startswith(d + "/")
    if pattern.startswith("**/"):
        return fnmatch.fnmatch(os.path.basename(rel), pattern[3:])
    if "**" in pattern:                                         # e.g. alembic/**/*.sql
        head, tail = pattern.split("/**/", 1)
        return rel.startswith(head + "/") and fnmatch.fnmatch(os.path.basename(rel), tail)
    return fnmatch.fnmatch(rel, pattern)


def _ignored(rel: str) -> bool:
    deny, allow = _rules()
    return any(_match(rel, p) for p in deny) and not any(_match(rel, p) for p in allow)


def test_backend_has_a_dockerignore_because_the_dockerfile_copies_everything():
    dockerfile = open(os.path.join(BACKEND, "Dockerfile"), encoding="utf-8").read()
    assert re.search(r"^COPY \. \.", dockerfile, re.M)          # the reason the guard is needed
    assert os.path.isfile(IGNORE)


def test_backup_and_dump_rules_are_present():
    deny, allow = _rules()
    for rule in ("backups/", "**/backups/", "state/", "**/*.sql", "**/*.sql.gz", "**/*.sql.partial",
                 "**/*.partial", "**/*.dump", "**/*.pgdump", "**/*.bak", ".env", ".env.*"):
        assert rule in deny, f"missing .dockerignore rule: {rule}"
    assert ".env.example" in allow
    assert not any(a.startswith(("backups", "state", "**/backups")) for a in allow)   # never re-included


def test_future_backup_names_from_every_environment_are_excluded():
    for rel in ("backups/autospare_20270101_010000.sql", "backups/autospare_pii_20270101_010000.sql",
                "backups/autospare_20270101_010000.sql.partial", "backups/autospare_20270101_010000.sql.meta",
                "backups/sandbox_autospare_20270101_132300.sql", "state/backups-sandbox/sandbox_autospare_20270101_132300.sql",
                "state/worker_state.json", "nested/backups/anything.bin", "stray_dump.sql", "data/export.sql.gz",
                "data/catalog.pgdump", "old.bak", ".env", ".env.production"):
        assert _ignored(rel), rel


def test_application_source_is_not_excluded():
    for rel in ("BACKEND_API_ROUTES.py", "auto_backup.py", "night_pipeline.py", "requirements.txt", "Dockerfile",
                "scripts/pre_restart.sh", "alembic.ini", "alembic/versions/0001_init.py", "alembic/versions/seed.sql",
                "routes/parts.py", "data/fixture.json", ".env.example", "tests/test_night_pipeline.py"):
        assert not _ignored(rel), rel


def test_every_dump_or_secret_file_in_the_context_right_now_is_excluded():
    leaked = []
    for root, dirs, files in os.walk(BACKEND):
        dirs[:] = [d for d in dirs if d not in ("node_modules", ".git")]
        for f in files:
            rel = os.path.relpath(os.path.join(root, f), BACKEND)
            if (ARTIFACT.search(f) or SECRET.search(f)) and not _ignored(rel):
                if rel.replace(os.sep, "/").startswith(("alembic/", "alembic_pii/")) and f.endswith(".sql"):
                    continue                                    # application SQL, deliberately kept
                if f == ".env.example":
                    continue
                leaked.append(rel)
    assert leaked == [], f"would be baked into the image: {leaked[:20]}"
