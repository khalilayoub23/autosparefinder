"""ebay_api_watch: detects Browse API retirement signals, ignores normal responses (no DB, no network)."""
import ebay_api_watch as w


class R:
    def __init__(self, status=200, headers=None, text=""):
        self.status_code, self.headers, self.text = status, headers or {}, text


def test_normal_and_rate_limit_are_not_signals():
    assert w.detect(R(200)) == ""
    assert w.detect(R(429, text="deprecated rate")) == ""
    assert w.detect(R(500)) == ""


def test_signals():
    assert "sunset" in w.detect(R(200, {"sunset": "Tue, 1 Dec 2026"}))
    assert w.detect(R(410)) == "HTTP 410"
    assert w.detect(R(404)) == "HTTP 404"
    assert "decommission" in w.detect(R(400, text='{"message":"API decommissioned"}'))


def test_detect_never_raises():
    assert w.detect(object()) == ""


def test_ended_listing_404_is_not_a_signal():
    assert w.detect(R(404), "browse/item") == ""
    assert w.detect(R(410), "browse/item") == ""
    assert w.detect(R(404), "browse/item_summary/search") == "HTTP 404"
    assert w.detect(R(301), "browse/item") == "HTTP 301"


# ---- coverage of all four Browse entry points (2026-11-18) ----
import asyncio
import json
import sys
import types

import pytest


@pytest.fixture(autouse=True)
def _reset_throttle():
    w._last_alert.clear()
    yield
    w._last_alert.clear()


def _fake_notify(calls):
    async def notify_owner(*a, **kw):
        calls.append((a, kw))
        return {}
    mod = types.ModuleType("BACKEND_API_ROUTES")
    mod.notify_owner = notify_owner
    return mod


def test_check_alerts_once_and_throttles(monkeypatch):
    calls = []
    monkeypatch.setitem(sys.modules, "BACKEND_API_ROUTES", _fake_notify(calls))
    async def go():
        await w.check("browse/item_summary/search", R(410))
        await w.check("browse/item_summary/search", R(410))
    asyncio.run(go())
    assert len(calls) == 1
    assert calls[0][1]["alert_key"] == "ebay_api_watch:browse/item_summary/search"


def test_check_never_raises_when_notify_fails(monkeypatch):
    mod = types.ModuleType("BACKEND_API_ROUTES")
    async def boom(*a, **k): raise RuntimeError("x")
    mod.notify_owner = boom
    monkeypatch.setitem(sys.modules, "BACKEND_API_ROUTES", mod)
    asyncio.run(w.check("browse/item_summary/search", R(410)))
    asyncio.run(w.check("browse/item_summary/search", object()))


def test_check_sync_without_loop_logs_only(monkeypatch):
    calls = []
    monkeypatch.setitem(sys.modules, "BACKEND_API_ROUTES", _fake_notify(calls))
    w.check_sync("browse/item_summary/search", R(410))
    assert calls == []
    w.check_sync("browse/item_summary/search", object())  # never raises


class FR(R):
    def json(self):
        return json.loads(self.text or "{}")


def test_fitment_provider_invokes_checker_and_keeps_behaviour(monkeypatch):
    import external_fitment_providers as efp
    calls = []
    monkeypatch.setitem(sys.modules, "BACKEND_API_ROUTES", _fake_notify(calls))
    seen = []
    orig = w.check_sync
    monkeypatch.setattr(w, "check_sync", lambda ep, r: (seen.append((ep, r.status_code)), orig(ep, r))[1])

    async def go():
        ok = efp.classify_external_payload(FR(200, {"content-type": "application/json"}, '{"itemSummaries": []}'), source_kind="ebay_browse")
        gone = efp.classify_external_payload(FR(410, {"content-type": "application/json"}, ""), source_kind="ebay_browse")
        other = efp.classify_external_payload(FR(410), source_kind="autodoc_like")
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return ok, gone, other
    ok, gone, other = asyncio.run(go())
    assert ok["payload_kind"] == "json" and gone["payload_kind"] == "non_200" and other["payload_kind"] == "non_200"
    assert seen == [("browse/item_summary/search", 200), ("browse/item_summary/search", 410)]  # non-eBay kinds not inspected
    assert len(calls) == 1  # only the 410 alerts


def test_fitment_provider_survives_checker_failure(monkeypatch):
    import external_fitment_providers as efp
    monkeypatch.setattr(w, "detect", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    out = efp.classify_external_payload(FR(200, {"content-type": "application/json"}, '{"itemSummaries": []}'), source_kind="ebay_browse")
    assert out["payload_kind"] == "json"


def test_importer_search_invokes_checker(monkeypatch):
    import importers.ebay_brand_importer as imp
    seen = []
    async def fake_check(ep, r): seen.append((ep, r.status_code))
    monkeypatch.setattr(imp.ebay_api_watch, "check", fake_check)

    class Client:
        def __init__(self, resp): self.resp = resp
        async def get(self, *a, **k): return self.resp
    class Resp(R):
        def json(self): return {"itemSummaries": [{"itemId": "1"}], "total": 1}
    async def go(resp):
        return await imp.search_ebay(Client(resp), "t", "q", limit=5)
    assert asyncio.run(go(Resp(200))) == [{"itemId": "1"}]      # normal behaviour intact
    assert asyncio.run(go(Resp(410))) == []                      # non-200 path intact
    assert seen == [("browse/item_summary/search", 200), ("browse/item_summary/search", 410)]


def test_supplier_still_wired():
    import inspect
    from services.suppliers import ebay_supplier
    src = inspect.getsource(ebay_supplier)
    assert 'check("browse/item_summary/search"' in src and 'check("browse/item"' in src


def test_redirect_in_history_is_a_signal():
    r = R(200)
    r.history = [R(301)]
    assert w.detect(r, "browse/item_summary/search") == "HTTP 301 redirect"
    assert w.detect(R(200), "browse/item_summary/search") == ""
