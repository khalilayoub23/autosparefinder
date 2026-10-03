"""
Script: tests/test_restart_workers.py
Purpose: Regression tests for the two restart bugs fixed in FIXES_TRACKER #60:
           1. pre_restart.sh's stop loop matched and SIGTERM'd its own shell;
           2. resume hooks relaunched an importer without its arguments (and twice).
Method: real subprocesses (dummy "importers" and decoy shells) but a FAKE /proc root that
        lists only those dummies — the helper can never see, signal or relaunch a real
        process of the container these tests run in.
Last Updated: 2026-10-03
"""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import time

import pytest

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(BACKEND, "scripts")
HELPER = os.path.join(SCRIPTS, "restart_workers.py")
sys.path.insert(0, SCRIPTS)
import restart_workers as rw  # noqa: E402

ALL_NAMES = " ".join(rw.RESUMABLE + rw.STOP_ONLY)

DUMMY = r'''
import json, os, signal, sys, time
out = os.environ.get("DUMMY_OUT")
if out:
    with open(out, "a") as fh:
        fh.write(json.dumps(sys.argv) + "\n")
def _term(*_):
    if out:
        open(out + ".term", "a").write("SIGTERM\n")
    sys.exit(0)
signal.signal(signal.SIGTERM, _term)
if os.environ.get("DUMMY_EXIT"):
    sys.exit(0)
time.sleep(float(os.environ.get("DUMMY_SLEEP", "30")))
'''


def _fake_proc(root, procs):
    """root/<pid>/cmdline for the given {pid: argv}; cwd symlink → root."""
    for pid, argv in procs.items():
        d = os.path.join(root, str(pid))
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "cmdline"), "wb") as fh:
            fh.write(b"\0".join(a.encode() for a in argv) + b"\0")
        if not os.path.islink(os.path.join(d, "cwd")):
            os.symlink(root, os.path.join(d, "cwd"))
    return root


def _helper(cmd, env, timeout=40):
    return subprocess.run([sys.executable, HELPER, cmd], capture_output=True, text=True, timeout=timeout,
                          env={**os.environ, **env})


@pytest.fixture
def box(tmp_path):
    proc = tmp_path / "proc"
    proc.mkdir()
    (tmp_path / "logs").mkdir()
    env = {"RESTART_PROC_ROOT": str(proc), "RESTART_STATE_FILE": str(tmp_path / "worker_state.json"),
           "RESTART_LOG_DIR": str(tmp_path / "logs"), "RESTART_STOP_WAIT_S": "5", "RESTART_RESUME_STAGGER_S": "0",
           "DUMMY_OUT": str(tmp_path / "argv.log")}
    script = tmp_path / "car_parts_ie_import_generic.py"
    script.write_text(DUMMY)
    spawned = []

    def spawn(argv, extra_env=None):
        p = subprocess.Popen(argv, env={**os.environ, **env, **(extra_env or {})})
        spawned.append(p)
        return p
    yield type("Box", (), {"tmp": tmp_path, "proc": str(proc), "env": env, "script": str(script), "spawn": staticmethod(spawn)})
    for p in spawned:
        if p.poll() is None:
            p.kill()
            p.wait()
    for line in (open(env["DUMMY_OUT"]).read().splitlines() if os.path.exists(env["DUMMY_OUT"]) else []):
        pass
    subprocess.run(["pkill", "-f", str(tmp_path)], capture_output=True)      # only this test's tmp dir


# ── 1. matching is by real argv: shells, grep, pgrep and the helper never match ──

