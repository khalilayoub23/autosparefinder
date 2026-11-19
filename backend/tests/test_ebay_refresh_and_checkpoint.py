"""Durable sync cursor + eBay refresh of existing offers (2026-11-18). DB-backed cases run on the isolated DB only."""
import json
import uuid
from unittest.mock import AsyncMock

import pytest
import sqlalchemy as sa

import services.ebay_price_sync as eb
from services import sync_checkpoint as cp
from services.ebay_listing_identity import Live
from test_ebay_price_sync_lookup import db, _world, _offer, _FakeEbay, _Res  # noqa: F401  (fixtures + helpers)


def test_checkpoint_roundtrip_atomic_and_survives_corruption(tmp_path, monkeypatch):
    monkeypatch.setattr(cp, "_PATH", tmp_path / "c.json")
    assert cp.load("k", "dflt") == "dflt"
    cp.save("k", "v1"); cp.save("other", 2)
    assert cp.load("k") == "v1" and cp.load("other") == 2
    assert not list(tmp_path.glob(".sync_cursors.*"))            # no temp litter
    (tmp_path / "c.json").write_text("{torn")                    # corrupt file reads as default, never raises
    assert cp.load("k", "dflt") == "dflt"
    cp.save("k", "v2")
    assert json.loads((tmp_path / "c.json").read_text()) == {"k": "v2"}


def test_cursor_is_not_process_memory():
    assert not hasattr(eb, "_EBAY_SYNC_LAST_ID")


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setattr(cp, "_PATH", tmp_path / "c.json")
    monkeypatch.setattr(eb, "get_usd_to_ils_rate", AsyncMock(return_value=3.7))
    monkeypatch.setattr(eb, "_meili_sync_part", AsyncMock(return_value=True))
    monkeypatch.setattr(eb.asyncio, "sleep", AsyncMock())
    fake = _FakeEbay(); monkeypatch.setattr(eb, "ebay", fake)
    monkeypatch.setenv("EBAY_REFRESH_LIMIT", "5000")
    return fake


STORED_URL = "https://www.ebay.com/itm/123456789012?hash=x"


def _live(**kw):
    base = dict(ok=True, status="ok", item_id="123456789012", title="Genuine part", seller="s", condition="New", price=10.0,
                currency="USD", shipping=5.0, ships_to_israel=True, raw={})
    base.update(kw)
    return Live(**base)


def _serve(fake, live):
    calls = []
    async def get_listing(lid):
        calls.append(lid)
        return live
    fake.get_listing = get_listing
    return calls


async def _prep(db, monkeypatch):
    sup, pid, oem = await _world(db, monkeypatch)
    await db.execute(sa.text("UPDATE suppliers SET is_active=TRUE WHERE id=CAST(:s AS uuid)"), {"s": sup})
    # only OUR offers may be due: push every other eBay offer's check into the future
    await db.execute(sa.text("UPDATE supplier_parts SET last_checked_at = NOW() WHERE supplier_id=CAST(:s AS uuid)"), {"s": sup})
    return sup, pid, oem


async def _mk(db, sup, pid, oem, price=50, ptype=None, url=STORED_URL, days=60):
    oid = await _offer(db, sup, pid, oem, ptype, price)
    await db.execute(sa.text("UPDATE supplier_parts SET supplier_url=:u, shipping_cost_usd=7, shipping_cost_ils=25.9, "
                             "last_checked_at = NOW() - make_interval(days => :d) WHERE id=CAST(:i AS uuid)"), {"u": url, "i": oid, "d": days})
    await db.execute(sa.text("UPDATE parts_catalog SET base_price=99 WHERE id=CAST(:p AS uuid)"), {"p": pid})  # priced: not an 'unpriced' target
    return oid


async def _row(db, oid):
    return (await db.execute(sa.text("SELECT price_ils::float, is_available, supplier_url, shipping_cost_usd::float, availability "
                                     "FROM supplier_parts WHERE id=CAST(:i AS uuid)"), {"i": oid})).one()


async def _check(db, oid):
    return (await db.execute(sa.text("SELECT verdict, retry_after > NOW() FROM supplier_offer_checks WHERE supplier_part_id=CAST(:i AS uuid)"), {"i": oid})).first()


async def test_same_listing_price_change_is_written_with_history_and_an_exact_match_verdict(db, monkeypatch, _env):
    sup, pid, oem = await _prep(db, monkeypatch)
    oid = await _mk(db, sup, pid, oem)
    calls = _serve(_env, _live(price=10.0, shipping=5.0))
    rep = await eb.sync_ebay_prices(db, limit_per_run=1)
    assert calls == ["123456789012"]                      # fetched BY ID (no OEM search for a refresh)
    assert rep["refresh_updated"] == 1 and rep["refresh_verdicts"] == {"exact_match": 1}
    r = await _row(db, oid)
    assert r[0] == pytest.approx(10.0 * 3.7, rel=1e-3) and r[3] == 5.0
    assert (await _check(db, oid))[0] == "exact_match"
    h = (await db.execute(sa.text("SELECT old_price_ils::float, source FROM price_history WHERE supplier_part_id=CAST(:i AS uuid)"), {"i": oid})).one()
    assert h[0] == 50.0 and h[1] == "ebay_sync"


