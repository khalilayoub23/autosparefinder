"""Importers run from their DEFAULT source location, in the production container layout (2026-11-02).

Why: until now the e2e tests passed the fixture by argument/env, so a default pointing at the developer host
(/opt/autosparefinder/...) was never exercised - and tests that wrote their fixture to that path made it "exist" inside the
test container. Here the fixture is written to the path the importer resolves BY ITSELF (no argument, no override) and the
real script runs against the isolated sandbox DB.

Only meaningful - and only allowed to write fixtures - inside the scratch layout (`ASF_PATH_TEST_LAYOUT=1`: the candidate tree
as /app, empty tmpfs /app/state + /app/uploads); anywhere else (the shared sandbox_backend bind-mounts the PRODUCTION tree)
the tests skip. Fixture-based: NOT a live-source verification."""
import json
import os
from pathlib import Path

import pytest

import app_paths
import price_policy as pp
from importer_e2e_harness import base_by, cleanup, dsn, ensure_brand, new_tag, run_script, seed_part
import asyncpg
import maint_guard_support as support

OWNED = 33.0

pytestmark = pytest.mark.skipif(os.environ.get("ASF_PATH_TEST_LAYOUT") != "1",
                                reason="needs the scratch container layout (ASF_PATH_TEST_LAYOUT=1): fixtures are written to default paths")


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    support.neutral_probes(monkeypatch)


async def _connect():
    from importer_e2e_harness import align_sandbox_schema
    conn = await asyncpg.connect(dsn())
    await align_sandbox_schema(conn)
    return conn


@pytest.fixture(scope="module", autouse=True)
async def _leave_the_sandbox_as_found():
    """Importers create their supplier row ("SNG Barratt", "Inbar Group - Land Rover Israel") and the tests create brands: remove
    what THIS module added (ids recorded up front), so a run leaves the isolated database exactly as it found it."""
    from importer_e2e_harness import release_created_brands
    conn = await _connect()
    sup_before = {r["id"] for r in await conn.fetch("SELECT id FROM suppliers")}
    await conn.close()
    yield
    conn = await _connect()
    try:
        new = [r["id"] for r in await conn.fetch("SELECT id FROM suppliers") if r["id"] not in sup_before]
        if new:
            await conn.execute("DELETE FROM supplier_parts WHERE supplier_id = ANY($1::uuid[])", new)
            await conn.execute("DELETE FROM suppliers WHERE id = ANY($1::uuid[])", new)
        await release_created_brands(conn)
    finally:
        await conn.close()


@pytest.fixture
def place():
    """Write `text` at `path`, refuse to overwrite a pre-existing file, remove it afterwards."""
    made = []

    def _place(path: Path, text: str):
        assert not path.exists(), f"{path} exists in the layout: refusing to overwrite it"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        made.append(path)
        return path
    yield _place
    for p in made:
        p.unlink(missing_ok=True)


def test_layout_is_the_production_shape():
    """The layout the other tests rely on: app root /app, and none of the host-only directories exist."""
    assert app_paths.APP_ROOT == Path("/app")
    assert not Path("/opt/autosparefinder").exists(), "host path visible: this run would prove nothing"


async def test_bmw_il_direct_default_location(place):
    tag = new_tag()
    conn = await _connect()
    try:
        mid, _ = await ensure_brand(conn, "BMW")
        await seed_part(conn, f"BMW-OLD-{tag}-1", "BMW", mid, oem=f"B{tag}1")
        await seed_part(conn, f"BMW-OLD-{tag}-2", "BMW", mid, oem=f"B{tag}2", base=OWNED)
        place(app_paths.state_dir() / "champion_motors_parts.json", json.dumps({"parts": [
            {"oem_number": f"B{tag}1", "name_he": "רפידה", "price_ils": 200.0, "model": "G20"},
            {"oem_number": f"B{tag}2", "name_he": "פילטר", "price_ils": 80.0, "model": "F30"}]}))
        for _ in range(2):
            p = run_script("importers/bmw_il_direct_import.py")
            assert p.returncode == 0, p.stdout[-400:] + p.stderr[-800:]
            assert await base_by(conn, "sku", f"BMW-OLD-{tag}-1") == pp.base_price_for_cost(200.0)
            assert await base_by(conn, "sku", f"BMW-OLD-{tag}-2") == OWNED
    finally:
        await cleanup(conn, f"%{tag}%")
        await conn.close()