def test_scan_matches_only_python_running_a_managed_script(tmp_path):
    root = _fake_proc(str(tmp_path), {
        101: ["bash", "-c", f"for proc in {ALL_NAMES}; do pids=$(pgrep -f $proc); kill -TERM $pids; done"],
        102: ["pgrep", "-f", "car_parts_ie_import"],
        103: ["grep", "-E", "freesbe_importer|car_parts_ie_import"],
        104: ["sh", "-c", "ps aux | grep car_parts_ie_import_generic"],
        105: ["python3", "/app/scripts/restart_workers.py", "stop"],
        106: ["python3", "-m", "pytest", "tests/test_car_parts_ie_import.py"],
        107: ["python3", "-c", "import car_parts_ie_import_generic"],
        108: ["tail", "-f", "/app/state/logs/car_parts_ie_import_generic_1.log"],
        201: ["python3", "/app/importers/car_parts_ie_import_generic.py", "--brand", "tvr", "--file", "/tmp/a.json"],
        202: ["/usr/local/bin/python3.11", "-u", "/app/importers/freesbe_importer.py", "--page", "7"],
        203: ["python3", "/app/scrapers/oem_parts_online_scraper.py", "--brand", "kia"],
        204: ["python3", "/app/harvesters/car_parts_ie_flaresolverr_harvester.py"],   # the harvester is NOT an importer
    })
    found = {p["pid"]: p["kind"] for p in rw.scan(root)}
    assert found == {201: "resumable", 202: "resumable", 203: "stop_only"}


def test_the_old_stop_loop_really_self_matched():
    """Documents the bug: `pgrep -f <name>` inside a bash -c that names the targets finds itself."""
    r = subprocess.run(["bash", "-c", f'names="{ALL_NAMES}"; pgrep -f zz_no_such_freesbe_importer_zz | grep -c "^$$\\$" || true'],
                       capture_output=True, text=True)
    assert r.stdout.strip() == "1", "the shell's own command line contains the pattern, so pgrep -f returns the shell"


def test_stop_signals_every_target_and_never_the_decoy_shell(box):
    imp1 = box.spawn([sys.executable, box.script, "--brand", "tvr", "--file", "/tmp/a.json"])
    imp2 = box.spawn([sys.executable, box.script, "--brand", "uaz", "--file", "/tmp/b.json"])
    decoy = box.spawn(["bash", "-c", f"names='{ALL_NAMES}'; sleep 30"])            # what killed the old loop
    time.sleep(0.6)
    _fake_proc(box.proc, {imp1.pid: [sys.executable, box.script, "--brand", "tvr", "--file", "/tmp/a.json"],
                          imp2.pid: [sys.executable, box.script, "--brand", "uaz", "--file", "/tmp/b.json"],
                          decoy.pid: ["bash", "-c", f"names='{ALL_NAMES}'; sleep 30"]})
    r = _helper("stop", box.env)
    assert r.returncode == 0, r.stderr
    assert "signalled=2 exited=2 still_running=0" in r.stdout
    assert imp1.wait(5) == 0 and imp2.wait(5) == 0                   # both handled SIGTERM gracefully
    assert decoy.poll() is None, "a shell that merely mentions the names must not be touched"
    assert open(box.env["DUMMY_OUT"] + ".term").read().count("SIGTERM") == 2


def test_stop_with_nothing_running_is_a_clean_noop(box):
    r = _helper("stop", box.env)
    assert r.returncode == 0 and "no managed importer/scraper process is running" in r.stdout


# ── 2. capture keeps EXACT arguments, one entry per distinct command ───────────

def test_capture_records_exact_argv_for_every_distinct_importer(box):
    a = [sys.executable, box.script, "--brand", "tvr", "--file", "/tmp/tvr__280-coupe.json", "--vehicle-slug", "tvr/280-coupe"]
    b = [sys.executable, box.script, "--brand", "tvr", "--file", "/tmp/tvr__350.json", "--vehicle-slug", "tvr/350"]
    _fake_proc(box.proc, {11: a, 12: b, 13: a,                         # 13 = same command twice
                          14: ["python3", "/app/scrapers/oem_parts_online_scraper.py"],   # stop-only: never captured
                          15: ["bash", "-c", f"echo {ALL_NAMES}"]})
    r = _helper("capture", box.env)
    state = json.loads(r.stdout)
    assert [w["argv"] for w in state["workers"]] == [a, b]
    assert state["version"] == 2 and json.load(open(box.env["RESTART_STATE_FILE"])) == state


# ── 3. resume: exact arguments, no silent start, no duplicate, state consumed ──

def _state(box, workers, age_s=5):
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - age_s))
    json.dump({"version": 2, "workers": workers, "timestamp": ts}, open(box.env["RESTART_STATE_FILE"], "w"))


def _launched(box):
    p = box.env["DUMMY_OUT"]
    return [json.loads(l) for l in open(p).read().splitlines()] if os.path.exists(p) else []


