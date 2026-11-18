"""Durable sync cursor + eBay refresh of existing offers (2026-11-18). DB-backed cases run on the isolated DB only."""
import json
import uuid
from unittest.mock import AsyncMock

import pytest
import sqlalchemy as sa

import services.ebay_price_sync as eb
from services import sync_checkpoint as cp
from test_ebay_price_sync_lookup import db, _world, _offer, _FakeEbay  # noqa: F401  (fixtures + helpers)


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
    monkeypatch.setenv("EBAY_REFRESH_ALLOW_RELISTING", "1")
    return fake


async def _prep(db, monkeypatch):
    sup, pid, oem = await _world(db, monkeypatch)
    await db.execute(sa.text("UPDATE suppliers SET is_active=TRUE WHERE id=CAST(:s AS uuid)"), {"s": sup})
    # only OUR offers may be due: push every other eBay offer's check into the future
    await db.execute(sa.text("UPDATE supplier_parts SET last_checked_at = NOW() WHERE supplier_id=CAST(:s AS uuid)"), {"s": sup})
    return sup, pid, oem


async def _due(db, oid, days=60):
    await db.execute(sa.text("UPDATE supplier_parts SET last_checked_at = NOW() - make_interval(days => :d), updated_at = NOW() - make_interval(days => :d) WHERE id=CAST(:i AS uuid)"), {"i": oid, "d": days})


async def test_due_offer_price_changes_and_history_is_written(db, monkeypatch, _env):
    sup, pid, oem = await _prep(db, monkeypatch)
    oid = await _offer(db, sup, pid, oem, None, 50)
    await db.execute(sa.text("UPDATE parts_catalog SET base_price=99 WHERE id=CAST(:p AS uuid)"), {"p": pid})  # priced: not an 'unpriced' target
    await _due(db, oid)
    _env.price = 10.0
    rep = await eb.sync_ebay_prices(db, limit_per_run=1)
    assert rep["refresh_checked"] >= 1 and rep["refresh_updated"] >= 1
    new = (await db.execute(sa.text("SELECT price_ils::float, last_checked_at > NOW() - interval '1 minute' FROM supplier_parts WHERE id=CAST(:i AS uuid)"), {"i": oid})).one()
    assert new[0] == pytest.approx(10.0 * 3.7, rel=1e-3) and new[1] is True
    h = (await db.execute(sa.text("SELECT old_price_ils::float, source FROM price_history WHERE supplier_part_id=CAST(:i AS uuid)"), {"i": oid})).one()
    assert h[0] == 50.0 and h[1] == "ebay_sync"


async def test_not_due_offer_is_not_fetched(db, monkeypatch, _env):
    sup, pid, oem = await _prep(db, monkeypatch)
    oid = await _offer(db, sup, pid, oem, None, 50)
    await db.execute(sa.text("UPDATE parts_catalog SET base_price=99 WHERE id=CAST(:p AS uuid)"), {"p": pid})
    await _due(db, oid, days=1)
    rep = await eb.sync_ebay_prices(db, limit_per_run=1)
    assert (await db.execute(sa.text("SELECT price_ils::float FROM supplier_parts WHERE id=CAST(:i AS uuid)"), {"i": oid})).scalar() == 50.0


async def test_missing_listing_keeps_price_and_advances_due_time(db, monkeypatch, _env):
    sup, pid, oem = await _prep(db, monkeypatch)
    oid = await _offer(db, sup, pid, oem, None, 50)
    await db.execute(sa.text("UPDATE parts_catalog SET base_price=99 WHERE id=CAST(:p AS uuid)"), {"p": pid})
    await _due(db, oid)
    async def none(pn, limit=3): return []
    _env.search_by_oem = none
    rep = await eb.sync_ebay_prices(db, limit_per_run=1)
    row = (await db.execute(sa.text("SELECT price_ils::float, is_available, last_checked_at > NOW() - interval '1 minute' FROM supplier_parts WHERE id=CAST(:i AS uuid)"), {"i": oid})).one()
    assert row[0] == 50.0 and row[1] is True and row[2] is True and rep["refresh_no_listing"] >= 1


async def test_typed_variant_offer_is_never_refreshed(db, monkeypatch, _env):
    sup, pid, oem = await _prep(db, monkeypatch)
    oid = await _offer(db, sup, pid, oem, "original", 50)
    await db.execute(sa.text("UPDATE parts_catalog SET base_price=99 WHERE id=CAST(:p AS uuid)"), {"p": pid})
    await _due(db, oid)
    await eb.sync_ebay_prices(db, limit_per_run=1)
    assert (await db.execute(sa.text("SELECT price_ils::float FROM supplier_parts WHERE id=CAST(:i AS uuid)"), {"i": oid})).scalar() == 50.0


def test_same_listing_identity():
    from types import SimpleNamespace as N
    url = "https://www.ebay.com/itm/123456789012?hash=x"
    assert eb.same_listing(N(item_id="v1|123456789012|0", item_url=""), url)
    assert eb.same_listing(N(item_id="", item_url="https://www.ebay.com/itm/123456789012"), url)
    assert not eb.same_listing(N(item_id="v1|999999999999|0", item_url="https://www.ebay.com/itm/999999999999"), url)
    assert not eb.same_listing(N(item_id="v1|123456789012|0", item_url=""), None)


async def test_refresh_does_not_take_a_different_listing(db, monkeypatch, _env):
    sup, pid, oem = await _prep(db, monkeypatch)
    oid = await _offer(db, sup, pid, oem, None, 50)
    await db.execute(sa.text("UPDATE supplier_parts SET supplier_url='https://www.ebay.com/itm/111111111111' WHERE id=CAST(:i AS uuid)"), {"i": oid})
    await db.execute(sa.text("UPDATE parts_catalog SET base_price=99 WHERE id=CAST(:p AS uuid)"), {"p": pid})
    await _due(db, oid)
    monkeypatch.setenv("EBAY_REFRESH_ALLOW_RELISTING", "0")
    _env.price = 10.0          # _Res has item_id None / other url -> a different listing
    rep = await eb.sync_ebay_prices(db, limit_per_run=1)
    assert (await db.execute(sa.text("SELECT price_ils::float FROM supplier_parts WHERE id=CAST(:i AS uuid)"), {"i": oid})).scalar() == 50.0
    assert rep["refresh_listing_changed"] >= 1