async def test_large_legitimate_increase_is_not_rejected(db, monkeypatch, _env):
    sup, pid, oem = await _prep(db, monkeypatch)
    oid = await _mk(db, sup, pid, oem, price=50)
    _serve(_env, _live(price=5000.0))
    await eb.sync_ebay_prices(db, limit_per_run=1)
    assert (await _row(db, oid))[0] == pytest.approx(5000.0 * 3.7, rel=1e-3)


async def test_not_due_offer_is_not_fetched(db, monkeypatch, _env):
    sup, pid, oem = await _prep(db, monkeypatch)
    oid = await _mk(db, sup, pid, oem, days=1)
    calls = _serve(_env, _live())
    await eb.sync_ebay_prices(db, limit_per_run=1)
    assert calls == [] and (await _row(db, oid))[0] == 50.0


async def test_missing_listing_keeps_price_stops_being_purchasable_and_is_recorded(db, monkeypatch, _env):
    sup, pid, oem = await _prep(db, monkeypatch)
    oid = await _mk(db, sup, pid, oem)
    _serve(_env, Live(ok=False, status="ended"))
    rep = await eb.sync_ebay_prices(db, limit_per_run=1)
    r = await _row(db, oid)
    assert r[0] == 50.0 and r[1] is False and r[4] == "on_order" and rep["refresh_no_listing"] == 1
    assert (await _check(db, oid)) == ("missing_listing", True)


async def test_missing_listing_is_retried_after_its_retry_date_not_30_days(db, monkeypatch, _env):
    sup, pid, oem = await _prep(db, monkeypatch)
    oid = await _mk(db, sup, pid, oem, days=2)            # last_checked 2 days ago: NOT due by the 30-day rule
    await db.execute(sa.text("INSERT INTO supplier_offer_checks (supplier_part_id, verdict, reason, retry_after) "
                             "VALUES (CAST(:i AS uuid), 'missing_listing', 'x', NOW() - interval '1 hour')"), {"i": oid})
    calls = _serve(_env, _live(price=11.0))
    await eb.sync_ebay_prices(db, limit_per_run=1)
    assert calls == ["123456789012"] and (await _row(db, oid))[1] is True      # listing is back -> offer purchasable again


async def test_api_error_changes_nothing_and_is_retried_soon(db, monkeypatch, _env):
    sup, pid, oem = await _prep(db, monkeypatch)
    oid = await _mk(db, sup, pid, oem)
    _serve(_env, Live(ok=False, status="error"))
    await eb.sync_ebay_prices(db, limit_per_run=1)
    r = await _row(db, oid)
    assert r[0] == 50.0 and r[1] is True and (await _check(db, oid))[0] == "ambiguous_identity"


async def test_a_different_listing_in_the_response_is_never_written(db, monkeypatch, _env):
    sup, pid, oem = await _prep(db, monkeypatch)
    oid = await _mk(db, sup, pid, oem)
    _serve(_env, _live(item_id="999999999999", price=1.0))
    rep = await eb.sync_ebay_prices(db, limit_per_run=1)
    r = await _row(db, oid)
    assert r[0] == 50.0 and r[2] == STORED_URL and rep["refresh_listing_changed"] == 1
    assert (await _check(db, oid))[0] == "different_listing"


async def test_seller_mismatch_is_not_written(db, monkeypatch, _env):
    sup, pid, oem = await _prep(db, monkeypatch)
    oid = await _mk(db, sup, pid, oem)
    await db.execute(sa.text("UPDATE parts_catalog SET specifications = jsonb_build_object('ebay', jsonb_build_object('seller','shopA','item_url',CAST(:u AS text))) WHERE id=CAST(:p AS uuid)"), {"p": pid, "u": STORED_URL})
    _serve(_env, _live(seller="shopB", price=1.0))
    await eb.sync_ebay_prices(db, limit_per_run=1)
    assert (await _row(db, oid))[0] == 50.0 and (await _check(db, oid))[0] == "seller_mismatch"


async def test_condition_change_is_not_written(db, monkeypatch, _env):
    sup, pid, oem = await _prep(db, monkeypatch)
    oid = await _mk(db, sup, pid, oem)
    _serve(_env, _live(condition="Used", price=1.0))
    await eb.sync_ebay_prices(db, limit_per_run=1)
    assert (await _row(db, oid))[0] == 50.0 and (await _check(db, oid))[0] == "variant_mismatch"


async def test_foreign_currency_is_not_written(db, monkeypatch, _env):
    sup, pid, oem = await _prep(db, monkeypatch)
    oid = await _mk(db, sup, pid, oem)
    _serve(_env, _live(currency="EUR", price=1.0))
    await eb.sync_ebay_prices(db, limit_per_run=1)
    assert (await _row(db, oid))[0] == 50.0 and (await _check(db, oid))[0] == "currency_ambiguous"