@pytest.mark.parametrize("script", ["importers/jaguar_ndjson_import.py", "importers/jaguar_batch_import.py"])
async def test_jaguar_importers_default_location(place, script):
    tag = new_tag()
    conn = await _connect()
    try:
        mid, _ = await ensure_brand(conn, "Jaguar")
        await seed_part(conn, f"JAG-OLD-{tag}-1", "Jaguar", mid, oem=f"J{tag}1")
        await seed_part(conn, f"JAG-OLD-{tag}-2", "Jaguar", mid, oem=f"J{tag}2", base=OWNED)
        rows = [{"base_part_number": f"J{tag}1", "part_number": f"P{tag}1", "title": "Pad", "price_gbp": 20.0, "part_origin": "original", "applications": []},
                {"base_part_number": f"J{tag}2", "part_number": f"P{tag}2", "title": "Disc", "price_gbp": 10.0, "part_origin": "aftermarket", "applications": []}]
        place(app_paths.state_dir() / "jaguar_parts_raw.ndjson", "\n".join(json.dumps(r) for r in rows) + "\n")
        for _ in range(2):
            p = run_script(script)
            assert p.returncode == 0, p.stdout[-400:] + p.stderr[-800:]
            assert await base_by(conn, "sku", f"JAG-OLD-{tag}-1") == pp.base_price_for_cost(round(20.0 * 4.8, 2))
            assert await base_by(conn, "sku", f"JAG-OLD-{tag}-2") == OWNED
    finally:
        await cleanup(conn, f"%{tag}%")
        await conn.close()


async def test_sng_barratt_jaguar_import_reads_its_default_input(place):
    """The script's main() finds <state>/jaguar_parts_raw.ndjson itself. The single row has price 0 (skipped by the importer),
    so nothing is written - the run proves discovery + read, not pricing (priced by test_importers_e2e.py)."""
    place(app_paths.state_dir() / "jaguar_parts_raw.ndjson", json.dumps({"part_number": "ZZ-NOPRICE", "title": "x", "price_gbp": 0}) + "\n")
    p = run_script("importers/sng_barratt_jaguar_import.py")
    out = p.stdout + p.stderr
    assert "Input not found" not in out, out[-600:]
    assert p.returncode == 0, out[-900:]


async def test_lr_import_default_location(place):
    tag = new_tag()
    conn = await _connect()
    try:
        await ensure_brand(conn, "Land Rover")
        place(app_paths.APP_ROOT / "land_rover_parts.json",
              json.dumps({"parts": [{"oem_number": f"LR{tag}1", "name": f"Brake pad {tag}", "price": 100.0, "description": "d", "in_stock": True}]}))
        for _ in range(2):
            p = run_script("importers/lr_import.py")
            assert p.returncode == 0, p.stdout[-400:] + p.stderr[-800:]
        assert await base_by(conn, "oem_number", f"LR{tag}1") == pp.base_price_for_cost(100.0)
        assert len(await conn.fetch("SELECT 1 FROM parts_catalog WHERE oem_number = $1", f"LR{tag}1")) == 1
    finally:
        await cleanup(conn, f"%{tag}%")
        await conn.close()


async def test_import_delek_brands_default_location(place):
    tag = new_tag()
    conn = await _connect()
    try:
        mid, _ = await ensure_brand(conn, "NIO")
        await seed_part(conn, f"NIO-OLD-{tag}-1", "NIO", mid, oem=f"NO{tag}1")
        await seed_part(conn, f"NIO-OLD-{tag}-2", "NIO", mid, oem=f"NO{tag}2", base=OWNED)
        place(app_paths.APP_ROOT / "nio_parts.json", json.dumps([
            {"oem_number": f"NO{tag}1", "name_he": "רפידה", "name": "pad", "price_ils": 100.0, "is_original": True, "model": "ES6"},
            {"oem_number": f"NO{tag}2", "name_he": "פילטר", "name": "filter", "price_ils": 50.0, "is_original": True, "model": "ES6"}], ensure_ascii=False))
        for _ in range(2):
            p = run_script("importers/import_delek_brands.py", "--brand", "NIO")
            assert p.returncode == 0, p.stdout[-400:] + p.stderr[-800:]
            assert await base_by(conn, "sku", f"NIO-OLD-{tag}-1") == pp.base_price_for_cost(100.0)
            assert await base_by(conn, "sku", f"NIO-OLD-{tag}-2") == OWNED
    finally:
        await cleanup(conn, f"%{tag}%")
        await conn.close()


async def test_backfill_vw_fitment_reads_its_default_input(place):
    """An empty champion list: discovery + read proven (no FileNotFoundError), nothing to write."""
    place(app_paths.state_dir() / "champion_motors_parts.json", "[]")
    p = run_script("maintenance/backfill_vw_fitment.py")
    out = p.stdout + p.stderr
    assert "FileNotFoundError" not in out and "SourceMissing" not in out, out[-900:]