def test_resume_relaunches_with_the_exact_recorded_arguments(box):
    data = box.tmp / "tvr.json"
    data.write_text("[]")
    argv = [sys.executable, box.script, "--brand", "tvr", "--file", str(data), "--vehicle-slug", "tvr/280-coupe"]
    _state(box, [{"name": "car_parts_ie_import_generic", "argv": argv, "cwd": str(box.tmp)}])
    r = _helper("resume", {**box.env, "DUMMY_EXIT": "1"})
    assert r.returncode == 0 and "RESUME RESULT: resumed=1 not_resumed=0 already_running=0" in r.stdout
    time.sleep(0.8)
    assert _launched(box) == [argv[1:]], "the importer must receive exactly the arguments it had before the restart"


def test_resume_twice_launches_once_state_is_consumed(box):
    data = box.tmp / "a.json"
    data.write_text("[]")
    argv = [sys.executable, box.script, "--brand", "a", "--file", str(data)]
    _state(box, [{"name": "car_parts_ie_import_generic", "argv": argv}])
    r1 = _helper("resume", {**box.env, "DUMMY_EXIT": "1"})     # container_start.sh
    r2 = _helper("resume", {**box.env, "DUMMY_EXIT": "1"})     # post_restart.sh a moment later
    time.sleep(0.8)
    assert "resumed=1" in r1.stdout and "nothing to resume" in r2.stdout and r2.returncode == 0
    assert len(_launched(box)) == 1
    st = json.load(open(box.env["RESTART_STATE_FILE"]))
    assert st["workers"] == [] and st["last_resume"][0]["outcome"] == "resumed"


def test_concurrent_resume_hooks_cannot_double_launch(box):
    data = box.tmp / "a.json"
    data.write_text("[]")
    _state(box, [{"name": "car_parts_ie_import_generic", "argv": [sys.executable, box.script, "--brand", "a", "--file", str(data)]}])
    env = {**os.environ, **box.env, "DUMMY_EXIT": "1"}
    ps = [subprocess.Popen([sys.executable, HELPER, "resume"], env=env, stdout=subprocess.PIPE, text=True) for _ in range(4)]
    outs = [p.communicate(timeout=40)[0] for p in ps]
    time.sleep(0.8)
    assert sum("resumed=1" in o for o in outs) == 1 and len(_launched(box)) == 1


def test_resume_refuses_when_only_a_script_path_was_recorded(box):
    # the pre-#60 state shape that produced "error: the following arguments are required"
    _state(box, [{"name": "car_parts_ie_import_generic", "cmd": box.script}])
    r = _helper("resume", box.env)
    assert r.returncode == 3 and "NOT RESUMED car_parts_ie_import_generic — no arguments were recorded" in r.stdout
    assert "resumed=0 not_resumed=1" in r.stdout and _launched(box) == []


def test_resume_refuses_when_the_input_file_is_gone(box):
    argv = [sys.executable, box.script, "--brand", "tvr", "--file", str(box.tmp / "wiped_by_recreate.json")]
    _state(box, [{"name": "car_parts_ie_import_generic", "argv": argv}])
    r = _helper("resume", box.env)
    assert r.returncode == 3 and "input file is gone" in r.stdout and _launched(box) == []
    st = json.load(open(box.env["RESTART_STATE_FILE"]))
    assert st["workers"] == [] and st["last_resume"][0]["outcome"] == "not_resumed"   # never retried blindly


def test_resume_accepts_the_legacy_full_cmd_shape_with_its_arguments(box):
    data = box.tmp / "a.json"
    data.write_text("[]")
    full = f"{sys.executable} {box.script} --brand tvr --file {data} --vehicle-slug tvr/280-coupe"
    _state(box, [{"name": "car_parts_ie_import_generic", "cmd": box.script, "full_cmd": full}])
    r = _helper("resume", {**box.env, "DUMMY_EXIT": "1"})
    time.sleep(0.8)
    assert r.returncode == 0 and _launched(box) == [[box.script, "--brand", "tvr", "--file", str(data), "--vehicle-slug", "tvr/280-coupe"]]


