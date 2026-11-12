"""
Regression tests for the fail-closed restart guard (FIXES_TRACKER 2026-11-12).

The incident: `bash pre_restart.sh ; docker restart autospare_backend` restarted production although
the preflight said BLOCKED. Every test here records whether the RESTART EXECUTABLE WAS INVOKED
(a fake `docker` appends to an `executed` file), never just a shell status.

Chain under test is the real one: scripts/guarded_restart.sh -> scripts/restart_guard.py ->
`docker exec ... restart_preflight.py` (fake) -> scripts/docker_shim.py (installed first on PATH like
/usr/local/bin/docker in production) -> the restart (fake docker).
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
GUARDED = SCRIPTS / "guarded_restart.sh"
PRE_RESTART = SCRIPTS / "pre_restart.sh"

FAKE_DOCKER = r"""#!/bin/bash
# Fake docker: records restart-class calls in $FAKE_DIR/executed, answers the preflight per $FAKE_DIR/mode.
echo "$*" >> "$FAKE_DIR/all_calls"
mode="$(cat "$FAKE_DIR/mode" 2>/dev/null || echo safe)"
if [ "$1" = "exec" ] && [[ "$*" == *restart_preflight.py* ]]; then
  nonce=""
  prev=""
  for a in "$@"; do [ "$prev" = "--nonce" ] && nonce="$a"; prev="$a"; done
  now="$(python3 -c 'import time;print(time.time())')"
  case "$mode" in
    safe)        printf '{"safe": true, "blockers": [], "notes": [], "verdict": "SAFE", "checked_at": %s, "nonce": "%s"}' "$now" "$nonce"; exit 0;;
    blocked)     printf '{"safe": false, "blockers": ["group scan running"], "notes": [], "verdict": "BLOCKED", "checked_at": %s, "nonce": "%s"}' "$now" "$nonce"; exit 4;;
    unsafe)      printf '{"safe": false, "blockers": ["x"], "notes": [], "verdict": "UNSAFE", "checked_at": %s, "nonce": "%s"}' "$now" "$nonce"; exit 0;;
    nonzero)     echo "boom"; exit 1;;
    timeout)     sleep 6; exit 0;;
    empty)       exit 0;;
    malformed)   echo '{"safe": tru'; exit 0;;
    notobject)   echo '[1,2]'; exit 0;;
    safe_rc1)    printf '{"safe": true, "blockers": [], "notes": [], "verdict": "SAFE", "checked_at": %s, "nonce": "%s"}' "$now" "$nonce"; exit 1;;
    old_result)  echo '{"safe": true, "blockers": [], "notes": [], "verdict": "SAFE", "checked_at": 1000000000.0, "nonce": "'"$nonce"'"}'; exit 0;;
    replayed)    printf '{"safe": true, "blockers": [], "notes": [], "verdict": "SAFE", "checked_at": %s, "nonce": "nonce-of-an-earlier-run"}' "$now"; exit 0;;
    no_nonce)    printf '{"safe": true, "blockers": [], "notes": [], "verdict": "SAFE", "checked_at": %s}' "$now"; exit 0;;
    inconsistent) printf '{"safe": true, "blockers": ["still a blocker"], "notes": [], "verdict": "SAFE", "checked_at": %s, "nonce": "%s"}' "$now" "$nonce"; exit 0;;
  esac
fi
case "$1" in
  restart|stop|kill|rm|compose) echo "$*" >> "$FAKE_DIR/executed"; exit "$(cat "$FAKE_DIR/restart_rc" 2>/dev/null || echo 0)";;
  inspect) exit 1;;
