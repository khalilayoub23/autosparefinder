"""Phase C1 Step 0 release-gate checks.

These tests protect strict vehicle search behavior and cache-key isolation so
vehicle-bound search results cannot leak across vehicles.
"""

import os
import re
import sys
from pathlib import Path

import pytest


BACKEND_DIR = os.path.dirname(os.path.dirname(__file__))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from routes.parts import (  # noqa: E402
    SEARCH_RESPONSE_CACHE,
    _build_strict_vehicle_match_clause,
    _get_cached_search_response,
    _search_cache_key,
    _store_cached_search_response,
)


class _NoDbSession:
    async def execute(self, *_args, **_kwargs):
        raise RuntimeError("DB access is not required for this unit test")


@pytest.fixture(autouse=True)
def _stub_shared_model_rule(monkeypatch):
    """The model rule itself is tested against real data in test_vehicle_model_match.py;
    here only the clause shape is under test."""
    import vehicle_model_match as vmm

    async def _allowed(_db, _makes, models):
        return sorted({str(m).strip().lower() for m in models if m} | {"corolla e21"})
    monkeypatch.setattr(vmm, "allowed_fitment_models", _allowed)


def test_search_cache_key_isolated_by_vehicle_id():
    key_a = _search_cache_key(
        query="brake pad",
        vehicle_id="veh-001",
        vehicle_manufacturer="Toyota",
        vehicle_model="Corolla",
        vehicle_submodel=None,
        vehicle_year=2020,
        category="בלמים",
        per_type=4,
        sort_by="price_ils",
    )
    key_b = _search_cache_key(
        query="brake pad",
        vehicle_id="veh-002",
        vehicle_manufacturer="Toyota",
        vehicle_model="Corolla",
        vehicle_submodel=None,
        vehicle_year=2020,
        category="בלמים",
        per_type=4,
        sort_by="price_ils",
    )

    assert key_a != key_b
    assert key_a[1] == "veh-001"
    assert key_b[1] == "veh-002"


@pytest.mark.asyncio
async def test_cached_search_payload_isolated_by_vehicle_id():
    SEARCH_RESPONSE_CACHE.clear()

    key_a = _search_cache_key("oil filter", "veh-a", "Honda", "Civic", None, 2019, "מנוע", 4, "price_ils")
    key_b = _search_cache_key("oil filter", "veh-b", "Honda", "Civic", None, 2019, "מנוע", 4, "price_ils")

    payload_a = {"vehicle_id": "veh-a", "all_parts": [{"id": "A1"}]}
    payload_b = {"vehicle_id": "veh-b", "all_parts": [{"id": "B1"}]}

    await _store_cached_search_response(key_a, payload_a)
    await _store_cached_search_response(key_b, payload_b)

    out_a = await _get_cached_search_response(key_a)
    out_b = await _get_cached_search_response(key_b)

    assert out_a == payload_a
    assert out_b == payload_b
    assert out_a != out_b


@pytest.mark.asyncio
async def test_strict_vehicle_clause_requires_full_context():
    params = {}
    clause = await _build_strict_vehicle_match_clause(
        db=_NoDbSession(),
        params=params,
        vehicle_manufacturer="Toyota",
        vehicle_model=None,
        vehicle_submodel=None,
        vehicle_year=2020,
        prefix="gate",
    )

    assert clause is None
    assert params == {}


@pytest.mark.asyncio
async def test_strict_vehicle_clause_builds_json_and_pvf_guards():
    params = {}
    clause = await _build_strict_vehicle_match_clause(
        db=_NoDbSession(),
        params=params,
        vehicle_manufacturer="Toyota",
        vehicle_model="Corolla",
        vehicle_submodel="GLI",
        vehicle_year=2020,
        prefix="gate",
    )

    assert clause is not None
    assert "jsonb_array_elements(pc.compatible_vehicles)" in clause
    assert "part_vehicle_fitment pvf" in clause
    assert "pvf.year_from <= :gate_year" in clause
    assert "COALESCE(pvf.year_to, pvf.year_from) >= :gate_year" in clause

    assert params["gate_year"] == 2020
    assert any(key.startswith("gate_mfr_") for key in params)
    # model matching: the exact list chosen by the shared rule, no prefix/LIKE branch
    assert params["gate_model_terms"] == ["corolla", "corolla e21", "corolla gli"]
    assert "LOWER(TRIM(pvf.model)) = ANY(:gate_model_terms)" in clause
    assert "LIKE :gate_modelfwd" not in clause and "gate_modelfwd_0" not in params


@pytest.mark.asyncio
async def test_strict_vehicle_clause_has_no_prefix_or_substring_branch():
    # Defect #69 §9: the clause used word prefixes and "<model> %", so Corolla matched
    # Corolla Cross. Which models fit is now decided only by vehicle_model_match.
    params = {}
    clause = await _build_strict_vehicle_match_clause(
        db=_NoDbSession(), params=params, vehicle_manufacturer="Toyota",
        vehicle_model="Corolla", vehicle_submodel=None, vehicle_year=2021, prefix="gate",
        include_json=False)
    assert "LIKE" not in clause.split("pvf.model", 1)[1].split("AND pvf.year_from")[0]
    assert params["gate_model_terms"] == ["corolla", "corolla e21"]


@pytest.mark.asyncio
async def test_strict_vehicle_clause_can_disable_json_lane_for_fast_path():
    params = {}
    clause = await _build_strict_vehicle_match_clause(
        db=_NoDbSession(),
        params=params,
        vehicle_manufacturer="Toyota",
        vehicle_model="Corolla",
        vehicle_submodel="GLI",
        vehicle_year=2020,
        prefix="gatefast",
        include_json=False,
    )

    assert clause is not None
    assert "jsonb_array_elements(pc.compatible_vehicles)" not in clause
    assert "part_vehicle_fitment pvf" in clause
    assert params["gatefast_year"] == 2020


def test_manual_full_context_search_uses_structured_first_and_json_fallback():
    parts_source = (Path(BACKEND_DIR) / "routes" / "parts.py").read_text(encoding="utf-8")

    assert re.search(r'prefix="manualfit",\s*include_json=False', parts_source), (
        "manual full-context search must use structured strict fitment first"
    )
    assert "manual_json_fast_clause" in parts_source
    assert "manual_json_fallback_clause" in parts_source
    assert "manual_strict_clause_added" in parts_source
