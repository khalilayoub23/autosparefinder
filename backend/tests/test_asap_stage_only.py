"""2026-11-13: ASAP relay must be able to STAGE an original download (hash, unique name, no importer, no overwrite)."""
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_asap_collect_stage_only_and_archive_before_overwrite():
    src = (ROOT / "routes" / "system.py").read_text()
    fn = src[src.index("async def asap_collect"):]
    stage = fn.index('data.get("stage_only")')
    spawn = fn.index("subprocess.Popen")
    assert stage < spawn and "sha256" in fn[stage:spawn] and "return JSONResponse" in fn[stage:spawn]
    assert "archive" in fn[spawn - 1500:spawn] or "arch = d / " in fn


def test_importer_has_rolled_back_dry_run():
    src = (ROOT / "importers" / "asap_import.py").read_text()
    assert "--dry-run" in src and "outer.rollback()" in src and "outer.start()" in src