async def test_unknown_shipping_keeps_the_stored_shipping_and_never_touches_the_price_base(db, monkeypatch, _env):
    sup, pid, oem = await _prep(db, monkeypatch)
    oid = await _mk(db, sup, pid, oem)
    _serve(_env, _live(price=10.0, shipping=None))
    await eb.sync_ebay_prices(db, limit_per_run=1)
    r = await _row(db, oid)
    assert r[3] == 7.0 and r[0] == pytest.approx(37.0, rel=1e-3)      # shipping kept; price from the item alone


async def test_shipping_change_alone_does_not_corrupt_the_item_price(db, monkeypatch, _env):
    sup, pid, oem = await _prep(db, monkeypatch)
    oid = await _mk(db, sup, pid, oem, price=37.0)
    _serve(_env, _live(price=10.0, shipping=99.0))
    await eb.sync_ebay_prices(db, limit_per_run=1)
    r = await _row(db, oid)
    assert r[0] == pytest.approx(37.0, rel=1e-3) and r[3] == 99.0


async def test_offer_without_a_stored_listing_is_reported_not_guessed(db, monkeypatch, _env):
    sup, pid, oem = await _prep(db, monkeypatch)
    oid = await _mk(db, sup, pid, oem, url="https://example.com/nothing")
    calls = _serve(_env, _live())
    await eb.sync_ebay_prices(db, limit_per_run=1)
    assert calls == [] and (await _row(db, oid))[0] == 50.0 and (await _check(db, oid))[0] == "no_stored_listing"


async def test_typed_variant_offer_is_never_refreshed(db, monkeypatch, _env):
    sup, pid, oem = await _prep(db, monkeypatch)
    oid = await _mk(db, sup, pid, oem, ptype="original")
    calls = _serve(_env, _live())
    await eb.sync_ebay_prices(db, limit_per_run=1)
    assert calls == [] and (await _row(db, oid))[0] == 50.0


async def test_only_that_offer_goes_unavailable_not_the_other_variant_on_the_part(db, monkeypatch, _env):
    sup, pid, oem = await _prep(db, monkeypatch)
    oid = await _mk(db, sup, pid, oem)
    other = await _offer(db, sup, pid, "OTHER-" + oem, "original", 70)
    _serve(_env, Live(ok=False, status="ended"))
    await eb.sync_ebay_prices(db, limit_per_run=1)
    assert (await _row(db, oid))[1] is False and (await _row(db, other))[1] is True


async def test_call_budget_exhaustion_stops_the_run_and_leaves_the_rest_due(db, monkeypatch, _env):
    sup, pid, oem = await _prep(db, monkeypatch)
    oid = await _mk(db, sup, pid, oem)
    calls = _serve(_env, _live())
    monkeypatch.setenv("EBAY_RUN_CALL_BUDGET", "0")
    rep = await eb.sync_ebay_prices(db, limit_per_run=1)
    assert rep["call_budget_hit"] is True and calls == [] and (await _row(db, oid))[0] == 50.0 and (await _check(db, oid)) is None


async def test_discovery_does_not_replace_a_live_different_listing(db, monkeypatch, _env):
    """Population 2 (unpriced parts): a search hit from ANOTHER listing never overwrites an offer whose own listing is not verified gone."""
    sup, pid, oem = await _prep(db, monkeypatch)
    oid = await _offer(db, sup, pid, oem, None, 50)
    await db.execute(sa.text("UPDATE supplier_parts SET supplier_url='https://www.ebay.com/itm/111111111111' WHERE id=CAST(:i AS uuid)"), {"i": oid})
    await db.execute(sa.text("UPDATE parts_catalog SET base_price=0 WHERE id=CAST(:p AS uuid)"), {"p": pid})
    monkeypatch.setenv("EBAY_REFRESH_LIMIT", "0")
    eb.sync_checkpoint.save(eb._CURSOR_KEY, "00000000-0000-0000-0000-000000000000")
    _env.price = 10.0          # fake search hit = listing 123456789012
    rep = await eb.sync_ebay_prices(db, limit_per_run=5000)
    assert (await _row(db, oid))[0] == 50.0 and rep["candidate_verdicts"].get("different_listing", 0) >= 1


async def test_discovery_rejects_a_hit_that_does_not_name_the_part(db, monkeypatch, _env):
    sup, pid, oem = await _prep(db, monkeypatch)
    await db.execute(sa.text("UPDATE parts_catalog SET base_price=0 WHERE id=CAST(:p AS uuid)"), {"p": pid})
    monkeypatch.setenv("EBAY_REFRESH_LIMIT", "0")
    eb.sync_checkpoint.save(eb._CURSOR_KEY, "00000000-0000-0000-0000-000000000000")
    async def other(pn, limit=3):
        r = _Res(10.0, "SOMETHING-ELSE"); return [r]
    _env.search_by_oem = other
    rep = await eb.sync_ebay_prices(db, limit_per_run=5000)
    n = (await db.execute(sa.text("SELECT count(*) FROM supplier_parts WHERE part_id=CAST(:p AS uuid) AND supplier_id=CAST(:s AS uuid)"), {"p": pid, "s": sup})).scalar()
    assert n == 0 and rep["candidate_verdicts"].get("product_mismatch", 0) >= 1