esac
exit 0
"""


class Box:
    def __init__(self, tmp: Path):
        self.dir = tmp / "fake"
        self.real = tmp / "realbin"
        self.shimbin = tmp / "shimbin"
        self.empty = tmp / "emptybin"
        for d in (self.dir, self.real, self.shimbin, self.empty):
            d.mkdir()
        fd = self.real / "docker"
        fd.write_text(FAKE_DOCKER)
        fd.chmod(fd.stat().st_mode | stat.S_IEXEC)
        shim = self.shimbin / "docker"
        shutil.copy(SCRIPTS / "docker_shim.py", shim)
        shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
        self.mode("safe")

    def mode(self, m: str):
        (self.dir / "mode").write_text(m)

    def restart_rc(self, rc: int):
        (self.dir / "restart_rc").write_text(str(rc))

    def env(self, **extra):
        e = {k: v for k, v in os.environ.items() if not k.startswith(("RESTART_GUARD", "PRE_RESTART"))}
        e["PATH"] = f"{self.shimbin}:{self.real}:/usr/local/bin:/usr/bin:/bin"
        e["FAKE_DIR"] = str(self.dir)
        e.update(extra)
        return e

    def run(self, cmd, shell=False, timeout=30, **env):
        return subprocess.run(cmd, shell=shell, env=self.env(**env), capture_output=True, text=True,
                              timeout=timeout, executable="/bin/bash" if shell else None)

    def executed(self) -> list:
        p = self.dir / "executed"
        return p.read_text().splitlines() if p.exists() else []

    def guarded(self, *docker_args, **env):
        return self.run([str(GUARDED), "test_op", "--", "docker", *docker_args], **env)


@pytest.fixture()
def box(tmp_path):
    return Box(tmp_path)


RESTART = ("restart", "autospare_backend")


# ---- 1. SAFE proceeds ------------------------------------------------------------------------
def test_safe_preflight_lets_the_restart_run(box):
    r = box.guarded(*RESTART)
    assert r.returncode == 0, r.stderr
    assert box.executed() == ["restart autospare_backend"]
    assert "AUTHORIZED" in r.stderr


# ---- 2-9. every non-SAFE answer is a hard denial and the restart executable is never invoked -----
@pytest.mark.parametrize("mode", ["blocked", "unsafe", "nonzero", "empty", "malformed", "notobject",
                                  "safe_rc1", "old_result", "replayed", "no_nonce", "inconsistent"])
def test_anything_but_a_fresh_safe_is_denied_and_the_restart_never_runs(box, mode):
    box.mode(mode)
    r = box.guarded(*RESTART)
    assert r.returncode == 4, (mode, r.stderr)
    assert box.executed() == [], f"restart executable was INVOKED for preflight mode {mode!r}"
    assert "DENIED" in r.stderr and "NOT executed" in r.stderr


def test_preflight_timeout_is_denied_and_does_not_hang(box):
    box.mode("timeout")
    t0 = time.time()
    r = box.guarded(*RESTART, RESTART_GUARD_TIMEOUT_S="1")
    assert r.returncode == 4 and "timed out" in r.stderr
    assert time.time() - t0 < 5.5
    assert box.executed() == []


def test_timeout_env_can_only_lower_the_limit():
    sys.path.insert(0, str(SCRIPTS))
    import restart_guard as g
    os.environ["RESTART_GUARD_TIMEOUT_S"] = "99999"
    try:
        assert g._timeout() == g.DEFAULT_TIMEOUT_S
        os.environ["RESTART_GUARD_TIMEOUT_S"] = "garbage"
        assert g._timeout() == g.DEFAULT_TIMEOUT_S
        os.environ["RESTART_GUARD_TIMEOUT_S"] = "2"
        assert g._timeout() == 2.0
    finally:
        os.environ.pop("RESTART_GUARD_TIMEOUT_S", None)


def test_missing_preflight_executable_is_denied(box):
    env = box.env()
    env["PATH"] = str(box.empty)            # no docker anywhere
    r = subprocess.run([sys.executable, str(SCRIPTS / "restart_guard.py"), "--op", "t", "--",
                        "docker", *RESTART], env=env, capture_output=True, text=True, timeout=30)
    assert r.returncode == 4 and "not found" in r.stderr
    assert box.executed() == []


# ---- 10. the exact previous failure -----------------------------------------------------------
def test_blocked_preflight_followed_by_semicolon_restart_never_restarts(box):
    """`bash pre_restart.sh ; docker restart autospare_backend` with a BLOCKED preflight."""
    box.mode("blocked")
    r = box.run(f"bash {PRE_RESTART} ; docker restart autospare_backend", shell=True)
    assert "ABORTED by the restart guard" in r.stderr          # pre_restart.sh stopped at the preflight
    assert "DENIED `docker restart autospare_backend`" in r.stderr   # ... and the raw restart that followed
    assert box.executed() == [], "the restart executable was invoked after a BLOCKED preflight"


def test_raw_restart_is_denied_even_when_the_preflight_is_safe(box):
    box.mode("safe")
    r = box.run("docker restart autospare_backend", shell=True)
    assert r.returncode == 4 and box.executed() == []
    r = box.run("docker compose up -d --no-deps --force-recreate backend", shell=True)
    assert r.returncode == 4 and box.executed() == []


def test_semicolon_chain_around_the_guarded_call_cannot_restart_when_blocked(box):
    box.mode("blocked")
    r = box.run(f"{GUARDED} op -- docker restart autospare_backend ; echo continued", shell=True)
    assert "continued" in r.stdout            # the shell carried on (as it always does) ...
    assert box.executed() == []               # ... but the restart executable was never called


# ---- 11. nested scripts -----------------------------------------------------------------------
def test_nested_script_calling_the_guard_is_guarded(box, tmp_path):
    inner = tmp_path / "deploy_like.sh"
    inner.write_text(f"#!/bin/bash\n{GUARDED} nested -- docker restart autospare_backend\necho after\n")
    inner.chmod(0o755)
    box.mode("blocked")
    assert box.run(f"bash {inner}", shell=True).returncode == 0   # script continues (no set -e) ...
    assert box.executed() == []                                     # ... restart did not run
    box.mode("safe")
    box.run(f"bash {inner}", shell=True)
    assert box.executed() == ["restart autospare_backend"]


def test_nested_script_calling_raw_docker_is_denied_even_if_safe(box, tmp_path):
    inner = tmp_path / "raw.sh"
    inner.write_text("#!/bin/bash\ndocker restart autospare_backend\n")
    inner.chmod(0o755)
    outer = tmp_path / "outer.sh"
    outer.write_text(f"#!/bin/bash\nbash {inner}\n")
    box.mode("safe")
    r = box.run(f"bash {outer}", shell=True)
    assert r.returncode == 4 and box.executed() == []


# ---- 12. bypass attempts ------------------------------------------------------------------------
def test_environment_overrides_do_not_bypass(box):
    box.mode("blocked")
    r = box.guarded(*RESTART, PRE_RESTART_FORCE="1", RESTART_GUARD_FORCE="1", RESTART_GUARD_SKIP="1")
    assert r.returncode == 4 and "ignored" in r.stderr and box.executed() == []
    r = box.run(f"bash {PRE_RESTART}", shell=True, PRE_RESTART_FORCE="1")
    assert r.returncode == 4 and box.executed() == []


def test_forged_token_is_rejected(box):
    for tok in ("1" * 32, "not-hex", "", "../../etc/passwd"):
        r = box.run("docker restart autospare_backend", shell=True, RESTART_GUARD_TOKEN=tok)
        assert r.returncode == 4
    assert box.executed() == []


def test_token_is_single_use_and_bound_to_its_exact_command(box):
    sys.path.insert(0, str(SCRIPTS))
    import restart_guard as g
    tid = g.issue_token("t", list(RESTART), "n", time.time())
    other = box.run("docker restart autospare_postgres_catalog", shell=True, RESTART_GUARD_TOKEN=tid)
    assert other.returncode == 4 and "different command" in other.stderr     # wrong command; token burned
    tid = g.issue_token("t", list(RESTART), "n", time.time())
    assert box.run("docker restart autospare_backend", shell=True, RESTART_GUARD_TOKEN=tid).returncode == 0
    assert box.executed() == ["restart autospare_backend"]
    again = box.run("docker restart autospare_backend", shell=True, RESTART_GUARD_TOKEN=tid)
    assert again.returncode == 4 and box.executed() == ["restart autospare_backend"]   # reuse rejected


def test_expired_token_is_rejected(box):
    sys.path.insert(0, str(SCRIPTS))
    import restart_guard as g
    tid = g.issue_token("t", list(RESTART), "n", time.time() - 500)
    r = box.run("docker restart autospare_backend", shell=True, RESTART_GUARD_TOKEN=tid)
    assert r.returncode == 4 and "expired" in r.stderr and box.executed() == []


# ---- 13. staleness (unit level, injected clock/runner) ---------------------------------------------
def _fake_run(payload, rc=0):
    class P:
        returncode = rc
        stdout = json.dumps(payload) if not isinstance(payload, str) else payload
        stderr = ""
    return lambda cmd, **kw: P()


def test_freshness_window_unit():
    sys.path.insert(0, str(SCRIPTS))
    import restart_guard as g
    now = 2_000_000_000.0

    def dec(checked, nonce="N", **over):
        data = {"safe": True, "blockers": [], "verdict": "SAFE", "checked_at": checked, "nonce": nonce}
        data.update(over)
        return g.authorize(run=_fake_run(data), now=lambda: now, nonce="N")

    assert dec(now).authorized
    assert not dec(now - 60).authorized                    # older than MAX_AGE_S
    assert not dec(now + 3600).authorized                  # from the future
    assert not dec(now, nonce="other").authorized          # for another request
    assert not dec(True).authorized                        # bool is not a timestamp
    assert not dec("now").authorized
    assert not g.authorize(run=_fake_run({"verdict": "SAFE"}), now=lambda: now, nonce="N").authorized


def test_every_attempt_runs_a_new_preflight_with_a_new_nonce(box):
    box.mode("safe")
    box.guarded(*RESTART)
    box.guarded(*RESTART)
    nonces = [line.split("--nonce ")[1] for line in (box.dir / "all_calls").read_text().splitlines()
              if "restart_preflight.py" in line]
    assert len(nonces) == 2 and nonces[0] != nonces[1]


# ---- 14. the restart command's own failure is propagated ---------------------------------------------
def test_restart_failure_status_is_propagated(box):
    box.mode("safe")
    box.restart_rc(7)
    r = box.guarded(*RESTART)
    assert r.returncode == 7
    assert box.executed() == ["restart autospare_backend"]


def test_invalid_invocations_are_denials(box):
    assert box.run([str(GUARDED)]).returncode == 4
    assert box.run([str(GUARDED), "op"]).returncode == 4
    assert box.run([str(GUARDED), "op", "docker", "restart", "x"]).returncode == 4
    assert box.executed() == []


# ---- shim: only restart-class calls on protected services are gated -----------------------------------
def test_shim_passes_everything_else_through(box):
    for cmd in ("docker ps", "docker exec autospare_backend ls", "docker logs autospare_backend",
                "docker compose ps", "docker compose logs backend",
                "docker compose up --dry-run -d --no-deps backend", "docker stop sandbox_backend_tmp",
                "docker restart some_unrelated_container", "docker build ."):
        assert box.run(cmd, shell=True).returncode == 0, cmd
    # only the two non-protected restart-class calls reached the (fake) docker
    assert [e for e in box.executed() if e.startswith(("stop", "restart"))] == \
        ["stop sandbox_backend_tmp", "restart some_unrelated_container"]
    # the gated ones, however they are spelled
    for cmd in ("docker stop autospare_postgres_catalog", "docker container restart autospare_redis",
                "docker -H unix:///var/run/docker.sock kill autospare_nginx", "docker rm -f flaresolverr",
                "docker compose restart backend", "docker compose -f x.yml down", "docker compose stop"):
        assert box.run(cmd, shell=True).returncode == 4, cmd


# ---- every entry point in the repo routes through the guard -----------------------------------------
REPO = Path(__file__).resolve().parents[2]


def _repo_file(rel: str) -> str:
    p = REPO / rel
    if not p.exists():
        pytest.skip(f"{rel} is not part of this checkout (backend-only mount)")
    return p.read_text()


@pytest.mark.parametrize("rel,needle", [
    ("deploy/deploy.sh", "guarded_restart.sh\" deploy_recreate"),
    (".github/workflows/ci.yml", "guarded_restart.sh ci_deploy"),
    ("scripts/fitment_search_latency_guard.sh", "guarded_restart.sh\" \\\n    fitment_latency_guard"),
    ("deploy/flaresolverr_reaper.sh", "guarded_restart.sh flaresolverr_reaper"),
])
def test_entry_points_use_the_guard_and_have_no_raw_restart(rel, needle):
    text = _repo_file(rel)
    assert needle in text, f"{rel} does not call the guard"
    import re
    lines = text.splitlines()
    for n, line in enumerate(lines, 1):
        code = line.split("#", 1)[0]
        if re.search(r"docker( compose)? +(restart|stop|kill)\b|compose up\b", code):
            prev = lines[n - 2] if n > 1 else ""
            assert "guarded_restart.sh" in code or ("guarded_restart.sh" in prev) or prev.rstrip().endswith("\\"), \
                f"{rel}:{n} raw restart outside the guard: {line}"


def test_pre_restart_has_no_override_and_uses_the_guard():
    text = PRE_RESTART.read_text()
    code = "\n".join(l.split("#", 1)[0] for l in text.splitlines())
    assert "PRE_RESTART_FORCE" not in code and "restart_guard.py" in code
    assert "exit 4" in code


def test_restart_workers_stop_refuses_unless_preflight_is_safe(monkeypatch, capsys):
    sys.path.insert(0, str(SCRIPTS))
    import restart_workers as rw
    import restart_preflight as pf
    monkeypatch.setattr(pf, "gather", lambda: {})
    monkeypatch.setattr(pf, "evaluate", lambda facts: {"safe": False, "blockers": ["an import is running"], "notes": []})
    monkeypatch.setattr(rw.os, "kill", lambda *a: pytest.fail("signalled a process although BLOCKED"))
    monkeypatch.setattr(rw, "scan", lambda: pytest.fail("scanned although BLOCKED"))
    assert rw.cmd_stop() == 4
    assert "REFUSED" in capsys.readouterr().err

    def boom(_):
        raise RuntimeError("redis down")
    monkeypatch.setattr(pf, "evaluate", boom)
    assert rw.cmd_stop() == 4
