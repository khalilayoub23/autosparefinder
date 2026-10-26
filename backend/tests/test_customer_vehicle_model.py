"""A customer's vehicle model is never erased before fitment matching (FIXES_TRACKER #69 §16).

normalize_vehicle_model_name is an ingest noise filter; on a customer's selection it returned ''
for numeric models (Peugeot 3008/2008/208, a bare Mazda "3"), so the fitment lane matched nothing
and every such search fell into the unindexed JSON fallback scan.
"""
import inspect
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from manufacturer_normalization import customer_vehicle_model, normalize_vehicle_model_name  # noqa: E402


@pytest.mark.parametrize("make,model,expected", [
    ("Peugeot", "3008", "3008"),
    ("Peugeot", "2008", "2008"),        # a model, not a year
    ("Peugeot", "208", "208"),
    ("Mazda", "3", "3"),
    ("Mazda", "MAZDA 3", "3"),          # registry form; the ingest filter left "MAZDA"
    ("Mazda", "MAZDA3", "3"),
    ("Peugeot", "Peugeot 308", "308"),
    ("Mini", "Mini Cooper", "Mini Cooper"),  # non-numeric remainder: untouched
    ("Toyota", "Corolla", "Corolla"),
    ("Peugeot", "3008  2019", "3008"),  # trailing year after another word
    ("Peugeot", "  ", ""),
    ("Toyota", "sku", ""),              # junk token stays rejected
])
def test_customer_model_kept(make, model, expected):
    assert customer_vehicle_model(make, model) == expected


def test_the_ingest_filter_is_what_erased_them():
    assert normalize_vehicle_model_name("3008") == "" and normalize_vehicle_model_name("3") == ""


def test_search_and_chat_paths_use_the_customer_helper():
    from routes import parts
    import BACKEND_AI_AGENTS as agents
    for fn in (parts._build_strict_vehicle_match_clause, parts._resolve_vehicle_search_context):
        src = inspect.getsource(fn)
        assert "customer_vehicle_model" in src and "or normalize_vehicle_model_name(" not in src
    src = inspect.getsource(parts._build_fast_vehicle_fitment_json_clause)
    assert "customer_vehicle_model_forms(" in src and "normalize_vehicle_model_name(" not in src
    src = inspect.getsource(agents)
    assert "or normalize_vehicle_model_name(vehicle_row.model)" not in src
    assert '[vehicle_context["model"]]' not in src                # chat looks up every filing form
    assert "or normalize_vehicle_model_name(_vp_model)" not in src


# ── an unusable model never becomes a wildcard, and never costs a full scan ──────────────────
class _NoBrandDb:
    async def execute(self, stmt, params=None):
        raise RuntimeError("no brand lookup in this test")


@pytest.fixture
def _no_allowed(monkeypatch):
    import vehicle_model_match as vmm

    async def _a(_db, _makes, _models):
        return []
    monkeypatch.setattr(vmm, "allowed_fitment_models", _a)


@pytest.mark.parametrize("model", ["   "])
def test_json_lane_with_no_usable_model_is_a_constant_false(_no_allowed, model):
    import asyncio
    from routes import parts
    clause = asyncio.run(parts._build_vehicle_fitment_clause(
        _NoBrandDb(), {}, vehicle_manufacturer="Peugeot", vehicle_model=model, vehicle_year=2019))
    assert clause == "FALSE"


def test_json_lane_uses_the_customer_model_not_the_ingest_filter(monkeypatch):
    import asyncio
    import vehicle_model_match as vmm
    from routes import parts
    seen = {}

    async def _a(_db, _makes, models):
        seen["models"] = list(models)
        return []
    monkeypatch.setattr(vmm, "allowed_fitment_models", _a)
    params = {}
    asyncio.run(parts._build_vehicle_fitment_clause(
        _NoBrandDb(), params, vehicle_manufacturer="Mazda", vehicle_model="MAZDA 3", vehicle_year=2018))
    assert sorted(seen["models"]) == ["3", "mazda 3"]          # never the erroneous "mazda"


@pytest.mark.parametrize("make,model,expected", [
    ("Mazda", "MAZDA 3", ["3", "MAZDA 3"]),      # filed both ways: "3 bk…" and "mazda 3"
    ("MG", "MG4", ["4", "MG4"]),                 # filed only as "mg4": the raw form must survive
    ("Fiat", "FIAT 500 1.2", ["500 1.2", "FIAT 500 1.2"]),
    ("Peugeot", "3008", ["3008"]),
    ("Toyota", "Corolla", ["Corolla"]),
    ("Peugeot", "   ", []),
    ("Peugeot", "sku", []),
])
def test_model_forms_keep_every_filing_form(make, model, expected):
    from manufacturer_normalization import customer_vehicle_model_forms
    assert customer_vehicle_model_forms(make, model) == expected


@pytest.mark.parametrize("make,registry,expected", [
    ("Citroen", "BERLINGO", "BERLINGO"),        # unchanged; trim (COMFORT) is not the model
    ("Peugeot", "3008", "3008"),                # was ""
    ("Peugeot", "208", "208"),                  # was ""
    ("Tesla", "MODEL 3", "MODEL 3"),            # was ""
    ("Mazda", "MAZDA 3", "MAZDA 3"),            # was the bare make "MAZDA"
    ("MG", "MG4", "MG4"),
    ("Fiat", "FIAT 500 1.2", "FIAT 500"),       # unchanged (the search adds the 500 forms)
    ("Peugeot", "", ""),
])
def test_plate_lookup_never_returns_an_empty_or_make_only_model(make, registry, expected):
    from routes.parts import _plate_lookup_model, _split_vehicle_model_variant
    base, _sub = _split_vehicle_model_variant(make, registry)
    assert _plate_lookup_model(make, registry, base) == expected


def test_chat_keeps_the_deployed_behaviour_for_a_profile_without_a_model():
    # Reverted 2026-10-20: a free-text profile can be make + year with no model ("Toyota 2015");
    # turning it into "no parts" was based on a wrong premise. Decision is the owner's.
    import inspect
    import BACKEND_AI_AGENTS as agents
    src = inspect.getsource(agents.PartsFinderAgent.search_parts_in_db)
    assert "if _vp_mfr and _vp_model and _vp_year:" in src
    assert "elif vehicle_id or vehicle_context is not None:" not in src