def test_resume_does_not_duplicate_an_identical_running_importer(box):
    data = box.tmp / "a.json"
    data.write_text("[]")
    argv = [sys.executable, box.script, "--brand", "a", "--file", str(data)]
    _fake_proc(box.proc, {77: argv})                                   # it is already running
    _state(box, [{"name": "car_parts_ie_import_generic", "argv": argv}])
    r = _helper("resume", box.env)
    assert r.returncode == 0 and "SKIPPED" in r.stdout and "already_running=1" in r.stdout and _launched(box) == []


def test_resume_refuses_anything_that_is_not_a_managed_importer(box):
    evil = box.tmp / "rm_everything.py"
    evil.write_text(DUMMY)
    _state(box, [{"name": "x", "argv": [sys.executable, str(evil)]},
                 {"name": "y", "argv": ["bash", "-c", "echo car_parts_ie_import"]},
                 {"name": "z", "argv": ["python3", "/app/scrapers/oem_parts_online_scraper.py"]}])
    r = _helper("resume", box.env)
    assert r.returncode == 3 and "resumed=0 not_resumed=3" in r.stdout and _launched(box) == []


def test_resume_with_no_or_empty_state_is_a_clean_noop(box):
    assert _helper("resume", box.env).returncode == 0
    _state(box, [])
    r = _helper("resume", box.env)
    assert r.returncode == 0 and "nothing to resume" in r.stdout


# ── 4. the shell scripts use the helper and the broken patterns are gone ──────

def _sh(name):
    return open(os.path.join(SCRIPTS, name), encoding="utf-8").read()


def _code(s):
    return "\n".join(l for l in s.splitlines() if not l.strip().startswith("#"))


def test_pre_restart_uses_the_helper_and_has_no_self_matching_loop():
    sh = _code(_sh("pre_restart.sh"))
    assert "restart_workers.py capture" in sh and "restart_workers.py stop" in sh
    assert "pgrep" not in sh and "kill -TERM" not in sh and "ps aux" not in sh
    assert sh.index("restart_workers.py capture") < sh.index("status='superseded'") < sh.index("restart_workers.py stop")


def test_resume_hooks_only_resume_through_the_helper():
    for name in ("container_start.sh", "post_restart.sh"):
        sh = _code(_sh(name))
        assert "restart_workers.py resume" in sh, name
        assert "python3 {cmd}" not in sh and "resume_args" not in sh and "subprocess.Popen" not in sh, name
    assert "exit $rc" in _sh("post_restart.sh") and "Post-restart resume complete" not in _sh("post_restart.sh")


def test_brand_discovery_children_are_stop_only_never_resumed():
    assert set(rw.STOP_ONLY) == {"oempartsonline_importer", "oem_parts_online_scraper"}
    assert not set(rw.STOP_ONLY) & set(rw.RESUMABLE) and "catalog_scraper" not in rw.RESUMABLE
    for s in ("oem_parts_online_scraper.py", "oempartsonline_importer.py"):
        assert rw._kind(s) == "stop_only"
    assert rw._kind("catalog_scraper.py") is None and rw._kind("car_parts_ie_flaresolverr_harvester.py") is None


def test_resume_refuses_a_capture_that_is_not_from_this_restart(box):
    # pre_restart.sh ran, the restart did not follow; hours later a container start must not replay it
    data = box.tmp / "a.json"
    data.write_text("[]")
    _state(box, [{"name": "car_parts_ie_import_generic", "argv": [sys.executable, box.script, "--brand", "a", "--file", str(data)]}],
           age_s=3 * 3600)
    r = _helper("resume", box.env)
    assert r.returncode == 3 and "not from this restart" in r.stdout and _launched(box) == []
    assert json.load(open(box.env["RESTART_STATE_FILE"]))["workers"] == []


def test_stop_treats_an_exited_but_unreaped_process_as_exited(box):
    # importers are children of uvicorn: after SIGTERM they are zombies until reaped
    imp = box.spawn([sys.executable, box.script, "--brand", "a", "--file", "/tmp/a.json"])
    time.sleep(0.5)
    _fake_proc(box.proc, {imp.pid: [sys.executable, box.script, "--brand", "a", "--file", "/tmp/a.json"]})
    r = _helper("stop", box.env)                 # imp is not wait()ed here → zombie after it exits
    assert "signalled=1 exited=1 still_running=0" in r.stdout
