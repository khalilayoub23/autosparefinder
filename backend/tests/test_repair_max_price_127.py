"""
Script: tests/test_repair_max_price_127.py
Purpose: #127 data-repair script (maintenance/repair_foreign_max_price_127.py) on SYNTHETIC rows in the sandbox DB: foreign-derived
         max_price_ils is selected and nulled with a rollback record; Israeli-sourced, mixed and unmatched references are never
         touched; apply is idempotent; rollback restores the exact values; the dry run writes nothing.
Process: isolated DB only (tests/isolated_db_guard.py) - skips otherwise.
Data Imported/Modified: synthetic suppliers/parts/offers in the disposable catalog DB (removed afterwards).
Last Updated: 2026-11-03
"""
from __future__ import annotations

import os
import uuid

import pytest

import isolated_db_guard as guard

_REFUSAL = guard.url_refusal(os.getenv("DATABASE_URL"))
pytestmark = pytest.mark.skipif(bool(_REFUSAL), reason=f"isolated DB required: {_REFUSAL}")

import asyncpg  # noqa: E402

import price_policy as pp  # noqa: E402

if not hasattr(pp, "country_problem_sql"):
    pytest.skip("the repair requires #128 (price_policy.country_problem_sql): run on the 127+128 stack", allow_module_level=True)
import maintenance.repair_foreign_max_price_127 as R  # noqa: E402


@pytest.fixture
async def conn():
    dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    assert guard.url_refusal(dsn) is None
    c = await asyncpg.connect(dsn)
    yield c
    await c.execute("DELETE FROM supplier_parts WHERE part_id IN (SELECT id FROM parts_catalog WHERE sku LIKE 'R127%')")
    await c.execute("DELETE FROM parts_catalog WHERE sku LIKE 'R127%'")
    await c.execute("DELETE FROM suppliers WHERE name ~ '^R127 ' AND id NOT IN (SELECT supplier_id FROM supplier_parts)")
    await c.close()


async def _seed(c):
    bid = await c.fetchval("SELECT id FROM car_brands LIMIT 1")
    if not bid:
        pytest.skip("sandbox has no car_brands row")
    tag = uuid.uuid4().hex[:6]
    fs = await c.fetchval("INSERT INTO suppliers (id, name, country, is_active) VALUES (gen_random_uuid(), $1, 'US', TRUE) RETURNING id", f"R127 Foreign {tag}")
    ls = await c.fetchval("INSERT INTO suppliers (id, name, country, is_active) VALUES (gen_random_uuid(), $1, 'IL', TRUE) RETURNING id", f"R127 Local {tag}")
    ids = {}

    async def part(key, *, max_price, importer=None, online=None, foreign=None, local=None, url="https://x.example/p"):
        pid = await c.fetchval("INSERT INTO parts_catalog (id, sku, name, manufacturer_id, part_type, max_price_ils, importer_price_ils, online_price_ils, "
                               "is_active, created_at, updated_at) VALUES (gen_random_uuid(), $1, 'r127', $2, 'aftermarket', $3, $4, $5, TRUE, NOW(), NOW()) RETURNING id",
                               f"R127-{key}-{tag}", bid, max_price, importer, online)
        for sup, price in ((fs, foreign), (ls, local)):
            if price:
                await c.execute("INSERT INTO supplier_parts (id, part_id, supplier_id, supplier_sku, price_ils, price_usd, is_available, supplier_url, updated_at) "
                                "VALUES (gen_random_uuid(), $1, $2, $3, $4, 0, TRUE, $5, NOW())", pid, sup, f"k{key}{tag}", price, url)
        ids[key] = pid
    await part("A_x118", max_price=118.0, foreign=100)                      # foreign cost x1.18 (old refresh)      -> repair
    await part("B_raw", max_price=100.0, foreign=100)                        # foreign raw cost (old importers)      -> repair
    await part("C_x117", max_price=117.0, foreign=100)                       # OEMPartsOnline legacy x1.17           -> repair
    await part("D_mixed", max_price=153.13, foreign=129.77, local=47.2)      # has an ISRAELI offer                  -> protected
    await part("E_israeli", max_price=55.46, local=47.0)                     # Israeli-only reference               -> protected
    await part("F_unmatched", max_price=250.0, foreign=100)                  # matches no foreign signature          -> protected
    await part("G_catalog", max_price=236.0, online=200.0, foreign=None)     # catalog-level foreign x1.18, no offers -> repair
    await part("H_has_importer", max_price=236.0, importer=170.0, online=200.0)  # importer cost present                -> protected
    await part("I_nomax", max_price=None, foreign=100)                       # no max at all                         -> n/a
    us = await c.fetchval("SELECT id FROM suppliers WHERE id = $1", fs)
    unk = await c.fetchval("INSERT INTO suppliers (id, name, country, is_active) VALUES (gen_random_uuid(), $1, 'Unknown', TRUE) RETURNING id", f"R127 Unclassified {tag}")
    pid = await c.fetchval("INSERT INTO parts_catalog (id, sku, name, manufacturer_id, part_type, max_price_ils, is_active, created_at, updated_at) "
                           "VALUES (gen_random_uuid(), $1, 'r127', $2, 'aftermarket', 118.0, TRUE, NOW(), NOW()) RETURNING id", f"R127-J_unclassified-{tag}", bid)
    await c.execute("INSERT INTO supplier_parts (id, part_id, supplier_id, supplier_sku, price_ils, price_usd, is_available, supplier_url, updated_at) "
                    "VALUES (gen_random_uuid(), $1, $2, $3, 100, 0, TRUE, 'https://x', NOW())", pid, unk, f"kJ{tag}")
    ids["J_unclassified"] = pid                                              # only evidence is a supplier with country 'Unknown' -> protected
    return ids


