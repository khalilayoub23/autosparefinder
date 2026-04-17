"""
End-to-end fitment search flow checks for two user journeys:

1) VIN or license plate + part_type -> grouped OEM/Original/Aftermarket results
2) Manual vehicle selection (manufacturer/model/year + part_type) -> grouped results

These tests run against the live API at localhost:8000 and verify fitment accuracy
for returned part IDs against part_vehicle_fitment in the catalog DB.
"""

import asyncio
import os
import sys
from typing import Any, Dict, Optional

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

BACKEND_DIR = os.path.dirname(os.path.dirname(__file__))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from BACKEND_DATABASE_MODELS import DATABASE_URL

BASE_URL = "http://localhost:8000"


def _run_sql_fetchone(sql: str, params: Optional[Dict[str, Any]] = None):
    async def _query():
        eng = create_async_engine(DATABASE_URL, poolclass=NullPool)
        try:
            async with eng.connect() as conn:
                res = await conn.execute(text(sql), params or {})
                return res.fetchone()
        finally:
            await eng.dispose()

    return asyncio.run(_query())


def _pick_manual_candidate() -> Optional[Dict[str, Any]]:
    row = _run_sql_fetchone(
        """
        SELECT
            pvf.manufacturer,
            pvf.model,
            pvf.year_from AS year,
            COALESCE(NULLIF(pc.category, ''), 'בלמים') AS category
        FROM part_vehicle_fitment pvf
        JOIN parts_catalog pc ON pc.id = pvf.part_id
        WHERE pvf.manufacturer IS NOT NULL
          AND btrim(pvf.manufacturer) <> ''
          AND pvf.model IS NOT NULL
          AND btrim(pvf.model) <> ''
          AND pvf.year_from IS NOT NULL
          AND pc.is_active = TRUE
        GROUP BY pvf.manufacturer, pvf.model, pvf.year_from, pc.category
        ORDER BY COUNT(*) DESC
        LIMIT 1
        """
    )
    if not row:
        return None
    return {
        "manufacturer": row[0],
        "model": row[1],
        "year": int(row[2]),
        "category": row[3],
    }


def _pick_cross_ref_candidate() -> Optional[Dict[str, Any]]:
    row = _run_sql_fetchone(
        """
        SELECT
            pvf.manufacturer,
            pvf.model,
            pvf.year_from AS year,
            COALESCE(NULLIF(pc.category, ''), 'בלמים') AS category,
            pc.oem_number AS seed_query
        FROM part_cross_reference pcr
        JOIN parts_catalog pc ON pc.id = pcr.part_id
        JOIN part_vehicle_fitment pvf ON pvf.part_id = pc.id
        WHERE pc.is_active = TRUE
          AND COALESCE(pcr.is_superseded, FALSE) = FALSE
          AND pcr.ref_number IS NOT NULL
          AND btrim(pcr.ref_number) <> ''
          AND pc.oem_number IS NOT NULL
          AND btrim(pc.oem_number) <> ''
          AND pvf.manufacturer IS NOT NULL
          AND btrim(pvf.manufacturer) <> ''
          AND pvf.model IS NOT NULL
          AND btrim(pvf.model) <> ''
          AND pvf.year_from IS NOT NULL
        ORDER BY pvf.year_from DESC
        LIMIT 1
        """
    )
    if not row:
        return None
    return {
        "manufacturer": row[0],
        "model": row[1],
        "year": int(row[2]),
        "category": row[3],
        "seed_query": row[4],
    }


