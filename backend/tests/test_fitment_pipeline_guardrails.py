"""Fitment pipeline guardrails.

These tests protect the runtime fitment schema bootstrap and merge task output
contract so future changes do not silently break coverage workflows.
"""

import os
import sys

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool


BACKEND_DIR = os.path.dirname(os.path.dirname(__file__))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from BACKEND_DATABASE_MODELS import DATABASE_URL
import isolated_db_guard  # noqa: E402  (tests/ is on sys.path under pytest)
from db_update_agent import ensure_part_vehicle_fitment_table
from db_update_agent import merge_catalog_fitment_from_part_vehicle_fitment


def _make_catalog_session():
    engine = create_async_engine(DATABASE_URL, poolclass=NullPool)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return engine, factory


@pytest.mark.skipif(isolated_db_guard.static_refusal() is not None,
                    reason=f"runs DDL + a full-table DELETE: {isolated_db_guard.static_refusal()} "
                           "(isolated test database only — see tests/isolated_db_guard.py)")
@pytest.mark.asyncio
async def test_fitment_table_schema_guardrails_present():
    """Ensure runtime bootstrap yields expected columns/indexes for fitment flows."""
    engine, factory = _make_catalog_session()
    try:
        async with factory() as db:
            refusal = await isolated_db_guard.live_refusal(db)
            assert refusal is None, f"REFUSED before any DDL: {refusal}"
            await ensure_part_vehicle_fitment_table(db)

            table_exists = (await db.execute(text("""
                SELECT EXISTS (
                    SELECT 1
                    FROM information_schema.tables
                    WHERE table_schema = 'public'
                      AND table_name = 'part_vehicle_fitment'
                )
            """))).scalar()
            assert bool(table_exists), "part_vehicle_fitment table is missing"

            cols = (await db.execute(text("""
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = 'part_vehicle_fitment'
                  AND column_name IN ('tozeret_cd', 'degem_cd', 'shnat_yitzur', 'updated_at')
            """))).scalars().all()

            idx = (await db.execute(text("""
                SELECT indexname
                FROM pg_indexes
                WHERE tablename = 'part_vehicle_fitment'
                  AND indexname IN (
                    'idx_pvf_tozeret_degem',
                    'idx_pvf_manufacturer_model',
                    'uix_pvf_part_mfr_model_year_from'
                  )
            """))).scalars().all()

            assert set(cols) == {
                "tozeret_cd",
                "degem_cd",
                "shnat_yitzur",
                "updated_at",
            }
            assert set(idx) == {
                "idx_pvf_tozeret_degem",
                "idx_pvf_manufacturer_model",
                "uix_pvf_part_mfr_model_year_from",
            }
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_oom_blocked_tasks_are_refused_by_run_task_without_touching_the_db():
    """Every entry point (run_all_tasks, owner console, admin run-task API, maintenance scripts)
    goes through run_task(); a blocked task must be refused there, before any query."""
    import db_update_agent as dua

    class _NoDb:
        def __getattr__(self, name):
            raise AssertionError(f"blocked task touched the database ({name})")

    assert "merge_catalog_fitment_from_part_vehicle_fitment" in dua.OOM_BLOCKED_TASKS
    for name in dua.OOM_BLOCKED_TASKS:
        res = await dua.run_task(name, _NoDb())
        assert res["status"] == "skipped" and "OOM_BLOCKED_TASKS" in res["reason"], name
    from agents import owner_console
    assert not set(owner_console._task_names()) & dua.OOM_BLOCKED_TASKS


@pytest.mark.skipif(isolated_db_guard.static_refusal() is not None,
                    reason="writes parts_catalog: isolated test database only (tests/isolated_db_guard.py)")
@pytest.mark.skipif(os.getenv("RUN_HEAVY_DB_TESTS") != "1",
                    reason="runs a disabled full-catalogue task twice (~2.2 GB, writes parts_catalog): "
                           "OOM-killed in the production container 2026-10-18; opt in on a non-production DB")
@pytest.mark.asyncio
async def test_fitment_merge_output_contract_and_repeat_stability():
    """Merge task should keep a stable output contract and trend toward idempotence."""
    engine, factory = _make_catalog_session()
    try:
        async with factory() as db:
            refusal = await isolated_db_guard.live_refusal(db)
            assert refusal is None, f"REFUSED before any write: {refusal}"
            first = await merge_catalog_fitment_from_part_vehicle_fitment(db)
            second = await merge_catalog_fitment_from_part_vehicle_fitment(db)

        for result in (first, second):
            assert result.get("task") == "merge_catalog_fitment_from_part_vehicle_fitment"
            assert result.get("status") == "ok"
            for key in (
                "scanned_rows",
                "parts_with_fitment",
                "updated_parts",
                "merged_fitment_rows",
                "elapsed_s",
            ):
                assert key in result
                assert isinstance(result[key], (int, float))
                assert result[key] >= 0

        # A repeated run should not become more mutative than the first run.
        assert int(second["updated_parts"]) <= int(first["updated_parts"])
        assert int(second["merged_fitment_rows"]) <= int(first["merged_fitment_rows"])
    finally:
        await engine.dispose()