async def _max(c, pid):
    v = await c.fetchval("SELECT max_price_ils FROM parts_catalog WHERE id = $1", pid)
    return None if v is None else float(v)


async def test_dry_run_selects_exactly_the_foreign_derived_rows_and_writes_nothing(conn):
    ids = await _seed(conn)
    before = {k: await _max(conn, v) for k, v in ids.items()}
    found = await R.select_candidates(conn)
    mine = {k for k, v in ids.items() if v in found}
    assert mine == {"A_x118", "B_raw", "C_x117", "G_catalog"}, mine
    assert {k: await _max(conn, v) for k, v in ids.items()} == before, "the dry run wrote"
    rep = await R.preview(conn, [ids[k] for k in mine])
    # G has no listing/visible offer: the customer estimate changes from the 'local' max/1.18 (VAT added) to the foreign online price (0% VAT)
    assert rep["estimate_changes"] >= 1
    before_est = pp.catalog_estimate_price(None, 236.0, 200.0).total
    after_est = pp.catalog_estimate_price(None, None, 200.0).total
    assert (before_est, after_est) == (342.2, 290.0)


async def test_apply_nulls_only_the_selected_rows_records_provenance_and_is_idempotent(conn):
    ids = await _seed(conn)
    found = await R.select_candidates(conn)
    sel = sorted(i for i in found if i in set(ids.values()))
    assert await R.apply(conn, sel, batch=2) == 4                              # small batches exercise the per-batch transaction
    for k in ("A_x118", "B_raw", "C_x117", "G_catalog"):
        assert await _max(conn, ids[k]) is None, k
    assert [await _max(conn, ids[k]) for k in ("D_mixed", "E_israeli", "F_unmatched", "H_has_importer", "J_unclassified")] == [153.13, 55.46, 250.0, 236.0, 118.0]
    rec = await conn.fetchval("SELECT specifications->'max_price_repair_127'->>'prev' FROM parts_catalog WHERE id = $1", ids["A_x118"])
    assert float(rec) == 118.0
    assert await R.apply(conn, sel) == 0                                       # second run: nothing left (records exclude them)
    assert not (set(await R.select_candidates(conn)) & set(ids.values()))


async def test_rollback_restores_the_exact_values(conn):
    ids = await _seed(conn)
    expected = {k: await _max(conn, v) for k, v in ids.items()}
    sel = sorted(i for i in await R.select_candidates(conn) if i in set(ids.values()))
    await R.apply(conn, sel)
    assert await R.rollback(conn) >= 4
    assert {k: await _max(conn, v) for k, v in ids.items()} == expected
    assert await conn.fetchval("SELECT count(*) FROM parts_catalog WHERE sku LIKE 'R127%' AND specifications ? 'max_price_repair_127'") == 0


async def test_israeli_importer_references_are_never_overwritten_by_the_selection(conn):
    """Property: no selected part has an offer from a local supplier."""
    ids = await _seed(conn)
    found = await R.select_candidates(conn)
    local_parts = {r["part_id"] for r in await conn.fetch(
        f"SELECT DISTINCT l.part_id FROM supplier_parts l JOIN suppliers s ON s.id = l.supplier_id WHERE {pp.local_supplier_sql('s')}")}
    assert not (set(found) & local_parts)
