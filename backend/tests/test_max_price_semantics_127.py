"""
Script: tests/test_max_price_semantics_127.py
Purpose: #127 — pin the meaning of parts_catalog.max_price_ils / min_price_ils and prove every writer/reader pair agrees.
         max_price_ils = the ISRAELI CONSUMER REFERENCE incl. 18% VAT (price_policy.catalog_estimate reads it as a LOCAL cost:
         max / 1.18); it is written only from eligible LOCAL offers and never from a foreign supplier. min_price_ils is
         internal: the cheapest eligible offer incl. the VAT that offer carries.
         Earlier defects: refresh_min_max_prices wrote max = highest offer of ANY supplier / class / activity x 1.18, min = 0
         from a no-price offer, and (first fix) a bound VAT parameter typed integer by Postgres turned 1.18 into 1.
Process: pure checks + isolated DB only (tests/isolated_db_guard.py); the DB tests remove exactly the rows they create.
Data Imported/Modified: test suppliers/parts/offers in the disposable catalog DB (removed afterwards).
Last Updated: 2026-11-03
"""
from __future__ import annotations

import os
import re
import uuid

import pytest

import isolated_db_guard as guard
import price_policy as pp

_REFUSAL = guard.url_refusal(os.getenv("DATABASE_URL"))
db_only = pytest.mark.skipif(bool(_REFUSAL), reason=f"isolated DB required: {_REFUSAL}")

_CLEANUP = [
    "CREATE TEMP TABLE _tp ON COMMIT DROP AS SELECT id FROM parts_catalog WHERE sku LIKE 'M127%'",
    "DELETE FROM supplier_parts WHERE part_id IN (SELECT id FROM _tp)",
    "DELETE FROM parts_catalog WHERE id IN (SELECT id FROM _tp)",
    "DELETE FROM suppliers WHERE name ~ '^M127 ' AND id NOT IN (SELECT supplier_id FROM supplier_parts)",
]


@pytest.fixture(autouse=True)
async def _remove_test_rows():
    yield
    if _REFUSAL:
        return
    from sqlalchemy import text
    from BACKEND_DATABASE_MODELS import async_session_factory, engine
    async with async_session_factory() as db:
        assert await guard.live_refusal(db) is None
        for stmt in _CLEANUP:
            await db.execute(text(stmt))
        await db.commit()
    await engine.dispose()


@pytest.fixture
async def db():
    from BACKEND_DATABASE_MODELS import async_session_factory, engine
    async with async_session_factory() as session:
        assert await guard.live_refusal(session) is None
        yield session
    await engine.dispose()


async def _brand(db):
    from sqlalchemy import text
    row = (await db.execute(text("SELECT id::text FROM car_brands LIMIT 1"))).first()
    if not row:
        pytest.skip("sandbox has no car_brands row")
    return row[0]


async def _sup(db, name, country, active=True):
    from sqlalchemy import text
    sid = str(uuid.uuid4())
    # #129/#131: a LOCAL supplier whose VAT basis is unknown/NULL contributes nothing to prices (price_policy.vat_basis_blocks_sql);
    # production local suppliers carry a classified basis, so the fixture classifies its local supplier ('exclusive' = cost ex-VAT).
    await db.execute(text("INSERT INTO suppliers (id, name, country, is_active, vat_price_basis) VALUES (CAST(:i AS uuid), :n, :c, :a, :b)"),
                     {"i": sid, "n": f"M127 {name} {sid[:6]}", "c": country, "a": active, "b": "exclusive" if country == "IL" else None})
    return sid


async def _part(db, bid, part_type="aftermarket", **cols):
    from sqlalchemy import text
    pid = str(uuid.uuid4())
    await db.execute(text("INSERT INTO parts_catalog (id, sku, name, manufacturer_id, part_type, max_price_ils, importer_price_ils, "
                          "online_price_ils, is_active, created_at, updated_at) VALUES (CAST(:i AS uuid), :k, 'm127', "
                          "CAST(:m AS uuid), :t, :mx, :imp, :onl, TRUE, NOW(), NOW())"),
                     {"i": pid, "k": "M127" + pid[:10], "m": bid, "t": part_type, "mx": cols.get("max_price"),
                      "imp": cols.get("importer"), "onl": cols.get("online")})
    return pid


async def _offer(db, pid, sid, price, url="https://x.example/p", available=True):
    from sqlalchemy import text
    await db.execute(text("INSERT INTO supplier_parts (id, part_id, supplier_id, supplier_sku, price_ils, price_usd, is_available, "
                          "supplier_url, updated_at) VALUES (gen_random_uuid(), CAST(:p AS uuid), CAST(:s AS uuid), :k, :pr, 0, "
                          ":a, :u, NOW())"), {"p": pid, "s": sid, "k": uuid.uuid4().hex[:12], "pr": price, "a": available, "u": url})


async def _cols(db, pid):
    from sqlalchemy import text
    r = (await db.execute(text("SELECT min_price_ils, max_price_ils FROM parts_catalog WHERE id = CAST(:i AS uuid)"), {"i": pid})).first()
    return tuple(None if v is None else float(v) for v in r)


# ── the VAT-factor regression (first fix: a bound :vat typed integer -> 1.18 became 1) ───────────────────────────────
@db_only
async def test_vat_factor_is_applied_not_truncated_to_one(db):
    import db_update_agent as D
    bid = await _brand(db)
    il = await _sup(db, "Local", "IL")
    pid = await _part(db, bid)
    await _offer(db, pid, il, 200)
    await db.commit()
    await D.refresh_min_max_prices(db)
    assert await _cols(db, pid) == (236.0, 236.0)          # 200 x 1.18, NOT 200 x 1