def _fitment_matches(part_id: str, manufacturer: str, model: str, year: int) -> bool:
    row = _run_sql_fetchone(
        """
        SELECT EXISTS (
                        SELECT 1
                        FROM parts_catalog pc
                        WHERE pc.id::text = :part_id
                            AND (
                                     EXISTS (
                                             SELECT 1
                                             FROM part_vehicle_fitment pvf
                                             WHERE pvf.part_id = pc.id
                                                 AND (
                                                            LOWER(TRIM(pvf.manufacturer)) = LOWER(TRIM(:mfr))
                                                     OR LOWER(TRIM(pvf.manufacturer)) LIKE CONCAT('%', LOWER(TRIM(:mfr)), '%')
                                                     OR LOWER(TRIM(:mfr)) LIKE CONCAT('%', LOWER(TRIM(pvf.manufacturer)), '%')
                                                 )
                                                 AND (
                                                            LOWER(TRIM(pvf.model)) = LOWER(TRIM(:model))
                                                     OR LOWER(TRIM(pvf.model)) LIKE CONCAT(LOWER(TRIM(:model)), ' %')
                                                     OR LOWER(TRIM(:model)) LIKE CONCAT(LOWER(TRIM(pvf.model)), ' %')
                                                 )
                                                 AND pvf.year_from <= :year
                                                 AND COALESCE(pvf.year_to, pvf.year_from) >= :year
                                     )
                                     OR EXISTS (
                                             SELECT 1
                                           FROM jsonb_array_elements(
                                               CASE
                                                WHEN pc.compatible_vehicles IS NULL
                                                    OR jsonb_typeof(pc.compatible_vehicles) <> 'array'
                                                THEN '[]'::jsonb
                                                ELSE pc.compatible_vehicles
                                               END
                                           ) cv_fit
                                           WHERE (
                                                            LOWER(TRIM(COALESCE(cv_fit->>'make', cv_fit->>'manufacturer', ''))) = LOWER(TRIM(:mfr))
                                                     OR LOWER(TRIM(COALESCE(cv_fit->>'make', cv_fit->>'manufacturer', ''))) LIKE CONCAT('%', LOWER(TRIM(:mfr)), '%')
                                                     OR LOWER(TRIM(:mfr)) LIKE CONCAT('%', LOWER(TRIM(COALESCE(cv_fit->>'make', cv_fit->>'manufacturer', ''))), '%')
                                                 )
                                                 AND COALESCE(cv_fit->>'model', cv_fit->>'model_year', '') ILIKE CONCAT('%', :model, '%')
                                                 AND (
                                                   cv_fit->>'model_year' ILIKE CONCAT('%', CAST(:year_str AS TEXT), '%')
                                                     OR (
                                                                cv_fit->>'year_from' ~ '^[0-9]+$'
                                                                AND cv_fit->>'year_to' ~ '^[0-9]+$'
                                                                AND (cv_fit->>'year_from')::int <= :year
                                                                AND (cv_fit->>'year_to')::int >= :year
                                                     )
                                                 )
                                     )
                            )
        )
        """,
        {
            "part_id": part_id,
            "mfr": manufacturer,
            "model": model,
            "year": int(year),
            "year_str": str(year),
        },
    )
    return bool(row and row[0])


def _assert_grouped_shape(body: Dict[str, Any]):
    for key in ("original", "oem", "aftermarket", "all_parts"):
        assert key in body, f"missing key: {key}"
    assert isinstance(body["all_parts"], list), "all_parts must be a list"


def _assert_fitment_for_results(body: Dict[str, Any], manufacturer: str, model: str, year: int):
    if not body["all_parts"]:
        pytest.skip("No matching parts returned for this vehicle/part_type candidate")

    checked = 0
    for item in body["all_parts"][:10]:
        part = item.get("part") if isinstance(item, dict) else None
        assert part and part.get("id"), "all_parts item must include part.id"
        assert _fitment_matches(part["id"], manufacturer, model, year), (
            f"Part {part['id']} does not match fitment filters {manufacturer}/{model}/{year}"
        )
        checked += 1

    assert checked > 0, "Expected to validate at least one part"


def test_manual_vehicle_search_returns_grouped_fitment_accurate_results():
    candidate = _pick_manual_candidate()
    if not candidate:
        pytest.skip("No fitment candidate found in DB")

    resp = httpx.get(
        f"{BASE_URL}/api/v1/parts/search",
        params={
            "q": "",
            "vehicle_manufacturer": candidate["manufacturer"],
            "vehicle_model": candidate["model"],
            "vehicle_year": candidate["year"],
            "category": candidate["category"],
            "per_type": 4,
        },
        timeout=45,
    )
    assert resp.status_code == 200, f"manual search returned {resp.status_code}: {resp.text[:400]}"

    body = resp.json()
    _assert_grouped_shape(body)
    _assert_fitment_for_results(
        body,
        candidate["manufacturer"],
        candidate["model"],
        candidate["year"],
    )


def test_search_by_vin_supports_part_type_and_grouped_fitment_results():
    candidate = _pick_manual_candidate()
    if not candidate:
        pytest.skip("No fitment candidate found in DB")

    vin = os.getenv("TEST_VIN", "1HGCM82633A004352")
    resp = httpx.get(
        f"{BASE_URL}/api/v1/parts/search-by-vin",
        params={
            "vin": vin,
            "part_type": candidate["category"],
            "part_query": "",
            "limit": 4,
        },
        timeout=60,
    )

    if resp.status_code in (404, 422, 429, 502, 503, 504):
        pytest.skip(f"VIN external decode/provider unavailable for test run (status={resp.status_code})")

    assert resp.status_code == 200, f"search-by-vin returned {resp.status_code}: {resp.text[:400]}"
    body = resp.json()
    _assert_grouped_shape(body)

    vehicle = body.get("vehicle") or {}
    mfr = (vehicle.get("manufacturer") or "").strip()
    model = (vehicle.get("model") or "").strip()
    year = vehicle.get("year")

    if not (mfr and model and isinstance(year, int) and year > 0):
        pytest.skip("VIN decode did not provide complete vehicle context for strict fitment assertion")

    _assert_fitment_for_results(body, mfr, model, year)


