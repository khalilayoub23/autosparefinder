"""SP07 - maintenance/fetch_embed_model.py: a cached model counts as present only when it is COMPLETE AND LOADABLE; a download is verified
against what the source publishes; every failure is a non-zero exit with a message. Each test runs the REAL main() in a fresh interpreter
from a chosen working directory; only `huggingface_hub` (network) is replaced, and the model on disk is a real, loadable tiny one
(tiny_embed_model.py). The real 470 MB download is `test_real_download_*` (RUN_REAL_MODEL_DOWNLOAD=1, needs internet; verified
2026-11-02 - see evidence/state_path_audit/sp07_real_download.OUTPUT.txt)."""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from tiny_embed_model import write_tiny_model  # noqa: E402

PRELUDE = """
import importlib.util, json, os, sys, types
sys.path.insert(0, %r); sys.path.insert(0, %r)
from tiny_embed_model import write_tiny_model
s = importlib.util.spec_from_file_location('fem', %r); m = importlib.util.module_from_spec(s); sys.modules['fem'] = m; s.loader.exec_module(m)
calls = []
def install(download=None, api=None):
    hub = types.ModuleType('huggingface_hub')
    def snapshot_download(repo, local_dir=None, allow_patterns=None, force_download=False):
        calls.append({'dest': local_dir, 'force': force_download})
        if isinstance(download, Exception): raise download
        if download: download(local_dir)
    hub.snapshot_download = snapshot_download
    class HfApi:
        def model_info(self, repo, files_metadata=False):
            if isinstance(api, Exception): raise api
            return types.SimpleNamespace(sha='f' * 40, siblings=api(m.DEST) if api else [])
    hub.HfApi = HfApi
    sys.modules['huggingface_hub'] = hub
def published(dest, wrong_sha=False, wrong_size=False):
    out = []
    import hashlib
    for rel in m.PATTERNS:
        f = os.path.join(dest, rel); b = open(f, 'rb').read()
        lfs = types.SimpleNamespace(sha256=('0' * 64 if wrong_sha else hashlib.sha256(b).hexdigest())) if rel == 'onnx/model.onnx' else None
        out.append(types.SimpleNamespace(rfilename=rel, size=len(b) + (1 if wrong_size else 0), lfs=lfs))
    return out
""" % (str(APP), str(APP / "tests"), str(APP / "maintenance" / "fetch_embed_model.py"))


def run(body, cwd, dest, **env):
    e = {k: v for k, v in os.environ.items() if k not in ("EMBED_MODEL_DIR", "STATE_DIR")}
    e.update(PYTHONPATH=str(APP), EMBED_MODEL_DIR=str(dest), **{k: str(v) for k, v in env.items()})
    p = subprocess.run([sys.executable, "-W", "ignore", "-c", PRELUDE + body], cwd=str(cwd), env=e, capture_output=True, text=True, timeout=180)
    assert p.returncode == 0, f"{p.stdout[-800:]}\n{p.stderr[-2000:]}"
    return json.loads([ln for ln in p.stdout.splitlines() if ln.startswith("RESULT ")][-1][7:]), p


MAIN = "sys.argv = ['x']; rc = m.main(); print('RESULT ' + json.dumps({'rc': rc, 'calls': calls}))\n"


def test_valid_cache_is_reused_without_any_download_from_any_cwd(tmp_path):
    d = tmp_path / "model"
    write_tiny_model(str(d))
    before = (d / "onnx" / "model.onnx").stat()
    for cwd in ("/", str(tmp_path), str(APP / "tests")):
        out, p = run("install(download=RuntimeError('must not download'))\n" + MAIN, cwd, d)
        assert out == {"rc": 0, "calls": []} and "already present and loadable" in p.stdout
    after = (d / "onnx" / "model.onnx").stat()
    assert (before.st_ino, before.st_mtime_ns) == (after.st_ino, after.st_mtime_ns)


