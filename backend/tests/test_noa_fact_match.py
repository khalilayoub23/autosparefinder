"""Regression: NOA's catalog fact must match the WHOLE topic part and the car's make (2026-11-13)."""
import ast
import pathlib

from social.noa_fact import name_conditions


def test_all_tokens_required():
    sql, p = name_conditions("חיישן חמצן", "oxygen sensor")
    assert sql.count("ILIKE") == 4 and " AND " in sql
    assert p["h0"] == "%חיישן%" and p["h1"] == "%חמצן%" and p["e0"] == "%oxygen%" and p["e1"] == "%sensor%"


def test_parenthetical_dropped_and_empty_is_false():
    _, p = name_conditions("מצת חימום (דיזל)", "glow plug")
    assert "%דיזל%" not in p.values()
    assert name_conditions("", "")[0] == "FALSE"


def test_route_has_no_first_word_match_and_no_cross_make_fallback():
    src = pathlib.Path(__file__).resolve().parent.parent.joinpath("BACKEND_API_ROUTES.py").read_text()
    ast.parse(src)
    assert "heb_part.split()[0]" not in src
    fn = src[src.index("async def _noa_real_catalog_fact"):src.index("def _noa_utm_link")]
    assert fn.count("_sql.format(") == 1          # only the make-constrained query remains


def test_fact_requires_vehicle_specific_fitment():
    src = pathlib.Path(__file__).resolve().parent.parent.joinpath("BACKEND_API_ROUTES.py").read_text()
    fn = src[src.index("async def _noa_real_catalog_fact"):src.index("def _noa_utm_link")]
    assert "part_vehicle_fitment pvf" in fn and "pvf.model ILIKE :model" in fn and "pvf.manufacturer ILIKE :mfr" in fn