def test_sql_casts_the_vat_parameter_to_numeric():
    """Static twin of the regression: an uncast :vat sharing a CASE with an integer literal is typed integer."""
    import inspect
    import db_update_agent as D
    src = inspect.getsource(D.refresh_min_max_for_parts)
    assert "CAST(:vat AS numeric)" in src
    assert not re.search(r":vat(?!\w)(?<!CAST\(:vat AS numeric)", src.replace("CAST(:vat AS numeric)", "")), "bare :vat left"
    assert "refresh_min_max_for_parts" in inspect.getsource(D.refresh_min_max_prices), "the task must use the shared function"


# ── writer -> reader agreement ────────────────────────────────────────────────────────────────────────────────────────
@db_only
async def test_a_local_part_the_estimate_reads_back_exactly_the_offer_price(db):
    """refresh writes max for a LOCAL-only part; the policy estimate (the only customer reader) reproduces the price the
    same offer would have had as a live offer."""
    import db_update_agent as D
    bid = await _brand(db)
    il = await _sup(db, "Local", "IL")
    pid = await _part(db, bid)
    await _offer(db, pid, il, 100, url="")                      # no listing URL -> not customer-visible -> estimate path
    await db.commit()
    await D.refresh_min_max_prices(db)
    mn, mx = await _cols(db, pid)
    assert (mn, mx) == (118.0, 118.0)
    est = pp.catalog_estimate_price(None, mx, None)
    assert est.total == pp.unit_price(100, supplier_country="IL").total == 171.1


@db_only
async def test_a_foreign_only_part_is_never_given_a_consumer_reference(db):
    import db_update_agent as D
    bid = await _brand(db)
    us = await _sup(db, "Foreign", "US")
    pid = await _part(db, bid, online=300)
    await _offer(db, pid, us, 300, url="")
    await db.commit()
    await D.refresh_min_max_prices(db)
    mn, mx = await _cols(db, pid)
    assert mx is None and mn == 300.0                          # min follows the offer at 0% VAT; max stays empty
    est = pp.catalog_estimate_price(None, mx, 300)             # the estimate falls to the FOREIGN input: 0% VAT
    assert est.vat == 0.0 and est.total == 435.0


@db_only
async def test_class_incompatible_and_ineligible_offers_cannot_move_max(db):
    import db_update_agent as D
    from sqlalchemy import text
    bid = await _brand(db)
    il, il2, il3, off = (await _sup(db, "Local", "IL"), await _sup(db, "Local2", "IL"), await _sup(db, "Local3", "IL"),
                         await _sup(db, "Off", "IL", active=False))
    pid = await _part(db, bid, part_type="oem")
    await _offer(db, pid, il, 100)
    # another product class (aftermarket offer on an OEM part) must not set max
    await db.execute(text("INSERT INTO supplier_parts (id, part_id, supplier_id, supplier_sku, price_ils, price_usd, is_available, "
                          "part_type, supplier_url, updated_at) VALUES (gen_random_uuid(), CAST(:p AS uuid), CAST(:s AS uuid), 'x-alt', "
                          "9000, 0, TRUE, 'aftermarket', 'https://x', NOW())"), {"p": pid, "s": il2})
    await _offer(db, pid, off, 9999)                            # inactive supplier
    # unavailable (its own supplier: one untyped offer per supplier+part is a DB invariant - uq_sp_part_supplier_untyped, and the
    # old uq_supplier_parts_part_supplier before it - so a second offer of `il` for the same part cannot exist in production)
    await _offer(db, pid, il3, 8888, available=False)
    await db.commit()
    await D.refresh_min_max_prices(db)
    assert await _cols(db, pid) == (118.0, 118.0)


@db_only
async def test_repeated_refresh_is_stable_and_a_price_change_is_followed(db):
    import db_update_agent as D
    from sqlalchemy import text
    bid = await _brand(db)
    il = await _sup(db, "Local", "IL")
    pid = await _part(db, bid)
    await _offer(db, pid, il, 100)
    await db.commit()
    for _ in range(2):
        await D.refresh_min_max_prices(db)
        assert await _cols(db, pid) == (118.0, 118.0)
    await db.execute(text("UPDATE supplier_parts SET price_ils = 150, updated_at = NOW() WHERE part_id = CAST(:p AS uuid)"), {"p": pid})
    await db.commit()
    await D.refresh_min_max_prices(db)
    assert await _cols(db, pid) == (177.0, 177.0)


# ── one definition of 'local' ─────────────────────────────────────────────────────────────────────────────────────────
def test_cleanup_agent_and_refresh_share_the_policy_sql_for_local():
    import inspect
    import db_cleanup_agent as C
    import db_update_agent as D
    assert C._local_supplier_sql() == pp.local_supplier_sql("s")
    assert "pp.local_supplier_sql" in inspect.getsource(D.refresh_min_max_for_parts)


@db_only
async def test_the_scrapers_per_part_update_uses_the_same_function_and_policy(db):
    """catalog_scraper's per-part min/max update calls refresh_min_max_for_parts (no second formula): a part with a local
    and a higher foreign offer gets max from the LOCAL offer only."""
    import db_update_agent as D
    bid = await _brand(db)
    il, us = await _sup(db, "Local", "IL"), await _sup(db, "Foreign", "US")
    pid = await _part(db, bid)
    await _offer(db, pid, il, 100)
    await _offer(db, pid, us, 900)
    await db.commit()
    n = await D.refresh_min_max_for_parts(db, [pid], 3.72)
    await db.commit()
    assert n == 1
    assert await _cols(db, pid) == (118.0, 118.0)     # min = cheapest incl. its VAT; max = local only (not 900 x 1.18)
