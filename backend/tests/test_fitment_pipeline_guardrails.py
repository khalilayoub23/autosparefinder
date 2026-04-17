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
from db_update_agent import ensure_part_vehicle_fitment_table
from db_update_agent import merge_catalog_fitment_from_part_vehicle_fitment


def _make_catalog_session():
    engine = create_async_engine(DATABASE_URL, poolclass=NullPool)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return engine, factory


@pytest.mark.asyncio
async def test_fitment_table_schema_guardrails_present():
    """Ensure runtime bootstrap yields expected columns/indexes for fitment flows."""
    engine, factory = _make_catalog_session()
    try:
        async with factory() as db:
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
async def test_fitment_merge_output_contract_and_repeat_stability():
    """Merge task should keep a stable output contract and trend toward idempotence."""
    engine, factory = _make_catalog_session()
    try:
        async with factory() as db:
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
