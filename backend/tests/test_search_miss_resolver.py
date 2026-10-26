"""Search-miss resolution: a miss is resolved only by a real priced offer.

Regression for 2026-10-10: _scrape_search_misses_loop marked a miss handled when the supplier lookup
returned ANY result. The affiliate suppliers return a price-0 link placeholder for every query (also
after an HTTP 403), so every miss was marked triggered_scrape=TRUE and the customer was notified
"we found new parts" (live: 43 of 43 rows, 37 notifications).

No network (httpx.MockTransport), no database (the persistence call is a recording stub).
"""
import ast
import importlib
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

import search_miss_resolver as smr
from services.suppliers.base_supplier import PartResult
from services.suppliers.catalog_suppliers import _SimpleAffiliate

BACKEND = Path(__file__).resolve().parent.parent


def offer(price, total=None, supplier="ebay"):
    return PartResult(supplier=supplier, item_id="X1", title="Brake pad", price=price, currency="USD",
                      shipping_cost=0.0, total_cost=price if total is None else total, condition="new",
                      seller="s", seller_rating=4.5, item_url="u", image_url=None, location="US",
                      estimated_delivery_days=7)


@pytest.fixture(autouse=True)
def _fresh_cooldown():
    smr._last_checked.clear()
    yield
    smr._last_checked.clear()


def test_only_priced_results_are_genuine():
    priced = offer(41.5)
    assert smr.genuine_offers([offer(0.0), priced, offer(12.0, total=0.0), {"price": 0, "total_cost": 0},
                               {"price": "19.9", "total_cost": 25}, {"price": None}, {"price": "n/a", "total_cost": 3}]) \
        == [priced, {"price": "19.9", "total_cost": 25}]
    assert smr.genuine_offers([]) == [] and smr.genuine_offers(None) == []


async def test_every_affiliate_placeholder_is_rejected():
    class Shop(_SimpleAffiliate):
        name, search_pattern = "shop", "https://shop.example/s?q={oem}"

    results = await Shop().search("brake pads corolla")
    assert len(results) == 1 and results[0].price == 0.0          # the placeholder exists for any query...
    assert smr.genuine_offers(results) == []                       # ...and never counts as a match


@pytest.mark.parametrize("modname,cls", [("services.suppliers.partsouq_supplier", "PartSouqSupplier"),
                                         ("services.suppliers.autodoc_supplier", "AutodocSupplier"),
                                         ("services.suppliers.amayama_supplier", "AmayamaSupplier")])
async def test_blocked_supplier_403_never_resolves_a_miss(monkeypatch, modname, cls):
    mod = importlib.import_module(modname)
    real, hits = httpx.AsyncClient, []

    def factory(*a, **kw):
        def handler(request):
            hits.append(1)
            return httpx.Response(403, text="Forbidden")
        kw["transport"] = httpx.MockTransport(handler)
        return real(*a, **kw)

    monkeypatch.setattr(mod.httpx, "AsyncClient", factory)
    marked = []

    async def search_fn(query, limit_per_supplier=5):
        return await getattr(mod, cls)().search(query, limit_per_supplier)

    async def mark(miss):
        marked.append(miss.id)

    out = await smr.process_misses([SimpleNamespace(id="m1", query="BRAKEPAD")], search_fn, mark)
    assert hits == [1] and marked == []                            # one request, no retry, not marked
    assert (out["resolved"], out["unresolved"], out["errors"]) == (0, 1, 0)


async def test_persistence_decisions_valid_offer_placeholder_error_and_empty():
    answers = {"good": [offer(0.0, supplier="rockauto"), offer(55.0)], "placeholder": [offer(0.0)],
               "empty": [], "boom": RuntimeError("supplier down")}
    marked, asked = [], []

    async def search_fn(query, limit_per_supplier=5):
        asked.append((query, limit_per_supplier))
        if isinstance(answers[query], Exception):
            raise answers[query]
        return answers[query]

    async def mark(miss):
        marked.append(miss.id)

    misses = [SimpleNamespace(id=q, query=q) for q in ("good", "placeholder", "empty", "boom")]
    out = await smr.process_misses(misses, search_fn, mark)
    assert marked == ["good"]                                      # only the genuine match is persisted
    assert out == {"checked": 4, "resolved": 1, "unresolved": 2, "errors": 1, "deferred": 0}
    assert asked == [(q, 5) for q in ("good", "placeholder", "empty", "boom")]   # one lookup each


async def test_unresolved_miss_is_not_looked_up_again_within_the_cooldown():
    asked, marked = [], []

    async def search_fn(query, limit_per_supplier=5):
        asked.append(query)
        return [offer(0.0)] if len(asked) < 3 else [offer(30.0)]

    async def mark(miss):
        marked.append(miss.id)

    miss = [SimpleNamespace(id="m1", query="rare part")]
    t0 = 1_000_000.0
    assert (await smr.process_misses(miss, search_fn, mark, now=t0))["unresolved"] == 1
    out = await smr.process_misses(miss, search_fn, mark, now=t0 + 6 * 3600)      # next 6 h cycle
    assert out["deferred"] == 1 and out["checked"] == 0 and asked == ["rare part"]
    await smr.process_misses(miss, search_fn, mark, now=t0 + smr.RECHECK_AFTER_S + 1)
    assert len(asked) == 2 and marked == []
    await smr.process_misses(miss, search_fn, mark, now=t0 + 2 * smr.RECHECK_AFTER_S + 2)
    assert len(asked) == 3 and marked == ["m1"]                    # a later genuine offer still resolves it


def test_the_loop_marks_a_miss_only_through_the_resolver():
    src = (BACKEND / "BACKEND_API_ROUTES.py").read_text(encoding="utf-8")
    fn = next(n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.AsyncFunctionDef)
              and n.name == "_scrape_search_misses_loop")
    body = ast.get_source_segment(src, fn)
    assert body.count("UPDATE search_misses SET triggered_scrape = TRUE") == 1
    assert "process_misses(misses, search_all_suppliers, _mark_resolved)" in body
    assert "if results:" not in body                               # the old non-empty test is gone
    assert "last_seen_at > NOW() - INTERVAL '30 days'" in body
    # the update lives only inside the callback handed to the resolver
    mark = next(n for n in ast.walk(fn) if isinstance(n, ast.AsyncFunctionDef) and n.name == "_mark_resolved")
    assert "UPDATE search_misses SET triggered_scrape = TRUE" in ast.get_source_segment(src, mark)
