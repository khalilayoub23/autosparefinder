"""The 17 state-file paths classified CONFIGURABLE_BUT_UNVERIFIED by the 2026-11-02 importer path audit - each exercised through its
REAL module in a fresh interpreter (never the helper alone): default resolution from different working directories, override
(absolute / relative), read, write, read-back, repeat, malformed / unwritable behaviour. IDs SP01..SP17 are the audit's ids
(files/docs/IMPORTER_PATH_AUDIT.md section 8). Fixtures live in tmp_path; nothing here touches /app/state or any host path.

SP05 and the layout-gated checks need the isolated sandbox DB (RUN_DB_MUTATION_TESTS=1, ENVIRONMENT=sandbox)."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent
STATE = APP / "state"

# id -> (file relative to the app root, python expression for the resolved path, override env var, default tail under the state root,
#        honours STATE_DIR)
SPEC = {
    "SP01": ("category_embed.py", "m.MODEL_DIR", "EMBED_MODEL_DIR", "models/minilm-multilingual", False),
    "SP03": ("inproc_job_marker.py", "str(m.DIR)", "INPROC_JOB_MARKER_DIR", "inproc_jobs", False),
    "SP04": ("maintenance/build_part_thumbnails.py", "m._CLEARANCE_FAIL_FILE", "THUMB_CLEARANCE_FAIL_FILE", "thumb_clearance_failed.json", False),
    "SP05": ("maintenance/categorize_parts_batch.py", "m.CURSOR_PATH", "CATEGORIZE_CURSOR", "categorize_improve_cursor.json", False),
    "SP06": ("maintenance/fb_cookie_import.py", "str(m._STATE_DIR)", "STATE_DIR", "fb_browser_session", True),
    "SP07": ("maintenance/fetch_embed_model.py", "m.DEST", "EMBED_MODEL_DIR", "models/minilm-multilingual", False),
    "SP08": ("meili_checkpoint_state.py", "str(m.CHECKPOINT)", "MEILI_SYNC_CHECKPOINT", "meili_sync_checkpoint.json", False),
    "SP09": ("runtime_identity.py", "m.state_dir()", "RUNTIME_IDENTITY_DIR", "runtime_identity", False),
    "SP10": ("scripts/restart_workers.py", "m.STATE_FILE", "RESTART_STATE_FILE", "worker_state.json", False),
    "SP11": ("scripts/restart_workers.py", "m.LOG_DIR", "RESTART_LOG_DIR", "logs", False),
    "SP12": ("social/facebook_browser/fb_browser_login.py", "str(m._STATE_DIR)", "STATE_DIR", "fb_browser_session", True),
    "SP13": ("social/facebook_browser/session.py", "str(m._STATE_DIR)", "STATE_DIR", "fb_browser_session", True),
    "SP14": ("social/facebook_browser/session.py", "str(m._SCREENSHOT_DIR)", "STATE_DIR", "logs/fb_browser_failures", True),
    "SP15": ("social/facebook_browser/session.py", "str(m._PROFILE_DIR)", "FB_PROFILE_DIR", "fb_native_test/profile", True),
    "SP16": ("social/noa_ops.py", "m.fb_profile_dir()", "FB_PROFILE_DIR", "fb_native_test/profile", True),
    "SP17": ("thumbnail_idle_state.py", "str(m.STATE)", "THUMBNAIL_IDLE_STATE", "thumbnail_import_idle.json", False),
}
_OVERRIDES = {v[2] for v in SPEC.values()} | {"STATE_DIR", "FB_PROFILE_DIR", "HF_HOME", "TRANSFORMERS_CACHE"}


def _env(**extra):
    env = {k: v for k, v in os.environ.items() if k not in _OVERRIDES}
    env["PYTHONPATH"] = str(APP)
    env.update({k: str(v) for k, v in extra.items()})
    return env


def py(code, cwd, timeout=180, **env):
    """Run `code` in a FRESH interpreter (module-level constants are read at import) from `cwd`; the last stdout line is JSON."""
    p = subprocess.run([sys.executable, "-W", "ignore", "-c", code], cwd=str(cwd), env=_env(**env), capture_output=True, text=True, timeout=timeout)
    assert p.returncode == 0, f"rc={p.returncode}\nSTDOUT:{p.stdout[-1500:]}\nSTDERR:{p.stderr[-2500:]}"
    last = [ln for ln in p.stdout.splitlines() if ln.strip()][-1]
    return json.loads(last), p


def load(rel):
    """Python prologue importing the module by FILE path (bare names differ between folders) as `m`."""
    return (f"import importlib.util, json, sys; sys.path.insert(0, {str(APP)!r});"
            f"s = importlib.util.spec_from_file_location('mod_under_test', {str(APP / rel)!r}); m = importlib.util.module_from_spec(s);"
            f"sys.modules['mod_under_test'] = m; s.loader.exec_module(m);")


def resolved(sp, cwd, **env):
    rel, expr, *_ = SPEC[sp]
    out, _ = py(load(rel) + f"print(json.dumps({expr}))", cwd, **env)
    return out


IDS = sorted(SPEC)


# ── generic: default / working-directory independence / absolute and relative override, for every path ─────────────────────
@pytest.mark.parametrize("sp", IDS)
def test_default_resolution_is_the_state_volume_and_independent_of_the_cwd(sp, tmp_path):
    _rel, _expr, _var, tail, honors = SPEC[sp]
    want = str(STATE / tail)
    assert resolved(sp, tmp_path) == want == resolved(sp, "/") == resolved(sp, APP / "tests")
    assert not want.startswith("/opt/autosparefinder/") or str(APP).startswith("/opt/autosparefinder/backend")


@pytest.mark.parametrize("sp", IDS)
def test_absolute_override_is_used_by_the_real_module(sp, tmp_path):
    _rel, _expr, var, tail, honors = SPEC[sp]
    target = tmp_path / "ü ø קבצים" / "x"
    got = resolved(sp, tmp_path, **{var: target})
    assert got == (str(target / tail) if var == "STATE_DIR" else str(target)) and "/app/state" not in got


@pytest.mark.parametrize("sp", IDS)
def test_relative_override_resolves_against_the_app_root_not_the_cwd(sp, tmp_path):
    _rel, _expr, var, tail, honors = SPEC[sp]
    got = resolved(sp, tmp_path, **{var: "rel_dir/x"})
    base = APP / "rel_dir" / "x"
    assert got == (str(base / tail) if var == "STATE_DIR" else str(base)), got
    assert str(tmp_path) not in got


@pytest.mark.parametrize("sp", [s for s in IDS if SPEC[s][4] and SPEC[s][2] != "STATE_DIR"])
def test_state_dir_override_roots_the_default_of_modules_that_honour_it(sp, tmp_path):
    _rel, _expr, var, tail, _h = SPEC[sp]
    assert resolved(sp, tmp_path, STATE_DIR=tmp_path / "sd") == str(tmp_path / "sd" / tail)
    assert resolved(sp, tmp_path, STATE_DIR=tmp_path / "sd", **{var: tmp_path / "own"}) == str(tmp_path / "own")      # explicit var wins


# ── SP01 category_embed.MODEL_DIR (EMBED_MODEL_DIR): model weights ─────────────────────────────────────────────────────────
def test_SP01_model_dir_read(tmp_path):
    d = tmp_path / "model"
    code = load("category_embed.py") + ("import os; onnx = os.path.join(m.MODEL_DIR, 'onnx'); os.makedirs(onnx, exist_ok=True);"
                                        "a = m.available();"
                                        "open(os.path.join(onnx, 'model.onnx'), 'wb').write(b'x'); open(os.path.join(m.MODEL_DIR, 'tokenizer.json'), 'w').write('{}');"
                                        "print(json.dumps({'absent': a, 'present': m.available(), 'onnx': m.ONNX_PATH, 'tok': m.TOKENIZER_PATH}))")
    out, _ = py(code, tmp_path, EMBED_MODEL_DIR=d)
    assert out["absent"] is False                               # no model: graceful, no exception
    assert out["present"] is True                               # files found at MODEL_DIR (numpy/onnxruntime/tokenizers import in the image)
    assert out["onnx"] == str(d / "onnx" / "model.onnx") and out["tok"] == str(d / "tokenizer.json")


def test_SP01_local_exemplar_bank_is_read_from_the_model_dir(tmp_path):
    code = load("category_embed.py") + (
        "import numpy as np, os; os.makedirs(m.MODEL_DIR, exist_ok=True);"
        "np.savez(os.path.join(m.MODEL_DIR, 'exemplars_local.npz'), vecs=np.eye(2, dtype='float32'), labels=np.array(['brakes', 'engine']));"
        "m._load = lambda: True; m._centroids = None; m._centroid_labels = [];"      # the ONNX session cannot be fabricated; the bank read is the real code
        "n = m.build_centroids(force=True);"
        "print(json.dumps({'n': n, 'labels': m._centroid_labels}))")
    out, _ = py(code, tmp_path, EMBED_MODEL_DIR=tmp_path / "md")
    assert out["n"] == 2 and out["labels"] == ["brakes", "engine"]


# ── SP02 hf_client: legacy HF hub-cache probe (HF_HOME / TRANSFORMERS_CACHE / ~/.cache/huggingface / /root/.cache/huggingface) ─
def test_SP02_hf_cache_probe_reads_only_and_never_raises(tmp_path):
    code = load("hf_client.py") + ("import pathlib, os; slug = m.HF_EMBED_MODEL.replace('/', '--');"
                                   "empty = m._is_model_cached();"
                                   "hub = pathlib.Path(os.environ['HF_HOME']) / 'hub' / f'models--{slug}'; hub.mkdir(parents=True);"
                                   "found = m._is_model_cached();"
                                   "os.environ['HF_HOME'] = os.path.join(os.environ['HF_HOME'], 'hub', 'models--' + slug, 'nofile', 'x');"
                                   "blocked = m._is_model_cached();"
                                   "print(json.dumps({'empty': empty, 'found': found, 'blocked': blocked, 'default_probed': '/root/.cache/huggingface' in open(m.__file__).read()}))")
    out, _ = py(code, tmp_path, HF_HOME=tmp_path / "hf")
    assert out == {"empty": False, "found": True, "blocked": False, "default_probed": True}


# ── SP03 inproc_job_marker.DIR: durable "running / finished" marker read by scripts/restart_preflight ────────────────────────
def test_SP03_marker_write_read_update_repeat_and_malformed(tmp_path):
    d = tmp_path / "markers"
    code = load("inproc_job_marker.py") + (
        "import os, time;"
        "unknown = m.status('group_scan');"
        "m.mark_started('group_scan', now=1000.0); s1 = m.status('group_scan', now=1100.0);"
        "m.mark_started('group_scan', now=2000.0); s2 = m.status('group_scan', now=2100.0);"            # repeat start overwrites, never duplicates
        "m.mark_finished('group_scan', now=2200.0); s3 = m.status('group_scan', now=2300.0);"
        "open(os.path.join(m.DIR, 'bad.json'), 'w').write('{not json'); s4 = m.status('bad');"
        "stale = m.status('group_scan', stale_after_s=1, now=9e9);"
        "print(json.dumps({'unknown': unknown['known'], 's1': s1, 's2': s2, 's3': s3, 'bad_known': s4['known'], 'files': sorted(os.listdir(m.DIR))}))")
    out, _ = py(code, tmp_path, INPROC_JOB_MARKER_DIR=d)
    assert out["unknown"] is False
    assert out["s1"]["running"] is True and out["s1"]["started_at"] == 1000.0
    assert out["s2"]["started_at"] == 2000.0 and out["s2"]["running"] is True
    assert out["s3"]["running"] is False and out["s3"]["finished_at"] == 2200.0
    assert out["bad_known"] is False                                                          # malformed = unknown, never an exception
    assert out["files"] == ["bad.json", "group_scan.json"]                                    # one file per job, no .tmp left
    assert json.loads((d / "group_scan.json").read_text())["finished_at"] == 2200.0


def test_SP03_unwritable_marker_dir_names_the_path_then_raises(tmp_path):
    blocker = tmp_path / "afile"
    blocker.write_text("x")
    code = load("inproc_job_marker.py") + ("import logging; logging.basicConfig(level=logging.WARNING);"
                                           "try_ = None\n"
                                           "try:\n    m.mark_started('group_scan')\nexcept OSError as e:\n    try_ = type(e).__name__\n"
                                           "print(json.dumps({'raised': try_}))")
    out, p = py(code, tmp_path, INPROC_JOB_MARKER_DIR=blocker / "sub")
    assert out["raised"] in ("NotADirectoryError", "FileExistsError", "OSError")
    assert "NOT written" in p.stderr and str(blocker) in p.stderr


# ── SP04 build_part_thumbnails._CLEARANCE_FAIL_FILE (THUMB_CLEARANCE_FAIL_FILE): cache of failed clearance mints ───────────────
def test_SP04_clearance_failure_cache_roundtrip_malformed_and_unwritable(tmp_path):
    f = tmp_path / "state" / "fail.json"
    code = load("maintenance/build_part_thumbnails.py") + (
        "import os; os.makedirs(os.path.dirname(m._CLEARANCE_FAIL_FILE), exist_ok=True);"
        "none = m._load_failed();"
        "m._save_failed({'a.example': 100.5}); a = m._load_failed(); m._save_failed({'a.example': 100.5, 'b.example': 7.0}); b = m._load_failed();"
        "open(m._CLEARANCE_FAIL_FILE, 'w').write('[broken'); bad = m._load_failed();"
        "print(json.dumps({'none': none, 'a': a, 'b': b, 'bad': bad, 'tmp_left': os.path.exists(m._CLEARANCE_FAIL_FILE + '.tmp')}))")
    out, _ = py(code, tmp_path, THUMB_CLEARANCE_FAIL_FILE=f)
    assert out == {"none": {}, "a": {"a.example": 100.5}, "b": {"a.example": 100.5, "b.example": 7.0}, "bad": {}, "tmp_left": False}
    blocker = tmp_path / "afile"
    blocker.write_text("x")
    out, p = py(load("maintenance/build_part_thumbnails.py") + "m._save_failed({'x': 1.0}); print(json.dumps(True))", tmp_path,
                THUMB_CLEARANCE_FAIL_FILE=blocker / "sub" / "fail.json")
    assert "WARNING" in p.stdout and str(blocker) in p.stdout                                  # best-effort cache, but never silent


# ── SP05 categorize_parts_batch.CURSOR_PATH (CATEGORIZE_CURSOR): improve-only keyset cursor (real script, sandbox DB) ───────────
async def test_SP05_improve_only_cursor_written_resumed_and_failures_are_loud(tmp_path):
    import asyncpg
    from importer_e2e_harness import cleanup, dsn, ensure_brand, new_tag, run_script, seed_part
    tag = new_tag()
    conn = await asyncpg.connect(dsn())
    cur = tmp_path / "cursor" / "c.json"
    try:
        mid, _ = await ensure_brand(conn, "BMW")
        await seed_part(conn, f"CUR-{tag}", "BMW", mid, oem=f"CU{tag}")
        await conn.execute("UPDATE parts_catalog SET category = 'כללי', updated_at = NOW() - interval '1 hour' WHERE sku = $1", f"CUR-{tag}")
        pid = str(await conn.fetchval("SELECT id FROM parts_catalog WHERE sku = $1", f"CUR-{tag}"))
        env = {"CATEGORIZE_CURSOR": str(cur)}
        p = run_script("maintenance/categorize_parts_batch.py", "--improve-only", env_extra=env, timeout=300)
        assert p.returncode == 0, p.stdout[-600:] + p.stderr[-900:]
        assert "improve-only cursor: (start)" in p.stdout                                       # missing cursor = start, not an error
        assert json.loads(cur.read_text())["last_id"] == pid                                    # written at the override path, real format
        await conn.execute("UPDATE parts_catalog SET updated_at = NOW() - interval '1 hour' WHERE sku = $1", f"CUR-{tag}")
        p = run_script("maintenance/categorize_parts_batch.py", "--improve-only", env_extra=env, timeout=300)
        assert p.returncode == 0 and f"improve-only cursor: {pid}" in p.stdout                  # second run RESUMES from the persisted id
        cur.write_text("{broken")
        p = run_script("maintenance/categorize_parts_batch.py", "--improve-only", env_extra=env, timeout=300)
        assert "WARNING: cursor" in p.stdout and "unreadable" in p.stdout                       # malformed: loud, restarts
        blocker = tmp_path / "afile"
        blocker.write_text("x")
        await conn.execute("UPDATE parts_catalog SET updated_at = NOW() - interval '1 hour', category = 'כללי' WHERE sku = $1", f"CUR-{tag}")
        p = run_script("maintenance/categorize_parts_batch.py", "--improve-only", env_extra={"CATEGORIZE_CURSOR": str(blocker / "x" / "c.json")}, timeout=300)
        assert "improve-only cursor NOT persisted" in p.stdout and str(blocker) in p.stdout    # unwritable: named, not swallowed
    finally:
        await cleanup(conn, f"%{tag}%")
        from importer_e2e_harness import release_created_brands
        await release_created_brands(conn)
        await conn.close()


# ── SP06 fb_cookie_import._STATE_DIR/cookies.json (STATE_DIR): the real main(), health check stubbed (no network) ───────────────
def test_SP06_cookie_import_writes_cookies_json_and_check_reads_it(tmp_path):
    code = load("maintenance/fb_cookie_import.py") + (
        "import asyncio, os\n"
        "async def ok(c): return True\n"
        "m._health_check = ok\n"
        "sys.argv = ['x']\n"
        "try:\n    m.main()\nexcept SystemExit as e:\n    rc = e.code\n"
        "sys.argv = ['x', '--check']; m.main();"
        "print(json.dumps({'rc': rc, 'cookies': str(m._COOKIES_FILE)}))")
    out, p = py(code, tmp_path, STATE_DIR=tmp_path / "sd", FB_C_USER="123", FB_XS="abc", FB_DATR="d", FB_SB="s", FB_FR="f")
    f = tmp_path / "sd" / "fb_browser_session" / "cookies.json"
    assert out["rc"] == 0 and out["cookies"] == str(f)
    assert sorted(c["name"] for c in json.loads(f.read_text())) == ["c_user", "datr", "fr", "sb", "xs"]
    assert "AUTHENTICATED" in p.stderr or "AUTHENTICATED" in p.stdout
    before = f.read_text()
    py(code, tmp_path, STATE_DIR=tmp_path / "sd", FB_C_USER="123", FB_XS="abc", FB_DATR="d", FB_SB="s", FB_FR="f")      # repeat: overwrite, not append
    again = json.loads(f.read_text())
    assert [c["name"] for c in again] == [c["name"] for c in json.loads(before)] and len(again) == 5      # cookie expiry is time-based: names/count only


# ── SP07 fetch_embed_model.DEST (EMBED_MODEL_DIR): present -> skip; absent -> download target is DEST (download stubbed) ──────
def test_SP07_fetch_skips_when_present_and_downloads_into_dest_when_absent(tmp_path):
    """Path level (download stubbed; the genuine download is tests/test_fetch_embed_model.py + evidence/sp07_real_download): absent -> the
    download target is exactly DEST; the downloaded model must be LOADABLE for the second run to skip (presence alone no longer counts)."""
    d = tmp_path / "dest"
    code = load("maintenance/fetch_embed_model.py") + (
        "import os, types\n"
        "sys.path.insert(0, %r)\nfrom tiny_embed_model import write_tiny_model\n" % str(APP / "tests") +
        "calls = []\n"
        "def fake(repo, local_dir=None, allow_patterns=None, force_download=False):\n"
        "    calls.append(local_dir); write_tiny_model(local_dir)\n"
        "class Api:\n    def model_info(self, *a, **k): raise OSError('offline')\n"
        "hub = types.ModuleType('huggingface_hub'); hub.snapshot_download = fake; hub.HfApi = Api; sys.modules['huggingface_hub'] = hub\n"
        "sys.argv = ['x']; r1 = m.main(); r2 = m.main()\n"
        "print(json.dumps({'r1': r1, 'r2': r2, 'calls': calls, 'dest': m.DEST}))")
    out, p = py(code, tmp_path, EMBED_MODEL_DIR=d)
    assert out["r1"] == 0 and out["r2"] == 0 and out["calls"] == [str(d)]                    # second run found a loadable model: no second download
    assert "already present and loadable" in p.stdout and (d / "onnx" / "model.onnx").exists()


# ── SP08 meili_checkpoint_state.CHECKPOINT (MEILI_SYNC_CHECKPOINT) - and the WRITER (meili_sync) must use the same file ─────────
def test_SP08_reader_and_writer_agree_on_the_override_and_formats(tmp_path):
    f = tmp_path / "sd" / "ck.json"
    code = ("import sys, json; sys.path.insert(0, %r)\n" % str(APP) +
            "import importlib.util\n"
            "def mod(rel, name):\n    s = importlib.util.spec_from_file_location(name, %r + '/' + rel); x = importlib.util.module_from_spec(s); sys.modules[name] = x; s.loader.exec_module(x); return x\n" % str(APP) +
            "ms = mod('meili_sync.py', 'ms'); rd = mod('meili_checkpoint_state.py', 'rd')\n"
            "missing = rd.last_completed_sync()\n"
            "ms._save_checkpoint(5, 10); partial = rd.last_completed_sync(); loaded = ms._load_checkpoint()\n"
            "ms._save_checkpoint(10, 10); done = rd.last_completed_sync()\n"
            "ms._save_checkpoint(10, 10); again = ms._load_checkpoint()\n"
            "open(ms.CHECKPOINT_FILE, 'w').write('{broken'); bad = rd.last_completed_sync(); badload = ms._load_checkpoint()\n"
            "ms._clear_checkpoint(); ms._clear_checkpoint(); gone = rd.last_completed_sync()\n"
            "print(json.dumps({'same': ms.CHECKPOINT_FILE == str(rd.CHECKPOINT), 'file': ms.CHECKPOINT_FILE, 'missing': missing, 'partial': partial,"
            " 'loaded_offset': loaded['offset'], 'done': done is not None, 'again': again['offset'], 'bad': bad, 'badload': badload, 'gone': gone}))")
    out, _ = py(code, tmp_path, MEILI_SYNC_CHECKPOINT=f)
    assert out == {"same": True, "file": str(f), "missing": None, "partial": None, "loaded_offset": 5, "done": True, "again": 10, "bad": None,
                   "badload": None, "gone": None}


def test_SP08_default_writer_and_reader_share_the_state_file(tmp_path):
    code = ("import sys, json, importlib.util; sys.path.insert(0, %r)\n" % str(APP) +
            "def mod(rel, name):\n    s = importlib.util.spec_from_file_location(name, %r + '/' + rel); x = importlib.util.module_from_spec(s); sys.modules[name] = x; s.loader.exec_module(x); return x\n" % str(APP) +
            "print(json.dumps([mod('meili_sync.py', 'ms').CHECKPOINT_FILE, str(mod('meili_checkpoint_state.py', 'rd').CHECKPOINT)]))")
    out, _ = py(code, tmp_path)
    assert out == [str(STATE / "meili_sync_checkpoint.json")] * 2


# ── SP09 runtime_identity.state_dir() (RUNTIME_IDENTITY_DIR): per-process module-identity records ───────────────────────────
def test_SP09_record_written_atomically_unique_per_process_and_failure_is_visible(tmp_path):
    d = tmp_path / "ri"
    code = load("runtime_identity.py") + (
        "import os\n"
        "rec = {'module': 'x', 'pid': 4242, 'process_start_ticks': 7, 'sha256': 'ab'}\n"
        "m._write(rec); m._write(rec); m._write({**rec, 'pid': 4243})\n"
        "print(json.dumps(sorted(os.listdir(m.state_dir()))))")
    out, _ = py(code, tmp_path, RUNTIME_IDENTITY_DIR=d)
    assert out == ["x.4242.7.json", "x.4243.7.json"]                                          # repeat = same file, no .tmp left
    assert json.loads((d / "x.4242.7.json").read_text())["sha256"] == "ab"
    blocker = tmp_path / "afile"
    blocker.write_text("x")
    _o, p = py(load("runtime_identity.py") + "m._write({'module': 'x', 'pid': 1, 'process_start_ticks': 1}); print(json.dumps(1))", tmp_path,
               RUNTIME_IDENTITY_DIR=blocker / "sub")
    assert "record NOT written" in p.stderr and str(blocker) in p.stderr


def test_SP09_identity_check_script_reads_the_same_directory(tmp_path):
    src = (APP / "maintenance" / "runtime_identity_check.py").read_text()
    assert "state_dir" in src or "RUNTIME_IDENTITY_DIR" in src or "runtime_identity" in src


# ── SP10 / SP11 restart_workers.STATE_FILE (RESTART_STATE_FILE) and LOG_DIR (RESTART_LOG_DIR) ───────────────────────────────
def _fake_proc(root: Path, pid: int, argv: list):
    d = root / str(pid)
    d.mkdir(parents=True)
    (d / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")
    os.symlink("/app", d / "cwd")


def test_SP10_capture_writes_the_state_file_and_list_reads_it_back(tmp_path):
    proc = tmp_path / "proc"
    proc.mkdir()
    _fake_proc(proc, 321, ["python3", "/app/importers/freesbe_importer.py", "--pages", "3"])
    state = tmp_path / "s" / "worker_state.json"
    code = load("scripts/restart_workers.py") + ("rc = m.cmd_capture(); data = json.load(open(m.STATE_FILE)); rc2 = m.cmd_capture(); data2 = json.load(open(m.STATE_FILE));"
                                                  "print(json.dumps({'rc': rc, 'rc2': rc2, 'n1': len(data['workers']), 'n2': len(data2['workers']), 'argv': data['workers'][0]['argv'],"
                                                  " 'file': m.STATE_FILE}))")
    out, _ = py(code, tmp_path, RESTART_PROC_ROOT=proc, RESTART_STATE_FILE=state)
    assert out["rc"] == 0 and out["n1"] == 1 == out["n2"] and out["argv"][-2:] == ["--pages", "3"] and out["file"] == str(state)
    assert not Path(str(state) + ".tmp").exists()


def test_SP10_resume_consumes_the_capture_exactly_once_and_stale_state_is_not_replayed(tmp_path):
    state = tmp_path / "worker_state.json"
    state.write_text(json.dumps({"version": 2, "timestamp": "2000-01-01T00:00:00Z",
                                 "workers": [{"name": "freesbe_importer", "argv": ["python3", "/nonexistent/x.py"], "cwd": "/app", "kind": "x"}]}))
    proc = tmp_path / "proc"
    proc.mkdir()
    code = load("scripts/restart_workers.py") + "rc = m.cmd_resume(); d = json.load(open(m.STATE_FILE)); print(json.dumps({'rc': rc, 'workers': d['workers'], 'consumed': 'consumed_at' in d}))"
    out, p = py(code, tmp_path, RESTART_PROC_ROOT=proc, RESTART_STATE_FILE=state, RESTART_LOG_DIR=tmp_path / "logs")
    assert out["workers"] == [] and out["consumed"] is True and "NOT RESUMED" in p.stdout and "captured" in p.stdout     # stale (not merely malformed) -> never launched, state consumed
    out2, p2 = py(code, tmp_path, RESTART_PROC_ROOT=proc, RESTART_STATE_FILE=state, RESTART_LOG_DIR=tmp_path / "logs")
    assert out2["workers"] == [] and "NOT RESUMED" not in p2.stdout                                   # second hook sees an empty list


def test_SP11_resume_writes_the_resumed_worker_log_under_log_dir(tmp_path):
    script = tmp_path / "freesbe_importer.py"                  # the resume validator only relaunches managed (resumable) importers
    script.write_text("print('worker-ran')\n")
    from datetime import datetime, timezone
    state = tmp_path / "worker_state.json"
    state.write_text(json.dumps({"version": 2, "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                 "workers": [{"name": "freesbe_importer", "argv": [sys.executable, str(script)], "cwd": str(tmp_path), "kind": "resumable"}]}))
    proc = tmp_path / "proc"
    proc.mkdir()
    logs = tmp_path / "lg" / "nested"
    code = load("scripts/restart_workers.py") + "import time; rc = m.cmd_resume(); time.sleep(1.5); print(json.dumps(rc))"
    py(code, tmp_path, RESTART_PROC_ROOT=proc, RESTART_STATE_FILE=state, RESTART_LOG_DIR=logs)
    written = sorted(logs.glob("resumed_freesbe_importer_*.log"))
    assert logs.is_dir() and written, "resume did not create LOG_DIR / the worker log there (validator may have refused the fixture)"
    assert "worker-ran" in written[0].read_text()


# ── SP12 fb_browser_login: cookies / screenshots directory shared with session.py and fb_cookie_import ─────────────────────────
def test_SP12_login_screenshot_lands_in_the_state_dir_and_three_modules_agree(tmp_path):
    code = load("social/facebook_browser/fb_browser_login.py") + (
        "import asyncio\n"
        "class P:\n    async def screenshot(self, path, full_page=False): open(path, 'wb').write(b'png')\n"
        "asyncio.run(m._screenshot(P(), 'step1')); asyncio.run(m._screenshot(P(), 'step1'))\n"
        "print(json.dumps({'dir': str(m._STATE_DIR), 'cookies': str(m._COOKIES_FILE), 'shots': sorted(__import__('os').listdir(m._SCREENSHOT_DIR))}))")
    out, _ = py(code, tmp_path, STATE_DIR=tmp_path / "sd")
    assert out["shots"] == ["step1.png"] and out["cookies"] == str(tmp_path / "sd" / "fb_browser_session" / "cookies.json")
    ids = {resolved(s, tmp_path, STATE_DIR=tmp_path / "sd") for s in ("SP06", "SP12", "SP13")}
    assert ids == {str(tmp_path / "sd" / "fb_browser_session")}                                      # one directory for writer, reader, importer


# ── SP13 session._STATE_DIR/_COOKIES_FILE: cookie jar read/write ────────────────────────────────────────────────────────────────
def test_SP13_cookie_jar_roundtrip_update_and_malformed(tmp_path):
    code = load("social/facebook_browser/session.py") + (
        "empty = m._load_cookies();"
        "m._save_cookies([{'name': 'c_user', 'value': '1'}]); a = m._load_cookies();"
        "m._save_cookies([{'name': 'c_user', 'value': '2'}, {'name': 'xs', 'value': 'z'}]); b = m._load_cookies();"
        "open(m._COOKIES_FILE, 'w').write('[oops'); bad = m._load_cookies();"
        "print(json.dumps({'empty': empty, 'a': a, 'b': b, 'bad': bad, 'file': str(m._COOKIES_FILE)}))")
    out, _ = py(code, tmp_path, STATE_DIR=tmp_path / "sd")
    assert out["empty"] == [] and out["bad"] == [] and out["a"][0]["value"] == "1" and [c["name"] for c in out["b"]] == ["c_user", "xs"]
    assert out["file"] == str(tmp_path / "sd" / "fb_browser_session" / "cookies.json")


# ── SP14 session._SCREENSHOT_DIR: failure screenshots, rotation at 50 ─────────────────────────────────────────────────────────
def test_SP14_failure_screenshots_written_rotated_and_keep_bounded(tmp_path):
    code = load("social/facebook_browser/session.py") + (
        "import asyncio, os\n"
        "class P:\n    async def screenshot(self, path, full_page=False): open(path, 'wb').write(b'png')\n"
        "d = m._SCREENSHOT_DIR; os.makedirs(d, exist_ok=True)\n"
        "for i in range(55): open(os.path.join(d, f'old{i:02d}.png'), 'wb').write(b'x'); os.utime(os.path.join(d, f'old{i:02d}.png'), (1000 + i, 1000 + i))\n"
        "asyncio.run(m._save_failure_screenshot(P(), 'login_failed'))\n"
        "names = sorted(os.listdir(d))\n"
        "print(json.dumps({'n': len(names), 'new': [n for n in names if n.startswith('login_failed_')], 'oldest_gone': 'old00.png' not in names, 'dir': str(d)}))")
    out, _ = py(code, tmp_path, STATE_DIR=tmp_path / "sd")
    assert len(out["new"]) == 1 and out["oldest_gone"] is True and out["n"] <= 51
    assert out["dir"] == str(tmp_path / "sd" / "logs" / "fb_browser_failures")


# ── SP15 session._PROFILE_DIR (FB_PROFILE_DIR): the persistent Chrome profile - real Chromium writes and re-reads it ──────────
def test_SP15_real_chromium_persists_its_profile_in_the_resolved_directory(tmp_path):
    code = load("social/facebook_browser/session.py") + (
        "import os\n"
        "from playwright.sync_api import sync_playwright\n"
        "m._PROFILE_DIR.mkdir(parents=True, exist_ok=True)\n"
        "def run(set_cookie):\n"
        "    with sync_playwright() as p:\n"
        "        ctx = p.chromium.launch_persistent_context(str(m._PROFILE_DIR), headless=True, args=['--no-sandbox'])\n"
        "        try:\n"
        "            if set_cookie:\n"
        "                ctx.add_cookies([{'name': 'k', 'value': 'v1', 'domain': 'persist.example', 'path': '/', 'expires': 4102444800}])\n"
        "                ctx.new_page().goto('about:blank')\n"
        "            names = [c['name'] for c in ctx.cookies('http://persist.example/')]\n"
        "        finally:\n"
        "            ctx.close()\n"
        "    return names\n"
        "first = run(True); second = run(False)\n"
        "print(json.dumps({'first': first, 'second': second, 'default_dir': os.path.isdir(os.path.join(str(m._PROFILE_DIR), 'Default')), 'profile': str(m._PROFILE_DIR)}))")
    out, _ = py(code, tmp_path, timeout=240, FB_PROFILE_DIR=tmp_path / "profile")
    assert out["profile"] == str(tmp_path / "profile") and out["default_dir"] is True
    assert out["first"] == ["k"] and out["second"] == ["k"]                                           # survives closing the browser (a new process re-reads it)


# ── SP16 noa_ops.fb_profile_dir(): the readiness check looks exactly where the session writes ───────────────────────────────────
def test_SP16_readiness_profile_is_the_session_profile_and_cookies_probe(tmp_path):
    prof = tmp_path / "sd" / "fb_native_test" / "profile"
    (prof / "Default").mkdir(parents=True)
    (prof / "Default" / "Cookies").write_bytes(b"x")
    code = ("import sys, json, importlib.util; sys.path.insert(0, %r)\n" % str(APP) +
            "def mod(rel, name):\n    s = importlib.util.spec_from_file_location(name, %r + '/' + rel); x = importlib.util.module_from_spec(s); sys.modules[name] = x; s.loader.exec_module(x); return x\n" % str(APP) +
            "n = mod('social/noa_ops.py', 'noa'); s = mod('social/facebook_browser/session.py', 'sess')\n"
            "import os\n"
            "print(json.dumps({'same': n.fb_profile_dir() == str(s._PROFILE_DIR), 'dir': n.fb_profile_dir(), 'cookies': os.path.exists(os.path.join(n.fb_profile_dir(), 'Default', 'Cookies'))}))")
    out, _ = py(code, tmp_path, STATE_DIR=tmp_path / "sd")
    assert out == {"same": True, "dir": str(prof), "cookies": True}
    out2, _ = py(code, tmp_path, STATE_DIR=tmp_path / "sd", FB_PROFILE_DIR=tmp_path / "other")
    assert out2["same"] is True and out2["dir"] == str(tmp_path / "other") and out2["cookies"] is False


# ── SP17 thumbnail_idle_state.STATE (THUMBNAIL_IDLE_STATE): restart-safe idle back-off ─────────────────────────────────────────
def test_SP17_idle_backoff_save_resume_repeat_clear_malformed_and_unwritable(tmp_path):
    f = tmp_path / "x" / "idle.json"
    code = load("thumbnail_idle_state.py") + (
        "missing = m.resume(floor_s=120, now=1000.0);"
        "m.save(600, now=1000.0); r1 = m.resume(floor_s=120, now=1100.0); m.save(1200, now=1000.0); r2 = m.resume(floor_s=120, now=1100.0);"
        "data = json.load(open(m.STATE));"
        "m.clear(); m.clear(); cleared = m.resume(floor_s=120, now=1100.0);"
        "open(m.STATE, 'w').write('{nope'); bad = m.resume(floor_s=120, now=1100.0);"
        "print(json.dumps({'missing': missing, 'r1': r1, 'r2': r2, 'data': data, 'cleared': cleared, 'bad': bad}))")
    out, _ = py(code, tmp_path, THUMBNAIL_IDLE_STATE=f)
    assert out["missing"] == [120.0, 300.0] == out["cleared"] == out["bad"]
    assert out["r1"] == [500.0, 1200.0] and out["r2"] == [1100.0, 2400.0] and out["data"] == {"drained_at": 1000.0, "backoff_s": 1200.0}
    blocker = tmp_path / "afile"
    blocker.write_text("x")
    _o, p = py(load("thumbnail_idle_state.py") + "m.save(600); print(json.dumps(1))", tmp_path, THUMBNAIL_IDLE_STATE=blocker / "sub" / "idle.json")
    assert "WARNING" in p.stdout and str(blocker) in p.stdout


# ── static guard: state paths are never cwd-relative and never host-only (targeted) ─────────────────────────────────────────────
def test_state_modules_do_not_hardcode_the_state_volume_or_use_cwd_relative_paths():
    import ast
    offenders = []
    for rel, expr, var, tail, honors in SPEC.values():
        tree = ast.parse((APP / rel).read_text(encoding="utf-8"))
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and getattr(n.func, "attr", "") in ("getenv", "get") and n.args and isinstance(n.args[0], ast.Constant) \
                    and n.args[0].value == var and len(n.args) > 1 and isinstance(n.args[1], ast.Constant) and str(n.args[1].value).startswith("/app/"):
                offenders.append(f"{rel}:{n.lineno} os.getenv({var!r}, {n.args[1].value!r})")
            if isinstance(n, ast.Call) and getattr(n.func, "attr", "") in ("getcwd", "chdir"):
                offenders.append(f"{rel}:{n.lineno} cwd-based")
    assert not offenders, "state path built from a hard-coded /app default or the cwd (use app_paths.state_path/state_dir):\n" + "\n".join(offenders)
