"""#107 regression: db_update_agent.flag_fake_skus must not load the catalogue into the backend process.

It used to SELECT every candidate (4.27M rows on production) with fetchall() — ~0.7-1.2 GB inside uvicorn on every
run_all_tasks cycle, which together with the daily group scan's browser pushed the 4 GB backend container into OOM
kills (2026-10-23, 2026-10-25). It now reads keyset batches. These tests prove (1) the same rows are flagged as by the
old algorithm, (2) memory stays bounded and far below the old full load on the same data, (3) the bound is in the code.
Isolated database only (tests/isolated_db_guard.py): static refusal skips, live refusal fails before any write.
"""
import ast
import asyncio
import os
import tracemalloc
import uuid

import pytest

import isolated_db_guard as g

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REFUSAL = g.static_refusal()
TAG = "ZZFFS" + uuid.uuid4().hex[:6].upper()


def test_the_task_reads_bounded_keyset_batches():
    src = open(os.path.join(BACKEND, "db_update_agent.py"), encoding="utf-8").read()
    fn = next(n for n in ast.parse(src).body if isinstance(n, ast.AsyncFunctionDef) and n.name == "flag_fake_skus")
    body = ast.get_source_segment(src, fn)
    assert "LIMIT :n" in body and "id > :last_id" in body and "ORDER BY id" in body
    assert "FOR UPDATE SKIP LOCKED" in body


async def _seed(conn, n_real, n_fake, n_flagged):
    """n_real real-looking SKUs, n_fake auto-generated ones, n_flagged fake ones already flagged (own brand row)."""
    brand = await conn.fetchval("INSERT INTO car_brands (id, name) VALUES (gen_random_uuid(), $1) RETURNING id",
                                f"zz-ffs-brand-{TAG}")
    await conn.execute(f"""
        INSERT INTO parts_catalog (id, sku, name, manufacturer_id, is_active, needs_oem_lookup)
        SELECT gen_random_uuid(), '{TAG}' || lpad(g::text, 8, '0') || 'X', 'ffs test part', '{brand}'::uuid, TRUE, FALSE
        FROM generate_series(1, {n_real}) g""")
    await conn.execute(f"""
        INSERT INTO parts_catalog (id, sku, name, manufacturer_id, is_active, needs_oem_lookup)
        SELECT gen_random_uuid(), 'PART-' || (900000000 + g)::text, 'ffs test part {TAG}', '{brand}'::uuid, TRUE, FALSE
        FROM generate_series(1, {n_fake}) g""")
    await conn.execute(f"""
        INSERT INTO parts_catalog (id, sku, name, manufacturer_id, is_active, needs_oem_lookup)
        SELECT gen_random_uuid(), 'SKU-' || (800000000 + g)::text, 'ffs test part {TAG}', '{brand}'::uuid, TRUE, TRUE
        FROM generate_series(1, {n_flagged}) g""")


async def _cleanup(conn):
    await conn.execute(f"DELETE FROM parts_catalog WHERE sku LIKE '{TAG}%' OR name = 'ffs test part {TAG}'")
    await conn.execute("DELETE FROM car_brands WHERE name = $1", f"zz-ffs-brand-{TAG}")


def _old_algorithm_ids(rows, is_fake):
    """The pre-fix algorithm, as a reference: every candidate in memory, Python predicate."""
    return {pid for pid, sku in rows if is_fake(sku)}


@pytest.mark.skipif(REFUSAL is not None, reason=f"isolated DB only: {REFUSAL}")
def test_same_rows_flagged_and_memory_bounded(monkeypatch):
    import asyncpg
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import NullPool
    import db_update_agent as dua

    url = os.environ["DATABASE_URL"]
    dsn = url.replace("postgresql+asyncpg://", "postgresql://")
    monkeypatch.setattr(dua, "_FLAG_FAKE_SKUS_BATCH", 2_000, raising=False)

    async def run():
        conn = await asyncpg.connect(dsn)
        try:
            why = await g.live_refusal(_AsyncpgAdapter(conn))
            if why:
                pytest.fail(f"REFUSED before any write: {why}")
            await _cleanup(conn)
            await _seed(conn, n_real=50_000, n_fake=1_500, n_flagged=300)
            # reference: what the OLD code would load and flag (same predicate, whole candidate set)
            tracemalloc.start()
            cand = await conn.fetch("SELECT id, sku FROM parts_catalog WHERE sku IS NOT NULL "
                                    "AND (needs_oem_lookup IS NULL OR needs_oem_lookup = FALSE)")
            cand = [(r["id"], r["sku"]) for r in cand]
            old_peak = tracemalloc.get_traced_memory()[1]
            tracemalloc.stop()
            expected = _old_algorithm_ids(cand, dua._is_fake_sku)
            del cand
            seeded_fake = {r["id"] for r in await conn.fetch(
                f"SELECT id FROM parts_catalog WHERE name = 'ffs test part {TAG}' AND sku LIKE 'PART-%'")}
            assert seeded_fake <= expected

            engine = create_async_engine(url, poolclass=NullPool)
            try:
                async with sessionmaker(engine, class_=AsyncSession)() as db:
                    tracemalloc.start()
                    result = await dua.flag_fake_skus(db)
                    new_peak = tracemalloc.get_traced_memory()[1]
                    tracemalloc.stop()
            finally:
                await engine.dispose()
            assert result["status"] == "ok", result

            flagged_now = {r["id"] for r in await conn.fetch(
                "SELECT id FROM parts_catalog WHERE id = ANY($1::uuid[]) AND needs_oem_lookup", list(expected))}
            assert flagged_now == expected                     # exactly the rows the old algorithm flags
            untouched = await conn.fetchval(
                f"SELECT count(*) FROM parts_catalog WHERE sku LIKE '{TAG}%' AND needs_oem_lookup")
            assert untouched == 0                              # real SKUs are never flagged
            assert await conn.fetchval(
                f"SELECT count(*) FROM parts_catalog WHERE name = 'ffs test part {TAG}' AND sku LIKE 'SKU-%' "
                "AND needs_oem_lookup") == 300                # already-flagged rows stay flagged
            print(f"\n[#107] old full-load peak={old_peak/2**20:.1f} MiB  new batched peak={new_peak/2**20:.1f} MiB")
            assert new_peak < old_peak / 4, (old_peak, new_peak)    # bounded: far below loading every candidate
            assert result.get("batches", 0) >= 25                  # really batched (>= 51,500 rows / 2,000)
        finally:
            await _cleanup(conn)
            await conn.close()

    asyncio.run(run())


class _AsyncpgAdapter:
    """live_refusal() takes a SQLAlchemy-style session; adapt a raw asyncpg connection (read-only checks)."""

    def __init__(self, conn):
        self.conn = conn

    async def execute(self, stmt, params=None):
        value = await self.conn.fetchval(str(stmt))

        class R:
            def scalar(_self):
                return value
        return R()