@pytest.mark.parametrize("damage", ["truncated_onnx", "garbage_onnx", "empty_onnx", "missing_config", "missing_onnx_only_incomplete", "corrupt_tokenizer"])
def test_incomplete_or_unreadable_cache_is_never_accepted_and_is_repaired_by_a_forced_download(tmp_path, damage):
    d = tmp_path / "model"
    write_tiny_model(str(d))
    onnx = d / "onnx" / "model.onnx"
    if damage == "truncated_onnx":
        onnx.write_bytes(onnx.read_bytes()[: onnx.stat().st_size // 2])
    elif damage == "garbage_onnx":
        onnx.write_bytes(os.urandom(1000))
    elif damage == "empty_onnx":
        onnx.write_bytes(b"")
    elif damage == "missing_config":
        (d / "config.json").unlink()
    elif damage == "missing_onnx_only_incomplete":                       # what an interrupted download leaves behind
        onnx.unlink()
        inc = d / ".cache" / "huggingface" / "download" / "onnx"
        inc.mkdir(parents=True)
        (inc / "model.onnx.abc.incomplete").write_bytes(b"x" * 500)
    elif damage == "corrupt_tokenizer":
        (d / "tokenizer.json").write_text("{not json")
    body = ("install(download=lambda dest: write_tiny_model(dest), api=lambda dest: published(dest))\n" + MAIN)
    out, p = run(body, "/", d)
    assert out["rc"] == 0 and len(out["calls"]) == 1, (out, p.stdout)             # NOT skipped, repaired
    assert out["calls"][0]["force"] is True                                         # something was there and failed verification: never reused
    assert "NOT valid" in p.stdout and any(ln.startswith("[fetch_embed_model] done:") and "loadable" in ln for ln in p.stdout.splitlines())


def test_download_that_leaves_a_bad_or_missing_artifact_fails_non_zero(tmp_path):
    d = tmp_path / "model"
    bodies = {
        "writes_nothing": "install(download=lambda dest: None)\n",
        "writes_truncated": "install(download=lambda dest: (write_tiny_model(dest), open(os.path.join(dest, 'onnx', 'model.onnx'), 'wb').write(b'\\x08')))\n",
    }
    for name, setup in bodies.items():
        out, p = run(setup + MAIN, "/", d)
        assert out["rc"] == 1 and "FAILED" in p.stderr and "not valid" in p.stderr, (name, p.stderr)
        assert "done:" not in p.stdout


def test_download_is_checked_against_the_published_size_and_sha256(tmp_path):
    d = tmp_path / "model"
    ok, p = run("install(download=lambda dest: write_tiny_model(dest), api=lambda dest: published(dest))\n" + MAIN, "/", d)
    assert ok["rc"] == 0 and "checked against source revision" in p.stdout
    for kind, needle in (("wrong_sha=True", "sha256"), ("wrong_size=True", "source publishes")):
        bad = tmp_path / ("m_" + kind.split("=")[0])
        out, p = run(f"install(download=lambda dest: write_tiny_model(dest), api=lambda dest: published(dest, {kind}))\n" + MAIN, "/", bad)
        assert out["rc"] == 1 and needle in p.stderr, (kind, p.stderr)
        # the file that disagreed with the source is removed, so the NEXT run cannot mistake it for a valid cache
        again, p2 = run("install(download=lambda dest: write_tiny_model(dest), api=lambda dest: published(dest))\n" + MAIN, "/", bad)
        assert again["rc"] == 0 and len(again["calls"]) == 1, (kind, p2.stdout)
    # metadata unreachable: the load check still applies and the limitation is stated, not hidden
    out, p = run("install(download=lambda dest: write_tiny_model(dest), api=OSError('no route'))\n" + MAIN, "/", tmp_path / "m_nometa")
    assert out["rc"] == 0 and "published checksums unavailable" in p.stdout


def test_network_failure_is_a_clear_non_zero_exit_not_a_traceback(tmp_path):
    out, p = run("install(download=ConnectionError('name resolution failed'))\n" + MAIN, "/", tmp_path / "model")
    assert out["rc"] == 1 and "FAILED - download" in p.stderr and "ConnectionError" in p.stderr and "Traceback" not in p.stderr


def test_force_redownloads_a_valid_cache(tmp_path):
    d = tmp_path / "model"
    write_tiny_model(str(d))
    out, _ = run("install(download=lambda dest: write_tiny_model(dest), api=lambda dest: published(dest))\n"
                 "sys.argv = ['x', '--force']; rc = m.main(); print('RESULT ' + json.dumps({'rc': rc, 'calls': calls}))\n", "/", d)
    assert out["rc"] == 0 and len(out["calls"]) == 1 and out["calls"][0]["force"] is True


@pytest.mark.skipif(os.environ.get("RUN_REAL_MODEL_DOWNLOAD") != "1", reason="real 470 MB download: set RUN_REAL_MODEL_DOWNLOAD=1 (needs internet)")
def test_real_download_matches_the_published_artifact_loads_and_is_reused(tmp_path):
    """The actual MiniLM download from the real source into an isolated directory (never /app/state)."""
    d = tmp_path / "model"
    env = {k: v for k, v in os.environ.items() if k != "STATE_DIR"}
    env.update(PYTHONPATH=str(APP), EMBED_MODEL_DIR=str(d))
    cmd = [sys.executable, str(APP / "maintenance" / "fetch_embed_model.py")]
    p = subprocess.run(cmd, cwd="/", env=env, capture_output=True, text=True, timeout=900)
    assert p.returncode == 0, p.stderr[-1500:]
    onnx = d / "onnx" / "model.onnx"
    # the published identity is asked for in a SUBPROCESS: the suite's network guard (conftest) refuses non-local lookups made in-process
    meta_code = ("import json; from huggingface_hub import HfApi; s = {x.rfilename: x for x in HfApi().model_info("
                 "'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2', files_metadata=True).siblings}['onnx/model.onnx']; "
                 "print(json.dumps([s.size, s.lfs.sha256]))")
    q0 = subprocess.run([sys.executable, "-c", meta_code], cwd="/", env=env, capture_output=True, text=True, timeout=120)
    assert q0.returncode == 0, q0.stderr[-800:]
    pub_size, pub_sha = json.loads(q0.stdout.splitlines()[-1])
    assert onnx.stat().st_size == pub_size > 400_000_000
    assert hashlib.sha256(onnx.read_bytes()).hexdigest() == pub_sha
    st = onnx.stat()
    p2 = subprocess.run(cmd, cwd=str(tmp_path), env={**env, "HF_HUB_OFFLINE": "1"}, capture_output=True, text=True, timeout=300)
    assert p2.returncode == 0 and "already present and loadable" in p2.stdout
    assert (onnx.stat().st_ino, onnx.stat().st_mtime_ns) == (st.st_ino, st.st_mtime_ns)
    sys.path.insert(0, str(APP))
    code = ("import sys, json; sys.path.insert(0, %r); import category_embed as ce; v = ce.embed(['brake pad', 'רפידות בלם']); "
            "print(json.dumps([ce.available(), list(v.shape)]))" % str(APP))
    q = subprocess.run([sys.executable, "-c", code], cwd="/", env=env, capture_output=True, text=True, timeout=300)
    assert q.returncode == 0 and json.loads(q.stdout.splitlines()[-1]) == [True, [2, 384]], q.stderr[-1000:]
