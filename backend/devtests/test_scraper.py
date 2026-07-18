#!/usr/bin/env python3
"""
Test suite for catalog_scraper.py (REX brand discovery pipeline).

Tests:
  1. agent_todos — 27 missing brands loaded for rex, HIGH priority first
  2. Source 0   — opel local seed import runs and inserts parts
  3. Discovery  — run_brand_discovery() for Opel, verify parts appear in catalog
  4. todo_requests_ranked_first — HIGH todos trigger ranked-first mode
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import asyncpg

DB_DSN = "postgresql://autospare:e4b79d75ca640dbe7f259618f078b82f21573e419308f668beed5e20b26b1d43@postgres_catalog:5432/autospare"

_PASS = "\033[32mPASS\033[0m"
_FAIL = "\033[31mFAIL\033[0m"

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    icon = _PASS if ok else _FAIL
    print(f"  [{icon}] {name}" + (f": {detail}" if detail else ""))


async def test_agent_todos(conn: asyncpg.Connection) -> None:
    print("\n── Test 1: agent_todos ─────────────────────────────────────")
    rows = await conn.fetch(
        """
        SELECT title, priority, status
        FROM agent_todos
        WHERE assigned_to_agent = 'rex'
          AND category = 'catalog_discovery'
        ORDER BY CASE priority WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END
        """
    )
    check("27 catalog_discovery todos exist for rex", len(rows) >= 27, f"found={len(rows)}")

    high_todos = [r for r in rows if r["priority"] == "high"]
    check("9 HIGH priority todos", len(high_todos) == 9, f"found={len(high_todos)}")

    brands = [r["title"].split(":")[1].strip().split("(")[0].strip() for r in rows]
    check("Opel in todos", any("Opel" in b for b in brands))
    check("Isuzu in todos", any("Isuzu" in b for b in brands))
    check("Lexus in todos", any("Lexus" in b for b in brands))

    not_started = [r for r in rows if r["status"] == "not_started"]
    check("All new todos are not_started", len(not_started) == len(rows), f"not_started={len(not_started)}")

    # Verify ranked_first mode is set in HIGH todos
    high_with_mode = await conn.fetch(
        """
        SELECT id FROM agent_todos
        WHERE assigned_to_agent='rex' AND category='catalog_discovery'
          AND priority='high'
          AND artifacts->>'mode' = 'ranked_first'
        """
    )
    check("HIGH todos have mode=ranked_first", len(high_with_mode) == 9, f"found={len(high_with_mode)}")


async def test_local_seed_registry() -> None:
    print("\n── Test 2: local seed registry ─────────────────────────────")
    sys.path.insert(0, str(Path(__file__).parent))

    # Force reload so _init_local_seed_registry() re-evaluates with the data dir
    import importlib
    import catalog_scraper  # noqa: F401
    importlib.invalidate_caches()

    from catalog_scraper import _LOCAL_SEED_REGISTRY
    check("Opel registered in _LOCAL_SEED_REGISTRY", "Opel" in _LOCAL_SEED_REGISTRY,
          f"keys={list(_LOCAL_SEED_REGISTRY.keys())}")

    if "Opel" in _LOCAL_SEED_REGISTRY:
        opel_path = _LOCAL_SEED_REGISTRY["Opel"]
        check("Opel seed file exists on disk", opel_path.exists(), str(opel_path))
        if opel_path.exists():
            import json
            data = json.loads(opel_path.read_text())
            products = data.get("products", [])
            check("Opel seed has products", len(products) > 0, f"products={len(products)}")


async def test_source_0_opel(conn: asyncpg.Connection) -> None:
    print("\n── Test 3: Source 0 — Opel local seed import ───────────────")
    from pathlib import Path as _Path
    sys.path.insert(0, "/app")

    seed = _Path(__file__).parent.parent / "data" / "opel_car_parts_ie_seed.json"
    check("Seed file accessible from container", seed.exists(), str(seed))
    if not seed.exists():
        check("Opel import skipped — seed missing", False)
        return

    from opel_car_parts_ie_import import import_file  # type: ignore
    result = await import_file(seed)
    check("import_file returned dict", isinstance(result, dict), str(result))
    check("parts_inserted >= 0", result.get("parts_inserted", -1) >= 0,
          f"inserted={result.get('parts_inserted')}")
    check("fitment_rows >= 0", result.get("fitment_rows", -1) >= 0,
          f"fitment={result.get('fitment_rows')}")

    # Verify parts are in catalog
    opel_count = await conn.fetchval(
        "SELECT COUNT(*) FROM parts_catalog WHERE manufacturer = 'Opel' AND is_active = TRUE"
    )
    check("Opel parts exist in catalog after import", opel_count > 0, f"count={opel_count}")

    # Verify supplier_parts rows
    supplier_count = await conn.fetchval(
        """
        SELECT COUNT(*) FROM supplier_parts sp
        JOIN suppliers s ON s.id = sp.supplier_id
        WHERE s.name = 'Car-Parts.ie'
        """
    )
    check("Car-Parts.ie supplier_parts rows exist", supplier_count > 0, f"rows={supplier_count}")


async def test_todo_requests_ranked_first(conn: asyncpg.Connection) -> None:
    print("\n── Test 4: todo_requests_ranked_first() ────────────────────")
    from agent_todo_utils import get_active_agent_todos, todo_requests_ranked_first

    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
    from sqlalchemy import text
    import os

    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        check("DATABASE_URL set", False, "skipping SQLAlchemy tests")
        return

    engine = create_async_engine(db_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        todos = await get_active_agent_todos(db, "rex")
        catalog_todos = [t for t in todos if t.get("category") == "catalog_discovery"]
        check("catalog_discovery todos returned by get_active_agent_todos", len(catalog_todos) > 0,
              f"count={len(catalog_todos)}")
        ranked = todo_requests_ranked_first(catalog_todos)
        check("todo_requests_ranked_first returns True for HIGH catalog todos", ranked)
    await engine.dispose()


async def main() -> int:
    print("=" * 60)
    print("AutoSpareFinder — Scraper (REX) Test Suite")
    print("=" * 60)

    conn = await asyncpg.connect(DB_DSN)
    try:
        await test_agent_todos(conn)
        await test_local_seed_registry()
        await test_source_0_opel(conn)
        await test_todo_requests_ranked_first(conn)
    finally:
        await conn.close()

    passed = sum(1 for _, ok, _ in results if ok)
    failed = sum(1 for _, ok, _ in results if not ok)
    print(f"\n{'=' * 60}")
    print(f"Results: {passed} passed, {failed} failed out of {len(results)} checks")
    print("=" * 60)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