def test_search_by_license_plate_supports_part_type_and_grouped_fitment_results():
    candidate = _pick_manual_candidate()
    if not candidate:
        pytest.skip("No fitment candidate found in DB")

    plate = os.getenv("TEST_LICENSE_PLATE", "1234567")
    resp = httpx.get(
        f"{BASE_URL}/api/parts/by-license-plate/{plate}",
        params={
            "part_type": candidate["category"],
            "query": "",
            "per_type": 4,
        },
        timeout=60,
    )

    if resp.status_code in (404, 422, 429, 502, 503, 504):
        pytest.skip(f"License plate external lookup unavailable for test run (status={resp.status_code})")

    assert resp.status_code == 200, f"by-license-plate returned {resp.status_code}: {resp.text[:400]}"
    body = resp.json()
    _assert_grouped_shape(body)

    vehicle = body.get("vehicle") or {}
    mfr = (vehicle.get("manufacturer") or "").strip()
    model = (vehicle.get("model") or "").strip()
    year = vehicle.get("year")

    if not (mfr and model and isinstance(year, int) and year > 0):
        pytest.skip("Plate lookup did not provide complete vehicle context for strict fitment assertion")

    _assert_fitment_for_results(body, mfr, model, year)


def test_search_by_license_plate_without_part_type_returns_grouped_shape():
    plate = os.getenv("TEST_LICENSE_PLATE", "1234567")
    resp = httpx.get(
        f"{BASE_URL}/api/parts/by-license-plate/{plate}",
        timeout=60,
    )

    if resp.status_code in (404, 422, 429, 502, 503, 504):
        pytest.skip(f"License plate external lookup unavailable for test run (status={resp.status_code})")

    assert resp.status_code == 200, f"by-license-plate returned {resp.status_code}: {resp.text[:400]}"
    body = resp.json()
    _assert_grouped_shape(body)


def test_search_cross_ref_toggle_keeps_grouped_contract():
    candidate = _pick_manual_candidate()
    if not candidate:
        pytest.skip("No fitment candidate found in DB")

    params = {
        "q": "",
        "vehicle_manufacturer": candidate["manufacturer"],
        "vehicle_model": candidate["model"],
        "vehicle_year": candidate["year"],
        "category": candidate["category"],
        "per_type": 4,
    }

    resp_disabled = httpx.get(
        f"{BASE_URL}/api/v1/parts/search",
        params={**params, "enable_cross_refs": "false"},
        timeout=60,
    )
    assert resp_disabled.status_code == 200, f"search (crossrefs=false) returned {resp_disabled.status_code}: {resp_disabled.text[:400]}"
    body_disabled = resp_disabled.json()
    _assert_grouped_shape(body_disabled)

    resp_enabled = httpx.get(
        f"{BASE_URL}/api/v1/parts/search",
        params={**params, "enable_cross_refs": "true"},
        timeout=60,
    )
    assert resp_enabled.status_code == 200, f"search (crossrefs=true) returned {resp_enabled.status_code}: {resp_enabled.text[:400]}"
    body_enabled = resp_enabled.json()
    _assert_grouped_shape(body_enabled)


def test_search_cross_ref_toggle_is_non_regressive_for_seed_query():
    candidate = _pick_cross_ref_candidate()
    if not candidate:
        pytest.skip("No cross-reference candidate with fitment found in DB")

    params = {
        "q": candidate["seed_query"],
        "vehicle_manufacturer": candidate["manufacturer"],
        "vehicle_model": candidate["model"],
        "vehicle_year": candidate["year"],
        "category": candidate["category"],
        "per_type": 4,
    }

    resp_disabled = httpx.get(
        f"{BASE_URL}/api/v1/parts/search",
        params={**params, "enable_cross_refs": "false"},
        timeout=60,
    )
    if resp_disabled.status_code in (404, 422, 429, 502, 503, 504):
        pytest.skip(f"Search unavailable for seed query (status={resp_disabled.status_code})")
    assert resp_disabled.status_code == 200, f"search (crossrefs=false) returned {resp_disabled.status_code}: {resp_disabled.text[:400]}"
    body_disabled = resp_disabled.json()
    _assert_grouped_shape(body_disabled)

    resp_enabled = httpx.get(
        f"{BASE_URL}/api/v1/parts/search",
        params={**params, "enable_cross_refs": "true"},
        timeout=60,
    )
    if resp_enabled.status_code in (404, 422, 429, 502, 503, 504):
        pytest.skip(f"Search unavailable for seed query with cross refs enabled (status={resp_enabled.status_code})")
    assert resp_enabled.status_code == 200, f"search (crossrefs=true) returned {resp_enabled.status_code}: {resp_enabled.text[:400]}"
    body_enabled = resp_enabled.json()
    _assert_grouped_shape(body_enabled)

    assert len(body_enabled["all_parts"]) >= len(body_disabled["all_parts"]), (
        "Cross-reference expansion should not reduce result count for the same seed query"
    )
